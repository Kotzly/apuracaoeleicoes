"""How many sections and municipalities moved between most-voted candidates, for Sankey charts.

Nothing is estimated: each section or municipality has its most-voted presidential candidate
in each round, and a link counts those with one leader in a round and another in the next.
Valid votes only, so blanks and nulls never lead. Across elections, the same section number
may have been redrawn; municipalities are stable, but each counts the same whatever its size.
Units that exist on only one side appear as their own nodes.
"""

from __future__ import annotations

from pathlib import Path

from . import derived
from .domain import SECTION_UNIT

TIE = "Empate"
NO_VOTES = "Sem votos"
SECTION = "Seção"
MUNICIPALITY = "Município"
# level -> (SQL identifying the unit, node for new units, node for closed units)
LEVELS = {
    SECTION: (SECTION_UNIT, "Seção nova", "Seção encerrada"),
    MUNICIPALITY: ("municipality_code::BIGINT", "Município novo", "Município encerrado"),
}
_METHOD_VERSION = 1  # raise when the table changes, so existing ones get rebuilt


def _build_leaders(con: derived.Connection, office: int, unit: str) -> None:
    """One row per unit and round, with its most-voted candidate."""
    # Office -1: sections where nobody voted.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE leaders AS
        WITH counted AS (
            SELECT election_year, round_number, {unit} AS unit,
                   any_value(candidate_name) AS candidate, sum(vote_count) AS v
            FROM votes
            WHERE office_code = {office} AND vote_type = 'Nominal' AND vote_count > 0
            GROUP BY election_year, round_number, unit, candidate_number
        ), top AS (
            SELECT election_year, round_number, unit,
                   CASE WHEN count(*) > 1 THEN '{TIE}' ELSE any_value(candidate) END AS leader
            FROM (SELECT * FROM counted
                  QUALIFY v = max(v) OVER (PARTITION BY election_year, round_number, unit))
            GROUP BY election_year, round_number, unit
        )
        SELECT s.election_year, s.round_number, s.unit, any_value(s.state) AS state,
               any_value(s.municipality_name) AS municipality_name,
               coalesce(any_value(t.leader), '{NO_VOTES}') AS leader
        FROM (SELECT *, {unit} AS unit FROM sections WHERE office_code IN ({office}, -1)) s
        LEFT JOIN top t USING (election_year, round_number, unit)
        GROUP BY s.election_year, s.round_number, s.unit""")


def _source_state(con: derived.Connection, office: int) -> str:
    """Identifies the data and method behind the table; a change in either means rebuilding."""
    rows, votes = con.execute(f"""SELECT count(*), sum(vote_count) FROM votes
                                  WHERE office_code = {office}""").fetchone()
    return f"v{_METHOD_VERSION} office={office} rows={rows} votes={votes}"


def _insert_links(con: derived.Connection, level: str, new: str, closed: str) -> None:
    rounds = con.execute("""SELECT DISTINCT election_year, round_number FROM leaders
                            ORDER BY 1, 2""").fetchall()
    side = "(SELECT * FROM leaders WHERE election_year = {} AND round_number = {})"
    links = " UNION ALL ".join(f"""
        SELECT {start[0]} AS from_year, {start[1]} AS from_round,
               {end[0]} AS to_year, {end[1]} AS to_round,
               coalesce(a.state, b.state) AS state,
               coalesce(b.municipality_name, a.municipality_name) AS municipality_name,
               coalesce(a.leader, '{new}') AS source_candidate,
               coalesce(b.leader, '{closed}') AS target_candidate
        FROM {side.format(*start)} a FULL JOIN {side.format(*end)} b USING (unit)"""
                               for start, end in zip(rounds, rounds[1:]))
    con.execute(f"""
        INSERT INTO leader_flows
        SELECT '{level}',
               from_year || ' T' || from_round || ' → ' || to_year || ' T' || to_round,
               from_year, from_round, to_year, to_round, state, municipality_name,
               source_candidate, target_candidate,
               from_year || ' T' || from_round || ' · ' || source_candidate,
               to_year || ' T' || to_round || ' · ' || target_candidate,
               count(*)
        FROM ({links}) GROUP BY ALL""")


def build(db: Path, office: int = 1, force: bool = False) -> int | None:
    """Rebuilds the table `leader_flows`, unless `votes` is unchanged since the last build."""
    if not force and derived.is_current(db, "leader_flows", lambda con: _source_state(con, office)):
        print("leader_flows: up to date")
        return None
    with derived.rebuilding(db) as con:
        con.execute("""CREATE OR REPLACE TABLE leader_flows (
            level VARCHAR, transition VARCHAR, from_year INTEGER, from_round INTEGER,
            to_year INTEGER, to_round INTEGER, state VARCHAR, municipality_name VARCHAR,
            source_candidate VARCHAR, target_candidate VARCHAR, source VARCHAR, target VARCHAR,
            units BIGINT)""")
        for level, (unit, new, closed) in LEVELS.items():
            _build_leaders(con, office, unit)
            _insert_links(con, level, new, closed)
        derived.mark_current(con, "leader_flows", _source_state(con, office))
    with derived.reading(db) as con:
        derived.report_rows(con, "leader_flows")
        return con.execute("SELECT count(*) FROM leader_flows").fetchone()[0]
