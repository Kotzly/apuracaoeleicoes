"""Facts about Brazilian elections that several modules need, in one place."""

from __future__ import annotations

ABROAD_STATE = "ZZ"
PRESIDENT = 1
SENATOR = 5
MAYOR = 11

# Names shown in the derived tables and dashboards. `votes` and `sections` keep the spelling of
# the TSE files, with a capital in "Deputado Federal" (see formats.py): compare offices by code.
OFFICES = {PRESIDENT: "Presidente", 3: "Governador", SENATOR: "Senador", 6: "Deputado federal",
           7: "Deputado estadual", 8: "Deputado distrital", MAYOR: "Prefeito", 13: "Vereador"}
OFFICE_LIST = ", ".join(map(str, OFFICES))  # for an SQL `IN`
OFFICE_NAME_CASE = "CASE office_code " + " ".join(
    f"WHEN {code} THEN '{name}'" for code, name in OFFICES.items()) + " END"

CYCLE_YEARS = 4  # the same office is elected again four years later
VALID_VOTES = "vote_type IN ('Nominal', 'Legenda')"
BLANK_AND_NULL = "('Branco', 'Nulo')"
# Valid, blank and null votes. Votes "anulado e apurado em separado" (a few, only in 2018) are out.
COUNTED_VOTES = f"({VALID_VOTES} OR vote_type IN {BLANK_AND_NULL})"

# A section as one number: municipality codes have up to 5 digits, zones 3 and sections 4 or fewer.
SECTION_UNIT = "municipality_code * 100000000::BIGINT + zone * 100000 + section"

# `votes` with the party of every presidential candidate: in 2014 the file of the sections abroad
# has no party at all. A presidential number means one party per year, taken from the other states.
VOTES_WITH_PARTY = f"""(
    SELECT v.* REPLACE (coalesce(v.party, p.party) AS party)
    FROM votes v
    LEFT JOIN (SELECT election_year, candidate_number, any_value(party) AS party
               FROM votes WHERE office_code = {PRESIDENT} AND party IS NOT NULL
               GROUP BY election_year, candidate_number) p
      ON v.office_code = {PRESIDENT} AND v.election_year = p.election_year
     AND v.candidate_number = p.candidate_number)"""


def consecutive_elections(years: list[int]) -> list[tuple[int, int]]:
    """Pairs of loaded elections of one office: a gap in the data is not a comparison."""
    loaded = set(years)
    return [(y, y + CYCLE_YEARS) for y in sorted(loaded) if y + CYCLE_YEARS in loaded]


def sql_string(value: object) -> str:
    """An SQL string literal, for values such as file paths that may hold a quote."""
    return "'" + str(value).replace("'", "''") + "'"
