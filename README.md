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
  Com ele, a recarga substitui só esses cargos; sem ele, todos os cargos daquele ano/turno/UF.
- A carga avisa quando um número não pôde ser lido (voto, seção, zona, município ou cargo), em vez
  de seguir com um total menor sem dizer nada.
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

Duas tabelas principais, sem nenhum acumulado gravado:

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

### Tabelas derivadas

`python -m election_tally build-derived` gera as tabelas abaixo, e `docker compose up` o executa
antes do Superset. Prefira estas tabelas em consultas e gráficos: elas evitam varrer `votes`.
A primeira execução leva cerca de 15 minutos e baixa cerca de 1 GB do TSE. Depois, só refaz o que
mudou; `--force` refaz tudo, inclusive arquivos que o TSE republicou. Se um download falhar, o
comando avisa e o Superset sobe mesmo assim.

**Resultados**, calculados a partir de `votes`:

| Tabela | Uma linha por | Para que serve |
|---|---|---|
| `national_results` | ano × turno × votável, só presidente | Resultado nacional de presidente |
| `state_results` | ano × turno × cargo × UF × votável | Resultado por UF de presidente, governador, senador e deputados; o exterior é `ZZ` |
| `municipality_results` | ano × turno × cargo × município × votável | Resultado por município de todos os cargos e consultas populares |
| `party_results` | ano × turno × cargo × UF × partido | Votos por partido; some as UFs para o total nacional, como em deputado federal |
| `leader_flows` | nível × transição × município × mais votado na origem e no destino | Sankey de seções e municípios por candidato mais votado |

Em 2014, o arquivo do exterior não traz o partido dos candidatos a presidente. Nas tabelas derivadas ele vem
do mesmo candidato nas outras UFs; em `votes` continua vazio, como no arquivo.
`votes` conta linhas com votos; quem teve zero votos em um local não aparece em `*_results`.
`share_of_valid` é a fração dos votos válidos (nominais e de legenda) naquele local e cargo, e
fica vazia para branco e nulo. `state_results` não traz prefeito nem vereador, e `national_results`
só traz presidente, porque o mesmo número pertence a candidatos diferentes em cada local.

**Comparações entre eleições**, calculadas a partir dos resultados, de `votes` e de `candidates`
(refeitas também quando `election_tally/data/party_renames.csv` muda):

| Tabela | Uma linha por | Para que serve |
|---|---|---|
| `party_names` | nome antigo | Renomeações de partidos (o CSV); `final_name` segue a cadeia até o nome atual |
| `round_shifts` | ano × município × finalista, só presidente | Votos e percentual de cada finalista no 1º e no 2º turno; o exterior é `ZZ` |
| `state_round_shifts` | ano × UF × finalista, só presidente | O mesmo por UF, com o percentual calculado sobre os totais da UF |
| `zone_round_shifts` | ano × UF × zona × finalista, só presidente | O mesmo por zona; o número da zona só é único dentro da UF |
| `blank_null_votes` | ano × turno × cargo × município | Brancos, nulos, válidos e o total, em contagens; `has_second_round` marca quem votou nos dois turnos |
| `candidate_results` | ano × turno × cargo × candidatura | Votos, perfil, se foi eleita e `elected_before` (mesmo nome, cargo e local um mandato antes; aproximado, pois não há CPF) |
| `unit_winners` | ano × cargo × UF ou município, só presidente, governador e prefeito | Mais votado, margem, líder do 1º turno, viradas, continuidade do partido e porte do município |
| `runoff_shifts` | ano × cargo × nível × unidade × finalista, só governador e prefeito | Votos e percentual de cada finalista nos dois turnos, por UF, município e zona |
| `office_seats` | ano × cargo × unidade | Vagas e candidatos da eleição ordinária, sem as suplementares |
| `party_shifts` | nível × cargo × turno × par de eleições × unidade × partido | Votos e percentual do partido na eleição anterior e na atual do mesmo cargo, e a variação |
| `unit_volatility` | nível × cargo × turno × par de eleições × unidade | Índice de Pedersen: metade da soma das variações absolutas de percentual |
| `elected_party` | ano × cargo × UF × partido | Eleitos e votos do 1º turno por partido |
| `municipal_party` | ano × cargo municipal × município × partido | Eleitos e votos do 1º turno, para os gráficos de prefeito e vereador responderem ao filtro de município |
| `senate_seats` | ano × UF × partido | Cadeiras do Senado após cada eleição: os eleitos do ano e os da eleição anterior, pelo partido de eleição |
| `mayor_flows` | transição × município × partido do prefeito na origem e no destino | Sankey do partido dos prefeitos entre 2016, 2020 e 2024 |
| `municipality_shapes` | município × parte do contorno | Contorno de cada município (lista JSON de pares `[longitude, latitude]`) para o mapa por cidade; sem o exterior |

