"""Tables behind the "Cargos" dashboard, on a small hand-made set of elections."""

import duckdb
import pytest

from election_tally import office_analysis, results
from election_tally.load import create_schema

ORD, SUP = "ELEIÇÃO ORDINÁRIA", "ELEIÇÃO SUPLEMENTAR"
# (year, round, code, type, office, state, city, number, full name, party, result, gender)
CANDIDATES = [
    # Governor of SP: the first-round leader loses the runoff.
    (2018, 1, 1, ORD, 3, "SP", None, 45, "ANA", "PSDB", "2º TURNO", "FEMININO"),
    (2018, 1, 1, ORD, 3, "SP", None, 13, "BETO", "PT", "2º TURNO", "MASCULINO"),
    (2018, 1, 1, ORD, 3, "SP", None, 22, "CAIO", "PL", "NÃO ELEITO", "MASCULINO"),
    (2018, 2, 1, ORD, 3, "SP", None, 45, "ANA", "PSDB", "NÃO ELEITO", "FEMININO"),
    (2018, 2, 1, ORD, 3, "SP", None, 13, "BETO", "PT", "ELEITO", "MASCULINO"),
    # Governor of SP again: re-elected in the first round, same party.
    (2022, 1, 2, ORD, 3, "SP", None, 13, "BETO", "PT", "ELEITO", "MASCULINO"),
    (2022, 1, 2, ORD, 3, "SP", None, 45, "ANA", "PSDB", "NÃO ELEITO", "FEMININO"),
    # Mayor of city 1: a supplementary election replaces the ordinary winner.
    (2016, 1, 3, ORD, 11, "SP", 1, 13, "DIA", "PT", "ELEITO", "FEMININO"),
    (2016, 1, 4, SUP, 11, "SP", 1, 45, "EVA", "PSDB", "ELEITO", "FEMININO"),
    (2020, 1, 5, ORD, 11, "SP", 1, 45, "EVA", "PSDB", "ELEITO", "FEMININO"),
    # A namesake of EVA, in another city, is not her re-election.
    (2020, 1, 5, ORD, 11, "SP", 2, 15, "EVA", "PMDB", "ELEITO", "FEMININO"),
    # Federal deputies, with an old party name.
    (2018, 1, 1, ORD, 6, "SP", None, 1501, "GIL", "PMDB", "ELEITO POR QP", "MASCULINO"),
    (2022, 1, 2, ORD, 6, "SP", None, 1501, "GIL", "MDB", "ELEITO POR MÉDIA", "MASCULINO"),
    (2022, 1, 2, ORD, 6, "SP", None, 1302, "HIL", "PT", "NÃO ELEITO", "FEMININO"),
    # President.
    (2022, 1, 2, ORD, 1, None, None, 13, "IVO", "PT", "2º TURNO", "MASCULINO"),
    (2022, 1, 2, ORD, 1, None, None, 22, "JOE", "PL", "2º TURNO", "MASCULINO"),
    (2022, 2, 6, ORD, 1, None, None, 13, "IVO", "PT", "ELEITO", "MASCULINO"),
    (2022, 2, 6, ORD, 1, None, None, 22, "JOE", "PL", "NÃO ELEITO", "MASCULINO"),
]
# (year, round, office, state, city, zone, kind, number, party, votes)
N, B = "Nominal", "Branco"
VOTES = [
    (2018, 1, 3, "SP", 1, 1, N, 45, "PSDB", 50), (2018, 1, 3, "SP", 1, 1, N, 13, "PT", 40),
    (2018, 1, 3, "SP", 1, 1, N, 22, "PL", 10), (2018, 1, 3, "SP", 1, 1, B, 95, None, 50),
    (2018, 2, 3, "SP", 1, 1, N, 45, "PSDB", 45), (2018, 2, 3, "SP", 1, 1, N, 13, "PT", 55),
    (2022, 1, 3, "SP", 1, 1, N, 13, "PT", 70), (2022, 1, 3, "SP", 1, 1, N, 45, "PSDB", 30),
    (2016, 1, 11, "SP", 1, 1, N, 13, "PT", 60), (2016, 1, 11, "SP", 1, 1, N, 45, "PSDB", 40),
    (2020, 1, 11, "SP", 1, 1, N, 45, "PSDB", 80), (2020, 1, 11, "SP", 1, 1, N, 13, "PT", 20),
    (2020, 1, 11, "SP", 2, 2, N, 15, "PMDB", 10),
    (2018, 1, 6, "SP", 1, 1, N, 1501, "PMDB", 90), (2018, 1, 6, "SP", 1, 1, "Legenda", 15, "PMDB", 10),
    (2022, 1, 6, "SP", 1, 1, N, 1501, "MDB", 70), (2022, 1, 6, "SP", 1, 1, N, 1302, "PT", 30),
    (2022, 1, 1, "SP", 1, 1, N, 13, "PT", 40), (2022, 1, 1, "SP", 1, 1, N, 22, "PL", 60),
    (2022, 1, 1, "MG", 2, 1, N, 13, "PT", 90), (2022, 1, 1, "MG", 2, 1, N, 22, "PL", 10),
    (2022, 2, 1, "SP", 1, 1, N, 13, "PT", 45), (2022, 2, 1, "SP", 1, 1, N, 22, "PL", 55),
    (2022, 2, 1, "MG", 2, 1, N, 13, "PT", 80), (2022, 2, 1, "MG", 2, 1, N, 22, "PL", 20),
]
# (year, type, office, state, city, seats)
SEATS = [(2018, ORD, 6, "SP", None, 70), (2022, ORD, 6, "SP", None, 70), (2022, ORD, 1, None, None, 1),
         (2016, ORD, 11, "SP", 1, 1), (2016, SUP, 11, "SP", 1, 1), (2020, ORD, 11, "SP", 1, 1)]
