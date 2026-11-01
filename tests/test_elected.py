"""Elected by party and the mayors' flow, on small hand-made elections."""

import duckdb
import pytest

from election_tally import elected, results
from election_tally.load import create_schema

ORDINARY, SUPPLEMENTARY = "ELEIÇÃO ORDINÁRIA", "ELEIÇÃO SUPLEMENTAR"
# (year, round, election code, type, office, state, city, number, party, result)
CANDIDATES = [
    # City 1: the ordinary winner was replaced by the supplementary election.
    (2016, 1, 220, ORDINARY, 11, "SP", 1, 13, "PT", "ELEITO"),
    (2016, 1, 230, SUPPLEMENTARY, 11, "SP", 1, 45, "PSDB", "ELEITO"),
    (2016, 1, 230, SUPPLEMENTARY, 11, "SP", 1, 15, "PMDB", "NÃO ELEITO"),
    # City 2: an old name; another state with the same city code never mixes in.
    (2016, 1, 220, ORDINARY, 11, "SP", 2, 15, "PMDB", "ELEITO"),
    (2016, 1, 220, ORDINARY, 11, "MG", 2, 13, "PT", "ELEITO"),
    # City 3: won in the second round.
    (2016, 1, 220, ORDINARY, 11, "SP", 3, 13, "PT", "2º TURNO"),
    (2016, 2, 220, ORDINARY, 11, "SP", 3, 40, "PSB", "ELEITO"),
    (2016, 2, 220, ORDINARY, 11, "SP", 3, 13, "PT", "NÃO ELEITO"),
    # City 4: nobody elected in 2016.
    (2016, 1, 220, ORDINARY, 11, "SP", 4, 22, "PR", "NÃO ELEITO"),
    (2020, 1, 420, ORDINARY, 11, "SP", 1, 13, "PT", "ELEITO"),
    (2020, 1, 420, ORDINARY, 11, "SP", 2, 15, "MDB", "ELEITO"),
    (2020, 1, 420, ORDINARY, 11, "MG", 2, 13, "PT", "ELEITO"),
    (2020, 1, 420, ORDINARY, 11, "SP", 3, 40, "PSB", "ELEITO"),
    (2020, 1, 420, ORDINARY, 11, "SP", 4, 22, "PL", "ELEITO"),
    (2024, 1, 620, ORDINARY, 11, "SP", 1, 13, "PT", "ELEITO"),
    (2024, 1, 620, ORDINARY, 11, "SP", 2, 13, "PT", "ELEITO"),
    (2024, 1, 620, ORDINARY, 11, "MG", 2, 13, "PT", "ELEITO"),
    (2024, 1, 620, ORDINARY, 11, "SP", 3, 40, "PSB", "NÃO ELEITO"),
    (2024, 1, 620, ORDINARY, 11, "SP", 4, 22, "PL", "ELEITO"),
    # Federal deputies of SP in 2022 and the president, without a state.
    (2022, 1, 544, ORDINARY, 6, "SP", None, 1301, "PT", "ELEITO POR QP"),
    (2022, 1, 544, ORDINARY, 6, "SP", None, 1302, "PT", "ELEITO POR MÉDIA"),
    (2022, 1, 544, ORDINARY, 6, "SP", None, 4501, "PSDB", "ELEITO POR QP"),
    (2022, 1, 544, ORDINARY, 6, "SP", None, 3001, "NOVO", "NÃO ELEITO"),
    (2022, 1, 544, ORDINARY, 1, None, None, 13, "PT", "2º TURNO"),
    (2022, 2, 545, ORDINARY, 1, None, None, 13, "PT", "ELEITO"),
    (2022, 2, 545, ORDINARY, 1, None, None, 22, "PL", "NÃO ELEITO"),
]
# (year, office, state, city, number, party, votes) in round 1
VOTES = [(2022, 6, "SP", 1, 1301, "PT", 60), (2022, 6, "SP", 1, 4501, "PSDB", 30),
         (2022, 6, "SP", 1, 3001, "NOVO", 10), (2022, 6, "MG", 2, 1301, "PT", 10),
         (2022, 6, "MG", 2, 3001, "NOVO", 10),
         (2022, 1, "SP", 1, 13, "PT", 70), (2022, 1, "MG", 2, 22, "PL", 30)]


