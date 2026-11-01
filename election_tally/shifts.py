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
the table would otherwise grow by gigabytes (`SECTION_OFFICES`). Each pair of elections comes from
the years loaded for that office, so a new year needs no change here.
"""

from __future__ import annotations

from pathlib import Path

from . import derived, parties
from .domain import (BLANK_AND_NULL, COUNTED_VOTES, OFFICE_LIST, OFFICES, PRESIDENT, VALID_VOTES,
                     VOTES_WITH_PARTY, consecutive_elections)
from .parties import canonical_party_sql

_STATE_TABLE = "party_shifts"
_ROUND_TABLES = ("round_shifts", "state_round_shifts", "zone_round_shifts")
SECTION_OFFICES = (1, 3, 5, 11)  # majoritarian offices; the others stop at Zona
# Level -> the columns that identify a unit (each level has a temp table `base_<level>` with them).
_LEVELS = {
    "Brasil": ("nation",),
    "UF": ("state",),
    "Município": ("municipality_code",),
    "Zona": ("state", "zone"),
    "Seção": ("municipality_code", "zone", "section"),
}
_UNIT_COLUMNS = ("state, state_iso, municipality_code, municipality_name, "
                 "municipality_ibge_code, zone, section")


def _offices_of(level: str) -> tuple[int, ...]:
    return SECTION_OFFICES if level == "Seção" else tuple(OFFICES)


def _create_bases(con: derived.Connection) -> None:
    """One temp table per level: valid votes by year, round, office, unit and party.

    The party is NULL for votes of candidates without one, which still count in the total. Blank
    and null votes use their type as the party.
    """
    party = f"CASE WHEN vote_type IN {BLANK_AND_NULL} THEN vote_type ELSE {canonical_party_sql()} END"
    head = "election_year, round_number, office_code"
    con.execute("""
        CREATE OR REPLACE TEMP TABLE municipality_dim AS
        SELECT municipality_code, any_value(state) AS state,
               arg_max(municipality_name, (election_year, round_number)) AS municipality_name,
               any_value(municipality_ibge_code) AS municipality_ibge_code
        FROM municipality_results GROUP BY municipality_code""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_município AS
        SELECT {head}, state, municipality_code, {party} AS party, sum(votes)::INTEGER AS votes
        FROM municipality_results
        WHERE office_code IN ({OFFICE_LIST}) AND {COUNTED_VOTES}
        GROUP BY ALL ORDER BY office_code, election_year, round_number, municipality_code""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_uf AS
        SELECT {head}, state, party, sum(votes)::BIGINT AS votes
        FROM base_município GROUP BY ALL ORDER BY office_code, election_year, round_number, state""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_brasil AS
        SELECT {head}, 0 AS nation, party, sum(votes)::BIGINT AS votes FROM base_uf GROUP BY ALL""")
    section_offices = ", ".join(map(str, SECTION_OFFICES))
    for level, offices in (("zona", OFFICE_LIST), ("seção", section_offices)):
        keys = ", ".join(_LEVELS[level.capitalize()])
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE base_{level} AS
            SELECT {head}, {keys}, {party} AS party, sum(vote_count)::INTEGER AS votes
            FROM {VOTES_WITH_PARTY}
            WHERE vote_count > 0 AND {COUNTED_VOTES} AND office_code IN ({offices})
            GROUP BY ALL ORDER BY office_code, election_year, round_number, {keys}""")


def _with_unit_columns(level: str, source: str, columns: str) -> str:
    """SELECT of `columns` over `source` (which has the level's keys), adding the unit's columns."""
    keys = _LEVELS[level]
    named_by_city = "municipality_code" in keys
    state = "m.state" if named_by_city else "state" if "state" in keys else "NULL::VARCHAR"
    join = "LEFT JOIN municipality_dim m USING (municipality_code)" if named_by_city else ""
    def key(name: str) -> str:
        return name if name in keys else "NULL::INTEGER"
    return f"""
        SELECT {state} AS state, 'BR-' || ({state}) AS state_iso,
               {key('municipality_code')} AS municipality_code,
               {'m.municipality_name' if named_by_city else 'NULL::VARCHAR'} AS municipality_name,
               {'m.municipality_ibge_code' if named_by_city else 'NULL::INTEGER'} AS municipality_ibge_code,
               {key('zone')} AS zone, {key('section')} AS section, {columns}
        FROM {source} {join}"""


