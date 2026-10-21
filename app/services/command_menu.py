CATALOGO_COMANDOS = {
    "Geral": (
        ("menu", "", "Mostrar todos os comandos"),
        ("comandos", "", "Mostrar todos os comandos"),
        ("start", "", "Apresentação do Jarvis"),
        ("ajuda", "", "Ajuda com exemplos de uso"),
        ("meuid", "", "Consultar seu identificador neste canal"),
    ),
    "Finanças": (
        ("resumo", "", "Gráficos e balanço financeiro do mês"),
        ("exportar", "", "Exportar transações para Excel"),
        ("meta", "CATEGORIA VALOR", "Definir limite de gastos"),
        ("fatura", "DIA", "Definir o dia de fechamento do cartão"),
        ("parcelas", "", "Listar parcelamentos"),
        ("cancelarparcela", "ID", "Cancelar um parcelamento"),
        ("assinaturas", "", "Listar assinaturas e gastos fixos"),
        ("cancelarassinatura", "ID", "Cancelar uma assinatura"),
    ),
    "Lembretes": (
        ("lembretes", "", "Listar lembretes ativos"),
        ("adiar", "ID MINUTOS", "Adiar um lembrete"),
        ("concluir", "ID", "Encerrar um lembrete, inclusive recorrente"),
        ("cancelarlembrete", "ID", "Cancelar um lembrete"),
    ),
    "Calendário": (
        ("agenda", "", "Consultar compromissos de hoje"),
        ("calendarios", "", "Listar calendários conectados"),
        ("confirmar", "ID", "Confirmar a criação de um evento"),
        ("descartar", "ID", "Descartar um pedido de evento"),
    ),
    "Seu dia": (
        ("hoje", "", "Resumo de agenda, lembretes e finanças"),
        ("resumodiario", "HH:MM | off", "Ativar ou desativar o envio diário"),
        ("fuso", "America/Sao_Paulo", "Configurar seu fuso horário"),
    ),
}


def menu_comandos(prefixo=""):
    linhas = ["*Comandos do Jarvis*" if not prefixo else f"*Sugestões para /{prefixo}:*"]
    encontrados = False
    for grupo, comandos in CATALOGO_COMANDOS.items():
        correspondentes = [comando for comando in comandos if comando[0].startswith(prefixo)]
        if not correspondentes:
            continue
        encontrados = True
        linhas.append(f"\n*{grupo}*")
        for nome, argumentos, descricao in correspondentes:
            uso = f"/{nome}" + (f" {argumentos}" if argumentos else "")
            linhas.append(f"{uso}\n{descricao}\n")
    if not encontrados:
        return "Comando não encontrado. Envie / ou /menu para ver todas as opções."
    linhas.append("Envie o comando com os argumentos indicados. ID é o número mostrado na lista.")
    if not prefixo:
        linhas.append("Filtrar opções: /ca · Exemplos de pedidos: /ajuda")
    return "\n".join(linhas).strip()
