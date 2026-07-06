import os
import logging
import asyncio
import io
import matplotlib.pyplot as plt

plt.rcParams['text.parse_math'] = False  # sem isso, "R$ x ... R$ y" vira fórmula matemática
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from sqlalchemy import func, extract
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from web_alive import manter_vivo
from app.services import brain
from app import models, database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()

models.Base.metadata.create_all(bind=database.engine)

def salvar_transacao(dados_ia, user_id):
    session = database.SessionLocal()
    try:
        nova = models.Transacao(**dados_ia)
        nova.user_id = user_id
        session.add(nova)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao salvar: {e}")
        return False
    finally:
        session.close()

def criar_parcelamento(dados, parcelas, user_id):
    session = database.SessionLocal()
    try:
        parc = models.Parcelamento(
            user_id=user_id,
            descricao=dados['descricao'],
            valor_parcela=dados['valor'],
            categoria=dados['categoria'],
            metodo_pagamento=dados['metodo_pagamento'],
            parcelas_total=parcelas,
            parcelas_pagas=1,
            proxima_data=datetime.now() + relativedelta(months=1),
        )
        session.add(parc)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao criar parcelamento: {e}")
        return False
    finally:
        session.close()

def listar_parcelamentos(user_id):
    session = database.SessionLocal()
    try:
        return session.query(models.Parcelamento).filter_by(user_id=user_id).all()
    finally:
        session.close()

def cancelar_parcelamento(parc_id, user_id):
    session = database.SessionLocal()
    try:
        parc = session.query(models.Parcelamento).filter_by(id=parc_id, user_id=user_id).first()
        if not parc:
            return None
        descricao = parc.descricao
        session.delete(parc)
        session.commit()
        return descricao
    except Exception as e:
        print(f"Erro ao cancelar parcelamento: {e}")
        return None
    finally:
        session.close()

def criar_assinatura(dados, user_id):
    session = database.SessionLocal()
    try:
        ass = models.Assinatura(
            user_id=user_id,
            descricao=dados['descricao'],
            valor=dados['valor'],
            categoria=dados['categoria'],
            metodo_pagamento=dados['metodo_pagamento'],
            proxima_data=datetime.now() + relativedelta(months=1),
        )
        session.add(ass)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao criar assinatura: {e}")
        return False
    finally:
        session.close()

def listar_assinaturas(user_id):
    session = database.SessionLocal()
    try:
        return session.query(models.Assinatura).filter_by(user_id=user_id).all()
    finally:
        session.close()

def cancelar_assinatura(ass_id, user_id):
    session = database.SessionLocal()
    try:
        ass = session.query(models.Assinatura).filter_by(id=ass_id, user_id=user_id).first()
        if not ass:
            return None
        descricao = ass.descricao
        session.delete(ass)
        session.commit()
        return descricao
    except Exception as e:
        print(f"Erro ao cancelar assinatura: {e}")
        return None
    finally:
        session.close()

def _texto_assinaturas(user_id):
    assinaturas = listar_assinaturas(user_id)
    if not assinaturas:
        return (
            "Nenhuma assinatura salva.\n"
            "Para criar, diga algo como: 'Assinei Netflix por 39,90 no crédito'."
        )
    linhas = ["🔄 *Suas assinaturas:*\n"]
    for a in assinaturas:
        linhas.append(
            f"`#{a.id}` {a.descricao} — {_fmt_reais(a.valor)}/mês "
            f"(renova {a.proxima_data.strftime('%d/%m/%Y')})"
        )
    linhas.append(f"\n💰 Total fixo mensal: *{_fmt_reais(sum(a.valor for a in assinaturas))}*")
    linhas.append("Para cancelar uma: /cancelarassinatura [número]")
    return "\n".join(linhas)

def processar_assinaturas_devidas():
    """Registra as assinaturas que renovaram e devolve os avisos [(user_id, msg)]."""
    session = database.SessionLocal()
    avisos = []
    try:
        devidas = session.query(models.Assinatura).filter(
            models.Assinatura.proxima_data <= datetime.now()
        ).all()
        for ass in devidas:
            session.add(models.Transacao(
                user_id=ass.user_id,
                descricao=f"{ass.descricao} (assinatura)",
                valor=ass.valor,
                categoria=ass.categoria,
                metodo_pagamento=ass.metodo_pagamento,
                tipo='Saida',
            ))
            ass.proxima_data = ass.proxima_data + relativedelta(months=1)
            avisos.append((ass.user_id, (
                f"🔄 *Assinatura renovada!*\n\n"
                f"📝 {ass.descricao}\n"
                f"💰 {_fmt_reais(ass.valor)}\n"
                f"📅 Próxima renovação: {ass.proxima_data.strftime('%d/%m/%Y')}"
            )))
        session.commit()
        return avisos
    except Exception as e:
        print(f"Erro ao processar assinaturas: {e}")
        return []
    finally:
        session.close()

