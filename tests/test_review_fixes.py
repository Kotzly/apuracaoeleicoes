"""Defects found by reviewing the pipeline, and the guards that keep them from coming back."""

import glob
import zipfile
from pathlib import Path

import duckdb
import pytest
import requests
import yaml

from election_tally import blank_votes, derived, download, elected, load as load_module, repair, results, shifts, tse_tables
from election_tally.__main__ import main
from election_tally.domain import consecutive_elections
from election_tally.load import create_schema

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"


def _yaml(pattern: str) -> list[dict]:
    return [yaml.safe_load(Path(f).read_text(encoding="utf-8"))
            for f in sorted(glob.glob(str(DASHBOARDS / pattern)))]


# --- Superset: one row per percentage base means any chart without a base counts twice


def test_every_chart_with_a_percentage_base_has_exactly_one_selected():
    with_base = {d["uuid"] for d in _yaml("datasets/*/*.yaml")
                 if any(c["column_name"] == "vote_base" for c in d["columns"])}
    charts = {c["uuid"]: c for c in _yaml("charts/*.yaml")}
    for dashboard in _yaml("dashboards/*.yaml"):
        filters = dashboard["metadata"].get("native_filter_configuration", [])
        base_filters = [f for f in filters
                        if any(t.get("column", {}).get("name") == "vote_base" for t in f.get("targets", []))]
        for item in dashboard["position"].values():
            if not (isinstance(item, dict) and item.get("type") == "CHART"):
                continue
            chart = charts[item["meta"]["uuid"]]
            if chart["dataset_uuid"] not in with_base:
                continue
            own = [f for f in chart["params"].get("adhoc_filters") or [] if f.get("subject") == "vote_base"]
            scoped = [f for f in base_filters
                      if item["meta"]["chartId"] not in f["scope"].get("excluded", [])]
            assert own or scoped, f"{dashboard['dashboard_title']}: {chart['slice_name']} counts twice"


def test_blank_and_null_share_change_is_not_doubled_by_the_base_rows():
    dataset = next(d for d in _yaml("datasets/*/*.yaml") if d["table_name"] == "party_shifts")
    con = duckdb.connect()
    con.execute("""CREATE TABLE party_shifts AS
                   SELECT 'UF' AS level, 'Nulo' AS party, 0.1::DOUBLE AS share_from, 0.2::DOUBLE AS share_to,
                          0.1::DOUBLE AS share_change, 0.1::DOUBLE AS share_from_all, 0.2::DOUBLE AS share_to_all""")
    chart = next(c for c in _yaml("charts/*.yaml") if c["slice_name"] == "UF: maiores altas entre eleições")
    base = next(f for f in chart["params"]["adhoc_filters"] if f.get("subject") == "vote_base")
    got = con.execute(f"SELECT round(sum(share_change), 9) FROM ({dataset['sql']}) WHERE vote_base = ?",
                      [base["comparator"]]).fetchone()[0]
    assert got == 0.1


# --- Results: a presidential vote always has its party


