# apuracaoeleicoes

Reconstrói a **evolução da apuração** das eleições brasileiras a partir dos
boletins de urna (BU) publicados pelo TSE, por Brasil, UF, município e zona
eleitoral. Tudo fica num arquivo DuckDB, com um painel pronto no Superset.

A ideia: cada BU traz o horário em que foi recebido pela Justiça Eleitoral.
Ordenando as seções por esse horário e somando de forma acumulada, sai a curva
da apuração em qualquer nível de agregação.

## Anos suportados

Todos conferidos com arquivos reais do TSE.

| Ano | Arquivo | Evolução? | Observações |
|-----|---------|-----------|-------------|
| 2014 | TXT sem cabeçalho | **não** | o recebimento do BU traz só a data, sem hora; só o consolidado |
| 2016 | TXT sem cabeçalho | sim | o turno vem do nome do arquivo; recebimento em horário local (convertido) |
| 2018 | CSV | **não** | o TSE não publicou `DT_BU_RECEBIDO` nem `DT_EMISSAO_BU`; só o consolidado |
| 2020 | CSV | sim | alguns nomes com aspas mal escapadas (tratado) |
| 2022 | CSV | sim | mesmo layout de 2020 |
| 2024 | CSV | sim | datas em ISO, números sem aspas |
| 2026 | CSV | sim | mesmo layout de 2024 |

Nos anos sem horário de recebimento (2014 e 2018), `bu_received_at` fica nulo: os totais
valem, mas as curvas de evolução ficam vazias. Antes de 2014 não foi verificado.

## Uso

```bash
pip install -e .            # ou: pip install "duckdb>=1.4.2,<2" requests

python -m election_tally pipeline --year 2024                 # baixa e carrega o ano inteiro
python -m election_tally pipeline --year 2022 --state SP,RJ --round 1 --offices 1,3
```

Cada execução acrescenta ao mesmo `elections.duckdb` (ou ao arquivo de `--db`). Para
vários anos, rode uma vez por ano:

```bash
for ano in 2014 2016 2018 2020 2022 2024 2026; do
    python -m election_tally pipeline --year $ano
done
```

Comandos avulsos:

```bash
python -m election_tally download --year 2022 --state AC      # só baixa (confere o SHA-512)
python -m election_tally load --year 2022 data/raw/2022/*.zip
python -m election_tally inspect --year 2018 arquivo.zip      # mapeamento de colunas do arquivo
python -m election_tally validate --cycle ele2022 --election 544 --office 1   # soma dos BUs x oficial
python -m election_tally download-municipalities                # atualiza a tabela TSE -> IBGE
```

- Os downloads rodam em paralelo, 4 por vez (`--jobs N` muda isso). Se algum falhar, os
  outros terminam e o comando lista o que falhou; rodar de novo baixa só o que falta.
- Recarregar um ano/turno/UF substitui o que havia; anos diferentes convivem no mesmo banco.
- `--offices` reduz bastante o tamanho (deputados e vereadores são a maior parte das linhas).
- `validate` compara a soma dos boletins com o JSON oficial da divulgação
  (`resultados.tse.jus.br`); use `--json` para um arquivo já baixado.

## Modelo de dados

Como a Justiça Eleitoral organiza o eleitorado:

- **Seção**: uma urna, onde vota um grupo fixo de eleitores (algumas centenas). É a menor
  unidade, e cada BU é de uma seção.
- **Local de votação**: o prédio (uma escola, por exemplo), que reúne várias seções.
- **Zona eleitoral**: a unidade administrativa (o cartório eleitoral) que cuida de várias
  seções. Uma zona pode abranger vários municípios pequenos, e uma cidade grande tem várias zonas.

Cada seção pertence a exatamente um município e a uma zona, e as duas informações estão em
toda linha. Por isso o total por município ou UF sai direto das seções, sem passar pela zona.

Duas tabelas, sem nenhum acumulado gravado:

- **`votes`**: uma linha por seção × cargo × votável (`vote_count`, `bu_received_at`, ...).
- **`sections`**: uma linha por seção × cargo (`eligible_voters`, `turnout`, horários da urna).
  Fica separada porque esses números se repetiriam em toda linha de `votes`.

Pontos de atenção:

- **Horários em Brasília.** Abertura, encerramento e emissão vêm no horário local da
  urna; o recebimento vem no horário de Brasília (2020 em diante) ou local (2016). Na
  carga, tudo é convertido para o horário de Brasília pelo fuso da UF (com horário de
  verão). Simplificação: oeste do AM e Fernando de Noronha usam o fuso principal da UF,
  e o exterior (`state = 'ZZ'`) não é convertido.
- **Zona não é subdivisão de município.** Uma zona pode abranger vários municípios, e um
  município pode ter várias zonas. Agrupe zonas por `(state, zone)`.
