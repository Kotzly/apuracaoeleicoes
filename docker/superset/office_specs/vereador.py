"""Vereador: base spec, to be completed with what is specific to the office."""

from .common import candidates_block, change_block, legislature_block, office, result_block, subtab, territory_block

CODE = 13

OFFICE = office("Vereador", CODE, 8000, [
    subtab("Resultado", *result_block(CODE)),
    subtab("Território", *territory_block()),
    subtab("Mudança", *change_block()),
    subtab("Candidatos", *candidates_block()),
    subtab("Bancadas", *legislature_block()),
])
