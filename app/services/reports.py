import calendar
import io
import textwrap
import threading
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, MaxNLocator

from app.services.personal_time import agora_utc, utc, validar_fuso
from app.services.presentation import mensagem, moeda, nome


FUNDO = "#F3F5FA"
PAPEL = "#FFFFFF"
TEXTO = "#17263D"
MUTED = "#66758B"
AZUL = "#4361DA"
VERDE = "#118574"
ALERTA = "#B66B19"
CRITICO = "#BB4052"
TRILHO = "#E5EAF3"
TRAVA_GRAFICO = threading.Lock()


def somar_por(transacoes, atributo):
    totais = defaultdict(float)
    for item in transacoes:
        totais[nome(getattr(item, atributo))] += item.valor
    return dict(sorted(totais.items(), key=lambda item: (-item[1], item[0])))


def dados_resumo(transacoes):
    entradas = [item for item in transacoes if item.tipo == "Entrada"]
    saidas = [item for item in transacoes if item.tipo == "Saida"]
    recebido = sum(item.valor for item in entradas)
    gasto = sum(item.valor for item in saidas)
    return {"recebido": recebido, "gasto": gasto, "resultado": recebido - gasto,
            "categorias": somar_por(saidas, "categoria"), "pagamentos": somar_por(saidas, "metodo_pagamento"),
            "origens": somar_por(entradas, "categoria"), "quantidade": len(transacoes)}


def texto_resumo(transacoes, titulo):
    dados = dados_resumo(transacoes)
    leitura = []
    if dados["categorias"] and dados["gasto"]:
        categoria, valor = next(iter(dados["categorias"].items()))
        leitura.append(f"Maior gasto: {categoria} · {moeda(valor)} ({valor / dados['gasto']:.0%} das saídas).")
    if dados["recebido"] > 0:
        leitura.append(f"As saídas equivalem a {dados['gasto'] / dados['recebido']:.0%} das entradas registradas.")
    elif dados["gasto"]:
        leitura.append("Nenhuma entrada registrada neste mês; a comparação com a renda ainda está incompleta.")
    return mensagem(f"Resumo financeiro · {titulo}",
                    f"Entradas  *{moeda(dados['recebido'])}*\nSaídas  *{moeda(dados['gasto'])}*\n"
                    f"Resultado  *{moeda(dados['resultado'])}*",
                    "\n".join(leitura),
                    rodape=f"{dados['quantidade']} registro{'s' if dados['quantidade'] != 1 else ''} no período. "
                    "Resultado = entradas − saídas; não representa o saldo da conta bancária.")


def _agrupar(totais, limite):
    itens = list(totais.items())
    if len(itens) <= limite:
        return itens
    return itens[:limite - 1] + [("Demais categorias", sum(valor for categoria, valor in itens[limite - 1:]))]


def _titulo(eixo, titulo, subtitulo):
    eixo.set_facecolor(FUNDO)
    eixo.set_title(titulo, loc="left", fontsize=17, fontweight="bold", color=TEXTO, pad=34)
    eixo.text(0, 1.04, subtitulo, transform=eixo.transAxes, fontsize=11, color=MUTED, va="bottom")
    for borda in eixo.spines.values():
        borda.set_visible(False)


def _barras(eixo, totais, cor, titulo, subtitulo, limite=6, compacto=False):
    _titulo(eixo, titulo, subtitulo)
    eixo.set_xlim(0, 1)
    eixo.set_xticks([])
    eixo.set_yticks([])
    itens = _agrupar(totais, limite)
    if not itens:
        eixo.set_ylim(0, 1)
        eixo.text(0, 0.6, "Nenhum registro no período.", color=MUTED, fontsize=13)
        return
    total = sum(totais.values())
    eixo.set_ylim(-0.5, len(itens) - 0.3)
    for indice, (categoria, valor) in enumerate(itens):
        posicao = len(itens) - indice - 1
        tamanho = 11 if compacto else 13
        rotulo = textwrap.shorten(categoria, width=23 if compacto else 43, placeholder="…")
        eixo.text(0, posicao + 0.16, rotulo, fontsize=tamanho, color=TEXTO, va="bottom")
        eixo.text(1, posicao + 0.16, f"{moeda(valor)} · {valor / total:.0%}" if total else moeda(valor),
                  ha="right", va="bottom", fontsize=tamanho, color=TEXTO, fontweight="bold")
        eixo.barh(posicao - 0.1, 1, height=0.12, color=TRILHO)
        eixo.barh(posicao - 0.1, valor / total if total else 0, height=0.12, color=cor)


