NOMES = {
    "Alimentacao": "Alimentação", "Servicos": "Serviços", "Salario": "Salário",
    "Credito": "Crédito", "Debito": "Débito", "Saida": "Saída",
}
REPETICOES = {"nenhuma": "Uma vez", "diaria": "Todos os dias", "semanal": "Toda semana",
             "mensal": "Todo mês", "dias_uteis": "Dias úteis"}


def moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def nome(valor):
    texto = " ".join(str(valor or "Outros").split())
    return NOMES.get(texto, texto)


def bloco(titulo, *linhas):
    corpo = "\n".join(str(linha) for linha in linhas if linha is not None and linha != "")
    return f"*{titulo}*" + (f"\n{corpo}" if corpo else "")


def mensagem(titulo, *secoes, rodape=None):
    partes = [f"*{titulo}*"] + [secao for secao in secoes if secao]
    if rodape:
        partes.append(rodape)
    return "\n\n".join(partes)


def erro(texto, acao=None):
    return mensagem("Não consegui concluir", texto, rodape=acao)


def transacao(dados, assinatura=False, alerta=""):
    titulo = "Entrada registrada" if dados["tipo"] == "Entrada" else "Gasto registrado"
    detalhe = f"{nome(dados['categoria'])} · {nome(dados['metodo_pagamento'])}"
    recorrencia = "Assinatura mensal ativada. Próximas cobranças serão registradas automaticamente." if assinatura else None
    return mensagem(f"✓ {titulo}", bloco(moeda(dados["valor"]), nome(dados["descricao"]), detalhe),
                    recorrencia, alerta.strip(), rodape="Ver assinaturas: /assinaturas" if assinatura else None)


def lista_assinaturas(assinaturas):
    if not assinaturas:
        return mensagem("Assinaturas", "Nenhuma assinatura ativa.",
                        rodape="Para adicionar: Assinei Netflix por 39,90 no crédito.")
    itens = [bloco(f"{nome(item.descricao)} · #{item.id}",
                   f"{moeda(item.valor)}/mês · Renova em {item.proxima_data:%d/%m/%Y}") for item in assinaturas]
    return mensagem("Assinaturas", *itens, bloco("Total mensal", moeda(sum(item.valor for item in assinaturas))),
                    rodape="Cancelar: /cancelarassinatura ID")


def lista_parcelas(parcelamentos):
    if not parcelamentos:
        return mensagem("Parcelamentos", "Nenhum parcelamento ativo.",
                        rodape="Para adicionar: Comprei uma TV em 10x de 200 no crédito.")
    itens = [bloco(f"{nome(item.descricao)} · #{item.id}",
                   f"{moeda(item.valor_parcela)} por parcela · {item.parcelas_pagas}/{item.parcelas_total} registradas",
                   f"Próxima: {item.proxima_data:%d/%m/%Y} · Restam {item.parcelas_total - item.parcelas_pagas}")
             for item in parcelamentos]
    return mensagem("Parcelamentos", *itens, rodape="Cancelar: /cancelarparcela ID")


def aviso_meta(categoria, gasto, limite):
    percentual = gasto / limite * 100 if limite > 0 else 0
    if gasto > limite:
        situacao = f"Limite excedido em {moeda(gasto - limite)}."
    elif gasto == limite:
        situacao = "Limite do mês atingido."
    else:
        situacao = f"Restam {moeda(limite - gasto)} no mês."
        if percentual >= 80:
            situacao += " Atenção: perto do limite."
    return bloco(f"Meta · {nome(categoria)}", f"{moeda(gasto)} de {moeda(limite)} · {percentual:.0f}% utilizado", situacao)
