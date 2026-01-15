import os
import json
from google import genai
from dotenv import load_dotenv

load_dotenv()

def interpretar_mensagem(mensagem_usuario: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
        
    client = genai.Client(api_key=api_key)

    prompt = f"""
    Você é o cérebro de um assistente financeiro. Analise a mensagem: "{mensagem_usuario}"

    Identifique a intenção e retorne APENAS um JSON.

    CENÁRIO 1: Registro de Transação (ex: "gastei 10 reais", "recebi 50", "uber de 20").
    Retorne:
    {{
        "intencao": "transacao",
        "dados": {{
            "descricao": "resumo curto",
            "valor": 0.00,
            "categoria": "Alimentacao, Transporte, Lazer, Casa, Servicos, Outros",
            "metodo_pagamento": "Credito, Debito, Pix, Dinheiro",
            "tipo": "Saida" (ou Entrada)
        }}
    }}

    CENÁRIO 2: Pedido de Resumo/Gráfico (ex: "me dê um resumo", "quanto gastei?", "ver gastos").
    Retorne:
    {{
        "intencao": "resumo"
    }}

    CENÁRIO 3: Exportação de Dados (ex: "exportar para excel", "baixar planilha", "me dá o csv").
    Retorne:
    {{
        "intencao": "exportacao"
    }}

    Se não entender, retorne null.
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