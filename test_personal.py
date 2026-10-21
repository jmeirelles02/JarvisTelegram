import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app import models
    from app.services.personal import AssistentePessoal, proxima_ocorrencia
    from app.services.personal_time import UTC, ler_data
    from app.services.calendar_service import Compromisso, obter_calendario, CalendarioIndisponivel


class AssistenteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CALENDAR_PROVIDER": "", "CALENDAR_USER_ID": "",
                                          "JARVIS_TIMEZONE": "America/Sao_Paulo"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.enviar = Mock()
        self.calendario = Mock()
        self.calendario.listar.return_value = []
        self.factory = Mock(return_value=self.calendario)
        self.assistente = AssistentePessoal(self.sessions, self.enviar, self.factory)
        self.agora = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)

    def criar(self, data="2026-10-21T09:00:00", repeticao="nenhuma", user_id=10):
        return self.assistente.criar_lembrete(user_id, {"descricao": "Pagar internet", "data": data,
                                                       "repeticao": repeticao}, self.agora)

    def test_lembrete_persistido_enviado_uma_vez_apos_reiniciar(self):
        self.criar()
        reiniciado = AssistentePessoal(self.sessions, self.enviar, self.factory)
        reiniciado.ciclo(self.agora + timedelta(hours=1))
        reiniciado.ciclo(self.agora + timedelta(hours=2))
        self.enviar.assert_called_once()
        self.assertIn("Pagar internet", self.enviar.call_args.args[1])
        with self.sessions() as session:
            self.assertIsNone(session.get(models.Lembrete, 1).proxima_data)
            self.assertEqual(session.query(models.EntregaPessoal).one().estado, "enviado")

    def test_falha_de_envio_persiste_e_reenvia(self):
        self.criar()
        self.enviar.side_effect = RuntimeError("offline")
        self.assistente.ciclo(self.agora + timedelta(hours=1))
        with self.sessions() as session:
            entrega = session.query(models.EntregaPessoal).one()
            self.assertEqual(entrega.estado, "pendente")
            self.assertEqual(entrega.tentativas, 1)
        self.enviar.side_effect = None
        self.assistente.ciclo(self.agora + timedelta(hours=1, seconds=30))
        self.assertEqual(self.enviar.call_count, 1)
        self.assistente.ciclo(self.agora + timedelta(hours=1, minutes=1))
        self.assertEqual(self.enviar.call_count, 2)

    def test_reinicio_nao_dispara_todas_as_ocorrencias_atrasadas(self):
        self.criar(repeticao="diaria")
        self.assistente.ciclo(self.agora + timedelta(days=40))
        self.enviar.assert_called_once()
        with self.sessions() as session:
            self.assertGreater(ler_data(session.get(models.Lembrete, 1).proxima_data.isoformat(), "UTC"),
                               self.agora + timedelta(days=40))

    def test_mensal_preserva_dia_31_e_horario_local(self):
        data = ler_data("2026-01-31T09:00:00", "America/Sao_Paulo")
        fevereiro = proxima_ocorrencia(data, "mensal", "America/Sao_Paulo", 31, data)
        marco = proxima_ocorrencia(fevereiro, "mensal", "America/Sao_Paulo", 31, fevereiro)
        self.assertEqual(fevereiro, datetime(2026, 2, 28, 12, tzinfo=UTC))
        self.assertEqual(marco, datetime(2026, 3, 31, 12, tzinfo=UTC))

    def test_dias_uteis_e_horario_de_verao(self):
        sexta = ler_data("2026-10-23T09:00:00", "America/Sao_Paulo")
        segunda = proxima_ocorrencia(sexta, "dias_uteis", "America/Sao_Paulo", 23, sexta)
        self.assertEqual(segunda, datetime(2026, 10, 26, 12, tzinfo=UTC))
        sabado = ler_data("2026-03-07T09:00:00", "America/New_York")
        domingo = proxima_ocorrencia(sabado, "diaria", "America/New_York", 7, sabado)
        self.assertEqual(domingo.hour, 13)

    def test_dados_invalidos_nao_criam_lembretes(self):
        for data in ("2026-10-21", "amanhã", "2026-10-20T09:00:00", None):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.assistente.criar_lembrete(10, {"descricao": "Teste", "data": data}, self.agora)
        with self.sessions() as session:
            self.assertEqual(session.query(models.Lembrete).count(), 0)

    def test_usuario_nao_acessa_lembrete_alheio(self):
        self.criar()
        with self.assertRaises(ValueError):
            self.assistente.alterar_lembrete(11, 1, "cancelar")
        self.assertNotIn("Pagar internet", self.assistente.listar_lembretes(11))

    def test_adiar_cancela_entrega_pendente(self):
        self.criar()
        agora = self.agora + timedelta(hours=1)
        self.assistente.agendar_lembretes(agora)
        self.assistente.alterar_lembrete(10, 1, "adiar", 10, agora)
        self.assistente.enviar_pendentes(agora)
        self.enviar.assert_not_called()
        self.assistente.ciclo(agora + timedelta(minutes=10))
        self.enviar.assert_called_once()
        self.assistente.alterar_lembrete(10, 1, "concluir")
        with self.sessions() as session:
            self.assertFalse(session.get(models.Lembrete, 1).ativo)

    def test_resumo_no_fuso_sem_duplicar_e_sem_enviar_antes(self):
        self.criar()
        self.assistente.configurar_resumo(10, "08:00")
        self.assistente.ciclo(self.agora - timedelta(minutes=1))
        self.enviar.assert_not_called()
        self.assistente.ciclo(self.agora)
        self.assistente.ciclo(self.agora + timedelta(seconds=1))
        self.enviar.assert_called_once()
        self.assertIn("Seu dia", self.enviar.call_args.args[1])

    def test_resumo_vazio_fica_silencioso_e_off_cancela_retentativa(self):
        self.assistente.configurar_resumo(10, "08:00")
        self.assistente.ciclo(self.agora)
        self.enviar.assert_not_called()
        self.criar(user_id=11)
        self.assistente.configurar_resumo(11, "08:00")
        self.enviar.side_effect = RuntimeError("offline")
        self.assistente.ciclo(self.agora)
        self.assistente.configurar_resumo(11, None)
        self.enviar.reset_mock(side_effect=True)
        self.assistente.ciclo(self.agora + timedelta(minutes=5))
        self.enviar.assert_not_called()

    def test_resumo_ontem_expira_sem_envio(self):
        self.criar()
        self.assistente.configurar_resumo(10, "08:00")
        self.assistente.agendar_resumos(self.agora)
        self.assistente.enviar_pendentes(self.agora + timedelta(days=1))
        self.enviar.assert_not_called()

    def test_resumo_financeiro_isolado_por_usuario(self):
        with self.sessions.begin() as session:
            session.add_all([
                models.Transacao(user_id=10, valor=50, tipo="Saida", categoria="Casa", data=self.agora),
                models.Transacao(user_id=11, valor=999, tipo="Saida", categoria="Casa", data=self.agora),
                models.Meta(user_id=10, categoria="Casa", valor_limite=200),
            ])
        texto, relevante = self.assistente.resumo(10, self.agora)
        self.assertTrue(relevante)
        self.assertIn("50.00", texto)
        self.assertIn("150.00", texto)
        self.assertNotIn("999", texto)

    def test_evento_exige_confirmacao_informa_conflitos_e_nao_duplica(self):
        self.calendario.listar.return_value = [Compromisso("busy", "Reunião", self.agora + timedelta(hours=1),
                                                          self.agora + timedelta(hours=2))]
        mensagem = self.assistente.preparar_evento(10, {"titulo": "Dentista", "inicio": "2026-10-21T09:00:00",
                                                       "fim": "2026-10-21T10:00:00"}, self.agora)
        self.assertIn("Conflitos", mensagem)
        self.calendario.criar.assert_not_called()
        self.assistente.confirmar_evento(10, 1, agora=self.agora)
        self.assistente.confirmar_evento(10, 1, agora=self.agora)
        self.calendario.criar.assert_called_once()

    def test_evento_expirado_alheio_ou_descartado_nao_e_criado(self):
        self.assistente.preparar_evento(10, {"titulo": "Dentista", "inicio": "2026-10-21T09:00:00",
                                            "fim": "2026-10-21T10:00:00"}, self.agora)
        with self.assertRaises(ValueError):
            self.assistente.confirmar_evento(11, 1, agora=self.agora)
        with self.assertRaises(ValueError):
            self.assistente.confirmar_evento(10, 1, agora=self.agora + timedelta(minutes=16))
        self.assistente.confirmar_evento(10, 1, descartar=True, agora=self.agora)
        with self.assertRaises(ValueError):
            self.assistente.confirmar_evento(10, 1, agora=self.agora)
        self.calendario.criar.assert_not_called()

    def test_calendario_acessivel_somente_ao_dono(self):
        with patch.dict(os.environ, {"CALENDAR_PROVIDER": "icloud", "CALENDAR_USER_ID": "10"}):
            with self.assertRaises(CalendarioIndisponivel):
                obter_calendario(11)
            self.assertEqual(type(obter_calendario(10)).__name__, "CalendarioICloud")

    def test_aviso_calendario_uma_vez_e_erro_nao_bloqueia_lembrete(self):
        self.criar()
        with patch.dict(os.environ, {"CALENDAR_PROVIDER": "icloud", "CALENDAR_USER_ID": "10"}):
            self.calendario.listar.return_value = [Compromisso("event", "Dentista", self.agora + timedelta(minutes=30),
                                                              self.agora + timedelta(hours=1))]
            self.assistente.ciclo(self.agora)
            self.assistente.ciclo(self.agora)
            self.enviar.assert_called_once()
            self.calendario.listar.side_effect = RuntimeError("iCloud offline")
            self.assistente.ciclo(self.agora + timedelta(hours=1))
            self.assertEqual(self.enviar.call_count, 2)

    def test_comandos_invalidos_e_contexto_temporal(self):
        self.assertTrue(self.assistente.comando(10, "adiar", []))
        self.assertIn("Uso:", self.enviar.call_args.args[1])
        self.assertTrue(self.assistente.comando(10, "resumodiario", ["25:00"]))
        self.assertIn("23:59", self.enviar.call_args.args[1])
        self.assertIn("America/Sao_Paulo", self.assistente.contexto(10))
        self.assertFalse(self.assistente.executar_intencao(10, {"intencao": "resumo"}))

    def test_evento_removido_cancela_aviso_pendente(self):
        with patch.dict(os.environ, {"CALENDAR_PROVIDER": "icloud", "CALENDAR_USER_ID": "10"}):
            self.calendario.listar.return_value = [Compromisso("event", "Dentista", self.agora + timedelta(minutes=30),
                                                              self.agora + timedelta(hours=1))]
            self.enviar.side_effect = RuntimeError("offline")
            self.assistente.ciclo(self.agora)
            self.calendario.listar.return_value = []
            self.enviar.reset_mock(side_effect=True)
            self.assistente.ciclo(self.agora + timedelta(minutes=1))
            self.enviar.assert_not_called()
            with self.sessions() as session:
                self.assertEqual(session.query(models.EntregaPessoal).one().estado, "cancelado")

    def test_aviso_calendario_aguarda_sincronizacao_antes_de_repetir(self):
        with patch.dict(os.environ, {"CALENDAR_PROVIDER": "icloud", "CALENDAR_USER_ID": "10"}):
            self.calendario.listar.return_value = [Compromisso("event", "Dentista", self.agora + timedelta(minutes=30),
                                                              self.agora + timedelta(hours=1))]
            self.enviar.side_effect = RuntimeError("offline")
            self.assistente.ciclo(self.agora)
            self.enviar.reset_mock(side_effect=True)
            self.calendario.listar.side_effect = RuntimeError("offline")
            self.assistente.ciclo(self.agora + timedelta(minutes=1))
            self.enviar.assert_not_called()

    def test_fluxo_do_bot_agenda_lembrete_sem_registrar_gasto(self):
        import run_bot

        resultado = {"intencao": "lembrete_criar", "dados": {
            "descricao": "Pagar 50 reais de internet", "data": "2026-10-22T09:00:00",
        }}
        with patch.object(run_bot, "assistente_pessoal", self.assistente), \
                patch.object(run_bot, "contexto_financeiro", return_value="Sem transações"), \
                patch.object(run_bot.brain, "interpretar_mensagem", return_value=resultado) as interpretar, \
                patch.object(run_bot, "salvar_transacao") as salvar, \
                patch("app.services.personal.agora_utc", return_value=self.agora):
            run_bot.processar_entrada(10, "Me lembra de pagar 50 reais de internet amanhã às 9h")
        salvar.assert_not_called()
        self.assertIn("America/Sao_Paulo", interpretar.call_args.kwargs["contexto_temporal"])
        with self.sessions() as session:
            self.assertEqual(session.query(models.Lembrete).count(), 1)

    def test_comando_hoje_bypassa_modelo(self):
        import run_bot

        with patch.object(run_bot, "assistente_pessoal", self.assistente), \
                patch.object(run_bot.brain, "interpretar_mensagem") as interpretar:
            run_bot.tratar_mensagem(10, "/hoje")
        interpretar.assert_not_called()
        self.assertIn("Seu dia", self.enviar.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
