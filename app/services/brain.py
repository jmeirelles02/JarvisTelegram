import os
import json
from google import genai
from dotenv import load_dotenv

load_dotenv()

def interpretar_mensagem(mensagem_usuario: str):
    """
    Identifica se é uma Transação ou um Pedido de Resumo.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Erro: Chave GEMINI_API_KEY não encontrada.")
        return None
        
    client = genai.Client(api_key=api_key)

    prompt = f"""
    Você é o cérebro de um assistente financeiro. Analise a mensagem: "{mensagem_usuario}"

    Sua missão é identificar a intenção do usuário e retornar APENAS um JSON.

    CENÁRIO 1: O usuário está informando um gasto ou ganho (ex: "gastei 10 reais", "recebi 50", "uber de 20").
    Retorne:
    {{
        "intencao": "transacao",
        "dados": {{
            "descricao": "resumo curto do item",
            "valor": 0.00 (float),
            "categoria": "escolha: Alimentacao, Transporte, Lazer, Casa, Servicos, Outros",
            "metodo_pagamento": "escolha: Credito, Debito, Pix, Dinheiro",
            "tipo": "Saida" (ou Entrada)
        }}
    }}

    CENÁRIO 2: O usuário quer ver relatórios, totais ou gráficos (ex: "me dê um resumo", "quanto gastei?", "gerar gráfico").
    Retorne:
    {{
        "intencao": "resumo"
    }}

    Se não entender nada, retorne null.
    """

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )
        
        texto = response.text.replace("```json", "").replace("```", "")
        return json.loads(texto)

    except Exception as e:
        print(f"Erro na IA: {e}")
        return None