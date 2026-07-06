import os
import json
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1"
MODELO_TEXTO = "llama-3.3-70b-versatile"
MODELO_VISAO = "meta-llama/llama-4-scout-17b-16e-instruct"
MODELO_AUDIO = "whisper-large-v3"

# Prompt compacto: mesma extração, ~60% menos tokens que a versão original
PROMPT_TEXTO = """Responda APENAS com JSON. Classifique a entrada (texto ou imagem de comprovante):

1. Transação: {"intencao":"transacao","dados":{"descricao":"resumo do item","valor":0.00,"categoria":"Alimentacao|Transporte|Lazer|Casa|Contas|Servicos|Outros","metodo_pagamento":"Credito|Debito|Pix|Dinheiro","tipo":"Saida|Entrada","parcelas":1}}
- Fatura ou boleto (cartão de crédito, luz, água, internet, telefone): categoria "Contas".
- Compra parcelada (ex: "10x de 200", "em 12 vezes"): "parcelas" = quantidade e "valor" = valor de UMA parcela (se o usuário der o total, divida pelo número de parcelas).
- Compra à vista: "parcelas": 1.
2. Resumo/saldo/gráfico: {"intencao":"resumo"}
3. Planilha/excel/exportar: {"intencao":"exportacao"}
4. Ajuda/capacidades: {"intencao":"ajuda"}

Se receber imagem: extraia o valor total e o estabelecimento."""

# ponytail: atalho por palavra-chave — intenções simples não gastam nenhum token
ATALHOS = {
    "resumo": {"resumo", "saldo", "grafico", "gráfico", "dashboard", "balanco", "balanço"},
    "exportacao": {"planilha", "excel", "exportar", "csv", "extrato"},
    "ajuda": {"ajuda", "help", "comandos", "funcoes", "funções"},
}

def _atalho_local(texto):
    palavras = set(texto.lower().split())
    if len(palavras) <= 5:
        for intencao, chaves in ATALHOS.items():
            if palavras & chaves:
                return {"intencao": intencao}
    return None

def _transcrever_audio(api_key, arquivo_bytes, mime_type):
    response = requests.post(
        f"{GROQ_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.ogg", arquivo_bytes, mime_type)},
        data={"model": MODELO_AUDIO, "language": "pt"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["text"]

def interpretar_mensagem(mensagem_usuario=None, arquivo_bytes=None, mime_type=None):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        # Áudio não entra direto no chat: transcreve com Whisper e vira texto
        if arquivo_bytes and mime_type and mime_type.startswith("audio"):
            transcricao = _transcrever_audio(api_key, arquivo_bytes, mime_type)
            logger.info("🎙️ Áudio transcrito: %s", transcricao)
            mensagem_usuario = transcricao
            arquivo_bytes = None

        if mensagem_usuario and not arquivo_bytes:
            atalho = _atalho_local(mensagem_usuario)
            if atalho:
                logger.info("⚡ Atalho local (0 tokens): %s", atalho["intencao"])
                return atalho

        conteudo = []
        if arquivo_bytes and mime_type:
            b64 = base64.b64encode(arquivo_bytes).decode()
            conteudo.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            })
        conteudo.append({"type": "text", "text": f"{mensagem_usuario or ''}\n{PROMPT_TEXTO}"})

        response = requests.post(
            f"{GROQ_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODELO_VISAO if arquivo_bytes else MODELO_TEXTO,
                "messages": [{"role": "user", "content": conteudo}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 300,
            },
            timeout=60,
        )
        response.raise_for_status()
        dados = response.json()

        # Log de uso de tokens
        usage = dados.get("usage", {})
        logger.info(
            "📊 Tokens usados -> Prompt: %d | Resposta: %d | Total: %d",
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0),
        )

        return json.loads(dados["choices"][0]["message"]["content"])

    except Exception as e:
        logger.error(f"Erro na IA: {e}")
        return {"intencao": "erro_api"}

if __name__ == "__main__":
    # Smoke test: python -m app.services.brain (precisa de GROQ_API_KEY válida)
    logging.basicConfig(level=logging.INFO)

    atalho = interpretar_mensagem("me dê um resumo")
    print(atalho)
    assert atalho == {"intencao": "resumo"}, "Falhou: atalho local de resumo"

    resultado = interpretar_mensagem("Comprei uma TV em 10x de 200 no crédito")
    print(resultado)
    assert resultado.get("intencao") == "transacao", "Falhou: esperava intencao=transacao"
    dados = resultado["dados"]
    assert dados.get("parcelas") == 10, f"Falhou: esperava 10 parcelas, veio {dados.get('parcelas')}"
    assert dados.get("valor") == 200.0, f"Falhou: esperava valor 200.0, veio {dados.get('valor')}"
    print("OK")