def processar_parcelas_devidas():
    """Registra as parcelas que venceram e devolve os avisos [(user_id, msg)]."""
    session = database.SessionLocal()
    avisos = []
    try:
        devidas = session.query(models.Parcelamento).filter(
            models.Parcelamento.proxima_data <= datetime.now()
        ).all()
        for parc in devidas:
            parc.parcelas_pagas += 1
            session.add(models.Transacao(
                user_id=parc.user_id,
                descricao=f"{parc.descricao} (parcela {parc.parcelas_pagas}/{parc.parcelas_total})",
                valor=parc.valor_parcela,
                categoria=parc.categoria,
                metodo_pagamento=parc.metodo_pagamento,
                tipo='Saida',
            ))
            msg = (
                f"📆 *Parcela do mês registrada!*\n\n"
                f"📝 {parc.descricao} — parcela {parc.parcelas_pagas}/{parc.parcelas_total}\n"
                f"💰 R$ {parc.valor_parcela:.2f}"
            )
            if parc.parcelas_pagas >= parc.parcelas_total:
                msg += "\n\n✅ Essa foi a última parcela! Parcelamento concluído."
                session.delete(parc)
            else:
                parc.proxima_data = parc.proxima_data + relativedelta(months=1)
            avisos.append((parc.user_id, msg))
        session.commit()
        return avisos
    except Exception as e:
        print(f"Erro ao processar parcelas: {e}")
        return []
    finally:
        session.close()

async def vigiar_recorrencias(app):
    # ponytail: checagem a cada 6h basta para recorrências mensais e faz catch-up após restart
    loop = asyncio.get_running_loop()
    while True:
        avisos = await loop.run_in_executor(None, processar_parcelas_devidas)
        avisos += await loop.run_in_executor(None, processar_assinaturas_devidas)
        for user_id, msg in avisos:
            try:
                await app.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown')
            except Exception as e:
                print(f"Erro ao enviar aviso de recorrência: {e}")
        await asyncio.sleep(6 * 3600)

async def iniciar_tarefas(app):
    asyncio.create_task(vigiar_recorrencias(app))

def definir_meta_db(categoria, valor, user_id):
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria, user_id=user_id).first()
        if meta:
            meta.valor_limite = valor
        else:
            meta = models.Meta(categoria=categoria, valor_limite=valor, user_id=user_id)
            session.add(meta)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao definir meta: {e}")
        return False
    finally:
        session.close()

def _barra_progresso(pct):
    cheios = min(10, int(round(pct / 10)))
    return "▰" * cheios + "▱" * (10 - cheios)

def progresso_meta(categoria, user_id):
    """Linha visual do progresso da meta da categoria; '' se não houver meta."""
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria, user_id=user_id).first()
        if not meta or not meta.valor_limite:
            return ""

        agora = datetime.now()
        total_gasto = session.query(func.sum(models.Transacao.valor)).filter(
            models.Transacao.user_id == user_id,
            models.Transacao.categoria == categoria,
            models.Transacao.tipo == 'Saida',
            extract('month', models.Transacao.data) == agora.month,
            extract('year', models.Transacao.data) == agora.year
        ).scalar() or 0.0

        pct = (total_gasto / meta.valor_limite) * 100

        if pct >= 100:
            emoji = "🔴"
            aviso = f"\n🚨 Meta estourada em {_fmt_reais(total_gasto - meta.valor_limite)}!"
        elif pct >= 80:
            emoji = "🟠"
            aviso = f"\n⚠️ Quase no limite — restam {_fmt_reais(meta.valor_limite - total_gasto)}"
        else:
            emoji = "🟢"
            aviso = ""

        return (
            f"\n\n{emoji} *Meta de {categoria}:* {pct:.0f}%\n"
            f"{_barra_progresso(pct)}\n"
            f"{_fmt_reais(total_gasto)} de {_fmt_reais(meta.valor_limite)}{aviso}"
        )
    except Exception as e:
        print(f"Erro ao verificar meta: {e}")
        return ""
    finally:
        session.close()

