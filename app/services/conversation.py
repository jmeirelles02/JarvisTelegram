"""Contexto recente por usuário; guarda apenas texto, nunca bytes de mídia."""
import logging
from datetime import timedelta
from threading import RLock

from app import models
from app.services.personal_time import agora_utc


logger = logging.getLogger(__name__)
MAX_MENSAGENS = 24
MAX_TEXTO = 2000
MAX_CONTEXTO = 18000
VALIDADE = timedelta(hours=24)


class Conversa:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        # Quantidade fixa de locks: evita crescimento com novos usuários.
        self._locks = [RLock() for _ in range(64)]

    def lock(self, user_id):
        return self._locks[hash(user_id) % len(self._locks)]

    def historico(self, user_id):
        try:
            with self.session_factory() as session:
                mensagens = session.query(models.MensagemConversa).filter(
                    models.MensagemConversa.user_id == user_id,
                    models.MensagemConversa.criada_em >= agora_utc() - VALIDADE,
                ).order_by(models.MensagemConversa.id.desc()).limit(MAX_MENSAGENS).all()
                resultado = []
                tamanho = 0
                for mensagem in mensagens:
                    if tamanho + len(mensagem.texto) > MAX_CONTEXTO:
                        break
                    tamanho += len(mensagem.texto)
                    resultado.append({"role": mensagem.papel, "content": mensagem.texto})
                return list(reversed(resultado))
        except Exception as erro:
            logger.warning("Não foi possível ler o contexto da conversa: %s", type(erro).__name__)
            return []

    def registrar(self, user_id, papel, texto):
        if papel not in {"user", "assistant"} or not texto:
            return
        try:
            agora = agora_utc()
            with self.session_factory() as session, session.begin():
                session.add(models.MensagemConversa(user_id=user_id, papel=papel,
                            texto=texto[:MAX_TEXTO], criada_em=agora))
                session.flush()
                # Expiração global e limite por usuário, inclusive após reiniciar.
                session.query(models.MensagemConversa).filter(
                    models.MensagemConversa.criada_em < agora - VALIDADE,
                ).delete(synchronize_session=False)
                antigos = session.query(models.MensagemConversa.id).filter_by(user_id=user_id).order_by(
                    models.MensagemConversa.id.desc()).offset(MAX_MENSAGENS).all()
                if antigos:
                    session.query(models.MensagemConversa).filter(
                        models.MensagemConversa.id.in_([item.id for item in antigos]),
                    ).delete(synchronize_session=False)
        except Exception as erro:
            # A indisponibilidade da memória não deve impedir o atendimento.
            logger.warning("Não foi possível salvar o contexto da conversa: %s", type(erro).__name__)
