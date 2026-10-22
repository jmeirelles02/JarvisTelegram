import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import IntegrityError

from app import models
from app.services import agenda, brain, calendar_service, presentation as visual
from app.services.personal_time import (
    agora_utc, formatar_data, fuso_padrao, ler_data, utc, validar_fuso, validar_horario,
)


logger = logging.getLogger(__name__)
REPETICOES = {"nenhuma", "diaria", "semanal", "mensal", "dias_uteis"}
INTENCOES = {"lembrete_criar", "lembretes", "lembrete_alterar", "agenda", "evento_criar",
             "evento_confirmar", "evento_descartar", "agenda_gerenciar", "hoje", "resumo_diario_config", "esclarecer"}
COMANDOS_PESSOAIS = {"lembretes", "concluir", "adiar", "cancelarlembrete", "resumodiario", "hoje", "agenda",
                      "calendarios", "diagnosticocalendario", "confirmar", "descartar", "fuso"}


def proxima_ocorrencia(data, repeticao, fuso, dia_original, agora):
    if repeticao == "nenhuma":
        return None
    local = utc(data).astimezone(validar_fuso(fuso))
    agora_local = utc(agora).astimezone(validar_fuso(fuso))
    if repeticao == "mensal":
        meses = max(1, (agora_local.year - local.year) * 12 + agora_local.month - local.month)
        seguinte = local + relativedelta(months=meses, day=dia_original)
        if seguinte <= agora_local:
            seguinte += relativedelta(months=1, day=dia_original)
    else:
        dias = 7 if repeticao == "semanal" else 1
        saltos = max(1, (agora_local.date() - local.date()).days // dias)
        seguinte = local + timedelta(days=dias * saltos)
        while seguinte <= agora_local or (repeticao == "dias_uteis" and seguinte.weekday() >= 5):
            seguinte += timedelta(days=dias)
    return utc(seguinte)


def enfileirar(session, chave, user_id, texto, agora, tipo="lembrete", lembrete_id=None,
               expira_em=None, estado="pendente"):
    if session.query(models.EntregaPessoal.id).filter_by(chave=chave).first():
        return
    try:
        with session.begin_nested():
            session.add(models.EntregaPessoal(
                chave=chave, user_id=user_id, texto=texto, tipo=tipo, lembrete_id=lembrete_id,
                tentar_em=agora, expira_em=expira_em, estado=estado,
            ))
            session.flush()
    except IntegrityError:
        if not session.query(models.EntregaPessoal.id).filter_by(chave=chave).first():
            raise


class AssistentePessoal:
    def __init__(self, session_factory, enviar_texto, calendario_factory=None):
        self.session_factory = session_factory
        self.enviar_texto = enviar_texto
        self.calendario_factory = calendario_factory or calendar_service.obter_calendario

    def fuso(self, user_id):
        with self.session_factory() as session:
            preferencias = session.get(models.PreferenciasPessoais, user_id)
            return preferencias.fuso if preferencias else fuso_padrao()

    def contexto(self, user_id):
        fuso = self.fuso(user_id)
        local = agora_utc().astimezone(validar_fuso(fuso))
        return f"Data/hora atual: {local.isoformat()}; dia da semana: {local.strftime('%A')}; fuso: {fuso}."

    def consulta_rapida(self, user_id, texto):
        if agenda.periodo_consulta(texto) is None:
            return None
        return agenda.interpretar_consulta(texto, self.fuso(user_id))

    def confirmacao_rapida(self, texto, historico):
        """Um sim só confirma o pedido exibido na última resposta, sem depender da IA."""
        if not historico or historico[-1].get("role") != "assistant":
            return None
        ultima = historico[-1].get("content", "")
        pedido = re.search(r"Criar: /confirmar (\d+)\b", ultima)
        if "Confirmar compromisso" not in ultima or not pedido:
            return None
        normalizado = "".join(letra for letra in unicodedata.normalize("NFD", texto.lower())
                              if unicodedata.category(letra) != "Mn")
        normalizado = " ".join(normalizado.strip().rstrip(".!?").split())
        if normalizado in {"sim", "confirmo", "pode criar", "pode marcar", "pode agendar", "sim pode criar", "sim, pode criar"}:
            return {"intencao": "evento_confirmar", "dados": {"id": int(pedido.group(1))}}
        if normalizado in {"nao", "desisti", "pode descartar", "descarta", "nao precisa", "nao, obrigado"}:
            return {"intencao": "evento_descartar", "dados": {"id": int(pedido.group(1))}}
        return None

    def criar_lembrete(self, user_id, dados, agora=None):
        agora = agora or agora_utc()
        fuso = self.fuso(user_id)
        descricao = dados.get("descricao")
        if not isinstance(descricao, str) or not descricao.strip() or len(descricao) > 500:
            raise ValueError("Informe o que devo lembrar, em até 500 caracteres.")
        data = ler_data(dados.get("data"), fuso)
        repeticao = dados.get("repeticao", "nenhuma")
        if repeticao not in REPETICOES:
            raise ValueError("Repetição aceita: diaria, semanal, mensal ou dias_uteis.")
        if data <= agora:
            raise ValueError("O horário precisa estar no futuro. Informe uma nova data e hora.")
        if repeticao == "dias_uteis" and data.astimezone(validar_fuso(fuso)).weekday() >= 5:
            raise ValueError("Para dias úteis, escolha a primeira ocorrência entre segunda e sexta.")
        with self.session_factory.begin() as session:
            lembrete = models.Lembrete(user_id=user_id, descricao=descricao.strip(), proxima_data=data,
                                       repeticao=repeticao, fuso=fuso,
                                       dia_original=data.astimezone(validar_fuso(fuso)).day)
            session.add(lembrete)
            session.flush()
            return visual.mensagem(f"✓ Lembrete criado · #{lembrete.id}",
                visual.bloco(lembrete.descricao, formatar_data(data, fuso), visual.REPETICOES[repeticao]),
                rodape="Gerenciar: /lembretes")

    def listar_lembretes(self, user_id):
        with self.session_factory() as session:
            lembretes = session.query(models.Lembrete).filter_by(user_id=user_id, ativo=True).order_by(
                models.Lembrete.proxima_data, models.Lembrete.id).all()
            itens = []
            for lembrete in lembretes:
                data = formatar_data(lembrete.proxima_data, lembrete.fuso) if lembrete.proxima_data else "aguardando conclusão"
                itens.append(visual.bloco(f"{visual.nome(lembrete.descricao)} · #{lembrete.id}",
                                         data, visual.REPETICOES[lembrete.repeticao]))
            if not lembretes:
                return visual.mensagem("Lembretes", "Nenhum lembrete ativo.",
                                       rodape="Para adicionar: Me lembra de pagar a internet amanhã às 9h.")
            return visual.mensagem("Lembretes", *itens,
                rodape="/adiar ID MINUTOS · /concluir ID · /cancelarlembrete ID\nConcluir encerra também a recorrência.")

    def alterar_lembrete(self, user_id, identificador, acao, minutos=None, agora=None):
        agora = agora or agora_utc()
        if acao not in {"concluir", "cancelar", "adiar"}:
            raise ValueError("Escolha concluir, cancelar ou adiar.")
        if acao == "adiar" and (type(minutos) is not int or not 1 <= minutos <= 525600):
            raise ValueError("Informe de 1 a 525600 minutos. Exemplo: /adiar 3 10.")
        with self.session_factory.begin() as session:
            lembrete = session.query(models.Lembrete).filter_by(
                id=identificador, user_id=user_id, ativo=True).with_for_update().first()
            if not lembrete:
                raise ValueError("Lembrete não encontrado. Veja /lembretes.")
            session.query(models.EntregaPessoal).filter_by(
                lembrete_id=identificador, estado="pendente").update({"estado": "cancelado"})
            if acao == "adiar":
                lembrete.proxima_data = agora + timedelta(minutes=minutos)
                lembrete.dia_original = lembrete.proxima_data.astimezone(validar_fuso(lembrete.fuso)).day
                return visual.mensagem(f"Lembrete adiado · #{identificador}", visual.nome(lembrete.descricao),
                                       f"Novo horário: {formatar_data(lembrete.proxima_data, lembrete.fuso)}")
            lembrete.ativo = False
            return visual.mensagem(f"✓ Lembrete {'concluído' if acao == 'concluir' else 'cancelado'} · #{identificador}",
                                   visual.nome(lembrete.descricao))

    def configurar_resumo(self, user_id, horario):
        if horario is not None:
            validar_horario(horario)
        with self.session_factory.begin() as session:
            preferencias = session.get(models.PreferenciasPessoais, user_id)
            if not preferencias:
                preferencias = models.PreferenciasPessoais(user_id=user_id, fuso=fuso_padrao())
                session.add(preferencias)
            preferencias.horario_resumo = horario
            session.query(models.EntregaPessoal).filter_by(user_id=user_id, tipo="resumo", estado="pendente").update(
                {"estado": "cancelado"})
        if horario:
            return visual.mensagem("✓ Resumo diário ativado", f"Todos os dias às *{horario}*.",
                                   f"Fuso: {self.fuso(user_id)}", rodape="Consultar agora: /hoje · Desativar: /resumodiario off")
        return visual.mensagem("Resumo diário desativado", "Você ainda pode consultar seu dia com /hoje.")

    def configurar_fuso(self, user_id, nome):
        validar_fuso(nome)
        with self.session_factory.begin() as session:
            preferencias = session.get(models.PreferenciasPessoais, user_id)
            if not preferencias:
                preferencias = models.PreferenciasPessoais(user_id=user_id)
                session.add(preferencias)
            preferencias.fuso = nome
        return visual.mensagem("✓ Fuso atualizado", nome,
                               "Vale para novos agendamentos e o resumo diário. Lembretes existentes mantêm o fuso original.")

    def agenda(self, user_id, dados=None):
        dados = dados or {}
        fuso = self.fuso(user_id)
        inicio = ler_data(dados["inicio"], fuso) if dados.get("inicio") else utc(
            agora_utc().astimezone(validar_fuso(fuso)).replace(hour=0, minute=0, second=0, microsecond=0))
        fim = ler_data(dados["fim"], fuso) if dados.get("fim") else utc(
            inicio.astimezone(validar_fuso(fuso)) + timedelta(days=1))
        if not inicio < fim <= inicio + timedelta(days=31):
            raise ValueError("Consulte um intervalo entre 1 minuto e 31 dias.")
        eventos = self.calendario_factory(user_id).listar(inicio, fim, fuso)
        return agenda.formatar_agenda(eventos, inicio, fim, fuso)

    def _linhas_eventos(self, eventos, fuso):
        return agenda.linhas_eventos(eventos, fuso, agrupar=False)

    def gerenciar_agenda(self, user_id, dados, pedido, historico=None, agora=None):
        instante_fixo = agora
        agora = agora or agora_utc()
        fuso = self.fuso(user_id)
        local = agora.astimezone(validar_fuso(fuso))
        segunda = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        inicio = ler_data(dados["inicio_busca"], fuso) if dados.get("inicio_busca") else utc(segunda)
        fim = ler_data(dados["fim_busca"], fuso) if dados.get("fim_busca") else (
            inicio + timedelta(days=7) if dados.get("inicio_busca") else utc(segunda + timedelta(days=7)))
        if not inicio < fim <= inicio + timedelta(days=31):
            raise ValueError("Informe um período de até 31 dias para encontrar o compromisso.")
        calendario = self.calendario_factory(user_id)
        if not hasattr(calendario, "alterar"):
            raise ValueError("A alteração de compromissos ainda não está disponível para este provedor.")
        eventos = calendario.listar(inicio, fim, fuso)
        if not eventos:
            return f"Não encontrei compromissos entre {formatar_data(inicio, fuso)} e {formatar_data(fim, fuso)}. Em qual dia está o compromisso que você quer mudar?"
        if len(eventos) > 100:
            return "Há muitos compromissos nesse período. Em qual dia está o que você quer mudar?"
        referencias = {str(i): evento for i, evento in enumerate(eventos)}
        contexto = {"agora": local.isoformat(), "fuso": fuso, "eventos": [
            {"ref": ref, "titulo": evento.titulo, "inicio": evento.inicio.astimezone(local.tzinfo).isoformat(),
             "fim": evento.fim.astimezone(local.tzinfo).isoformat(), "dia_inteiro": evento.dia_inteiro,
             "recorrencia_original": evento.recorrencia, "recorrente": evento.recorrente}
            for ref, evento in referencias.items()]}
        plano = brain.planejar_agenda(pedido, contexto, historico)
        if plano.get("estado") == "esclarecer":
            resposta = plano.get("resposta")
            return resposta if isinstance(resposta, str) and resposta.strip() else "Qual compromisso e horário você quer ajustar?"
        if plano.get("estado") != "executar":
            return "Não consegui avaliar essa alteração agora. Nenhum compromisso foi modificado."
        ref = plano.get("ref")
        if not isinstance(ref, str) or ref not in referencias or plano.get("escopo") != "ocorrencia":
            raise ValueError("Preciso identificar uma ocorrência específica antes de alterar a agenda.")
        evento = referencias[ref]
        acao = plano.get("acao")
        if acao not in {"mover", "cancelar"} or evento.dia_inteiro or evento.fim <= evento.inicio:
            raise ValueError("Preciso de um compromisso com horário e de uma alteração válida.")
        novo_inicio = None
        if acao == "mover":
            if plano.get("novo_inicio") is not None and plano.get("deslocamento_minutos") is not None:
                raise ValueError("Não ficou claro qual é o novo horário. Para quando devo mover esse compromisso?")
            if plano.get("deslocamento_minutos") is not None:
                minutos = plano["deslocamento_minutos"]
                if type(minutos) is not int or not -44640 <= minutos <= 44640:
                    raise ValueError("Informe quanto deseja adiantar ou adiar esse compromisso.")
                novo_inicio = evento.inicio + timedelta(minutes=minutos)
            else:
                novo_inicio = ler_data(plano.get("novo_inicio"), fuso)
            if novo_inicio <= (instante_fixo or agora_utc()):
                raise ValueError("O novo horário está no passado. Para quando devo mover o compromisso?")
            if novo_inicio == evento.inicio:
                return f"{visual.nome(evento.titulo)} já está nesse horário: {formatar_data(novo_inicio, fuso)}."
            novo_fim = novo_inicio + (evento.fim - evento.inicio)
            conflitos = [item for item in calendario.listar(novo_inicio, novo_fim, fuso)
                         if item.ocupado and not (item.uid == evento.uid and item.inicio == evento.inicio)]
            if conflitos and plano.get("permitir_conflito") is not True:
                return (f"Esse horário coincide com {', '.join(visual.nome(item.titulo) for item in conflitos)}. "
                        f"Quer manter {visual.nome(evento.titulo)} em {formatar_data(novo_inicio, fuso)} mesmo assim?")
        elif evento.inicio <= (instante_fixo or agora_utc()):
            raise ValueError("Esse compromisso já começou ou passou. Qual ocorrência você quer cancelar?")
        identidade = calendar_service.identidade_calendario()
        operacao = hashlib.sha256(f"{user_id}|{identidade}|{evento.uid}|{evento.recorrencia or evento.inicio.isoformat()}|{acao}|{novo_inicio}".encode()).hexdigest()
        calendario.alterar(evento, acao, novo_inicio, operacao, fuso)
        # Invalida o aviso do horário antigo. A próxima sincronização agenda o novo horário.
        chave_antiga = "agenda:" + hashlib.sha256((str(user_id) + identidade + evento.uid + utc(evento.inicio).isoformat()).encode()).hexdigest()
        with self.session_factory.begin() as session:
            session.query(models.EntregaPessoal).filter_by(user_id=user_id, chave=chave_antiga, estado="pendente").update({"estado": "cancelado"})
            if not evento.recorrente:
                registros = session.query(models.EventoPendente).filter_by(user_id=user_id, uid=evento.uid, estado="criado").all()
                for registro in registros:
                    if acao == "cancelar":
                        registro.estado = "cancelado"
                    else:
                        registro.inicio, registro.fim = novo_inicio, novo_inicio + (evento.fim - evento.inicio)
        if acao == "cancelar":
            return visual.mensagem("✓ Compromisso cancelado", visual.nome(evento.titulo), formatar_data(evento.inicio, fuso),
                                   "A alteração vale apenas para essa ocorrência.")
        return visual.mensagem("✓ Compromisso remarcado", visual.nome(evento.titulo),
            f"De: {formatar_data(evento.inicio, fuso)}\nPara: {formatar_data(novo_inicio, fuso)}",
            "Mantive a duração e os demais compromissos da série." if evento.recorrente else "Mantive a duração do compromisso.")

    def preparar_evento(self, user_id, dados, agora=None):
        agora = agora or agora_utc()
        fuso = self.fuso(user_id)
        titulo = dados.get("titulo")
        if not isinstance(titulo, str) or not titulo.strip() or len(titulo) > 500:
            raise ValueError("Informe o título do compromisso, em até 500 caracteres.")
        inicio = ler_data(dados.get("inicio"), fuso)
        fim = ler_data(dados.get("fim"), fuso)
        minutos = dados.get("aviso_minutos", 30)
        if type(minutos) is not int or not 0 <= minutos <= 40320:
            raise ValueError("O aviso deve ser entre 0 e 40320 minutos antes do evento.")
        if inicio <= agora or not inicio < fim <= inicio + timedelta(days=7):
            raise ValueError("Informe início no futuro e término posterior, com duração máxima de 7 dias.")
        conflitos = [evento for evento in self.calendario_factory(user_id).listar(inicio, fim, fuso) if evento.ocupado]
        with self.session_factory.begin() as session:
            evento = models.EventoPendente(user_id=user_id, uid=uuid.uuid4().hex, titulo=titulo.strip(),
                                           inicio=inicio, fim=fim, aviso_minutos=minutos,
                                           expira_em=agora + timedelta(minutes=15),
                                           calendario=calendar_service.identidade_calendario())
            session.add(evento)
            session.flush()
            inicio_local = inicio.astimezone(validar_fuso(fuso))
            fim_local = fim.astimezone(validar_fuso(fuso))
            horario_final = fim_local.strftime("%H:%M") if inicio_local.date() == fim_local.date() else formatar_data(fim, fuso)
            conflito = visual.bloco("Conflitos de horário", "\n".join(f"• {visual.nome(item.titulo)}" for item in conflitos)) if conflitos else None
            return visual.mensagem("Confirmar compromisso",
                visual.bloco(visual.nome(titulo), f"{formatar_data(inicio, fuso)} até {horario_final}",
                             f"Aviso: {minutos} min antes · Fuso: {fuso}"), conflito,
                rodape=f"Pode responder ‘pode criar’ ou ‘pode descartar’.\nCriar: /confirmar {evento.id}\nDescartar: /descartar {evento.id}\nO pedido expira em 15 minutos.")

    def confirmar_evento(self, user_id, identificador, descartar=False, agora=None):
        agora = agora or agora_utc()
        with self.session_factory.begin() as session:
            evento = session.query(models.EventoPendente).filter_by(id=identificador, user_id=user_id).with_for_update().first()
            if not evento:
                raise ValueError("Pedido não encontrado para este usuário.")
            if evento.estado == "criado":
                if descartar:
                    raise ValueError("Esse compromisso já foi criado. Descartar só abandona pedidos ainda não criados. Para cancelar, diga qual compromisso e ocorrência deseja remover.")
                return visual.mensagem("Compromisso já criado", visual.nome(evento.titulo), rodape="Confira em /agenda.")
            if evento.estado != "pendente" or utc(evento.expira_em) <= agora or utc(evento.inicio) <= agora:
                raise ValueError("Pedido expirado ou descartado. Envie o compromisso novamente.")
            if descartar:
                evento.estado = "descartado"
                return visual.mensagem("Pedido descartado", "O compromisso não foi criado.")
            if evento.calendario != calendar_service.identidade_calendario():
                raise ValueError("O calendário configurado mudou. Envie o compromisso novamente.")
            self.calendario_factory(user_id).criar(evento.uid, evento.titulo, utc(evento.inicio), utc(evento.fim), evento.aviso_minutos)
            evento.estado = "criado"
            return visual.mensagem("✓ Compromisso criado", visual.bloco(visual.nome(evento.titulo),
                                   formatar_data(evento.inicio, self.fuso(user_id))), rodape="Consultar: /agenda")

    def resumo(self, user_id, agora=None):
        agora = agora or agora_utc()
        fuso = self.fuso(user_id)
        zona = validar_fuso(fuso)
        local = agora.astimezone(zona)
        inicio = local.replace(hour=0, minute=0, second=0, microsecond=0)
        fim = inicio + timedelta(days=1)
        inicio_mes = inicio.replace(day=1)
        secoes = []
        relevante = False
        if calendar_service.dono_calendario() == user_id and calendar_service.provedor_nome():
            try:
                eventos = self.calendario_factory(user_id).listar(utc(inicio), utc(fim), fuso)
                secoes.append(visual.bloco("Agenda", "\n".join(self._linhas_eventos(eventos, fuso))))
                relevante = bool(eventos)
            except Exception as erro:
                logger.warning("Agenda indisponível no resumo: %s", type(erro).__name__)
                secoes.append(visual.bloco("Agenda", "Indisponível agora. Tente /agenda mais tarde."))
                relevante = True
        with self.session_factory() as session:
            lembretes = session.query(models.Lembrete).filter(
                models.Lembrete.user_id == user_id, models.Lembrete.ativo.is_(True),
                (models.Lembrete.proxima_data < utc(fim)) | models.Lembrete.proxima_data.is_(None),
            ).all()
            pendencias = []
            for lembrete in lembretes:
                quando = formatar_data(lembrete.proxima_data, lembrete.fuso) if lembrete.proxima_data else "aguardando conclusão"
                pendencias.append(f"• {visual.nome(lembrete.descricao)} · #{lembrete.id}\n  {quando}")
            if pendencias:
                secoes.append(visual.bloco("Lembretes", "\n".join(pendencias)))
            relevante = relevante or bool(lembretes)
            cobrancas = []
            for modelo, rotulo in ((models.Assinatura, "Renovação"), (models.Parcelamento, "Parcela prevista")):
                registros = session.query(modelo).filter(
                    modelo.user_id == user_id, modelo.proxima_data >= utc(inicio),
                    modelo.proxima_data < utc(fim + timedelta(days=1)),
                ).all()
                for registro in registros:
                    valor = registro.valor if modelo is models.Assinatura else registro.valor_parcela
                    data = utc(registro.proxima_data).astimezone(zona).strftime("%d/%m")
                    cobrancas.append(f"• {visual.nome(registro.descricao)} · {visual.moeda(valor)}\n  {rotulo} em {data}")
                relevante = relevante or bool(registros)
            if cobrancas:
                secoes.append(visual.bloco("Cobranças · Hoje e amanhã", "\n".join(cobrancas)))
            transacoes = session.query(models.Transacao).filter(
                models.Transacao.user_id == user_id, models.Transacao.data >= utc(inicio_mes),
                models.Transacao.data < utc(inicio_mes + relativedelta(months=1)),
            ).all()
            entradas = sum(item.valor for item in transacoes if item.tipo == "Entrada")
            saidas = sum(item.valor for item in transacoes if item.tipo == "Saida")
            if transacoes:
                secoes.append(visual.bloco("Finanças do mês", f"Entradas: {visual.moeda(entradas)}\n"
                    f"Saídas: {visual.moeda(saidas)}\nResultado dos registros: {visual.moeda(entradas - saidas)}"))
                relevante = True
            metas = []
            for meta in session.query(models.Meta).filter_by(user_id=user_id).all():
                gasto = sum(item.valor for item in transacoes if item.tipo == "Saida" and item.categoria == meta.categoria)
                restante = meta.valor_limite - gasto
                situacao = f"restam {visual.moeda(restante)}" if restante >= 0 else f"limite excedido em {visual.moeda(-restante)}"
                metas.append(f"• {visual.nome(meta.categoria)} · {situacao}")
                relevante = True
            if metas:
                secoes.append(visual.bloco("Metas", "\n".join(metas)))
        if not relevante:
            secoes = ["Nenhum lembrete, compromisso ou registro financeiro para mostrar."]
        return visual.mensagem(f"Seu dia · {local.strftime('%d/%m/%Y')}", *secoes), relevante

    def executar_intencao(self, user_id, resultado, mensagem_usuario="", historico=None):
        intencao = resultado.get("intencao")
        if intencao not in INTENCOES:
            return False
        dados = resultado.get("dados") or {}
        try:
            if not isinstance(dados, dict):
                raise ValueError("Não entendi os dados. Informe o pedido novamente.")
            if intencao == "lembrete_criar":
                mensagem = self.criar_lembrete(user_id, dados)
            elif intencao == "lembretes":
                mensagem = self.listar_lembretes(user_id)
            elif intencao == "lembrete_alterar":
                mensagem = self.alterar_lembrete(user_id, self._identificador(dados.get("id")), dados.get("acao"), dados.get("minutos"))
            elif intencao == "agenda":
                mensagem = self.agenda(user_id, dados)
            elif intencao == "evento_criar":
                mensagem = self.preparar_evento(user_id, dados)
            elif intencao == "agenda_gerenciar":
                mensagem = self.gerenciar_agenda(user_id, dados, mensagem_usuario, historico)
            elif intencao in {"evento_confirmar", "evento_descartar"}:
                mensagem = self.confirmar_evento(user_id, self._identificador(dados.get("id")), intencao == "evento_descartar")
            elif intencao == "hoje":
                mensagem = self.resumo(user_id)[0]
            elif intencao == "resumo_diario_config":
                if dados.get("desativar") is True:
                    mensagem = self.configurar_resumo(user_id, None)
                else:
                    validar_horario(dados.get("horario"))
                    mensagem = self.configurar_resumo(user_id, dados["horario"])
            else:
                mensagem = resultado.get("resposta") or "Informe o dia e o horário do pedido."
        except ValueError as erro:
            mensagem = visual.mensagem("Confira o pedido", str(erro))
        except Exception as erro:
            logger.warning("Falha no assistente pessoal: %s", type(erro).__name__)
            mensagem = visual.erro("O serviço está indisponível agora.", "Confira a configuração do calendário ou tente novamente em instantes.")
        self.enviar_texto(user_id, mensagem)
        return True

    def _identificador(self, valor):
        if isinstance(valor, bool):
            raise ValueError("Informe o número do item.")
        try:
            identificador = int(str(valor).lstrip("#"))
        except (ValueError, TypeError):
            raise ValueError("Informe o número do item. Veja /lembretes.") from None
        if identificador < 1:
            raise ValueError("Informe um número de item positivo.")
        return identificador

    def comando(self, user_id, comando, args):
        if comando not in COMANDOS_PESSOAIS:
            return False
        try:
            if comando == "lembretes":
                mensagem = self.listar_lembretes(user_id)
            elif comando in {"concluir", "adiar", "cancelarlembrete"}:
                if len(args) != (2 if comando == "adiar" else 1):
                    raise ValueError(f"Uso: /{comando} ID" + (" MINUTOS" if comando == "adiar" else ""))
                minutos = int(args[1]) if comando == "adiar" else None
                mensagem = self.alterar_lembrete(user_id, self._identificador(args[0]),
                                                "cancelar" if comando == "cancelarlembrete" else comando, minutos)
            elif comando == "resumodiario":
                if len(args) != 1:
                    raise ValueError("Use /resumodiario 08:00 para ativar ou /resumodiario off para desativar.")
                mensagem = self.configurar_resumo(user_id, None if args[0].lower() == "off" else args[0])
            elif comando == "hoje":
                mensagem = self.resumo(user_id)[0]
            elif comando == "agenda":
                mensagem = self.agenda(user_id)
            elif comando == "calendarios":
                nomes = self.calendario_factory(user_id).listar_calendarios()
                mensagem = "📅 *Seus calendários*\n\n" + "\n".join(f"• {nome}" for nome in nomes)
            elif comando == "diagnosticocalendario":
                calendario = self.calendario_factory(user_id)
                if not hasattr(calendario, "diagnosticar"):
                    raise ValueError("O diagnóstico detalhado está disponível para o iCloud.")
                mensagem = calendario.diagnosticar()
            elif comando in {"confirmar", "descartar"}:
                if len(args) != 1:
                    raise ValueError(f"Use /{comando} ID com o número do pedido de evento.")
                mensagem = self.confirmar_evento(user_id, self._identificador(args[0]), comando == "descartar")
            else:
                if len(args) != 1:
                    raise ValueError("Use /fuso America/Sao_Paulo.")
                mensagem = self.configurar_fuso(user_id, args[0])
        except ValueError as erro:
            mensagem = visual.mensagem("Confira o pedido", str(erro))
        except Exception as erro:
            logger.warning("Falha no comando pessoal /%s: %s", comando, type(erro).__name__)
            mensagem = visual.erro("Não foi possível concluir o comando agora.", "Tente novamente em instantes. A causa pode ser uma falha de conexão ou integração.")
        self.enviar_texto(user_id, mensagem)
        return True

    def agendar_lembretes(self, agora):
        with self.session_factory.begin() as session:
            lembretes = session.query(models.Lembrete).filter(
                models.Lembrete.ativo.is_(True), models.Lembrete.proxima_data <= agora,
            ).with_for_update(skip_locked=True).all()
            for lembrete in lembretes:
                data = utc(lembrete.proxima_data)
                enfileirar(session, f"lembrete:{lembrete.id}:{data.isoformat()}", lembrete.user_id,
                           visual.mensagem(f"🔔 Lembrete · #{lembrete.id}",
                               visual.bloco(visual.nome(lembrete.descricao), formatar_data(data, lembrete.fuso)),
                               rodape=f"Adiar 10 min: /adiar {lembrete.id} 10\nConcluir: /concluir {lembrete.id}" +
                               (" (encerra a recorrência)" if lembrete.repeticao != "nenhuma" else "")),
                           agora, lembrete_id=lembrete.id)
                lembrete.proxima_data = proxima_ocorrencia(data, lembrete.repeticao, lembrete.fuso, lembrete.dia_original, agora)

    def agendar_resumos(self, agora):
        with self.session_factory() as session:
            usuarios = [item.user_id for item in session.query(models.PreferenciasPessoais).filter(
                models.PreferenciasPessoais.horario_resumo.isnot(None)).all()]
        for user_id in usuarios:
            with self.session_factory.begin() as session:
                preferencias = session.query(models.PreferenciasPessoais).filter_by(user_id=user_id).with_for_update().one()
                if not preferencias.horario_resumo:
                    continue
                local = agora.astimezone(validar_fuso(preferencias.fuso))
                if local.strftime("%H:%M") < preferencias.horario_resumo:
                    continue
                chave = f"resumo:{user_id}:{local.date().isoformat()}"
                if session.query(models.EntregaPessoal.id).filter_by(chave=chave).first():
                    continue
                texto, relevante = self.resumo(user_id, agora)
                expira = utc(datetime.combine(local.date() + timedelta(days=1), time.min, local.tzinfo))
                enfileirar(session, chave, user_id, texto, agora, tipo="resumo", expira_em=expira,
                           estado="pendente" if relevante else "ignorado")

    def agendar_avisos_calendario(self, agora):
        user_id = calendar_service.dono_calendario()
        if not user_id or not calendar_service.provedor_nome():
            return
        fuso = self.fuso(user_id)
        with self.session_factory.begin() as session:
            criados = {item.uid: item.aviso_minutos for item in session.query(models.EventoPendente).filter_by(
                user_id=user_id, estado="criado", calendario=calendar_service.identidade_calendario()).filter(
                    models.EventoPendente.fim > agora).all()}
        if calendar_service.provedor_nome() == "google":
            criados = {hashlib.sha256(uid.encode()).hexdigest(): minutos for uid, minutos in criados.items()}
        horizonte = max(30, max(criados.values(), default=30)) + 2
        eventos = self.calendario_factory(user_id).listar(agora, agora + timedelta(minutes=horizonte), fuso)
        with self.session_factory.begin() as session:
            chaves_atuais = set()
            for evento in eventos:
                if evento.dia_inteiro:
                    continue
                minutos = criados.get(evento.uid, 30)
                aviso = utc(evento.inicio) - timedelta(minutes=minutos)
                if aviso <= agora < utc(evento.fim) and (agora < utc(evento.inicio) or minutos == 0):
                    chave = hashlib.sha256((str(user_id) + calendar_service.identidade_calendario() +
                                            evento.uid + utc(evento.inicio).isoformat()).encode()).hexdigest()
                    chave = "agenda:" + chave
                    chaves_atuais.add(chave)
                    enfileirar(session, chave, user_id,
                               visual.mensagem("Compromisso em breve", visual.bloco(visual.nome(evento.titulo),
                                                formatar_data(evento.inicio, fuso))), agora,
                               tipo="agenda", expira_em=utc(evento.fim) if minutos == 0 else utc(evento.inicio))
            pendentes = session.query(models.EntregaPessoal).filter_by(user_id=user_id, tipo="agenda", estado="pendente").all()
            for entrega in pendentes:
                if entrega.chave not in chaves_atuais:
                    entrega.estado = "cancelado"

    def enviar_pendentes(self, agora, agenda_disponivel=True):
        with self.session_factory() as session:
            identificadores = [item.id for item in session.query(models.EntregaPessoal).filter(
                models.EntregaPessoal.estado == "pendente", models.EntregaPessoal.tentar_em <= agora,
                models.EntregaPessoal.tipo != "proativo",
            ).order_by(models.EntregaPessoal.id).limit(100).all()]
        for identificador in identificadores:
            with self.session_factory.begin() as session:
                entrega = session.query(models.EntregaPessoal).filter_by(
                    id=identificador, estado="pendente").filter(
                        models.EntregaPessoal.tentar_em <= agora).with_for_update(skip_locked=True).first()
                if not entrega:
                    continue
                if entrega.tipo == "agenda":
                    if entrega.user_id != calendar_service.dono_calendario() or not calendar_service.provedor_nome():
                        entrega.estado = "cancelado"
                        continue
                    if not agenda_disponivel:
                        continue
                if entrega.expira_em and utc(entrega.expira_em) <= agora:
                    entrega.estado = "expirado"
                    continue
                try:
                    self.enviar_texto(entrega.user_id, entrega.texto)
                    entrega.estado = "enviado"
                except Exception as erro:
                    entrega.tentativas += 1
                    entrega.tentar_em = agora + timedelta(seconds=min(3600, 60 * 2 ** min(entrega.tentativas - 1, 6)))
                    logger.warning("Falha na entrega pessoal %s: %s", entrega.id, type(erro).__name__)

    def ciclo(self, agora=None):
        agora = agora or agora_utc()
        for etapa in (self.agendar_lembretes, self.agendar_resumos):
            try:
                etapa(agora)
            except Exception as erro:
                logger.warning("Falha na etapa %s: %s", etapa.__name__, type(erro).__name__)
        agenda_disponivel = True
        try:
            self.agendar_avisos_calendario(agora)
        except Exception as erro:
            agenda_disponivel = False
            logger.warning("Falha ao sincronizar avisos de calendário: %s", type(erro).__name__)
        try:
            self.enviar_pendentes(agora, agenda_disponivel=agenda_disponivel)
        except Exception as erro:
            logger.warning("Falha ao enviar pendências pessoais: %s", type(erro).__name__)

    def vigiar(self):
        import threading

        parada = threading.Event()
        while not parada.is_set():
            self.ciclo()
            parada.wait(60)
