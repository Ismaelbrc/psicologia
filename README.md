# Tributarista

Grafo de conhecimento da tributação imobiliária brasileira: **construção, reforma, aluguel, compra e venda, propriedade e sucessão**. O grafo cobre o regime atual e a transição da Reforma Tributária (EC 132/2023 e LC 214/2025) até 2033.

Não tem dependências externas; usa apenas Python ≥ 3.10 e a biblioteca padrão.

```bash
python -m tributarista operacoes                    # lista as operações modeladas
python -m tributarista consultar locacao_residencial --perfil pf
python -m tributarista consultar construcao_empreitada --perfil pj_lucro_presumido --data 2030-01-01
python -m tributarista consultar reforma_obra_propria --perfil pf
python -m tributarista buscar holding
python -m tributarista explicar itbi
python -m tributarista caminho reforma_obra_propria alienacao_imovel
python -m tributarista mermaid locacao_imovel > locacao.mmd
python -m tributarista pendencias                   # fila do que precisa ser conferido
python -m tributarista validar
python -m unittest discover -s tests
```

## O que a consulta responde

Para uma **operação**, um **perfil** (PF, lucro real, lucro presumido, Simples) e uma **data**, a consulta devolve:

- **tributos que incidem e que não incidem**, com nota prática e fonte legal;
- **benefícios e reduções** aplicáveis (isenções de ganho de capital, dedução de materiais no ISS, RET, redutores do IBS/CBS...);
- **obrigações acessórias** (CNO, SERO, CND de obra, carnê-leão, GCAP, DIMOB, CIB...);
- **jurisprudência** vinculante ou repetitiva relevante;
- **itens relacionados** (efeito da reforma do imóvel no ganho de capital futuro, revisão do IPTU, planejamento via holding...).

A data importa porque as regras mudam com a transição da reforma. Pelo cronograma:

- **2026:** fase de teste do IBS/CBS.
- **2027:** fim do PIS/Cofins.
- **2029 a 2032:** redução gradual do ISS e do ICMS.
- **2033:** extinção do ISS e do ICMS.

Consultar a mesma operação em 2025 e em 2033 dá respostas diferentes.

Operações especializadas herdam as regras das mais gerais. Por exemplo:

- `locacao_residencial` herda de `locacao_imovel`;
- `incorporacao_imobiliaria` herda de `construcao_obra_propria` e de `alienacao_imovel`.

## Simulador de investimento (pessoa física)

```bash
python -m tributarista simular imovel                       # 1 imóvel: custos, parcela, fluxo mensal, venda, TIR
python -m tributarista simular imovel --com-saque --cet-saque 0.11
python -m tributarista simular estrategias                  # A) segurar  B) saque + próximo  C) flips e depois segurar
python -m tributarista simular estrategias --valor-pos-reforma 420000 --reforma-com-acrescimo --eventos
python -m tributarista simular empate-saque                 # CET máximo do saque x portabilidade
python -m tributarista simular imovel --help                # lista todas as premissas
```

Todas as premissas são editáveis por linha de comando: preço, entrada, ITBI (padrão de Goiânia, 2%), registro, CET, prazo, Price ou SAC, reforma, valor pós-reforma, aluguel, vacância, administração, IR, saque (LTV, CET, IOF, mês), valorização, corretagem, horizonte, limite de imóveis e prazos do flip. Use `--json` para ter a saída estruturada.

A estratégia de flips emite alertas quando a simulação aciona os gatilhos de IBS/CBS da pessoa física (LC 214, art. 251):

- mais de 3 vendas no ano;
- 2 vendas em 5 anos com obra de acréscimo (`--reforma-com-acrescimo`);
- mais de 3 imóveis alugados com receita acima de R$ 240 mil por ano.

As limitações do modelo estão descritas no topo de `tributarista/simulador.py`.

## Modelo

