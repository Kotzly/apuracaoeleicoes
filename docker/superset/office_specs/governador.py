"""Governador: base spec plus the runoff between rounds and the flips and continuity of each state."""

from .common import (UNIT_WINNERS, bar, candidates_block, change_block, flips_table, line, metric, note, office,
                     reelection_line, result_block, round_blank_block, runoff_block, subtab, table, territory_block,
                     where)

CODE = 3

GOVERNING = "continuity_party"  # who ended up with the seat, which is not always who won the last round

CHANGE_NOTE = (
    "Como ler: o partido que governa é o de quem ficou com o cargo, o que pode diferir do mais votado quando houve "
    "eleição suplementar ou candidatura barrada. A continuidade compara com a eleição anterior, quatro anos antes, e "
    "não existe em 2014, o primeiro ano da base. Viradas só podem ocorrer onde houve 2º turno (14 estados em 2014 e "
    "2018, 12 em 2022 e 6 em 2026). A margem é a diferença em pontos percentuais entre o 1º e o 2º colocado no "
    "último turno. Reeleição é aproximada: cruza-se o nome completo no mesmo cargo e estado, pois o TSE muda o "
    "identificador a cada eleição e não há CPF.")

RUNOFF_NOTE = (
    "Só os estados em que ninguém passou de 50% dos votos válidos no 1º turno têm 2º turno; os demais não aparecem "
    "nestes gráficos. A variação é em pontos percentuais da participação de cada finalista entre os dois turnos.")

OFFICE = office("Governador", CODE, 2000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block(seat_competition=False)),
    subtab("Entre turnos",
           note("runoff_note", RUNOFF_NOTE, height=8),
           *runoff_block(),
           *round_blank_block()),
    subtab("Viradas e continuidade",
           note("flips_note", CHANGE_NOTE, height=14),
           [flips_table()],
           [line("flips_by_year", "Estados em que o líder do 1º turno perdeu", UNIT_WINNERS, "election_year",
                 [metric("COUNT(*) FILTER (WHERE flipped)", "Viradas"),
                  metric("COUNT(*) FILTER (WHERE has_second_round)", "Estados com 2º turno")],
                 y_format=".0f", ignore=("partido", "uf")),
            bar("governing_parties", "Partido que governa os estados, por eleição", UNIT_WINNERS, "election_year",
                [metric("COUNT(*)", "Estados")], groupby=(GOVERNING,), stack="Stack", history=True, sort="Estados",
                ignore=("partido",), row_limit=300)],
           [table("governing_table", "Partido que governa cada UF, por eleição", UNIT_WINNERS,
                  ["election_year", "state", GOVERNING, "winner_name", "party_current", "previous_party",
                   "same_party_as_previous", "has_second_round"],
                  order=[("election_year", False), ("state", True)], history=True, height=70)],
           [line("continuity_by_year", "Continuidade: estados que mantiveram o partido", UNIT_WINNERS, "election_year",
                 [metric("100.0 * COUNT(*) FILTER (WHERE same_party_as_previous) / "
                         "NULLIF(COUNT(*) FILTER (WHERE same_party_as_previous IS NOT NULL), 0)",
                         "% que manteve o partido")],
                 filters=[where("same_party_as_previous IS NOT NULL")], ignore=("partido", "uf")),
            bar("continuity_by_state", "Quantas vezes cada UF manteve ou trocou o partido", UNIT_WINNERS, "state",
                [metric("COUNT(*) FILTER (WHERE same_party_as_previous)", "Manteve"),
                 metric("COUNT(*) FILTER (WHERE NOT same_party_as_previous)", "Trocou")],
                stack="Stack", history=True, sort="Manteve", row_limit=30, ignore=("partido", "uf"))],
           [reelection_line(),
            line("margin_by_year", "Margem de vitória (p.p.)", UNIT_WINNERS, "election_year",
                 [metric("AVG(margin_pp)", "Margem média"), metric("MIN(margin_pp)", "Menor margem")],
                 ignore=("partido", "uf"))],
           [table("closest_races", "Margem de vitória por UF", UNIT_WINNERS,
                  ["election_year", "state", "winner_name", "party_current", "winner_pct", "runner_up_name",
                   "runner_up_party", "margin_pp", "has_second_round"],
                  order=[("margin_pp", True)], history=True, height=60)]),
])