`party_shifts` e `unit_volatility` comparam o mesmo cargo com a eleição quatro anos antes (presidente,
governador, senador e deputados: 2014 → 2018 → 2022 → 2026; prefeito e vereador: 2016 → 2020 → 2024), com
`transition` como `2018 → 2022 T1`. As comparações saem dos anos carregados: um ano novo entra sozinho, e
quando falta um ano no meio, nada é comparado por cima da lacuna. O `level` é `Brasil`, `UF`, `Município`, `Zona` ou `Seção`, e as
colunas `state`, `state_iso` (`BR-SP`, como no mapa do Superset), `municipality_*`, `zone` e `section`
ficam vazias onde não se aplicam. Zonas são identificadas por (`state`, `zone`) e seções por
(`municipality_code`, `zone`, `section`). O percentual é dos votos válidos (nominais e de legenda) da
unidade naquele cargo e turno. Um partido que concorreu em só um dos lados aparece nos dois, com
0 votos onde não concorreu, e votos de candidatos sem partido contam no total mas não têm linha.
Brancos e nulos têm linhas próprias em `party_shifts` (`party` = `Branco` ou `Nulo`), com o percentual sobre todos os
votos da unidade, e não entram em `pedersen_index`. As colunas `share_*_all`, `share_round*_all` e `pedersen_index_all` trazem as
mesmas medidas sobre todos os votos, e os datasets do Superset escolhem entre as duas bases pela coluna `vote_base`
(filtro **Base do percentual**, obrigatório: os datasets têm uma linha por base, e sem ele contariam em dobro;
um gráfico novo sobre esses datasets precisa desse filtro ou de um filtro próprio em `vote_base`, e um teste confere isso).
A unidade precisa ter votos válidos nos dois lados: seção nova ou município criado não entra, e o
2º turno só compara locais que votaram nos dois. Consultas populares e o cargo 25 ficam de fora.
Seção e zona podem ter sido redesenhadas entre eleições: o número é mantido como está.

**Seção só existe para presidente, governador, senador e prefeito** (cargos 1, 3, 5 e 11). Com todos os
cargos a tabela passaria de 150 milhões de linhas (estimativa; com estes quatro, nos anos de 2014 a 2026, são
62 milhões e cerca de 2,5 GB). Os demais cargos chegam
até zona, e `SECTION_OFFICES` em `election_tally/shifts.py` muda isso.

`elected_party`: eleito é `result LIKE 'ELEITO%'`. Em governador e prefeito, onde há uma vaga, vale o
eleito da última eleição do local (a suplementar vence a ordinária; entre iguais, o maior código da
eleição e do turno). O presidente tem `state = 'BR'`. `vote_share` e `seat_share` são relativos à UF,
então **não os some nem os tire a média entre UFs**. Para o total nacional, some `elected` e `votes`
e divida pelo total (`seats_total` e `votes_total` se repetem em todos os partidos da UF), ou use no
Superset a visão de contribuição sobre `elected`.

`senate_seats`: não é a bancada oficial, porque não traz trocas de partido nem suplentes. Começa em 2018,
já que 2014 dependeria de 2010. A partir de 2022, DEM e PSL contam como UNIÃO só nesta tabela.