| Tipo de nó | Exemplos |
|---|---|
| `tributo` | ISS, ITBI, IPTU, IRPF, CBS, IBS, INSS de obra, CPRB |
| `operacao` | construção por empreitada, obra própria, reforma, locação, venda, integralização |
| `perfil` | `pf`, `pj_lucro_real`, `pj_lucro_presumido`, `pj_simples` |
| `norma` / `dispositivo` | CF art. 156, CTN art. 38, LC 116 item 7.02, LC 214 art. 251 |
| `beneficio` / `regime` | isenção de 180 dias, redutor social, RET |
| `obrigacao` | CNO, CND de obra, carnê-leão, DIMOB |
| `jurisprudencia` | STF Tema 1.124, STJ Tema 1.113, STF Tema 796 |

As principais relações são:

- `incide_em` e `nao_incide_em`;
- `reduz` e `isenta`;
- `exige`;
- `substitui` (ex.: CBS → PIS/Cofins);
- `especializa`;
- `interpreta`;
- `fundamentado_em` e `parte_de`.

Cada aresta pode ter `condicao` (perfis/operações), `vigencia` (início/fim), `fontes`, `nota` e `status`.

Os dados ficam em `tributarista/dados/*.json`, separados por tema. Para acrescentar uma regra, basta editar o JSON e rodar `validar`.

## Limites (leia antes de usar)

1. **Isto não é "todo o código tributário".** É uma camada curada do que importa no mercado imobiliário: cerca de 150 nós e 200 arestas. O sistema tributário brasileiro não é um código único; são a CF, o CTN, dezenas de leis federais e as leis de 27 Estados e 5.570 Municípios.
2. **Nada aqui substitui o texto oficial.** Cerca de um quarto das afirmações está marcado como `verificar` ou `controverso`, principalmente os detalhes da LC 214/2025: números de artigo, limites da PF contribuinte, percentuais de transição e o regime do RET pós-2027. Rode `python -m tributarista pendencias` e confira cada item.
3. **Alíquotas municipais e estaduais não estão no grafo.** Isso vale para o ISS (2% a 5%), o ITBI, o IPTU e o ITCMD. O grafo diz o que incide e onde, não quanto em cada cidade.
4. Os dados foram montados sem acesso ao Planalto; o ambiente em que este repositório foi criado bloqueia o domínio. Por isso existe a ingestão abaixo.

## Ingestão do texto integral das leis

Para ter o texto legal completo artigo por artigo, e não só a camada curada, rode num ambiente com acesso ao Planalto:

```bash
python -m tributarista ingerir https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp214.htm --norma lc214_2025
python -m tributarista ingerir https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp116.htm --norma lc116_2003
python -m tributarista ingerir https://www.planalto.gov.br/ccivil_03/leis/l5172compilado.htm --norma ctn
python -m tributarista explicar 'lc214_2025#art251'
```

Cada artigo vira um nó `norma#artN` em `tributarista/dados/ingeridos/`. Trechos tachados, que o Planalto usa para redação revogada, são descartados.

### Conferir os itens pendentes contra o texto

```bash
./scripts/conferir_leis.sh        # ingerir-padrao (LC 214, LC 116, CTN) + validar + conferir
```

O script gera `relatorio_conferencia.md`. Para cada item curado listado em `tributarista/conferencia.json`, ele procura o artigo que reúne os termos da afirmação e classifica o resultado:

- **ARTIGO DIVERGENTE:** os termos existem, mas em outro artigo. O número do artigo no grafo provavelmente está errado.
- **NÃO ENCONTRADO:** nenhum artigo reúne os termos. A afirmação pode estar errada ou redigida de outro jeito.
- **OK:** os termos estão no artigo esperado, e o relatório mostra o trecho. **Isso não prova a afirmação**; só diz onde ler.

Depois de ler o trecho e corrigir o JSON em `tributarista/dados/`, troque `"status": "verificar"` por `"consolidado"`. O relatório também lista as pendências que dependem de normas fora dessas três leis (IN RFB 2.021/2021, Lei 7.739/1989, RIR/2018, LC 123...).

Se o download falhar, salve o HTML pelo navegador e rode `python -m tributarista ingerir arquivo.htm --norma lc214_2025`.
