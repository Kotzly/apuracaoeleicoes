"""Tables from other TSE files, the lookups and the result tables, on hand-made data."""

import zipfile

import duckdb
import pytest

from election_tally import lookups, results, tse_tables
from election_tally.load import create_schema

RACE = ["ANO_ELEICAO", "NM_TIPO_ELEICAO", "CD_ELEICAO", "DS_ELEICAO", "SG_UF", "SG_UE", "NM_UE",
        "CD_CARGO", "DS_CARGO"]
FILES = {
    "consulta_cand": (RACE + ["NR_TURNO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_URNA_CANDIDATO",
                              "NM_CANDIDATO", "SG_PARTIDO", "DS_SITUACAO_CANDIDATURA",
                              "DS_SIT_TOT_TURNO", "DS_GENERO"], [
        ["2022", "ORD", "545", "GERAL", "BR", "BR", "BRASIL", "1", "PRESIDENTE", "2", "1", "13", "LULA",
         "LUIZ", "PT", "APTO", "ELEITO", "MASCULINO"],
        ["2022", "ORD", "546", "GERAL", "SP", "SP", "SÃO PAULO", "6", "DEPUTADO FEDERAL", "1", "2",
         "1234", "ANA", "ANA S", "X", "#NE", "SUPLENTE", "FEMININO"],
        ["2022", "ORD", "546", "GERAL", "SP", "71072", "SÃO PAULO", "13", "VEREADOR", "1", "3", "1235",
         "BIA – B", "BIA S", "X", "APTO", "#NULO#", "FEMININO"],
    ]),
    "consulta_vagas": (RACE + ["QT_VAGA"], [
        ["2022", "ORD", "546", "GERAL", "SP", "SP", "SÃO PAULO", "6", "Deputado Federal", "70"],
    ]),
    "consulta_coligacao": (RACE + ["NR_TURNO", "NR_PARTIDO", "SG_PARTIDO", "SQ_COLIGACAO",
                                   "NM_COLIGACAO"], [
        ["2022", "ORD", "545", "GERAL", "BR", "BR", "BRASIL", "1", "PRESIDENTE", "1", "13", "PT", "9",
         "JUNTOS"],
    ]),
    "perfil_eleitorado": (["AA_ELEICAO", "SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA", "DS_GENERO",
                           "DS_FAIXA_ETARIA", "DS_GRAU_ESCOLARIDADE", "QT_ELEITORES"], [
        ["2022", "SP", "71072", "SÃO PAULO", "1", "F", "30 a 34", "MÉDIO", "5"],
        ["2022", "SP", "71072", "SÃO PAULO", "2", "F", "30 a 34", "MÉDIO", "7"],
    ]),
}


def _fake_fetch(url, target):
    folder = url.split("/")[-2]
    header, rows = FILES[folder]
    text = "\n".join(";".join(f'"{v}"' for v in row) for row in [header, *rows]) + "\n"
    with zipfile.ZipFile(target, "w") as z:
        if folder != "perfil_eleitorado":
            z.writestr("x_SP.csv", "ignored")
        z.writestr("x_BRASIL.csv" if folder != "perfil_eleitorado" else "x_2022.csv",
                   text.encode("cp1252"))


