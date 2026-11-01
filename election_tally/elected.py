"""Who won: elected candidates by party, and how mayors' parties moved between elections.

`elected_party` counts the elected (result `ELEITO%`) of each party next to its first-round votes.
For mayors and governors, where a place has one seat, a supplementary election replaces the winner
of the ordinary one: the winner counts from the latest election (supplementary before ordinary,
then the highest election code and round). The president has `state` = 'BR'.

`mayor_flows` follows the party of each city's mayor from one election to the next, for Sankey
charts: a city without an elected mayor on one side goes to the node `Sem eleito`, and parties
outside the 12 with most mayors in the three elections are grouped as `Outros`.
`senate_seats` adds each Senate election to the previous one, the two that fill the 81 seats. It
counts the party a senator was elected by: later party switches and alternates are not in the data.
Party names are the current ones (see `parties.py`).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import parties
from .parties import FEDERAL_YEARS, MUNICIPAL_YEARS, OFFICES, canonical_party_sql

NO_MAYOR = "Sem eleito"
OTHERS = "Outros"
TOP_PARTIES = 12
_STATE_TABLE = "elected_party"
_MAYOR = 11
_SINGLE_SEAT = (3, _MAYOR)  # one seat per state or city: only the latest election counts
_NATIONAL = "BR"
_SENATOR = 5
# Elected before the merger, but sitting in UNIÃO afterwards. Only `senate_seats` joins them: a
# seat lasts two elections, and everywhere else a merger stays a change of party.
_SENATE_MERGERS = {"DEM": "UNIÃO", "PSL": "UNIÃO"}
_MERGER_YEAR = 2022


def _create_elected_tables(con: duckdb.DuckDBPyConnection) -> None:
    party = canonical_party_sql("party")
    offices = ", ".join(map(str, OFFICES))
    single = ", ".join(map(str, _SINGLE_SEAT))
    # Every seat holder, once: `winners` is also what mayor_flows reads.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE winners AS
        SELECT election_year, office_code, coalesce(state, '{_NATIONAL}') AS state,
               municipality_code, municipality_name, {party} AS party
        FROM candidates c
        WHERE office_code IN ({offices}) AND result LIKE 'ELEITO%'
        QUALIFY CASE WHEN office_code IN ({single})
                     THEN dense_rank() OVER (
                         PARTITION BY election_year, office_code, state, municipality_code
                         ORDER BY coalesce(election_type, '') LIKE '%SUPLEMENTAR%' DESC,
                                  election_code DESC, round_number DESC) = 1
                     ELSE true END""")
    names = " ".join(f"WHEN {code} THEN '{name}'" for code, name in OFFICES.items())
    pparty = canonical_party_sql()
    con.execute(f"""
        CREATE OR REPLACE TABLE elected_party AS
        WITH seats AS (
            SELECT election_year, office_code, state, party, count(*) AS elected
            FROM winners GROUP BY ALL),
        ballots AS (
            SELECT election_year, office_code,
                   CASE WHEN office_code = 1 THEN '{_NATIONAL}' ELSE state END AS state,
                   {pparty} AS party, sum(votes)::BIGINT AS votes
            FROM party_results
            WHERE round_number = 1 AND office_code IN ({offices})
            GROUP BY ALL),
        joined AS (
            SELECT election_year, office_code, state, party,
                   coalesce(elected, 0)::INTEGER AS elected, coalesce(votes, 0)::BIGINT AS votes
            FROM seats FULL JOIN ballots USING (election_year, office_code, state, party))
        SELECT election_year::SMALLINT AS election_year, office_code::SMALLINT AS office_code,
               CASE office_code {names} END AS office_name, state, party, elected, votes,
               votes * 1.0 / nullif(sum(votes) OVER w, 0) AS vote_share,
               elected * 1.0 / nullif(sum(elected) OVER w, 0) AS seat_share,
               (sum(elected) OVER w)::INTEGER AS seats_total, (sum(votes) OVER w)::BIGINT AS votes_total
        FROM joined
        WINDOW w AS (PARTITION BY election_year, office_code, state)
        ORDER BY office_code, election_year, state, party""")
    n = con.execute("SELECT count(*) FROM elected_party").fetchone()[0]
    print(f"elected_party: {n:,} rows")