`mayor_flows`: cidade sem prefeito eleito de um lado vai para o nó `Sem eleito`. Os partidos fora dos
12 com mais prefeitos nas três eleições viram `Outros` em `source` e `target`; `source_party` e
`target_party` guardam o partido real. Os nós levam o ano (`2016 · PT`), então o gráfico não faz ciclos.

`municipality_shapes`: vem de [`election_tally/data/municipality_shapes.csv`](election_tally/data/municipality_shapes.csv)
(0,9 MB; 5.570 municípios em 5.598 anéis, e cada anel é uma polilinha codificada), que acompanha o repositório,
então **não há download no `build-derived`**: se o arquivo faltar ou estiver quebrado, o comando só avisa e o
Superset sobe sem o mapa por cidade. O arquivo é a malha municipal do IBGE, na qualidade mínima, obtida de
`https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?intrarregiao=municipio&formato=application/vnd.geo+json&qualidade=minima`
(só o contorno externo de cada parte; enclaves são descartados). A página da API não informa a licença; trate como
dado aberto do IBGE e cite a fonte (IBGE, Malha Municipal). `python -m election_tally download-municipality-shapes`
baixa a versão mais nova e regrava o arquivo; o `build-derived` refaz a tabela quando o conteúdo do arquivo muda. Ao
construir, o comando imprime, por ano, quantos municípios têm contorno: a malha do IBGE não traz
Boa Esperança do Norte (MT, código 5101837, criado em 2025), que aparece em 2024 e 2026 sem contorno; em 2014–2022 os
5.568 a 5.570 municípios de cada ano casam todos. O exterior (`ZZ`) não tem contorno.

**Nomes de partidos.** Nestas tabelas o partido que só mudou de nome tem sempre o nome atual
(PMDB é MDB, PTN é PODE, PEN é PATRIOTA etc.; veja `party_renames.csv`). **Fusões e incorporações não
são juntadas**: os gráficos mantêm separados DEM e PSL (hoje UNIÃO), PRP (incorporado ao PATRIOTA), PATRIOTA e
PTB (hoje PRD), PHS e PSC (hoje PODE), PPL (hoje PCdoB) e PROS (hoje SOLIDARIEDADE), para mostrar o
que estava na urna. Os nomes são comparados como escritos nos arquivos do TSE.

**Arquivos do TSE**, além dos boletins:

| Tabela | Uma linha por | Para que serve |
|---|---|---|
| `candidates` | candidato × turno | Todos os candidatos, mesmo sem voto: número, nomes, partido, cargo, local, situação final, gênero, cor/raça, escolaridade e ocupação. Eleitos: `result LIKE 'ELEITO%'` |
| `seats` | eleição × cargo × local | Vagas em disputa |
| `coalitions` | turno × cargo × local × partido | Partidos, federações e coligações |
| `electorate_profile` | ano × município × gênero × faixa etária × escolaridade | Eleitorado, somando zonas, estado civil e cor/raça (esta só existe nos anos recentes) |

CPF, e-mail e data de nascimento ficam de fora de propósito. Nas tabelas ligadas a uma disputa,
`country` só é preenchido em disputas nacionais, `state` nas estaduais e municipais e
`municipality_*` só nas municipais. Senadores e deputados federais têm `state`, porque concorrem
por UF. Compare cargos pelo código: a grafia de `office_name` muda entre arquivos.

**Consulta rápida**, calculadas a partir de `sections` e `candidates`:

| Tabela | Uma linha por | Para que serve |
|---|---|---|
| `offices` | código × nome do cargo | Cargos, vices, suplentes e perguntas de plebiscito; um código de pergunta (51 em diante) tem vários nomes |
| `elections` | código da eleição × ano × turno | Descrição de cada eleição; a descrição fica vazia nos códigos sem candidatos, como os de consultas populares |
| `section_locations` | ano × turno × seção | Município, UF e país de cada zona e seção |

Limites conhecidos:

