"""Confere a camada curada contra o texto legal ingerido e gera um relatório em Markdown.

Resultado por checagem:
- OK: os termos aparecem juntos no artigo esperado (ou achados, se não há artigo esperado).
- ARTIGO DIVERGENTE: os termos existem, mas em outro(s) artigo(s). Provável número errado no grafo.
- NÃO ENCONTRADO: nenhum artigo reúne os termos; a afirmação pode estar errada ou redigida diferente.
- SEM TEXTO: a norma ainda não foi ingerida.

Um OK só diz onde ler, não que a afirmação curada está correta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .grafo import Grafo, _normalizar

CHECAGENS = Path(__file__).parent / "conferencia.json"
LIMITE_ACHADOS = 8


@dataclass
class Resultado:
    no: str
    norma: str
    termos: list[str]
    afirmacao: str
    esperado: str | None
    achados: list[str]
    status: str
    trecho: str


def _rotulo(id_: str) -> str:
    return id_.split("#", 1)[1].removeprefix("art")


def _trecho(texto: str, termo: str, raio: int = 280) -> str:
    alvo = _normalizar(texto)
    i = alvo.find(_normalizar(termo))
    if i < 0:
        return texto[: 2 * raio]
    ini, fim = max(0, i - raio), min(len(texto), i + raio)
    return ("…" if ini else "") + texto[ini:fim].replace("\n", " ") + ("…" if fim < len(texto) else "")


def conferir(g: Grafo, checagens: list[dict]) -> list[Resultado]:
    textos: dict[str, dict[str, str]] = {}
    for n in g.nos.values():
        if n.get("ingerido") and "#" in n["id"]:
            textos.setdefault(n["id"].split("#", 1)[0], {})[_rotulo(n["id"])] = n.get("descricao", "")

    resultados = []
    for c in checagens:
        artigos = textos.get(c["norma"])
        esperado = c.get("artigo_esperado")
        if not artigos:
            resultados.append(Resultado(c["no"], c["norma"], c["termos"], c["afirmacao"], esperado, [], "SEM TEXTO", ""))
            continue
        termos_n = [_normalizar(t) for t in c["termos"]]
        achados = [r for r, t in artigos.items() if all(tn in _normalizar(t) for tn in termos_n)]
        if not achados:
            status, trecho = "NÃO ENCONTRADO", ""
        elif esperado and esperado not in achados:
            status, trecho = "ARTIGO DIVERGENTE", _trecho(artigos[achados[0]], c["termos"][-1])
        else:
            alvo = esperado if esperado else achados[0]
            status, trecho = "OK", _trecho(artigos[alvo], c["termos"][-1])
        resultados.append(Resultado(c["no"], c["norma"], c["termos"], c["afirmacao"], esperado, achados, status, trecho))
    return resultados


def relatorio(g: Grafo, resultados: list[Resultado]) -> str:
    ordem = {"ARTIGO DIVERGENTE": 0, "NÃO ENCONTRADO": 1, "OK": 2, "SEM TEXTO": 3}
    contagem: dict[str, int] = {}
    for r in resultados:
        contagem[r.status] = contagem.get(r.status, 0) + 1

    linhas = [
        "# Conferência da camada curada contra o texto legal",
        "",
        "Resumo: " + ", ".join(f"{k}: {v}" for k, v in sorted(contagem.items(), key=lambda kv: ordem[kv[0]])),
        "",
        "Um **OK** só indica onde ler; a afirmação precisa ser lida contra o trecho. "
        "**ARTIGO DIVERGENTE** e **NÃO ENCONTRADO** exigem correção em `tributarista/dados/`.",
        "",
    ]
    for r in sorted(resultados, key=lambda r: (ordem[r.status], r.norma, r.no)):
        nome = g.nos.get(r.no, {}).get("nome", r.no)
        achados = ", ".join(r.achados[:LIMITE_ACHADOS]) + (" …" if len(r.achados) > LIMITE_ACHADOS else "")
        linhas += [
            f"## [{r.status}] {nome}",
            f"- nó: `{r.no}` · norma: `{r.norma}` · termos: {', '.join(repr(t) for t in r.termos)}",
            f"- artigo esperado: {r.esperado or '—'} · encontrado em: {achados or '—'}",
            f"- afirmação a confirmar: {r.afirmacao}",
        ]
        dependentes = [a for a in g.arestas if r.no in (a.get("fontes") or []) and a.get("status", "consolidado") != "consolidado"]
        if dependentes:
            linhas.append(f"- arestas pendentes que citam este nó: {len(dependentes)} " + "; ".join(f"`{a['de']}→{a['para']}`" for a in dependentes[:6]))
        if r.trecho:
            linhas += ["", f"> {r.trecho}"]
        linhas.append("")

    verificaveis = {c.no for c in resultados}
    fora = sorted(
        n["id"] for n in g.nos.values()
        if n.get("status", "consolidado") != "consolidado" and n["id"] not in verificaveis
    )
    if fora:
        linhas += [
            "## Pendências fora do alcance desta conferência",
            "",
            "Dependem de outras normas (IN RFB 2.021/2021, Lei 7.739/1989, RIR/2018, Lei 14.973/2024, LC 123…) ou de jurisprudência:",
            "",
        ] + [f"- `{i}` — {g.nos[i].get('nome', '')}" for i in fora]
    return "\n".join(linhas) + "\n"


def carregar_checagens(caminho: Path = CHECAGENS) -> list[dict]:
    return json.loads(caminho.read_text(encoding="utf-8"))["checagens"]
