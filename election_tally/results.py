"""Vote totals by municipality, state, country and party, so charts need not scan `votes`.

`share_of_valid` is the share of a row's area and office among valid votes (candidates and
party-only votes); blanks and nulls have none. Numbers repeat between municipalities in
municipal offices and between states in state offices, so a candidate's total exists only at
the level of the race: `state_results` leaves out municipal offices and `national_results` has
only the president. `party_results` covers every office, and summing it over states gives the
national result of offices such as federal deputy.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

_METHOD_VERSION = 1  # raise when a table changes, so existing ones get rebuilt
_STATE_WIDE_OFFICES = "1, 3, 5, 6, 7, 8"
_PRESIDENT = 1
_FIRST_QUESTION = 51
_VALID = "vote_type IN ('Nominal', 'Legenda')"
_STATE_TABLE = "national_results"


def _share(partition: str) -> str:
    return (f"CASE WHEN {_VALID} THEN votes * 1.0 / sum(votes) FILTER (WHERE {_VALID}) "
            f"OVER (PARTITION BY {partition}) END AS share_of_valid")


_TABLES = {
    "municipality_results": f"""
        SELECT *, {_share('election_year, round_number, office_code, state, municipality_code')}
        FROM (SELECT election_year, round_number, office_code, any_value(office_name) AS office_name,
                     state, municipality_code, any_value(municipality_name) AS municipality_name,
                     any_value(municipality_ibge_code) AS municipality_ibge_code,
                     any_value(country) AS country, any_value(country_code) AS country_code,
                     vote_type, candidate_number, any_value(candidate_name) AS candidate_name,
                     any_value(party) AS party, sum(vote_count)::BIGINT AS votes
              FROM votes WHERE vote_count > 0
              GROUP BY election_year, round_number, office_code, state, municipality_code,
                       vote_type, candidate_number)""",
    "state_results": f"""
        SELECT *, {_share('election_year, round_number, office_code, state')}
        FROM (SELECT election_year, round_number, office_code, any_value(office_name) AS office_name,
                     state, vote_type, candidate_number,
                     any_value(candidate_name) AS candidate_name, any_value(party) AS party,
                     sum(votes)::BIGINT AS votes
              FROM municipality_results WHERE office_code IN ({_STATE_WIDE_OFFICES})
              GROUP BY election_year, round_number, office_code, state, vote_type, candidate_number)""",
    "national_results": f"""
        SELECT *, {_share('election_year, round_number')}
        FROM (SELECT election_year, round_number, vote_type, candidate_number,
                     any_value(candidate_name) AS candidate_name, any_value(party) AS party,
                     sum(votes)::BIGINT AS votes
              FROM state_results WHERE office_code = {_PRESIDENT}
              GROUP BY election_year, round_number, vote_type, candidate_number)""",
    "party_results": f"""
        SELECT election_year, round_number, office_code, any_value(office_name) AS office_name,
               state, party, sum(votes)::BIGINT AS votes
        FROM municipality_results
        WHERE office_code < {_FIRST_QUESTION} AND party IS NOT NULL
        GROUP BY election_year, round_number, office_code, state, party""",
}


def _source_state(con: duckdb.DuckDBPyConnection) -> str:
    rows, votes = con.execute("SELECT count(*), sum(vote_count) FROM votes").fetchone()
    return f"v{_METHOD_VERSION} rows={rows} votes={votes}"


def _is_current(db: Path) -> bool:
    """Read-only, so checking never conflicts with a running Superset."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        comment = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = ?",
                              [_STATE_TABLE]).fetchone()
        return bool(comment) and comment[0] == _source_state(con)
    finally:
        con.close()


def build(db: Path, force: bool = False) -> None:
    """Rebuilds the result tables, unless `votes` is unchanged since the last build."""
    if not force and _is_current(db):
        print("results: up to date")
        return
    con = duckdb.connect(str(db))
    try:
        for name, select in _TABLES.items():
            con.execute(f"CREATE OR REPLACE TABLE {name} AS {select}")
            n = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            print(f"{name}: {n:,} rows", flush=True)
        con.execute(f"COMMENT ON TABLE {_STATE_TABLE} IS '{_source_state(con)}'")
        con.execute("CHECKPOINT")
    finally:
        con.close()
