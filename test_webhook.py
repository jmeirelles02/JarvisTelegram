"""Smoke test do parsing do webhook da Evolution: python test_webhook.py"""
import run_bot

run_bot.PERMITIDOS = set()  # o teste não depende do allowlist do .env


def evento(key_extra=None, message=None):
    key = {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": "3A1B2C"}
    key.update(key_extra or {})
    return {"event": "messages.upsert", "data": {"key": key, "message": message or {}}}


texto = run_bot.extrair_mensagem(evento(message={"conversation": " Gastei 50 no mercado "}))
assert texto == (5511999999999, "Gastei 50 no mercado", None, None), texto

longo = run_bot.extrair_mensagem(evento(message={"extendedTextMessage": {"text": "oi"}}))
assert longo[1] == "oi", longo

imagem = run_bot.extrair_mensagem(evento(message={"imageMessage": {"caption": "nota do posto"}}))
assert imagem[1] == "nota do posto" and imagem[3] == "image/jpeg", imagem

sem_legenda = run_bot.extrair_mensagem(evento(message={"imageMessage": {}}))
assert sem_legenda[1] == "Analise este comprovante", sem_legenda

audio = run_bot.extrair_mensagem(evento(message={"audioMessage": {"seconds": 3}}))
assert audio[2] is not None and audio[3] == "audio/ogg", audio

# o eco da própria mensagem não pode virar entrada, senão o bot conversa sozinho
assert run_bot.extrair_mensagem(evento({"fromMe": True}, {"conversation": "oi"})) is None
assert run_bot.extrair_mensagem(evento({"remoteJid": "123@g.us"}, {"conversation": "oi"})) is None
assert run_bot.extrair_mensagem(evento(message={})) is None
assert run_bot.extrair_mensagem({}) is None

run_bot.PERMITIDOS = {"5511888888888"}
assert run_bot.extrair_mensagem(evento(message={"conversation": "oi"})) is None, "allowlist não barrou"

print("OK")