- **Eleitos nem sempre fecham com as vagas.** Quando o resultado é anulado, a cidade fica sem
  prefeito eleito na eleição ordinária, e o vencedor aparece na suplementar. Das 5.568 cidades de
  2016, 5.518 têm eleito na ordinária, 43 só na suplementar e 7 em nenhuma (2020: 5.494, 70 e 4;
  2024: 5.528 de 5.569, 27 e 14).
- **Candidatos com votos fora de `candidates`:** conselheiros distritais de Fernando de Noronha
  não constam nos arquivos do TSE, assim como 3 deputados estaduais de 2014.
- **Amapá em 2020** tem dois arquivos por turno: novembro, com 15 municípios, e dezembro, com
  Macapá, cuja eleição foi adiada. Por isso a carga substitui por ano, turno, UF e eleição.

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

O Superset responde só em `127.0.0.1`. Para abrir na rede local, defina uma senha de verdade e
`SUPERSET_BIND=0.0.0.0` no `.env`: o login padrão é público, ele dá acesso ao SQL Lab, e o DuckDB sabe ler
arquivos, então trate o acesso ao Superset como acesso ao contêiner.

Os painéis são seis:

- **Evolução da apuração**: votos por candidato e por partido, com filtro de recebimento do BU;
- **Entre turnos**: cidades, UFs e zonas em que cada finalista à presidência mais mudou, em % e em votos;
- **Eleitos e bancadas**: eleitos por partido, fluxo de prefeitos, bancadas, Senado e votos × cadeiras;
- **Brancos e nulos**: evolução deles entre anos e entre turnos, por cargo, UF e município;
- **Mudança entre eleições**: variação por partido em UF, cidade, zona e seção, mapas, volatilidade e fluxo dos mais votados;
- **Cargos**: uma aba por cargo, com resultado, território, mudança e candidatos, mais as análises específicas de cada um.

O painel **Cargos** é gerado e seus YAML não são versionados: `office_specs/` descreve cada aba e `bootstrap.sh` roda
`build_office_dashboard.py` a cada inicialização, então uma mudança num spec vale no próximo `docker compose up`.
`python docker/superset/build_office_dashboard.py --check --office governador --db elections.duckdb` só valida, rodando
cada gráfico nos dados. `export-dashboards.sh` deixa esse painel e seus gráficos de fora: um ajuste neles feito na
interface se perde na próxima inicialização, se não for levado para o spec.

A importação nunca apaga: os antigos painéis **Candidatos eleitos** e **Fluxo dos mais votados**, hoje dentro
dos painéis acima, continuam numa instalação existente até serem excluídos na interface. Antes disso,
`export-dashboards.sh` os traria de volta para o repositório.

O painel **Evolução da apuração** tem filtros de ano, turno, cargo, UF, município, zona e seção, e mostra:

- os votos acumulados por candidato ao longo do recebimento dos BUs (a curva da apuração);
- as seções recebidas, acumuladas;
- o resultado consolidado com percentuais, o comparecimento e o número de seções;
- um mapa do comparecimento por UF (gráfico "Country Map" do Superset, que já traz o
  contorno dos estados; não usa mapa de fundo nem chave de serviço externo).

O painel **Mudança entre eleições** traz, na aba Mapa, um mapa por cidade da variação da fatia do partido
(gráfico "deck.gl Polygon" sobre o dataset virtual `party_shift_map`, que junta `party_shifts` ao
`municipality_shapes`). O "Country Map" do Superset 6.1 só tem mapas de países e de estados, nenhum de municípios do
Brasil. O mapa por cidade **não usa mapa de fundo**: não precisa de `MAPBOX_API_KEY` nem altera
`superset_config.py`, e o navegador não contata serviço de mapas algum. A cor vem de faixas fixas simétricas em torno de
zero (vermelho perde, azul ganha), definidas no próprio gráfico, sem JavaScript.

Como as peças se encaixam:

- `docker/superset/Dockerfile` acrescenta `duckdb` e `duckdb-engine` à imagem oficial.
  O DuckDB fica na versão 1.x dos dois lados: um arquivo gravado pelo DuckDB 2.x não abre no 1.x.
