import os
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from caldav.lib.error import AuthorizationError, ReportError
from caldav.lib.url import URL
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
        self.agenda.url = URL("https://caldav.icloud.com/calendars/pessoal/")
        self.cliente.principal.return_value.calendars.return_value = [self.agenda]
        self.cliente.request.return_value = Mock(status=404)
        self.cliente.put.return_value = Mock(status=201)
        self.provider = CalendarioICloud()
        client_patch = patch("caldav.DAVClient", return_value=self.cliente)
        self.davclient = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.inicio = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def test_criar_ical_com_uid_estavel_alarme_e_sem_senha(self):
        self.agenda.get_event_by_uid.side_effect = ReportError(reason="400 Bad Request")
        self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 60)
        arquivo = self.cliente.put.call_args.args[1].encode("utf-8")
        componente = Calendar.from_ical(arquivo).walk("VEVENT")[0]
        self.assertEqual(str(componente["UID"]), "unique-id")
        self.assertEqual(componente.decoded("DTSTART"), self.inicio)
        self.assertEqual(componente.walk("VALARM")[0].decoded("TRIGGER"), -timedelta(hours=1))
        self.assertNotIn(b"app-secret", arquivo)
        self.assertEqual(self.cliente.put.call_args.kwargs["headers"]["If-None-Match"], "*")
        self.assertTrue(self.cliente.put.call_args.args[0].endswith("/unique-id.ics"))
        self.agenda.get_event_by_uid.assert_not_called()
        self.agenda.save_event.assert_not_called()
        self.assertEqual(self.davclient.call_args.kwargs["url"], "https://caldav.icloud.com/")

    def test_retry_criacao_nao_duplica_evento_existente(self):
        arquivo = self._arquivo("unique-id")
        self.cliente.request.return_value = Mock(status=200, raw=arquivo)
        self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        self.cliente.put.assert_not_called()

    def _arquivo(self, uid):
        arquivo = Calendar()
        evento = Event()
        evento.add("uid", uid)
        evento.add("dtstart", self.inicio)
        evento.add("dtend", self.inicio + timedelta(hours=1))
        arquivo.add_component(evento)
        return arquivo.to_ical()

    def test_corrida_de_criacao_verifica_uid_apos_412(self):
        self.cliente.request.side_effect = [Mock(status=404), Mock(status=200, raw=self._arquivo("unique-id"))]
        self.cliente.put.return_value.status = 412
        resultado = self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        self.assertEqual(resultado, "unique-id")
        self.assertEqual(self.cliente.request.call_count, 2)

    def test_recurso_com_outro_uid_nao_e_sobrescrito(self):
        self.cliente.request.return_value = Mock(status=200, raw=self._arquivo("outro-uid"))
        with self.assertRaisesRegex(CalendarioIndisponivel, "não corresponde"):
            self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)
        self.cliente.put.assert_not_called()

    def test_put_sem_permissao_nao_confirma_sucesso(self):
        self.cliente.put.return_value.status = 403
        with self.assertRaisesRegex(CalendarioIndisponivel, "permissão de edição"):
            self.provider.criar("unique-id", "Dentista", self.inicio, self.inicio + timedelta(hours=1), 30)

    def test_autenticacao_tem_orientacao_especifica_sem_expor_segredos(self):
        self.cliente.principal.side_effect = AuthorizationError(reason="app-secret")
        with self.assertRaises(CalendarioIndisponivel) as erro:
            self.provider.listar_calendarios()
        self.assertIn("senha específica de app", str(erro.exception))
        self.assertNotIn("app-secret", str(erro.exception))

    def test_report_error_nao_acusa_senha_invalida_e_nao_expoe_conteudo(self):
        self.agenda.search.side_effect = ReportError(reason="400 Bad Request app-secret", body="ICS privado")
        self.cliente.request.return_value.status = 400
        with self.assertLogs("app.services.calendar_service", level="WARNING") as logs:
            with self.assertRaises(CalendarioIndisponivel) as erro:
                self.provider.listar(self.inicio, self.inicio + timedelta(hours=1), "UTC")
        self.assertIn("REPORT HTTP 400", str(erro.exception))
        self.assertNotIn("app-secret", str(erro.exception))
        self.assertNotIn("ICS privado", " ".join(logs.output))
        self.assertNotIn("app-secret", " ".join(logs.output))

    def test_report_minimo_recupera_consulta_rejeitada_pela_biblioteca(self):
        from xml.etree import ElementTree as ET
        self.agenda.search.side_effect = ReportError(reason="400 Bad Request")
        root = ET.Element("{DAV:}multistatus")
        response = ET.SubElement(root, "{DAV:}response")
        ET.SubElement(response, "{DAV:}href").text = "/calendars/pessoal/evento.ics"
        propstat = ET.SubElement(response, "{DAV:}propstat")
        ET.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
        prop = ET.SubElement(propstat, "{DAV:}prop")
        ET.SubElement(prop, "{urn:ietf:params:xml:ns:caldav}calendar-data").text = self._arquivo("evento").decode()
        self.cliente.request.return_value = Mock(status=207, raw=ET.tostring(root))
        eventos = self.provider.listar(self.inicio, self.inicio + timedelta(days=1), "UTC")
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0].uid, "evento")
        self.assertTrue(eventos[0].recurso.endswith("/evento.ics"))
        self.assertEqual(self.cliente.request.call_args.args[1], "REPORT")
        pedido = self.cliente.request.call_args.args[2]
        self.assertIn("time-range", pedido)
        self.assertNotIn("text-match", pedido)
        self.assertNotIn("expand", pedido)

    def test_diagnostico_identifica_uid_rejeitado_sem_gravar_eventos(self):
        self.agenda.search.return_value = []
        self.agenda.get_event_by_uid.side_effect = ReportError(reason="400 Bad Request app-secret")
        resultado = self.provider.diagnosticar()
        self.assertIn("por período funcionaram", resultado)
        self.assertIn("UID falhou com ReportError HTTP 400", resultado)
        self.assertNotIn("app-secret", resultado)
        self.cliente.put.assert_not_called()

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
        self.assertFalse(self.agenda.search.call_args.kwargs["expand"])

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