def buscar_metas(user_id):
    session = database.SessionLocal()
    try:
        return session.query(models.Meta).filter_by(user_id=user_id).all()
    finally:
        session.close()

def buscar_todas_transacoes(user_id):
    session = database.SessionLocal()
    try:
        transacoes = session.query(models.Transacao).filter_by(user_id=user_id).all()
        return transacoes
    finally:
        session.close()

def buscar_transacoes_mes_atual(user_id):
    session = database.SessionLocal()
    try:
        mes_atual = datetime.now().month
        ano_atual = datetime.now().year
        
        transacoes = session.query(models.Transacao).filter(
            models.Transacao.user_id == user_id,
            extract('month', models.Transacao.data) == mes_atual,
            extract('year', models.Transacao.data) == ano_atual
        ).all()
        return transacoes
    finally:
        session.close()

def contexto_financeiro(user_id):
    """Resumo de uma linha do mês atual, enviado como contexto para a IA responder dúvidas."""
    transacoes = buscar_transacoes_mes_atual(user_id)
    if not transacoes:
        partes = ["Sem transações registradas neste mês."]
    else:
        entradas = sum(t.valor for t in transacoes if t.tipo == 'Entrada')
        saidas = sum(t.valor for t in transacoes if t.tipo == 'Saida')
        por_cat = _somar_por([t for t in transacoes if t.tipo == 'Saida'], 'categoria')
        cats = ", ".join(f"{c} {_fmt_reais(v)}" for c, v in sorted(por_cat.items(), key=lambda kv: -kv[1]))
        partes = [
            f"entradas {_fmt_reais(entradas)}, gastos {_fmt_reais(saidas)}, "
            f"saldo {_fmt_reais(entradas - saidas)}. Gastos por categoria: {cats or 'nenhum'}."
        ]
    assinaturas = listar_assinaturas(user_id)
    if assinaturas:
        fixos = ", ".join(f"{a.descricao} {_fmt_reais(a.valor)}" for a in assinaturas)
        partes.append(
            f"Assinaturas mensais fixas: {fixos} "
            f"(total {_fmt_reais(sum(a.valor for a in assinaturas))})."
        )
    return " ".join(partes)

def gerar_arquivo_excel(user_id):
    session = database.SessionLocal()
    try:
        query = session.query(models.Transacao).filter_by(user_id=user_id).statement
        df = pd.read_sql(query, session.bind)
        
        if 'data' in df.columns:
            df['data'] = pd.to_datetime(df['data']).dt.tz_localize(None)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Extrato')
        
        buffer.seek(0)
        return buffer
    except Exception as e:
        print(f"Erro ao gerar Excel: {e}")
        return None
    finally:
        session.close()

# Paleta validada (CVD-safe): azul p/ saídas, aqua p/ entradas, vermelho só p/ saldo negativo
COR_SAIDA = '#2a78d6'
COR_ENTRADA = '#1baf7a'
COR_FUNDO = '#fcfcfb'
COR_TEXTO = '#0b0b0b'
COR_TEXTO_2 = '#52514e'
COR_MUTED = '#898781'
COR_GRADE = '#e1e0d9'
COR_TRILHO = '#f0efec'
# Cores de status (reservadas p/ metas, nunca p/ séries)
STATUS_BOM = '#0ca30c'
STATUS_ALERTA = '#ec835a'
STATUS_CRITICO = '#d03b3b'
COR_SALDO_POSITIVO = '#006300'

