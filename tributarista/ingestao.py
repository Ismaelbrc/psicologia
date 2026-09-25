"""Ingestão de textos legais (formato HTML do Planalto) como nós `dispositivo`.

Cada artigo vira um nó `{norma}#art{N}` com o texto integral em `descricao`,
ligado à norma por `parte_de`. Trechos tachados (<strike>/<s>/<del>), que no
Planalto indicam redação revogada, são descartados.

O resultado é gravado em `dados/ingeridos/<norma>.json` e carregado junto com
a camada curada. Os nós curados continuam sendo a fonte das regras; o texto
ingerido serve para consulta e conferência dos itens marcados "verificar".
"""

from __future__ import annotations

import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .grafo import DADOS_DIR

TAGS_BLOCO = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}
TAGS_REVOGADO = {"strike", "s", "del"}

RE_ARTIGO = re.compile(
    r"^\s*Art\.\s*(\d+(?:\.\d{3})*)\s*(?:º|°|o)?\s*(?:-\s*([A-Z]{1,2}))?\s*[\.\-–]?\s+",
    re.MULTILINE,
)


class _Extrator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._revogado = 0
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in TAGS_REVOGADO:
            self._revogado += 1
        elif tag in ("script", "style"):
            self._ignorar += 1
        elif tag in TAGS_BLOCO:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in TAGS_REVOGADO:
            self._revogado = max(0, self._revogado - 1)
        elif tag in ("script", "style"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in TAGS_BLOCO:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._revogado and not self._ignorar:
            self.partes.append(data)


def html_para_texto(html: str) -> str:
    ex = _Extrator()
    ex.feed(html)
    texto = "".join(ex.partes).replace("\xa0", " ")
    linhas = [re.sub(r"[ \t]+", " ", ln).strip() for ln in texto.splitlines()]
    return "\n".join(ln for ln in linhas if ln)


RE_ANEXO = re.compile(r"^\s*(ANEXO\b|Lista de servi[çc]os anexa)", re.MULTILINE | re.IGNORECASE)


def extrair_artigos(texto: str) -> list[tuple[str, str]]:
    """Retorna [(rotulo, texto)] com rótulo como '8', '8-A', '251' e, se houver, 'anexo'.

    Anexos (ex.: lista de serviços da LC 116) vêm depois do último artigo e
    são separados para não inflarem o texto dele.
    """
    anexo = ""
    primeiro = RE_ARTIGO.search(texto)
    if primeiro:
        m_anexo = RE_ANEXO.search(texto, primeiro.end())
        if m_anexo:
            texto, anexo = texto[: m_anexo.start()], texto[m_anexo.start() :].strip()
    artigos = _artigos(texto)
    if anexo:
        artigos.append(("anexo", anexo))
    return artigos


def _artigos(texto: str) -> list[tuple[str, str]]:
    marcas = list(RE_ARTIGO.finditer(texto))
    artigos: dict[str, str] = {}
    for i, m in enumerate(marcas):
        num = m.group(1).replace(".", "")
        rotulo = f"{num}-{m.group(2)}" if m.group(2) else num
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        corpo = texto[m.start() : fim].strip()
        # Mantém a primeira ocorrência: repetições costumam vir de citações/anexos.
        artigos.setdefault(rotulo, corpo)
    return list(artigos.items())


def decodificar(conteudo: bytes) -> str:
    for enc in ("utf-8", "windows-1252", "latin-1"):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("utf-8", errors="replace")


def ler_fonte(fonte: str) -> str:
    if re.match(r"^https?://", fonte):
        req = urllib.request.Request(fonte, headers={"User-Agent": "Mozilla/5.0 (compatible; tributarista/0.1)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return decodificar(r.read())
    return decodificar(Path(fonte).read_bytes())


def ingerir(fonte: str, norma_id: str, nome_norma: str | None = None, destino: Path | None = None) -> dict:
    artigos = extrair_artigos(html_para_texto(ler_fonte(fonte)))
    if not artigos:
        raise ValueError(f"nenhum artigo reconhecido em {fonte}")
    nos, arestas = [], []
    if nome_norma:
        nos.append({"id": norma_id, "tipo": "norma", "nome": nome_norma, "url": fonte})
    for rotulo, corpo in artigos:
        anexo = rotulo == "anexo"
        id_ = f"{norma_id}#anexo" if anexo else f"{norma_id}#art{rotulo}"
        nome = f"{norma_id} anexo" if anexo else f"{norma_id} art. {rotulo}"
        nos.append({"id": id_, "tipo": "dispositivo", "nome": nome, "descricao": corpo, "ingerido": True})
        arestas.append({"de": id_, "rel": "parte_de", "para": norma_id})
    dados = {"fonte": fonte, "nos": nos, "arestas": arestas}
    destino = destino or (DADOS_DIR / "ingeridos")
    destino.mkdir(parents=True, exist_ok=True)
    (destino / f"{norma_id}.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return dados
