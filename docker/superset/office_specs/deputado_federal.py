"""Deputado federal: base spec plus the benches of the Chamber, votes against seats and the vote structure."""

from .common import (OFFICE_SEATS, bar, candidates_block, change_block, legislature_block, metric, note, office,
                     result_block, subtab, territory_block)

CODE = 6

BENCH_NOTE = (
    "Como ler: a Câmara tem 513 cadeiras, distribuídas entre as 27 UFs de 8 a 70 cadeiras, e cada UF é um distrito "
    "eleitoral. As cadeiras são divididas entre os partidos pelo quociente partidário (QP) e as sobras, pela média; "
    "por isso um partido pode ter menos votos que outro e mais cadeiras. Os partidos aparecem com o nome atual, "
    "mesmo nas eleições em que existiam com outro nome ou antes de uma fusão. Votos de legenda são os dados ao "
    "partido, sem candidato. A reeleição é aproximada: cruza-se o nome completo no mesmo cargo e UF, pois o TSE muda "
    "o identificador a cada eleição e não há CPF; homônimos e mudanças de nome distorcem o resultado. Não há 2º "
    "turno para este cargo.")

OFFICE = office("Deputado federal", CODE, 4000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block()),
    subtab("Bancadas",
           note("bench_note", BENCH_NOTE, height=14),
           *legislature_block(),
           [bar("seats_by_state", "Cadeiras de cada UF na Câmara", OFFICE_SEATS, "uf", [metric("SUM(seats)", "Cadeiras")],
                row_limit=30, ignore=("partido",)),
            bar("candidates_per_seat_state", "Candidatos por vaga em cada UF", OFFICE_SEATS, "uf",
                [metric("SUM(candidates) * 1.0 / NULLIF(SUM(seats), 0)", "Candidatos por vaga")], row_limit=30,
                y_format=".1f", ignore=("partido",))]),
])
