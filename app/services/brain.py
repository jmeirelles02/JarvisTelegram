import os
import json
import logging
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

def interpretar_mensagem(mensagem_usuario=None, arquivo_bytes=None, mime_type=None):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
        
    client = genai.Client(api_key=api_key)

    prompt_texto = """
    Analise a entrada (texto, imagem ou áudio) e extraia a intenção em JSON.
    
    CENÁRIO 1: Transação (Gasto ou Ganho)
    Retorne:
    {
        "intencao": "transacao",
        "dados": {
            "descricao": "resumo do item",
            "valor": 0.00,
            "categoria": "Alimentacao, Transporte, Lazer, Casa, Servicos, Outros",
            "metodo_pagamento": "Credito, Debito, Pix, Dinheiro",
            "tipo": "Saida" (ou Entrada)
        }
    }

    CENÁRIO 2: Resumo/Gráfico (ex: "resumo", "grafico", "saldo")
    Retorne: {"intencao": "resumo"}

    CENÁRIO 3: Exportação (ex: "planilha", "excel", "csv")
    Retorne: {"intencao": "exportacao"}

    CENÁRIO 4: Ajuda/Capacidades (ex: "o que você faz?", "ajuda", "funções", "comandos")
    Retorne: {"intencao": "ajuda"}

    Se receber IMAGEM: Extraia o total, estabelecimento e data.
    Se receber ÁUDIO: Transcreva e classifique.
    """

    conteudo = []
    
    if arquivo_bytes and mime_type:
        conteudo.append(types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type))
    
    if mensagem_usuario:
        conteudo.append(mensagem_usuario)
        
    conteudo.append(prompt_texto)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=conteudo
        )
        
        # Log de uso de tokens
        usage = getattr(response, 'usage_metadata', None)
        if usage:
            prompt_tokens = getattr(usage, 'prompt_token_count', 0)
            candidates_tokens = getattr(usage, 'candidates_token_count', 0)
            total_tokens = getattr(usage, 'total_token_count', 0)
            logger.info(
                "📊 Tokens usados -> Prompt: %d | Resposta: %d | Total: %d",
                prompt_tokens, candidates_tokens, total_tokens
            )
        else:
            logger.info("📊 Informação de tokens não disponível nesta resposta.")
        
        texto_limpo = response.text.replace("```json", "").replace("```", "")
        return json.loads(texto_limpo)

    except Exception as e:
        print(f"Erro na IA: {e}")
        return None