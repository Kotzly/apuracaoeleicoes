"""Prefeito: base spec plus the mayor flows, city size, the runoff in big cities, flips and continuity."""

from .common import (UNIT_WINNERS, aggregated_table, bar, candidates_block, change_block, flips_table, line, metric, note,
                     office, reelection_line, result_block, reuse, round_blank_block, runoff_block, subtab, table,
                     territory_block, where)

CODE = 11

GOVERNING = "continuity_party"  # who ended up with the seat, which is not always who won the last round

KEPT = ("100.0 * COUNT(*) FILTER (WHERE same_party_as_previous) / "
        "NULLIF(COUNT(*) FILTER (WHERE same_party_as_previous IS NOT NULL), 0)")

FLOW_NOTE = (
    "Os fluxos mostram, entre duas eleições municipais, de que partido para que partido passou cada prefeitura, "
    "conforme o partido do prefeito eleito em cada ano.")

BAND_NOTE = (
    "Porte é o número de eleitores aptos do município na eleição: até 5 mil, 5 a 20 mil, 20 a 50 mil, 50 a 200 mil e "
    "mais de 200 mil. O filtro de ano vale para estes gráficos; escolha uma eleição para comparar os portes.")

RUNOFF_NOTE = (
    "Só municípios com mais de 200 mil eleitores podem ter 2º turno, e apenas quando ninguém passa de 50% dos votos "
    "válidos no 1º turno: são cerca de 50 a 60 cidades por eleição (57 em 2016, 58 em 2020 e 51 em 2024). A variação é "
    "em pontos percentuais da participação de cada finalista entre os dois turnos. Os níveis são município e zona; "
    "não há 2º turno por UF.")

CHANGE_NOTE = (
    "Como ler: o partido que governa é o de quem ficou com o cargo, o que pode diferir do mais votado quando houve "
    "eleição suplementar (que pode mudar quem ocupa a prefeitura depois do pleito) ou candidatura barrada. A "
    "continuidade compara com a eleição municipal anterior, quatro anos antes, e não existe em 2016, o primeiro ano "
    "da base. Viradas (líder do 1º turno derrotado no 2º) só ocorrem nas cidades com 2º turno. A margem é a diferença "
    "em pontos percentuais entre o 1º e o 2º colocado no último turno. Reeleição é aproximada: cruza-se o nome "
    "completo no mesmo cargo e município, pois o TSE muda o identificador a cada eleição e não há CPF.")

OFFICE = office("Prefeito", CODE, 7000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block(seat_competition=False)),
    subtab("Fluxo de prefeitos",
           note("flow_note", FLOW_NOTE, height=6),
           [reuse("Prefeitos_de_um_partido_para_outro")],
           [reuse("Fluxos_entre_partidos_de_prefeitos")]),
    subtab("Porte do município",
           note("band_note", BAND_NOTE, height=8),
           [bar("band_winners", "Partido que governa, por porte do município", UNIT_WINNERS, "size_band",
                [metric("COUNT(*)", "Municípios")], groupby=(GOVERNING,), stack="Stack", sort="Municípios",
                ignore=("partido",), row_limit=300, width=12)],
           [bar("band_margin", "Margem média de vitória por porte (p.p.)", UNIT_WINNERS, "size_band",
                [metric("AVG(margin_pp)", "Margem média"), metric("MIN(margin_pp)", "Menor margem")],
                y_format=".1f", sort="Margem média", ignore=("partido",)),
            bar("band_continuity", "Continuidade do partido por porte (%)", UNIT_WINNERS, "size_band",
                [metric(KEPT, "% que manteve o partido")],
                y_format=".1f", filters=[where("same_party_as_previous IS NOT NULL")], ignore=("partido",))],
           [aggregated_table("band_table", "Eleições por porte do município", UNIT_WINNERS, ["size_band"],
                             [metric("COUNT(*)", "Municípios"),
                              metric("AVG(winner_pct)", "% médio do eleito"),
                              metric("AVG(margin_pp)", "Margem média (p.p.)"),
                              metric("COUNT(*) FILTER (WHERE has_second_round)", "Com 2º turno"),
                              metric("COUNT(*) FILTER (WHERE flipped)", "Viradas"),
                              metric(KEPT, "% que manteve o partido"),
                              metric("MIN(eligible_voters)", "Menor eleitorado")],
                             order_by=metric("MIN(eligible_voters)", "Menor eleitorado"), ignore=("partido",))]),
    subtab("Entre turnos",
           note("runoff_note", RUNOFF_NOTE, height=10),
           *runoff_block(levels=("Município", "Zona")),
           *round_blank_block()),
    subtab("Viradas e continuidade",
           note("flips_note", CHANGE_NOTE, height=16),
           [flips_table()],
           [line("flips_by_year", "Municípios em que o líder do 1º turno perdeu", UNIT_WINNERS, "election_year",
                 [metric("COUNT(*) FILTER (WHERE flipped)", "Viradas"),
                  metric("COUNT(*) FILTER (WHERE has_second_round)", "Municípios com 2º turno")],
                 y_format=".0f", ignore=("partido", "uf")),
            line("continuity_by_year", "Continuidade: municípios que mantiveram o partido", UNIT_WINNERS, "election_year",
                 [metric(KEPT, "% que manteve o partido")],
                 filters=[where("same_party_as_previous IS NOT NULL")], ignore=("partido", "uf"))],
           [bar("governing_parties", "Partido que governa os municípios, por eleição", UNIT_WINNERS, "election_year",
                [metric("COUNT(*)", "Municípios")], groupby=(GOVERNING,), stack="Stack", history=True, sort="Municípios",
                ignore=("partido",), row_limit=500, width=12)],
           [reelection_line(),
            line("margin_by_year", "Margem de vitória (p.p.)", UNIT_WINNERS, "election_year",
                 [metric("AVG(margin_pp)", "Margem média"), metric("MIN(margin_pp)", "Menor margem")],
                 ignore=("partido", "uf"))],
           [table("closest_races", "Margem de vitória por município", UNIT_WINNERS,
                  ["election_year", "state", "label", "winner_name", "party_current", "winner_pct", "runner_up_name",
                   "runner_up_party", "margin_pp", "has_second_round"],
                  order=[("margin_pp", True)], history=True, height=60)]),
])
