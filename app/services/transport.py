import os

from app.services import telegram, whatsapp


def canal():
    nome = os.getenv("JARVIS_CHANNEL", "telegram").strip().lower()
    if nome not in {"telegram", "whatsapp"}:
        raise ValueError("Configure JARVIS_CHANNEL como telegram ou whatsapp.")
    return nome


def _cliente():
    return telegram if canal() == "telegram" else whatsapp


def enviar_texto(numero, texto):
    return _cliente().enviar_texto(numero, texto)


def enviar_midia(numero, buffer, mimetype, nome_arquivo, tipo, legenda=""):
    return _cliente().enviar_midia(numero, buffer, mimetype, nome_arquivo, tipo, legenda)


def baixar_midia(chave):
    return _cliente().baixar_midia(chave)
