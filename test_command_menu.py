import os
import unittest
from unittest.mock import patch

from app.services.command_menu import CATALOGO_COMANDOS, menu_comandos

with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    import run_bot
    from app.services.personal import COMANDOS_PESSOAIS


class MenuComandosTests(unittest.TestCase):
    def test_menu_cobre_exatamente_os_comandos_disponiveis(self):
        nomes = [nome for comandos in CATALOGO_COMANDOS.values() for nome, argumentos, descricao in comandos]
        self.assertEqual(len(nomes), len(set(nomes)))
        self.assertEqual(set(nomes), set(run_bot.COMANDOS) | COMANDOS_PESSOAIS)

    def test_barra_e_aliases_exibem_menu_sem_modelo_ou_acao(self):
        for texto in ("/", "/menu", "/comandos", "/MENU"):
            with self.subTest(texto=texto), patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                    patch.object(run_bot, "processar_entrada") as processar:
                run_bot.tratar_mensagem(10, texto)
                enviar.assert_called_once_with(10, menu_comandos())
                processar.assert_not_called()

    def test_prefixo_filtra_sem_executar_acoes(self):
        with patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot, "processar_entrada") as processar, \
                patch.object(run_bot, "cancelar_assinatura") as cancelar:
            run_bot.tratar_mensagem(10, "/ca")
        resposta = enviar.call_args.args[1]
        self.assertIn("/calendarios", resposta)
        self.assertIn("/cancelarassinatura ID", resposta)
        self.assertIn("/cancelarparcela ID", resposta)
        self.assertIn("/cancelarlembrete ID", resposta)
        self.assertNotIn("/resumo", resposta)
        cancelar.assert_not_called()
        processar.assert_not_called()

    def test_comando_desconhecido_oferece_menu(self):
        with patch.object(run_bot.whatsapp, "enviar_texto") as enviar, \
                patch.object(run_bot, "processar_entrada") as processar:
            run_bot.tratar_mensagem(10, "/xyz")
        self.assertIn("Envie / ou /menu", enviar.call_args.args[1])
        processar.assert_not_called()

    def test_comando_exato_mantem_argumentos(self):
        with patch.object(run_bot.assistente_pessoal, "comando", return_value=True) as pessoal:
            run_bot.tratar_mensagem(10, "/adiar 3 10")
        pessoal.assert_called_once_with(10, "adiar", ["3", "10"])

    def test_atalhos_financeiros_e_ajuda(self):
        with patch.object(run_bot, "processar_entrada") as processar:
            run_bot.tratar_mensagem(10, "/resumo")
            run_bot.tratar_mensagem(10, "/exportar")
        self.assertEqual(processar.call_args_list[0].args, (10, "resumo"))
        self.assertEqual(processar.call_args_list[1].args, (10, "exportar"))
        with patch.object(run_bot.whatsapp, "enviar_texto") as enviar:
            run_bot.tratar_mensagem(10, "/ajuda")
        enviar.assert_called_once_with(10, run_bot.TEXTO_AJUDA)


if __name__ == "__main__":
    unittest.main()
