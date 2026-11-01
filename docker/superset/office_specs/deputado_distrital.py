"""Deputado distrital: the reduced spec of an office elected only in the Distrito Federal, with zones as the smallest unit."""

from .common import candidates_block, change_block, legislature_block, note, office, result_block, subtab, territory_block

CODE = 8

DISTRICT_NOTE = (
    "Como ler: o cargo existe só no Distrito Federal, que elege 24 deputados distritais para a Câmara Legislativa. "
    "Como o DF é uma única unidade, esta aba não tem mapas nem comparação entre UFs ou municípios, e a menor unidade "
    "de análise é a zona eleitoral. Os filtros \"UF\" e \"Município\" não se aplicam a este cargo. As cadeiras são "
    "divididas entre os partidos pelo quociente partidário e as sobras, pela média, e os partidos aparecem com o nome "
    "atual, mesmo nas eleições em que existiam com outro nome ou antes de uma fusão. Não há 2º turno para este cargo.")

OFFICE = office("Deputado distrital", CODE, 6000, [
    subtab("Resultado", note("district_note", DISTRICT_NOTE, height=14), *result_block(CODE)),
    subtab("Território", *territory_block(maps=False, by_state=False)),
    subtab("Mudança", *change_block(maps=False, by_state=False, by_city=False)),
    subtab("Candidatos", *candidates_block()),
    subtab("Bancadas", *legislature_block(by_state=False)),
])
