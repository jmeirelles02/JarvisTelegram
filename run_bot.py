import os
import io
import time
import logging
import threading
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from flask import Flask, request
from app.services import brain, telegram, transport as whatsapp, presentation as visual
from app import models, database
from app.services.personal import AssistentePessoal
from app.services.conversation import Conversa
from app.services.assistant import AssistenteContextual
from app.services.proactivity import IniciativaPessoal
from app.services.command_menu import menu_comandos
from app.services.reports import gerar_dashboard as gerar_dashboard_completo, texto_resumo, somar_por as _somar_por
from app.services.presentation import moeda as _fmt_reais
from app.services.personal_time import agora_utc, utc, validar_fuso

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()

models.Base.metadata.create_all(bind=database.engine)

conversa = Conversa(lambda: database.SessionLocal())


def enviar_texto(user_id, texto):
    resultado = whatsapp.enviar_texto(user_id, texto)
    conversa.registrar(user_id, "assistant", texto)
    return resultado


assistente_pessoal = AssistentePessoal(database.SessionLocal, enviar_texto)
contexto_pessoal = AssistenteContextual(lambda: database.SessionLocal(), enviar_texto)
iniciativa = IniciativaPessoal(lambda: database.SessionLocal(), contexto_pessoal, conversa, enviar_texto)

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

def _proxima_data_fatura(dia):
    """Próxima ocorrência do dia de fechamento da fatura (clampa em meses curtos)."""
    hoje = datetime.now()
    data = hoje + relativedelta(day=dia)
    if data <= hoje:
        data = hoje + relativedelta(months=1, day=dia)
    return data

def definir_dia_fatura(dia, user_id):
    session = database.SessionLocal()
    try:
        config = session.query(models.ConfigFatura).filter_by(user_id=user_id).first()
        if config:
            config.dia_fechamento = dia
        else:
            session.add(models.ConfigFatura(user_id=user_id, dia_fechamento=dia))
        session.commit()
        return True
    except Exception as e:
        print(f"Erro ao definir dia da fatura: {e}")
        return False
    finally:
        session.close()

def criar_parcelamento(dados, parcelas, user_id):
    """Agenda o parcelamento sem registrar gasto agora: a 1ª parcela só conta
    quando a fatura fecha. Devolve (data_primeira_parcela, tem_dia_fatura) ou None."""
    session = database.SessionLocal()
    try:
        config = session.query(models.ConfigFatura).filter_by(user_id=user_id).first()
        if config:
            primeira = _proxima_data_fatura(config.dia_fechamento)
        else:
            # ponytail: sem /fatura definido, aproxima a 1ª parcela em +1 mês
            primeira = datetime.now() + relativedelta(months=1)
        parc = models.Parcelamento(
            user_id=user_id,
            descricao=dados['descricao'],
            valor_parcela=dados['valor'],
            categoria=dados['categoria'],
            metodo_pagamento=dados['metodo_pagamento'],
            parcelas_total=parcelas,
            parcelas_pagas=0,
            proxima_data=primeira,
        )
        session.add(parc)
        session.commit()
        return primeira, config is not None
    except Exception as e:
        print(f"Erro ao criar parcelamento: {e}")
        return None
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
    return visual.lista_assinaturas(listar_assinaturas(user_id))

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
            avisos.append((ass.user_id, visual.mensagem("Assinatura renovada",
                visual.bloco(_fmt_reais(ass.valor), visual.nome(ass.descricao)),
                f"Cobrança registrada neste mês.\nPróxima renovação: {ass.proxima_data:%d/%m/%Y}",
                rodape="Ver assinaturas: /assinaturas")))
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
            msg = visual.mensagem("Parcela registrada",
                visual.bloco(_fmt_reais(parc.valor_parcela), visual.nome(parc.descricao),
                             f"Parcela {parc.parcelas_pagas} de {parc.parcelas_total}"))
            if parc.parcelas_pagas >= parc.parcelas_total:
                msg += "\n\n✓ Parcelamento concluído. Esta foi a última parcela."
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

