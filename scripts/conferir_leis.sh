#!/usr/bin/env bash
# Baixa LC 214/2025, LC 116/2003 e CTN do Planalto, ingere no grafo e gera
# relatorio_conferencia.md. Rodar numa máquina com acesso a www.planalto.gov.br.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m tributarista ingerir-padrao
python3 -m tributarista validar
python3 -m tributarista conferir --saida relatorio_conferencia.md || true
echo "Abra relatorio_conferencia.md. Comece pelos itens [ARTIGO DIVERGENTE] e [NÃO ENCONTRADO]."
