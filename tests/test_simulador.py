import unittest

from tributarista.cli import main
from tributarista.simulador import Premissas, analisar_imovel, empate_saque, pmt, simular, taxa_mensal, tir_mensal


class TestMatematica(unittest.TestCase):
    def test_pmt_price(self):
        self.assertAlmostEqual(pmt(taxa_mensal(0.14), 360, 240_000), 2688, delta=1)

    def test_tir(self):
        self.assertAlmostEqual(tir_mensal([-100, 110]), 0.10, places=6)


class TestSimulador(unittest.TestCase):
    def test_custos_goiania(self):
        p = Premissas()
        self.assertEqual(p.custos_compra["itbi"], 6_000)
        self.assertEqual(p.caixa_para_comprar, 134_000)

    def test_imovel_base(self):
        r = analisar_imovel(Premissas())
        self.assertAlmostEqual(r["primeira_parcela"], 2688, delta=1)
        self.assertAlmostEqual(r["tir_aa"], 0.112, delta=0.005)
        self.assertLess(r["fluxo_mensal"]["ano 2"], 0)

    def test_saque_rende_menos_a_14(self):
        sem = analisar_imovel(Premissas(cet_saque=0.14))
        com = analisar_imovel(Premissas(cet_saque=0.14), com_saque=True)
        self.assertLess(com["tir_aa"], sem["tir_aa"])
        # avaliação no mês 24 já inclui 2 anos de valorização (500k × 1,05² ≈ 551k)
        self.assertAlmostEqual(com["saque"]["emprestimo"], 0.6 * 500_000 * 1.05**2, delta=1)
        self.assertGreater(com["saque"]["liquido"], 0)

    def test_empate_saque(self):
        r = empate_saque(Premissas())
        self.assertAlmostEqual(r["cet_empate_saque"], 0.119, delta=0.005)

    def test_flip_depende_do_valor(self):
        bom = simular(Premissas(), "vender")
        ruim = simular(Premissas(valor_pos_reforma=420_000), "vender")
        self.assertGreater(bom["tir_aa"], 0.2)
        self.assertLess(ruim["tir_aa"], 0.12)

    def test_alerta_acrescimo_de_area(self):
        r = simular(Premissas(reforma_com_acrescimo=True), "vender")
        self.assertTrue(any("382" in a for a in r["alertas"]))
        self.assertFalse(any("382" in a for a in simular(Premissas(), "vender")["alertas"]))

    def test_alerta_quantidade_de_vendas(self):
        r = simular(Premissas(meses_flip=3, meses_obra=2, valor_pos_reforma=600_000), "vender")
        self.assertTrue(any("§1º, II" in a for a in r["alertas"]))

    def test_cli(self):
        self.assertEqual(main(["simular", "imovel", "--aluguel", "3500", "--sistema", "sac"]), 0)
        self.assertEqual(main(["simular", "estrategias", "--cet-saque", "0.10", "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
