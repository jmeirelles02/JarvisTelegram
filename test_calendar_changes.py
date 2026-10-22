"""Verifica a expansão real de séries antes/depois de alterações, sem rede."""
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from icalendar import Calendar, Event, Alarm
from caldav.lib.url import URL

from app.services.calendar_changes import alterar_ocorrencia, ocorrencias, instante
from app.services.calendar_service import CalendarioICloud, CalendarioIndisponivel, Compromisso
from app.services.personal_time import UTC, validar_fuso


FUSO = "America/Sao_Paulo"


def serie():
    arquivo = Calendar()
    arquivo.add("version", "2.0")
    evento = Event()
    evento.add("uid", "psicologo")
    evento.add("summary", "Psicólogo")
    evento.add("dtstart", datetime(2026, 10, 16, 10, tzinfo=validar_fuso(FUSO)))
    evento.add("dtend", datetime(2026, 10, 16, 11, tzinfo=validar_fuso(FUSO)))
    evento.add("rrule", {"freq": "weekly", "count": 6})
    alarme = Alarm()
    alarme.add("action", "DISPLAY")
    alarme.add("trigger", -timedelta(minutes=30))
    evento.add_component(alarme)
    arquivo.add_component(evento)
    return arquivo


def alvo(inicio=datetime(2026, 10, 23, 13, tzinfo=UTC)):
    return Compromisso("psicologo", "Psicólogo", inicio, inicio + timedelta(hours=1),
        recurso="https://caldav.icloud.com/calendars/pessoal/psicologo.ics")


def horarios(arquivo):
    eventos = ocorrencias(arquivo, datetime(2026, 10, 10, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC), FUSO)
    return sorted(instante(item.decoded("DTSTART"), FUSO) for item in eventos
                  if str(item.get("STATUS", "")).upper() != "CANCELLED")


class ExcecoesTests(unittest.TestCase):
    def test_move_sexta_para_quinta_sem_alterar_demais_semanas(self):
        original = serie()
        antes = horarios(original)
        destino = datetime(2026, 10, 22, 13, tzinfo=UTC)
        alterado, mudou = alterar_ocorrencia(original, alvo(), "mover", destino, "op1", FUSO)
        depois = horarios(alterado)
        self.assertTrue(mudou)
        self.assertEqual(depois, sorted([valor if valor != alvo().inicio else destino for valor in antes]))
        master, excecao = alterado.walk("VEVENT")
        self.assertEqual(master.to_ical(), original.walk("VEVENT")[0].to_ical())
        self.assertEqual(excecao.decoded("RECURRENCE-ID"), alvo().inicio)
        self.assertNotIn("RANGE", excecao["RECURRENCE-ID"].params)
        self.assertEqual(excecao.decoded("DTEND") - excecao.decoded("DTSTART"), timedelta(hours=1))
        self.assertEqual(len(excecao.walk("VALARM")), 1)

    def test_adiar_e_antecipar_sao_deslocamentos_do_horario_real(self):
        for minutos in (60, -30):
            with self.subTest(minutos=minutos):
                destino = alvo().inicio + timedelta(minutes=minutos)
                alterado, _ = alterar_ocorrencia(serie(), alvo(), "mover", destino, "op", FUSO)
                self.assertIn(destino, horarios(alterado))
                self.assertNotIn(alvo().inicio, horarios(alterado))

    def test_segunda_remarcacao_preserva_recurrence_id_da_sexta_original(self):
        destino = datetime(2026, 10, 22, 13, tzinfo=UTC)
        primeiro, _ = alterar_ocorrencia(serie(), alvo(), "mover", destino, "op1", FUSO)
        segundo, _ = alterar_ocorrencia(primeiro, alvo(destino), "mover", destino + timedelta(hours=1), "op2", FUSO)
        self.assertEqual(len(segundo.walk("VEVENT")), 2)
        self.assertEqual(segundo.walk("VEVENT")[1].decoded("RECURRENCE-ID"), alvo().inicio)
        self.assertIn(destino + timedelta(hours=1), horarios(segundo))
        self.assertNotIn(destino, horarios(segundo))

    def test_cancelar_so_retira_a_ocorrencia_selecionada(self):
        original = serie()
        alterado, _ = alterar_ocorrencia(original, alvo(), "cancelar", None, "cancelar", FUSO)
        self.assertEqual(horarios(alterado), [valor for valor in horarios(original) if valor != alvo().inicio])
        self.assertEqual(alterado.walk("VEVENT")[0].to_ical(), original.walk("VEVENT")[0].to_ical())

    def test_retentativa_nao_cria_duas_excecoes(self):
        destino = alvo().inicio - timedelta(days=1)
        primeiro, _ = alterar_ocorrencia(serie(), alvo(), "mover", destino, "op1", FUSO)
        segundo, mudou = alterar_ocorrencia(primeiro, alvo(), "mover", destino, "op1", FUSO)
        self.assertFalse(mudou)
        self.assertEqual(primeiro.to_ical(), segundo.to_ical())

    def test_snapshot_que_mudou_e_recusado(self):
        modificado = serie()
        modificado.walk("VEVENT")[0]["SUMMARY"] = "Outro título"
        with self.assertRaisesRegex(ValueError, "alterado desde"):
            alterar_ocorrencia(modificado, alvo(), "mover", alvo().inicio + timedelta(hours=1), "op", FUSO)

    def test_espaco_final_normalizado_pela_biblioteca_nao_impede_alteracao(self):
        original = serie()
        original.walk("VEVENT")[0]["SUMMARY"] = "Psicólogo "
        alterado, mudou = alterar_ocorrencia(original, alvo(), "mover", alvo().inicio - timedelta(days=1), "op", FUSO)
        self.assertTrue(mudou)
        self.assertIn(alvo().inicio - timedelta(days=1), horarios(alterado))

    def test_preserva_outra_excecao_existente(self):
        original = serie()
        outro_alvo = alvo(datetime(2026, 10, 30, 13, tzinfo=UTC))
        primeira, _ = alterar_ocorrencia(original, outro_alvo, "mover", outro_alvo.inicio + timedelta(hours=2), "outro", FUSO)
        segunda, _ = alterar_ocorrencia(primeira, alvo(), "mover", alvo().inicio - timedelta(days=1), "op", FUSO)
        self.assertEqual(len(segunda.walk("VEVENT")), 3)
        self.assertIn(outro_alvo.inicio + timedelta(hours=2), horarios(segunda))

    def test_evento_avulso_nao_ganha_regra_de_recorrencia(self):
        original = serie()
        original.walk("VEVENT")[0].pop("RRULE")
        evento = alvo(datetime(2026, 10, 16, 13, tzinfo=UTC))
        alterado, _ = alterar_ocorrencia(original, evento, "mover", evento.inicio + timedelta(days=1), "op", FUSO)
        self.assertEqual(len(alterado.walk("VEVENT")), 1)
        self.assertNotIn("RECURRENCE-ID", alterado.walk("VEVENT")[0])

    def test_mudanca_de_fuso_na_serie_nao_desloca_semanas_apos_horario_de_verao(self):
        original = serie()
        master = original.walk("VEVENT")[0]
        master.pop("DTSTART"); master.pop("DTEND")
        zona = validar_fuso("America/New_York")
        master.add("dtstart", datetime(2026, 10, 16, 10, tzinfo=zona))
        master.add("dtend", datetime(2026, 10, 16, 11, tzinfo=zona))
        esperado = alvo(datetime(2026, 10, 23, 14, tzinfo=UTC))
        antes = horarios(original)
        destino = esperado.inicio - timedelta(days=1)
        alterado, _ = alterar_ocorrencia(original, esperado, "mover", destino, "op", FUSO)
        self.assertEqual(horarios(alterado), sorted([valor if valor != esperado.inicio else destino for valor in antes]))
        self.assertIn(datetime(2026, 11, 6, 15, tzinfo=UTC), horarios(alterado))