- **Exterior** (`state = 'ZZ'`) entra no total do Brasil e em nenhuma UF. Ali o "município"
  é a cidade (PARIS, TÓQUIO…), e o TSE não informa o país: as colunas `country` e
  `country_code` (ISO 3166-1 alfa-3) vêm de [`election_tally/data/abroad_cities.csv`](election_tally/data/abroad_cities.csv),
  pelo código da cidade. Seções no Brasil ficam com "Brasil"/"BRA". Se aparecer uma cidade
  que não está na tabela, a carga para e lista o que falta; acrescente a linha e carregue de novo.
- **Código IBGE do município.** O TSE e o IBGE numeram os municípios de forma diferente, e
  mapas por município usam a malha do IBGE. Cada seção no Brasil ganha
  `municipality_ibge_code`, vindo da tabela oficial do TSE "Códigos oficiais de UF e
  municípios segundo o TSE e o IBGE", guardada em
  [`election_tally/data/municipalities.csv`](election_tally/data/municipalities.csv) (gerada
  pelo TSE em 25/10/2026). `download-municipalities` baixa a versão mais nova e regrava essa
  cópia; recarregue os dados depois. Município sem correspondência gera um aviso na carga.
- **Seções não instaladas** aparecem com 0 votos e `candidate_number` nulo; o nome do cargo
  é preenchido pelo código. Os nomes de cargo seguem uma grafia só em todos os anos.
- Recebimento não é divulgação: a curva é a dos BUs chegando, não exatamente a que
  apareceu no site do TSE.

Exemplos de consulta para cada nível (evolução e consolidado) em [`queries.sql`](queries.sql).

## Superset (Docker)

A pasta `docker/` sobe o Superset 6.1 com o driver do DuckDB e carrega os painéis
guardados no repositório a cada inicialização.

```bash
python -m election_tally pipeline --year 2024      # cria ou atualiza ./elections.duckdb
cd docker
cp .env.example .env                               # defina SUPERSET_SECRET_KEY
docker compose up --build                          # http://localhost:8088 (admin / admin)
```

O painel **Evolução da apuração** tem filtros de ano, turno, cargo, UF, município e
zona, e mostra:

- os votos acumulados por candidato ao longo do recebimento dos BUs (a curva da apuração);
- as seções recebidas, acumuladas;
- o resultado consolidado com percentuais, o comparecimento e o número de seções;
- um mapa do comparecimento por UF (gráfico "Country Map" do Superset, que já traz o
  contorno dos estados; não usa mapa de fundo nem chave de serviço externo).

Como as peças se encaixam:

- `docker/superset/Dockerfile` acrescenta `duckdb` e `duckdb-engine` à imagem oficial.
  O DuckDB fica na versão 1.x dos dois lados: um arquivo gravado pelo DuckDB 2.x não abre no 1.x.
- `docker/superset/bootstrap.sh` roda a cada inicialização: migrações, usuário admin e
  importação dos YAML de `docker/superset/dashboards/` (por `import_assets.py`), que
  sobrescreve conexão, datasets, gráficos e painéis já existentes.
- O arquivo DuckDB é montado **somente leitura** em `/data/elections.duckdb` (defina
  `ELECTIONS_DB` no `.env` para outro caminho). Recarregue os dados com o Superset
  parado e suba de novo.
- Os metadados do próprio Superset (usuários, gráficos extras) ficam no volume `superset_home`.

**Editando os painéis.** Como a importação sobrescreve os painéis do repositório a cada
inicialização, edições feitas na interface se perdem se não forem salvas de volta:

```bash
docker/superset/export-dashboards.sh    # grava em docker/superset/dashboards/; revise e faça commit
```

Para montar gráficos à mão, a curva da apuração é um gráfico de série temporal sobre
`votes`: eixo `bu_received_at` (granularidade de minuto), métrica `SUM(vote_count)`,
dimensão `candidate_name` e, em *Advanced Analytics → Rolling window*, a soma cumulativa.

## Como suportar um ano novo (ou um layout que mudou)

Cada ano usa um `Format` (`election_tally/formats.py`), que define onde estão os
arquivos, como lê-los e de que coluna bruta sai cada coluna do schema canônico.
Coluna sem correspondência vira `NULL`; coluna obrigatória ausente faz a carga falhar.

1. Rode `python -m election_tally inspect --year AAAA arquivo.zip` num arquivo do ano.
2. Se só mudou o nome de uma coluna, acrescente o alias no `mapping` de `BwebCSV`.
3. Se mudou a forma de ler (separador, cabeçalho, fuso, derivação de coluna), crie
   uma subclasse que sobrescreve só a diferença e registre o ano:

```python
@register(2028)
class Bweb2028(BwebCSV):
    delim = "|"
    mapping = {**BwebCSV.mapping, "section": ["NUM_SECAO"]}
```

Anos sem formato próprio herdam o do ano registrado mais recente anterior a eles.
Uma coluna nova no schema entra em `CANONICAL`; as tabelas existentes ganham a coluna
com `NULL` nos anos antigos.

## Testes

```bash
pip install pytest && python -m pytest
```

Os testes usam dados sintéticos e amostras reais (AC, 1º turno) de 2016, 2018, 2020,
2022, 2024 e 2026, e do exterior em 2014 e 2022, em `tests/data/`.
