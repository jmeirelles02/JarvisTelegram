import hashlib
import logging
import uuid
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import IntegrityError

from app import models
from app.services import agenda, calendar_service
from app.services.personal_time import (
    agora_utc, formatar_data, fuso_padrao, ler_data, utc, validar_fuso, validar_horario,
)


logger = logging.getLogger(__name__)
REPETICOES = {"nenhuma", "diaria", "semanal", "mensal", "dias_uteis"}
INTENCOES = {"lembrete_criar", "lembretes", "lembrete_alterar", "agenda", "evento_criar",
             "hoje", "resumo_diario_config", "esclarecer"}
COMANDOS_PESSOAIS = {"lembretes", "concluir", "adiar", "cancelarlembrete", "resumodiario", "hoje", "agenda",
                      "calendarios", "confirmar", "descartar", "fuso"}


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
            return (f"🔔 Lembrete #{lembrete.id}: {lembrete.descricao}\n"
                    f"{formatar_data(data, fuso)} ({fuso}) · repetição: {repeticao}.\n"
                    "Veja /lembretes para concluir, adiar ou cancelar.")

    def listar_lembretes(self, user_id):
        with self.session_factory() as session:
            lembretes = session.query(models.Lembrete).filter_by(user_id=user_id, ativo=True).order_by(
                models.Lembrete.proxima_data, models.Lembrete.id).all()
            linhas = ["🔔 *Seus lembretes:*"]
            for lembrete in lembretes:
                data = formatar_data(lembrete.proxima_data, lembrete.fuso) if lembrete.proxima_data else "aguardando conclusão"
                linhas.append(f"#{lembrete.id} · {lembrete.descricao} · {data} · {lembrete.repeticao}")
            if not lembretes:
                linhas.append("Nenhum ativo. Exemplo: 'Me lembra de pagar a internet amanhã às 9h'.")
            linhas.append("/concluir ID · /adiar ID MINUTOS · /cancelarlembrete ID\n"
                          "Concluir encerra o lembrete, inclusive se for recorrente.")
            return "\n".join(linhas)

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
                return f"🔔 Lembrete #{identificador} adiado para {formatar_data(lembrete.proxima_data, lembrete.fuso)}."
            lembrete.ativo = False
            return f"✅ Lembrete #{identificador} {'concluído' if acao == 'concluir' else 'cancelado'}."

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
        return f"☀️ Resumo diário ativado às {horario} ({self.fuso(user_id)})." if horario else "Resumo diário desativado."

    def configurar_fuso(self, user_id, nome):
        validar_fuso(nome)
        with self.session_factory.begin() as session:
            preferencias = session.get(models.PreferenciasPessoais, user_id)
            if not preferencias:
                preferencias = models.PreferenciasPessoais(user_id=user_id)
                session.add(preferencias)
            preferencias.fuso = nome
        return f"Fuso definido: {nome}. Novos agendamentos e o resumo usarão esse fuso; lembretes existentes mantêm o seu."

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
            mensagem = (f"📅 Criar '{titulo.strip()}'?\n{formatar_data(inicio, fuso)} até {formatar_data(fim, fuso)}"
                        f" ({fuso}). Aviso {minutos} min antes.\n")
            if conflitos:
                mensagem += "⚠️ Conflitos: " + "; ".join(evento.titulo for evento in conflitos) + ".\n"
            return mensagem + f"/confirmar {evento.id} para criar. /descartar {evento.id} para desistir. Expira em 15 min."

    def confirmar_evento(self, user_id, identificador, descartar=False, agora=None):
        agora = agora or agora_utc()
        with self.session_factory.begin() as session:
            evento = session.query(models.EventoPendente).filter_by(id=identificador, user_id=user_id).with_for_update().first()
            if not evento:
                raise ValueError("Pedido não encontrado para este usuário.")
            if evento.estado == "criado":
                return "Esse evento já foi criado no calendário."
            if evento.estado != "pendente" or utc(evento.expira_em) <= agora or utc(evento.inicio) <= agora:
                raise ValueError("Pedido expirado ou descartado. Envie o compromisso novamente.")
            if descartar:
                evento.estado = "descartado"
                return "Pedido descartado."
            if evento.calendario != calendar_service.identidade_calendario():
                raise ValueError("O calendário configurado mudou. Envie o compromisso novamente.")
            self.calendario_factory(user_id).criar(evento.uid, evento.titulo, utc(evento.inicio), utc(evento.fim), evento.aviso_minutos)
            evento.estado = "criado"
            return f"✅ '{evento.titulo}' criado no calendário para {formatar_data(evento.inicio, self.fuso(user_id))}."

    def resumo(self, user_id, agora=None):
        agora = agora or agora_utc()
        fuso = self.fuso(user_id)
        zona = validar_fuso(fuso)
        local = agora.astimezone(zona)
        inicio = local.replace(hour=0, minute=0, second=0, microsecond=0)
        fim = inicio + timedelta(days=1)
        inicio_mes = inicio.replace(day=1)
        linhas = [f"☀️ *Seu dia · {local.strftime('%d/%m/%Y')}*"]
        relevante = False
        if calendar_service.dono_calendario() == user_id and calendar_service.provedor_nome():
            try:
                eventos = self.calendario_factory(user_id).listar(utc(inicio), utc(fim), fuso)
                linhas.extend(self._linhas_eventos(eventos, fuso))
                relevante = bool(eventos)
            except Exception as erro:
                logger.warning("Agenda indisponível no resumo: %s", type(erro).__name__)
                linhas.append("📅 Agenda indisponível agora. Consulte /agenda mais tarde.")
                relevante = True
        with self.session_factory() as session:
            lembretes = session.query(models.Lembrete).filter(
                models.Lembrete.user_id == user_id, models.Lembrete.ativo.is_(True),
                (models.Lembrete.proxima_data < utc(fim)) | models.Lembrete.proxima_data.is_(None),
            ).all()
            for lembrete in lembretes:
                quando = formatar_data(lembrete.proxima_data, lembrete.fuso) if lembrete.proxima_data else "aguardando conclusão"
                linhas.append(f"🔔 #{lembrete.id} {lembrete.descricao} · {quando}")
            relevante = relevante or bool(lembretes)
            for modelo, rotulo in ((models.Assinatura, "Renovação"), (models.Parcelamento, "Parcela prevista")):
                registros = session.query(modelo).filter(
                    modelo.user_id == user_id, modelo.proxima_data >= utc(inicio),
                    modelo.proxima_data < utc(fim + timedelta(days=1)),
                ).all()
                for registro in registros:
                    valor = registro.valor if modelo is models.Assinatura else registro.valor_parcela
                    linhas.append(f"💳 {rotulo}: {registro.descricao} · R$ {valor:.2f} · "
                                  f"{formatar_data(registro.proxima_data, fuso)}")
                relevante = relevante or bool(registros)
            transacoes = session.query(models.Transacao).filter(
                models.Transacao.user_id == user_id, models.Transacao.data >= utc(inicio_mes),
                models.Transacao.data < utc(inicio_mes + relativedelta(months=1)),
            ).all()
            entradas = sum(item.valor for item in transacoes if item.tipo == "Entrada")
            saidas = sum(item.valor for item in transacoes if item.tipo == "Saida")
            if transacoes:
                linhas.append(f"💰 Registros do mês: entradas R$ {entradas:.2f} · saídas R$ {saidas:.2f} · "
                              f"diferença R$ {entradas - saidas:.2f}.")
                relevante = True
            for meta in session.query(models.Meta).filter_by(user_id=user_id).all():
                gasto = sum(item.valor for item in transacoes if item.tipo == "Saida" and item.categoria == meta.categoria)
                restante = meta.valor_limite - gasto
                linhas.append(f"🎯 {meta.categoria}: " + (f"restam R$ {restante:.2f}." if restante >= 0 else f"limite excedido em R$ {-restante:.2f}."))
                relevante = True
        if not relevante:
            linhas.append("Nenhum lembrete, compromisso ou registro financeiro para mostrar.")
        return "\n".join(linhas), relevante

    def executar_intencao(self, user_id, resultado):
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
            mensagem = str(erro)
        except Exception as erro:
            logger.warning("Falha no assistente pessoal: %s", type(erro).__name__)
            mensagem = "Não consegui acessar o serviço agora. Verifique a configuração do calendário ou tente novamente."
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
            elif comando in {"confirmar", "descartar"}:
                if len(args) != 1:
                    raise ValueError(f"Use /{comando} ID com o número do pedido de evento.")
                mensagem = self.confirmar_evento(user_id, self._identificador(args[0]), comando == "descartar")
            else:
                if len(args) != 1:
                    raise ValueError("Use /fuso America/Sao_Paulo.")
                mensagem = self.configurar_fuso(user_id, args[0])
        except ValueError as erro:
            mensagem = str(erro)
        except Exception as erro:
            logger.warning("Falha no comando pessoal: %s", type(erro).__name__)
            mensagem = "Não consegui acessar o calendário. Verifique as credenciais e tente novamente."
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
                           f"🔔 *Lembrete #{lembrete.id}:* {lembrete.descricao}\n"
                           f"Agendado para {formatar_data(data, lembrete.fuso)}.\n"
                           f"/adiar {lembrete.id} 10 · /concluir {lembrete.id}" +
                           (" (encerra a recorrência)" if lembrete.repeticao != "nenhuma" else ""),
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
                               f"📅 {evento.titulo} · {formatar_data(evento.inicio, fuso)}.", agora,
                               tipo="agenda", expira_em=utc(evento.fim) if minutos == 0 else utc(evento.inicio))
            pendentes = session.query(models.EntregaPessoal).filter_by(user_id=user_id, tipo="agenda", estado="pendente").all()
            for entrega in pendentes:
                if entrega.chave not in chaves_atuais:
                    entrega.estado = "cancelado"

    def enviar_pendentes(self, agora, agenda_disponivel=True):
        with self.session_factory() as session:
            identificadores = [item.id for item in session.query(models.EntregaPessoal).filter(
                models.EntregaPessoal.estado == "pendente", models.EntregaPessoal.tentar_em <= agora,
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