def _build_pair(con: derived.Connection, level: str, office: int, start: int, end: int,
                round_number: int) -> None:
    keys = ", ".join(_LEVELS[level])
    side = (f"SELECT {keys}, party, votes FROM base_{level.lower()} "
            f"WHERE election_year = {{}} AND round_number = {round_number} AND office_code = {office}")
    transition = f"{start} → {end} T{round_number}"
    valid = f"sum(votes) FILTER (WHERE coalesce(party, '') NOT IN {BLANK_AND_NULL})"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE batch AS
        WITH a AS ({side.format(start)}), b AS ({side.format(end)}),
        units AS (SELECT {keys}, ta, tb, all_a, all_b FROM
                     (SELECT {keys}, {valid} AS ta, sum(votes) AS all_a FROM a GROUP BY {keys})
                     JOIN (SELECT {keys}, {valid} AS tb, sum(votes) AS all_b FROM b GROUP BY {keys}) USING ({keys})
                  WHERE ta > 0 AND tb > 0)
        SELECT {keys}, party, coalesce(a.votes, 0)::BIGINT AS votes_from,
               coalesce(b.votes, 0)::BIGINT AS votes_to,
               coalesce(a.votes, 0) * 1.0 / CASE WHEN party IN {BLANK_AND_NULL} THEN all_a ELSE ta END AS share_from,
               coalesce(b.votes, 0) * 1.0 / CASE WHEN party IN {BLANK_AND_NULL} THEN all_b ELSE tb END AS share_to,
               coalesce(a.votes, 0) * 1.0 / all_a AS share_from_all,
               coalesce(b.votes, 0) * 1.0 / all_b AS share_to_all
        FROM (SELECT * FROM a WHERE party IS NOT NULL) a
             FULL JOIN (SELECT * FROM b WHERE party IS NOT NULL) b USING ({keys}, party)
             JOIN units USING ({keys})""")
    head = f"'{level}', {office}, '{OFFICES[office]}', {round_number}, '{transition}', {start}, {end}"
    order = "ORDER BY state, municipality_name, zone, section"
    measures = ('party, votes_from, votes_to, share_from, share_to, share_to - share_from AS share_change, '
                'share_from_all, share_to_all')
    con.execute(f"""
        INSERT INTO party_shifts
        SELECT {head}, {_UNIT_COLUMNS}, party, votes_from, votes_to, share_from, share_to,
               share_change, share_from_all, share_to_all
        FROM ({_with_unit_columns(level, 'batch', measures)})
        {order}, party""")
    parties_only = f"FILTER (WHERE party NOT IN {BLANK_AND_NULL})"
    pedersen = f"""(SELECT {keys}, 0.5 * sum(abs(share_to - share_from)) {parties_only} AS pedersen_index,
                           (count(*) {parties_only})::INTEGER AS parties,
                           0.5 * sum(abs(share_to_all - share_from_all)) AS pedersen_index_all
                    FROM batch GROUP BY {keys} HAVING count(*) {parties_only} > 0)"""
    con.execute(f"""
        INSERT INTO unit_volatility
        SELECT {head}, {_UNIT_COLUMNS}, pedersen_index, parties, pedersen_index_all
        FROM ({_with_unit_columns(level, pedersen, 'pedersen_index, parties, pedersen_index_all')}) {order}""")


def _create_tables(con: derived.Connection) -> None:
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


def _nominal_rows(source: str, place: str) -> str:
    """Nominal votes of `source`, with the share over all the votes of the row's place and round."""
    return f"""
        SELECT * FROM (
            SELECT *, votes * 1.0 / sum(votes) FILTER (WHERE {COUNTED_VOTES}) OVER (
                       PARTITION BY election_year, round_number, {place}) AS share_of_all
            FROM {source})
        WHERE vote_type = 'Nominal'"""