def _fmt_reais(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _somar_por(transacoes, atributo):
    somas = {}
    for t in transacoes:
        chave = getattr(t, atributo) or "Outros"
        somas[chave] = somas.get(chave, 0) + t.valor
    # crescente: no barh o último item fica no topo, então o maior valor fica em cima
    return dict(sorted(somas.items(), key=lambda kv: kv[1]))

def _preparar_eixo(ax, titulo):
    ax.set_facecolor(COR_FUNDO)
    ax.set_title(titulo, loc='left', fontsize=15, color=COR_TEXTO, pad=12, fontweight='bold')
    for lado in ('top', 'right'):
        ax.spines[lado].set_visible(False)
    for lado in ('left', 'bottom'):
        ax.spines[lado].set_color(COR_GRADE)
    ax.tick_params(colors=COR_MUTED, labelsize=12)

def _barras_horizontais(ax, somas, cor, com_percentual=False):
    total = sum(somas.values()) or 1
    valores = list(somas.values())
    barras = ax.barh(list(somas.keys()), valores, color=cor, height=0.55)
    if com_percentual:
        rotulos = [f"{_fmt_reais(v)}  ({v / total:.0%})" for v in valores]
    else:
        rotulos = [_fmt_reais(v) for v in valores]
    ax.bar_label(barras, labels=rotulos, padding=5, fontsize=11, color=COR_TEXTO)
    ax.set_xlim(0, max(valores) * 1.5)  # folga para os rótulos
    ax.xaxis.set_visible(False)
    ax.spines['bottom'].set_visible(False)
    ax.tick_params(axis='y', labelcolor=COR_TEXTO_2, labelsize=12)

def _painel_hero(ax, total_entrada, total_saida, saldo, titulo):
    ax.axis('off')
    cor_saldo = COR_SALDO_POSITIVO if saldo >= 0 else STATUS_CRITICO
    ax.text(0.5, 0.98, titulo, ha='center', va='top', fontsize=16, color=COR_TEXTO, fontweight='bold')
    ax.text(0.5, 0.68, 'Saldo do mês', ha='center', va='top', fontsize=12, color=COR_MUTED)
    ax.text(0.5, 0.52, _fmt_reais(saldo), ha='center', va='top', fontsize=34,
            color=cor_saldo, fontweight='bold')
    ax.text(0.5, 0.02, f"↑ Recebido {_fmt_reais(total_entrada)}      ↓ Gasto {_fmt_reais(total_saida)}",
            ha='center', va='bottom', fontsize=13, color=COR_TEXTO_2)

def _painel_metas(ax, metas, gasto_por_categoria):
    nomes, fracoes, cores, pcts = [], [], [], []
    for meta in metas:
        gasto = gasto_por_categoria.get(meta.categoria, 0.0)
        pct = (gasto / meta.valor_limite * 100) if meta.valor_limite else 0
        nomes.append(f"{meta.categoria}\n{_fmt_reais(gasto)} / {_fmt_reais(meta.valor_limite)}")
        fracoes.append(min(pct, 100) / 100)
        cores.append(STATUS_CRITICO if pct >= 100 else STATUS_ALERTA if pct >= 80 else STATUS_BOM)
        pcts.append(pct)

    posicoes = list(range(len(metas)))
    ax.barh(posicoes, [1] * len(metas), color=COR_TRILHO, height=0.45)
    ax.barh(posicoes, fracoes, color=cores, height=0.45)
    for pos, pct, cor in zip(posicoes, pcts, cores):
        ax.text(1.04, pos, f"{pct:.0f}%", va='center', fontsize=13, color=cor, fontweight='bold')

    ax.set_yticks(posicoes)
    ax.set_yticklabels(nomes, fontsize=11)
    ax.tick_params(axis='y', labelcolor=COR_TEXTO_2)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.28)
    ax.xaxis.set_visible(False)
    for lado in ('bottom', 'left'):
        ax.spines[lado].set_visible(False)

