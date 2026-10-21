import unittest
from datetime import datetime, timedelta

from app.services.agenda import formatar_agenda, interpretar_consulta
from app.services.calendar_service import Compromisso
from app.services.personal_time import UTC, ler_data


class ConsultaAgendaTests(unittest.TestCase):
    def setUp(self):
        self.agora = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def test_perguntas_de_sexta_resolvidas_sem_modelo(self):
        for texto in ("Oque tenho para fazer sexta?", "O que tenho sexta-feira?", "Quais são meus compromissos de sexta?",
                      "Qual é a minha agenda para sexta-feira?", "Agenda na sexta", "minha agenda sexta"):
            with self.subTest(texto=texto):
                resultado = interpretar_consulta(texto, "America/Sao_Paulo", self.agora)
                self.assertEqual(resultado, {"intencao": "agenda", "dados": {
                    "inicio": "2026-10-23T00:00:00-03:00", "fim": "2026-10-24T00:00:00-03:00"}})

    def test_hoje_amanha_semana_e_data_explicita(self):
        for texto, comeco, termino in (("agenda", 21, 22), ("agenda de amanhã", 22, 23),
                                       ("agenda depois de amanhã", 23, 24), ("agenda desta semana", 19, 26),
                                       ("agenda da próxima semana", 26, 2), ("agenda de 23/10/2026", 23, 24)):
            with self.subTest(texto=texto):
                resultado = interpretar_consulta(texto, "America/Sao_Paulo", self.agora)
                self.assertIsNotNone(resultado)
                self.assertEqual(datetime.fromisoformat(resultado["dados"]["inicio"]).day, comeco)
                self.assertEqual(datetime.fromisoformat(resultado["dados"]["fim"]).day, termino)

    def test_mesmo_dia_e_proxima_sexta_sao_diferentes(self):
        sexta = self.agora + timedelta(days=2)
        atual = interpretar_consulta("agenda sexta", "America/Sao_Paulo", sexta)
        proxima = interpretar_consulta("agenda próxima sexta", "America/Sao_Paulo", sexta)
        self.assertIn("2026-10-23", atual["dados"]["inicio"])
        self.assertIn("2026-10-30", proxima["dados"]["inicio"])

    def test_usa_data_local_e_nao_data_utc(self):
        madrugada_utc = datetime(2026, 10, 22, 1, tzinfo=UTC)
        resultado = interpretar_consulta("agenda hoje", "America/Sao_Paulo", madrugada_utc)
        self.assertIn("2026-10-21", resultado["dados"]["inicio"])

    def test_criacoes_ambiguidades_e_periodos_complexos_ficam_para_ia(self):
        for texto in ("Marca dentista sexta", "Me lembra de pagar a internet sexta", "Gastei 50 sexta",
                      "agenda de amanhã à tarde", "agenda de 31/02", "Quais compromissos devo cancelar sexta?"):
            with self.subTest(texto=texto):
                self.assertIsNone(interpretar_consulta(texto, "America/Sao_Paulo", self.agora))


class FormatoAgendaTests(unittest.TestCase):
    def setUp(self):
        self.fuso = "America/Sao_Paulo"
        self.inicio = ler_data("2026-10-23T00:00:00", self.fuso)
        self.fim = ler_data("2026-10-24T00:00:00", self.fuso)

    def evento(self, titulo="Psicólogo", inicio="2026-10-23T10:20:00", fim="2026-10-23T11:20:00", dia_inteiro=False):
        return Compromisso(titulo, titulo, ler_data(inicio, self.fuso), ler_data(fim, self.fuso), dia_inteiro)

    def test_dia_unico_tem_titulo_horario_e_contagem_sem_intervalo_tecnico(self):
        texto = formatar_agenda([self.evento()], self.inicio, self.fim, self.fuso)
        self.assertEqual(texto, "📅 *Sexta-feira · 23/10/2026*\n\n*10:20–11:20* · Psicólogo\n\n1 compromisso")
        self.assertNotIn("00:00", texto)
        self.assertNotIn("24/10", texto)

    def test_periodo_agrupa_por_dia_ordena_e_exclui_data_final(self):
        eventos = [self.evento("Sábado", "2026-10-24T09:00:00", "2026-10-24T10:00:00"), self.evento()]
        texto = formatar_agenda(eventos, self.inicio, self.fim + timedelta(days=1), self.fuso)
        self.assertIn("Agenda · 23/10/2026 a 24/10/2026", texto)
        self.assertLess(texto.index("Sexta-feira"), texto.index("Sábado"))
        self.assertNotIn("25/10/2026", texto)
        self.assertIn("2 compromissos", texto)

    def test_evento_dia_inteiro_e_noturno(self):
        eventos = [self.evento("Feriado", "2026-10-23T00:00:00", "2026-10-24T00:00:00", True),
                   self.evento("Plantão", "2026-10-23T22:00:00", "2026-10-24T06:00:00")]
        texto = formatar_agenda(eventos, self.inicio, self.fim, self.fuso)
        self.assertIn("*Dia inteiro* · Feriado", texto)
        self.assertIn("23/10 22:00 → 24/10 06:00", texto)

    def test_vazio_e_titulo_com_quebras_de_linha(self):
        vazio = formatar_agenda([], self.inicio, self.fim, self.fuso)
        self.assertIn("Nenhum compromisso", vazio)
        self.assertNotIn("00:00", vazio)
        texto = formatar_agenda([self.evento("Reunião\ncom equipe")], self.inicio, self.fim, self.fuso)
        self.assertIn("Reunião com equipe", texto)


if __name__ == "__main__":
    unittest.main()
