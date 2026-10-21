import re
import unicodedata
from datetime import timedelta

from app.services.personal_time import agora_utc, utc, validar_fuso


DIAS_SEMANA = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")
DIAS_NORMALIZADOS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}
CONSULTA = re.compile(
    r"^(?:qual(?: e)? (?:a )?(?:minha )?agenda|"
    r"quais (?:sao )?(?:os )?(?:meus )?(?:compromissos|eventos)|"
    r"o ?que (?:eu )?tenho(?: (?:para|pra|a) fazer)?|"
    r"(?:me (?:mostra|mostre|diz|diga) )?(?:minha )?agenda)"
    r"(?: (?P<periodo>.+))?$"
)


def periodo_consulta(texto):
    normalizado = "".join(caractere for caractere in unicodedata.normalize("NFD", texto.lower())
                          if unicodedata.category(caractere) != "Mn")
    normalizado = " ".join(normalizado.strip().rstrip("?!.").split())
    consulta = CONSULTA.fullmatch(normalizado)
    if not consulta:
        return None
    periodo = consulta.group("periodo") or "hoje"
    return re.sub(r"^(?:de|da|do|na|no|para|pra|desta|dessa|nesta|nessa) ", "", periodo)


def interpretar_consulta(texto, fuso, agora=None):
    periodo = periodo_consulta(texto)
    if periodo is None:
        return None
    local = (agora or agora_utc()).astimezone(validar_fuso(fuso))
    inicio = local.replace(hour=0, minute=0, second=0, microsecond=0)
    dias = 1
    relativos = {"hoje": 0, "amanha": 1, "depois de amanha": 2}
    if periodo in relativos:
        inicio += timedelta(days=relativos[periodo])
    elif periodo in {"semana", "esta semana", "essa semana"}:
        inicio -= timedelta(days=local.weekday())
        dias = 7
    elif periodo in {"proxima semana", "semana que vem"}:
        inicio += timedelta(days=7 - local.weekday())
        dias = 7
    else:
        semana = re.fullmatch(r"(?:(?P<proxima>proxima) |(?:esta|essa) )?"
                             r"(?P<dia>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?"
                             r"(?P<seguinte> que vem)?", periodo)
        data = re.fullmatch(r"(?P<dia>\d{1,2})/(?P<mes>\d{1,2})(?:/(?P<ano>\d{4}))?", periodo)
        if semana:
            distancia = (DIAS_NORMALIZADOS[semana.group("dia")] - local.weekday()) % 7
            if distancia == 0 and (semana.group("proxima") or semana.group("seguinte")):
                distancia = 7
            inicio += timedelta(days=distancia)
        elif data:
            try:
                inicio = inicio.replace(year=int(data.group("ano") or local.year),
                                        month=int(data.group("mes")), day=int(data.group("dia")))
            except ValueError:
                return None
        else:
            return None
    return {"intencao": "agenda", "dados": {"inicio": inicio.isoformat(), "fim": (inicio + timedelta(days=dias)).isoformat()}}


def titulo_dia(data):
    return f"{DIAS_SEMANA[data.weekday()]} · {data.strftime('%d/%m/%Y')}"


def linhas_eventos(eventos, fuso, inicio=None, agrupar=True):
    if not eventos:
        return ["Nenhum compromisso nesse período."]
    zona = validar_fuso(fuso)
    linhas = []
    dia_anterior = None
    for evento in sorted(eventos, key=lambda item: (utc(item.inicio), item.titulo)):
        comeco = utc(evento.inicio).astimezone(zona)
        termino = utc(evento.fim).astimezone(zona)
        dia = max(comeco.date(), inicio.date()) if inicio else comeco.date()
        if agrupar and dia != dia_anterior:
            if linhas:
                linhas.append("")
            linhas.extend((f"*{titulo_dia(dia)}*", ""))
            dia_anterior = dia
        titulo = " ".join(evento.titulo.split()) or "Sem título"
        if evento.dia_inteiro:
            ultimo_dia = (termino - timedelta(microseconds=1)).date()
            horario = "Dia inteiro"
            if ultimo_dia > comeco.date():
                horario += f" · {comeco.strftime('%d/%m')}–{ultimo_dia.strftime('%d/%m')}"
        elif comeco.date() != termino.date():
            horario = f"{comeco.strftime('%d/%m %H:%M')} → {termino.strftime('%d/%m %H:%M')}"
        else:
            horario = comeco.strftime("%H:%M")
            if termino > comeco:
                horario += "–" + termino.strftime("%H:%M")
        linhas.append(f"*{horario}* · {titulo}")
    return linhas


def formatar_agenda(eventos, inicio, fim, fuso):
    zona = validar_fuso(fuso)
    inicio_local = utc(inicio).astimezone(zona)
    fim_local = utc(fim).astimezone(zona)
    ultimo_dia = (fim_local - timedelta(microseconds=1)).date()
    dia_unico = inicio_local.date() == ultimo_dia
    if dia_unico:
        linhas = [f"📅 *{titulo_dia(inicio_local)}*", ""]
    else:
        linhas = [f"📅 *Agenda · {inicio_local.strftime('%d/%m/%Y')} a {ultimo_dia.strftime('%d/%m/%Y')}*", ""]
    linhas.extend(linhas_eventos(eventos, fuso, inicio_local, agrupar=not dia_unico))
    if eventos:
        quantidade = len(eventos)
        linhas.extend(("", f"{quantidade} compromisso{'s' if quantidade != 1 else ''}"))
    return "\n".join(linhas)
