import hashlib
import os
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from urllib.parse import urljoin, urlparse
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import requests

from app.services.personal_time import UTC, utc, validar_fuso


logger = logging.getLogger(__name__)


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
    recurso: str | None = None
    recorrencia: str | None = None
    recorrente: bool = False


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
    def _recursos(self, cliente, agenda, inicio, fim):
        from caldav.lib.error import ReportError
        try:
            return agenda.search(start=inicio, end=fim, event=True, expand=False)
        except ReportError:
            # Consulta mínima por período, sem filtros de UID ou expansão no servidor.
            logger.warning("iCloud: consulta da biblioteca rejeitada; tentando REPORT mínimo por período.")
            from icalendar import Calendar
            corpo = ('<?xml version="1.0" encoding="utf-8"?>'
                '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
                '<c:comp-filter name="VEVENT"><c:time-range '
                f'start="{utc(inicio):%Y%m%dT%H%M%SZ}" end="{utc(fim):%Y%m%dT%H%M%SZ}"/>'
                '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
            resposta = cliente.request(str(agenda.url), "REPORT", corpo,
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
            if resposta.status != 207:
                logger.warning("iCloud: REPORT mínimo recusado; HTTP %s.", resposta.status)
                raise CalendarioIndisponivel(f"O iCloud rejeitou a consulta mínima de período (CalDAV REPORT HTTP {resposta.status}). "
                    "A falha não confirma que suas credenciais estejam erradas.")
            recursos = []
            for item in ET.fromstring(resposta.raw).findall("{DAV:}response"):
                href = item.findtext("{DAV:}href")
                if not href:
                    continue
                for propstat in item.findall("{DAV:}propstat"):
                    if " 200 " not in propstat.findtext("{DAV:}status", ""):
                        continue
                    dados = propstat.findtext("{DAV:}prop/{urn:ietf:params:xml:ns:caldav}calendar-data")
                    if dados:
                        recursos.append(SimpleNamespace(url=self._url_recurso(agenda, href),
                            icalendar_instance=Calendar.from_ical(dados)))
            return recursos

    @staticmethod
    def _url_recurso(agenda, recurso):
        base = urlparse(str(agenda.url))
        url = urljoin(str(agenda.url), recurso)
        destino = urlparse(url)
        if (destino.scheme, destino.netloc) != (base.scheme, base.netloc) or not destino.path.startswith(base.path.rstrip("/") + "/"):
            raise CalendarioIndisponivel("O recurso não pertence ao calendário selecionado.")
        return url

    @staticmethod
    def _erro_http(operacao, codigo=None):
        detalhe = f" HTTP {codigo}" if codigo else ""
        if codigo in {401, 403}:
            return CalendarioIndisponivel(
                f"O iCloud recusou o acesso ao {operacao}.{detalhe} "
                "Confira a conta Apple e a senha específica de app em ICLOUD_USERNAME e ICLOUD_APP_PASSWORD. "
                "Se a agenda for compartilhada, confira também a permissão de edição.")
        if codigo == 429:
            return CalendarioIndisponivel("O iCloud limitou temporariamente as consultas. Tente novamente em alguns minutos.")
        return CalendarioIndisponivel(
            f"O iCloud não concluiu a operação de {operacao}.{detalhe} "
            "A falha não confirma que suas credenciais estejam erradas. Tente novamente em instantes.")

    @contextmanager
    def _conexao(self, operacao):
        from caldav.lib.error import AuthorizationError, DAVError, ReportError
        from niquests.exceptions import RequestException as ErroRedeDAV

        try:
            with self._cliente() as cliente:
                yield cliente
        except CalendarioIndisponivel:
            raise
        except AuthorizationError as erro:
            codigo = re.search(r"\b(401|403)\b", str(getattr(erro, "reason", "")))
            codigo = int(codigo.group(1)) if codigo else 401
            logger.warning("iCloud: falha de autorização na operação %s.", operacao)
            raise self._erro_http(operacao, codigo) from None
        except DAVError as erro:
            # Não registrar URL, corpo ICS ou a mensagem bruta, que podem conter dados pessoais.
            codigo = re.search(r"\b([45]\d{2})\b", str(getattr(erro, "reason", "")))
            codigo = int(codigo.group(1)) if codigo else None
            logger.warning("iCloud: %s na operação %s; HTTP %s.", type(erro).__name__, operacao, codigo or "não informado")
            if isinstance(erro, ReportError) and codigo not in {401, 403, 429}:
                raise CalendarioIndisponivel(
                    f"O iCloud rejeitou uma consulta de eventos (CalDAV REPORT{f' HTTP {codigo}' if codigo else ''}). "
                    "Isso pode ser uma incompatibilidade da consulta; não significa necessariamente senha inválida.") from None
            raise self._erro_http(operacao, codigo) from None
        except (requests.RequestException, ErroRedeDAV):
            logger.warning("iCloud: falha de conexão na operação %s.", operacao)
            raise CalendarioIndisponivel("Não consegui conectar ao iCloud. Tente novamente em alguns instantes.") from None

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
        with self._conexao("listar calendários") as cliente:
            return [agenda.name for agenda in cliente.principal().calendars()]

    def listar(self, inicio, fim, fuso):
        from app.services.calendar_changes import ocorrencias
        zona = validar_fuso(fuso)
        compromissos = []
        with self._conexao("consultar eventos") as cliente:
            agenda = self._selecionar(cliente)
            for evento in self._recursos(cliente, agenda, inicio, fim):
                componente = evento.icalendar_instance
                for item in ocorrencias(componente, inicio, fim, fuso):
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
                            str(evento.url),
                            item.decoded("RECURRENCE-ID").isoformat() if "RECURRENCE-ID" in item else None,
                            any("RRULE" in vevent or "RDATE" in vevent or "RECURRENCE-ID" in vevent
                                for vevent in componente.walk("VEVENT")),
                        ))
        return sorted(compromissos, key=lambda evento: evento.inicio)

    def alterar(self, esperado, acao, novo_inicio, operacao, fuso):
        from icalendar import Calendar
        from app.services.calendar_changes import alterar_ocorrencia
        with self._conexao("alterar uma ocorrência") as cliente:
            agenda = self._selecionar(cliente)
            if not esperado.recurso:
                raise CalendarioIndisponivel("Consulte o compromisso novamente antes de alterá-lo.")
            url = self._url_recurso(agenda, esperado.recurso)
            resposta = cliente.request(url, "GET")
            if resposta.status != 200:
                raise self._erro_http("reler o compromisso", resposta.status)
            arquivo = Calendar.from_ical(resposta.raw)
            novo, mudou = alterar_ocorrencia(arquivo, esperado, acao, novo_inicio, operacao, fuso)
            if not mudou:
                return
            etag = resposta.headers.get("ETag") or resposta.headers.get("etag")
            if not isinstance(etag, str) or not etag or etag.startswith("W/"):
                raise CalendarioIndisponivel("O iCloud não forneceu uma versão segura do evento. Nada foi alterado.")
            salvo = cliente.put(url, novo.to_ical().decode("utf-8"), headers={
                "Content-Type": "text/calendar; charset=utf-8", "If-Match": etag})
            if salvo.status == 412:
                raise CalendarioIndisponivel("O evento mudou durante a alteração. Nada foi sobrescrito; consulte a agenda novamente.")
            if salvo.status not in {200, 201, 204}:
                raise self._erro_http("salvar a alteração", salvo.status)

    def diagnosticar(self):
        """Sonda apenas leitura: separa acesso, consulta por período e consulta por UID."""
        from uuid import uuid4
        from caldav.lib.error import NotFoundError, DAVError
        agora = datetime.now(UTC)
        with self._conexao("diagnosticar calendário") as cliente:
            agenda = self._selecionar(cliente)
            self._recursos(cliente, agenda, agora, agora + timedelta(minutes=1))
            try:
                agenda.get_event_by_uid("jarvis-diagnostico-" + uuid4().hex)
            except NotFoundError:
                return "Acesso e consultas por período e por UID funcionaram neste teste de leitura."
            except DAVError as erro:
                codigo = re.search(r"\b([45]\d{2})\b", str(getattr(erro, "reason", "")))
                return ("Acesso e consulta por período funcionaram; a consulta por UID falhou com "
                        f"{type(erro).__name__}" + (f" HTTP {codigo.group(1)}." if codigo else "."))
            return "Acesso e consulta por período funcionaram."

    def criar(self, uid, titulo, inicio, fim, aviso_minutos):
        from icalendar import Alarm, Calendar, Event

        with self._conexao("criar compromisso") as cliente:
            agenda = self._selecionar(cliente)
            # A busca por UID usa REPORT, inclusive dentro de save_event(no_overwrite=True).
            # O mesmo recurso tem um caminho estável e pode ser consultado por GET.
            url = str(agenda.url.join(quote(uid, safe="") + ".ics"))
            existente = cliente.request(url, "GET")
            if existente.status == 200:
                self._validar_uid(existente.raw, uid)
                return uid
            if existente.status != 404:
                raise self._erro_http("verificar o compromisso", existente.status)
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
            resposta = cliente.put(url, arquivo.to_ical().decode("utf-8"), headers={
                "Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*",
            })
            if resposta.status == 412:
                # Outra tentativa pode ter criado o recurso após nosso GET.
                existente = cliente.request(url, "GET")
                if existente.status != 200:
                    raise self._erro_http("verificar o compromisso já existente", existente.status)
                self._validar_uid(existente.raw, uid)
            elif resposta.status not in {201, 204}:
                raise self._erro_http("salvar o compromisso", resposta.status)
        return uid

    @staticmethod
    def _validar_uid(conteudo, uid):
        from icalendar import Calendar

        eventos = Calendar.from_ical(conteudo).walk("VEVENT")
        if not eventos or any(str(evento.get("UID", "")) != uid for evento in eventos):
            raise CalendarioIndisponivel("O recurso encontrado no iCloud não corresponde a este compromisso. Nada foi sobrescrito.")


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
