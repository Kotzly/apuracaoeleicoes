"""Tables behind the "Cargos" dashboard: candidacies, winners, runoffs and seats of each office.

`candidate_results` has one row per candidate and round, with the votes. A supplementary election
has no votes here, because the results only hold the ordinary one; for mayors and governors it still
decides who is `elected` (same rule as `elected_party`). `elected_before` says whether the same full
name won the same office in the same place one term before (8 years for a senator, 4 otherwise): it
is approximate, because the TSE id changes every election and there is no CPF, and NULL when that
election was not loaded.

`unit_winners` has one row per year, office and unit for the one-seat offices: each state for
president and governor, each city for mayor. `party` is the most voted in the last round, and
`holder_party` who ended up with the seat. They differ when a supplementary election replaced the
winner, or when the most voted candidacy was barred. The president has no holder per state, so
continuity follows the most voted party there. `size_band` is the city's electorate.

`runoff_shifts` follows the finalists of governor and mayor between rounds, in the places that voted
in both. The president is in `round_shifts`, whose finalists are the same everywhere.

`office_seats` has seats and candidates of the ordinary election of each year, office and unit.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import parties
from .parties import MUNICIPAL_OFFICES, OFFICES, canonical_party_sql

_STATE_TABLE = "office_seats"  # built last: its comment says the four tables are current
_PRESIDENT, _GOVERNOR, _SENATOR, _MAYOR = 1, 3, 5, 11
_TERM = f"(CASE WHEN c.office_code = {_SENATOR} THEN 8 ELSE 4 END)"  # a senator's seat lasts two elections
_NATIONAL = "BR"
_ORDINARY = "coalesce(election_type, '') NOT LIKE '%SUPLEMENTAR%'"
_MUNICIPAL = ", ".join(map(str, MUNICIPAL_OFFICES))
_NAMES = " ".join(f"WHEN {code} THEN '{name}'" for code, name in OFFICES.items())
_SIZE_BANDS = ((5_000, "Até 5 mil"), (20_000, "5 a 20 mil"), (50_000, "20 a 50 mil"),
               (200_000, "50 a 200 mil"))
_LARGEST_BAND = "Mais de 200 mil"
_TABLES = ("candidate_results", "unit_winners", "runoff_shifts", "office_seats")


def _create_candidate_results(con: duckdb.DuckDBPyConnection) -> None:
    offices = ", ".join(map(str, OFFICES))
    party = canonical_party_sql("c.party")
    place = f"CASE WHEN c.office_code IN ({_MUNICIPAL}) THEN c.municipality_code END"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _ballots AS
        SELECT election_year, round_number, {_PRESIDENT} AS office_code, NULL::VARCHAR AS state,
               NULL::INTEGER AS municipality_code, candidate_number, votes, share_of_valid
        FROM national_results WHERE vote_type = 'Nominal'
        UNION ALL
        SELECT election_year, round_number, office_code, state, NULL, candidate_number, votes, share_of_valid
        FROM state_results
        WHERE vote_type = 'Nominal' AND office_code IN ({offices}) AND office_code <> {_PRESIDENT}
              AND office_code NOT IN ({_MUNICIPAL})
        UNION ALL
        SELECT election_year, round_number, office_code, state, municipality_code, candidate_number, votes, share_of_valid
        FROM municipality_results WHERE vote_type = 'Nominal' AND office_code IN ({_MUNICIPAL})""")
    # A one-seat office only counts the holder of the latest election (see elected.py).
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _candidacies AS
        SELECT c.*, {place} AS place,
               c.result LIKE 'ELEITO%' AND (c.office_code NOT IN ({_GOVERNOR}, {_MAYOR}) OR dense_rank() OVER (
                   PARTITION BY c.election_year, c.office_code, c.state, {place}
                   ORDER BY c.result LIKE 'ELEITO%' DESC, coalesce(c.election_type, '') LIKE '%SUPLEMENTAR%' DESC,
                            c.election_code DESC, c.round_number DESC) = 1) AS elected
        FROM candidates c WHERE c.office_code IN ({offices})""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _elected_names AS
        SELECT DISTINCT election_year, office_code, state, place, full_name FROM _candidacies WHERE elected""")
    con.execute(f"""
        CREATE OR REPLACE TABLE candidate_results AS
        WITH first_year AS (SELECT office_code, min(election_year) AS y FROM _candidacies GROUP BY office_code)
        SELECT c.election_year::SMALLINT AS election_year, c.round_number::TINYINT AS round_number,
               c.election_type, c.office_code::SMALLINT AS office_code, CASE c.office_code {_NAMES} END AS office_name,
               coalesce(c.state, '{_NATIONAL}') AS uf, c.state, c.place AS municipality_code,
               CASE WHEN c.place IS NOT NULL THEN c.municipality_name END AS municipality_name,
               CASE WHEN c.place IS NOT NULL THEN c.municipality_name || ' (' || c.state || ')' END AS label,
               c.candidate_number, c.candidate_name, c.full_name, c.party, {canonical_party_sql('c.party')} AS party_current,
               c.candidacy_status, c.result, c.elected, c.gender, c.race, c.education, c.occupation,
               CASE WHEN {_ORDINARY.replace('election_type', 'c.election_type')} THEN b.votes END::BIGINT AS votes,
               CASE WHEN {_ORDINARY.replace('election_type', 'c.election_type')} THEN b.share_of_valid END AS share_of_valid,
               CASE WHEN c.election_year - {_TERM} < f.y THEN NULL ELSE p.full_name IS NOT NULL END AS elected_before
        FROM _candidacies c
        JOIN first_year f USING (office_code)
        LEFT JOIN _ballots b ON b.election_year = c.election_year AND b.round_number = c.round_number
             AND b.office_code = c.office_code AND b.candidate_number = c.candidate_number
             AND b.state IS NOT DISTINCT FROM (CASE WHEN c.office_code = {_PRESIDENT} THEN NULL ELSE c.state END)
             AND b.municipality_code IS NOT DISTINCT FROM c.place
        LEFT JOIN _elected_names p ON p.election_year = c.election_year - {_TERM} AND p.office_code = c.office_code
             AND p.state IS NOT DISTINCT FROM c.state AND p.place IS NOT DISTINCT FROM c.place
             AND p.full_name = c.full_name
        ORDER BY c.office_code, c.election_year, c.state, c.place, c.round_number, c.candidate_number""")
    print(f"candidate_results: {con.execute('SELECT count(*) FROM candidate_results').fetchone()[0]:,} rows")


def _size_band_sql(column: str) -> str:
    whens = " ".join(f"WHEN {column} <= {limit} THEN '{name}'" for limit, name in _SIZE_BANDS)
    return f"CASE WHEN {column} IS NULL THEN NULL {whens} ELSE '{_LARGEST_BAND}' END"


def _create_unit_winners(con: duckdb.DuckDBPyConnection) -> None:
    party = canonical_party_sql()
    con.execute(f"""
        CREATE OR REPLACE TABLE unit_winners AS
        WITH ballots AS (
            SELECT election_year, round_number, office_code, state, NULL::INTEGER AS municipality_code,
                   NULL::VARCHAR AS municipality_name, candidate_number, candidate_name, {party} AS party,
                   votes, share_of_valid
            FROM state_results WHERE vote_type = 'Nominal' AND office_code IN ({_PRESIDENT}, {_GOVERNOR})
            UNION ALL
            SELECT election_year, round_number, office_code, state, municipality_code, municipality_name,
                   candidate_number, candidate_name, {party}, votes, share_of_valid
            FROM municipality_results WHERE vote_type = 'Nominal' AND office_code = {_MAYOR}),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY election_year, round_number, office_code, state, municipality_code
                                         ORDER BY votes DESC, candidate_number) AS position,
                   max(round_number) OVER (PARTITION BY election_year, office_code, state, municipality_code) AS last_round
            FROM ballots),
        holders AS (
            SELECT DISTINCT election_year, office_code, state, municipality_code, party_current AS holder_party
            FROM candidate_results WHERE elected AND office_code IN ({_GOVERNOR}, {_MAYOR})),
        electorate AS (
            SELECT election_year, state, municipality_code, sum(eligible_voters)::BIGINT AS eligible_voters
            FROM sections WHERE office_code = {_MAYOR} AND round_number = 1 GROUP BY ALL),
        units AS (
            SELECT w.election_year::SMALLINT AS election_year, w.office_code::SMALLINT AS office_code, w.state,
                   w.municipality_code, w.municipality_name, w.last_round = 2 AS has_second_round,
                   w.candidate_number AS winner_number, w.candidate_name AS winner_name, w.party, w.votes,
                   100.0 * w.share_of_valid AS winner_pct,
                   s.candidate_name AS runner_up_name, s.party AS runner_up_party,
                   100.0 * (w.share_of_valid - coalesce(s.share_of_valid, 0)) AS margin_pp,
                   f.candidate_name AS round1_leader_name, f.party AS round1_leader_party,
                   f.candidate_number <> w.candidate_number AS flipped, h.holder_party
            FROM ranked w
            LEFT JOIN ranked s ON s.position = 2 AND s.round_number = w.round_number AND s.election_year = w.election_year
                 AND s.office_code = w.office_code AND s.state = w.state
                 AND s.municipality_code IS NOT DISTINCT FROM w.municipality_code
            LEFT JOIN ranked f ON f.position = 1 AND f.round_number = 1 AND f.election_year = w.election_year
                 AND f.office_code = w.office_code AND f.state = w.state
                 AND f.municipality_code IS NOT DISTINCT FROM w.municipality_code
            LEFT JOIN holders h ON h.election_year = w.election_year AND h.office_code = w.office_code
                 AND h.state = w.state AND h.municipality_code IS NOT DISTINCT FROM w.municipality_code
            WHERE w.position = 1 AND w.round_number = w.last_round
            QUALIFY row_number() OVER (PARTITION BY w.election_year, w.office_code, w.state, w.municipality_code
                                       ORDER BY h.holder_party) = 1)
        SELECT CASE u.office_code {_NAMES} END AS office_name, u.*, u.state AS uf,
               CASE WHEN u.municipality_code IS NOT NULL THEN u.municipality_name || ' (' || u.state || ')' END AS label,
               u.party AS party_current, e.eligible_voters, {_size_band_sql('e.eligible_voters')} AS size_band,
               coalesce(u.holder_party, CASE WHEN u.office_code = {_PRESIDENT} THEN u.party END) AS continuity_party,
               lag(coalesce(u.holder_party, CASE WHEN u.office_code = {_PRESIDENT} THEN u.party END)) OVER previous AS previous_party,
               CASE WHEN lag(u.election_year) OVER previous = u.election_year - 4 THEN
                   lag(coalesce(u.holder_party, CASE WHEN u.office_code = {_PRESIDENT} THEN u.party END)) OVER previous
                   = coalesce(u.holder_party, CASE WHEN u.office_code = {_PRESIDENT} THEN u.party END) END AS same_party_as_previous
        FROM units u
        LEFT JOIN electorate e ON e.election_year = u.election_year AND e.state = u.state
             AND e.municipality_code = u.municipality_code
        WINDOW previous AS (PARTITION BY u.office_code, u.state, u.municipality_code ORDER BY u.election_year)
        ORDER BY u.office_code, u.election_year, u.state, u.municipality_name""")
    print(f"unit_winners: {con.execute('SELECT count(*) FROM unit_winners').fetchone()[0]:,} rows")


def _create_runoff_shifts(con: duckdb.DuckDBPyConnection) -> None:
    """Finalists of governor (per state) and mayor (per city), at every level below their contest."""
    party = canonical_party_sql()
    # -1: not part of the key. A zone may cross cities, so the governor's zones ignore the city.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _runoff_votes AS
        WITH v AS (SELECT *, CASE WHEN office_code = {_MAYOR} THEN municipality_code ELSE -1 END AS contest
                   FROM votes WHERE office_code IN ({_GOVERNOR}, {_MAYOR})),
        contests AS (SELECT DISTINCT election_year, office_code, state, contest FROM v WHERE round_number = 2)
        SELECT election_year, round_number, office_code, state, contest, municipality_code,
               any_value(municipality_name) AS municipality_name, zone, candidate_number,
               any_value(candidate_name) AS candidate_name, any_value({party}) AS party,
               sum(vote_count)::BIGINT AS votes
        FROM v JOIN contests USING (election_year, office_code, state, contest)
        WHERE vote_type = 'Nominal' AND vote_count > 0
        GROUP BY election_year, round_number, office_code, state, contest, municipality_code, zone, candidate_number""")
    # level -> (which rows, the city of a unit, the zone of a unit)
    levels = {"UF": (f"office_code = {_GOVERNOR}", "-1", "-1"),
              "Município": ("true", "municipality_code", "-1"),
              "Zona": ("true", "contest", "zone")}
    keys = "election_year, office_code, state, contest, city, zone_key"
    selects = [f"""
        SELECT * FROM (
        WITH unit_votes AS (
            SELECT election_year, round_number, office_code, state, contest, {city} AS city, {zone} AS zone_key,
                   any_value(municipality_name) AS municipality_name, candidate_number, sum(votes) AS votes
            FROM _runoff_votes WHERE {rows}
            GROUP BY election_year, round_number, office_code, state, contest, city, zone_key, candidate_number),
        places AS (
            SELECT {keys}, any_value(municipality_name) AS municipality_name,
                   sum(votes) FILTER (WHERE round_number = 1) AS total1,
                   sum(votes) FILTER (WHERE round_number = 2) AS total2
            FROM unit_votes GROUP BY {keys}
            HAVING total1 > 0 AND total2 > 0),
        finalists AS (
            SELECT election_year, office_code, state, contest, candidate_number,
                   any_value(candidate_name) AS candidate_name, any_value(party) AS party
            FROM _runoff_votes WHERE round_number = 2
            GROUP BY election_year, office_code, state, contest, candidate_number)
        SELECT p.election_year, p.office_code, '{level}' AS level, p.state,
               nullif(p.city, -1) AS municipality_code,
               CASE WHEN p.city <> -1 THEN p.municipality_name END AS municipality_name,
               nullif(p.zone_key, -1) AS zone, f.candidate_number, f.candidate_name, f.party,
               coalesce(a.votes, 0) AS votes_round1, coalesce(b.votes, 0) AS votes_round2,
               coalesce(a.votes, 0) * 1.0 / p.total1 AS share_round1,
               coalesce(b.votes, 0) * 1.0 / p.total2 AS share_round2
        FROM places p
        JOIN finalists f USING (election_year, office_code, state, contest)
        LEFT JOIN unit_votes a ON a.round_number = 1 AND a.candidate_number = f.candidate_number
             AND a.election_year = p.election_year AND a.office_code = p.office_code AND a.state = p.state
             AND a.contest = p.contest AND a.city = p.city AND a.zone_key = p.zone_key
        LEFT JOIN unit_votes b ON b.round_number = 2 AND b.candidate_number = f.candidate_number
             AND b.election_year = p.election_year AND b.office_code = p.office_code AND b.state = p.state
             AND b.contest = p.contest AND b.city = p.city AND b.zone_key = p.zone_key)"""
               for level, (rows, city, zone) in levels.items()]
    con.execute(f"""
        CREATE OR REPLACE TABLE runoff_shifts AS
        SELECT election_year::SMALLINT AS election_year, office_code::SMALLINT AS office_code,
               CASE office_code {_NAMES} END AS office_name, level, state, state AS uf, municipality_code,
               municipality_name, zone, candidate_number, candidate_name, party, party AS party_current,
               votes_round1::BIGINT AS votes_round1, votes_round2::BIGINT AS votes_round2, share_round1, share_round2,
               share_round2 - share_round1 AS share_change, (votes_round2 - votes_round1)::BIGINT AS votes_change
        FROM ({" UNION ALL ".join(selects)})
        ORDER BY office_code, election_year, level, state, municipality_name, zone, candidate_number""")
    print(f"runoff_shifts: {con.execute('SELECT count(*) FROM runoff_shifts').fetchone()[0]:,} rows")


