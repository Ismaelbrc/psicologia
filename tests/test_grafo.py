import tempfile
import unittest
from datetime import date
from pathlib import Path

from tributarista import Grafo
from tributarista.cli import main
from tributarista.ingestao import extrair_artigos, html_para_texto, ingerir

FIXTURES = Path(__file__).parent / "fixtures"


def tributos(r, chave="incide"):
    return {a["de"] for a in r[chave]}


class TestIntegridade(unittest.TestCase):
    def setUp(self):
        self.g = Grafo.carregar()

    def test_grafo_valido(self):
        self.assertEqual(self.g.validar(), [])

    def test_toda_incidencia_tem_fonte(self):
        sem_fonte = [
            f"{a['de']}->{a['para']}"
            for a in self.g.arestas
            if a["rel"] in ("incide_em", "reduz", "isenta") and not a.get("fontes")
            and a["para"] != "aquisicao_materiais_construcao"
        ]
        self.assertEqual(sem_fonte, [])

    def test_toda_operacao_tem_alguma_regra(self):
        for op in self.g.por_tipo("operacao"):
            r = self.g.consultar(op["id"])
            self.assertTrue(r["incide"] or r["nao_incide"], op["id"])


class TestConsultas(unittest.TestCase):
    def setUp(self):
        self.g = Grafo.carregar()

    def test_aluguel_pf_hoje_nao_tem_pis_cofins_nem_iss(self):
        r = self.g.consultar("locacao_residencial", "pf", date(2025, 6, 1))
        self.assertIn("irpf", tributos(r))
        self.assertNotIn("pis", tributos(r))
        self.assertNotIn("cbs", tributos(r))  # CBS só a partir de 2026
        self.assertIn("iss", tributos(r, "nao_incide"))

    def test_aluguel_pj_presumido_2025_vs_2027(self):
        r25 = self.g.consultar("locacao_comercial", "pj_lucro_presumido", date(2025, 6, 1))
        r27 = self.g.consultar("locacao_comercial", "pj_lucro_presumido", date(2027, 6, 1))
        self.assertTrue({"irpj", "csll", "pis", "cofins"} <= tributos(r25))
        self.assertFalse({"pis", "cofins"} & tributos(r27))
        self.assertTrue({"cbs", "ibs", "irpj", "csll"} <= tributos(r27))

    def test_redutor_social_so_na_residencial(self):
        res = self.g.consultar("locacao_residencial", "pj_lucro_presumido", date(2027, 1, 1))
        com = self.g.consultar("locacao_comercial", "pj_lucro_presumido", date(2027, 1, 1))
        self.assertIn("redutor_social_locacao", {a["de"] for a in res["beneficios"]})
        self.assertNotIn("redutor_social_locacao", {a["de"] for a in com["beneficios"]})

    def test_iss_extinto_em_2033(self):
        r32 = self.g.consultar("construcao_empreitada", "pj_lucro_presumido", date(2032, 12, 31))
        r33 = self.g.consultar("construcao_empreitada", "pj_lucro_presumido", date(2033, 1, 1))
        self.assertIn("iss", tributos(r32))
        self.assertNotIn("iss", tributos(r33))
        self.assertIn("ibs", tributos(r33))

    def test_construcao_herda_inss_de_obra(self):
        r = self.g.consultar("construcao_obra_propria", "pf", date(2025, 1, 1))
        self.assertIn("contrib_previdenciaria_obra", tributos(r))
        self.assertIn("iss", tributos(r, "nao_incide"))
        self.assertIn("cno", {a["para"] for a in r["obrigacoes"]})
        self.assertIn("dispensa_inss_obra_economica", {a["de"] for a in r["beneficios"]})

    def test_simples_nao_ve_regras_do_presumido(self):
        r = self.g.consultar("construcao_empreitada", "pj_simples", date(2025, 1, 1))
        self.assertIn("das_simples", tributos(r))
        self.assertNotIn("irpj", tributos(r))
        self.assertNotIn("cprb", tributos(r))

    def test_venda_pf_traz_isencoes(self):
        r = self.g.consultar("alienacao_imovel", "pf", date(2025, 1, 1))
        ben = {a["de"] for a in r["beneficios"]}
        self.assertTrue({"isencao_imovel_unico_440k", "isencao_reinvestimento_180d", "fatores_reducao_fr1_fr2"} <= ben)

    def test_incorporacao_herda_venda_e_obra_e_tem_ret(self):
        r = self.g.consultar("incorporacao_imobiliaria", "pj_lucro_presumido", date(2025, 1, 1))
        self.assertIn("contrib_previdenciaria_obra", tributos(r))
        self.assertIn("irpj", tributos(r))
        self.assertIn("ret", {a["de"] for a in r["beneficios"]})

    def test_itbi_com_jurisprudencia(self):
        r = self.g.consultar("aquisicao_imovel", "pf", date(2025, 1, 1))
        self.assertIn("itbi", tributos(r))
        juris = {a["de"] for a in r["jurisprudencia"]}
        self.assertIn("stf_tema_1124", juris)
        self.assertNotIn("stf_tema_796", juris)  # só para integralização
        r2 = self.g.consultar("integralizacao_capital_imovel", None, date(2025, 1, 1))
        self.assertIn("stf_tema_796", {a["de"] for a in r2["jurisprudencia"]})

    def test_caminho_e_mermaid(self):
        trilha = self.g.caminho("reforma_obra_propria", "alienacao_imovel")
        self.assertIsNotNone(trilha)
        m = self.g.subgrafo("locacao_imovel").para_mermaid()
        self.assertTrue(m.startswith("graph LR"))

    def test_busca_por_sinonimo_sem_acento(self):
        ids = {n["id"] for n in self.g.buscar("aluguel")}
        self.assertIn("locacao_imovel", ids)
        self.assertIn("heranca_imovel", {n["id"] for n in self.g.buscar("heranca")})


