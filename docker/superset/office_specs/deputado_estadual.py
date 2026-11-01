"""Deputado estadual: base spec plus the benches of the 26 state assemblies, votes against seats and the vote structure."""

from .common import (OFFICE_SEATS, bar, candidates_block, change_block, legislature_block, metric, note, office,
                     result_block, subtab, territory_block)

CODE = 7

BENCH_NOTE = (
    "Como ler: as 26 assembleias legislativas somam 1.035 cadeiras, de 24 a 94 por UF, e cada UF é um distrito "
    "eleitoral. O Distrito Federal não entra aqui, pois elege deputados distritais (aba própria). As cadeiras são "
    "divididas entre os partidos pelo quociente partidário (QP) e as sobras, pela média; por isso um partido pode ter "
    "menos votos que outro e mais cadeiras. Cada assembleia tem seu próprio número de cadeiras, então compare "
    "proporções, não totais, entre UFs. Os partidos aparecem com o nome atual, mesmo nas eleições em que existiam com "
    "outro nome ou antes de uma fusão. Votos de legenda são os dados ao partido, sem candidato. A reeleição é "
    "aproximada: cruza-se o nome completo no mesmo cargo e UF, pois o TSE muda o identificador a cada eleição e não há "
    "CPF; homônimos e mudanças de nome distorcem o resultado. Não há 2º turno para este cargo.")

OFFICE = office("Deputado estadual", CODE, 5000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block()),
    subtab("Bancadas",
           note("bench_note", BENCH_NOTE, height=16),
           *legislature_block(),
           [bar("seats_by_state", "Cadeiras de cada assembleia", OFFICE_SEATS, "uf", [metric("SUM(seats)", "Cadeiras")],
                row_limit=30, ignore=("partido",)),
            bar("candidates_per_seat_state", "Candidatos por vaga em cada UF", OFFICE_SEATS, "uf",
                [metric("SUM(candidates) * 1.0 / NULLIF(SUM(seats), 0)", "Candidatos por vaga")], row_limit=30,
                y_format=".1f", ignore=("partido",))]),
])