def _db(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    con.execute("""CREATE TABLE candidates (election_year INTEGER, round_number INTEGER,
        election_code INTEGER, election_type VARCHAR, state VARCHAR, municipality_code INTEGER,
        municipality_name VARCHAR, office_code INTEGER, candidate_number INTEGER, party VARCHAR,
        result VARCHAR)""")
    for year, rnd, code, kind, office, state, city, number, party, result in CANDIDATES:
        con.execute("INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [year, rnd, code, kind, state, city, f"CIDADE {city}" if city else None,
                     office, number, party, result])
    for year, office, state, city, number, party, count in VOTES:
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                       municipality_name, zone, section, office_code, vote_type, candidate_number,
                       candidate_name, party, vote_count)
                       VALUES (?, 1, ?, ?, 'X', 1, 1, ?, 'Nominal', ?, 'N', ?, ?)""",
                    [year, state, city, office, number, party, count])
    con.close()
    results.build(path)
    elected.build(path)
    return path


def _rows(path, sql, *args):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql, list(args)).fetchall()
    finally:
        con.close()


def test_one_mayor_per_city_and_the_supplementary_election_wins(tmp_path):
    path = _db(tmp_path)
    got = _rows(path, """SELECT party, elected FROM elected_party
                         WHERE election_year = 2016 AND office_code = 11 AND state = 'SP'
                         ORDER BY party""")
    # city 1: PSDB (supplementary), city 2: MDB (renamed PMDB), city 3: PSB (second round)
    assert [r for r in got if r[1]] == [("MDB", 1), ("PSB", 1), ("PSDB", 1)]
    total = _rows(path, """SELECT sum(elected) FROM elected_party
                           WHERE election_year = 2016 AND office_code = 11""")
    assert total == [(4,)]          # the same city code in MG is another city
    assert _rows(path, """SELECT DISTINCT office_name FROM elected_party
                          WHERE office_code = 11""") == [("Prefeito",)]


def test_seat_and_vote_shares_within_the_state(tmp_path):
    path = _db(tmp_path)
    rows = _rows(path, """SELECT party, elected, votes, vote_share, seat_share, seats_total,
                          votes_total FROM elected_party WHERE office_code = 6 AND state = 'SP'
                          ORDER BY party""")
    by = {r[0]: r[1:] for r in rows}
    assert by["PT"] == (2, 60, pytest.approx(0.6), pytest.approx(2 / 3), 3, 100)
    assert by["NOVO"][:2] == (0, 10) and by["NOVO"][3] == 0       # ran, elected nobody
    # MG has no deputy elected: the party is there with its votes and no seat share
    mg = _rows(path, "SELECT sum(elected), sum(seat_share) FROM elected_party WHERE state = 'MG' AND office_code = 6")
    assert mg == [(0, None)]
    # Nationally, share = SUM(elected) / SUM of the state totals, not the average of shares
    national = _rows(path, """SELECT sum(elected) FILTER (WHERE party = 'PT') * 1.0
                              / (SELECT sum(elected) FROM elected_party WHERE office_code = 6)
                              FROM elected_party WHERE office_code = 6""")
    assert national == [(pytest.approx(2 / 3),)]


def test_president_is_national_and_elected_in_the_second_round(tmp_path):
    path = _db(tmp_path)
    rows = _rows(path, """SELECT state, party, elected, votes FROM elected_party
                          WHERE office_code = 1 ORDER BY party""")
    assert rows == [("BR", "PL", 0, 30), ("BR", "PT", 1, 70)]


def test_mayor_flows_have_no_mayor_node_and_years_in_labels(tmp_path):
    path = _db(tmp_path)
    rows = _rows(path, """SELECT transition, state, municipality_name, source_party, target_party,
                          source, target, units FROM mayor_flows ORDER BY 1, 2, 3""")
    got = {(r[0], r[1], r[2]): r[3:] for r in rows}
    # city 4 had nobody elected in 2016
    assert got[("2016 → 2020", "SP", "CIDADE 4")][:2] == ("Sem eleito", "PL")
    assert got[("2016 → 2020", "SP", "CIDADE 4")][2] == "2016 · Sem eleito"
    # PMDB is MDB in 2016, so the party of city 2 is the same in 2016 and 2020
    assert got[("2016 → 2020", "SP", "CIDADE 2")][:2] == ("MDB", "MDB")
    # city 3 has no elected mayor in 2024
    assert got[("2020 → 2024", "SP", "CIDADE 3")][:2] == ("PSB", "Sem eleito")
    assert len(rows) == 10           # 5 cities, 2 transitions
    # The year is in every label, so a link never joins a node to itself: no cycles.
    assert all(r[5].startswith(r[0][:4]) and r[6].startswith(r[0][-4:]) for r in rows)
    assert all(r[5] != r[6] for r in rows)


def test_parties_outside_the_top_are_grouped_as_others(tmp_path, monkeypatch):
    monkeypatch.setattr(elected, "TOP_PARTIES", 2)
    path = _db(tmp_path)
    # Mayors in the three years: PT 6, then MDB, PSB, PL with 2 each (MDB first by name).
    rows = _rows(path, "SELECT source_party, source, target_party, target FROM mayor_flows")
    sides = [(r[0], r[1]) for r in rows] + [(r[2], r[3]) for r in rows]
    others = {party for party, node in sides if node.endswith("· Outros")}
    assert others == {"PSDB", "PSB", "PL"}                 # the real party stays in *_party
    assert all(node.endswith(party) for party, node in sides if party in ("PT", "MDB", "Sem eleito"))


def _senate_db(tmp_path, rows):
    """rows: (year, state, party) of each elected senator."""
    path = tmp_path / "s.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    con.execute("""CREATE TABLE candidates (election_year INTEGER, round_number INTEGER,
        election_code INTEGER, election_type VARCHAR, state VARCHAR, municipality_code INTEGER,
        municipality_name VARCHAR, office_code INTEGER, candidate_number INTEGER, party VARCHAR,
        result VARCHAR)""")
    for number, (year, state, party) in enumerate(rows):
        con.execute("INSERT INTO candidates VALUES (?, 1, 1, ?, ?, NULL, NULL, 5, ?, ?, 'ELEITO')",
                    [year, ORDINARY, state, number, party])
    # A loser never takes a seat.
    con.execute("INSERT INTO candidates VALUES (2022, 1, 1, ?, 'SP', NULL, NULL, 5, 99, 'NOVO', 'NÃO ELEITO')",
                [ORDINARY])
    con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                   municipality_name, zone, section, office_code, vote_type, candidate_number,
                   candidate_name, party, vote_count)
                   VALUES (2022, 1, 'SP', 1, 'X', 1, 1, 1, 'Nominal', 13, 'N', 'PT', 10)""")
    con.close()
    results.build(path)
    elected.build(path)
    return path


