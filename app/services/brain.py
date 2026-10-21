import os
import json
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Prompt compacto: mesma extração, ~60% menos tokens que a versão original
PROMPT_TEXTO = """Responda APENAS com JSON. Classifique a entrada (texto, áudio ou imagem de comprovante):

1. Transação: {"intencao":"transacao","dados":{"descricao":"resumo do item","valor":0.00,"categoria":"Alimentacao|Transporte|Lazer|Casa|Contas|Servicos|Outros","metodo_pagamento":"Credito|Debito|Pix|Dinheiro","tipo":"Saida|Entrada","parcelas":1,"assinatura":false}}
- Fatura ou boleto (cartão de crédito, luz, água, internet, telefone): categoria "Contas".
- Se tipo "Entrada", use categoria "Salario|Extra|Rendimentos|Vendas|Presente|Outros" (salário/pagamento mensal = Salario; freela/bico = Extra; juros/dividendos/aluguel recebido = Rendimentos).
- Compra parcelada (ex: "10x de 200", "em 12 vezes"): "parcelas" = quantidade e "valor" = valor de UMA parcela (se o usuário der o total, divida pelo número de parcelas).
- Compra à vista: "parcelas": 1.
- Assinatura/mensalidade recorrente ("assinei Netflix", "mensalidade da academia"): "assinatura": true.
2. Resumo/saldo/gráfico: {"intencao":"resumo"}
3. Planilha/excel/exportar: {"intencao":"exportacao"}
4. Ajuda/capacidades: {"intencao":"ajuda"}
5. Listar assinaturas/gastos fixos mensais ("quais assinaturas tenho?"): {"intencao":"assinaturas"}
6. Conversa (saudação, papo, dúvida sobre o sistema ou sobre os gastos): {"intencao":"conversa","resposta":"resposta breve, simpática e natural em português, como um assistente pessoal"}
- Saudações ("olá", "bom dia") e perguntas NUNCA são transações. Transação exige valor em dinheiro explícito.
- Dúvidas sobre gastos: responda usando o contexto financeiro fornecido; para detalhes além dele, sugira pedir um "resumo".
- Estilo de conversa e esclarecimentos: direto, acolhedor e enxuto, em português brasileiro. Responda em até 3 parágrafos curtos, separando assuntos com uma linha em branco. Use no máximo um emoji e destaque com *negrito* apenas valores ou um título curto quando ajudar. Para listar, use linhas iniciadas por •. Nunca use tabelas, títulos com #, blocos de código ou longas listas de comandos. Valores sempre como R$ 1.234,56. Evite repetir o pedido e termine com uma ação só quando necessária.
- A diferença entre entradas e saídas é o resultado dos registros do mês, não um saldo bancário consultado.

Assistente pessoal (prioridade sobre transações quando o pedido for agendar algo):
7. Criar lembrete: {"intencao":"lembrete_criar","dados":{"descricao":"pagar aluguel","data":"YYYY-MM-DDTHH:MM:SS","repeticao":"nenhuma|diaria|semanal|mensal|dias_uteis"}}
8. Listar lembretes: {"intencao":"lembretes"}
9. Alterar lembrete identificado por número: {"intencao":"lembrete_alterar","dados":{"id":1,"acao":"concluir|cancelar|adiar","minutos":10}}
10. Consultar calendário: {"intencao":"agenda","dados":{"inicio":"YYYY-MM-DDT00:00:00","fim":"YYYY-MM-DDT00:00:00"}}. O fim é exclusivo. Sem período, use dados {} para hoje.
11. Criar compromisso: {"intencao":"evento_criar","dados":{"titulo":"Dentista","inicio":"YYYY-MM-DDTHH:MM:SS","fim":"YYYY-MM-DDTHH:MM:SS","aviso_minutos":30}}
12. Ver resumo do dia (agenda + lembretes + finanças): {"intencao":"hoje"}. Resumo financeiro/gráfico continua sendo "resumo".
13. Configurar envio diário: {"intencao":"resumo_diario_config","dados":{"horario":"08:00"}}; para desligar, dados {"desativar":true}.
- Use a data/hora atual e o fuso fornecidos para resolver amanhã, sexta, daqui a 10 minutos e recorrências. Retorne horários locais sem offset, a menos que o usuário especifique outro fuso.
- Nunca invente data, hora, duração de compromisso, identificador ou sucesso de uma ação. Aviso de evento não informado: 30 minutos.
- Se faltarem dados ou houver ambiguidade, retorne {"intencao":"esclarecer","resposta":"pergunta curta pedindo para reenviar o pedido completo com o dado faltante"}.
- Lembrete mensal sem horário exige esclarecimento. A primeira ocorrência deve estar no futuro. Não registre como gasto um pedido para lembrar de pagar algo.
- Não confirme nem descarte eventos: esses pedidos exigem /confirmar ID ou /descartar ID. Não diga que consultou ou criou algo em uma resposta de conversa.

Se receber imagem: extraia o valor total e o estabelecimento."""

