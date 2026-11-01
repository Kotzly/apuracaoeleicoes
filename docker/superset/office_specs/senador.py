"""Senador: base spec plus the Senate sub-tab: seats per party, the renewal of 1/3 and 2/3 and the accumulated composition."""

from .common import (CANDIDATE_RESULTS, ELECTED, OFFICE_SEATS, SENATE_SEATS, bar, candidates_block, change_block, line,
                     metric, note, office, pivot, reelection_line, result_block, reuse, subtab, territory_block, where)

CODE = 5

SENATE_NOTE = (
    "Como ler: o Senado tem 81 cadeiras, 3 por estado, e cada senador cumpre 8 anos. A eleição alterna entre um terço "
    "(uma vaga por estado, 27 no total, como em 2014 e 2022) e dois terços (duas vagas por estado, 54 no total, como "
    "em 2018 e 2026). Em 2018 e em 2026 cada eleitor tem dois votos, um para cada vaga, então os votos somam o dobro "
    "dos eleitores e o percentual de cada candidato é sobre os votos válidos de todas as vagas. Não há 2º turno: "
    "quem tem mais votos ocupa a vaga. A composição acumulada soma a eleição escolhida à anterior, as duas que "
    "preenchem as 81 cadeiras, e só existe de 2018 em diante, quando as duas eleições estão na base. Ela conta o "
    "partido pelo qual o senador foi eleito: trocas de partido posteriores e suplentes que assumiram não aparecem. "
    "DEM e PSL já constam como UNIÃO na composição de 2022 em diante. As vagas por estado vêm das vagas "
    "registradas no TSE para a eleição ordinária, sem as suplementares. A reeleição compara com a eleição de 8 anos antes, a da mesma vaga, "
    "e só existe de 2022 em diante; é aproximada, pelo nome completo no mesmo estado, pois o TSE muda o "
    "identificador a cada eleição e não há CPF.")

OFFICE = office("Senador", CODE, 3000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block()),
    subtab("Senado",
           note("senate_note", SENATE_NOTE, height=22),
           [reuse("Senadores_eleitos_por_ano"), reuse("Cadeiras_por_partido_de_eleicao")],
           [reuse("Senadores_eleitos_tabela")],
           [reelection_line()],
           [line("renewal", "Parcela do Senado renovada em cada eleição (%)", CANDIDATE_RESULTS, "election_year",
                 [ELECTED, metric("100.0 * COUNT(*) FILTER (WHERE elected) / 81", "% das 81 cadeiras")],
                 y_format=".0f", ignore=("partido", "uf", "turno")),
            bar("seats_by_year", "Vagas em disputa e candidaturas por eleição", OFFICE_SEATS, "election_year",
                [metric("SUM(seats)", "Vagas"), metric("SUM(candidates)", "Candidaturas")],
                history=True, sort="Vagas", ignore=("partido", "uf"))],
           [pivot("seats_state_year", "Vagas por UF e eleição (1 ou 2 por estado)", OFFICE_SEATS, ["uf"], ["election_year"],
                  metric("SUM(seats)", "Vagas"), history=True, ignore=("partido",), height=80)],
           [bar("composition", "Composição do Senado por partido na eleição escolhida", SENATE_SEATS, "party_current",
                [metric("SUM(seats_this_election)", "Eleitos nesta eleição"),
                 metric("SUM(seats_previous_election)", "Eleitos na eleição anterior")],
                horizontal=True, stack="Stack", sort="Eleitos nesta eleição", row_limit=30,
                filters=[where("seats > 0")], pinned=False),
            line("composition_line", "Cadeiras do Senado por partido ao longo do tempo", SENATE_SEATS, "election_year",
                 [metric("SUM(seats)", "Cadeiras")], groupby=("party_current",), y_format=".0f",
                 filters=[where("seats > 0")], ignore=("uf",), pinned=False)],
           [pivot("composition_state", "Senadores por UF e partido na eleição escolhida", SENATE_SEATS, ["uf"],
                  ["party_current"], metric("SUM(seats)", "Cadeiras"), filters=[where("seats > 0")], height=80, pinned=False)]),
])
