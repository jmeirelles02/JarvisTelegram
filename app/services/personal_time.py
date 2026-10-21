import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc


def agora_utc():
    return datetime.now(UTC)


def fuso_padrao():
    return os.getenv("JARVIS_TIMEZONE", "America/Sao_Paulo")


def validar_fuso(nome):
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Fuso inválido. Exemplo: America/Sao_Paulo.") from None


def utc(data):
    return data.replace(tzinfo=UTC) if data.tzinfo is None else data.astimezone(UTC)


def ler_data(valor, fuso):
    if not isinstance(valor, str) or "T" not in valor:
        raise ValueError("Informe a data e a hora completas para agendar.")
    try:
        data = datetime.fromisoformat(valor)
    except ValueError:
        raise ValueError("Data inválida. Informe dia, mês, ano e horário.") from None
    zona = validar_fuso(fuso)
    if data.tzinfo is None:
        alternativas = {data.replace(tzinfo=zona, fold=fold).utcoffset() for fold in (0, 1)}
        if len(alternativas) > 1:
            raise ValueError("Esse horário muda com o horário de verão. Informe o horário com UTC offset.")
        data = data.replace(tzinfo=zona)
    return data.astimezone(UTC)


def validar_horario(valor):
    if not isinstance(valor, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", valor):
        raise ValueError("Use um horário entre 00:00 e 23:59. Exemplo: /resumodiario 08:00.")
    return valor


def formatar_data(data, fuso):
    return utc(data).astimezone(validar_fuso(fuso)).strftime("%d/%m/%Y às %H:%M")
