"""Altera um VEVENT ou uma exceção da série sem deslocar as outras ocorrências."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import recurring_ical_events

from app.services.personal_time import agora_utc, utc, validar_fuso


def instante(valor, fuso):
    zona = validar_fuso(fuso)
    if isinstance(valor, datetime):
        return utc(valor.replace(tzinfo=zona) if valor.tzinfo is None else valor)
    return datetime.combine(valor, datetime.min.time(), zona).astimezone(validar_fuso("UTC"))


def ocorrencias(arquivo, inicio, fim, fuso):
    return recurring_ical_events.of(arquivo).between(
        inicio.astimezone(validar_fuso(fuso)), fim.astimezone(validar_fuso(fuso)))


def alterar_ocorrencia(arquivo, esperado, acao, novo_inicio, operacao, fuso):
    if acao not in {"mover", "cancelar"}:
        raise ValueError("Ação de calendário inválida.")
    mesmo_uid = [item for item in arquivo.walk("VEVENT") if str(item.get("UID", "")) == esperado.uid]
    if not mesmo_uid:
        raise ValueError("O compromisso não está mais nesse recurso. Consulte a agenda novamente.")
    # Uma retentativa após timeout reconhece a operação que já chegou ao iCloud.
    if any(str(item.get("X-JARVIS-OPERACAO", "")) == operacao for item in mesmo_uid):
        return arquivo, False
    candidatos = [item for item in ocorrencias(arquivo, esperado.inicio, esperado.fim + timedelta(seconds=1), fuso)
                  if str(item.get("UID", "")) == esperado.uid
                  and instante(item.decoded("DTSTART"), fuso) == esperado.inicio
                  and instante(item.decoded("DTEND"), fuso) == esperado.fim
                  and str(item.get("STATUS", "")).upper() != "CANCELLED"]
    if len(candidatos) != 1:
        raise ValueError("Esse horário mudou desde a consulta. Vou precisar consultar o compromisso novamente.")
    atual = candidatos[0]
    if " ".join(str(atual.get("SUMMARY", "Sem título")).split()) != " ".join(esperado.titulo.split()):
        raise ValueError("O compromisso foi alterado desde a consulta. Consulte a agenda novamente.")
    if "ATTENDEE" in atual or "ORGANIZER" in atual:
        raise ValueError("Esse compromisso tem participantes. Ajuste-o no calendário para preservar o envio de convites.")
    if esperado.dia_inteiro:
        raise ValueError("Informe um compromisso com horário para fazer essa alteração pela conversa.")
    master = next((item for item in mesmo_uid if "RECURRENCE-ID" not in item), None)
    recorrente = bool(master and any(campo in master for campo in ("RRULE", "RDATE"))) or any(
        "RECURRENCE-ID" in item for item in mesmo_uid)
    novo = deepcopy(atual if recorrente else master or atual)
    rid = atual.decoded("RECURRENCE-ID") if "RECURRENCE-ID" in atual else atual.decoded("DTSTART")
    if recorrente:
        for campo in ("RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID"):
            novo.pop(campo, None)
        # Mantém a identidade da sexta original mesmo quando DTSTART passa à quinta.
        novo.add("recurrence-id", rid)
    if acao == "mover":
        duracao = esperado.fim - esperado.inicio
        zona_original = atual.decoded("DTSTART").tzinfo or validar_fuso(fuso)
        for campo in ("DTSTART", "DTEND", "DURATION", "STATUS"):
            novo.pop(campo, None)
        novo.add("dtstart", novo_inicio.astimezone(zona_original))
        novo.add("dtend", (novo_inicio + duracao).astimezone(zona_original))
        novo.add("status", "CONFIRMED")
    else:
        novo.pop("STATUS", None)
        novo.add("status", "CANCELLED")
    novo.pop("DTSTAMP", None)
    novo.add("dtstamp", agora_utc())
    novo["SEQUENCE"] = int(novo.get("SEQUENCE", 0)) + 1
    novo["X-JARVIS-OPERACAO"] = operacao
    resultado = deepcopy(arquivo)
    manter = []
    for item in resultado.subcomponents:
        if item.name != "VEVENT" or str(item.get("UID", "")) != esperado.uid:
            manter.append(item)
        elif recorrente:
            if "RECURRENCE-ID" not in item or instante(item.decoded("RECURRENCE-ID"), fuso) != instante(rid, fuso):
                manter.append(item)
        # Para evento avulso, substitui apenas esse VEVENT.
    resultado.subcomponents = manter + [novo]
    return resultado, True