def vigiar_recorrencias():
    # ponytail: checagem a cada 6h basta para recorrências mensais e faz catch-up após restart
    while True:
        avisos = processar_parcelas_devidas() + processar_assinaturas_devidas()
        for user_id, msg in avisos:
            try:
                enviar_texto(user_id, msg)
            except Exception as e:
                print(f"Erro ao enviar aviso de recorrência: {e}")
        time.sleep(6 * 3600)


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

def progresso_meta(categoria, user_id):
    """Linha visual do progresso da meta da categoria; '' se não houver meta."""
    session = database.SessionLocal()
    try:
        meta = session.query(models.Meta).filter_by(categoria=categoria, user_id=user_id).first()
        if not meta or not meta.valor_limite:
            return ""

        total_gasto = sum(item.valor for item in buscar_transacoes_mes_atual(user_id)
                          if item.tipo == 'Saida' and item.categoria == categoria)
        return visual.aviso_meta(categoria, total_gasto, meta.valor_limite)
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
        zona = validar_fuso(assistente_pessoal.fuso(user_id))
        inicio = agora_utc().astimezone(zona).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        fim = inicio + relativedelta(months=1)
        transacoes = session.query(models.Transacao).filter(
            models.Transacao.user_id == user_id,
            models.Transacao.data >= utc(inicio),
            models.Transacao.data < utc(fim)
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
            f"resultado dos registros {_fmt_reais(entradas - saidas)}. Gastos por categoria: {cats or 'nenhum'}."
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

# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

TEXTO_START = visual.mensagem("Jarvis · Seu assistente pessoal",
    "Finanças, lembretes e agenda em uma conversa.",
    "Conte sobre sua rotina e seus objetivos. Vou usar esse contexto para ajudar e avisar quando perceber algo relevante.",
    visual.bloco("Registre sem formulários", "Gastei 50 no mercado.\nTambém aceito áudio e foto de comprovante."),
    visual.bloco("Organize seu dia", "Me lembra de pagar a internet amanhã às 9h.\nO que tenho para fazer sexta?"),
    visual.bloco("Sugestões espontâneas", "Até duas por dia, entre 08h e 21h no seu fuso. Você pode ajustar conversando comigo ou desligar em /proatividade off."),
    visual.bloco("Comece por aqui", "/resumo · Finanças do mês\n/hoje · Agenda e pendências\n/memorias · O que lembro sobre você\n/menu · Todos os comandos"))

TEXTO_AJUDA = visual.mensagem("Como usar o Jarvis",
    visual.bloco("Converse do seu jeito", "Conte sua rotina, preferências e objetivos. Posso relacionar isso à sua agenda e aos seus registros para ajudar a planejar o próximo passo."),
    visual.bloco("Finanças", "Gastei 50 no mercado · Recebi 5000 de salário\n"
                 "Comprei uma TV em 10x de 200 · Assinei Netflix por 39,90\n"
                 "/resumo · /exportar · /assinaturas · /parcelas\n/meta Alimentacao 500 · /fatura 5"),
    visual.bloco("Lembretes", "Me lembra de pagar o aluguel todo dia 5 às 9h\n"
                 "/lembretes · /adiar ID MINUTOS\n/concluir ID · /cancelarlembrete ID\n"
                 "Concluir encerra também as próximas ocorrências."),
    visual.bloco("Agenda", "O que tenho para fazer sexta?\nMarca dentista amanhã das 15h às 16h\n"
                 "/agenda · /calendarios · /confirmar ID · /descartar ID\n"
                 "Conecte iCloud ou Google. Aviso padrão: 30 min antes."),
    visual.bloco("Seu dia", "/hoje · Resumo agora\n/resumodiario 08:00 · Ativar envio diário\n"
                 "/resumodiario off · Desativar\n/fuso America/Sao_Paulo · Ajustar horário"),
    visual.bloco("Memória e iniciativa", "/memorias · Ver o que lembro\n/esquecer ID · Apagar uma informação\n"
                 "/proatividade on ou off · Ligar ou desligar sugestões\n"
                 "Para ajustar: Só me mande sugestões entre 10h e 19h, uma por dia."),
    rodape="Todos os comandos: /menu · Seu identificador: /meuid")


def comando_start(user_id, args):
    enviar_texto(user_id, TEXTO_START)


def comando_definir_meta(user_id, args):
    try:
        if len(args) < 2:
            enviar_texto(user_id, visual.mensagem("Definir uma meta", "Uso: /meta CATEGORIA VALOR", "Exemplo: /meta Alimentacao 500"))
            return

        categoria = args[0].capitalize()
        valor = float(args[1].replace(',', '.'))

        if definir_meta_db(categoria, valor, user_id):
            enviar_texto(user_id, visual.mensagem("✓ Meta definida", visual.bloco(visual.nome(categoria), f"Limite mensal: {_fmt_reais(valor)}"), rodape="Acompanhe em /resumo."))
        else:
            enviar_texto(user_id, visual.erro("A meta não foi salva. Tente novamente."))

    except ValueError:
        enviar_texto(user_id, visual.erro("Informe um valor numérico.", "Exemplo: /meta Alimentacao 500,50"))


def comando_parcelas(user_id, args):
    enviar_texto(user_id, visual.lista_parcelas(listar_parcelamentos(user_id)))


def comando_cancelar_parcela(user_id, args):
    try:
        parc_id = int(args[0].lstrip('#'))
    except (IndexError, ValueError):
        enviar_texto(user_id, visual.mensagem("Cancelar parcelamento", "Uso: /cancelarparcela ID", rodape="Encontre o ID em /parcelas."))
        return

    descricao = cancelar_parcelamento(parc_id, user_id)
    if descricao:
        enviar_texto(user_id, visual.mensagem("Parcelamento cancelado", visual.nome(descricao), "As parcelas já registradas foram mantidas."))
    else:
        enviar_texto(user_id, visual.erro("Parcelamento não encontrado.", "Confira o ID em /parcelas."))


def comando_fatura(user_id, args):
    try:
        dia = int(args[0])
    except (IndexError, ValueError):
        dia = 0
    if not 1 <= dia <= 31:
        enviar_texto(user_id, visual.mensagem("Fechamento do cartão", "Uso: /fatura DIA", "Exemplo: /fatura 5 · Dias aceitos: 1 a 31."))
        return

    if definir_dia_fatura(dia, user_id):
        enviar_texto(
            user_id,
            visual.mensagem("✓ Fechamento atualizado", f"Todo dia *{dia}*.", "Novos parcelamentos começam no próximo fechamento.")
        )
    else:
        enviar_texto(user_id, visual.erro("O dia de fechamento não foi salvo. Tente novamente."))


def comando_assinaturas(user_id, args):
    enviar_texto(user_id, _texto_assinaturas(user_id))


def comando_cancelar_assinatura(user_id, args):
    try:
        ass_id = int(args[0].lstrip('#'))
    except (IndexError, ValueError):
        enviar_texto(user_id, visual.mensagem("Cancelar assinatura", "Uso: /cancelarassinatura ID", rodape="Encontre o ID em /assinaturas."))
        return

    descricao = cancelar_assinatura(ass_id, user_id)
    if descricao:
        enviar_texto(user_id, visual.mensagem("Assinatura cancelada", visual.nome(descricao), "As cobranças já registradas foram mantidas."))
    else:
        enviar_texto(user_id, visual.erro("Assinatura não encontrada.", "Confira o ID em /assinaturas."))


def comando_menu(user_id, args):
    enviar_texto(user_id, menu_comandos())


def comando_ajuda(user_id, args):
    enviar_texto(user_id, TEXTO_AJUDA)


def comando_resumo(user_id, args):
    processar_entrada(user_id, "resumo", intencao_direta="resumo")


def comando_exportar(user_id, args):
    processar_entrada(user_id, "exportar", intencao_direta="exportacao")


def comando_meuid(user_id, args):
    enviar_texto(user_id, visual.mensagem("Seu identificador", str(user_id), rodape="Use este número em CALENDAR_USER_ID para conectar sua agenda."))


def comando_assistente(user_id, nome, args):
    contexto_pessoal.comando(user_id, nome, args)


COMANDOS = {
    'menu': comando_menu,
    'comandos': comando_menu,
    'ajuda': comando_ajuda,
    'resumo': comando_resumo,
    'exportar': comando_exportar,
    'meuid': comando_meuid,
    'start': comando_start,
    'meta': comando_definir_meta,
    'parcelas': comando_parcelas,
    'cancelarparcela': comando_cancelar_parcela,
    'fatura': comando_fatura,
    'assinaturas': comando_assinaturas,
    'cancelarassinatura': comando_cancelar_assinatura,
    'memorias': lambda user_id, args: comando_assistente(user_id, "memorias", args),
    'esquecer': lambda user_id, args: comando_assistente(user_id, "esquecer", args),
    'proatividade': lambda user_id, args: comando_assistente(user_id, "proatividade", args),
}


# ---------------------------------------------------------------------------
# Fluxo principal de mensagem
# ---------------------------------------------------------------------------

def processar_entrada(user_id, texto, arquivo_bytes=None, mime_type=None, *, intencao_direta=None):
    with conversa.lock(user_id):
        contexto_pessoal.conhecer_usuario(user_id)
        historico = conversa.historico(user_id)
        entrada = texto or ""
        if arquivo_bytes:
            entrada = f"[Mídia recebida: {mime_type}] {entrada}"
        conversa.registrar(user_id, "user", entrada)
        return _processar_entrada(user_id, texto, arquivo_bytes, mime_type, historico, intencao_direta)


def _processar_entrada(user_id, texto, arquivo_bytes, mime_type, historico, intencao_direta):
    if texto and not arquivo_bytes:
        consulta = assistente_pessoal.consulta_rapida(user_id, texto)
        if consulta and assistente_pessoal.executar_intencao(user_id, consulta):
            return
    if intencao_direta:
        resultado = {"intencao": intencao_direta}
    else:
        contexto = contexto_financeiro(user_id)
        resultado = brain.interpretar_mensagem(texto, arquivo_bytes, mime_type, contexto,
                                             contexto_temporal=assistente_pessoal.contexto(user_id),
                                             historico=historico, contexto_pessoal=contexto_pessoal.contexto(user_id))

    if not resultado:
        enviar_texto(user_id, visual.mensagem("Pode completar o pedido?", "Diga o que deseja registrar ou consultar.", rodape="Veja exemplos em /ajuda."))
        return

    if contexto_pessoal.executar_intencao(user_id, resultado, texto):
        return

    contexto_pessoal.assimilar(user_id, resultado, texto)
    if assistente_pessoal.executar_intencao(user_id, resultado):
        return

    intencao = resultado.get("intencao")

    if intencao == "erro_api":
        enviar_texto(
            user_id,
            visual.erro("O serviço de interpretação está indisponível agora.", "Tente novamente em alguns minutos. Comandos como /agenda e /hoje continuam disponíveis.")
        )
        return

    if intencao == "conversa":
        enviar_texto(user_id, resultado.get("resposta") or "Oi! 👋 Como posso ajudar?")
        return

    if intencao == "transacao":
        dados = resultado["dados"]
        if not dados.get('valor'):
            enviar_texto(
                user_id,
                "Qual foi o valor da compra?"
            )
            return
        dados['categoria'] = dados['categoria'].capitalize()

        try:
            parcelas = int(dados.pop('parcelas', 1) or 1)
        except (TypeError, ValueError):
            parcelas = 1
        eh_assinatura = bool(dados.pop('assinatura', False))

        if parcelas > 1:
            resultado_parc = criar_parcelamento(dados, parcelas, user_id)
            if not resultado_parc:
                enviar_texto(user_id, visual.erro("O parcelamento não foi salvo. Tente novamente."))
                return
            primeira, tem_fatura = resultado_parc
            dica_fatura = "" if tem_fatura else "Configure o fechamento para acertar as datas: /fatura DIA."
            enviar_texto(
                user_id,
                visual.mensagem("✓ Parcelamento agendado",
                    visual.bloco(f"{parcelas}× {_fmt_reais(dados['valor'])}", visual.nome(dados['descricao']),
                                 f"{visual.nome(dados['categoria'])} · {visual.nome(dados['metodo_pagamento'])}"),
                    f"Total: {_fmt_reais(parcelas * dados['valor'])}\nPrimeira parcela: {primeira:%d/%m/%Y}",
                    "As parcelas entram nos gastos conforme forem registradas no fechamento.", dica_fatura,
                    rodape="Acompanhar: /parcelas")
            )
            return

        assinatura_criada = False
        if eh_assinatura:
            if criar_assinatura(dados, user_id):
                assinatura_criada = True

        if salvar_transacao(dados, user_id):
            alerta = ""
            if dados['tipo'] == 'Saida':
                alerta = progresso_meta(dados['categoria'], user_id)

            msg = visual.transacao(dados, assinatura=assinatura_criada, alerta=alerta)
        else:
            msg = visual.erro("A transação não foi salva. Tente novamente.")

        enviar_texto(user_id, msg)

    elif intencao == "assinaturas":
        enviar_texto(user_id, _texto_assinaturas(user_id))

    elif intencao == "resumo":
        meses_pt = {
            1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
            5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
            9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
        }
        fuso = assistente_pessoal.fuso(user_id)
        hoje = agora_utc().astimezone(validar_fuso(fuso))
        mes_nome = f"{meses_pt[hoje.month]}/{hoje.year}"

        transacoes = buscar_transacoes_mes_atual(user_id)
        if not transacoes:
            enviar_texto(user_id, visual.mensagem(f"Resumo · {mes_nome}", "Ainda não há transações neste mês.",
                                                        rodape="Para começar: Gastei 50 no mercado."))
            return

        enviar_texto(user_id, texto_resumo(transacoes, mes_nome))
        imagem = gerar_dashboard_completo(transacoes, buscar_metas(user_id), mes_nome, fuso=fuso, agora=hoje)
        whatsapp.enviar_midia(user_id, imagem, 'image/png', 'resumo_financeiro.png', 'image',
                             "Seu mês em gráficos. Barras: participação de cada grupo. Linhas: entradas e saídas acumuladas por dia.")

    elif intencao == "exportacao":
        arquivo_excel = gerar_arquivo_excel(user_id)
        if arquivo_excel:
            whatsapp.enviar_midia(
                user_id, arquivo_excel,
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'financas_pessoais.xlsx', 'document', "Seu histórico completo de transações, pronto para filtrar e analisar no Excel."
            )
            conversa.registrar(user_id, "assistant", "Planilha de transações enviada: financas_pessoais.xlsx.")
        else:
            enviar_texto(user_id, visual.erro("Não foi possível gerar a planilha.", "Tente /exportar novamente em alguns instantes."))

    elif intencao == "ajuda":
        enviar_texto(user_id, TEXTO_AJUDA)

    else:
        enviar_texto(user_id, "Não consegui entender o que você quer fazer. Pode me explicar um pouco melhor?")


def tratar_mensagem(user_id, texto, arquivo_bytes=None, mime_type=None):
    with conversa.lock(user_id):
        return _tratar_mensagem(user_id, texto, arquivo_bytes, mime_type)


def _tratar_mensagem(user_id, texto, arquivo_bytes=None, mime_type=None):
    if texto and texto.startswith('/') and not arquivo_bytes:
        contexto_pessoal.conhecer_usuario(user_id)
        partes = texto.split()
        if partes[0][1:].lower() not in {"resumo", "exportar"}:
            conversa.registrar(user_id, "user", texto)
        if assistente_pessoal.comando(user_id, partes[0][1:].lower(), partes[1:]):
            return
        comando = COMANDOS.get(partes[0][1:].lower())
        if comando:
            comando(user_id, partes[1:])
        else:
            enviar_texto(user_id, menu_comandos(partes[0][1:].lower()))
        return
    processar_entrada(user_id, texto, arquivo_bytes, mime_type)


# ---------------------------------------------------------------------------
# Webhook da Evolution API
# ---------------------------------------------------------------------------

# Vazio = qualquer número pode falar com o bot. Preencha para restringir.
PERMITIDOS = {n.strip() for n in os.getenv("NUMEROS_PERMITIDOS", "").split(",") if n.strip()}
# Segredo no path: sem ele, qualquer um que ache o IP do VPS manda o bot disparar mensagem.
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "trocar-esse-token")


