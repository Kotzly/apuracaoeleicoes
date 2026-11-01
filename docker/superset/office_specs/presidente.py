"""Presidente: base spec plus the between-rounds charts, the flow of the most voted and the winner of each UF."""

from .common import (UNIT_WINNERS, candidates_block, change_block, metric, note, office, pivot, result_block, reuse,
                     round_blank_block, subtab, table, territory_block, where)

CODE = 1

# The 2nd round has the same two finalists everywhere, so these charts already belong to the president.
_BETWEEN_ROUNDS = [
    ["Cidades_variacao_pct", "Cidades_variacao_votos", "Maiores_altas_pct", "Maiores_quedas_pct",
     "Maiores_altas_votos", "Maiores_quedas_votos", "Distribuicao_da_variacao_pct",
     "Distribuicao_da_variacao_votos"],
    ["UF_por_variacao_pct", "UF_por_variacao_votos", "UF_entre_turnos_maiores_altas_pct",
     "UF_entre_turnos_maiores_quedas_pct", "UF_entre_turnos_maiores_altas_votos",
     "UF_entre_turnos_maiores_quedas_votos", "UF_distribuicao_da_variacao_pct", "UF_distribuicao_da_variacao_votos"],
    ["Zonas_por_variacao_pct", "Zonas_por_variacao_votos", "Zonas_entre_turnos_maiores_altas_pct",
     "Zonas_entre_turnos_maiores_quedas_pct", "Zonas_entre_turnos_maiores_altas_votos",
     "Zonas_entre_turnos_maiores_quedas_votos", "Zonas_distribuicao_da_variacao_pct",
     "Zonas_distribuicao_da_variacao_votos"],
]


def _between_rounds() -> list:
    rows = []
    for keys in _BETWEEN_ROUNDS:
        for i in range(0, len(keys), 2):
            rows.append([reuse(k) for k in keys[i:i + 2]])
    return rows


def _winner_by_state():
    chart = pivot("winner_by_state", "Vencedor em cada UF por eleição", UNIT_WINNERS, ["state"], ["election_year"],
                  metric("MAX(party_current)", "Partido do mais votado"), filters=[where("state IS NOT NULL")])
    chart.params["aggregateFunction"] = "List Unique Values"
    chart.params["row_limit"] = 5000
    chart.ignore = ("uf", "ano", "turno", "partido")
    return chart


OFFICE = office("Presidente", CODE, 1000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block(seat_competition=False)),
    subtab("Entre turnos",
           note("between_rounds_note",
                "Compara o 1º e o 2º turno da mesma eleição, sempre entre os dois finalistas. A variação é a do "
                "percentual de votos válidos do candidato no local, em pontos percentuais (p.p.), ou a de votos. "
                "Os brancos e nulos entre turnos estão no final desta aba."),
           *_between_rounds(),
           *round_blank_block()),
    subtab("Fluxo dos mais votados",
           note("flow_note",
                "Cada faixa liga o candidato mais votado de um local no 1º turno ao do 2º turno. Municípios e seções "
                "são contados uma vez cada, sem ponderar pelo número de eleitores."),
           [reuse("Municipios_por_candidato_mais_votado")],
           [reuse("Secoes_por_candidato_mais_votado")]),
    subtab("Vencedor por UF",
           note("winner_note",
                "Partido do candidato mais votado na última rodada de cada eleição, em cada UF (ZZ é o exterior). "
                "Mostra quem venceu em cada estado, não o eleito nacional. Não responde aos filtros de ano, UF e "
                "partido, porque cada coluna é uma eleição."),
           [_winner_by_state()],
           [table("winner_table", "Vencedor em cada UF: candidato, partido e margem", UNIT_WINNERS,
                  ["election_year", "state", "winner_name", "party_current", "winner_pct", "runner_up_name",
                   "runner_up_party", "margin_pp", "round1_leader_name"],
                  order=[("election_year", False), ("state", True)], filters=[where("state IS NOT NULL")],
                  ignore=("partido",))]),
])