def _create_mayor_flows(con: duckdb.DuckDBPyConnection) -> None:
    years = ", ".join(map(str, MUNICIPAL_YEARS))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE top_parties AS
        SELECT party FROM winners WHERE office_code = {_MAYOR} AND election_year IN ({years})
        GROUP BY party ORDER BY count(*) DESC, party LIMIT {TOP_PARTIES}""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE mayors AS
        SELECT cities.election_year, cities.state, cities.municipality_code,
               cities.municipality_name, coalesce(w.party, '{NO_MAYOR}') AS party
        FROM (SELECT election_year, state, municipality_code, any_value(municipality_name) AS municipality_name
              FROM candidates WHERE office_code = {_MAYOR} AND election_year IN ({years})
              GROUP BY ALL) cities
        LEFT JOIN (SELECT * FROM winners WHERE office_code = {_MAYOR}) w
             USING (election_year, state, municipality_code)""")
    side = "(SELECT * FROM mayors WHERE election_year = {})"
    links = " UNION ALL ".join(f"""
        SELECT {a} AS from_year, {b} AS to_year, coalesce(x.state, y.state) AS state,
               coalesce(y.municipality_name, x.municipality_name) AS municipality_name,
               coalesce(x.party, '{NO_MAYOR}') AS source_party,
               coalesce(y.party, '{NO_MAYOR}') AS target_party
        FROM {side.format(a)} x FULL JOIN {side.format(b)} y USING (state, municipality_code)"""
                               for a, b in zip(MUNICIPAL_YEARS, MUNICIPAL_YEARS[1:]))
    node = ("CASE WHEN {p} = '" + NO_MAYOR + "' OR {p} IN (SELECT party FROM top_parties) THEN {p} "
            "ELSE '" + OTHERS + "' END")
    con.execute(f"""
        CREATE OR REPLACE TABLE mayor_flows AS
        SELECT from_year || ' → ' || to_year AS transition, from_year::SMALLINT AS from_year,
               to_year::SMALLINT AS to_year, state, municipality_name, source_party, target_party,
               from_year || ' · ' || {node.format(p='source_party')} AS source,
               to_year || ' · ' || {node.format(p='target_party')} AS target,
               count(*)::INTEGER AS units
        FROM ({links}) GROUP BY ALL ORDER BY from_year, state, municipality_name""")
    n = con.execute("SELECT count(*) FROM mayor_flows").fetchone()[0]
    print(f"mayor_flows: {n:,} rows")


def _create_senate_seats(con: duckdb.DuckDBPyConnection) -> None:
    """Seats after each election, for the years whose previous election is also loaded."""
    loaded = {row[0] for row in con.execute(f"""SELECT DISTINCT election_year FROM elected_party
                                                WHERE office_code = {_SENATOR} AND elected > 0""").fetchall()}
    pairs = [(a, b) for a, b in zip(FEDERAL_YEARS, FEDERAL_YEARS[1:]) if a in loaded and b in loaded]
    merged = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in _SENATE_MERGERS.items())
    selects = [f"""
        SELECT {year}::SMALLINT AS election_year, state,
               {f"CASE party {merged} ELSE party END" if year >= _MERGER_YEAR else "party"} AS party,
               (sum(elected) FILTER (WHERE election_year = {year}))::INTEGER AS seats_this_election,
               (sum(elected) FILTER (WHERE election_year = {previous}))::INTEGER AS seats_previous_election
        FROM elected_party
        WHERE office_code = {_SENATOR} AND elected > 0 AND election_year IN ({previous}, {year})
        GROUP BY ALL""" for previous, year in pairs]
    # Typed even when empty, because the dataset expects these columns.
    empty = """SELECT NULL::SMALLINT AS election_year, NULL::VARCHAR AS state, NULL::VARCHAR AS party,
                      NULL::INTEGER AS seats_this_election, NULL::INTEGER AS seats_previous_election
               WHERE false"""
    con.execute(f"""
        CREATE OR REPLACE TABLE senate_seats AS
        SELECT election_year, state, party, coalesce(seats_this_election, 0) AS seats_this_election,
               coalesce(seats_previous_election, 0) AS seats_previous_election,
               coalesce(seats_this_election, 0) + coalesce(seats_previous_election, 0) AS seats
        FROM ({' UNION ALL '.join(selects + [empty])})
        ORDER BY election_year, state, party""")
    n = con.execute("SELECT count(*) FROM senate_seats").fetchone()[0]
    print(f"senate_seats: {n:,} rows")


def build(db: Path, force: bool = False) -> None:
    """Rebuilds `elected_party`, `mayor_flows` and `senate_seats`, unless results, candidates and renames are unchanged."""
    if not parties.has_tables(db, "national_results", "party_results", "candidates"):
        print("warning: elected_party not built: the results or `candidates` could not be loaded")
        return
    if (not force and parties.is_current(db, _STATE_TABLE, uses_candidates=True)
            and parties.has_tables(db, "senate_seats")):
        print("elected_party: up to date")
        return
    con = duckdb.connect(str(db))
    try:
        con.begin()
        _create_elected_tables(con)
        _create_mayor_flows(con)
        _create_senate_seats(con)
        con.execute(f"COMMENT ON TABLE elected_party IS '{parties.source_state(con, True)}'")
        con.commit()
        con.execute("CHECKPOINT")
    finally:
        con.close()