# ponytail: atalho por palavra-chave — intenções simples não gastam nenhum token
ATALHOS = {
    "resumo": {"resumo", "saldo", "grafico", "gráfico", "dashboard", "balanco", "balanço"},
    "exportacao": {"planilha", "excel", "exportar", "csv", "extrato"},
    "ajuda": {"ajuda", "help", "comandos", "funcoes", "funções"},
    # só o plural: "assinatura" no singular costuma aparecer em transações ("paguei a assinatura X")
    "assinaturas": {"assinaturas"},
}

def _atalho_local(texto):
    normalizado = texto.lower().strip().rstrip("?!.")
    pessoais = {"agenda": "agenda", "minha agenda": "agenda", "lembretes": "lembretes",
                "meus lembretes": "lembretes", "hoje": "hoje", "resumo do dia": "hoje",
                "resumo diário": "hoje", "resumo diario": "hoje"}
    if normalizado in pessoais:
        return {"intencao": pessoais[normalizado]}
    palavras = set(texto.lower().split())
    if palavras & {"lembra", "lembre", "lembrar", "lembrete", "lembretes", "agenda", "agendar",
                   "calendário", "calendario", "diário", "diario", "diariamente"}:
        return None
    if len(palavras) <= 5:
        for intencao, chaves in ATALHOS.items():
            if palavras & chaves:
                return {"intencao": intencao}
    return None

def interpretar_mensagem(mensagem_usuario=None, arquivo_bytes=None, mime_type=None, contexto=None, contexto_temporal=None):
    try:
        if mensagem_usuario and not arquivo_bytes:
            atalho = _atalho_local(mensagem_usuario)
            if atalho:
                logger.info("⚡ Atalho local (0 tokens): %s", atalho["intencao"])
                return atalho

        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        modelo = os.getenv("OPENROUTER_MODEL", "").strip()
        if not api_key or not modelo:
            logger.error("Configure OPENROUTER_API_KEY e OPENROUTER_MODEL no ambiente.")
            return {"intencao": "erro_api"}

        conteudo = []
        if arquivo_bytes and mime_type:
            b64 = base64.b64encode(arquivo_bytes).decode()
            media_type = mime_type.split(";", 1)[0].strip().lower()
            if media_type.startswith("audio/"):
                formato = media_type.split("/", 1)[1]
                formato = {"mpeg": "mp3", "mp4": "m4a", "x-wav": "wav"}.get(formato, formato)
                conteudo.append({
                    "type": "input_audio",
                    "input_audio": {"data": b64, "format": formato},
                })
            else:
                conteudo.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64}"},
                })
        texto_prompt = f"{mensagem_usuario or ''}\n{PROMPT_TEXTO}"
        if contexto:
            texto_prompt += f"\nContexto financeiro do usuário (mês atual): {contexto}"
        if contexto_temporal:
            texto_prompt += f"\nContexto temporal para agendamentos: {contexto_temporal}"
        conteudo.append({"type": "text", "text": texto_prompt})

        response = requests.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": modelo,
                "messages": [{"role": "user", "content": conteudo}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 600,
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
    # Smoke test: python -m app.services.brain (precisa de OPENROUTER_API_KEY e OPENROUTER_MODEL)
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

    conversa = interpretar_mensagem("Olá Jarvis, tudo bem?", contexto="entradas R$ 5.000,00, gastos R$ 1.200,00")
    print(conversa)
    assert conversa.get("intencao") == "conversa", f"Falhou: 'Olá' devia ser conversa, veio {conversa.get('intencao')}"
    assert conversa.get("resposta"), "Falhou: conversa sem resposta"

    atalho_ass = interpretar_mensagem("quais assinaturas eu tenho?")
    print(atalho_ass)
    assert atalho_ass == {"intencao": "assinaturas"}, "Falhou: atalho local de assinaturas"

    ent = interpretar_mensagem("Recebi 5000 de salário no pix")
    print(ent)
    assert ent.get("intencao") == "transacao", f"Falhou: esperava transacao, veio {ent.get('intencao')}"
    assert ent["dados"].get("tipo") == "Entrada", f"Falhou: esperava Entrada, veio {ent['dados'].get('tipo')}"
    assert ent["dados"].get("categoria") == "Salario", f"Falhou: esperava Salario, veio {ent['dados'].get('categoria')}"

    ass = interpretar_mensagem("Assinei o Spotify por 21,90 no crédito")
    print(ass)
    assert ass.get("intencao") == "transacao", f"Falhou: esperava transacao, veio {ass.get('intencao')}"
    assert ass["dados"].get("assinatura") is True, "Falhou: 'assinei' devia marcar assinatura=true"
    assert ass["dados"].get("valor") == 21.9, f"Falhou: esperava 21.9, veio {ass['dados'].get('valor')}"
    print("OK")
