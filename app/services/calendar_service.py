import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

import requests

from app.services.personal_time import UTC, utc, validar_fuso


class CalendarioIndisponivel(ValueError):
    pass


@dataclass
class Compromisso:
    uid: str
    titulo: str
    inicio: datetime
    fim: datetime
    dia_inteiro: bool = False
    ocupado: bool = True


def dono_calendario():
    valor = os.getenv("CALENDAR_USER_ID", "")
    return int(valor) if valor.isdigit() else None


def provedor_nome():
    return os.getenv("CALENDAR_PROVIDER", "").strip().lower()


def identidade_calendario():
    if provedor_nome() == "icloud":
        return "icloud:" + os.getenv("ICLOUD_USERNAME", "") + ":" + os.getenv("ICLOUD_CALENDAR_NAME", "")
    conta = hashlib.sha256((os.getenv("GOOGLE_CLIENT_ID", "") + ":" +
                            os.getenv("GOOGLE_REFRESH_TOKEN", "")).encode()).hexdigest()
    return "google:" + conta + ":" + os.getenv("GOOGLE_CALENDAR_ID", "primary")


def obter_calendario(user_id):
    if dono_calendario() != user_id:
        raise CalendarioIndisponivel("Calendário não conectado para este usuário. Configure CALENDAR_USER_ID no servidor com o ID de /meuid.")
    if provedor_nome() == "icloud":
        return CalendarioICloud()
    if provedor_nome() == "google":
        return CalendarioGoogle()
    raise CalendarioIndisponivel("Configure CALENDAR_PROVIDER=icloud ou google no servidor.")


