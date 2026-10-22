"""Memórias explícitas e preferências do assistente, isoladas por usuário."""
import json
import logging
import re
from datetime import timedelta

from app import models
from app.services import calendar_service, presentation as visual
from app.services.personal_time import agora_utc, fuso_padrao, validar_fuso, validar_horario, utc


logger = logging.getLogger(__name__)
COMANDOS_ASSISTENTE = {"memorias", "esquecer", "proatividade"}
INTENCOES_ASSISTENTE = {"memoria_guardar", "memorias", "memoria_esquecer", "proatividade_config"}


class AssistenteContextual:
    def __init__(self, session_factory, enviar_texto, calendario_factory=None):
        self.session_factory = session_factory
        self.enviar_texto = enviar_texto
        self.calendario_factory = calendario_factory or calendar_service.obter_calendario

    def conhecer_usuario(self, user_id):
        with self.session_factory() as session, session.begin():
            if not session.get(models.PerfilAssistente, user_id):
                session.add(models.PerfilAssistente(user_id=user_id))

    def fuso(self, user_id):
        with self.session_factory() as session:
            preferencias = session.get(models.PreferenciasPessoais, user_id)
            return preferencias.fuso if preferencias else fuso_padrao()

    def memorias(self, user_id):
        with self.session_factory() as session:
            return [{"id": item.id, "chave": item.chave, "tipo": item.tipo, "texto": item.texto,
                     "concluido": item.concluido,
                     "atualizada_em": utc(item.atualizada_em).isoformat()}
                    for item in session.query(models.MemoriaPessoal).filter_by(user_id=user_id).order_by(
                        models.MemoriaPessoal.id).limit(20).all()]

    def guardar(self, user_id, memoria, mensagem_usuario):
        if not isinstance(memoria, dict):
            raise ValueError("Não consegui identificar o que guardar.")
        chave, tipo, trecho = (memoria.get(campo) for campo in ("chave", "tipo", "trecho"))
        concluido = memoria.get("concluido", False)
        if (not isinstance(chave, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", chave)
                or tipo not in {"preferencia", "rotina", "objetivo"}
                or not isinstance(trecho, str) or not 3 <= len(trecho.strip()) <= 500 or type(concluido) is not bool):
            raise ValueError("Informe uma preferência, rotina ou objetivo que devo lembrar.")
        # A memória é uma citação da mensagem atual, nunca um fato inventado pelo modelo.
        if not mensagem_usuario or trecho not in mensagem_usuario:
            raise ValueError("Só posso guardar informações que você afirmou nesta mensagem.")
        self.conhecer_usuario(user_id)
        with self.session_factory() as session, session.begin():
            item = session.query(models.MemoriaPessoal).filter_by(user_id=user_id, chave=chave).first()
            if not item:
                if session.query(models.MemoriaPessoal).filter_by(user_id=user_id).count() >= 20:
                    raise ValueError("Já guardei 20 informações. Apague uma em /memorias antes de adicionar outra.")
                item = models.MemoriaPessoal(user_id=user_id, chave=chave)
                session.add(item)
            if item.texto != trecho.strip() or item.tipo != tipo or item.concluido != concluido:
                item.texto, item.tipo, item.concluido, item.atualizada_em = trecho.strip(), tipo, concluido, agora_utc()
                # Uma correção de contexto invalida sugestões ainda não entregues.
                self._cancelar_pendentes(session, user_id)
        return trecho.strip()

    def assimilar(self, user_id, resultado, mensagem_usuario):
        memorias = resultado.get("memorias")
        if not isinstance(memorias, list):
            return
        for memoria in memorias[:3]:
            try:
                self.guardar(user_id, memoria, mensagem_usuario)
            except Exception as erro:
                logger.warning("Memória não guardada: %s", type(erro).__name__)

    def esquecer(self, user_id, identificador):
        if type(identificador) is not int or identificador < 1:
            raise ValueError("Informe o número da informação que devo esquecer. Veja /memorias.")
        with self.session_factory() as session, session.begin():
            item = session.query(models.MemoriaPessoal).filter_by(user_id=user_id, id=identificador).first()
            if not item:
                raise ValueError("Essa informação não está nas suas memórias. Veja /memorias.")
            session.delete(item)
            # Remove o contexto textual que poderia reintroduzir a informação apagada.
            session.query(models.MensagemConversa).filter_by(user_id=user_id).delete(synchronize_session=False)
            self._cancelar_pendentes(session, user_id)
        return "Esqueci essa informação e limpei o contexto recente da nossa conversa."

    def _cancelar_pendentes(self, session, user_id):
        session.query(models.PerfilAssistente).filter_by(user_id=user_id).update({
            models.PerfilAssistente.versao_contexto: models.PerfilAssistente.versao_contexto + 1,
        })
        session.query(models.EntregaPessoal).filter_by(
            user_id=user_id, tipo="proativo", estado="pendente").update({"estado": "cancelado"})

    def configurar(self, user_id, dados):
        if not isinstance(dados, dict):
            raise ValueError("Informe como deseja receber minhas sugestões.")
        self.conhecer_usuario(user_id)
        fuso = self.fuso(user_id)
        with self.session_factory() as session, session.begin():
            perfil = session.query(models.PerfilAssistente).filter_by(user_id=user_id).with_for_update().one()
            ativo = dados.get("ativo", perfil.proativo)
            inicio = validar_horario(dados.get("inicio", perfil.inicio_contato))
            fim = validar_horario(dados.get("fim", perfil.fim_contato))
            limite = dados.get("limite_diario", perfil.limite_diario)
            if type(ativo) is not bool or type(limite) is not int or not 1 <= limite <= 5 or inicio == fim:
                raise ValueError("Use horários diferentes e de 1 a 5 sugestões por dia.")
            perfil.proativo, perfil.inicio_contato, perfil.fim_contato, perfil.limite_diario = ativo, inicio, fim, limite
            self._cancelar_pendentes(session, user_id)
            texto = (f"Vou avisar quando perceber algo relevante, entre {inicio} e {fim}, "
                     f"com no máximo {limite} sugestões por dia. Fuso: {fuso}. "
                     "Lembretes e avisos já agendados continuam com seus próprios horários.") if ativo else (
                     "Desativei minhas sugestões espontâneas. Seus lembretes e avisos já agendados continuam ativos.")
        return texto

    def contexto(self, user_id):
        agora = agora_utc()
        memorias = self.memorias(user_id)
        with self.session_factory() as session:
            perfil = session.get(models.PerfilAssistente, user_id)
            pendencias = [{"id": item.id, "descricao": item.descricao,
                          "quando": utc(item.proxima_data).isoformat() if item.proxima_data else "aguardando conclusão"}
                         for item in session.query(models.Lembrete).filter_by(user_id=user_id, ativo=True).order_by(
                             models.Lembrete.id).limit(20).all()]
            estado = {"memorias": memorias, "lembretes": pendencias,
                      "sugestoes_ativas": perfil.proativo if perfil else True,
                      "horario_contato": [perfil.inicio_contato, perfil.fim_contato] if perfil else ["08:00", "21:00"]}
        if calendar_service.dono_calendario() == user_id and calendar_service.provedor_nome():
            try:
                eventos = self.calendario_factory(user_id).listar(agora, agora + timedelta(days=1), self.fuso(user_id))
                estado["agenda_proximas_24h"] = [{"titulo": item.titulo, "inicio": utc(item.inicio).isoformat(),
                    "fim": utc(item.fim).isoformat()} for item in eventos[:30]]
            except Exception as erro:
                logger.warning("Agenda indisponível no contexto: %s", type(erro).__name__)
                estado["agenda_proximas_24h"] = "indisponível; não inferir ausência de compromissos"
        return json.dumps(estado, ensure_ascii=False)

    def executar_intencao(self, user_id, resultado, mensagem_usuario=""):
        intencao = resultado.get("intencao")
        if intencao not in INTENCOES_ASSISTENTE:
            return False
        dados = resultado.get("dados") or {}
        try:
            if intencao == "memoria_guardar":
                texto = f'Vou levar em conta: “{self.guardar(user_id, dados, mensagem_usuario)}”.'
            elif intencao == "memorias":
                itens = self.memorias(user_id)
                texto = visual.mensagem("O que lembro sobre você", *(
                    [f'• #{item["id"]} · {item["texto"]}' + (" (concluído)" if item["concluido"] else "")
                     for item in itens] or ["Ainda não guardei informações sobre sua rotina."]),
                    rodape="Você pode pedir para esquecer uma informação ou usar /esquecer ID.")
            elif intencao == "memoria_esquecer":
                texto = self.esquecer(user_id, dados.get("id") if isinstance(dados, dict) else None)
            else:
                texto = self.configurar(user_id, dados)
        except ValueError as erro:
            texto = str(erro)
        except Exception as erro:
            logger.warning("Falha no contexto pessoal: %s", type(erro).__name__)
            texto = "Não consegui atualizar seu contexto pessoal agora. Tente novamente em instantes."
        self.enviar_texto(user_id, texto)
        return True

    def comando(self, user_id, comando, args):
        if comando not in COMANDOS_ASSISTENTE:
            return False
        if comando == "memorias":
            return self.executar_intencao(user_id, {"intencao": "memorias"})
        if comando == "esquecer" and len(args) == 1 and args[0].lstrip("#").isdigit():
            return self.executar_intencao(user_id, {"intencao": "memoria_esquecer", "dados": {"id": int(args[0].lstrip("#"))}})
        if comando == "proatividade":
            if len(args) == 1 and args[0].lower() in {"on", "off"}:
                return self.executar_intencao(user_id, {"intencao": "proatividade_config", "dados": {"ativo": args[0].lower() == "on"}})
            if not args:
                self.conhecer_usuario(user_id)
                with self.session_factory() as session:
                    perfil = session.get(models.PerfilAssistente, user_id)
                    self.enviar_texto(user_id, f"Sugestões {'ativas' if perfil.proativo else 'desativadas'}: "
                        f"{perfil.inicio_contato}–{perfil.fim_contato}, até {perfil.limite_diario} por dia. "
                        "Pode ajustar os horários conversando comigo. /proatividade on ou off para ligar ou desligar.")
                return True
        self.enviar_texto(user_id, "Use /esquecer ID ou /proatividade on|off. Veja o que lembro em /memorias.")
        return True
