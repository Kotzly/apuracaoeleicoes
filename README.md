# apuracaoeleicoes

Reconstrói a **evolução da apuração** das eleições brasileiras a partir dos
boletins de urna (BU) publicados pelo TSE, por Brasil, UF, município e zona
eleitoral, e guarda tudo num arquivo DuckDB pronto para o Superset.

A ideia: cada BU traz o horário em que foi recebido pela Justiça Eleitoral.
Ordenando as seções por esse horário e somando cumulativamente, sai a curva da
apuração em qualquer nível de agregação.

## Anos suportados

| Ano | Arquivo | Evolução? | Observações |
|-----|---------|-----------|-------------|
| 2016 | TXT sem cabeçalho | sim | turno vem do nome do arquivo; recebimento em horário local (convertido) |
| 2018 | CSV | **não** | o TSE não publicou `DT_BU_RECEBIDO` nem `DT_EMISSAO_BU`; só o consolidado |
| 2020 | CSV | sim | alguns nomes com aspas mal escapadas (tratado) |
| 2022 | CSV | sim | mesmo layout de 2020 (não conferido em arquivo real) |
| 2024 | CSV | sim | datas em ISO, números sem aspas |
| 2026 | CSV | sim | presume o layout de 2024 |

2014 e anteriores: o portal não publica boletins de urna nesse formato.

## Uso

```bash
pip install -e .            # ou: pip install duckdb requests

python -m apuracao pipeline --ano 2024                  # baixa e carrega o ano todo
python -m apuracao pipeline --ano 2022 --uf SP,RJ --turno 1 --cargos 1,3
```

Comandos avulsos:

```bash
python -m apuracao baixar   --ano 2022 --uf AC          # só baixa (confere o SHA-512)
python -m apuracao carregar --ano 2022 data/raw/2022/*.zip
python -m apuracao inspecionar --ano 2018 arquivo.zip   # mapeamento de colunas do arquivo
python -m apuracao validar --ciclo ele2022 --eleicao 544 --cargo 1   # soma dos BUs x oficial
```

- Recarregar um ano/turno/UF substitui o que existia; anos diferentes convivem no mesmo banco.
- `--cargos` reduz bastante o tamanho (deputados e vereadores são a maior parte das linhas).
- `validar` compara a soma dos boletins com o JSON oficial da divulgação
  (`resultados.tse.jus.br`); use `--json` para um arquivo já baixado.

## Modelo de dados

Duas tabelas, sem nenhum acumulado guardado:

- **`votos`**: uma linha por seção × cargo × votável (`qt_votos`, `ts_bu_recebido`, ...).
- **`secoes`**: uma linha por seção × cargo (`qt_aptos`, `qt_comparecimento`, horários
  da urna). Fica separada porque esses números se repetem em toda linha de `votos`.

Pontos de atenção:

- **Horários em Brasília.** Abertura, encerramento e emissão vêm em horário local
  da urna; o recebimento vem em horário de Brasília (2020+) ou local (2016). Na carga
  tudo é convertido para o horário de Brasília, pelo fuso da UF (inclui horário de
  verão). Simplificação: oeste do AM e Fernando de Noronha usam o fuso principal da UF;
  o exterior (`ZZ`) não é convertido.
- **Zona ≠ subdivisão de município.** Uma zona pode abranger vários municípios e um
  município pode ter várias zonas. Agrupe zona por `(sg_uf, nr_zona)`.
- **Exterior** (`sg_uf = 'ZZ'`) entra no total do Brasil e em nenhuma UF.
- **Seções não instaladas** aparecem com 0 votos e `nr_votavel` nulo.
- Recebimento ≠ divulgação: a curva é a dos BUs chegando, não a exata do site do TSE.

Exemplos para cada nível (evolução e consolidado) em [`consultas.sql`](consultas.sql).

## Superset

```bash
pip install duckdb-engine
```

Conexão (Settings → Database Connections → Other):

```
duckdb:////caminho/absoluto/eleicoes.duckdb?access_mode=read_only
```

Abra em **read-only**: o DuckDB permite um só processo escrevendo, e o Superset
segura a conexão. Para recarregar dados, pare o Superset (ou gere um arquivo novo
e troque).

Para a curva de evolução: gráfico de série temporal sobre `votos`, eixo
`ts_bu_recebido` (granularidade minuto), métrica `SUM(qt_votos)`, dimensão
`nm_votavel`, filtros de ano/cargo/UF/município/zona, e em *Advanced Analytics*
a opção **cumulative sum**. Assim o mesmo gráfico serve para qualquer nível.

## Como suportar um ano novo (ou um layout que mudou)

Cada ano usa um `Formato` (`apuracao/formatos.py`), que define onde estão os
arquivos, como lê-los e de que coluna bruta sai cada coluna do schema canônico.
Coluna sem correspondência vira `NULL`; coluna obrigatória ausente faz a carga falhar.

1. Rode `python -m apuracao inspecionar --ano AAAA arquivo.zip` num arquivo do ano.
2. Se só mudou o nome de uma coluna, acrescente o alias no `mapa` de `BwebCSV`.
3. Se mudou a forma de ler (separador, cabeçalho, fuso, derivação de coluna),
   crie uma subclasse que sobrescreve só a diferença e registre o ano:

```python
@registrar(2028)
class Bweb2028(BwebCSV):
    delim = "|"
    mapa = {**BwebCSV.mapa, "nr_secao": ["NUM_SECAO"]}
```

Anos sem formato próprio herdam o do ano registrado mais recente anterior a eles.
Uma coluna nova no schema entra em `CANONICO`; tabelas existentes ganham a coluna
com `NULL` nos anos antigos.

## Testes

```bash
pip install pytest && python -m pytest
```

Os testes usam dados sintéticos e amostras reais (AC, 1º turno) de 2016, 2018,
2020 e 2024 em `tests/dados/`.
