import os
import logging
import asyncio
import io
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime
from collections import Counter
from dotenv import load_dotenv
from sqlalchemy import func, extract
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

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

def verificar_alertas(categoria, user_id):
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria, user_id=user_id).first()
        if not meta:
            return ""

        mes_atual = datetime.now().month
        ano_atual = datetime.now().year
        
        total_gasto = session.query(func.sum(models.Transacao.valor)).filter(
            models.Transacao.user_id == user_id,
            models.Transacao.categoria == categoria,
            models.Transacao.tipo == 'Saida',
            extract('month', models.Transacao.data) == mes_atual,
            extract('year', models.Transacao.data) == ano_atual
        ).scalar() or 0.0

        porcentagem = (total_gasto / meta.valor_limite) * 100
        
        if porcentagem >= 100:
            return f"\n\n🚨 **ALERTA VERMELHO:** Você estourou sua meta de {categoria}! ({porcentagem:.1f}%)"
        elif porcentagem >= 80:
            return f"\n\n⚠️ **Atenção:** Você já usou {porcentagem:.1f}% da meta de {categoria}."
        
        return ""
    except Exception as e:
        print(f"Erro ao verificar alertas: {e}")
        return ""
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

def gerar_dashboard_completo(transacoes):
    if not transacoes:
        return None

    saidas = [t for t in transacoes if t.tipo == 'Saida']
    entradas = [t for t in transacoes if t.tipo == 'Entrada']

    cat_saida = Counter([t.categoria for t in saidas])
    met_saida = Counter([t.metodo_pagamento for t in saidas])
    cat_entrada = Counter([t.categoria for t in entradas])

    total_saida = sum(t.valor for t in saidas)
    total_entrada = sum(t.valor for t in entradas)

    fig, axs = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Dashboard Financeiro do Jarvis', fontsize=20)

    if saidas:
        axs[0, 0].bar(cat_saida.keys(), cat_saida.values(), color='#ff6666')
        axs[0, 0].set_title('Gastos por Categoria')
        axs[0, 0].tick_params(axis='x', rotation=45)
    else:
        axs[0, 0].text(0.5, 0.5, 'Sem dados de Saída', ha='center')

    if saidas:
        axs[0, 1].pie(met_saida.values(), labels=met_saida.keys(), autopct='%1.1f%%', startangle=90)
        axs[0, 1].set_title('Métodos de Pagamento (Gastos)')
    else:
        axs[0, 1].text(0.5, 0.5, 'Sem dados de Saída', ha='center')

    if entradas:
        axs[1, 0].bar(cat_entrada.keys(), cat_entrada.values(), color='#66b3ff')
        axs[1, 0].set_title('Origem das Entradas')
        axs[1, 0].tick_params(axis='x', rotation=45)
    else:
        axs[1, 0].text(0.5, 0.5, 'Sem dados de Entrada', ha='center')

    axs[1, 1].bar(['Entradas', 'Saídas'], [total_entrada, total_saida], color=['green', 'red'])
    axs[1, 1].set_title('Balanço Geral')

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
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
    resultado = await loop.run_in_executor(None, brain.interpretar_mensagem, texto, arquivo_bytes, mime_type)

    if not resultado:
        await context.bot.send_message(chat_id=chat_id, text="Não entendi. Tente ser mais claro.")
        return

    intencao = resultado.get("intencao")

    if intencao == "transacao":
        dados = resultado["dados"]
        dados['categoria'] = dados['categoria'].capitalize()
        
        sucesso = await loop.run_in_executor(None, salvar_transacao, dados, user_id)
        
        if sucesso:
            alerta = ""
            if dados['tipo'] == 'Saida':
                alerta = await loop.run_in_executor(None, verificar_alertas, dados['categoria'], user_id)
            
            msg = (
                f"✅ *Anotado!*\n\n"
                f"📝 *Item:* {dados['descricao']}\n"
                f"💰 *Valor:* R$ {dados['valor']:.2f}\n"
                f"📂 *Categoria:* {dados['categoria']}\n"
                f"💳 *Método:* {dados['metodo_pagamento']}\n"
                f"🔄 *Tipo:* {dados['tipo']}"
                f"{alerta}"
            )
        else:
            msg = "Erro ao salvar no banco."
        
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

elif intencao == "resumo":
        mes_nome = datetime.now().strftime("%B/%Y")
        await context.bot.send_message(chat_id=chat_id, text=f"📊 Gerando Dashboard de {mes_nome}...")
        
        transacoes = await loop.run_in_executor(None, buscar_transacoes_mes_atual, user_id)
        
        if not transacoes:
            await context.bot.send_message(chat_id=chat_id, text=f"Nenhum dado encontrado neste mês ({mes_nome}).")
            return
        
        total_entrada = sum(t.valor for t in transacoes if t.tipo == 'Entrada')
        total_saida = sum(t.valor for t in transacoes if t.tipo == 'Saida')
        saldo = total_entrada - total_saida
        
        imagem = await loop.run_in_executor(None, gerar_dashboard_completo, transacoes)
        
        msg_resumo = (
            f"📉 **Resumo Mensal ({mes_nome})**\n\n"
            f"💸 **Recebido:** R$ {total_entrada:.2f}\n"
            f"💳 **Gasto:** R$ {total_saida:.2f}\n"
            f"💰 **Saldo do Mês:** R$ {saldo:.2f}\n"
        )

        await context.bot.send_message(chat_id=chat_id, text=msg_resumo, parse_mode='Markdown')
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
            "2️⃣ **Gestão de Metas:**\n"
            "   🎯 Use `/meta [Categoria] [Valor]`\n"
            "   Ex: `/meta Alimentacao 500`\n\n"
            "3️⃣ **Análises:**\n"
            "   📊 Peça: 'Me dê um resumo' para ver gráficos e saldo\n\n"
            "4️⃣ **Exportação:**\n"
            "   📂 Peça: 'Exportar planilha' para receber o Excel"
        )
        await context.bot.send_message(chat_id=chat_id, text=msg_ajuda, parse_mode='Markdown')

if __name__ == '__main__':
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('meta', comando_definir_meta))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), processar_entrada))
    app.add_handler(MessageHandler(filters.PHOTO, processar_entrada))
    app.add_handler(MessageHandler(filters.VOICE, processar_entrada))
    
    print("Jarvis seu assistente financeiro está online no telegram!")
    app.run_polling()