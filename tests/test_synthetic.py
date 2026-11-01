"""The made-up dataset: small, coherent, kept apart from real data, and enough for build-derived."""

import csv
import re

import duckdb
import pytest

from election_tally import __main__ as cli
from election_tally import countries, municipalities, parties, synthetic
from election_tally.load import load

# The real database divided by 100.
SIZE_LIMITS = {"votes": 3_859_916, "sections": 147_558, "candidates": 16_269, "coalitions": 5_709,
               "electorate_profile": 102_665}
SMALL = 100  # sections, for the tests that need a database of their own


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The full dataset after build-derived, with no network and asked to rebuild everything."""
    db = tmp_path_factory.mktemp("synthetic") / "synthetic.duckdb"
    synthetic.generate(db)
    with pytest.MonkeyPatch.context() as patch:
        def no_network(*args, **kwargs):
            raise AssertionError("build-derived tried to use the network")
        patch.setattr("requests.get", no_network)
        cli.main(["build-derived", "--db", str(db)])
    return db


@pytest.fixture
def con(built):
    connection = duckdb.connect(str(built), read_only=True)
    yield connection
    connection.close()


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(synthetic, "_SECTIONS_TOTAL", SMALL)


def _rows(con, sql):
    return con.execute(sql).fetchall()


@pytest.mark.parametrize("table,limit", SIZE_LIMITS.items())
def test_at_least_100_times_smaller(con, table, limit):
    assert 0 < _rows(con, f"SELECT count(*) FROM {table}")[0][0] <= limit


def test_same_seed_same_data(tmp_path, small):
    digests = []
    for name, seed in (("a", 1), ("b", 1), ("c", 2)):
        db = tmp_path / f"{name}.duckdb"
        synthetic.generate(db, seed)
        c = duckdb.connect(str(db), read_only=True)
        digests.append(_rows(c, """SELECT (SELECT sum(hash(v)) FROM votes v),
                                          (SELECT sum(hash(s)) FROM candidates s)""")[0])
        c.close()
    assert digests[0] == digests[1]
    assert digests[0] != digests[2]


def test_votes_add_up_to_turnout(con):
    assert _rows(con, """
        SELECT count(*) FROM sections s LEFT JOIN (
            SELECT election_year, round_number, election_code, state, municipality_code, zone,
                   section, office_code, sum(vote_count) AS votes
            FROM votes GROUP BY ALL) v
          USING (election_year, round_number, election_code, state, municipality_code, zone,
                 section, office_code)
        WHERE coalesce(v.votes, 0) <> s.turnout OR s.eligible_voters - s.turnout <> s.abstentions""") == [(0,)]


def test_places_are_real_and_every_state_takes_part(con):
    with open(str(municipalities.TABLE_PATH), encoding="utf-8", newline="") as f:
        known = {(r["state"], int(r["municipality_code"]), int(r["ibge_code"])) for r in csv.DictReader(f)}
    with open(str(countries.TABLE_PATH), encoding="utf-8", newline="") as f:
        abroad = {int(r["municipality_code"]) for r in csv.DictReader(f)}
    places = _rows(con, """SELECT DISTINCT state, municipality_code, municipality_ibge_code FROM sections""")
    assert {p for p in places if p[0] != "ZZ"} <= known
    assert {p[1] for p in places if p[0] == "ZZ"} <= abroad
    assert {p[0] for p in places} == {k[0] for k in known} | {"ZZ"}
    assert len({p[1] for p in places}) < 100
    # Every city in Brazil can be drawn on the map.
    shapes = {r[0] for r in _rows(con, "SELECT municipality_ibge_code FROM municipality_shapes")}
    assert {p[2] for p in places if p[0] != "ZZ"} <= shapes


def test_nothing_real_but_the_places(con):
    years = {r[0] for r in _rows(con, "SELECT DISTINCT election_year FROM votes")}
    assert years == set(parties.SYNTHETIC_FEDERAL_YEARS + parties.SYNTHETIC_MUNICIPAL_YEARS)
    names = {r[0] for r in _rows(con, """SELECT party FROM votes WHERE party IS NOT NULL
                                         UNION SELECT party FROM candidates""")}
    assert all(re.fullmatch(r"P-[A-O]|P\d+", n) for n in names)
    assert not names & set(parties.resolve(parties.read_renames()))
    people = {r[0] for r in _rows(con, """SELECT candidate_name FROM votes WHERE vote_type = 'Nominal'
                                          UNION SELECT candidate_name FROM candidates""")}
    assert all(re.fullmatch(r"CANDIDAT[AO] \d+|SIM|NÃO", n) for n in people)


def test_structure_is_the_real_one(con):
    by_year = dict(_rows(con, """SELECT election_year, list(DISTINCT office_code ORDER BY office_code)
                                 FROM sections WHERE round_number = 1 GROUP BY 1"""))
    assert set(by_year[2206]) - {-1} == {1, 3, 5, 6, 7, 8, 25}
    assert _rows(con, "SELECT count(*) > 0 FROM sections WHERE office_code = -1") == [(True,)]
    assert {11, 13, 51} <= set(by_year[2208]) and not {1, 3, 5, 6, 7, 8} & set(by_year[2208])
    assert _rows(con, "SELECT DISTINCT state FROM sections WHERE office_code = 8") == [("DF",)]
    assert _rows(con, "SELECT DISTINCT office_code FROM sections WHERE state = 'ZZ' AND office_code > 0") == [(1,)]
    assert {r[0] for r in _rows(con, "SELECT DISTINCT vote_type FROM votes WHERE office_code = 6")} == {
        "Nominal", "Legenda", "Branco", "Nulo"}
    # Some zones cover more than one municipality.
    assert _rows(con, """SELECT count(*) > 0 FROM (
        SELECT 1 FROM sections GROUP BY state, zone HAVING count(DISTINCT municipality_code) > 1)""") == [(True,)]
    # As in 2014 and 2018, the first federal elections have no receipt time.
    assert _rows(con, """SELECT DISTINCT election_year FROM votes WHERE bu_received_at IS NULL
                         ORDER BY 1""") == [(2206,), (2210,)]


def test_results_follow_the_votes(con):
    # The president always goes to a second round, and its winner has the most votes.
    assert _rows(con, """SELECT DISTINCT election_year FROM candidates
                         WHERE office_code = 1 AND round_number = 2 ORDER BY 1""") == [
        (y,) for y in parties.SYNTHETIC_FEDERAL_YEARS]
    assert _rows(con, """
        SELECT count(*) FROM candidates c JOIN national_results r
          ON r.election_year = c.election_year AND r.round_number = 2
         AND r.candidate_number = c.candidate_number AND r.vote_type = 'Nominal'
        WHERE c.office_code = 1 AND c.round_number = 2 AND c.result = 'ELEITO'
          AND r.votes < (SELECT max(votes) FROM national_results x
                         WHERE x.election_year = r.election_year AND x.round_number = 2
                           AND x.vote_type = 'Nominal')""") == [(0,)]
    # Only some governors and mayors need a second round.
    for office in (3, 11):
        first, runoff = _rows(con, f"""
            SELECT count(*) FILTER (WHERE result = 'ELEITO' AND round_number = 1),
                   count(*) FILTER (WHERE round_number = 2) FROM candidates
            WHERE office_code = {office}""")[0]
        assert first > 0 and runoff > 0


@pytest.mark.parametrize("table", [
    "party_shifts", "unit_volatility", "round_shifts", "state_round_shifts", "zone_round_shifts",
    "elected_party", "mayor_flows", "senate_seats", "leader_flows", "municipality_results",
    "state_results", "national_results", "party_results", "offices", "elections",
    "section_locations", "municipality_shapes", "party_names"])
def test_build_derived_fills_every_table(con, table):
    assert _rows(con, f"SELECT count(*) FROM {table}")[0][0] > 0


def test_the_cycles_are_followed(con):
    federal, municipal = parties.SYNTHETIC_FEDERAL_YEARS, parties.SYNTHETIC_MUNICIPAL_YEARS
    pairs = {r for r in _rows(con, "SELECT DISTINCT from_year, to_year FROM party_shifts")}
    assert pairs == set(zip(federal, federal[1:])) | set(zip(municipal, municipal[1:]))
    assert {r[0] for r in _rows(con, "SELECT DISTINCT from_year FROM mayor_flows")} == set(municipal[:-1])
    assert {r[0] for r in _rows(con, "SELECT DISTINCT election_year FROM senate_seats")} == set(federal[1:])
    assert _rows(con, "SELECT count(*) > 0 FROM mayor_flows WHERE source LIKE '%Outros'") == [(True,)]


@pytest.mark.parametrize("table", ["round_shifts", "state_round_shifts", "zone_round_shifts"])
def test_finalists_also_lose_ground_between_rounds(con, table):
    """The dashboards chart the largest falls, so they need some, in share and in votes."""
    assert _rows(con, f"""SELECT count(*) FILTER (WHERE share_change < 0) > 0,
                                 count(*) FILTER (WHERE votes_change < 0) > 0 FROM {table}""") == [(True, True)]


def test_build_derived_again_is_up_to_date_and_never_downloads(built, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("build-derived tried to use the network")
    monkeypatch.setattr("requests.get", no_network)
    capsys.readouterr()
    cli.main(["build-derived", "--db", str(built)])
    out = capsys.readouterr().out
    assert "results: up to date" in out and "elected_party: up to date" in out
    assert "warning" not in out


def test_refuses_a_file_with_real_data(tmp_path):
    db = tmp_path / "real.duckdb"
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE votes (election_year SMALLINT)")
    c.execute("INSERT INTO votes VALUES (2022)")
    c.close()
    with pytest.raises(SystemExit, match="not synthetic"):
        synthetic.generate(db)
    c = duckdb.connect(str(db), read_only=True)
    assert _rows(c, "SELECT * FROM votes") == [(2022,)]
    c.close()


def test_real_load_refuses_a_synthetic_file(tmp_path, small):
    db = tmp_path / "s.duckdb"
    synthetic.generate(db)
    with pytest.raises(SystemExit, match="synthetic dataset"):
        load([tmp_path / "x.zip"], db, 2022)


def test_generating_again_replaces_the_data(tmp_path, small):
    db = tmp_path / "s.duckdb"
    synthetic.generate(db)
    cli.main(["build-derived", "--db", str(db)])
    synthetic.generate(db, 2)
    c = duckdb.connect(str(db), read_only=True)
    tables = {r[0] for r in _rows(c, "SELECT table_name FROM duckdb_tables()")}
    assert _rows(c, f"SELECT seed FROM {parties.SYNTHETIC_TABLE}") == [(2,)]
    c.close()
    assert "national_results" not in tables


def test_a_real_database_is_not_synthetic(tmp_path):
    c = duckdb.connect(str(tmp_path / "real.duckdb"))
    assert not parties.is_synthetic(c)
    c.close()


@pytest.mark.parametrize("command", ["load", "pipeline"])
def test_cli_generates_into_its_own_file(tmp_path, monkeypatch, small, command):
    monkeypatch.chdir(tmp_path)
    assert cli.main([command, "--synthetic", "--seed", "3"]) == 0
    assert (tmp_path / "synthetic.duckdb").exists()
    assert not (tmp_path / "elections.duckdb").exists()


@pytest.mark.parametrize("args,message", [
    (["load", "--synthetic", "--year", "2022"], "does not combine"),
    (["load", "--synthetic", "x.zip"], "does not combine"),
    (["pipeline", "--synthetic", "--state", "SP"], "does not combine"),
    (["pipeline", "--synthetic", "--jobs", "2"], "does not combine"),
    (["pipeline", "--synthetic", "--skip-line-check"], "does not combine"),
    (["load", "--seed", "1", "--year", "2022", "x.zip"], "--seed needs"),
    (["load", "x.zip"], "--year is required"),
    (["pipeline"], "--year is required"),
    (["load", "--year", "2022"], "needs files"),
])
def test_cli_rejects(args, message, capsys):
    with pytest.raises(SystemExit):
        cli.main(args)
    assert message in capsys.readouterr().err


def test_download_still_requires_a_year(capsys):
    with pytest.raises(SystemExit):
        cli.main(["download"])
    assert "--year" in capsys.readouterr().err
