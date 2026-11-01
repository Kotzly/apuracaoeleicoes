"""Vereador: base spec plus the benches of the city councils, candidates per seat and the spread of the parties."""

from .common import (CANDIDATE_RESULTS, OFFICE_SEATS, bar, candidates_block, change_block, legislature_block, line,
                     metric, note, office, result_block, subtab, table, territory_block, where)

CODE = 13

BENCH_NOTE = (
    "Como ler: são 5.569 câmaras municipais, e cada município é um distrito eleitoral próprio, com de 9 a 55 "
    "cadeiras conforme a população. As cadeiras são divididas entre os partidos município a município, pelo "
    "quociente partidário (QP) e as sobras; por isso os totais por partido são a soma de milhares de disputas "
    "pequenas. Só há eleições municipais em 2016, 2020 e 2024; os gráficos de séries históricas têm três pontos. "
    "Os partidos aparecem com o nome atual, mesmo nas eleições em que existiam com outro nome ou antes de uma "
    "fusão. Votos de legenda são os dados ao partido, sem candidato. A reeleição é aproximada: cruza-se o nome "
    "completo no mesmo cargo e município, pois o TSE muda o identificador a cada eleição e não há CPF; homônimos e "
    "mudanças de nome distorcem o resultado. Não há 2º turno para este cargo. A tabela de puxadores ordena pelo "
    "número bruto de votos, o que favorece as capitais e as maiores cidades; para a força relativa dentro do "
    "município, use a tabela de maior percentual dos votos válidos. Nos filtros, escolha UF e Município para "
    "olhar uma câmara só.")

OFFICE = office("Vereador", CODE, 8000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block()),
    subtab("Bancadas",
           note("bench_note", BENCH_NOTE, height=16),
           *legislature_block(municipal=True),
           [table("vote_getters_share", "Vereadores eleitos com maior percentual dos votos válidos", CANDIDATE_RESULTS,
                  ["election_year", "state", "label", "candidate_name", "party_current", "votes", "pct_of_valid"],
                  order=[("pct_of_valid", False)], filters=[where("elected AND votes >= 1000")], row_limit=500,
                  height=60)],
           [bar("candidates_per_seat_state", "Candidatos por vaga em cada UF", OFFICE_SEATS, "uf",
                [metric("SUM(candidates) * 1.0 / NULLIF(SUM(seats), 0)", "Candidatos por vaga")], row_limit=30,
                y_format=".1f", ignore=("partido",)),
            bar("seats_by_state", "Cadeiras de vereador em cada UF", OFFICE_SEATS, "uf", [metric("SUM(seats)", "Cadeiras")],
                row_limit=30, ignore=("partido",))],
           [bar("contested_cities", "Cidades mais disputadas (candidatos por vaga, com ao menos 9 cadeiras)", OFFICE_SEATS,
                "label", [metric("SUM(candidates) * 1.0 / NULLIF(SUM(seats), 0)", "Candidatos por vaga")], row_limit=30,
                y_format=".1f", horizontal=True, filters=[where("seats >= 9")], ignore=("partido",), height=70),
            bar("largest_councils", "Maiores câmaras: cadeiras e candidatos", OFFICE_SEATS, "label",
                [metric("SUM(seats)", "Cadeiras"), metric("SUM(candidates)", "Candidatos")], row_limit=30,
                horizontal=True, sort="Cadeiras", ignore=("partido",), height=70)],
           [bar("parties_cities", "Em quantos municípios cada partido elegeu vereador", CANDIDATE_RESULTS, "party_current",
                [metric("COUNT(DISTINCT municipality_code)", "Municípios com eleito")], row_limit=35,
                filters=[where("elected")]),
            line("parties_cities_line", "Municípios com vereador eleito, por partido", CANDIDATE_RESULTS, "election_year",
                 [metric("COUNT(DISTINCT municipality_code)", "Municípios com eleito")], groupby=("party_current",),
                 y_format=".0f", filters=[where("elected")])],
           ),
])