def _insert_president(con, year, state, municipality, number, party, votes, section=1, rnd=1):
    con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, municipality_name,
                   zone, section, office_code, vote_type, candidate_number, candidate_name, party, vote_count)
                   VALUES (?, ?, ?, ?, ?, 1, ?, 1, 'Nominal', ?, ?, ?, ?)""",
                [year, rnd, state, municipality, f"C{municipality}", section, number, f"C{number}", party, votes])


def _db_abroad_without_party_in_2014(tmp_path):
    path = tmp_path / "abroad.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    for year, party_13 in ((2014, None), (2018, "PT")):
        _insert_president(con, year, "ZZ", 900, 13, party_13, 30)
        _insert_president(con, year, "ZZ", 900, 45, None if year == 2014 else "PSDB", 70)
    _insert_president(con, 2014, "SP", 100, 13, "PT", 60)
    _insert_president(con, 2014, "SP", 100, 45, "PSDB", 40)
    _insert_president(con, 2018, "SP", 100, 13, "PT", 50)
    _insert_president(con, 2018, "SP", 100, 45, "PSDB", 50)
    con.close()
    results.build(path)
    shifts.build(path)
    return path


def test_votes_abroad_without_party_take_it_from_the_other_states(tmp_path):
    path = _db_abroad_without_party_in_2014(tmp_path)
    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("""SELECT count(*) FROM municipality_results WHERE party IS NULL""").fetchone()[0] == 0
    assert con.execute("""SELECT sum(votes) FROM party_results WHERE election_year = 2014 AND state = 'ZZ'""").fetchone()[0] == 100
    for level in ("UF", "Município", "Zona", "Seção"):
        share = con.execute("""SELECT round(share_from, 4), round(share_to, 4) FROM party_shifts
                               WHERE level = ? AND party = 'PT' AND state = 'ZZ'""", [level]).fetchall()
        assert share == [(0.3, 0.3)], level  # was (0, 0.3), a jump that never happened


# --- New years need no change in the code


def test_only_loaded_consecutive_elections_are_compared():
    assert consecutive_elections([2022, 2018, 2026, 2014]) == [(2014, 2018), (2018, 2022), (2022, 2026)]
    assert consecutive_elections([2014, 2022]) == []  # a gap is not a comparison
    assert consecutive_elections([2030, 2026]) == [(2026, 2030)]


def _candidates(con, rows):
    con.execute("""CREATE TABLE candidates (election_year INTEGER, round_number INTEGER, election_code INTEGER,
                   election_type VARCHAR, state VARCHAR, municipality_code INTEGER, municipality_name VARCHAR,
                   office_code INTEGER, candidate_number INTEGER, party VARCHAR, result VARCHAR)""")
    con.executemany("INSERT INTO candidates VALUES (?, 1, 1, 'ELEIÇÃO ORDINÁRIA', 'SP', ?, ?, ?, 13, ?, 'ELEITO')", rows)


def test_a_new_election_year_appears_in_every_comparison(tmp_path):
    path = tmp_path / "future.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    for year in (2026, 2030):  # a year the code has never heard of
        _insert_president(con, year, "SP", 100, 13, "PT", 10)
    con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, municipality_name, zone, section,
                   office_code, vote_type, candidate_number, party, vote_count)
                   VALUES (2024, 1, 'SP', 100, 'C100', 1, 1, 11, 'Nominal', 13, 'PT', 5),
                          (2028, 1, 'SP', 100, 'C100', 1, 1, 11, 'Nominal', 13, 'PT', 5)""")
    _candidates(con, [(2024, 100, "C100", 11, "PT"), (2028, 100, "C100", 11, "PL"),
                      (2026, None, None, 5, "PT"), (2030, None, None, 5, "PL")])
    con.close()
    results.build(path)
    shifts.build(path)
    elected.build(path)
    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("SELECT DISTINCT transition FROM party_shifts WHERE office_code = 1").fetchall() == [("2026 → 2030 T1",)]
    assert con.execute("SELECT DISTINCT transition FROM party_shifts WHERE office_code = 11").fetchall() == [("2024 → 2028 T1",)]
    assert con.execute("SELECT transition, source_party, target_party FROM mayor_flows").fetchall() == [("2024 → 2028", "PT", "PL")]
    assert con.execute("SELECT election_year, party, seats FROM senate_seats ORDER BY party").fetchall() == [
        (2030, "PL", 1), (2030, "PT", 1)]


# --- Loading


def test_repair_leaves_text_in_another_encoding_as_it_came():
    jose = "JOSÉ“X”\n".encode("cp1252")  # a Windows-1252 quote right after a capital with an accent
    assert repair.repair_chunk(jose) == jose


def test_tse_file_with_windows_quotes_after_an_accent_loads(tmp_path):
    archive = tmp_path / "c.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("c_BRASIL.csv", '"NOME"\n"JOSÉ“X”"\n'.encode("cp1252"))
    file = tse_tables._national_file(archive, tmp_path)
    assert "JOSÉ“X”" in file.read_text(encoding="utf-8")


def _load_row(tmp_path, office, votes, name="a"):
    line = ('ANO_ELEICAO;NR_TURNO;CD_ELEICAO;SG_UF;CD_MUNICIPIO;NM_MUNICIPIO;NR_ZONA;NR_SECAO;CD_CARGO_PERGUNTA;'
            'DS_CARGO_PERGUNTA;DS_TIPO_VOTAVEL;NR_VOTAVEL;NM_VOTAVEL;SG_PARTIDO;QT_VOTOS;DT_BU_RECEBIDO\n'
            f'2022;1;544;SP;71072;SAO PAULO;1;1;{office};X;Nominal;13;A;PT;{votes};02/10/2022 18:00:00\n')
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"{name}.csv", line)
    return archive


def test_reloading_some_offices_keeps_the_other_ones(tmp_path):
    db = tmp_path / "e.duckdb"
    load_module.load([_load_row(tmp_path, 1, 10, "first"), _load_row(tmp_path, 3, 20, "second")], db, 2022)
    load_module.load([_load_row(tmp_path, 3, 20, "second")], db, 2022, offices=[3])
    load_module.load([_load_row(tmp_path, 1, 11, "again")], db, 2022, offices=[1])
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT office_code, vote_count FROM votes ORDER BY 1").fetchall() == [(1, 11), (3, 20)]
    assert con.execute("SELECT office_code FROM sections ORDER BY 1").fetchall() == [(1,), (3,)]
    con.close()
    # Without `offices`, a file still replaces its whole year, round, state and election.
    load_module.load([_load_row(tmp_path, 1, 12, "whole")], db, 2022)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT office_code, vote_count FROM votes").fetchall() == [(1, 12)]


