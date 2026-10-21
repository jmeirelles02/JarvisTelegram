"""Cliente da Evolution API — o transporte que substituiu o Telegram.

Só o que o Jarvis usa: mandar texto, mandar arquivo e baixar mídia recebida.
"""
import os
import base64
import logging

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

URL = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
INSTANCIA = os.getenv("EVOLUTION_INSTANCE", "jarvis")
APIKEY = os.getenv("EVOLUTION_APIKEY", "")


def _post(rota, payload):
    resposta = requests.post(
        f"{URL}/{rota}/{INSTANCIA}",
        json=payload,
        headers={"apikey": APIKEY},
        timeout=60,
    )
    resposta.raise_for_status()
    return resposta.json()


def enviar_texto(numero, texto):
    _post("message/sendText", {"number": str(numero), "text": texto})


def enviar_midia(numero, buffer, mimetype, nome_arquivo, tipo, legenda=""):
    """tipo: 'image' | 'document' | 'video' | 'audio'."""
    _post("message/sendMedia", {
        "number": str(numero),
        "mediatype": tipo,
        "mimetype": mimetype,
        "media": base64.b64encode(buffer.read()).decode(),
        "fileName": nome_arquivo,
        "caption": legenda,
    })


def baixar_midia(key):
    """O webhook não traz o binário, só a chave da mensagem — o download é um 2º request."""
    dados = _post("chat/getBase64FromMediaMessage", {"message": {"key": key}})
    return base64.b64decode(dados["base64"])
