import unittest
from datetime import datetime
from types import SimpleNamespace

from PIL import Image

from app.services import presentation, reports
from app.services.personal_time import UTC


def registro(valor, categoria, tipo="Saida", dia=1, metodo="Pix"):
    return SimpleNamespace(valor=valor, categoria=categoria, tipo=tipo, metodo_pagamento=metodo,
                           data=datetime(2026, 10, dia, 12, tzinfo=UTC), descricao=categoria)


def exemplo():
    transacoes = [registro(6500, "Salario", "Entrada"), registro(850, "Extra", "Entrada", 14),
                  registro(1800, "Casa", dia=5), registro(420, "Alimentacao", dia=4, metodo="Credito"),
                  registro(280, "Alimentacao", dia=10), registro(180, "Transporte", dia=12),
                  registro(220, "Lazer", dia=15, metodo="Credito"), registro(129.90, "Servicos", dia=18),
                  registro(340, "Contas", dia=20), registro(75, "Outros", dia=21)]
    metas = [SimpleNamespace(categoria="Alimentacao", valor_limite=600),
             SimpleNamespace(categoria="Lazer", valor_limite=500),
             SimpleNamespace(categoria="Transporte", valor_limite=300)]
    return transacoes, metas


class ApresentacaoTests(unittest.TestCase):
    def test_moeda_rotulos_e_confirmacao_de_transacao(self):
        dados = {"descricao": "Supermercado", "valor": 1234.56, "categoria": "Alimentacao",
                 "metodo_pagamento": "Credito", "tipo": "Saida"}
        texto = presentation.transacao(dados)
        self.assertIn("R$ 1.234,56", texto)
        self.assertIn("Alimentação · Crédito", texto)
        self.assertIn("\n\n", texto)
        self.assertNotIn("📝", texto)

    def test_meta_no_limite_e_acima_tem_situacoes_corretas(self):
        self.assertIn("atingido", presentation.aviso_meta("Casa", 500, 500))
        self.assertIn("excedido em R$ 100,00", presentation.aviso_meta("Casa", 600, 500))
        self.assertIn("Restam R$ 100,00", presentation.aviso_meta("Casa", 400, 500))

    def test_listas_tem_total_id_e_proxima_data(self):
        assinatura = SimpleNamespace(id=3, descricao="Netflix", valor=39.90, proxima_data=datetime(2026, 11, 5))
        texto = presentation.lista_assinaturas([assinatura])
        self.assertIn("Netflix · #3", texto)
        self.assertIn("R$ 39,90/mês", texto)
        self.assertIn("05/11/2026", texto)
        self.assertIn("Total mensal", texto)
        self.assertIn("/cancelarassinatura ID", texto)


class RelatorioTests(unittest.TestCase):
    def test_totais_e_categorias_do_relatorio(self):
        transacoes, metas = exemplo()
        dados = reports.dados_resumo(transacoes)
        self.assertAlmostEqual(dados["recebido"], 7350)
        self.assertAlmostEqual(dados["gasto"], 3444.90)
        self.assertAlmostEqual(dados["resultado"], 3905.10)
        self.assertEqual(list(dados["categorias"])[0], "Casa")
        self.assertAlmostEqual(dados["categorias"]["Alimentação"], 700)
        texto = reports.texto_resumo(transacoes, "Outubro/2026")
        self.assertIn("R$ 3.905,10", texto)
        self.assertIn("47%", texto)
        self.assertIn("52% das saídas", texto)
        self.assertIn("não representa o saldo", texto)

    def test_sem_entradas_nao_inventa_porcentagem_da_renda(self):
        texto = reports.texto_resumo([registro(50, "Casa")], "Outubro/2026")
        self.assertIn("Nenhuma entrada registrada", texto)
        self.assertNotIn("das entradas registradas", texto)
        self.assertNotRegex(texto.lower(), r"\bnan\b")

    def test_serie_acumula_dias_em_ordem_e_respeita_fuso(self):
        transacoes = [registro(50, "Casa", dia=2), registro(1000, "Salario", "Entrada")]
        dias, entradas, saidas = reports.serie_acumulada(transacoes, "America/Sao_Paulo", datetime(2026, 10, 3, 12, tzinfo=UTC))
        self.assertEqual(dias, [1, 2, 3])
        self.assertEqual(entradas, [1000, 1000, 1000])
        self.assertEqual(saidas, [0, 50, 50])
        transacoes[0].data = datetime(2026, 10, 2, 1, tzinfo=UTC)
        self.assertEqual(reports.serie_acumulada(transacoes, "America/Sao_Paulo")[2][0], 50)

    def test_agrupamento_preserva_total(self):
        valores = {f"Categoria {indice}": indice + 1 for indice in range(10)}
        agrupados = reports._agrupar(valores, 6)
        self.assertEqual(len(agrupados), 6)
        self.assertEqual(sum(valor for categoria, valor in agrupados), sum(valores.values()))

    def test_eixo_monetario_grande_sem_notacao_cientifica(self):
        self.assertEqual(reports._moeda_eixo(1500), "R$ 1,5 mil")
        self.assertEqual(reports._moeda_eixo(1_200_000_000), "R$ 1,2 bi")

    def test_imagem_valida_com_metas_e_sem_entradas(self):
        for transacoes, metas in (exemplo(), ([registro(50, "Casa")], []), ([registro(100, "Extra", "Entrada")], [])):
            with self.subTest(quantidade=len(transacoes)):
                buffer = reports.gerar_dashboard(transacoes, metas, "Outubro/2026", agora=datetime(2026, 10, 21, 12, tzinfo=UTC))
                with Image.open(buffer) as imagem:
                    self.assertEqual(imagem.format, "PNG")
                    self.assertGreater(imagem.height, imagem.width)
                    self.assertLess(imagem.width + imagem.height, 10000)
        self.assertIsNone(reports.gerar_dashboard([]))


if __name__ == "__main__":
    unittest.main()