class CalendarioICloud:
    def _cliente(self):
        import caldav

        usuario = os.getenv("ICLOUD_USERNAME", "")
        senha = os.getenv("ICLOUD_APP_PASSWORD", "")
        if not usuario or not senha:
            raise CalendarioIndisponivel("Configure ICLOUD_USERNAME e ICLOUD_APP_PASSWORD no servidor.")
        return caldav.DAVClient(url="https://caldav.icloud.com/", username=usuario, password=senha, timeout=20)

    def _selecionar(self, cliente):
        nome = os.getenv("ICLOUD_CALENDAR_NAME", "")
        if not nome:
            raise CalendarioIndisponivel("Escolha ICLOUD_CALENDAR_NAME no servidor. Consulte os nomes com /calendarios.")
        agendas = [agenda for agenda in cliente.principal().calendars() if agenda.name == nome]
        if len(agendas) != 1:
            raise CalendarioIndisponivel("O nome do calendário iCloud deve identificar uma única agenda. Veja /calendarios.")
        return agendas[0]

    def listar_calendarios(self):
        with self._cliente() as cliente:
            return [agenda.name for agenda in cliente.principal().calendars()]

    def listar(self, inicio, fim, fuso):
        zona = validar_fuso(fuso)
        compromissos = []
        with self._cliente() as cliente:
            agenda = self._selecionar(cliente)
            for evento in agenda.search(start=inicio, end=fim, event=True, expand=True):
                componente = evento.icalendar_instance
                for item in componente.walk("VEVENT"):
                    if str(item.get("STATUS", "")).upper() == "CANCELLED":
                        continue
                    comeco = item.decoded("DTSTART")
                    dia_inteiro = isinstance(comeco, date) and not isinstance(comeco, datetime)
                    termino = item.decoded("DTEND") if "DTEND" in item else comeco + (
                        item.decoded("DURATION") if "DURATION" in item else timedelta(days=1 if dia_inteiro else 0)
                    )
                    if dia_inteiro:
                        comeco = datetime.combine(comeco, time.min, zona)
                        termino = datetime.combine(termino, time.min, zona)
                    else:
                        comeco = comeco.replace(tzinfo=zona) if comeco.tzinfo is None else comeco
                        termino = termino.replace(tzinfo=zona) if termino.tzinfo is None else termino
                    if utc(comeco) < utc(fim) and utc(termino) > utc(inicio):
                        compromissos.append(Compromisso(
                            str(item.get("UID", evento.url)), str(item.get("SUMMARY", "Sem título")),
                            utc(comeco), utc(termino), dia_inteiro,
                            str(item.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
                        ))
        return sorted(compromissos, key=lambda evento: evento.inicio)

    def criar(self, uid, titulo, inicio, fim, aviso_minutos):
        from caldav.lib.error import NotFoundError
        from icalendar import Alarm, Calendar, Event

        with self._cliente() as cliente:
            agenda = self._selecionar(cliente)
            try:
                agenda.get_event_by_uid(uid)
                return uid
            except NotFoundError:
                pass
            arquivo = Calendar()
            arquivo.add("prodid", "-//Jarvis//Assistente pessoal//PT-BR")
            arquivo.add("version", "2.0")
            evento = Event()
            evento.add("uid", uid)
            evento.add("summary", titulo)
            evento.add("dtstamp", datetime.now(UTC))
            evento.add("dtstart", utc(inicio))
            evento.add("dtend", utc(fim))
            alarme = Alarm()
            alarme.add("action", "DISPLAY")
            alarme.add("description", titulo)
            alarme.add("trigger", -timedelta(minutes=aviso_minutos))
            evento.add_component(alarme)
            arquivo.add_component(evento)
            agenda.save_event(arquivo.to_ical(), no_overwrite=True)
        return uid


class CalendarioGoogle:
    def _token(self):
        valores = {chave: os.getenv(variavel, "") for chave, variavel in (
            ("client_id", "GOOGLE_CLIENT_ID"), ("client_secret", "GOOGLE_CLIENT_SECRET"),
            ("refresh_token", "GOOGLE_REFRESH_TOKEN"),
        )}
        if not all(valores.values()):
            raise CalendarioIndisponivel("Configure as credenciais Google no servidor. Veja o README.")
        resposta = requests.post("https://oauth2.googleapis.com/token", data={
            **valores, "grant_type": "refresh_token",
        }, timeout=20)
        resposta.raise_for_status()
        return resposta.json()["access_token"]

    def _requisitar(self, metodo, caminho, **kwargs):
        resposta = requests.request(metodo, "https://www.googleapis.com/calendar/v3/" + caminho,
                                    headers={"Authorization": "Bearer " + self._token()}, timeout=20, **kwargs)
        return resposta

    def _caminho_eventos(self):
        return "calendars/" + quote(os.getenv("GOOGLE_CALENDAR_ID", "primary"), safe="") + "/events"

    def listar_calendarios(self):
        return [os.getenv("GOOGLE_CALENDAR_ID", "primary")]

    def listar(self, inicio, fim, fuso):
        compromissos = []
        pagina = None
        while True:
            resposta = self._requisitar("GET", self._caminho_eventos(), params={
                "timeMin": utc(inicio).isoformat(), "timeMax": utc(fim).isoformat(),
                "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
                "timeZone": fuso, "pageToken": pagina,
            })
            resposta.raise_for_status()
            dados = resposta.json()
            for item in dados.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                dia_inteiro = "date" in item["start"]
                if dia_inteiro:
                    zona = validar_fuso(fuso)
                    comeco = datetime.combine(date.fromisoformat(item["start"]["date"]), time.min, zona)
                    termino = datetime.combine(date.fromisoformat(item["end"]["date"]), time.min, zona)
                else:
                    comeco = datetime.fromisoformat(item["start"]["dateTime"])
                    termino = datetime.fromisoformat(item["end"]["dateTime"])
                compromissos.append(Compromisso(item["id"], item.get("summary", "Sem título"),
                                                utc(comeco), utc(termino), dia_inteiro,
                                                item.get("transparency") != "transparent"))
            pagina = dados.get("nextPageToken")
            if not pagina:
                return sorted(compromissos, key=lambda evento: evento.inicio)

    def criar(self, uid, titulo, inicio, fim, aviso_minutos):
        identificador = hashlib.sha256(uid.encode()).hexdigest()
        corpo = {"id": identificador, "summary": titulo,
                 "start": {"dateTime": utc(inicio).isoformat()}, "end": {"dateTime": utc(fim).isoformat()},
                 "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": aviso_minutos}]}}
        resposta = self._requisitar("POST", self._caminho_eventos(), json=corpo)
        if resposta.status_code != 409:
            resposta.raise_for_status()
        return identificador
