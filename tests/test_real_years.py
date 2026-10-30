"""Tests with real samples (AC, 1st round) from each layout published by the TSE."""

from pathlib import Path

import duckdb
import pytest

from election_tally.load import load

DATA_DIR = Path(__file__).parent / "data"
SAMPLES = {
    2016: "bweb_1t_AC_04102016204813.zip",
    2018: "bweb_1t_AC_101020181938.zip",
    2020: "bweb_1t_AC_181120201549.zip",
    2022: "bweb_1t_AC_051020221321.zip",
    2024: "bweb_1t_AC_091020241636.zip",
    2026: "bweb_1t_AC_051020261403.zip",
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("real") / "e.duckdb"
    for year, file_name in SAMPLES.items():
        load([DATA_DIR / file_name], path, year)
    return path


def _q(db, sql):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_all_years_share_schema(db):
    assert _q(db, "SELECT election_year, count(*) FROM votes GROUP BY 1 ORDER BY 1") == \
        [(2016, 42), (2018, 40), (2020, 41), (2022, 40), (2024, 40), (2026, 40)]
    nulls = _q(db, """SELECT election_year FROM votes WHERE round_number IS NULL OR state IS NULL
                      OR municipality_code IS NULL OR zone IS NULL OR section IS NULL
                      OR office_code IS NULL OR vote_count IS NULL""")
    assert nulls == []


def test_2016_round_from_name_and_derived_vote_type(db):
    assert _q(db, "SELECT DISTINCT round_number FROM votes WHERE election_year = 2016") == [(1,)]
    types = {r[0] for r in _q(db, "SELECT DISTINCT vote_type FROM votes WHERE election_year = 2016")}
    assert {"Nominal", "Nulo"} <= types
    # section not installed: enters with 0 votes and no candidate
    assert _q(db, """SELECT vote_count, candidate_number FROM votes
                     WHERE election_year = 2016 AND zone = 9 AND section = 170""") == [(0, None)] * 2


def test_2018_has_no_received_time(db):
    assert _q(db, """SELECT count(*) FROM sections
                     WHERE election_year = 2018 AND bu_received_at IS NOT NULL""") == [(0,)]
    assert _q(db, "SELECT DISTINCT state FROM votes WHERE election_year = 2018") == [("AC",)]  # "SG_ UF"
    assert _q(db, "SELECT count(*) FROM votes WHERE election_year = 2018 AND party = '#NULO#'") == [(0,)]


def test_2020_badly_escaped_quotes(db):
    assert _q(db, "SELECT candidate_name, vote_count FROM votes WHERE election_year = 2020 AND candidate_number = 65133") \
        == [("DÉIA FILHA DO MARTILHO", 2)]


def test_times_in_brasilia(db):
    # Acre is UTC-5: local times become +2h (in 2016 that includes the receipt time:
    # 18:19:29 local in the sample).
    assert _q(db, "SELECT min(bu_received_at)::VARCHAR FROM sections WHERE election_year = 2016") == \
        [("2016-10-02 20:19:29",)]
    assert _q(db, """SELECT min(opened_at)::VARCHAR, min(closed_at) >= '2024-10-06 17:00'
                     FROM sections WHERE election_year = 2024""") == [("2024-10-06 08:00:01", True)]
    # receipt after closing in every year that has both
    assert _q(db, """SELECT count(*) FROM sections
                     WHERE bu_received_at < closed_at""") == [(0,)]


def test_eligible_voters_consistent(db):
    assert _q(db, """SELECT count(*) FROM sections
                     WHERE eligible_voters <> turnout + abstentions""") == [(0,)]



def test_unified_voting_hours_in_brasilia_time(db):
    # Since 2022 every state votes 08:00-17:00 Brasília time (06:00-15:00 local in AC).
    assert _q(db, """SELECT count(*) FROM sections WHERE election_year IN (2022, 2024, 2026)
                     AND (opened_at::TIME < '07:59' OR closed_at::TIME < '16:59')""") == [(0,)]
