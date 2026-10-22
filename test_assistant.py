import json
import os
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app import models
    from app.services.assistant import AssistenteContextual
    from app.services.conversation import Conversa
    from app.services.personal_time import agora_utc


class ContextoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.enviar = Mock()
        self.assistente = AssistenteContextual(self.sessions, self.enviar)
        env = patch.dict(os.environ, {"CALENDAR_PROVIDER": "", "JARVIS_TIMEZONE": "America/Sao_Paulo"})
        env.start()
        self.addCleanup(env.stop)

    def guardar(self, texto="Trabalho das 9h às 18h", user_id=10, **extras):
        return self.assistente.guardar(user_id, {"chave": "trabalho", "tipo": "rotina", "trecho": texto, **extras}, texto)

    def test_memoria_exige_trecho_real_da_mensagem_atual(self):
        with self.assertRaises(ValueError):
            self.assistente.guardar(10, {"chave": "trabalho", "tipo": "rotina", "trecho": "Trabalho de madrugada"}, "Oi!")
        self.assertEqual(self.assistente.memorias(10), [])

    def test_memoria_sobrevive_a_reinicio_e_isola_pessoas(self):
        self.guardar()
        reiniciado = AssistenteContextual(self.sessions, self.enviar)
        self.assertEqual(reiniciado.memorias(10)[0]["texto"], "Trabalho das 9h às 18h")
        self.assertEqual(reiniciado.memorias(11), [])

    def test_correcao_atualiza_mesma_memoria_e_cancela_sugestao_antiga(self):
        self.guardar()
        with self.sessions.begin() as session:
            session.add(models.EntregaPessoal(chave="antigo", user_id=10, tipo="proativo", estado="pendente",
                                              texto="Ideia", tentar_em=agora_utc()))
        self.guardar("Agora trabalho à noite")
        with self.sessions() as session:
            self.assertEqual(session.query(models.MemoriaPessoal).count(), 1)
            self.assertEqual(session.query(models.EntregaPessoal).one().estado, "cancelado")
        self.assertEqual(self.assistente.memorias(10)[0]["texto"], "Agora trabalho à noite")

    def test_esquecer_limpa_contexto_e_nao_apaga_memoria_alheia(self):
        self.guardar()
        self.guardar("Trabalho aos sábados", user_id=11)
        conversa = Conversa(self.sessions)
        conversa.registrar(10, "user", "Trabalho das 9h às 18h")
        conversa.registrar(11, "user", "Outra pessoa")
        identificador = self.assistente.memorias(10)[0]["id"]
        with self.assertRaises(ValueError):
            self.assistente.esquecer(11, identificador)
        self.assistente.esquecer(10, identificador)
        self.assertEqual(self.assistente.memorias(10), [])
        self.assertEqual(conversa.historico(10), [])
        self.assertEqual(len(self.assistente.memorias(11)), 1)
        self.assertEqual(len(conversa.historico(11)), 1)

    def test_preferencias_e_lembretes_entram_no_contexto(self):
        self.guardar()
        self.assistente.conhecer_usuario(10)
        with self.sessions.begin() as session:
            session.add(models.Lembrete(user_id=10, descricao="Internet", ativo=True, fuso="UTC", dia_original=1))
            session.add(models.Lembrete(user_id=11, descricao="Particular", ativo=True, fuso="UTC", dia_original=1))
        contexto = json.loads(self.assistente.contexto(10))
        self.assertEqual(contexto["memorias"][0]["texto"], "Trabalho das 9h às 18h")
        self.assertEqual([item["descricao"] for item in contexto["lembretes"]], ["Internet"])

    def test_configuracao_natural_e_comando_off_persistem(self):
        self.assistente.executar_intencao(10, {"intencao": "proatividade_config", "dados": {
            "inicio": "10:00", "fim": "19:00", "limite_diario": 1}})
        self.assistente.comando(10, "proatividade", ["off"])
        with self.sessions() as session:
            perfil = session.get(models.PerfilAssistente, 10)
            self.assertFalse(perfil.proativo)
            self.assertEqual((perfil.inicio_contato, perfil.fim_contato, perfil.limite_diario), ("10:00", "19:00", 1))

    def test_configuracao_invalida_nao_muda_preferencias(self):
        for dados in ({"ativo": "false"}, {"inicio": "25:00"}, {"inicio": "08:00", "fim": "08:00"},
                      {"limite_diario": True}, {"limite_diario": 0}):
            with self.subTest(dados=dados), self.assertRaises(ValueError):
                self.assistente.configurar(10, dados)
        with self.sessions() as session:
            perfil = session.get(models.PerfilAssistente, 10)
            self.assertTrue(perfil.proativo)
            self.assertEqual(perfil.inicio_contato, "08:00")

    def test_objetivo_pode_ser_marcado_como_concluido_sem_ser_esquecido(self):
        self.guardar("Quero terminar meu curso", chave="curso", tipo="objetivo")
        self.guardar("Terminei meu curso", chave="curso", tipo="objetivo", concluido=True)
        memoria = self.assistente.memorias(10)[0]
        self.assertTrue(memoria["concluido"])
        self.assertEqual(memoria["texto"], "Terminei meu curso")


if __name__ == "__main__":
    unittest.main()
