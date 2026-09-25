"""Linha de comando: python -m tributarista <comando> ..."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .grafo import ErroGrafo, Grafo

NORMAS_PADRAO = ("lc214_2025", "lc116_2003", "ctn")

MARCA = {"consolidado": "", "verificar": " [verificar]", "controverso": " [controverso]"}

AVISO = (
    "Aviso: material de apoio. Itens [verificar] e [controverso] devem ser conferidos no texto "
    "oficial e na legislação municipal/estadual aplicável antes de qualquer uso profissional."
)


def _marca(item: dict) -> str:
    return MARCA.get(item.get("status", "consolidado"), "")


def _nome(g: Grafo, id_: str) -> str:
    return g.nos[id_].get("nome", id_)


def _vig(a: dict) -> str:
    v = a.get("vigencia") or {}
    if not v:
        return ""
    return f" (vigência: {v.get('inicio', '...')} a {v.get('fim', '...')})"


def _fontes(g: Grafo, item: dict) -> str:
    fs = item.get("fontes") or []
    return ("\n      fonte: " + "; ".join(_nome(g, f) for f in fs)) if fs else ""


def _cond(g: Grafo, a: dict) -> str:
    c = a.get("condicao") or {}
    partes = []
    if c.get("perfis"):
        partes.append("perfis: " + ", ".join(_nome(g, p) for p in c["perfis"]))
    return f" <{'; '.join(partes)}>" if partes else ""


def imprimir_consulta(g: Grafo, r: dict) -> None:
    op = g.no(r["operacao"])
    print(f"\n== {op['nome']} ==")
    ctx = []
    if r["perfil"]:
        ctx.append(f"perfil: {_nome(g, r['perfil'])}")
    ctx.append(f"data: {r['data'] or 'qualquer'}")
    print("   " + " | ".join(ctx))
    if len(r["operacoes_consideradas"]) > 1:
        print("   inclui regras de: " + ", ".join(_nome(g, o) for o in r["operacoes_consideradas"][1:]))

    def bloco(titulo: str, itens: list[dict], chave: str, alvo: bool = False) -> None:
        if not itens:
            return
        print(f"\n-- {titulo}")
        for a in itens:
            sufixo = f" → {_nome(g, a['para'])} ({a['rel']})" if alvo else ""
            print(f"  • {_nome(g, a[chave])}{sufixo}{_marca(a)}{_cond(g, a)}{_vig(a)}")
            if a.get("nota"):
                print(f"      {a['nota']}")
            if a.get("fontes"):
                print(_fontes(g, a).lstrip("\n"))

    bloco("INCIDE", r["incide"], "de")
    bloco("NÃO INCIDE", r["nao_incide"], "de")
    bloco("BENEFÍCIOS / REDUÇÕES", r["beneficios"], "de", alvo=True)
    bloco("OBRIGAÇÕES", r["obrigacoes"], "para")
    bloco("JURISPRUDÊNCIA", r["jurisprudencia"], "de")
    if r["relacionados"]:
        print("\n-- VER TAMBÉM")
        for a in r["relacionados"]:
            outro = a["para"] if a["de"] in r["operacoes_consideradas"] else a["de"]
            print(f"  • {_nome(g, outro)}{_marca(a)}")
            if a.get("nota"):
                print(f"      {a['nota']}")
    print(f"\n{AVISO}\n")


def cmd_explicar(g: Grafo, id_: str) -> None:
    n = g.no(id_)
    print(f"\n{n.get('nome', id_)}  [{n['tipo']}]{_marca(n)}  id={id_}")
    if n.get("descricao"):
        print(f"  {n['descricao']}")
    if n.get("url"):
        print(f"  {n['url']}")
    if n.get("fontes"):
        print("  fontes: " + "; ".join(_nome(g, f) for f in n["fontes"]))
    sai, entra = g.saindo(id_), g.entrando(id_)
    if sai:
        print("  →")
        for a in sai:
            print(f"    {a['rel']} {_nome(g, a['para'])}{_marca(a)}{_cond(g, a)}{_vig(a)}")
    if entra:
        print("  ←")
        for a in entra:
            print(f"    {_nome(g, a['de'])} {a['rel']}{_marca(a)}{_cond(g, a)}{_vig(a)}")


def cmd_pendencias(g: Grafo) -> None:
    for n in sorted(g.nos.values(), key=lambda n: n["id"]):
        if n.get("status", "consolidado") != "consolidado":
            print(f"[{n['status']}] nó {n['id']}: {n.get('nome', '')}")
    for a in g.arestas:
        if a.get("status", "consolidado") != "consolidado":
            print(f"[{a['status']}] {a['de']} -{a['rel']}-> {a['para']}  ({a['_origem']})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tributarista", description="Grafo tributário imobiliário brasileiro")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consultar", help="o que incide numa operação")
    c.add_argument("operacao")
    c.add_argument("--perfil")
    c.add_argument("--data", help="AAAA-MM-DD (padrão: hoje; use 'todas' para ignorar vigência)")
    c.add_argument("--json", action="store_true")

    sub.add_parser("operacoes", help="lista operações")
    sub.add_parser("perfis", help="lista perfis")
    b = sub.add_parser("buscar")
    b.add_argument("termo")
    e = sub.add_parser("explicar")
    e.add_argument("id")
    cm = sub.add_parser("caminho")
    cm.add_argument("origem")
    cm.add_argument("destino")
    m = sub.add_parser("mermaid", help="exporta subgrafo em Mermaid")
    m.add_argument("id")
    m.add_argument("--profundidade", type=int, default=1)
    sub.add_parser("validar")
    sub.add_parser("stats")
    sub.add_parser("pendencias", help="itens a conferir no texto oficial")
    ip = sub.add_parser("ingerir-padrao", help="baixa e ingere LC 214, LC 116 e CTN do Planalto")
    ip.add_argument("--normas", nargs="+", default=list(NORMAS_PADRAO))
    cf = sub.add_parser("conferir", help="confere itens curados contra o texto ingerido")
    cf.add_argument("--saida", default="relatorio_conferencia.md")
    i = sub.add_parser("ingerir", help="ingere HTML do Planalto (URL ou arquivo)")
    i.add_argument("fonte")
    i.add_argument("--norma", required=True, help="id da norma, ex.: lc214_2025")
    i.add_argument("--nome", help="nome da norma, se ainda não existir no grafo")

    args = p.parse_args(argv)
    g = Grafo.carregar()

    try:
        if args.cmd == "consultar":
            em = None if args.data == "todas" else date.fromisoformat(args.data) if args.data else date.today()
            r = g.consultar(args.operacao, args.perfil, em)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
            else:
                imprimir_consulta(g, r)
        elif args.cmd in ("operacoes", "perfis"):
            for n in g.por_tipo("operacao" if args.cmd == "operacoes" else "perfil"):
                print(f"{n['id']:32} {n['nome']}")
        elif args.cmd == "buscar":
            for n in g.buscar(args.termo):
                print(f"{n['tipo']:15} {n['id']:40} {n.get('nome', '')}")
        elif args.cmd == "explicar":
            cmd_explicar(g, args.id)
        elif args.cmd == "caminho":
            trilha = g.caminho(args.origem, args.destino)
            if trilha is None:
                print("sem caminho")
                return 1
            for a in trilha:
                print(f"{_nome(g, a['de'])} -{a['rel']}-> {_nome(g, a['para'])}")
        elif args.cmd == "mermaid":
            print(g.subgrafo(args.id, args.profundidade).para_mermaid())
        elif args.cmd == "validar":
            erros = g.validar()
            for e_ in erros:
                print(e_)
            print(f"{len(erros)} erro(s)")
            return 1 if erros else 0
        elif args.cmd == "stats":
            print(json.dumps(g.estatisticas(), ensure_ascii=False, indent=2))
        elif args.cmd == "pendencias":
            cmd_pendencias(g)
        elif args.cmd == "ingerir-padrao":
            from .ingestao import ingerir

            falhas = 0
            for norma in args.normas:
                url = g.no(norma).get("url")
                try:
                    dados = ingerir(url, norma)
                    n = sum(1 for x in dados["nos"] if x["tipo"] == "dispositivo")
                    print(f"{norma}: {n} dispositivos ({url})")
                except Exception as ex:  # rede, HTML inesperado
                    falhas += 1
                    print(f"{norma}: FALHOU ({ex}) — baixe {url} manualmente e use 'ingerir <arquivo> --norma {norma}'", file=sys.stderr)
            return 1 if falhas else 0
        elif args.cmd == "conferir":
            from pathlib import Path

            from .conferencia import carregar_checagens, conferir, relatorio

            resultados = conferir(g, carregar_checagens())
            Path(args.saida).write_text(relatorio(g, resultados), encoding="utf-8")
            resumo: dict[str, int] = {}
            for r in resultados:
                resumo[r.status] = resumo.get(r.status, 0) + 1
            print(f"relatório em {args.saida}: " + ", ".join(f"{k}={v}" for k, v in resumo.items()))
            return 1 if resumo.get("ARTIGO DIVERGENTE") or resumo.get("NÃO ENCONTRADO") else 0
        elif args.cmd == "ingerir":
            from .ingestao import ingerir

            nome = args.nome if args.norma not in g.nos else None
            if args.norma not in g.nos and not nome:
                print(f"norma '{args.norma}' não existe no grafo; informe --nome", file=sys.stderr)
                return 2
            dados = ingerir(args.fonte, args.norma, nome)
            print(f"{sum(1 for n in dados['nos'] if n['tipo'] == 'dispositivo')} artigos ingeridos em dados/ingeridos/{args.norma}.json")
    except ErroGrafo as ex:
        print(f"erro: {ex}", file=sys.stderr)
        return 2
    return 0
