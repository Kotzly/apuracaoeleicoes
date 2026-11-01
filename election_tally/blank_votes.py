"""Blank and null votes of each office, round and municipality, next to all of its votes.

`blank_null_votes` has counts and not shares, so a share can be taken at any level from the sum of
its municipalities. Votes "anulado e apurado em separado" (a few, only in 2018) count as neither.
`has_second_round` marks the municipalities that voted in both rounds of that office and year: only
those can be compared between rounds, since a governor or mayor rarely goes to a second round
everywhere.
"""

from __future__ import annotations

from pathlib import Path

from . import derived, parties
from .domain import OFFICE_LIST, OFFICE_NAME_CASE, VALID_VOTES

_TABLE = "blank_null_votes"


def build(db: Path, force: bool = False) -> None:
    """Rebuilds the table, unless the results are unchanged."""
    if not derived.has_tables(db, "municipality_results", "national_results"):
        print(f"warning: {_TABLE} not built: the results tables are missing")
        return
    if not force and parties.is_current(db, _TABLE):
        print(f"{_TABLE}: up to date")
        return
    with derived.rebuilding(db) as con:
        con.execute(f"""
            CREATE OR REPLACE TABLE {_TABLE} AS
            WITH counts AS (
                SELECT election_year, round_number, office_code, state, municipality_code,
                       any_value(municipality_name) AS municipality_name,
                       (coalesce(sum(votes) FILTER (WHERE vote_type = 'Branco'), 0))::BIGINT AS votes_blank,
                       (coalesce(sum(votes) FILTER (WHERE vote_type = 'Nulo'), 0))::BIGINT AS votes_null,
                       (coalesce(sum(votes) FILTER (WHERE {VALID_VOTES}), 0))::BIGINT AS votes_valid
                FROM municipality_results
                WHERE office_code IN ({OFFICE_LIST})
                GROUP BY election_year, round_number, office_code, state, municipality_code)
            SELECT election_year::SMALLINT AS election_year, round_number::TINYINT AS round_number,
                   office_code::SMALLINT AS office_code, {OFFICE_NAME_CASE} AS office_name,
                   state, municipality_code, municipality_name, votes_blank, votes_null,
                   votes_blank + votes_null AS votes_blank_null, votes_valid,
                   votes_blank + votes_null + votes_valid AS votes_total,
                   count(*) OVER (PARTITION BY election_year, office_code, state, municipality_code) = 2
                       AS has_second_round
            FROM counts
            ORDER BY office_code, election_year, round_number, state, municipality_name""")
        derived.mark_current(con, _TABLE, parties.source_state(con))
        derived.report_rows(con, _TABLE)