# (year, office, state, city, zone, section, eligible)
SECTIONS = [(2016, 11, "SP", 1, 1, 1, 4000), (2016, 11, "SP", 1, 1, 2, 2000), (2020, 11, "SP", 1, 1, 1, 250000),
            (2020, 11, "SP", 2, 2, 1, 100)]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("o") / "o.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    con.execute("""CREATE TABLE candidates (election_year INTEGER, round_number INTEGER, election_code INTEGER,
        election_type VARCHAR, state VARCHAR, municipality_code INTEGER, municipality_name VARCHAR,
        office_code INTEGER, candidate_id BIGINT, candidate_number INTEGER, candidate_name VARCHAR,
        full_name VARCHAR, party VARCHAR, candidacy_status VARCHAR, result VARCHAR, gender VARCHAR,
        race VARCHAR, education VARCHAR, occupation VARCHAR)""")
    for i, (year, rnd, code, kind, office, state, city, number, name, party, result, gender) in enumerate(CANDIDATES):
        con.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'APTO',?,?,'PARDA','SUPERIOR','X')",
                    [year, rnd, code, kind, state, city, f"CIDADE {city}" if city else None, office, i, number,
                     name, name, party, result, gender])
    con.execute("""CREATE TABLE seats (election_year INTEGER, election_type VARCHAR, office_code INTEGER,
        state VARCHAR, municipality_code INTEGER, seats INTEGER)""")
    con.executemany("INSERT INTO seats VALUES (?,?,?,?,?,?)", SEATS)
    for year, rnd, office, state, city, zone, kind, number, party, count in VOTES:
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, municipality_name,
                       zone, section, office_code, vote_type, candidate_number, candidate_name, party, vote_count)
                       VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?)""",
                    [year, rnd, state, city, f"CIDADE {city}", zone, office, kind, number, f"C{number}", party, count])
    for year, office, state, city, zone, section, eligible in SECTIONS:
        con.execute("""INSERT INTO sections (election_year, round_number, state, municipality_code, zone, section,
                       office_code, eligible_voters) VALUES (?,1,?,?,?,?,?,?)""",
                    [year, state, city, zone, section, office, eligible])
    con.close()
    results.build(path)
    office_analysis.build(path)
    return path


