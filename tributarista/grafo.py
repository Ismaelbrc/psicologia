"""Grafo de conhecimento tributário.

Modelo:
- Nó: {id, tipo, nome, descricao?, fontes?, status?, vigencia?}
- Aresta: {de, rel, para, nota?, fontes?, status?, vigencia?, condicao?}

`status` indica a confiabilidade da afirmação:
- "consolidado": texto legal/jurisprudência pacífica, conferido.
- "verificar": afirmação plausível, mas número de artigo, valor ou detalhe
  precisa ser conferido no texto oficial antes de uso profissional.
- "controverso": há divergência doutrinária ou judicial relevante.

`vigencia` = {"inicio": "AAAA-MM-DD"?, "fim": "AAAA-MM-DD"?} (datas inclusivas).
`condicao` = {"perfis": [...], "operacoes": [...]} restringe quando a aresta vale.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

DADOS_DIR = Path(__file__).parent / "dados"

TIPOS_NO = {
    "tributo",
    "norma",
    "dispositivo",
    "operacao",
    "perfil",
    "regime",
    "beneficio",
    "obrigacao",
    "jurisprudencia",
    "conceito",
}

RELACOES = {
    "incide_em",  # tributo -> operacao
    "nao_incide_em",  # tributo -> operacao
    "fundamentado_em",  # qualquer -> norma/dispositivo/jurisprudencia
    "parte_de",  # dispositivo -> norma
    "reduz",  # beneficio/regime -> tributo
    "isenta",  # beneficio -> tributo
    "substitui",  # tributo novo -> tributo antigo
    "substitui_parcialmente",  # regime -> tributos que unifica
    "exige",  # operacao -> obrigacao
    "aplica_a",  # regime/beneficio -> perfil
    "interpreta",  # jurisprudencia -> tributo/dispositivo
    "altera",  # norma -> norma
    "relacionado",  # ligação conceitual genérica
    "especializa",  # operacao -> operacao mais geral
}

STATUS = {"consolidado", "verificar", "controverso"}

# Relações cuja origem é o "sujeito" que atua sobre a operação consultada.
REL_TRIBUTACAO = {"incide_em", "nao_incide_em"}
REL_BENEFICIO = {"reduz", "isenta"}


class ErroGrafo(Exception):
    pass


def _parse_data(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def vigente(item: dict, em: date | None) -> bool:
    if em is None:
        return True
    v = item.get("vigencia") or {}
    ini, fim = _parse_data(v.get("inicio")), _parse_data(v.get("fim"))
    if ini and em < ini:
        return False
    if fim and em > fim:
        return False
    return True


def condicao_atende(item: dict, perfil: str | None, operacao: str | None) -> bool:
    """Sem valor no contexto => não filtra (a condição aparece no resultado)."""
    c = item.get("condicao") or {}
    if perfil and c.get("perfis") and perfil not in c["perfis"]:
        return False
    if operacao and c.get("operacoes") and operacao not in c["operacoes"]:
        return False
    return True


@dataclass
class Grafo:
    nos: dict[str, dict] = field(default_factory=dict)
    arestas: list[dict] = field(default_factory=list)

    # ---------- construção ----------

    @classmethod
    def carregar(cls, *diretorios: Path | str) -> "Grafo":
        g = cls()
        for d in diretorios or (DADOS_DIR, DADOS_DIR / "ingeridos"):
            for arq in sorted(Path(d).glob("*.json")):
                g.mesclar(json.loads(arq.read_text(encoding="utf-8")), origem=arq.name)
        return g

    def mesclar(self, dados: dict, origem: str = "?") -> None:
        for no in dados.get("nos", []):
            if no["id"] in self.nos:
                raise ErroGrafo(f"{origem}: nó duplicado '{no['id']}'")
            self.nos[no["id"]] = {**no, "_origem": origem}
        for a in dados.get("arestas", []):
            self.arestas.append({**a, "_origem": origem})

    def validar(self) -> list[str]:
        erros = []
        for no in self.nos.values():
            if no.get("tipo") not in TIPOS_NO:
                erros.append(f"{no['id']}: tipo inválido '{no.get('tipo')}'")
            if no.get("status", "consolidado") not in STATUS:
                erros.append(f"{no['id']}: status inválido '{no.get('status')}'")
            for f in no.get("fontes", []):
                if f not in self.nos:
                    erros.append(f"{no['id']}: fonte inexistente '{f}'")
        for a in self.arestas:
            rotulo = f"{a.get('de')} -{a.get('rel')}-> {a.get('para')}"
            for ponta in ("de", "para"):
                if a.get(ponta) not in self.nos:
                    erros.append(f"{rotulo}: nó '{a.get(ponta)}' inexistente")
            if a.get("rel") not in RELACOES:
                erros.append(f"{rotulo}: relação inválida")
            if a.get("status", "consolidado") not in STATUS:
                erros.append(f"{rotulo}: status inválido")
            for f in a.get("fontes", []):
                if f not in self.nos:
                    erros.append(f"{rotulo}: fonte inexistente '{f}'")
            for chave, tipo in (("perfis", "perfil"), ("operacoes", "operacao")):
                for ref in (a.get("condicao") or {}).get(chave, []):
                    if self.nos.get(ref, {}).get("tipo") != tipo:
                        erros.append(f"{rotulo}: condição '{ref}' não é {tipo}")
            try:
                _parse_data((a.get("vigencia") or {}).get("inicio"))
                _parse_data((a.get("vigencia") or {}).get("fim"))
            except ValueError:
                erros.append(f"{rotulo}: vigência com data inválida")
        return erros

    # ---------- navegação ----------

    def no(self, id_: str) -> dict:
        try:
            return self.nos[id_]
        except KeyError:
            raise ErroGrafo(f"nó '{id_}' não existe") from None

    def saindo(self, id_: str, rel: str | Iterable[str] | None = None) -> list[dict]:
        rels = {rel} if isinstance(rel, str) else set(rel) if rel else None
        return [a for a in self.arestas if a["de"] == id_ and (rels is None or a["rel"] in rels)]

    def entrando(self, id_: str, rel: str | Iterable[str] | None = None) -> list[dict]:
        rels = {rel} if isinstance(rel, str) else set(rel) if rel else None
        return [a for a in self.arestas if a["para"] == id_ and (rels is None or a["rel"] in rels)]

    def por_tipo(self, tipo: str) -> list[dict]:
        return sorted((n for n in self.nos.values() if n["tipo"] == tipo), key=lambda n: n["id"])

    def buscar(self, termo: str) -> list[dict]:
        t = _normalizar(termo)
        achados = []
        for n in self.nos.values():
            alvo = _normalizar(" ".join([n["id"], n.get("nome", ""), n.get("descricao", ""), " ".join(n.get("sinonimos", []))]))
            if t in alvo:
                achados.append(n)
        return sorted(achados, key=lambda n: (n["tipo"], n["id"]))

    def caminho(self, origem: str, destino: str) -> list[dict] | None:
        """Menor caminho não-direcionado; retorna lista de arestas."""
        self.no(origem), self.no(destino)
        adj: dict[str, list[tuple[str, dict]]] = {}
        for a in self.arestas:
            adj.setdefault(a["de"], []).append((a["para"], a))
            adj.setdefault(a["para"], []).append((a["de"], a))
        anterior: dict[str, tuple[str, dict] | None] = {origem: None}
        fila = deque([origem])
        while fila:
            atual = fila.popleft()
            if atual == destino:
                break
            for viz, a in adj.get(atual, []):
                if viz not in anterior:
                    anterior[viz] = (atual, a)
                    fila.append(viz)
        if destino not in anterior:
            return None
        trilha = []
        cur = destino
        while anterior[cur] is not None:
            prev, a = anterior[cur]
            trilha.append(a)
            cur = prev
        return list(reversed(trilha))

    def operacoes_ancestrais(self, operacao: str) -> list[str]:
        """A operação e as mais gerais que ela especializa (ex.: locação residencial -> locação)."""
        visto, fila = [operacao], deque([operacao])
        while fila:
            for a in self.saindo(fila.popleft(), "especializa"):
                if a["para"] not in visto:
                    visto.append(a["para"])
                    fila.append(a["para"])
        return visto

    # ---------- consulta principal ----------

    def consultar(self, operacao: str, perfil: str | None = None, em: date | None = None) -> dict:
        """O que incide (ou não) numa operação, para um perfil, numa data."""
        if self.no(operacao)["tipo"] != "operacao":
            raise ErroGrafo(f"'{operacao}' não é uma operação")
        if perfil and self.no(perfil)["tipo"] != "perfil":
            raise ErroGrafo(f"'{perfil}' não é um perfil")

        ops = self.operacoes_ancestrais(operacao)

        def ok(a: dict) -> bool:
            return vigente(a, em) and condicao_atende(a, perfil, None)

        incide, nao_incide = [], []
        for op in ops:
            for a in self.entrando(op, REL_TRIBUTACAO):
                if ok(a):
                    (incide if a["rel"] == "incide_em" else nao_incide).append(a)

        tributos = {a["de"] for a in incide}
        beneficios = []
        for a in self.arestas:
            if a["rel"] in REL_BENEFICIO and a["para"] in tributos and vigente(a, em):
                conds = (a.get("condicao") or {}).get("operacoes")
                if conds and not set(conds) & set(ops):
                    continue
                if not condicao_atende(a, perfil, None):
                    continue
                beneficios.append(a)

        obrigacoes = [a for op in ops for a in self.saindo(op, "exige") if ok(a)]

        juris = []
        alvos = tributos | set(ops)
        for a in self.arestas:
            if a["rel"] == "interpreta" and a["para"] in alvos and vigente(a, em):
                conds = (a.get("condicao") or {}).get("operacoes")
                if not conds or set(conds) & set(ops):
                    juris.append(a)

        relacionados = [
            a
            for op in ops
            for a in self.saindo(op, "relacionado") + self.entrando(op, "relacionado")
            if ok(a)
        ]

        return {
            "operacao": operacao,
            "perfil": perfil,
            "data": em.isoformat() if em else None,
            "operacoes_consideradas": ops,
            "incide": incide,
            "nao_incide": nao_incide,
            "beneficios": beneficios,
            "obrigacoes": obrigacoes,
            "jurisprudencia": juris,
            "relacionados": relacionados,
        }

    # ---------- exportação ----------

    def subgrafo(self, centro: str, profundidade: int = 1) -> "Grafo":
        self.no(centro)
        incluidos, fronteira = {centro}, {centro}
        for _ in range(profundidade):
            nova = set()
            for a in self.arestas:
                if a["de"] in fronteira and a["para"] not in incluidos:
                    nova.add(a["para"])
                if a["para"] in fronteira and a["de"] not in incluidos:
                    nova.add(a["de"])
            incluidos |= nova
            fronteira = nova
        g = Grafo()
        g.nos = {k: v for k, v in self.nos.items() if k in incluidos}
        g.arestas = [a for a in self.arestas if a["de"] in incluidos and a["para"] in incluidos]
        return g

    def para_mermaid(self) -> str:
        linhas = ["graph LR"]
        for n in sorted(self.nos.values(), key=lambda n: n["id"]):
            rotulo = n.get("nome", n["id"]).replace('"', "'")
            linhas.append(f'  {_mid(n["id"])}["{rotulo}<br/><small>{n["tipo"]}</small>"]')
        for a in self.arestas:
            seta = "-.->" if a.get("status", "consolidado") != "consolidado" else "-->"
            linhas.append(f"  {_mid(a['de'])} {seta}|{a['rel']}| {_mid(a['para'])}")
        return "\n".join(linhas)

    def estatisticas(self) -> dict:
        por_tipo: dict[str, int] = {}
        for n in self.nos.values():
            por_tipo[n["tipo"]] = por_tipo.get(n["tipo"], 0) + 1
        por_status: dict[str, int] = {}
        for item in list(self.nos.values()) + self.arestas:
            s = item.get("status", "consolidado")
            por_status[s] = por_status.get(s, 0) + 1
        return {"nos": len(self.nos), "arestas": len(self.arestas), "por_tipo": por_tipo, "por_status": por_status}


def _mid(id_: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in id_)


def _normalizar(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))
