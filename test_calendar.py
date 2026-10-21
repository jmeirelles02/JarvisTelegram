import os
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from caldav.lib.error import NotFoundError
from icalendar import Calendar, Event

from app.services.calendar_service import CalendarioGoogle, CalendarioICloud, CalendarioIndisponivel
from app.services.personal_time import UTC


class ICloudTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"ICLOUD_USERNAME": "user@example.com", "ICLOUD_APP_PASSWORD": "app-secret",
                                      "ICLOUD_CALENDAR_NAME": "Pessoal"})
        env.start()
        self.addCleanup(env.stop)
        self.cliente = Mock()
        self.cliente.__enter__ = Mock(return_value=self.cliente)
        self.cliente.__exit__ = Mock(return_value=False)
        self.agenda = Mock()
        self.agenda.name = "Pessoal"
        self.cliente.principal.return_value.calendars.return_value = [self.agenda]
        self.provider = CalendarioICloud()
        client_patch = patch("caldav.DAVClient", return_value=self.cliente)
        self.davclient = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.inicio = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def test_criar_ical_com_uid_estavel_alarme_e_sem_senha(self):
        self.agenda.get_event_by_uid.side_effect = NotFoundError()
        self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 60)
        arquivo = self.agenda.save_event.call_args.args[0]
        componente = Calendar.from_ical(arquivo).walk("VEVENT")[0]
        self.assertEqual(str(componente["UID"]), "unique-id")
        self.assertEqual(componente.decoded("DTSTART"), self.inicio)
        self.assertEqual(componente.walk("VALARM")[0].decoded("TRIGGER"), -timedelta(hours=1))
        self.assertNotIn(b"app-secret", arquivo)
        self.assertTrue(self.agenda.save_event.call_args.kwargs["no_overwrite"])
        self.assertEqual(self.davclient.call_args.kwargs["url"], "https://caldav.icloud.com/")

    def test_retry_criacao_nao_duplica_evento_existente(self):
        self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        self.agenda.save_event.assert_not_called()

    def test_agenda_tem_dia_inteiro_e_respeita_fuso_e_cancelamentos(self):
        arquivo = Calendar()
        for titulo, status in (("Feriado", "CONFIRMED"), ("Cancelado", "CANCELLED")):
            evento = Event()
            evento.add("uid", titulo)
            evento.add("summary", titulo)
            evento.add("dtstart", date(2026, 10, 21))
            evento.add("dtend", date(2026, 10, 22))
            evento.add("status", status)
            arquivo.add_component(evento)
        remoto = Mock(icalendar_instance=arquivo)
        self.agenda.search.return_value = [remoto]
        eventos = self.provider.listar(self.inicio, self.inicio + timedelta(hours=10), "America/Sao_Paulo")
        self.assertEqual(len(eventos), 1)
        self.assertTrue(eventos[0].dia_inteiro)
        self.assertEqual(eventos[0].inicio, datetime(2026, 10, 21, 3, tzinfo=UTC))
        self.assertTrue(self.agenda.search.call_args.kwargs["expand"])

    def test_selecao_exige_nome_unico(self):
        self.cliente.principal.return_value.calendars.return_value = [self.agenda, self.agenda]
        with self.assertRaises(CalendarioIndisponivel):
            self.provider.listar(self.inicio, self.inicio + timedelta(hours=1), "UTC")


class GoogleTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret",
                                      "GOOGLE_REFRESH_TOKEN": "refresh", "GOOGLE_CALENDAR_ID": "me@example.com"})
        env.start()
        self.addCleanup(env.stop)
        self.provider = CalendarioGoogle()
        token_patch = patch("app.services.calendar_service.requests.post")
        self.token_post = token_patch.start()
        self.addCleanup(token_patch.stop)
        self.token_post.return_value.json.return_value = {"access_token": "token"}
        request_patch = patch("app.services.calendar_service.requests.request")
        self.request = request_patch.start()
        self.addCleanup(request_patch.stop)
        self.inicio = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def test_refresh_token_e_paginacao(self):
        primeira, segunda = Mock(), Mock()
        primeira.json.return_value = {"nextPageToken": "next", "items": [{"id": "one", "summary": "Dia inteiro",
            "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}}]}
        segunda.json.return_value = {"items": [{"id": "two", "summary": "Dentista",
            "start": {"dateTime": "2026-10-21T15:00:00-03:00"}, "end": {"dateTime": "2026-10-21T16:00:00-03:00"}}]}
        self.request.side_effect = [primeira, segunda]
        eventos = self.provider.listar(self.inicio, self.inicio + timedelta(days=1), "America/Sao_Paulo")
        self.assertEqual(len(eventos), 2)
        self.assertEqual(eventos[1].inicio.hour, 18)
        self.assertEqual(self.request.call_args.kwargs["params"]["pageToken"], "next")
        self.assertEqual(self.token_post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(self.request.call_args.kwargs["headers"]["Authorization"], "Bearer token")
        self.assertIn("me%40example.com", self.request.call_args.args[1])

    def test_criacao_tem_id_estavel_para_retentativas(self):
        self.request.return_value.status_code = 409
        primeiro = self.provider.criar("uid", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        segundo = self.provider.criar("uid", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        self.assertEqual(primeiro, segundo)
        self.assertEqual(self.request.call_args.kwargs["json"]["id"], primeiro)

    def test_falha_de_autenticacao_nao_consulta_calendario(self):
        self.token_post.return_value.raise_for_status.side_effect = RuntimeError("unauthorized")
        with self.assertRaises(RuntimeError):
            self.provider.listar(self.inicio, self.inicio + timedelta(days=1), "UTC")
        self.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