class TestIngestao(unittest.TestCase):
    def test_extrai_artigos_ignorando_revogados(self):
        html = (FIXTURES / "lei_exemplo.html").read_bytes().decode("windows-1252")
        arts = dict(extrair_artigos(html_para_texto(html)))
        self.assertEqual(list(arts), ["1", "2", "3", "8-A", "251"])
        self.assertIn("Nova redação", arts["3"])
        self.assertIn("Parágrafo único", arts["3"])
        self.assertNotIn("revogada", arts["3"])
        self.assertIn("II - locação", arts["251"])

    def test_ingerir_gera_arquivo_mesclavel(self):
        with tempfile.TemporaryDirectory() as tmp:
            dados = ingerir(str(FIXTURES / "lei_exemplo.html"), "lc_exemplo", "LC de exemplo", Path(tmp))
            self.assertEqual(len([n for n in dados["nos"] if n["tipo"] == "dispositivo"]), 5)
            g = Grafo.carregar(Path(__file__).parents[1] / "tributarista" / "dados", Path(tmp))
            self.assertEqual(g.validar(), [])
            self.assertIn("lc_exemplo#art251", g.nos)


class TestCLI(unittest.TestCase):
    def test_comandos_basicos(self):
        self.assertEqual(main(["validar"]), 0)
        self.assertEqual(main(["consultar", "locacao_imovel", "--perfil", "pf", "--data", "2026-01-01"]), 0)
        self.assertEqual(main(["consultar", "inexistente"]), 2)


if __name__ == "__main__":
    unittest.main()


class TestRevendaPF(unittest.TestCase):
    def setUp(self):
        self.g = Grafo.carregar()

    def test_flip_pf_tem_gatilhos_e_nao_equipara_pj(self):
        r = self.g.consultar("revenda_imovel_reformado", "pf", date(2027, 6, 1))
        cbs = [a for a in r["incide"] if a["de"] == "cbs" and "lc214_2025:art251_p1_iii" in a.get("fontes", [])]
        self.assertTrue(cbs)
        self.assertIn("irpj", tributos(r, "nao_incide"))
        self.assertIn("irpf", tributos(r))  # herdado da alienação
        rel = {a["para"] for a in r["relacionados"]}
        self.assertTrue({"reforma_como_construcao", "pf_vs_pj_revenda"} <= rel)