def _cartoes(eixo, dados):
    eixo.set_axis_off()
    valores = (("ENTRADAS", dados["recebido"], VERDE, "Valores recebidos"),
               ("SAÍDAS", dados["gasto"], AZUL, "Valores gastos"),
               ("RESULTADO", dados["resultado"], VERDE if dados["resultado"] >= 0 else CRITICO, "Entradas menos saídas"))
    for indice, (rotulo, valor, cor, legenda) in enumerate(valores):
        comeco = indice * 0.345
        eixo.add_patch(FancyBboxPatch((comeco, 0.04), 0.31, 0.9,
                                     boxstyle="round,pad=0.008,rounding_size=0.035", linewidth=0,
                                     facecolor=PAPEL, transform=eixo.transAxes))
        eixo.text(comeco + 0.025, 0.73, rotulo, fontsize=10, color=MUTED, fontweight="bold", transform=eixo.transAxes)
        valor_formatado = moeda(valor)
        tamanho = min(21, 300 / max(1, len(valor_formatado)))
        eixo.text(comeco + 0.025, 0.39, valor_formatado, fontsize=tamanho, color=cor,
                  fontweight="bold", transform=eixo.transAxes)
        eixo.text(comeco + 0.025, 0.16, legenda, fontsize=10, color=MUTED, transform=eixo.transAxes)


def serie_acumulada(transacoes, fuso, agora=None):
    zona = validar_fuso(fuso)
    datas = [utc(item.data).astimezone(zona) for item in transacoes if item.data is not None]
    local = (agora or agora_utc()).astimezone(zona)
    referencia = max(datas) if datas else local
    ultimo_dia = max(data.day for data in datas) if datas else 1
    if (referencia.year, referencia.month) == (local.year, local.month):
        ultimo_dia = max(ultimo_dia, local.day)
    else:
        ultimo_dia = calendar.monthrange(referencia.year, referencia.month)[1]
    recebidos = [0.0] * ultimo_dia
    gastos = [0.0] * ultimo_dia
    for item in transacoes:
        if item.data is None:
            continue
        data = utc(item.data).astimezone(zona)
        if (data.year, data.month) != (referencia.year, referencia.month):
            continue
        if item.tipo == "Entrada":
            recebidos[data.day - 1] += item.valor
        elif item.tipo == "Saida":
            gastos[data.day - 1] += item.valor
    for indice in range(1, ultimo_dia):
        recebidos[indice] += recebidos[indice - 1]
        gastos[indice] += gastos[indice - 1]
    return list(range(1, ultimo_dia + 1)), recebidos, gastos


def _curva(eixo, transacoes, fuso, agora):
    _titulo(eixo, "02  Como o mês evoluiu", "Dias do mês na horizontal; cada linha soma os registros até aquela data.")
    dias, entradas, saidas = serie_acumulada(transacoes, fuso, agora)
    eixo.plot(dias, entradas, color=VERDE, linewidth=2.8, label="Entradas", drawstyle="steps-post")
    eixo.plot(dias, saidas, color=AZUL, linewidth=2.8, linestyle="--", label="Saídas", drawstyle="steps-post")
    eixo.set_xlim(1, max(2, dias[-1]))
    eixo.set_ylim(bottom=0, top=max(entradas + saidas + [1]) * 1.25)
    eixo.grid(axis="y", color=TRILHO, linewidth=0.8)
    eixo.set_axisbelow(True)
    eixo.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=7))
    eixo.yaxis.set_major_locator(MaxNLocator(nbins=4))
    eixo.yaxis.set_major_formatter(FuncFormatter(lambda valor, posicao: _moeda_eixo(valor)))
    eixo.tick_params(axis="both", length=0, labelsize=10, colors=MUTED, pad=8)
    eixo.legend(loc="upper left", frameon=False, ncol=2, fontsize=11)


def _moeda_eixo(valor):
    for divisor, unidade in ((1_000_000_000, "bi"), (1_000_000, "mi"), (1000, "mil")):
        if abs(valor) >= divisor:
            numero = f"{valor / divisor:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return f"R$ {numero} {unidade}"
    return f"R$ {valor:g}".replace(".", ",")


