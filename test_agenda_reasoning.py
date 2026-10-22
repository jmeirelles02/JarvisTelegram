"""Execução de planos com dados reais de agenda simulados; nenhum serviço externo."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app import models
    from app.services.personal import AssistentePessoal
    from app.services.calendar_service import Compromisso
    from app.services.personal_time import UTC


class PlanejamentoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        env = patch.dict(os.environ, {"JARVIS_TIMEZONE": "America/Sao_Paulo", "CALENDAR_PROVIDER": "icloud"})
        env.start()
        self.addCleanup(env.stop)
        self.agora = datetime(2026, 10, 21, 12, tzinfo=UTC)  # quarta às 9h
        self.psicologo = Compromisso("psicologo", "Psicólogo", datetime(2026, 10, 23, 13, tzinfo=UTC),
                                    datetime(2026, 10, 23, 14, tzinfo=UTC), recorrente=True)
        self.dentista = Compromisso("dentista", "Dentista", datetime(2026, 10, 24, 13, tzinfo=UTC),
                                   datetime(2026, 10, 24, 14, tzinfo=UTC))
        self.calendario = Mock()
        self.calendario.listar.side_effect = [[self.psicologo, self.dentista], []]
        self.assistente = AssistentePessoal(self.sessions, Mock(), Mock(return_value=self.calendario))
        planejamento = patch("app.services.personal.brain.planejar_agenda")
        self.planejar = planejamento.start()
        self.addCleanup(planejamento.stop)
        self.planejar.return_value = {"estado": "executar", "ref": "0", "escopo": "ocorrencia", "acao": "mover",
                                     "novo_inicio": "2026-10-22T10:00:00"}

    def executar(self, pedido="Troquei meu psicólogo de dia essa semana, para quinta às 10:00", dados=None, historico=None):
        return self.assistente.gerenciar_agenda(10, dados or {}, pedido, historico, self.agora)

    def test_entende_mudanca_apos_consultar_eventos_e_preserva_horario_original(self):
        resultado = self.executar()
        args = self.calendario.alterar.call_args.args
        self.assertIs(args[0], self.psicologo)
        self.assertEqual(args[1], "mover")
        self.assertEqual(args[2], datetime(2026, 10, 22, 13, tzinfo=UTC))
        self.assertIn("Compromisso remarcado", resultado)
        self.assertIn("23/10/2026 às 10:00", resultado)
        self.assertIn("22/10/2026 às 10:00", resultado)
        self.calendario.criar.assert_not_called()
        self.assertEqual(self.planejar.call_args.args[1]["eventos"][0]["titulo"], "Psicólogo")
        self.assertEqual(self.calendario.listar.call_args_list[0].args[0].day, 19)  # início da semana

    def test_adiar_e_antecipar_usam_deslocamento_da_ocorrencia_encontrada(self):
        for minutos, pedido in ((60, "ficou uma hora mais tarde"), (-30, "consegui antecipar em meia hora")):
            with self.subTest(minutos=minutos):
                self.calendario.reset_mock()
                self.calendario.listar.side_effect = [[self.psicologo], []]
                self.planejar.return_value = {"estado": "executar", "ref": "0", "escopo": "ocorrencia",
                                              "acao": "mover", "deslocamento_minutos": minutos}
                self.executar(pedido)
                self.assertEqual(self.calendario.alterar.call_args.args[2], self.psicologo.inicio + timedelta(minutes=minutos))

    def test_ambiguidade_pergunta_sem_mudar_qualquer_evento(self):
        self.planejar.return_value = {"estado": "esclarecer", "resposta": "Você quer mudar o psicólogo ou o dentista?"}
        resultado = self.executar("Precisei mudar minha consulta")
        self.assertIn("psicólogo ou o dentista", resultado)
        self.calendario.alterar.assert_not_called()

    def test_resposta_curta_recebe_contexto_da_pergunta_anterior(self):
        historico = [{"role": "user", "content": "Minha consulta ficou para quinta às 10h"},
                     {"role": "assistant", "content": "Você quer mudar o psicólogo ou o dentista?"}]
        self.executar("O psicólogo", historico=historico)
        self.assertEqual(self.planejar.call_args.args[2], historico)

    def test_nao_aplica_mudanca_de_serie_como_uma_ocorrencia(self):
        self.planejar.return_value["escopo"] = "serie"
        with self.assertRaisesRegex(ValueError, "ocorrência específica"):
            self.executar("O horário fixo do psicólogo mudou")
        self.calendario.alterar.assert_not_called()

    def test_referencia_inventada_e_recusada(self):
        self.planejar.return_value["ref"] = "99"
        with self.assertRaises(ValueError):
            self.executar()
        self.calendario.alterar.assert_not_called()

    def test_horario_no_passado_e_recusado(self):
        self.planejar.return_value["novo_inicio"] = "2026-10-20T10:00:00"
        with self.assertRaisesRegex(ValueError, "passado"):
            self.executar()
        self.calendario.alterar.assert_not_called()

    def test_sem_eventos_pede_periodo_mais_preciso(self):
        self.calendario.listar.side_effect = [[]]
        self.assertIn("Em qual dia", self.executar())
        self.planejar.assert_not_called()

    def test_conflito_exige_que_a_pessoa_decida_o_que_fazer(self):
        self.calendario.listar.side_effect = [[self.psicologo], [self.dentista]]
        self.assertIn("Quer manter", self.executar())
        self.calendario.alterar.assert_not_called()

    def test_confirmacao_de_conflito_permite_executar_o_plano(self):
        self.calendario.listar.side_effect = [[self.psicologo], [self.dentista]]
        self.planejar.return_value["permitir_conflito"] = True
        self.executar("Sim, pode manter mesmo com o conflito")
        self.calendario.alterar.assert_called_once()

    def test_cancelamento_nao_cria_um_compromisso_substituto(self):
        self.planejar.return_value = {"estado": "executar", "ref": "0", "escopo": "ocorrencia", "acao": "cancelar"}
        self.assertIn("Compromisso cancelado", self.executar("Não vou ao psicólogo essa semana"))
        self.assertIsNone(self.calendario.alterar.call_args.args[2])
        self.calendario.criar.assert_not_called()

    def test_falha_de_planejamento_nao_executa_alteracao(self):
        self.planejar.return_value = {"estado": "erro"}
        self.assertIn("Nenhum compromisso foi modificado", self.executar())
        self.calendario.alterar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
