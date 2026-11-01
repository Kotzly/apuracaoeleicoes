"""How party shares moved between one election and the previous one of the same office.

`party_shifts` has one row per level, office, round, pair of elections, unit and party; a party
that ran on either side of the pair appears on both, with 0 votes where it did not run. Share is
the party's votes over the unit's valid votes (Nominal and Legenda) for that office and round.
Blank and null votes have rows too, under the party `Branco` and `Nulo`: their share is over all of
the unit's votes, since they are not part of the valid ones, and they stay out of the volatility.
The `*_all` columns are the same measures over all of the unit's votes, for every row; there, blank
and null votes do count in the volatility, as a move of voters like any other.
A unit enters a pair only if it has valid votes on both sides, so a new section or a municipality
that was created has no previous share to compare. `unit_volatility` is the Pedersen index of each
unit, half the sum of the absolute changes in share. `round_shifts` follows the presidential
candidates between the two rounds of a year, in each municipality; `state_round_shifts` and
`zone_round_shifts` do the same in each state and zone.

Party names are the current ones (see `parties.py`). The same section number may have been redrawn
between elections; the data keeps them as numbered. Seção covers only some offices, because
the table would otherwise grow by gigabytes (`SECTION_OFFICES`).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import parties
from .parties import OFFICES, canonical_party_sql, years_of

_STATE_TABLE = "party_shifts"
_ROUND_TABLES = ("round_shifts", "state_round_shifts", "zone_round_shifts")
_PRESIDENT = 1
_VALID = "vote_type IN ('Nominal', 'Legenda')"
_BLANKS = "('Branco', 'Nulo')"
_COUNTED = f"({_VALID} OR vote_type IN {_BLANKS})"
SECTION_OFFICES = (1, 3, 5, 11)  # majoritarian offices; the others stop at Zona
# level -> SQL, over `unit`, for each unit column (m: municipality_dim, for the names of cities)
_NO_STATE = "NULL::VARCHAR"
_UNIT_COLUMNS = {
    "Brasil": dict(state=_NO_STATE, code="NULL::INTEGER", zone="NULL::INTEGER",
                   section="NULL::INTEGER"),
    "UF": dict(state="chr((unit // 256)::INTEGER) || chr((unit % 256)::INTEGER)",
               code="NULL::INTEGER", zone="NULL::INTEGER", section="NULL::INTEGER"),
    "Município": dict(state="m.state", code="unit::INTEGER", zone="NULL::INTEGER",
                      section="NULL::INTEGER"),
    "Zona": dict(state="chr((unit // 10000 // 256)::INTEGER) || chr((unit // 10000 % 256)::INTEGER)",
                 code="NULL::INTEGER", zone="(unit % 10000)::INTEGER", section="NULL::INTEGER"),
    "Seção": dict(state="m.state", code="(unit // 100000000)::INTEGER",
                  zone="(unit // 100000 % 1000)::INTEGER", section="(unit % 100000)::INTEGER"),
}
_ZONE_UNIT = "(ascii(state[1]) * 256 + ascii(state[2])) * 10000 + zone"
_SECTION_UNIT = "municipality_code * 100000000::BIGINT + zone * 100000 + section"


def _offices_of(level: str) -> tuple[int, ...]:
    return SECTION_OFFICES if level == "Seção" else tuple(OFFICES)


def _create_bases(con: duckdb.DuckDBPyConnection) -> None:
    """One temp table per level: valid votes by year, round, office, unit and party.

    The party is NULL for votes of candidates without one, which still count in the total. Blank
    and null votes use their type as the party.
    """
    party = f"CASE WHEN vote_type IN {_BLANKS} THEN vote_type ELSE {canonical_party_sql()} END"
    offices = ", ".join(map(str, OFFICES))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE municipality_dim AS
        SELECT municipality_code, any_value(state) AS state,
               any_value(municipality_name) AS municipality_name,
               any_value(municipality_ibge_code) AS municipality_ibge_code
        FROM municipality_results GROUP BY municipality_code""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_município AS
        SELECT election_year, round_number, office_code, municipality_code::BIGINT AS unit,
               {party} AS party, sum(votes)::INTEGER AS votes
        FROM municipality_results
        WHERE office_code IN ({offices}) AND {_COUNTED}
        GROUP BY ALL ORDER BY office_code, election_year, round_number, unit""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_uf AS
        SELECT election_year, round_number, office_code, ascii(m.state[1]) * 256 + ascii(m.state[2]) AS unit,
               party, sum(votes)::BIGINT AS votes
        FROM base_município b JOIN municipality_dim m ON m.municipality_code = b.unit
        GROUP BY ALL ORDER BY office_code, election_year, round_number, unit""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE base_brasil AS
        SELECT election_year, round_number, office_code, 0::BIGINT AS unit, party,
               sum(votes)::BIGINT AS votes
        FROM base_uf GROUP BY ALL""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_zona AS
        SELECT election_year, round_number, office_code, ({_ZONE_UNIT})::BIGINT AS unit,
               {party} AS party, sum(vote_count)::INTEGER AS votes
        FROM votes WHERE vote_count > 0 AND {_COUNTED} AND office_code IN ({offices})
        GROUP BY ALL ORDER BY office_code, election_year, round_number, unit""")
    section_offices = ", ".join(map(str, SECTION_OFFICES))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_seção AS
        SELECT election_year, round_number, office_code, ({_SECTION_UNIT})::BIGINT AS unit,
               {party} AS party, sum(vote_count)::INTEGER AS votes
        FROM votes WHERE vote_count > 0 AND {_COUNTED} AND office_code IN ({section_offices})
        GROUP BY ALL ORDER BY office_code, election_year, round_number, unit""")


def _decoded(level: str, source: str, columns: str) -> str:
    """SELECT of `columns` over `source` (which has `unit`), with the unit columns decoded."""
    c = _UNIT_COLUMNS[level]
    if level in ("Município", "Seção"):
        join = f"LEFT JOIN municipality_dim m ON m.municipality_code = {c['code']}"
        name, ibge = "m.municipality_name", "m.municipality_ibge_code"
    else:
        join, name, ibge = "", "NULL::VARCHAR", "NULL::INTEGER"
    return f"""
        SELECT {c['state']} AS state, 'BR-' || ({c['state']}) AS state_iso,
               {c['code']} AS municipality_code, {name} AS municipality_name,
               {ibge} AS municipality_ibge_code, {c['zone']} AS zone, {c['section']} AS section,
               {columns}
        FROM {source} {join}"""


def _build_pair(con: duckdb.DuckDBPyConnection, level: str, office: int, start: int, end: int,
                round_number: int) -> None:
    base = f"base_{level.lower()}"
    side = (f"SELECT unit, party, votes FROM {base} "
            f"WHERE election_year = {{}} AND round_number = {round_number} AND office_code = {office}")
    transition = f"{start} → {end} T{round_number}"
    valid = f"sum(votes) FILTER (WHERE coalesce(party, '') NOT IN {_BLANKS})"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE batch AS
        WITH a AS ({side.format(start)}), b AS ({side.format(end)}),
        units AS (SELECT unit, ta, tb, all_a, all_b FROM
                     (SELECT unit, {valid} AS ta, sum(votes) AS all_a FROM a GROUP BY unit)
                     JOIN (SELECT unit, {valid} AS tb, sum(votes) AS all_b FROM b GROUP BY unit) USING (unit)
                  WHERE ta > 0 AND tb > 0)
        SELECT unit, party, coalesce(a.votes, 0)::BIGINT AS votes_from,
               coalesce(b.votes, 0)::BIGINT AS votes_to,
               coalesce(a.votes, 0) * 1.0 / CASE WHEN party IN {_BLANKS} THEN all_a ELSE ta END AS share_from,
               coalesce(b.votes, 0) * 1.0 / CASE WHEN party IN {_BLANKS} THEN all_b ELSE tb END AS share_to,
               coalesce(a.votes, 0) * 1.0 / all_a AS share_from_all,
               coalesce(b.votes, 0) * 1.0 / all_b AS share_to_all
        FROM (SELECT * FROM a WHERE party IS NOT NULL) a
             FULL JOIN (SELECT * FROM b WHERE party IS NOT NULL) b USING (unit, party)
             JOIN units USING (unit)""")
    head = f"'{level}', {office}, '{OFFICES[office]}', {round_number}, '{transition}', {start}, {end}"
    unit_columns = ("state, state_iso, municipality_code, municipality_name, "
                    "municipality_ibge_code, zone, section")
    order = "ORDER BY state, municipality_name, zone, section"
    con.execute(f"""
        INSERT INTO party_shifts
        SELECT {head}, {unit_columns}, party, votes_from, votes_to, share_from, share_to,
               share_change, share_from_all, share_to_all
        FROM ({_decoded(level, 'batch', 'party, votes_from, votes_to, share_from, share_to, '
                                        'share_to - share_from AS share_change, share_from_all, share_to_all')})
        {order}, party""")
    parties_only = f"FILTER (WHERE party NOT IN {_BLANKS})"
    pedersen = f"""(SELECT unit, 0.5 * sum(abs(share_to - share_from)) {parties_only} AS pedersen_index,
                           (count(*) {parties_only})::INTEGER AS parties,
                           0.5 * sum(abs(share_to_all - share_from_all)) AS pedersen_index_all
                    FROM batch GROUP BY unit HAVING count(*) {parties_only} > 0)"""
    con.execute(f"""
        INSERT INTO unit_volatility
        SELECT {head}, {unit_columns}, pedersen_index, parties, pedersen_index_all
        FROM ({_decoded(level, pedersen, 'pedersen_index, parties, pedersen_index_all')}) {order}""")