def _build_round_table(con: derived.Connection, table: str, rows: str, keys: tuple[str, ...],
                       place_columns: tuple[str, ...] | None = None) -> None:
    """Each finalist's votes in the two rounds, in every place (`keys`) that voted in both.

    A finalist who got no votes in a place in one round has 0 there, not a missing row. `rows` has
    the year, round, keys, candidate number, votes and shares of the place's own votes, so a share
    is never the mean of smaller units, which would weigh a village like a capital. `place_columns`
    are the place's columns to keep, in order: its keys, unless it has others, such as a name.
    """
    party = canonical_party_sql()
    key_list = ", ".join(keys)
    place_columns = place_columns or keys
    extras = "".join(f", any_value({c}) AS {c}" for c in place_columns if c not in keys)
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
            SELECT election_year, {key_list}{extras} FROM r GROUP BY election_year, {key_list}
            HAVING count(DISTINCT round_number) = 2)
        SELECT p.election_year::SMALLINT AS election_year,
               {', '.join(f'p.{c}' for c in place_columns)}, f.candidate_number,
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
        ORDER BY election_year, {', '.join(place_columns)}, candidate_number""")
    derived.report_rows(con, table)


def _build_round_tables(con: derived.Connection) -> None:
    _build_round_table(
        con, "round_shifts",
        _nominal_rows(f"(SELECT * FROM municipality_results WHERE office_code = {PRESIDENT})",
                      "municipality_code"),
        ("municipality_code",),
        ("state", "municipality_code", "municipality_name", "municipality_ibge_code"))
    # Municipalities that voted in only one round still count, so a state's votes may differ
    # slightly from the sum of its rows in `round_shifts`.
    _build_round_table(
        con, "state_round_shifts",
        _nominal_rows(f"""(SELECT election_year, round_number, state, candidate_number, votes,
                                  share_of_valid, vote_type
                           FROM state_results WHERE office_code = {PRESIDENT})""", "state"),
        ("state",))
    # A zone is (state, zone), since numbers repeat between states. Rounds of a year share one map.
    _build_round_table(
        con, "zone_round_shifts",
        _nominal_rows(f"""(
            SELECT election_year, round_number, state, zone, candidate_number, votes, vote_type,
                   votes * 1.0 / sum(votes) FILTER (WHERE {VALID_VOTES}) OVER (
                       PARTITION BY election_year, round_number, state, zone) AS share_of_valid
            FROM (SELECT election_year, round_number, state, zone::INTEGER AS zone, vote_type,
                         candidate_number, sum(vote_count)::BIGINT AS votes
                  FROM votes
                  WHERE office_code = {PRESIDENT} AND vote_count > 0 AND {COUNTED_VOTES}
                  GROUP BY ALL))""", "state, zone"),
        ("state", "zone"))


def build(db: Path, force: bool = False) -> None:
    """Rebuilds these tables, unless the results and renames are unchanged.

    When those are unchanged and only a round table is missing, only the round tables are built:
    `party_shifts` takes minutes.
    """
    if not derived.has_tables(db, "national_results", "municipality_results"):
        print("warning: party_shifts not built: the results tables are missing")
        return
    current = not force and parties.is_current(db, _STATE_TABLE)
    if current and derived.has_tables(db, *_ROUND_TABLES):
        print("party_shifts: up to date")
        return
    with derived.rebuilding(db) as con:
        con.execute("SET memory_limit = '4GB'")
        _build_round_tables(con)
        if current:
            return
        _create_bases(con)
        _create_tables(con)
        for level in _LEVELS:
            for office in _offices_of(level):
                years = [row[0] for row in con.execute(
                    "SELECT DISTINCT election_year FROM municipality_results WHERE office_code = ?",
                    [office]).fetchall()]
                for start, end in consecutive_elections(years):
                    for round_number in (1, 2):
                        _build_pair(con, level, office, start, end, round_number)
        derived.mark_current(con, _STATE_TABLE, parties.source_state(con))
    with derived.reading(db) as con:
        derived.report_rows(con, "party_shifts", "unit_volatility")
