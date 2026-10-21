import logging
import os
import threading

import requests

from app.services.command_menu import CATALOGO_COMANDOS


logger = logging.getLogger(__name__)


class ErroTelegram(RuntimeError):
    pass


def _token():
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise ErroTelegram("Configure TELEGRAM_TOKEN no Environment do serviço.")
    return token


def _chamar(metodo, dados=None, arquivos=None, timeout=60):
    url = f"https://api.telegram.org/bot{_token()}/{metodo}"
    try:
        if arquivos:
            resposta = requests.post(url, data=dados, files=arquivos, timeout=timeout)
        else:
            resposta = requests.post(url, json=dados or {}, timeout=timeout)
        if not resposta.ok:
            raise ErroTelegram(f"Telegram retornou HTTP {resposta.status_code} em {metodo}.")
        conteudo = resposta.json()
    except (requests.RequestException, ValueError):
        raise ErroTelegram(f"Não foi possível concluir {metodo} no Telegram.") from None
    if not conteudo.get("ok"):
        raise ErroTelegram(f"Telegram recusou {metodo}.")
    return conteudo.get("result")


def enviar_texto(numero, texto):
    for inicio in range(0, len(texto), 4000):
        _chamar("sendMessage", {"chat_id": numero, "text": texto[inicio:inicio + 4000]})


def enviar_midia(numero, buffer, mimetype, nome_arquivo, tipo, legenda=""):
    metodo, campo = {"image": ("sendPhoto", "photo"), "document": ("sendDocument", "document"),
                     "video": ("sendVideo", "video"), "audio": ("sendAudio", "audio")}[tipo]
    buffer.seek(0)
    _chamar(metodo, {"chat_id": numero, "caption": legenda}, {campo: (nome_arquivo, buffer, mimetype)})


def baixar_midia(file_id):
    arquivo = _chamar("getFile", {"file_id": file_id})
    caminho = arquivo.get("file_path", "")
    if not caminho:
        raise ErroTelegram("O Telegram não disponibilizou o arquivo.")
    try:
        resposta = requests.get(f"https://api.telegram.org/file/bot{_token()}/{caminho}", timeout=60)
        if not resposta.ok:
            raise ErroTelegram("Não foi possível baixar o arquivo do Telegram.")
        return resposta.content
    except requests.RequestException:
        raise ErroTelegram("Não foi possível baixar o arquivo do Telegram.") from None


def extrair_mensagem(update, bot_username=""):
    mensagem = update.get("message") or {}
    chat = mensagem.get("chat") or {}
    usuario = mensagem.get("from") or {}
    user_id = usuario.get("id")
    if chat.get("type") != "private" or usuario.get("is_bot") or type(user_id) is not int or chat.get("id") != user_id:
        return None
    permitidos = {valor.strip() for valor in os.getenv("TELEGRAM_USER_IDS", "").split(",") if valor.strip()}
    if permitidos and str(user_id) not in permitidos:
        return None
    if mensagem.get("photo"):
        return user_id, mensagem.get("caption") or "Analise este comprovante", mensagem["photo"][-1]["file_id"], "image/jpeg"
    audio = mensagem.get("voice") or mensagem.get("audio")
    if audio:
        return user_id, mensagem.get("caption") or "Transcreva o áudio e analise o pedido", audio["file_id"], audio.get("mime_type", "audio/ogg")
    texto = mensagem.get("text", "").strip()
    if not texto:
        return None
    if texto.startswith("/"):
        partes = texto.split(maxsplit=1)
        comando, separador, destinatario = partes[0].partition("@")
        if separador:
            if not bot_username or destinatario.lower() != bot_username.lower():
                return None
            texto = comando + (" " + partes[1] if len(partes) > 1 else "")
    return user_id, texto, None, None


def preparar():
    bot = _chamar("getMe")
    webhook = _chamar("getWebhookInfo")
    if webhook.get("url"):
        raise ErroTelegram("O bot tem um webhook ativo. Remova-o antes de iniciar o modo polling.")
    comandos = [{"command": nome, "description": descricao + (f" ({argumentos})" if argumentos else "")}
                for grupo in CATALOGO_COMANDOS.values() for nome, argumentos, descricao in grupo]
    try:
        _chamar("setMyCommands", {"commands": comandos})
        _chamar("setMyCommands", {"commands": comandos, "language_code": "pt"})
        _chamar("setChatMenuButton", {"menu_button": {"type": "commands"}})
    except ErroTelegram:
        logger.warning("Não foi possível registrar o menu nativo do Telegram. O menu / continua disponível.")
    return bot["username"]


def vigiar(atender, bot_username, parada=None):
    parada = parada or threading.Event()
    offset = None
    while not parada.is_set():
        try:
            updates = _chamar("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}, timeout=40)
            for update in updates:
                mensagem = extrair_mensagem(update, bot_username)
                if mensagem:
                    atender(*mensagem)
                offset = update["update_id"] + 1
        except ErroTelegram as erro:
            logger.warning("%s", erro)
            parada.wait(10)
        except Exception as erro:
            logger.warning("Falha ao tratar atualização do Telegram: %s", type(erro).__name__)
            parada.wait(10)