def _create_tables(con: duckdb.DuckDBPyConnection) -> None:
    unit = """state VARCHAR, state_iso VARCHAR, municipality_code INTEGER,
              municipality_name VARCHAR, municipality_ibge_code INTEGER, zone INTEGER,
              section INTEGER"""
    head = """level VARCHAR, office_code SMALLINT, office_name VARCHAR, round_number TINYINT,
              transition VARCHAR, from_year SMALLINT, to_year SMALLINT"""
    con.execute(f"""CREATE OR REPLACE TABLE party_shifts ({head}, {unit}, party VARCHAR,
        votes_from BIGINT, votes_to BIGINT, share_from DOUBLE, share_to DOUBLE,
        share_change DOUBLE, share_from_all DOUBLE, share_to_all DOUBLE)""")
    con.execute(f"""CREATE OR REPLACE TABLE unit_volatility ({head}, {unit},
        pedersen_index DOUBLE, parties INTEGER, pedersen_index_all DOUBLE)""")


def _build_round_shifts(con: duckdb.DuckDBPyConnection) -> None:
    """Each finalist's votes in the two rounds, in every municipality that voted in both.

    A finalist who got no votes in a municipality in one round has 0 there, not a missing row.
    """
    party = canonical_party_sql()
    con.execute(f"""
        CREATE OR REPLACE TABLE round_shifts AS
        WITH finalists AS (
            SELECT election_year, candidate_number, any_value(candidate_name) AS candidate_name,
                   any_value({party}) AS party
            FROM national_results WHERE round_number = 2 AND vote_type = 'Nominal'
            GROUP BY ALL),
        r AS (SELECT * FROM (
                  SELECT *, votes * 1.0 / sum(votes) FILTER (WHERE {_COUNTED}) OVER (
                             PARTITION BY election_year, round_number, municipality_code) AS share_of_all
                  FROM municipality_results WHERE office_code = {_PRESIDENT})
              WHERE vote_type = 'Nominal'),
        places AS (
            SELECT election_year, municipality_code, any_value(state) AS state,
                   any_value(municipality_name) AS municipality_name,
                   any_value(municipality_ibge_code) AS municipality_ibge_code
            FROM r GROUP BY election_year, municipality_code
            HAVING count(DISTINCT round_number) = 2)
        SELECT p.election_year::SMALLINT AS election_year, p.state, p.municipality_code,
               p.municipality_name, p.municipality_ibge_code, f.candidate_number,
               f.candidate_name, f.party,
               coalesce(a.votes, 0)::BIGINT AS votes_round1, coalesce(b.votes, 0)::BIGINT AS votes_round2,
               coalesce(a.share_of_valid, 0) AS share_round1,
               coalesce(b.share_of_valid, 0) AS share_round2,
               coalesce(b.share_of_valid, 0) - coalesce(a.share_of_valid, 0) AS share_change,
               (coalesce(b.votes, 0) - coalesce(a.votes, 0))::BIGINT AS votes_change,
               coalesce(a.share_of_all, 0) AS share_round1_all,
               coalesce(b.share_of_all, 0) AS share_round2_all
        FROM places p JOIN finalists f USING (election_year)
        LEFT JOIN r a ON a.election_year = p.election_year AND a.round_number = 1
                     AND a.municipality_code = p.municipality_code
                     AND a.candidate_number = f.candidate_number
        LEFT JOIN r b ON b.election_year = p.election_year AND b.round_number = 2
                     AND b.municipality_code = p.municipality_code
                     AND b.candidate_number = f.candidate_number
        ORDER BY election_year, state, municipality_name, candidate_number""")
    n = con.execute("SELECT count(*) FROM round_shifts").fetchone()[0]
    print(f"round_shifts: {n:,} rows")


