"""Decisões e entrega da iniciativa com relógio fixo, sem rede ou mensagens reais."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app import models
    from app.services.assistant import AssistenteContextual
    from app.services.calendar_service import Compromisso
    from app.services.conversation import Conversa
    from app.services.personal import AssistentePessoal
    from app.services.personal_time import UTC
    from app.services.proactivity import IniciativaPessoal


class IniciativaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.enviar = Mock()
        self.calendario = Mock()
        self.calendario.listar.return_value = []
        self.factory = Mock(return_value=self.calendario)
        self.contexto = AssistenteContextual(self.sessions, self.enviar, self.factory)
        self.conversa = Conversa(self.sessions)
        self.avaliar = Mock(side_effect=lambda contexto, historico: {
            "avisar": True, "sinal_id": contexto["sinais"][0]["id"],
            "sugestao": "Quer pensar no próximo passo comigo?",
        })
        self.iniciativa = IniciativaPessoal(self.sessions, self.contexto, self.conversa, self.enviar, self.avaliar)
        self.agora = datetime(2026, 10, 22, 12, tzinfo=UTC)  # 09:00 em São Paulo
        env = patch.dict(os.environ, {"CALENDAR_PROVIDER": "", "CALENDAR_USER_ID": "", "JARVIS_TIMEZONE": "America/Sao_Paulo"})
        env.start()
        self.addCleanup(env.stop)
        self.contexto.conhecer_usuario(10)

    def meta(self, categoria="Casa", user_id=10, gasto=85):
        with self.sessions.begin() as session:
            session.add(models.Meta(user_id=user_id, categoria=categoria, valor_limite=100))
            session.add(models.Transacao(user_id=user_id, descricao="Compra", valor=gasto, tipo="Saida",
                                        categoria=categoria, data=self.agora - timedelta(hours=1)))

    def entregas(self):
        with self.sessions() as session:
            return [(item.chave, item.estado) for item in session.query(models.EntregaPessoal).filter_by(tipo="proativo").all()]

    def test_sem_sinais_nao_gasta_modelo_nem_envia_mensagem(self):
        self.iniciativa.ciclo(self.agora)
        self.avaliar.assert_not_called()
        self.enviar.assert_not_called()

    def test_modelo_pode_decidir_ficar_em_silencio(self):
        self.meta()
        self.avaliar.side_effect = None
        self.avaliar.return_value = {"avisar": False}
        self.iniciativa.ciclo(self.agora)
        self.avaliar.assert_called_once()
        self.enviar.assert_not_called()

    def test_alerta_baseado_em_fatos_sem_repetir_apos_reinicio(self):
        self.meta()
        self.iniciativa.ciclo(self.agora)
        self.assertIn("R$ 85,00", self.enviar.call_args.args[1])
        self.assertEqual(self.enviar.call_args.args[1].count("R$ 85,00"), 1)
        self.assertEqual(self.entregas()[0][1], "enviado")
        reiniciado = IniciativaPessoal(self.sessions, self.contexto, self.conversa, self.enviar, self.avaliar)
        reiniciado.ciclo(self.agora + timedelta(hours=5))
        self.enviar.assert_called_once()
        self.avaliar.assert_called_once()

    def test_nao_usa_fatos_de_outro_usuario(self):
        self.meta(user_id=11)
        self.iniciativa.ciclo(self.agora)
        self.avaliar.assert_not_called()
        self.enviar.assert_not_called()

    def test_horario_silencioso_e_off_nao_chamam_modelo(self):
        self.meta()
        self.iniciativa.ciclo(self.agora.replace(hour=3))  # meia-noite local
        self.contexto.configurar(10, {"ativo": False})
        self.iniciativa.ciclo(self.agora)
        self.avaliar.assert_not_called()
        self.enviar.assert_not_called()

    def test_espaca_contatos_e_respeita_limite_diario(self):
        for categoria in ("Casa", "Lazer", "Contas"):
            self.meta(categoria)
        self.iniciativa.ciclo(self.agora)
        self.iniciativa.ciclo(self.agora + timedelta(hours=1))
        self.assertEqual(self.enviar.call_count, 1)
        self.iniciativa.ciclo(self.agora + timedelta(hours=4, minutes=1))
        self.assertEqual(self.enviar.call_count, 2)
        self.iniciativa.ciclo(self.agora + timedelta(hours=8, minutes=2))
        self.assertEqual(self.enviar.call_count, 2)
        self.assertEqual(self.avaliar.call_count, 2)

    def test_analise_tem_intervalo_mesmo_quando_modelo_nao_avisa(self):
        self.meta()
        self.avaliar.side_effect = None
        self.avaliar.return_value = {"avisar": False}
        self.iniciativa.ciclo(self.agora)
        self.iniciativa.ciclo(self.agora + timedelta(minutes=1))
        self.avaliar.assert_called_once()

    def test_evitar_interromper_conversa_recente(self):
        self.meta()
        with self.sessions.begin() as session:
            session.add(models.MensagemConversa(user_id=10, papel="user", texto="Vamos conversar", criada_em=self.agora))
        self.iniciativa.ciclo(self.agora + timedelta(minutes=1))
        self.avaliar.assert_not_called()

    def test_fato_inventado_pelo_modelo_e_recusado(self):
        self.meta()
        self.avaliar.side_effect = None
        self.avaliar.return_value = {"avisar": True, "sinal_id": "inventado", "sugestao": "Faça algo"}
        self.iniciativa.ciclo(self.agora)
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas(), [])

    def test_mudanca_de_contexto_durante_analise_descarta_sugestao(self):
        self.meta()

        def avaliar(contexto, historico):
            self.contexto.guardar(10, {"chave": "trabalho", "tipo": "rotina", "trecho": "Agora trabalho à noite"},
                                  "Agora trabalho à noite")
            return {"avisar": True, "sinal_id": contexto["sinais"][0]["id"], "sugestao": "Vamos organizar sua manhã?"}

        self.avaliar.side_effect = avaliar
        self.iniciativa.ciclo(self.agora)
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas(), [])

    def test_falha_no_envio_reagenda_e_revalida_fato_antes_de_tentar(self):
        self.meta()
        self.enviar.side_effect = RuntimeError("offline")
        with self.assertLogs("app.services.proactivity", level="WARNING"):
            self.iniciativa.ciclo(self.agora)
        self.assertEqual(self.entregas()[0][1], "pendente")
        self.enviar.reset_mock(side_effect=True)
        # O gasto foi corrigido antes da nova tentativa; a sugestão perdeu a razão de existir.
        with self.sessions.begin() as session:
            session.query(models.Transacao).filter_by(user_id=10).update({"valor": 10})
        self.iniciativa.enviar_pendentes(self.agora + timedelta(minutes=5))
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "cancelado")

    def test_sugestao_pendente_cancelada_quando_usuario_desliga(self):
        self.meta()
        self.iniciativa.analisar(10, self.agora)
        self.contexto.configurar(10, {"ativo": False})
        self.iniciativa.enviar_pendentes(self.agora)
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "cancelado")

    def test_retentativa_atualiza_valores_sem_repetir_fato_antigo(self):
        self.meta()
        self.iniciativa.analisar(10, self.agora)
        with self.sessions.begin() as session:
            session.query(models.Transacao).filter_by(user_id=10).update({"valor": 90})
        self.iniciativa.enviar_pendentes(self.agora + timedelta(minutes=1))
        texto = self.enviar.call_args.args[1]
        self.assertIn("R$ 90,00", texto)
        self.assertNotIn("R$ 85,00", texto)
        self.assertEqual(texto.count("Quer pensar no próximo passo comigo?"), 1)

    def test_entrega_expirada_nao_e_enviada(self):
        self.meta()
        self.iniciativa.analisar(10, self.agora)
        self.iniciativa.enviar_pendentes(self.agora + timedelta(days=1))
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "expirado")

    def test_reconfere_horario_depois_de_analise_demorada(self):
        self.meta()
        # A análise começou em horário permitido e terminou após o fim da janela.
        with patch("app.services.proactivity.agora_utc", side_effect=[self.agora, self.agora + timedelta(hours=13)]):
            self.iniciativa.ciclo()
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "expirado")

    def test_outro_worker_nao_pode_enviar_sugestao_ignorando_limites(self):
        self.meta()
        self.iniciativa.analisar(10, self.agora)
        AssistentePessoal(self.sessions, self.enviar).enviar_pendentes(self.agora)
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "pendente")

    def test_conflito_usa_agenda_real_e_nao_e_entregue_durante_falha_de_sincronizacao(self):
        self.calendario.listar.return_value = [
            Compromisso("a", "Dentista", self.agora + timedelta(hours=1), self.agora + timedelta(hours=2)),
            Compromisso("b", "Reunião", self.agora + timedelta(hours=1, minutes=30), self.agora + timedelta(hours=3)),
        ]
        with patch.dict(os.environ, {"CALENDAR_PROVIDER": "icloud", "CALENDAR_USER_ID": "10"}):
            self.iniciativa.analisar(10, self.agora)
            self.calendario.listar.side_effect = RuntimeError("offline")
            with self.assertLogs("app.services.proactivity", level="WARNING"):
                self.iniciativa.enviar_pendentes(self.agora)
            self.enviar.assert_not_called()
            self.calendario.listar.side_effect = None
            self.calendario.listar.return_value = []  # o conflito foi resolvido
            self.iniciativa.enviar_pendentes(self.agora + timedelta(minutes=1))
        self.enviar.assert_not_called()
        self.assertEqual(self.entregas()[0][1], "cancelado")

    def test_objetivo_so_e_acompanhado_depois_de_dias_e_para_apos_conclusao(self):
        with self.sessions.begin() as session:
            session.add(models.MemoriaPessoal(user_id=10, chave="curso", tipo="objetivo", texto="Quero terminar meu curso",
                                              atualizada_em=self.agora - timedelta(days=4)))
        sinais, _ = self.iniciativa.coletar_sinais(10, self.agora)
        self.assertEqual(sinais[0]["tipo"], "objetivo")
        self.contexto.guardar(10, {"chave": "curso", "tipo": "objetivo", "trecho": "Terminei meu curso",
                                  "concluido": True}, "Terminei meu curso")
        sinais, _ = self.iniciativa.coletar_sinais(10, self.agora + timedelta(days=10))
        self.assertEqual(sinais, [])

    def test_cobranca_e_previsao_e_nao_afirma_saldo_bancario(self):
        with self.sessions.begin() as session:
            session.add(models.Assinatura(user_id=10, descricao="Academia", valor=150, proxima_data=self.agora + timedelta(hours=6)))
        sinais, _ = self.iniciativa.coletar_sinais(10, self.agora)
        self.assertIn("previsão dos seus registros", sinais[0]["fato"])
        self.iniciativa.ciclo(self.agora)
        self.assertIn("Academia", self.enviar.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
