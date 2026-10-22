"""Iniciativa periódica: fatos verificáveis, seleção pela IA e entrega com limites."""
import hashlib
import logging
import threading
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from app import models
from app.services import brain, calendar_service, presentation as visual
from app.services.personal import enfileirar
from app.services.personal_time import agora_utc, utc, validar_fuso, formatar_data


logger = logging.getLogger(__name__)
INTERVALO_ANALISE = timedelta(minutes=30)
INTERVALO_CONTATO = timedelta(hours=4)
PAUSA_CONVERSA = timedelta(minutes=10)


def chave_sinal(user_id, sinal):
    resumo = hashlib.sha256(sinal["id"].encode()).hexdigest()
    return f'proativo:{user_id}:{sinal["tipo"]}:{resumo}'


class IniciativaPessoal:
    def __init__(self, session_factory, contexto, conversa, enviar_texto, avaliar=None):
        self.session_factory = session_factory
        self.contexto = contexto
        self.conversa = conversa
        self.enviar_texto = enviar_texto
        self.avaliar = avaliar or brain.avaliar_iniciativa

    def _janela(self, perfil, agora):
        zona = validar_fuso(self.contexto.fuso(perfil.user_id))
        local = agora.astimezone(zona)
        horario = local.strftime("%H:%M")
        inicio, fim = perfil.inicio_contato, perfil.fim_contato
        dentro = inicio <= horario < fim if inicio < fim else horario >= inicio or horario < fim
        termino = datetime.combine(local.date(), time.fromisoformat(fim), zona)
        if termino <= local:
            termino += timedelta(days=1)
        return dentro, local, utc(termino)

    def _disponivel(self, session, perfil, agora):
        dentro, local, _ = self._janela(perfil, agora)
        if not perfil.proativo or not dentro:
            return False
        # Dá espaço para a conversa em curso, inclusive quando o usuário responde a uma iniciativa.
        recente = session.query(models.MensagemConversa.id).filter(
            models.MensagemConversa.user_id == perfil.user_id, models.MensagemConversa.papel == "user",
            models.MensagemConversa.criada_em > agora - PAUSA_CONVERSA,
        ).first()
        if recente:
            return False
        inicio = utc(local.replace(hour=0, minute=0, second=0, microsecond=0))
        fim = utc(local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        enviados = session.query(models.EntregaPessoal).filter_by(
            user_id=perfil.user_id, tipo="proativo", estado="enviado").order_by(models.EntregaPessoal.tentar_em.desc())
        ultimo = enviados.first()
        if ultimo and utc(ultimo.tentar_em) > agora - INTERVALO_CONTATO:
            return False
        return enviados.filter(models.EntregaPessoal.tentar_em >= inicio,
                               models.EntregaPessoal.tentar_em < fim).count() < perfil.limite_diario

    def coletar_sinais(self, user_id, agora):
        fuso = self.contexto.fuso(user_id)
        local = agora.astimezone(validar_fuso(fuso))
        inicio_mes = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        fim_dia = utc(local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        sinais = []

        def adicionar(identificador, tipo, fato, expira=fim_dia):
            if expira > agora:
                sinais.append({"id": identificador, "tipo": tipo, "fato": fato, "expira_em": utc(expira).isoformat()})

        with self.session_factory() as session:
            transacoes = session.query(models.Transacao).filter(
                models.Transacao.user_id == user_id, models.Transacao.tipo == "Saida",
                models.Transacao.data >= utc(inicio_mes),
                models.Transacao.data < utc(inicio_mes + relativedelta(months=1)),
            ).all()
            for meta in session.query(models.Meta).filter_by(user_id=user_id).all():
                gasto = sum(item.valor for item in transacoes if item.categoria == meta.categoria)
                if meta.valor_limite and meta.valor_limite > 0 and gasto >= meta.valor_limite * .8:
                    nivel = 100 if gasto >= meta.valor_limite else 80
                    adicionar(f"meta:{meta.id}:{local:%Y-%m}:{nivel}", "financas",
                        f"Os gastos registrados em {visual.nome(meta.categoria)} neste mês chegaram a "
                        f"{visual.moeda(gasto)} de um limite de {visual.moeda(meta.valor_limite)}.")
            for modelo in (models.Assinatura, models.Parcelamento):
                registros = session.query(modelo).filter(
                    modelo.user_id == user_id, modelo.proxima_data >= agora,
                    modelo.proxima_data <= agora + timedelta(hours=24),
                ).all()
                for item in registros:
                    if modelo is models.Parcelamento and item.parcelas_pagas >= item.parcelas_total:
                        continue
                    valor = item.valor if modelo is models.Assinatura else item.valor_parcela
                    adicionar(f"cobranca:{modelo.__tablename__}:{item.id}:{utc(item.proxima_data).isoformat()}", "financas",
                        f"Há uma cobrança prevista de {visual.moeda(valor)} para {visual.nome(item.descricao)}, "
                        f"em {formatar_data(item.proxima_data, fuso)}. É uma previsão dos seus registros.", utc(item.proxima_data))
            for item in session.query(models.Lembrete).filter_by(user_id=user_id, ativo=True, proxima_data=None).all():
                aviso = session.query(models.EntregaPessoal).filter_by(
                    user_id=user_id, lembrete_id=item.id, estado="enviado", tipo="lembrete").order_by(
                        models.EntregaPessoal.id.desc()).first()
                if aviso and utc(aviso.tentar_em) <= agora - timedelta(hours=2):
                    adicionar(f"pendencia:{item.id}", "pendencia",
                        f'O lembrete “{visual.nome(item.descricao)}” (#{item.id}) foi enviado e está aguardando sua conclusão. '
                        "Não sei se você já resolveu isso fora da conversa.")
            for item in session.query(models.MemoriaPessoal).filter_by(user_id=user_id, tipo="objetivo", concluido=False).all():
                if utc(item.atualizada_em) <= agora - timedelta(days=3):
                    adicionar(f"objetivo:{item.id}:{utc(item.atualizada_em).isoformat()}", "objetivo",
                        f'Você comentou: “{item.texto}”. Ainda não tenho uma atualização sobre esse objetivo.')

        agenda_disponivel = True
        if calendar_service.dono_calendario() == user_id and calendar_service.provedor_nome():
            try:
                eventos = self.contexto.calendario_factory(user_id).listar(agora, agora + timedelta(days=1), fuso)
                eventos = sorted([item for item in eventos if item.ocupado and not item.dia_inteiro
                                  and utc(item.inicio) > agora], key=lambda item: utc(item.inicio))[:50]
                for i, primeiro in enumerate(eventos):
                    for segundo in eventos[i + 1:]:
                        if utc(segundo.inicio) >= utc(primeiro.fim):
                            break
                        identificador = ":".join(sorted([
                            primeiro.uid + utc(primeiro.inicio).isoformat(), segundo.uid + utc(segundo.inicio).isoformat()]))
                        adicionar(f"conflito:{identificador}", "agenda",
                            f'“{visual.nome(primeiro.titulo)}” ({formatar_data(primeiro.inicio, fuso)}) e '
                            f'“{visual.nome(segundo.titulo)}” ({formatar_data(segundo.inicio, fuso)}) se sobrepõem na sua agenda.',
                            min(utc(primeiro.inicio), utc(segundo.inicio)))
            except Exception as erro:
                agenda_disponivel = False
                logger.warning("Agenda indisponível para iniciativa: %s", type(erro).__name__)
        # Conflitos de agenda e cobranças precedem acompanhamentos sem prazo.
        ordem = {"agenda": 0, "financas": 1, "pendencia": 2, "objetivo": 3}
        return sorted(sinais, key=lambda item: ordem[item["tipo"]])[:30], agenda_disponivel

    def analisar(self, user_id, agora):
        with self.session_factory() as session, session.begin():
            perfil = session.query(models.PerfilAssistente).filter_by(user_id=user_id).with_for_update().one()
            if not self._disponivel(session, perfil, agora):
                return
            if perfil.ultima_analise and utc(perfil.ultima_analise) > agora - INTERVALO_ANALISE:
                return
            if session.query(models.EntregaPessoal.id).filter_by(user_id=user_id, tipo="proativo", estado="pendente").first():
                return
            versao_contexto = perfil.versao_contexto
            perfil.ultima_analise = agora
        sinais, _ = self.coletar_sinais(user_id, agora)
        with self.session_factory() as session:
            existentes = {item.chave for item in session.query(models.EntregaPessoal).filter_by(user_id=user_id, tipo="proativo").all()}
        sinais = [item for item in sinais if chave_sinal(user_id, item) not in existentes]
        if not sinais:
            return
        avaliacao = self.avaliar({"agora_local": agora.astimezone(validar_fuso(self.contexto.fuso(user_id))).isoformat(),
            "memorias": self.contexto.memorias(user_id), "sinais": sinais}, self.conversa.historico(user_id))
        if not isinstance(avaliacao, dict) or avaliacao.get("avisar") is not True:
            return
        sinal = next((item for item in sinais if item["id"] == avaliacao.get("sinal_id")), None)
        sugestao = avaliacao.get("sugestao")
        if not sinal or not isinstance(sugestao, str) or not 5 <= len(sugestao.strip()) <= 400:
            return
        with self.session_factory() as session, session.begin():
            perfil = session.query(models.PerfilAssistente).filter_by(user_id=user_id).with_for_update().one()
            if perfil.versao_contexto != versao_contexto or not self._disponivel(session, perfil, agora):
                return
            _, _, fim_janela = self._janela(perfil, agora)
            expira = min(datetime.fromisoformat(sinal["expira_em"]), fim_janela)
            enfileirar(session, chave_sinal(user_id, sinal), user_id,
                       sinal["fato"] + "\n\n" + " ".join(sugestao.split()), agora, tipo="proativo", expira_em=expira)

    def enviar_pendentes(self, agora=None):
        instante_fixo = agora
        with self.session_factory() as session:
            usuarios = [item[0] for item in session.query(models.EntregaPessoal.user_id).filter_by(
                tipo="proativo", estado="pendente").distinct().all()]
        for user_id in usuarios:
            agora = instante_fixo or agora_utc()
            sinais, agenda_disponivel = self.coletar_sinais(user_id, agora)
            validos = {chave_sinal(user_id, item): item for item in sinais}
            with self.session_factory() as session, session.begin():
                perfil = session.query(models.PerfilAssistente).filter_by(user_id=user_id).with_for_update().first()
                entregas = session.query(models.EntregaPessoal).filter_by(
                    user_id=user_id, tipo="proativo", estado="pendente").with_for_update().all()
                for entrega in entregas:
                    if not perfil or not perfil.proativo:
                        entrega.estado = "cancelado"
                        continue
                    if entrega.expira_em and utc(entrega.expira_em) <= agora:
                        entrega.estado = "expirado"
                        continue
                    if ":agenda:" in entrega.chave and not agenda_disponivel:
                        continue
                    if entrega.chave not in validos:
                        entrega.estado = "cancelado"
                        continue
                    if utc(entrega.tentar_em) > agora or not self._disponivel(session, perfil, agora):
                        continue
                    # Recompõe o fato com os dados atuais: evita mandar um valor antigo após uma falha de envio.
                    fato = validos[entrega.chave]["fato"]
                    sugestao = entrega.texto.rsplit("\n\n", 1)[-1]
                    try:
                        self.enviar_texto(user_id, fato + "\n\n" + sugestao)
                        entrega.estado, entrega.tentar_em = "enviado", agora
                        session.flush()
                    except Exception as erro:
                        entrega.tentativas += 1
                        entrega.tentar_em = agora + timedelta(minutes=min(60, 2 ** min(entrega.tentativas, 6)))
                        logger.warning("Falha ao enviar iniciativa: %s", type(erro).__name__)

    def ciclo(self, agora=None):
        instante_fixo = agora
        agora = agora or agora_utc()
        with self.session_factory() as session:
            usuarios = [item[0] for item in session.query(models.PerfilAssistente.user_id).filter_by(proativo=True).all()]
        for user_id in usuarios:
            try:
                self.analisar(user_id, agora)
            except Exception as erro:
                logger.warning("Falha ao analisar iniciativa: %s", type(erro).__name__)
        # A consulta ao modelo pode demorar; a entrega usa um relógio atualizado.
        self.enviar_pendentes(instante_fixo)

    def vigiar(self):
        parada = threading.Event()
        while not parada.is_set():
            try:
                self.ciclo()
            except Exception as erro:
                logger.warning("Ciclo de iniciativa indisponível: %s", type(erro).__name__)
            parada.wait(60)