def _metas(eixo, metas, categorias):
    _titulo(eixo, "03  Seus limites de gastos", "Barra cheia = 100% do limite; o texto mostra eventuais excessos.")
    eixo.set_xlim(0, 1)
    eixo.set_ylim(-0.65, len(metas) - 0.1)
    eixo.set_xticks([])
    eixo.set_yticks([])
    for indice, meta in enumerate(metas):
        posicao = len(metas) - indice - 1
        gasto = categorias.get(nome(meta.categoria), 0)
        percentual = gasto / meta.valor_limite
        restante = meta.valor_limite - gasto
        cor = CRITICO if percentual > 1 else ALERTA if percentual >= 0.8 else VERDE
        eixo.text(0, posicao + 0.28, textwrap.shorten(nome(meta.categoria), width=42, placeholder="…"),
                  fontsize=13, color=TEXTO, fontweight="bold")
        eixo.text(1, posicao + 0.28, f"{percentual:.0%} utilizado", fontsize=12, color=cor, ha="right", fontweight="bold")
        eixo.barh(posicao + 0.02, 1, height=0.11, color=TRILHO)
        eixo.barh(posicao + 0.02, min(1, max(0, percentual)), height=0.11, color=cor)
        estado = f"Disponível: {moeda(restante)}" if restante >= 0 else f"Excedido: {moeda(-restante)}"
        eixo.text(0, posicao - 0.27, f"{moeda(gasto)} de {moeda(meta.valor_limite)}", fontsize=11, color=MUTED)
        eixo.text(1, posicao - 0.27, estado, fontsize=11, color=cor, ha="right")


def gerar_dashboard(transacoes, metas=None, titulo="Resumo financeiro", fuso="America/Sao_Paulo", agora=None):
    if not transacoes:
        return None
    dados = dados_resumo(transacoes)
    metas = sorted((meta for meta in (metas or []) if meta.valor_limite > 0), key=lambda meta: nome(meta.categoria))
    alturas = [0.9, 1.4, max(1.4, min(6, len(dados["categorias"])) * 0.56), 2.7]
    if metas:
        alturas.append(max(1.5, len(metas) * 0.8))
    alturas.append(max(1.5, min(4, max(len(dados["pagamentos"]), len(dados["origens"]))) * 0.6))
    figura = None
    with TRAVA_GRAFICO, matplotlib.rc_context({"font.family": "DejaVu Sans", "text.parse_math": False}):
        try:
            figura = Figure(figsize=(11, sum(alturas) + 0.66 * len(alturas)), facecolor=FUNDO)
            FigureCanvasAgg(figura)
            grade = figura.add_gridspec(len(alturas), 1, height_ratios=alturas, hspace=0.64,
                                       left=0.095, right=0.925, top=0.975, bottom=0.055)
            cabecalho = figura.add_subplot(grade[0])
            cabecalho.set_axis_off()
            cabecalho.text(0, 0.88, "J A R V I S  /  FINANÇAS", fontsize=11, color=AZUL, fontweight="bold")
            cabecalho.text(0, 0.34, titulo, fontsize=27, color=TEXTO, fontweight="bold")
            cabecalho.text(1, 0.38, f"{dados['quantidade']} registro{'s' if dados['quantidade'] != 1 else ''}",
                           fontsize=11, ha="right", color=MUTED)
            _cartoes(figura.add_subplot(grade[1]), dados)
            _barras(figura.add_subplot(grade[2]), dados["categorias"], AZUL, "01  Para onde foi seu dinheiro",
                    "Valor e participação de cada categoria no total das saídas.")
            _curva(figura.add_subplot(grade[3]), transacoes, fuso, agora)
            linha = 4
            if metas:
                _metas(figura.add_subplot(grade[linha]), metas, dados["categorias"])
                linha += 1
            detalhe = grade[linha].subgridspec(1, 2, wspace=0.17)
            _barras(figura.add_subplot(detalhe[0]), dados["pagamentos"], AZUL, "Como você pagou",
                    "Participação nas saídas.", limite=4, compacto=True)
            _barras(figura.add_subplot(detalhe[1]), dados["origens"], VERDE, "De onde veio",
                    "Participação nas entradas.", limite=4, compacto=True)
            figura.text(0.095, 0.022, "Resultado = entradas − saídas registradas. Não é o saldo da conta bancária.",
                        fontsize=10, color=MUTED)
            buffer = io.BytesIO()
            figura.savefig(buffer, format="png", dpi=125, facecolor=FUNDO)
            buffer.seek(0)
            return buffer
        finally:
            if figura is not None:
                figura.clear()