def gerar_dashboard_completo(transacoes, metas=None, titulo='Dashboard Financeiro'):
    if not transacoes:
        return None

    metas = metas or []
    saidas = [t for t in transacoes if t.tipo == 'Saida']
    entradas = [t for t in transacoes if t.tipo == 'Entrada']
    total_saida = sum(t.valor for t in saidas)
    total_entrada = sum(t.valor for t in entradas)
    saldo = total_entrada - total_saida
    gasto_por_categoria = _somar_por(saidas, 'categoria')

    # Painéis empilhados na vertical: formato retrato lê melhor no celular
    paineis = [(1.7, lambda ax: _painel_hero(ax, total_entrada, total_saida, saldo, titulo))]
    if metas:
        paineis.append((0.8 + 0.8 * len(metas), lambda ax: (
            _preparar_eixo(ax, 'Metas do mês'),
            _painel_metas(ax, metas, gasto_por_categoria))))
    if saidas:
        met_pagamento = _somar_por(saidas, 'metodo_pagamento')
        paineis.append((0.8 + 0.6 * len(gasto_por_categoria), lambda ax: (
            _preparar_eixo(ax, 'Gastos por categoria'),
            _barras_horizontais(ax, gasto_por_categoria, COR_SAIDA))))
        paineis.append((0.8 + 0.6 * len(met_pagamento), lambda ax: (
            _preparar_eixo(ax, 'Gastos por método de pagamento'),
            _barras_horizontais(ax, met_pagamento, COR_SAIDA, com_percentual=True))))
    if entradas:
        origem_entradas = _somar_por(entradas, 'categoria')
        paineis.append((0.8 + 0.6 * len(origem_entradas), lambda ax: (
            _preparar_eixo(ax, 'Entradas por origem'),
            _barras_horizontais(ax, origem_entradas, COR_ENTRADA))))

    alturas = [altura for altura, _ in paineis]
    fig = plt.figure(figsize=(8.5, sum(alturas) + 0.55 * len(paineis)))
    fig.patch.set_facecolor(COR_FUNDO)
    grade = fig.add_gridspec(len(paineis), 1, height_ratios=alturas, hspace=0.55,
                             left=0.22, right=0.94, top=0.97, bottom=0.03)
    for indice, (_, desenhar) in enumerate(paineis):
        desenhar(fig.add_subplot(grade[indice]))

    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=COR_FUNDO, dpi=110)
    buf.seek(0)
    plt.close()
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**Jarvis Seu Assistente Financeiro**\n\n"
        "Coisas que pode me pedir:\n"
        "👉 'Para definir metas para você mesmo digite: /meta [categoria] [valor]'\n"
        "👉 'Para exportar uma planilha digite: Exportar planilha'\n"
        "👉 'Para obter um resumo digite: Me dê um resumo'\n"
        "👉 Para registrar uma transação digite: 'Recebi 5000 de salário' ou 'Gastei 50 no bar'\n"
        "👉 Compra parcelada? Diga: 'Comprei uma TV em 10x de 200' — registro e aviso todo mês\n"
        "👉 Veja seus parcelamentos com /parcelas\n"
        "👉 Assinatura mensal? Diga: 'Assinei Netflix por 39,90' — renovo e registro todo mês\n"
        "👉 Veja suas assinaturas e gastos fixos com /assinaturas\n"
        "👉 Envie uma Foto de um comprovante \n"
        "👉 Envie um Áudio falando seu gasto "
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown')

async def comando_definir_meta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if len(context.args) < 2:
            await update.message.reply_text("Uso correto: /meta [Categoria] [Valor]")
            return

        categoria = context.args[0].capitalize()
        valor = float(context.args[1].replace(',', '.'))

        if definir_meta_db(categoria, valor, user_id):
            await update.message.reply_text(f"🎯 Meta de {categoria} definida para R$ {valor:.2f}!")
        else:
            await update.message.reply_text("Erro ao salvar meta.")
            
    except ValueError:
        await update.message.reply_text("O valor deve ser um número (ex: 500 ou 500.50).")

async def comando_parcelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loop = asyncio.get_running_loop()
    parcelamentos = await loop.run_in_executor(None, listar_parcelamentos, user_id)

    if not parcelamentos:
        await update.message.reply_text(
            "Nenhum parcelamento ativo.\nPara criar, diga algo como: 'Comprei uma TV em 10x de 200 no crédito'."
        )
        return

    linhas = ["📆 *Parcelamentos ativos:*\n"]
    for p in parcelamentos:
        restam = p.parcelas_total - p.parcelas_pagas
        linhas.append(
            f"`#{p.id}` {p.descricao} — {p.parcelas_pagas}/{p.parcelas_total} pagas, "
            f"restam {restam}x de R$ {p.valor_parcela:.2f} (próxima: {p.proxima_data.strftime('%d/%m/%Y')})"
        )
    linhas.append("\nPara cancelar um: /cancelarparcela [número]")
    await update.message.reply_text("\n".join(linhas), parse_mode='Markdown')