def test_senate_seats_add_each_election_to_the_previous_one(tmp_path):
    path = _senate_db(tmp_path, [(2014, "SP", "PT"),
                                 (2018, "SP", "PT"), (2018, "SP", "PMDB"),
                                 (2022, "SP", "PT"), (2026, "SP", "PL"), (2026, "SP", "PT")])
    got = _rows(path, """SELECT election_year, party, seats_this_election, seats_previous_election, seats
                         FROM senate_seats ORDER BY 1, 2""")
    assert got == [(2018, "MDB", 1, 0, 1), (2018, "PT", 1, 1, 2),      # 2014 has no previous election
                   (2022, "MDB", 0, 1, 1), (2022, "PT", 1, 1, 2),
                   (2026, "PL", 1, 0, 1), (2026, "PT", 1, 1, 2)]       # 2018 no longer counts in 2026


def test_senate_seats_join_dem_and_psl_into_uniao_only_after_the_merger(tmp_path):
    path = _senate_db(tmp_path, [(2014, "BA", "DEM"), (2018, "BA", "DEM"), (2018, "RJ", "PSL"),
                                 (2022, "BA", "UNIÃO"), (2022, "RJ", "PTB")])
    got = _rows(path, "SELECT election_year, state, party, seats FROM senate_seats ORDER BY 1, 2, 3")
    assert got == [(2018, "BA", "DEM", 2), (2018, "RJ", "PSL", 1),     # before it, they are separate
                   (2022, "BA", "UNIÃO", 2), (2022, "RJ", "PTB", 1), (2022, "RJ", "UNIÃO", 1)]
    # Only this table joins them.
    assert _rows(path, """SELECT elected FROM elected_party WHERE office_code = 5
                          AND election_year = 2018 AND party = 'DEM'""") == [(1,)]


def test_senate_seats_exist_without_senators_and_when_only_they_are_missing(tmp_path, capsys):
    path = _db(tmp_path)
    assert _rows(path, "SELECT count(*) FROM senate_seats") == [(0,)]
    con = duckdb.connect(str(path))
    con.execute("DROP TABLE senate_seats")
    con.close()
    capsys.readouterr()
    elected.build(path)
    assert "senate_seats:" in capsys.readouterr().out


def test_municipal_party_adds_up_to_elected_party(tmp_path):
    path = _db(tmp_path)
    by_city = """SELECT election_year, office_code, state, party, sum(elected) FROM municipal_party
                 WHERE elected > 0 GROUP BY ALL ORDER BY ALL"""
    by_state = """SELECT election_year, office_code, state, party, sum(elected) FROM elected_party
                  WHERE elected > 0 AND office_code IN (11, 13) GROUP BY ALL ORDER BY ALL"""
    assert _rows(path, by_city) == _rows(path, by_state) != []
    # the same city code in MG is another city
    assert _rows(path, """SELECT label, party, elected FROM municipal_party
                          WHERE election_year = 2024 AND municipality_code = 2 ORDER BY label""") == [
        ("CIDADE 2 (MG)", "PT", 1), ("CIDADE 2 (SP)", "PT", 1)]


def test_municipal_party_counts_first_round_votes_under_the_results_spelling(tmp_path):
    path = _db(tmp_path)
    con = duckdb.connect(str(path))
    for rnd, votes in ((1, 70), (2, 500)):
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, municipality_name,
                       zone, section, office_code, vote_type, candidate_number, candidate_name, party, vote_count)
                       VALUES (2024, ?, 'SP', 1, 'CIDADE UM', 1, 1, 11, 'Nominal', 13, 'N', 'PT', ?)""", [rnd, votes])
    con.close()
    results.build(path, force=True)
    elected.build(path, force=True)
    assert _rows(path, """SELECT label, party, elected, votes FROM municipal_party
                          WHERE election_year = 2024 AND office_code = 11 AND state = 'SP' AND municipality_code = 1
                          """) == [("CIDADE UM (SP)", "PT", 1, 70)]


def test_an_older_database_gets_municipal_party_without_forcing(tmp_path):
    path = _db(tmp_path)
    con = duckdb.connect(str(path))
    con.execute("DROP TABLE municipal_party")
    con.close()
    elected.build(path)
    assert _rows(path, "SELECT count(*) FROM municipal_party")[0][0] > 0