def test_a_number_that_cannot_be_read_is_reported(tmp_path, capsys):
    load_module.load([_load_row(tmp_path, 1, "mil")], tmp_path / "e.duckdb", 2022)
    assert "'vote_count': 1" in capsys.readouterr().out


def test_a_last_line_without_line_break_is_not_reported_as_a_parser_problem(tmp_path, capsys):
    archive = _load_row(tmp_path, 1, 10)
    with zipfile.ZipFile(archive) as z:
        text = z.read("a.csv")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.csv", text.rstrip(b"\n"))
    load_module.load([archive], tmp_path / "e.duckdb", 2022)
    assert "parsed" not in capsys.readouterr().out


def test_a_path_with_a_quote_loads(tmp_path):
    folder = tmp_path / "d'agua"
    folder.mkdir()
    load_module.load([_load_row(folder, 1, 10)], folder / "e.duckdb", 2022)
    tse_source = folder / "n.csv"
    tse_source.write_text("ANO_ELEICAO;SG_UF\n2022;SP\n", encoding="utf-8")
    table = tse_tables.Table("t", "f", {"election_year": (("ANO_ELEICAO",), "INTEGER")}, "SELECT election_year")
    con = duckdb.connect(str(folder / "e.duckdb"))
    assert tse_tables._load_year(con, table, tse_source, 2022, True) == 1


# --- Downloads: the final name of a file means it was verified


class _Hash:
    def __init__(self, text="", error=None):
        self.text, self.error = text, error

    def raise_for_status(self):
        if self.error:
            raise self.error


@pytest.mark.parametrize("answer", [_Hash(error=requests.HTTPError("503")), _Hash(text="")])
def test_a_file_that_could_not_be_verified_does_not_look_downloaded(tmp_path, monkeypatch, answer):
    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield b"content"

    def fake_get(url, **kw):
        return answer if url.endswith(".sha512") else Body()

    monkeypatch.setattr(download.requests, "get", fake_get)
    target = tmp_path / "f.zip"
    with pytest.raises((requests.HTTPError, download.DownloadError)):
        download._fetch("http://x/f.zip", target, "http://x/f.zip.sha512")
    assert list(tmp_path.iterdir()) == []


# --- Derived tables


def test_a_failed_rebuild_keeps_the_previous_tables(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t AS SELECT 1 AS old_version")
    con.close()
    with pytest.raises(ValueError):
        with derived.rebuilding(path) as con:
            con.execute("CREATE OR REPLACE TABLE t AS SELECT 2 AS new_version")
            raise ValueError("halfway")
    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("SELECT * FROM t").fetchall() == [(1,)]


def test_build_derived_on_an_empty_database_explains_what_to_do(tmp_path, capsys):
    path = tmp_path / "e.duckdb"
    duckdb.connect(str(path)).close()
    assert main(["build-derived", "--db", str(path)]) == 1
    assert "pipeline" in capsys.readouterr().err
    assert main(["build-derived", "--db", str(tmp_path / "missing.duckdb")]) == 1


def test_one_bad_tse_file_does_not_stop_the_build(tmp_path, monkeypatch, capsys):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    _insert_president(con, 2022, "SP", 100, 13, "PT", 1)
    con.close()

    def broken_file(*_):
        raise UnicodeDecodeError("latin-1", b"x", 0, 1, "bad")

    monkeypatch.setattr(tse_tables, "_fetch", lambda *_: None)
    monkeypatch.setattr(tse_tables, "_national_file", broken_file)
    table = tse_tables.Table("t", "f", {"election_year": (("A",), "INTEGER")}, "SELECT election_year")
    assert tse_tables.build_table(path, table) == []
    assert "not loaded" in capsys.readouterr().out


def test_a_renamed_column_of_a_tse_file_is_reported(tmp_path, capsys):
    source = tmp_path / "n.csv"
    source.write_text("ANO_ELEICAO\n2022\n", encoding="utf-8")
    table = tse_tables.Table("t", "f", {"election_year": (("ANO_ELEICAO",), "INTEGER"),
                                        "result": (("DS_SIT_TOT_TURNO",), "VARCHAR")},
                             "SELECT election_year, result")
    tse_tables._load_year(duckdb.connect(), table, source, 2022, True)
    assert "result" in capsys.readouterr().out


def test_blank_votes_still_builds_after_the_results(tmp_path):
    path = _db_abroad_without_party_in_2014(tmp_path)
    blank_votes.build(path)
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM blank_null_votes").fetchone()[0] == 4