async def comando_cancelar_parcela(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        parc_id = int(context.args[0].lstrip('#'))
    except (IndexError, ValueError):
        await update.message.reply_text("Uso correto: /cancelarparcela [número]. Veja os números com /parcelas.")
        return

    loop = asyncio.get_running_loop()
    descricao = await loop.run_in_executor(None, cancelar_parcelamento, parc_id, user_id)
    if descricao:
        await update.message.reply_text(f"🗑️ Parcelamento de '{descricao}' cancelado. As parcelas já registradas foram mantidas.")
    else:
        await update.message.reply_text("Parcelamento não encontrado. Veja os números com /parcelas.")

async def comando_assinaturas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loop = asyncio.get_running_loop()
    texto = await loop.run_in_executor(None, _texto_assinaturas, update.effective_user.id)
    await update.message.reply_text(texto, parse_mode='Markdown')

async def comando_cancelar_assinatura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        ass_id = int(context.args[0].lstrip('#'))
    except (IndexError, ValueError):
        await update.message.reply_text("Uso correto: /cancelarassinatura [número]. Veja os números com /assinaturas.")
        return

    loop = asyncio.get_running_loop()
    descricao = await loop.run_in_executor(None, cancelar_assinatura, ass_id, user_id)
    if descricao:
        await update.message.reply_text(f"🗑️ Assinatura de '{descricao}' cancelada. As cobranças já registradas foram mantidas.")
    else:
        await update.message.reply_text("Assinatura não encontrada. Veja os números com /assinaturas.")

async def processar_entrada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    texto = None
    arquivo_bytes = None
    mime_type = None

    if update.message.text:
        texto = update.message.text
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    elif update.message.photo:
        await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        foto = await update.message.photo[-1].get_file()
        byte_array = await foto.download_as_bytearray()
        arquivo_bytes = bytes(byte_array)
        mime_type = "image/jpeg"
        texto = update.message.caption or "Analise este comprovante"

    elif update.message.voice:
        await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
        voz = await update.message.voice.get_file()
        byte_array = await voz.download_as_bytearray()
        arquivo_bytes = bytes(byte_array)
        mime_type = "audio/ogg"
        texto = "Transcreva o áudio e analise o gasto"

    loop = asyncio.get_running_loop()
    contexto = await loop.run_in_executor(None, contexto_financeiro, user_id)
    resultado = await loop.run_in_executor(None, brain.interpretar_mensagem, texto, arquivo_bytes, mime_type, contexto)

    if not resultado:
        await context.bot.send_message(chat_id=chat_id, text="Não entendi. Tente ser mais claro.")
        return

    intencao = resultado.get("intencao")

    if intencao == "erro_api":
        await context.bot.send_message(
            chat_id=chat_id, 
            text="Desculpe, o serviço de inteligência artificial está temporariamente indisponível. Tente novamente em alguns minutos."
        )
        return

    if intencao == "conversa":
        await context.bot.send_message(
            chat_id=chat_id,
            text=resultado.get("resposta") or "Oi! 👋 Como posso ajudar?"
        )
        return

    if intencao == "transacao":
        dados = resultado["dados"]
        if not dados.get('valor'):
            await context.bot.send_message(
                chat_id=chat_id,
                text="Não identifiquei o valor. Pode repetir com o número? Ex: 'Gastei 50 no mercado'."
            )
            return
        dados['categoria'] = dados['categoria'].capitalize()

        try:
            parcelas = int(dados.pop('parcelas', 1) or 1)
        except (TypeError, ValueError):
            parcelas = 1
        eh_assinatura = bool(dados.pop('assinatura', False))

        info_parcelas = ""
        if eh_assinatura and parcelas == 1:
            if await loop.run_in_executor(None, criar_assinatura, dados, user_id):
                info_parcelas = (
                    "\n🔄 *Assinatura salva!* Registrei a cobrança deste mês e vou "
                    "renovar e registrar automaticamente todo mês. Veja com /assinaturas"
                )
        elif parcelas > 1:
            if await loop.run_in_executor(None, criar_parcelamento, dados, parcelas, user_id):
                info_parcelas = (
                    f"\n📆 *Parcelado:* 1/{parcelas} registrada agora. "
                    f"As próximas serão registradas e avisadas todo mês. Veja com /parcelas"
                )
            dados['descricao'] = f"{dados['descricao']} (parcela 1/{parcelas})"

        sucesso = await loop.run_in_executor(None, salvar_transacao, dados, user_id)

        if sucesso:
            alerta = ""
            if dados['tipo'] == 'Saida':
                alerta = await loop.run_in_executor(None, progresso_meta, dados['categoria'], user_id)

            if dados['tipo'] == 'Entrada':
                cabecalho = "💰 *Entrada registrada!*"
            else:
                cabecalho = "🧾 *Gasto anotado!*"

            msg = (
                f"{cabecalho}\n\n"
                f"📝 {dados['descricao']}\n"
                f"💵 *{_fmt_reais(dados['valor'])}*\n"
                f"📂 {dados['categoria']}  ·  💳 {dados['metodo_pagamento']}"
                f"{info_parcelas}"
                f"{alerta}"
            )
        else:
            msg = "Erro ao salvar no banco."
        
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

    elif intencao == "assinaturas":
        texto_ass = await loop.run_in_executor(None, _texto_assinaturas, user_id)
        await context.bot.send_message(chat_id=chat_id, text=texto_ass, parse_mode='Markdown')

    elif intencao == "resumo":
        meses_pt = {
            1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
            5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
            9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
        }
        hoje = datetime.now()
        mes_nome = f"{meses_pt[hoje.month]}/{hoje.year}"

        await context.bot.send_message(chat_id=chat_id, text=f"📊 Gerando Dashboard de {mes_nome}...")
        
        transacoes = await loop.run_in_executor(None, buscar_transacoes_mes_atual, user_id)
        
        if not transacoes:
            await context.bot.send_message(chat_id=chat_id, text=f"Nenhum dado encontrado neste mês ({mes_nome}).")
            return
        
        metas = await loop.run_in_executor(None, buscar_metas, user_id)
        imagem = await loop.run_in_executor(None, gerar_dashboard_completo, transacoes, metas, mes_nome)

        await context.bot.send_photo(chat_id=chat_id, photo=imagem)

    elif intencao == "exportacao":
        await context.bot.send_message(chat_id=chat_id, text="📂 Gerando planilha...")
        
        arquivo_excel = await loop.run_in_executor(None, gerar_arquivo_excel, user_id)
        
        if arquivo_excel:
            await context.bot.send_document(
                chat_id=chat_id,
                document=arquivo_excel,
                filename="financas_pessoais.xlsx",
                caption="Seu extrato completo! 📊"
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text="Erro ao exportar arquivo.")

    elif intencao == "ajuda":
        msg_ajuda = (
            "**Minhas Funcionalidades:**\n\n"
            "1️⃣ **Registrar Transações:**\n"
            "   📝 Texto: 'Gastei 50 no mercado'\n"
            "   📸 Foto: Envie comprovante\n"
            "   🎙️ Áudio: Fale seu gasto\n\n"
            "2️⃣ **Compras Parceladas:**\n"
            "   📆 'Comprei uma TV em 10x de 200' — cada parcela é registrada e avisada todo mês\n"
            "   Veja com /parcelas | Cancele com /cancelarparcela [número]\n\n"
            "3️⃣ **Assinaturas (gastos fixos):**\n"
            "   🔄 'Assinei Netflix por 39,90' — registro e renovo todo mês\n"
            "   Veja com /assinaturas | Cancele com /cancelarassinatura [número]\n\n"
            "4️⃣ **Gestão de Metas:**\n"
            "   🎯 Use /meta [Categoria] [Valor]\n"
            "   Ex: /meta Alimentacao 500\n\n"
            "5️⃣ **Análises:**\n"
            "   📊 Peça: 'Me dê um resumo' para ver gráficos e saldo\n\n"
            "6️⃣ **Exportação:**\n"
            "   📂 Peça: 'Exportar planilha' para receber o Excel"
        )
        await context.bot.send_message(chat_id=chat_id, text=msg_ajuda, parse_mode='Markdown')

if __name__ == '__main__':
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).post_init(iniciar_tarefas).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('meta', comando_definir_meta))
    app.add_handler(CommandHandler('parcelas', comando_parcelas))
    app.add_handler(CommandHandler('cancelarparcela', comando_cancelar_parcela))
    app.add_handler(CommandHandler('assinaturas', comando_assinaturas))
    app.add_handler(CommandHandler('cancelarassinatura', comando_cancelar_assinatura))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), processar_entrada))
    app.add_handler(MessageHandler(filters.PHOTO, processar_entrada))
    app.add_handler(MessageHandler(filters.VOICE, processar_entrada))
    
    print("Jarvis seu assistente financeiro está online no telegram!")
    manter_vivo()
    app.run_polling()