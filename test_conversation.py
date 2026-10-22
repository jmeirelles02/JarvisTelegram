"""Memória e continuidade do fluxo real, com SQLite e IA/transporte simulados."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app import models
    from app.services.conversation import Conversa, MAX_MENSAGENS, MAX_TEXTO, MAX_CONTEXTO
    from app.services.personal import AssistentePessoal
    from app.services.assistant import AssistenteContextual
    from app.services.personal_time import agora_utc
    import run_bot


class ConversaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.conversa = Conversa(self.sessions)
        self.contexto = AssistenteContextual(self.sessions, run_bot.enviar_texto)
        contexto_patch = patch.object(run_bot, "contexto_pessoal", self.contexto)
        contexto_patch.start()
        self.addCleanup(contexto_patch.stop)

    def test_persiste_apos_reiniciar_e_isola_usuarios(self):
        self.conversa.registrar(10, "user", "Me lembra de pagar internet amanhã")
        self.conversa.registrar(10, "assistant", "A que horas?")
        self.conversa.registrar(11, "user", "Segredo de outro usuário")
        historico = Conversa(self.sessions).historico(10)
        self.assertEqual(historico, [
            {"role": "user", "content": "Me lembra de pagar internet amanhã"},
            {"role": "assistant", "content": "A que horas?"},
        ])

    def test_limita_retencao_tamanho_e_contexto(self):
        antigo = agora_utc() - timedelta(days=2)
        with self.sessions.begin() as session:
            session.add(models.MensagemConversa(user_id=11, papel="user", texto="antiga", criada_em=antigo))
        self.assertEqual(self.conversa.historico(11), [])
        for i in range(MAX_MENSAGENS + 3):
            self.conversa.registrar(10, "user", f"{i}:" + "x" * (MAX_TEXTO + 10))
        with self.sessions() as session:
            self.assertEqual(session.query(models.MensagemConversa).filter_by(user_id=11).count(), 0)
            self.assertEqual(session.query(models.MensagemConversa).filter_by(user_id=10).count(), MAX_MENSAGENS)
        historico = self.conversa.historico(10)
        self.assertTrue(all(len(item["content"]) <= MAX_TEXTO for item in historico))
        self.assertLessEqual(sum(len(item["content"]) for item in historico), MAX_CONTEXTO)
        self.assertTrue(historico[-1]["content"].startswith(f"{MAX_MENSAGENS + 2}:"))

    def test_completa_lembrete_em_duas_mensagens_com_resposta_real_no_contexto(self):
        respostas = [
            {"intencao": "esclarecer", "resposta": "A que horas devo te lembrar?"},
            {"intencao": "lembrete_criar", "dados": {"descricao": "Pagar internet",
                "data": "2099-10-23T09:00:00", "repeticao": "nenhuma"}},
            {"intencao": "conversa", "resposta": "Por nada!"},
        ]
        post = Mock()
        post.return_value.json.side_effect = [
            {"choices": [{"message": {"content": json.dumps(item)}}]} for item in respostas
        ]
        assistente = AssistentePessoal(self.sessions, run_bot.enviar_texto)
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot, "assistente_pessoal", assistente), \
                patch.object(run_bot, "contexto_financeiro", return_value="Sem transações"), \
                patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot.brain.requests, "post", post), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "teste", "OPENROUTER_MODEL": "teste"}):
            run_bot.tratar_mensagem(10, "Me lembra de pagar internet dia 23 de outubro de 2099")
            run_bot.tratar_mensagem(10, "às 9h")
            run_bot.tratar_mensagem(10, "obrigado")
        segunda = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(segunda[1]["content"], "Me lembra de pagar internet dia 23 de outubro de 2099")
        self.assertEqual(segunda[2]["content"], "A que horas devo te lembrar?")
        self.assertEqual(segunda[3]["content"][0]["text"], "às 9h")
        terceira = post.call_args_list[2].kwargs["json"]["messages"]
        self.assertIn("Lembrete criado", terceira[-2]["content"])
        self.assertEqual(enviar.call_count, 3)
        with self.sessions() as session:
            self.assertEqual(session.query(models.Lembrete).count(), 1)
            lembrete = session.query(models.Lembrete).one()
            self.assertEqual(lembrete.descricao, "Pagar internet")
            self.assertEqual(lembrete.user_id, 10)

    def test_envio_falho_nao_e_memorizado_como_resposta_entregue(self):
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot.whatsapp, "enviar_texto", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                run_bot.enviar_texto(10, "Resposta não entregue")
        self.assertEqual(self.conversa.historico(10), [])

    def test_comando_resumo_com_historico_nao_depende_da_ia(self):
        self.conversa.registrar(10, "user", "Oi")
        assistente = AssistentePessoal(self.sessions, run_bot.enviar_texto)
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot, "assistente_pessoal", assistente), \
                patch.object(run_bot, "buscar_transacoes_mes_atual", return_value=[]), \
                patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot.brain, "interpretar_mensagem") as interpretar:
            run_bot.tratar_mensagem(10, "/resumo")
        interpretar.assert_not_called()
        self.assertIn("Ainda não há transações", enviar.call_args.args[1])

    def test_pode_criar_apos_previa_confirma_sem_modelo(self):
        calendario = Mock()
        calendario.listar.return_value = []
        assistente = AssistentePessoal(self.sessions, run_bot.enviar_texto, Mock(return_value=calendario))
        inicio = agora_utc() + timedelta(days=1)
        previa = assistente.preparar_evento(10, {"titulo": "Dentista", "inicio": inicio.isoformat(),
                                               "fim": (inicio + timedelta(hours=1)).isoformat()})
        self.conversa.registrar(10, "assistant", previa)
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot, "assistente_pessoal", assistente), \
                patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot.brain, "interpretar_mensagem") as interpretar:
            run_bot.tratar_mensagem(10, "Pode criar!")
        interpretar.assert_not_called()
        calendario.criar.assert_called_once()
        self.assertIn("Compromisso criado", enviar.call_args.args[1])

    def test_mudanca_de_agenda_recebe_o_pedido_original_e_historico(self):
        from app.services.calendar_service import Compromisso
        calendario = Mock()
        inicio = agora_utc() + timedelta(days=1)
        calendario.listar.side_effect = [[Compromisso("p", "Psicólogo", inicio, inicio + timedelta(hours=1))], []]
        assistente = AssistentePessoal(self.sessions, run_bot.enviar_texto, Mock(return_value=calendario))
        self.conversa.registrar(10, "user", "Estamos falando da consulta com o psicólogo")
        pedido = "Consegui antecipar em meia hora"
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot, "assistente_pessoal", assistente), \
                patch.object(run_bot, "contexto_financeiro", return_value=""), \
                patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot.brain, "interpretar_mensagem", return_value={"intencao": "agenda_gerenciar", "dados": {}}), \
                patch.object(run_bot.brain, "planejar_agenda", return_value={"estado": "executar", "ref": "0",
                    "escopo": "ocorrencia", "acao": "mover", "deslocamento_minutos": -30}) as planejar:
            run_bot.tratar_mensagem(10, pedido)
        self.assertEqual(planejar.call_args.args[0], pedido)
        self.assertIn("psicólogo", planejar.call_args.args[2][0]["content"])
        self.assertEqual(calendario.alterar.call_args.args[2], inicio - timedelta(minutes=30))
        self.assertIn("Compromisso remarcado", enviar.call_args.args[1])

    def test_falha_da_memoria_nao_bloqueia_o_envio(self):
        memoria = Conversa(Mock(side_effect=RuntimeError("offline")))
        with patch.object(run_bot, "conversa", memoria), \
                patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                self.assertLogs("app.services.conversation", level="WARNING"):
            run_bot.enviar_texto(10, "Oi!")
            self.assertEqual(memoria.historico(10), [])
        enviar.assert_called_once_with(10, "Oi!")

    def test_aprende_rotina_durante_conversa_e_a_usa_no_proximo_pedido(self):
        resultado = {"intencao": "conversa", "resposta": "Vou considerar sua rotina ao planejar o dia.", "memorias": [
            {"chave": "trabalho", "tipo": "rotina", "trecho": "Trabalho das 9h às 18h"}]}
        assistente = AssistentePessoal(self.sessions, run_bot.enviar_texto)
        with patch.object(run_bot, "conversa", self.conversa), \
                patch.object(run_bot, "assistente_pessoal", assistente), \
                patch.object(run_bot, "contexto_financeiro", return_value="Sem transações"), \
                patch.object(run_bot.whatsapp, "enviar_texto"), \
                patch.object(run_bot.brain, "interpretar_mensagem", return_value=resultado) as interpretar:
            run_bot.tratar_mensagem(10, "Trabalho das 9h às 18h")
            interpretar.return_value = {"intencao": "conversa", "resposta": "Podemos organizar o fim da tarde."}
            run_bot.tratar_mensagem(10, "Como posso organizar meu dia?")
        self.assertIn("Trabalho das 9h às 18h", interpretar.call_args.kwargs["contexto_pessoal"])
        self.assertEqual(self.contexto.memorias(10)[0]["chave"], "trabalho")


if __name__ == "__main__":
    unittest.main()