def _rows(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_candidate_votes_come_from_the_table_of_each_office_and_round(db):
    assert _rows(db, """SELECT votes, round(share_of_valid, 2), party_current, uf FROM candidate_results
                        WHERE election_year = 2018 AND office_code = 6""") == [(90, 0.90, "MDB", "SP")]
    assert _rows(db, """SELECT round_number, votes FROM candidate_results WHERE election_year = 2022 AND office_code = 1
                        AND candidate_number = 13 ORDER BY 1""") == [(1, 130), (2, 125)]
    assert _rows(db, """SELECT DISTINCT uf FROM candidate_results WHERE office_code = 1""") == [("BR",)]


def test_supplementary_election_replaces_the_winner_and_keeps_only_its_candidacy_elected(db):
    assert _rows(db, """SELECT full_name FROM candidate_results WHERE office_code = 11 AND election_year = 2016
                        AND elected""") == [("EVA",)]


def test_candidates_of_a_supplementary_election_never_take_the_ordinary_votes(db):
    assert _rows(db, """SELECT full_name, votes FROM candidate_results WHERE office_code = 11 AND election_year = 2016
                        ORDER BY 1""") == [("DIA", 60), ("EVA", None)]


def test_elected_before_is_unknown_in_the_first_cycle_and_ignores_namesakes_elsewhere(db):
    got = dict((r[0], r[1]) for r in _rows(db, """SELECT municipality_code, elected_before FROM candidate_results
                                                  WHERE office_code = 11 AND election_year = 2020"""))
    assert got == {1: True, 2: False}
    assert _rows(db, """SELECT DISTINCT elected_before FROM candidate_results
                        WHERE office_code = 11 AND election_year = 2016""") == [(None,)]
    assert _rows(db, """SELECT full_name, elected_before FROM candidate_results WHERE office_code = 6
                        AND election_year = 2022 ORDER BY 1""") == [("GIL", True), ("HIL", False)]


def test_unit_winners_shows_flips_margin_and_continuity(db):
    row = _rows(db, """SELECT party, round(margin_pp, 1), round1_leader_party, flipped, has_second_round,
                              same_party_as_previous FROM unit_winners
                       WHERE office_code = 3 AND election_year = 2018""")
    assert row == [("PT", 10.0, "PSDB", True, True, None)]
    row = _rows(db, """SELECT party, flipped, has_second_round, same_party_as_previous FROM unit_winners
                       WHERE office_code = 3 AND election_year = 2022""")
    assert row == [("PT", False, False, True)]


def test_unit_winners_of_the_president_are_the_most_voted_of_each_state(db):
    assert _rows(db, """SELECT state, party, flipped FROM unit_winners WHERE office_code = 1 ORDER BY 1""") == [
        ("MG", "PT", False), ("SP", "PL", False)]


def test_unit_winners_continuity_follows_the_seat_holder_and_cities_get_a_size(db):
    assert _rows(db, """SELECT party, holder_party, size_band FROM unit_winners
                        WHERE office_code = 11 AND election_year = 2016""") == [("PT", "PSDB", "5 a 20 mil")]
    got = _rows(db, """SELECT municipality_code, same_party_as_previous, size_band FROM unit_winners
                       WHERE office_code = 11 AND election_year = 2020 ORDER BY 1""")
    assert got == [(1, True, "Mais de 200 mil"), (2, None, "Até 5 mil")]


def test_runoff_shifts_compares_finalists_of_each_place_at_each_level(db):
    got = _rows(db, """SELECT level, candidate_number, votes_round1, votes_round2, round(share_change * 100, 1)
                       FROM runoff_shifts WHERE office_code = 3 ORDER BY 1, 2""")
    assert got == [("Município", 13, 40, 55, 15.0), ("Município", 45, 50, 45, -5.0),
                   ("UF", 13, 40, 55, 15.0), ("UF", 45, 50, 45, -5.0),
                   ("Zona", 13, 40, 55, 15.0), ("Zona", 45, 50, 45, -5.0)]


def test_runoff_shifts_has_neither_the_president_nor_places_without_a_second_round(db):
    assert _rows(db, "SELECT DISTINCT office_code, election_year FROM runoff_shifts") == [(3, 2018)]


def test_office_seats_counts_ordinary_elections_only(db):
    assert _rows(db, """SELECT seats, candidates, candidates_per_seat, women FROM office_seats
                        WHERE office_code = 6 AND election_year = 2022""") == [(70, 2, pytest.approx(2 / 70), 1)]
    assert _rows(db, """SELECT seats, candidates FROM office_seats WHERE office_code = 11 AND election_year = 2016""") == [(1, 1)]
    assert _rows(db, """SELECT uf, seats, candidates FROM office_seats WHERE office_code = 1""") == [("BR", 1, 2)]


def test_second_build_is_skipped(db, capsys):
    capsys.readouterr()
    office_analysis.build(db)
    assert "up to date" in capsys.readouterr().out