class GravacaoTests(unittest.TestCase):
    def setUp(self):
        self.cliente = Mock()
        self.cliente.__enter__ = Mock(return_value=self.cliente)
        self.cliente.__exit__ = Mock(return_value=False)
        self.agenda = Mock()
        self.agenda.url = URL("https://caldav.icloud.com/calendars/pessoal/")
        self.agenda.name = "Pessoal"
        self.cliente.principal.return_value.calendars.return_value = [self.agenda]
        self.cliente.request.return_value = Mock(status=200, raw=serie().to_ical(), headers={"ETag": '"v1"'})
        self.cliente.put.return_value = Mock(status=204)
        patcher = patch("caldav.DAVClient", return_value=self.cliente)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"ICLOUD_USERNAME": "teste", "ICLOUD_APP_PASSWORD": "senha", "ICLOUD_CALENDAR_NAME": "Pessoal"})
        env.start()
        self.addCleanup(env.stop)
        self.provider = CalendarioICloud()

    def test_uma_gravacao_condicional_no_recurso_original_sem_delete(self):
        self.provider.alterar(alvo(), "mover", alvo().inicio - timedelta(days=1), "op", FUSO)
        self.cliente.put.assert_called_once()
        self.assertEqual(self.cliente.put.call_args.args[0], alvo().recurso)
        self.assertEqual(self.cliente.put.call_args.kwargs["headers"]["If-Match"], '"v1"')
        self.assertEqual(self.cliente.request.call_args.args[1], "GET")
        self.assertEqual(len(Calendar.from_ical(self.cliente.put.call_args.args[1]).walk("VEVENT")), 2)

    def test_sem_etag_nao_grava(self):
        self.cliente.request.return_value.headers = {}
        with self.assertRaisesRegex(CalendarioIndisponivel, "versão segura"):
            self.provider.alterar(alvo(), "cancelar", None, "op", FUSO)
        self.cliente.put.assert_not_called()

    def test_concorrencia_412_nao_regrava_sem_condicao(self):
        self.cliente.put.return_value.status = 412
        with self.assertRaisesRegex(CalendarioIndisponivel, "mudou durante"):
            self.provider.alterar(alvo(), "cancelar", None, "op", FUSO)
        self.cliente.put.assert_called_once()

    def test_recurso_de_outro_calendario_e_recusado(self):
        evento = alvo()
        evento.recurso = "https://outro.exemplo/evento.ics"
        with self.assertRaises(CalendarioIndisponivel):
            self.provider.alterar(evento, "cancelar", None, "op", FUSO)
        self.cliente.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