def _build_place_round_shifts(con: duckdb.DuckDBPyConnection, table: str, rows: str, keys: list[str]) -> None:
    """Each finalist's votes in the two rounds, in every place (`keys`) that voted in both.

    `rows` has the year, round, keys, candidate number, votes and shares of the place's own valid
    and of all its votes, so a share is never the mean of smaller units, which would weigh a village like a capital.
    """
    party = canonical_party_sql()
    columns = ", ".join(f"p.{k}" for k in keys)
    same_place = " AND ".join(f"{{0}}.{k} = p.{k}" for k in keys)
    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        WITH finalists AS (
            SELECT election_year, candidate_number, any_value(candidate_name) AS candidate_name,
                   any_value({party}) AS party
            FROM national_results WHERE round_number = 2 AND vote_type = 'Nominal'
            GROUP BY ALL),
        r AS ({rows}),
        places AS (
            SELECT election_year, {', '.join(keys)} FROM r GROUP BY ALL
            HAVING count(DISTINCT round_number) = 2)
        SELECT p.election_year::SMALLINT AS election_year, {columns}, f.candidate_number,
               f.candidate_name, f.party,
               coalesce(a.votes, 0)::BIGINT AS votes_round1, coalesce(b.votes, 0)::BIGINT AS votes_round2,
               coalesce(a.share_of_valid, 0) AS share_round1,
               coalesce(b.share_of_valid, 0) AS share_round2,
               coalesce(b.share_of_valid, 0) - coalesce(a.share_of_valid, 0) AS share_change,
               (coalesce(b.votes, 0) - coalesce(a.votes, 0))::BIGINT AS votes_change,
               coalesce(a.share_of_all, 0) AS share_round1_all,
               coalesce(b.share_of_all, 0) AS share_round2_all
        FROM places p JOIN finalists f USING (election_year)
        LEFT JOIN r a ON a.election_year = p.election_year AND a.round_number = 1
                     AND {same_place.format('a')} AND a.candidate_number = f.candidate_number
        LEFT JOIN r b ON b.election_year = p.election_year AND b.round_number = 2
                     AND {same_place.format('b')} AND b.candidate_number = f.candidate_number
        ORDER BY election_year, {', '.join(keys)}, candidate_number""")
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"{table}: {n:,} rows")


def _build_state_round_shifts(con: duckdb.DuckDBPyConnection) -> None:
    """Municipalities that voted in only one round still count, so a state's votes may differ
    slightly from the sum of its rows in `round_shifts`."""
    _build_place_round_shifts(con, "state_round_shifts", f"""
        SELECT * FROM (
            SELECT election_year, round_number, state, candidate_number, votes, share_of_valid, vote_type,
                   votes * 1.0 / sum(votes) FILTER (WHERE {_COUNTED}) OVER (
                       PARTITION BY election_year, round_number, state) AS share_of_all
            FROM state_results WHERE office_code = {_PRESIDENT})
        WHERE vote_type = 'Nominal'""", ["state"])


def _build_zone_round_shifts(con: duckdb.DuckDBPyConnection) -> None:
    """A zone is (state, zone), since numbers repeat between states. Rounds of a year share one map."""
    _build_place_round_shifts(con, "zone_round_shifts", f"""
        SELECT * FROM (
            SELECT election_year, round_number, state, zone::INTEGER AS zone, candidate_number,
                   votes, votes * 1.0 / sum(votes) FILTER (WHERE {_VALID}) OVER (
                       PARTITION BY election_year, round_number, state, zone) AS share_of_valid,
                   votes * 1.0 / sum(votes) OVER (
                       PARTITION BY election_year, round_number, state, zone) AS share_of_all,
                   vote_type
            FROM (SELECT election_year, round_number, state, zone, vote_type, candidate_number,
                         sum(vote_count)::BIGINT AS votes
                  FROM votes
                  WHERE office_code = {_PRESIDENT} AND vote_count > 0 AND {_COUNTED}
                  GROUP BY ALL))
        WHERE vote_type = 'Nominal'""", ["state", "zone"])


def _build_round_tables(con: duckdb.DuckDBPyConnection) -> None:
    _build_round_shifts(con)
    _build_state_round_shifts(con)
    _build_zone_round_shifts(con)


def build(db: Path, force: bool = False) -> None:
    """Rebuilds these tables, unless the results and renames are unchanged.

    When those are unchanged and only a round table is missing, only the round tables are built:
    `party_shifts` takes minutes.
    """
    if not parties.has_tables(db, "national_results", "municipality_results"):
        print("warning: party_shifts not built: the results tables are missing")
        return
    current = not force and parties.is_current(db, _STATE_TABLE)
    if current and parties.has_tables(db, *_ROUND_TABLES):
        print("party_shifts: up to date")
        return
    con = duckdb.connect(str(db))
    try:
        con.execute("SET memory_limit = '4GB'")
        con.begin()
        _build_round_tables(con)
        if current:
            con.commit()
            con.execute("CHECKPOINT")
            return
        _create_bases(con)
        _create_tables(con)
        for level in _UNIT_COLUMNS:
            for office in _offices_of(level):
                years = years_of(con, office)
                for start, end in zip(years, years[1:]):
                    for round_number in (1, 2):
                        _build_pair(con, level, office, start, end, round_number)
        con.execute(f"COMMENT ON TABLE party_shifts IS '{parties.source_state(con)}'")
        con.commit()
        con.execute("CHECKPOINT")
        for table in ("party_shifts", "unit_volatility"):
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table}: {n:,} rows")
    finally:
        con.close()