def _db(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    con.execute("""INSERT INTO sections (election_year, round_number, election_code, state,
                   municipality_code, municipality_name, zone, section, office_code, office_name,
                   country, country_code, eligible_voters)
                   VALUES (2022, 2, 545, 'ZZ', 1, 'LISBOA', 1, 1, 1, 'Presidente', 'Portugal',
                           'PRT', 10)""")
    for state, m, z, num, name, kind, party, v in [
            ("SP", 10, 1, 13, "LULA", "Nominal", "PT", 60), ("SP", 10, 2, 13, "LULA", "Nominal", "PT", 10),
            ("SP", 10, 1, 22, "JAIR", "Nominal", "PL", 30), ("SP", 10, 1, 95, "BRANCO", "Branco", None, 50),
            ("RJ", 20, 1, 22, "JAIR", "Nominal", "PL", 100)]:
        con.execute("""INSERT INTO votes (election_year, round_number, office_code, office_name, state,
                       municipality_code, municipality_name, zone, section, vote_type, candidate_number,
                       candidate_name, party, vote_count)
                       VALUES (2022, 2, 1, 'Presidente', ?, ?, 'C', ?, 1, ?, ?, ?, ?, ?)""",
                    [state, m, z, kind, num, name, party, v])
    con.close()
    return path


def _all(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute(sql).fetchall()
    con.close()
    return rows


def test_tables_keep_scope_and_drop_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    path = _db(tmp_path)
    tse_tables.build(path)
    assert _all(path, """SELECT candidate_name, country, state, municipality_code, candidacy_status, result
                         FROM candidates ORDER BY candidate_id""") == [
        ("LULA", "Brasil", None, None, "APTO", "ELEITO"),
        ("ANA", None, "SP", None, None, "SUPLENTE"),
        ("BIA – B", None, "SP", 71072, "APTO", None)]
    assert _all(path, "SELECT state, office_name, seats FROM seats") == [
        ("SP", "DEPUTADO FEDERAL", 70)]
    assert _all(path, "SELECT country, party, coalition_name FROM coalitions") == [
        ("Brasil", "PT", "JUNTOS")]
    assert _all(path, "SELECT election_year, municipality_code, voters FROM electorate_profile") == [
        (2022, 71072, 12)]


def test_loaded_years_are_skipped_until_the_definition_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    path = _db(tmp_path)
    table = tse_tables.TABLES[1]
    assert tse_tables.build_table(path, table) == [2022]
    assert tse_tables.build_table(path, table) == []
    changed = tse_tables.Table(table.name, table.folder, table.columns, table.select, version=99)
    assert tse_tables.build_table(path, changed) == [2022]


def test_failed_download_warns_and_keeps_the_old_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    path = _db(tmp_path)
    table = tse_tables.TABLES[1]
    tse_tables.build_table(path, table)

    def fail(url, target):
        raise tse_tables.requests.ConnectionError("offline")
    monkeypatch.setattr(tse_tables, "_fetch", fail)
    assert tse_tables.build_table(path, table, force=True) == []
    assert "not loaded" in capsys.readouterr().out
    assert _all(path, "SELECT seats FROM seats") == [(70,)]


def test_lookups(tmp_path, monkeypatch, capsys):
    path = _db(tmp_path)
    lookups.build(path)
    assert "not built" in capsys.readouterr().out  # no candidates yet: must not stop the start
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    tse_tables.build_table(path, tse_tables.TABLES[0])
    lookups.build(path)
    assert _all(path, "SELECT * FROM elections") == [(545, 2022, 2, "GERAL", "ORD")]
    assert _all(path, """SELECT state, municipality_name, zone, section, country, country_code
                         FROM section_locations""") == [("ZZ", "LISBOA", 1, 1, "Portugal", "PRT")]
    assert _all(path, "SELECT office_code, office_name FROM offices ORDER BY 1") == [
        (1, "Presidente"), (6, "DEPUTADO FEDERAL"), (13, "VEREADOR")]
    capsys.readouterr()
    lookups.build(path)
    assert "up to date" in capsys.readouterr().out


def test_results_add_up_at_each_level(tmp_path, capsys):
    path = _db(tmp_path)
    results.build(path)
    assert _all(path, """SELECT state, candidate_name, votes, share_of_valid FROM municipality_results
                         WHERE state = 'SP' ORDER BY votes DESC""") == [
        ("SP", "LULA", 70, 0.7), ("SP", "BRANCO", 50, None), ("SP", "JAIR", 30, 0.3)]
    assert _all(path, """SELECT candidate_name, votes, round(share_of_valid, 3) FROM national_results
                         WHERE vote_type = 'Nominal' ORDER BY votes DESC""") == [
        ("JAIR", 130, 0.65), ("LULA", 70, 0.35)]
    assert _all(path, "SELECT state, party, votes FROM party_results ORDER BY 1, 2") == [
        ("RJ", "PL", 100), ("SP", "PL", 30), ("SP", "PT", 70)]
    capsys.readouterr()
    results.build(path)
    assert "up to date" in capsys.readouterr().out


def test_new_year_in_votes_is_fetched_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    path = _db(tmp_path)
    table = tse_tables.TABLES[1]
    tse_tables.build_table(path, table)
    con = duckdb.connect(str(path))
    con.execute("INSERT INTO votes (election_year, vote_count) VALUES (2028, 1)")
    con.close()
    fetched = []

    def spy(url, target):
        fetched.append(url)
        _fake_fetch(url, target)
    monkeypatch.setattr(tse_tables, "_fetch", spy)
    tse_tables.build_table(path, table)
    assert [u.rsplit("_", 1)[1] for u in fetched] == ["2028.zip"]


def test_a_year_that_fails_to_replace_keeps_its_old_rows(tmp_path):
    source = tmp_path / "n.csv"
    source.write_text("ANO_ELEICAO\n2022\n", encoding="utf-8")
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 2022 AS election_year, 'old' AS note")
    # The rows of the year are deleted first; this column makes the insert after it fail.
    table = tse_tables.Table("t", "f", {"election_year": (("ANO_ELEICAO",), "INTEGER")},
                             "SELECT election_year, 1 AS column_the_table_lacks")
    with pytest.raises(duckdb.Error):
        tse_tables._load_year(con, table, source, 2022, False)
    assert con.execute("SELECT * FROM t").fetchall() == [(2022, "old")]


def test_a_zip_without_the_national_file_warns_and_keeps_the_old_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tse_tables, "_fetch", _fake_fetch)
    path = _db(tmp_path)
    table = tse_tables.TABLES[1]
    tse_tables.build_table(path, table)

    def only_states(url, target):
        with zipfile.ZipFile(target, "w") as z:
            z.writestr("x_SP.csv", "a")
            z.writestr("x_RJ.csv", "b")
    monkeypatch.setattr(tse_tables, "_fetch", only_states)
    assert tse_tables.build_table(path, table, force=True) == []
    assert "no national file among 2 files" in capsys.readouterr().out
    assert _all(path, "SELECT seats FROM seats") == [(70,)]


class _Stream:
    def __init__(self, error=None):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def iter_content(self, size):
        yield from (b"first ", b"second")


def test_fetch_writes_the_whole_body(tmp_path, monkeypatch):
    monkeypatch.setattr(tse_tables.requests, "get", lambda url, stream, timeout: _Stream())
    target = tmp_path / "f.zip"
    tse_tables._fetch("http://x/f.zip", target)
    assert target.read_bytes() == b"first second"


def test_a_year_the_tse_has_not_published_warns_and_loads_nothing(tmp_path, monkeypatch, capsys):
    error = tse_tables.requests.HTTPError("404")
    monkeypatch.setattr(tse_tables.requests, "get", lambda url, stream, timeout: _Stream(error))
    assert tse_tables.build_table(_db(tmp_path), tse_tables.TABLES[1]) == []
    assert "seats of 2022 not loaded: 404" in capsys.readouterr().out
