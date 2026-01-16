import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

def interpretar_mensagem(mensagem_usuario=None, arquivo_bytes=None, mime_type=None):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
        
    client = genai.Client(api_key=api_key)

    prompt_texto = """
    Analise a entrada (texto, imagem ou áudio) e extraia as informações financeiras em JSON.
    
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

    CENÁRIO 2: Resumo/Gráfico
    Retorne: {"intencao": "resumo"}

    CENÁRIO 3: Exportação
    Retorne: {"intencao": "exportacao"}

    Se receber IMAGEM: Extraia o total, estabelecimento (descricao) e data.
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
            model="gemini-3-flash-preview",
            contents=conteudo
        )
        
        texto_limpo = response.text.replace("```json", "").replace("```", "")
        return json.loads(texto_limpo)

    except Exception as e:
        print(f"Erro na IA: {e}")
        return None