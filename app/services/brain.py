import os
import json
import re
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

    CENÁRIO 4: Ajuda/Capacidades (ex: "o que você faz?", "ajuda", "funções")
    Retorne: {"intencao": "ajuda"}

    Se receber IMAGEM: Extraia o total, estabelecimento e data.
    Se receber ÁUDIO: Transcreva o conteúdo. Se o áudio não for claro sobre gastos, retorne null.
    IMPORTANTE: Retorne APENAS o JSON, sem markdown.
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
        
        print(f"Resposta Bruta da IA: {response.text}")

        match = re.search(r"\{.*\}", response.text, re.DOTALL)
        
        if match:
            json_str = match.group(0)
            return json.loads(json_str)
        else:
            return None

    except Exception as e:
        print(f"Erro na IA: {e}")
        return None