- `docker/superset/bootstrap.sh` roda a cada inicialização: migrações, usuário admin e
  importação dos YAML de `docker/superset/dashboards/` (por `import_assets.py`), que
  sobrescreve conexão, datasets, gráficos e painéis já existentes.
- O arquivo DuckDB é montado **somente leitura** em `/data/elections.duckdb` (defina
  `ELECTIONS_DB` no `.env` para outro caminho). Recarregue os dados com o Superset
  parado e suba de novo.
- Antes do Superset, o serviço `prepare-data` roda uma vez e termina: ele executa
  `python -m election_tally build-derived`, que gera as tabelas calculadas a partir dos dados
  (veja [Tabelas derivadas](#tabelas-derivadas)) e não refaz o que já está em dia.
  Só esse serviço monta o arquivo com escrita, e novas tabelas derivadas entram nesse comando.
  Se ele precisar reconstruir algo, pare o Superset antes (`docker compose stop superset`).
- Os metadados do próprio Superset (usuários, gráficos extras) ficam no volume `superset_home`.

**Testando com dados sintéticos.** Para testar os painéis e o `build-derived` sem o banco real, há um
dataset inventado, centenas de vezes menor (cerca de 760 mil linhas em `votes` e 30 MB):

```bash
python -m election_tally load --synthetic      # ou: pipeline --synthetic; grava em ./synthetic.duckdb
python -m election_tally build-derived --db synthetic.duckdb
cd docker && ELECTIONS_DB=../synthetic.duckdb docker compose up
```

Os lugares são reais, para os mapas funcionarem: as 27 UFs, cerca de 70 municípios e 5 cidades do exterior. Os
anos (2206 a 2218), os candidatos (`CANDIDATO 13`), os partidos (`P-A` a `P-O`) e todos os votos são inventados.
Cargos, turnos, zonas, seções e tipos de voto seguem a estrutura real. A mesma `--seed` gera os mesmos dados.

- Os dados reais e os sintéticos não se misturam: `--synthetic` recusa um arquivo com outros dados, e a carga real
  recusa um arquivo sintético. Rodar `--synthetic` de novo apaga as tabelas do arquivo e recria tudo.
- O gerador grava direto nas tabelas, então não testa o download nem a leitura dos arquivos do TSE.
- Os painéis abrem vazios onde o filtro padrão aponta para dados reais (`2026`, `LULA`, `PT`, `2022 → 2026`): troque
  o filtro. Os textos fixos dos painéis continuam citando anos e partidos reais.

**Editando os painéis.** Como a importação sobrescreve os painéis do repositório a cada
inicialização, edições feitas na interface se perdem se não forem salvas de volta:

```bash
docker/superset/export-dashboards.sh    # grava em docker/superset/dashboards/; revise e faça commit
```

O script salva todos os painéis, gráficos, datasets, conexões e consultas salvas, mesmo os que
nenhum painel usa. Os nomes dos arquivos carregam ids internos e podem mudar entre exportações.

Para montar gráficos à mão, a curva da apuração é um gráfico de série temporal sobre
`votes`: eixo `bu_received_at` (granularidade de minuto), métrica `SUM(vote_count)`,
dimensão `candidate_name` e, em *Advanced Analytics → Rolling window*, a soma cumulativa.

## Como suportar um ano novo (ou um layout que mudou)

As [tabelas derivadas](#tabelas-derivadas) acompanham sozinhas: depois de carregar o ano, o próximo
`docker compose up` (ou `build-derived`) as completa, com o Superset parado. Se o TSE ainda não
tiver publicado um arquivo do ano, o comando avisa e tenta de novo na execução seguinte. Um ano já
carregado não é baixado de novo: se `candidates` foi carregado antes do resultado final (entre os
turnos, por exemplo), rode `build-derived --force`. Coluna renomeada nesses arquivos vira `NULL`, e a
carga lista as colunas sem correspondência: confira essa lista após um ano novo. Para ajustar,
edite `TABLES` em `election_tally/tse_tables.py` e aumente `version`.

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
