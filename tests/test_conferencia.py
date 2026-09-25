import tempfile
import unittest
from pathlib import Path

from tributarista import Grafo
from tributarista.conferencia import carregar_checagens, conferir, relatorio
from tributarista.ingestao import ingerir

RAIZ = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


class TestConferencia(unittest.TestCase):
    def test_checagens_apontam_para_nos_existentes(self):
        g = Grafo.carregar()
        for c in carregar_checagens():
            self.assertIn(c["no"], g.nos, c)
            self.assertEqual(g.nos[c["norma"]]["tipo"], "norma", c)

    def test_sem_texto_ingerido(self):
        g = Grafo.carregar(RAIZ / "tributarista" / "dados")
        self.assertEqual({r.status for r in conferir(g, carregar_checagens())}, {"SEM TEXTO"})

    def test_classifica_ok_divergente_e_nao_encontrado(self):
        with tempfile.TemporaryDirectory() as tmp:
            dados = ingerir(str(FIXTURES / "lc116_mini.html"), "lc116_2003", destino=Path(tmp))
            self.assertIn("lc116_2003#anexo", {n["id"] for n in dados["nos"]})
            g = Grafo.carregar(RAIZ / "tributarista" / "dados", Path(tmp))
            self.assertEqual(g.validar(), [])
            res = {r.no: r for r in conferir(g, carregar_checagens()) if r.norma == "lc116_2003"}

        self.assertEqual(res["lc116_2003:art3"].status, "OK")
        self.assertEqual(res["lc116_2003:art7_p2_i"].status, "OK")
        self.assertEqual(res["lc116_2003:lista_7_02"].status, "OK")
        self.assertEqual(res["lc116_2003:lista_7_05"].status, "OK")
        # Na fixture a alíquota mínima está no art. 9, não no 8-A esperado.
        self.assertEqual(res["lc116_2003:art8a"].status, "ARTIGO DIVERGENTE")
        self.assertEqual(res["lc116_2003:art8a"].achados, ["9"])
        # A fixture não tem o item 17.12.
        self.assertEqual(res["lc116_2003:lista_17_12"].status, "NÃO ENCONTRADO")
        self.assertIn("materiais fornecidos", res["lc116_2003:art7_p2_i"].trecho)

        md = relatorio(g, list(res.values()))
        self.assertLess(md.index("[ARTIGO DIVERGENTE]"), md.index("[OK]"))


if __name__ == "__main__":
    unittest.main()
