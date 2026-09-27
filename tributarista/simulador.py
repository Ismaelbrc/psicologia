"""Simulador de investimento imobiliário na pessoa física (compra financiada, reforma, aluguel, saque, revenda).

Uso: python -m tributarista simular {imovel,estrategias,empate-saque} [premissas...]
Todas as premissas têm padrão (imóvel em Goiânia, R$ 300 mil, reforma de R$ 60 mil,
aluguel de R$ 3.000) e podem ser trocadas por linha de comando. Rode com --help.

Modelo mensal simplificado. Limitações conhecidas:
- IR do aluguel por alíquota marginal fixa (não aplica a tabela progressiva);
- fator de redução FR2 aplicado sobre o ganho inteiro (aproximação);
- IBS/CBS sobre a venda não é calculado; apenas avisa se um gatilho do art. 251 da LC 214 for acionado;
- não modela vacância longa, inadimplência, custos de manutenção nem aprovação de crédito.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, fields


# ---------- matemática financeira (sem dependências) ----------

def taxa_mensal(aa: float) -> float:
    return (1 + aa) ** (1 / 12) - 1


def pmt(i: float, n: int, pv: float) -> float:
    return pv / n if i == 0 else pv * i / (1 - (1 + i) ** -n)


def vpl(i: float, fluxos: list[float]) -> float:
    return sum(f / (1 + i) ** t for t, f in enumerate(fluxos))


def tir_mensal(fluxos: list[float]) -> float | None:
    lo, hi = -0.99, 1.0
    f_lo, f_hi = vpl(lo, fluxos), vpl(hi, fluxos)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = vpl(mid, fluxos)
        if abs(f_mid) < 1e-6:
            break
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def anual(i_m: float | None) -> float | None:
    return None if i_m is None else (1 + i_m) ** 12 - 1


# ---------- premissas ----------

@dataclass
class Premissas:
    # compra
    preco: float = 300_000
    entrada: float = 0.20
    itbi: float = 0.02  # Goiânia
    registro: float = 4_200  # compra + alienação fiduciária (estimativa GO)
    avaliacao_banco: float = 3_300
    certidoes: float = 500
    # financiamento
    cet: float = 0.14
    prazo: int = 360
    sistema: str = "price"  # price | sac
    # reforma
    reforma: float = 60_000
    meses_obra: int = 4
    valor_pos_reforma: float = 500_000
    # aluguel
    aluguel: float = 3_000
    reajuste: float = 0.045
    vacancia_meses_ano: int = 1
    adm: float = 0.0
    aliq_ir: float = 0.275
    custo_vago: float = 750  # condomínio + IPTU quando vazio
    # saque (crédito com garantia de imóvel)
    ltv_saque: float = 0.60
    cet_saque: float = 0.11
    prazo_saque: int = 240
    mes_saque: int = 24
    custos_saque: float = 6_000
    iof_saque: float = 0.0338
    # mercado e saída
    valorizacao: float = 0.05
    corretagem: float = 0.05
    cdi: float = 0.10  # rendimento líquido do caixa e taxa de desconto
    anos: int = 10
    # cadeia
    max_imoveis: int = 4
    meses_flip: int = 10
    flip_ate_mes: int = 60
    reforma_com_acrescimo: bool = False

    @property
    def custos_compra(self) -> dict:
        return {
            "entrada": self.preco * self.entrada,
            "itbi": self.preco * self.itbi,
            "registro": self.registro,
            "avaliacao_banco": self.avaliacao_banco if self.entrada < 1 else 0,
            "certidoes": self.certidoes,
        }

    @property
    def caixa_para_comprar(self) -> float:
        return sum(self.custos_compra.values()) + self.reforma


class Divida:
    def __init__(self, valor: float, aa: float, n: int, sistema: str):
        self.saldo, self.i, self.n, self.sistema, self.k = valor, taxa_mensal(aa), n, sistema, 0
        self.amort_sac = valor / n if n else 0
        self.parcela_price = pmt(self.i, n, valor) if valor else 0

    def pagar(self) -> float:
        if self.saldo <= 0 or self.k >= self.n:
            return 0.0
        juros = self.saldo * self.i
        p = self.amort_sac + juros if self.sistema == "sac" else self.parcela_price
        self.saldo = max(0.0, self.saldo - (p - juros))
        self.k += 1
        return p

    def proxima_parcela(self) -> float:
        if self.saldo <= 0:
            return 0.0
        return self.amort_sac + self.saldo * self.i if self.sistema == "sac" else self.parcela_price


class Imovel:
    def __init__(self, p: Premissas, mes: int):
        self.p, self.mes_compra, self.vendido = p, mes, False
        fin = p.preco * (1 - p.entrada)
        self.divida = Divida(fin, p.cet, p.prazo, p.sistema)
        self.sacou = False

    def idade(self, m: int) -> int:
        return m - self.mes_compra

    def valor(self, m: int) -> float:
        p, idade = self.p, self.idade(m)
        if idade < p.meses_obra:
            return p.preco + p.reforma * idade / max(1, p.meses_obra)
        return p.valor_pos_reforma * (1 + p.valorizacao) ** (idade / 12)

    def ir_ganho(self, m: int, valor_venda: float) -> float:
        p = self.p
        custo = p.preco + p.preco * p.itbi + p.registro + p.reforma
        ganho = valor_venda * (1 - p.corretagem) - custo
        return max(0.0, 0.15 * ganho / 1.0035 ** self.idade(m))  # FR2 aproximado


# ---------- motor ----------

def simular(p: Premissas, politica: str = "segurar") -> dict:
    """politica: 'segurar' (sem saque), 'saque' (saque no mes_saque e compra do próximo), 'vender' (flips até flip_ate_mes)."""
    meses = p.anos * 12
    caixa, aporte = 0.0, 0.0
    fluxo_ext = [0.0] * (meses + 1)
    imoveis: list[Imovel] = []
    eventos: list[str] = []
    alertas: list[str] = []
    vendas_por_ano: dict[int, int] = {}
    vendas_com_obra: list[int] = []
    ultima_isencao = -10_000
    ir_venda_total = corretagem_total = ir_aluguel_total = 0.0

    def aportar(m: int, valor: float) -> None:
        nonlocal caixa, aporte
        caixa += valor
        aporte += valor
        fluxo_ext[m] -= valor

    def comprar(m: int) -> None:
        nonlocal caixa
        caixa -= sum(p.custos_compra.values())
        imoveis.append(Imovel(p, m))
        eventos.append(f"mês {m}: compra do imóvel {len(imoveis)}")

    aportar(0, p.caixa_para_comprar)
    comprar(0)

    for m in range(1, meses + 1):
        caixa *= 1 + taxa_mensal(p.cdi)
        for im in [x for x in imoveis if not x.vendido]:
            idade = im.idade(m)
            flip = politica == "vender" and im.mes_compra < p.flip_ate_mes
            if idade <= p.meses_obra:
                caixa -= p.reforma / p.meses_obra
            caixa -= im.divida.pagar()
            alugado = (not flip) and idade > p.meses_obra + 1 and (m % 12) >= p.vacancia_meses_ano
            if alugado:
                bruto = p.aluguel * (1 + p.reajuste) ** ((m - 1) // 12)
                liquido_adm = bruto * (1 - p.adm)
                ir = liquido_adm * p.aliq_ir
                ir_aluguel_total += ir
                caixa += liquido_adm - ir
            else:
                caixa -= p.custo_vago

            if politica == "saque" and not im.sacou and idade == p.mes_saque:
                novo = p.ltv_saque * im.valor(m)
                custo = p.custos_saque + p.iof_saque * novo
                liquido = novo - im.divida.saldo - custo
                eventos.append(f"mês {m}: saque no imóvel {imoveis.index(im) + 1}: empréstimo {novo:,.0f}, quita {im.divida.saldo:,.0f}, custos {custo:,.0f}, líquido {liquido:,.0f}")
                caixa += liquido
                im.divida = Divida(novo, p.cet_saque, p.prazo_saque, "price")
                im.sacou = True
                ativos = [x for x in imoveis if not x.vendido]
                if len(ativos) < p.max_imoveis and m <= meses - 6:
                    if caixa < p.caixa_para_comprar:
                        aportar(m, p.caixa_para_comprar - caixa)
                    comprar(m)

            if flip and idade == p.meses_flip:
                v = im.valor(m)
                ir = im.ir_ganho(m, v)
                if m - ultima_isencao >= 60:
                    ultima_isencao, ir = m, 0.0
                    eventos.append(f"mês {m}: venda com isenção de 180 dias (1x a cada 5 anos)")
                corr = p.corretagem * v
                caixa += v - corr - im.divida.saldo - 400 - ir
                ir_venda_total += ir
                corretagem_total += corr
                im.vendido = True
                ano = (m - 1) // 12
                vendas_por_ano[ano] = vendas_por_ano.get(ano, 0) + 1
                vendas_com_obra.append(m)
                eventos.append(f"mês {m}: venda do imóvel {imoveis.index(im) + 1} por {v:,.0f} (IR {ir:,.0f}, corretagem {corr:,.0f})")

        ativos = [x for x in imoveis if not x.vendido]
        if politica == "vender":
            if m < p.flip_ate_mes and not ativos and caixa >= p.caixa_para_comprar:
                comprar(m)
            elif m >= p.flip_ate_mes:
                while caixa >= p.caixa_para_comprar and len([x for x in imoveis if not x.vendido]) < p.max_imoveis and m <= meses - 6:
                    comprar(m)
        if caixa < 0:
            aportar(m, -caixa)

    # alertas de IBS/CBS (LC 214, art. 251) para a PF
    for ano, n in vendas_por_ano.items():
        if n > 3:
            alertas.append(f"ano {ano + 1}: {n} vendas de imóveis com menos de 5 anos: a PF vira contribuinte de IBS/CBS (art. 251, §1º, II e §2º)")
    if p.reforma_com_acrescimo:
        for a, b in zip(vendas_com_obra, vendas_com_obra[1:]):
            if b - a < 60:
                alertas.append(f"mês {b}: 2ª venda em 5 anos de imóvel com obra de acréscimo; pelo regulamento (art. 382, §6º), construção de parte da edificação conta como 'construído pelo alienante': risco de IBS/CBS (art. 251, §1º, III)")
                break
    n_alugados = len([x for x in imoveis if not x.vendido])
    receita_anual_final = n_alugados * p.aluguel * (1 + p.reajuste) ** (p.anos - 1) * 12
    if n_alugados > 3 and receita_anual_final > 240_000:
        alertas.append(f"{n_alugados} imóveis e receita de aluguel de R$ {receita_anual_final:,.0f}/ano: a PF vira contribuinte de IBS/CBS na locação (art. 251, §1º, I)")

    ativos = [x for x in imoveis if not x.vendido]
    fim = meses
    patrimonio = caixa + sum(x.valor(fim) - x.divida.saldo for x in ativos)
    aluguel_liq_final = p.aluguel * (1 + p.reajuste) ** (p.anos - 1) * (1 - p.adm) * (1 - p.aliq_ir)
    fluxo_mensal_final = sum(aluguel_liq_final - x.divida.proxima_parcela() for x in ativos if x.idade(fim) > p.meses_obra + 1)
    fluxo_ext[-1] += patrimonio
    return {
        "politica": politica,
        "imoveis_final": len(ativos),
        "imoveis_comprados": len(imoveis),
        "vendas": sum(vendas_por_ano.values()),
        "aporte_total": aporte,
        "patrimonio_liquido": patrimonio,
        "divida_final": sum(x.divida.saldo for x in ativos),
        "caixa_final": caixa,
        "fluxo_mensal_final": fluxo_mensal_final,
        "tir_aa": anual(tir_mensal(fluxo_ext)),
        "ir_aluguel_total": ir_aluguel_total,
        "ir_venda_total": ir_venda_total,
        "corretagem_total": corretagem_total,
        "eventos": eventos,
        "alertas": alertas,
    }


def analisar_imovel(p: Premissas, com_saque: bool = False, venda_ano: int | None = None) -> dict:
    """Um imóvel, do início até a venda no fim do horizonte (ou em venda_ano)."""
    anos = venda_ano or p.anos
    meses = anos * 12
    im = Imovel(p, 0)
    fluxos = [-(sum(p.custos_compra.values()))]
    mensal = []
    saque = None
    ir_aluguel = 0.0
    for m in range(1, meses + 1):
        cf = -(p.reforma / p.meses_obra if m <= p.meses_obra else 0)
        cf -= im.divida.pagar()
        if m > p.meses_obra + 1 and (m % 12) >= p.vacancia_meses_ano:
            bruto = p.aluguel * (1 + p.reajuste) ** ((m - 1) // 12) * (1 - p.adm)
            ir_aluguel += bruto * p.aliq_ir
            cf += bruto * (1 - p.aliq_ir)
        else:
            cf -= p.custo_vago
        if com_saque and m == p.mes_saque:
            novo = p.ltv_saque * im.valor(m)
            custo = p.custos_saque + p.iof_saque * novo
            saque = {"emprestimo": novo, "quita": im.divida.saldo, "custos": custo, "liquido": novo - im.divida.saldo - custo}
            cf += saque["liquido"]
            im.divida = Divida(novo, p.cet_saque, p.prazo_saque, "price")
        fluxos.append(cf)
        mensal.append(cf)
    v = im.valor(meses)
    ir_ganho = im.ir_ganho(meses, v)
    liquido_venda = v * (1 - p.corretagem) - ir_ganho - im.divida.saldo - 400
    fluxos[-1] += liquido_venda
    exposicao, acc = 0.0, 0.0
    for f in fluxos[:-1]:
        acc += f
        exposicao = min(exposicao, acc)
    primeira = Divida(p.preco * (1 - p.entrada), p.cet, p.prazo, p.sistema).proxima_parcela()
    return {
        "custos_compra": p.custos_compra,
        "caixa_dia_1": sum(p.custos_compra.values()),
        "primeira_parcela": primeira,
        "aluguel_liquido_ano1": p.aluguel * (1 - p.adm) * (1 - p.aliq_ir),
        "fluxo_mensal": {f"ano {a}": mensal[a * 12 - 6] for a in range(1, anos + 1) if a * 12 - 6 < len(mensal)},
        "saque": saque,
        "venda": {"valor": v, "corretagem": v * p.corretagem, "ir_ganho_capital": ir_ganho, "quita_divida": im.divida.saldo, "liquido": liquido_venda},
        "ir_aluguel_total": ir_aluguel,
        "exposicao_maxima": -exposicao,
        "lucro_total": sum(fluxos),
        "tir_aa": anual(tir_mensal(fluxos)),
    }


def empate_saque(p: Premissas) -> dict:
    """CET máximo do saque para empatar com (a) não sacar e (b) fazer só portabilidade à mesma taxa."""
    def fluxo(modo: str, r: float) -> list[float]:
        q = Premissas(**{**asdict(p), "cet_saque": r})
        if modo == "nenhum":
            return _fluxos_imovel(q, None)
        return _fluxos_imovel(q, modo)

    d = taxa_mensal(p.cdi)
    base = vpl(d, fluxo("nenhum", 0))

    def bissecao(alvo) -> float:
        lo, hi = 0.0, 0.40
        for _ in range(60):
            mid = (lo + hi) / 2
            if alvo(mid) > 0:
                lo = mid
            else:
                hi = mid
        return mid

    empate_nenhum = bissecao(lambda r: vpl(d, fluxo("saque", r)) - base)
    tabela = []
    for r in (0.14, 0.13, 0.12, 0.11, 0.10, 0.09, 0.08):
        tabela.append({
            "cet": r,
            "tir_portabilidade": anual(tir_mensal(fluxo("portabilidade", r))),
            "tir_saque": anual(tir_mensal(fluxo("saque", r))),
            "ganho_vpl_saque": vpl(d, fluxo("saque", r)) - base,
            "saque_so_vence_portabilidade_ate": bissecao(lambda rc, r=r: vpl(d, _fluxos_imovel(Premissas(**{**asdict(p), "cet_saque": rc}), "saque")) - vpl(d, fluxo("portabilidade", r))),
        })
    return {"tir_sem_saque": anual(tir_mensal(fluxo("nenhum", 0))), "cet_empate_saque": empate_nenhum, "tabela": tabela}


def _fluxos_imovel(p: Premissas, modo: str | None) -> list[float]:
    meses = p.anos * 12
    im = Imovel(p, 0)
    fl = [-(sum(p.custos_compra.values()))]
    for m in range(1, meses + 1):
        cf = -(p.reforma / p.meses_obra if m <= p.meses_obra else 0) - im.divida.pagar()
        if m > p.meses_obra + 1 and (m % 12) >= p.vacancia_meses_ano:
            cf += p.aluguel * (1 + p.reajuste) ** ((m - 1) // 12) * (1 - p.adm) * (1 - p.aliq_ir)
        else:
            cf -= p.custo_vago
        if m == p.mes_saque and modo == "portabilidade":
            cf -= 3_500
            im.divida = Divida(im.divida.saldo, p.cet_saque, p.prazo - m, p.sistema)
        elif m == p.mes_saque and modo == "saque":
            novo = p.ltv_saque * im.valor(m)
            cf += novo - im.divida.saldo - (p.custos_saque + p.iof_saque * novo)
            im.divida = Divida(novo, p.cet_saque, p.prazo_saque, "price")
        fl.append(cf)
    v = im.valor(meses)
    fl[-1] += v * (1 - p.corretagem) - im.ir_ganho(meses, v) - im.divida.saldo - 400
    return fl


# ---------- CLI ----------

AJUDA = {
    "preco": "preço de compra (R$)", "entrada": "fração de entrada (0.2 = 20%%)", "itbi": "alíquota do ITBI (Goiânia 0.02)",
    "registro": "registro da compra + alienação fiduciária (R$)", "avaliacao_banco": "avaliação/tarifa do banco (R$)",
    "certidoes": "certidões (R$)", "cet": "CET anual do financiamento", "prazo": "prazo em meses", "sistema": "price ou sac",
    "reforma": "custo da reforma (R$)", "meses_obra": "duração da obra (meses)", "valor_pos_reforma": "valor de mercado/avaliação após a reforma",
    "aluguel": "aluguel mensal (R$)", "reajuste": "reajuste anual do aluguel", "vacancia_meses_ano": "meses vagos por ano",
    "adm": "taxa de administração (0 = você administra)", "aliq_ir": "alíquota marginal de IR sobre o aluguel",
    "custo_vago": "condomínio + IPTU pagos quando vazio (R$/mês)", "ltv_saque": "fração da avaliação no saque",
    "cet_saque": "CET anual do saque/portabilidade", "prazo_saque": "prazo do saque (meses)", "mes_saque": "mês do saque após a compra",
    "custos_saque": "custos fixos do saque (R$)", "iof_saque": "IOF do saque (fração)", "valorizacao": "valorização anual",
    "corretagem": "corretagem na venda", "cdi": "rendimento líquido do caixa / taxa de desconto", "anos": "horizonte (anos)",
    "max_imoveis": "limite de imóveis financiados", "meses_flip": "meses até vender no flip", "flip_ate_mes": "faz flips até este mês",
    "reforma_com_acrescimo": "as reformas averbam acréscimo de área (risco do art. 251, §1º, III)",
}


def adicionar_premissas(ap: argparse.ArgumentParser) -> None:
    padrao = Premissas()
    g = ap.add_argument_group("premissas")
    for f in fields(Premissas):
        nome = "--" + f.name.replace("_", "-")
        valor = getattr(padrao, f.name)
        if isinstance(valor, bool):
            g.add_argument(nome, action="store_true", default=valor, help=AJUDA.get(f.name, ""))
        else:
            g.add_argument(nome, type=type(valor), default=valor, help=f"{AJUDA.get(f.name, '')} [padrão: {valor}]")


def premissas_de(args: argparse.Namespace) -> Premissas:
    return Premissas(**{f.name: getattr(args, f.name) for f in fields(Premissas)})


def _r(v: float | None) -> str:
    return "—" if v is None else f"R$ {v:,.0f}".replace(",", ".")


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1%}".replace(".", ",")


def executar(args: argparse.Namespace) -> int:
    p = premissas_de(args)
    if args.modo == "imovel":
        r = analisar_imovel(p, com_saque=args.com_saque)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2, default=float))
            return 0
        print("\n== Custos na compra ==")
        for k, v in r["custos_compra"].items():
            print(f"  {k:16} {_r(v)}")
        print(f"  {'caixa no dia 1':16} {_r(r['caixa_dia_1'])}   + reforma {_r(p.reforma)}")
        print(f"\n1ª parcela ({p.sistema}, {_pct(p.cet)}): {_r(r['primeira_parcela'])}   aluguel líquido de IR: {_r(r['aluguel_liquido_ano1'])}")
        print("\n== Seu bolso por mês (meio de cada ano) ==")
        print("  " + " | ".join(f"{k}: {_r(v)}" for k, v in r["fluxo_mensal"].items()))
        if r["saque"]:
            s = r["saque"]
            print(f"\n== Saque no mês {p.mes_saque} ==\n  empréstimo {_r(s['emprestimo'])}, quita {_r(s['quita'])}, custos {_r(s['custos'])}, líquido {_r(s['liquido'])}")
        v = r["venda"]
        print(f"\n== Venda no ano {p.anos} ==\n  valor {_r(v['valor'])}, corretagem {_r(v['corretagem'])}, IR ganho de capital {_r(v['ir_ganho_capital'])}, quita {_r(v['quita_divida'])}, líquido {_r(v['liquido'])}")
        print(f"\nIR sobre aluguel no período: {_r(r['ir_aluguel_total'])}")
        print(f"Máximo de dinheiro seu aplicado: {_r(r['exposicao_maxima'])}   Lucro total: {_r(r['lucro_total'])}   TIR: {_pct(r['tir_aa'])}")
    elif args.modo == "estrategias":
        res = {pol: simular(p, pol) for pol in ("segurar", "saque", "vender")}
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=float))
            return 0
        nomes = {"segurar": "A) comprar e segurar", "saque": "B) saque + próximo imóvel", "vender": "C) flips e depois segurar"}
        print(f"\n{'estratégia':28} {'imóveis':>7} {'vendas':>6} {'seu dinheiro':>14} {'patrimônio':>14} {'dívida':>14} {'fluxo/mês fim':>14} {'TIR':>7}")
        for pol, r in res.items():
            print(f"{nomes[pol]:28} {r['imoveis_final']:>7} {r['vendas']:>6} {_r(r['aporte_total']):>14} {_r(r['patrimonio_liquido']):>14} {_r(r['divida_final']):>14} {_r(r['fluxo_mensal_final']):>14} {_pct(r['tir_aa']):>7}")
        for pol, r in res.items():
            for a in r["alertas"]:
                print(f"  ⚠ {nomes[pol]}: {a}")
        if args.eventos:
            for pol, r in res.items():
                print(f"\n-- eventos {nomes[pol]}")
                for e in r["eventos"]:
                    print(f"  {e}")
    elif args.modo == "empate-saque":
        r = empate_saque(p)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2, default=float))
            return 0
        print(f"\nTIR sem saque: {_pct(r['tir_sem_saque'])}   CET máximo do saque para empatar (desconto {_pct(p.cdi)}): {_pct(r['cet_empate_saque'])}")
        print(f"\n{'CET':>5} {'TIR portab.':>12} {'TIR saque':>10} {'ganho VPL saque':>16} {'saque só vence a portabilidade se CET ≤':>40}")
        for t in r["tabela"]:
            print(f"{_pct(t['cet']):>5} {_pct(t['tir_portabilidade']):>12} {_pct(t['tir_saque']):>10} {_r(t['ganho_vpl_saque']):>16} {_pct(t['saque_so_vence_portabilidade_ate']):>40}")
    print("\nSimulação simplificada; confira as premissas e as limitações no topo de tributarista/simulador.py.")
    return 0