def extrair_mensagem(payload):
    """Evento messages.upsert -> (user_id, texto, key_da_midia, mime). None = ignorar."""
    dados = payload.get('data') or {}
    key = dados.get('key') or {}
    jid = key.get('remoteJid') or ''

    # fromMe evita o bot responder ao próprio eco; @s.whatsapp.net = conversa 1:1, sem grupos
    if key.get('fromMe') or not jid.endswith('@s.whatsapp.net'):
        return None

    numero = jid.split('@')[0]
    if not numero.isdigit() or (PERMITIDOS and numero not in PERMITIDOS):
        return None
    user_id = int(numero)

    msg = dados.get('message') or {}
    if 'imageMessage' in msg:
        legenda = msg['imageMessage'].get('caption') or "Analise este comprovante"
        return user_id, legenda, key, 'image/jpeg'
    if 'audioMessage' in msg:
        return user_id, "Transcreva o áudio e analise o pedido", key, 'audio/ogg'

    texto = msg.get('conversation') or (msg.get('extendedTextMessage') or {}).get('text')
    if not texto:
        return None
    return user_id, texto.strip(), None, None


def _atender(user_id, texto, key_midia, mime_type):
    """Roda fora do request: baixar mídia + chamar a IA leva segundos."""
    try:
        arquivo_bytes = whatsapp.baixar_midia(key_midia) if key_midia else None
        tratar_mensagem(user_id, texto, arquivo_bytes, mime_type)
    except Exception:
        # sem isso a exceção morre calada dentro da thread
        logging.exception("Erro ao atender %s", user_id)
        try:
            enviar_texto(user_id, visual.erro("O pedido não foi concluído.", "Tente novamente em alguns instantes."))
        except Exception:
            logging.exception("Falha ao avisar o usuário %s do erro", user_id)


servidor = Flask(__name__)


@servidor.get('/')
def home():
    return "Jarvis está online!"


@servidor.post(f'/webhook/{WEBHOOK_TOKEN}')
def webhook():
    if whatsapp.canal() != "whatsapp":
        return '', 404
    mensagem = extrair_mensagem(request.get_json(silent=True) or {})
    if mensagem:
        threading.Thread(target=_atender, args=mensagem, daemon=True).start()
    return '', 200  # 200 imediato: a Evolution reenvia se a resposta demorar


if __name__ == '__main__':
    canal = whatsapp.canal()
    if canal == "telegram":
        bot_username = telegram.preparar()
        threading.Thread(target=telegram.vigiar, args=(_atender, bot_username), daemon=True).start()
    threading.Thread(target=vigiar_recorrencias, daemon=True).start()
    threading.Thread(target=assistente_pessoal.vigiar, daemon=True).start()
    threading.Thread(target=iniciativa.vigiar, daemon=True).start()
    print(f"Jarvis online no {canal}")
    servidor.run(host='0.0.0.0', port=int(os.getenv("PORT", 8000)), threaded=True)