def _create_office_seats(con: duckdb.DuckDBPyConnection) -> None:
    offices = ", ".join(map(str, OFFICES))
    place = f"CASE WHEN office_code IN ({_MUNICIPAL}) THEN municipality_code END"
    con.execute(f"""
        CREATE OR REPLACE TABLE office_seats AS
        WITH vacancies AS (
            SELECT election_year, office_code, state, {place} AS place, sum(seats) AS seats
            FROM seats WHERE {_ORDINARY} AND office_code IN ({offices}) GROUP BY ALL),
        running AS (
            SELECT election_year, office_code, state, {place} AS place, any_value(municipality_name) AS municipality_name,
                   count(*) AS candidates, count(*) FILTER (WHERE gender = 'FEMININO') AS women
            FROM candidates WHERE {_ORDINARY} AND round_number = 1 AND office_code IN ({offices}) GROUP BY ALL)
        SELECT v.election_year::SMALLINT AS election_year, v.office_code::SMALLINT AS office_code,
               CASE v.office_code {_NAMES} END AS office_name, coalesce(v.state, '{_NATIONAL}') AS uf, v.state,
               v.place AS municipality_code, r.municipality_name,
               CASE WHEN v.place IS NOT NULL THEN r.municipality_name || ' (' || v.state || ')' END AS label,
               v.seats::INTEGER AS seats, coalesce(r.candidates, 0)::INTEGER AS candidates,
               coalesce(r.women, 0)::INTEGER AS women, coalesce(r.candidates, 0) * 1.0 / nullif(v.seats, 0) AS candidates_per_seat
        FROM vacancies v
        LEFT JOIN running r ON r.election_year = v.election_year AND r.office_code = v.office_code
             AND r.state IS NOT DISTINCT FROM v.state AND r.place IS NOT DISTINCT FROM v.place
        ORDER BY v.office_code, v.election_year, v.state, v.place""")
    print(f"office_seats: {con.execute('SELECT count(*) FROM office_seats').fetchone()[0]:,} rows")


def build(db: Path, force: bool = False) -> None:
    """Rebuilds the four tables, unless results, candidates and renames are unchanged."""
    if not parties.has_tables(db, "national_results", "state_results", "municipality_results", "candidates", "seats"):
        print("warning: office tables not built: the results, `candidates` or `seats` could not be loaded")
        return
    if not force and parties.is_current(db, _STATE_TABLE, uses_candidates=True) and parties.has_tables(db, *_TABLES):
        print("office tables: up to date")
        return
    con = duckdb.connect(str(db))
    try:
        con.begin()
        _create_candidate_results(con)
        _create_unit_winners(con)
        _create_runoff_shifts(con)
        _create_office_seats(con)
        con.execute(f"COMMENT ON TABLE {_STATE_TABLE} IS '{parties.source_state(con, True)}'")
        con.commit()
        con.execute("CHECKPOINT")
    finally:
        con.close()
