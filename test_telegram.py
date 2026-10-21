import io
import os
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from app.services import telegram, transport
from app.services.command_menu import CATALOGO_COMANDOS


def evento(texto=None, extra=None):
    mensagem = {"chat": {"type": "private", "id": 123}, "from": {"id": 123, "is_bot": False}}
    if texto:
        mensagem["text"] = texto
    mensagem.update(extra or {})
    return {"update_id": 100, "message": mensagem}


class TelegramTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"JARVIS_CHANNEL": "telegram", "TELEGRAM_TOKEN": "test-token",
                                      "TELEGRAM_USER_IDS": ""})
        env.start()
        self.addCleanup(env.stop)
        post = patch.object(telegram.requests, "post")
        self.post = post.start()
        self.addCleanup(post.stop)
        self.post.return_value.ok = True
        self.post.return_value.json.return_value = {"ok": True, "result": True}

    def test_telegram_e_padrao_e_nao_chama_evolution(self):
        with patch.dict(os.environ, clear=True), patch.object(telegram, "enviar_texto") as enviar, \
                patch.object(transport.whatsapp, "_post") as evolution:
            self.assertEqual(transport.canal(), "telegram")
            transport.enviar_texto(123, "Teste")
        enviar.assert_called_once_with(123, "Teste")
        evolution.assert_not_called()

    def test_texto_usa_token_existente_e_divide_mensagem_longa(self):
        telegram.enviar_texto(123, "x" * 4500)
        self.assertEqual(self.post.call_count, 2)
        self.assertIn("bottest-token/sendMessage", self.post.call_args.args[0])
        self.assertEqual(self.post.call_args.kwargs["json"], {"chat_id": 123, "text": "x" * 500})

    def test_envio_de_documento(self):
        arquivo = io.BytesIO(b"excel")
        arquivo.read()
        telegram.enviar_midia(123, arquivo, "application/test", "extrato.xlsx", "document", "Seu extrato")
        self.assertTrue(self.post.call_args.args[0].endswith("sendDocument"))
        self.assertEqual(self.post.call_args.kwargs["data"]["caption"], "Seu extrato")
        self.assertEqual(arquivo.tell(), 0)
        self.assertEqual(self.post.call_args.kwargs["files"]["document"][0], "extrato.xlsx")

    def test_download_midia(self):
        self.post.return_value.json.return_value = {"ok": True, "result": {"file_path": "voice/file.ogg"}}
        with patch.object(telegram.requests, "get") as get:
            get.return_value.ok = True
            get.return_value.content = b"audio"
            self.assertEqual(telegram.baixar_midia("file-id"), b"audio")
        self.assertTrue(get.call_args.args[0].endswith("/voice/file.ogg"))

    def test_erros_nao_expoem_token_na_excecao(self):
        self.post.side_effect = requests.ConnectionError("https://api.telegram.org/bottest-token/sendMessage")
        with self.assertRaises(telegram.ErroTelegram) as erro:
            telegram.enviar_texto(123, "Teste")
        self.assertNotIn("test-token", str(erro.exception))
        self.assertTrue(erro.exception.__suppress_context__)

    def test_token_ausente_nao_chama_api(self):
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": ""}), self.assertRaises(telegram.ErroTelegram):
            telegram.preparar()
        self.post.assert_not_called()

    def test_registro_de_menu_nativo_com_todos_os_comandos(self):
        respostas = [{"username": "JarvisBot"}, {"url": ""}, True, True, True]
        with patch.object(telegram, "_chamar", side_effect=respostas) as chamar:
            self.assertEqual(telegram.preparar(), "JarvisBot")
        nome, dados = chamar.call_args_list[2].args
        self.assertEqual(nome, "setMyCommands")
        esperado = {nome for grupo in CATALOGO_COMANDOS.values() for nome, argumentos, descricao in grupo}
        self.assertEqual({item["command"] for item in dados["commands"]}, esperado)
        self.assertEqual(chamar.call_args_list[3].args[1]["language_code"], "pt")
        self.assertEqual(chamar.call_args_list[4].args, ("setChatMenuButton", {"menu_button": {"type": "commands"}}))

    def test_webhook_existente_exige_remocao_explicita(self):
        with patch.object(telegram, "_chamar", side_effect=[{"username": "JarvisBot"}, {"url": "https://example.com"}]) as chamar:
            with self.assertRaises(telegram.ErroTelegram):
                telegram.preparar()
        self.assertFalse(any(item.args[0] == "deleteWebhook" for item in chamar.call_args_list))

    def test_parsing_texto_foto_audio_e_comando_com_nome(self):
        self.assertEqual(telegram.extrair_mensagem(evento(" /hoje ")), (123, "/hoje", None, None))
        self.assertEqual(telegram.extrair_mensagem(evento("/adiar@JarvisBot 3 10"), "JarvisBot")[1], "/adiar 3 10")
        self.assertIsNone(telegram.extrair_mensagem(evento("/hoje@OutroBot"), "JarvisBot"))
        foto = evento(extra={"photo": [{"file_id": "small"}, {"file_id": "large"}], "caption": "Nota fiscal"})
        self.assertEqual(telegram.extrair_mensagem(foto), (123, "Nota fiscal", "large", "image/jpeg"))
        audio = evento(extra={"voice": {"file_id": "voice", "mime_type": "audio/ogg"}})
        self.assertEqual(telegram.extrair_mensagem(audio)[2:], ("voice", "audio/ogg"))

    def test_grupos_bots_e_usuarios_nao_autorizados_sao_ignorados(self):
        self.assertIsNone(telegram.extrair_mensagem(evento("Teste", {"chat": {"type": "group", "id": -10}})))
        self.assertIsNone(telegram.extrair_mensagem(evento("Teste", {"from": {"id": 123, "is_bot": True}})))
        with patch.dict(os.environ, {"TELEGRAM_USER_IDS": "456"}):
            self.assertIsNone(telegram.extrair_mensagem(evento("Teste")))

    def test_polling_despacha_e_avanca_offset(self):
        parada = threading.Event()
        atender = Mock()

        def chamar(metodo, dados, timeout):
            if dados["offset"] is None:
                return [evento("/hoje")]
            self.assertEqual(dados["offset"], 101)
            parada.set()
            return []

        with patch.object(telegram, "_chamar", side_effect=chamar):
            telegram.vigiar(atender, "JarvisBot", parada)
        atender.assert_called_once_with(123, "/hoje", None, None)


if __name__ == "__main__":
    unittest.main()
