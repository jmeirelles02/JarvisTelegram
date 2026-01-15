import os
import logging
import asyncio
import io
import matplotlib.pyplot as plt
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

def salvar_transacao(dados_ia):
    session = database.SessionLocal()
    try:
        nova = models.Transacao(**dados_ia)
        session.add(nova)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao salvar: {e}")
        return False
    finally:
        session.close()

def definir_meta_db(categoria, valor):
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria).first()
        if meta:
            meta.valor_limite = valor
        else:
            meta = models.Meta(categoria=categoria, valor_limite=valor)
            session.add(meta)
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao definir meta: {e}")
        return False
    finally:
        session.close()

def verificar_alertas(categoria):
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria).first()
        if not meta:
            return ""

        mes_atual = datetime.now().month
        ano_atual = datetime.now().year
        
        total_gasto = session.query(func.sum(models.Transacao.valor)).filter(
            models.Transacao.categoria == categoria,
            models.Transacao.tipo == 'Saida',
            extract('month', models.Transacao.data) == mes_atual,
            extract('year', models.Transacao.data) == ano_atual
        ).scalar() or 0.0

        porcentagem = (total_gasto / meta.valor_limite) * 100
        
        if porcentagem >= 100:
            return f"\n\n**ALERTA:** Você estourou sua meta de {categoria}! ({porcentagem:.1f}%)"
        elif porcentagem >= 80:
            return f"\n\n**Atenção:** Você já usou {porcentagem:.1f}% da meta de {categoria}."
        
        return ""
    except Exception as e:
        print(f"Erro ao verificar alertas: {e}")
        return ""
    finally:
        session.close()

def buscar_dados_relatorio():
    session = database.SessionLocal()
    try:
        transacoes = session.query(models.Transacao).filter_by(tipo="Saida").all()
        return transacoes
    finally:
        session.close()

def gerar_analise_visual(transacoes):
    if not transacoes:
        return None
    categorias = [t.categoria for t in transacoes]
    metodos = [t.metodo_pagamento for t in transacoes]
    contagem_cat = Counter(categorias)
    contagem_met = Counter(metodos)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
    fig.suptitle('Análise de Gastos do Jarvis', fontsize=16)

    ax1.bar(contagem_cat.keys(), contagem_cat.values(), color='#4CAF50')
    ax1.set_title('Onde você mais gasta (Qtd)')
    ax1.tick_params(axis='x', rotation=45)
    
    ax2.pie(contagem_met.values(), labels=contagem_met.keys(), autopct='%1.1f%%', startangle=90)
    ax2.set_title('Como você paga')

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**Jarvis**\n\n"
        "**Novidade:** Defina metas de gastos!\n"
        "Use: `/meta [categoria] [valor]`\n"
        "Ex: `/meta Alimentacao 500`\n\n"
        "Depois é só lançar os gastos normalmente."
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown')

async def comando_definir_meta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 2:
            await update.message.reply_text("Uso correto: /meta [Categoria] [Valor]")
            return

        categoria = context.args[0].capitalize()
        valor = float(context.args[1].replace(',', '.'))

        if definir_meta_db(categoria, valor):
            await update.message.reply_text(f"🎯 Meta de {categoria} definida para R$ {valor:.2f}**!")
        else:
            await update.message.reply_text("Erro ao salvar meta.")
            
    except ValueError:
        await update.message.reply_text("O valor deve ser um número (ex: 500 ou 500.50).")

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    texto = update.message.text
    
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    loop = asyncio.get_running_loop()
    resultado = await loop.run_in_executor(None, brain.interpretar_mensagem, texto)

    if not resultado:
        await context.bot.send_message(chat_id=chat_id, text="Não entendi. Tente ser mais claro.")
        return

    intencao = resultado.get("intencao")

    if intencao == "transacao":
        dados = resultado["dados"]
        dados['categoria'] = dados['categoria'].capitalize()
        
        sucesso = await loop.run_in_executor(None, salvar_transacao, dados)
        
        if sucesso:
            alerta = await loop.run_in_executor(None, verificar_alertas, dados['categoria'])
            
            msg = (
                f"✅ *Anotado!*\n\n"
                f"📝 *Item:* {dados['descricao']}\n"
                f"💰 *Valor:* R$ {dados['valor']:.2f}\n"
                f"📂 *Categoria:* {dados['categoria']}\n"
                f"💳 *Método:* {dados['metodo_pagamento']}"
                f"{alerta}"
            )
        else:
            msg = "Erro ao salvar no banco."
        
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

    elif intencao == "resumo":
        await context.bot.send_message(chat_id=chat_id, text="📊 Compilando dados...")
        transacoes = await loop.run_in_executor(None, buscar_dados_relatorio)
        if not transacoes:
            await context.bot.send_message(chat_id=chat_id, text="Sem dados ainda!")
            return
        
        total = sum(t.valor for t in transacoes)
        imagem = await loop.run_in_executor(None, gerar_analise_visual, transacoes)
        
        await context.bot.send_message(chat_id=chat_id, text=f"📉 **Total Gasto:** R$ {total:.2f}", parse_mode='Markdown')
        await context.bot.send_photo(chat_id=chat_id, photo=imagem)

if __name__ == '__main__':
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('meta', comando_definir_meta))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), processar_mensagem))
    
    print("Jarvis seu assistente financeiro está online no telegram!")
    app.run_polling()