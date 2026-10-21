"""Testes da integração OpenRouter, sem rede, credenciais ou banco de dados."""
import base64
import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from app.services import brain


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "provider/test-model",
        })
        env.start()
        self.addCleanup(env.stop)
        post = patch.object(brain.requests, "post")
        self.post = post.start()
        self.addCleanup(post.stop)
        self.post.return_value = Mock()
        self.post.return_value.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"intencao": "conversa", "resposta": "Olá!"})}}],
        }

    def test_texto_usa_modelo_do_ambiente_e_contexto(self):
        result = brain.interpretar_mensagem("Oi Jarvis", contexto="saldo de 100")
        self.assertEqual(result["intencao"], "conversa")
        args, kwargs = self.post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["model"], "provider/test-model")
        self.assertIn("saldo de 100", kwargs["json"]["messages"][0]["content"][0]["text"])
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})

    def test_imagem_preserva_bytes_e_legenda(self):
        brain.interpretar_mensagem("nota do posto", b"imagem", "image/jpeg")
        content = self.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(content[0], {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b"imagem").decode()},
        })
        self.assertIn("nota do posto", content[1]["text"])

    def test_audio_vai_direto_ao_openrouter(self):
        for mime, formato in (("audio/ogg; codecs=opus", "ogg"), ("audio/mpeg", "mp3"), ("audio/mp4", "m4a")):
            with self.subTest(mime=mime):
                self.post.reset_mock()
                brain.interpretar_mensagem(arquivo_bytes=b"audio", mime_type=mime)
                self.post.assert_called_once()
                content = self.post.call_args.kwargs["json"]["messages"][0]["content"]
                self.assertEqual(content[0], {
                    "type": "input_audio",
                    "input_audio": {"data": base64.b64encode(b"audio").decode(), "format": formato},
                })

    def test_atalhos_funcionam_sem_configuracao(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENROUTER_MODEL": ""}):
            self.assertEqual(brain.interpretar_mensagem("resumo"), {"intencao": "resumo"})
        self.post.assert_not_called()

    def test_resumo_diario_e_lembretes_nao_caem_no_resumo_financeiro(self):
        self.assertEqual(brain.interpretar_mensagem("resumo diário"), {"intencao": "hoje"})
        self.assertEqual(brain.interpretar_mensagem("meus lembretes"), {"intencao": "lembretes"})
        brain.interpretar_mensagem("ative resumo diário às 8h", contexto_temporal="2026-10-21T08:00:00-03:00")
        self.post.assert_called_once()
        texto = self.post.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]
        self.assertIn("2026-10-21T08:00:00-03:00", texto)

    def test_lembrete_com_dinheiro_vai_para_interpretacao(self):
        brain.interpretar_mensagem("Me lembra de pagar 50 reais amanhã às 9h")
        self.post.assert_called_once()

    def test_configuracao_ausente_nao_chama_api(self):
        for variavel in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL"):
            with self.subTest(variavel=variavel), patch.dict(os.environ, {variavel: " "}):
                self.assertEqual(brain.interpretar_mensagem("Oi"), {"intencao": "erro_api"})
        self.post.assert_not_called()

    def test_falha_http_retorna_erro(self):
        self.post.return_value.raise_for_status.side_effect = requests.HTTPError("401")
        self.assertEqual(brain.interpretar_mensagem("Oi"), {"intencao": "erro_api"})

    def test_json_invalido_retorna_erro(self):
        self.post.return_value.json.return_value["choices"][0]["message"]["content"] = "invalid"
        self.assertEqual(brain.interpretar_mensagem("Oi"), {"intencao": "erro_api"})


if __name__ == "__main__":
    unittest.main()
