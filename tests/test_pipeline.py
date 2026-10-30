"""Tests with a synthetic CSV in the TSE bweb layout (latin-1, ';', quoted fields)."""

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from election_tally.load import load
from election_tally.validate import validate

HEADER = [
    "DT_GERACAO", "HH_GERACAO", "ANO_ELEICAO", "CD_TIPO_ELEICAO", "NM_TIPO_ELEICAO",
    "CD_PLEITO", "DT_PLEITO", "NR_TURNO", "CD_ELEICAO", "DS_ELEICAO", "SG_UF",
    "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA", "NR_SECAO", "NR_LOCAL_VOTACAO",
    "CD_CARGO_PERGUNTA", "DS_CARGO_PERGUNTA", "NR_PARTIDO", "SG_PARTIDO", "NM_PARTIDO",
    "DT_BU_RECEBIDO", "QT_APTOS", "QT_COMPARECIMENTO", "QT_ABSTENCOES", "CD_TIPO_URNA",
    "DS_TIPO_URNA", "CD_TIPO_VOTAVEL", "DS_TIPO_VOTAVEL", "NR_VOTAVEL", "NM_VOTAVEL",
    "QT_VOTOS", "NR_URNA_EFETIVADA", "DT_ABERTURA", "DT_ENCERRAMENTO", "DT_EMISSAO_BU",
]

# (state, municipality_code, municipality_name, zone, section, received_time, eligible,
#  turnout, votes{candidate: count})
SECTIONS = [
    ("SP", 71072, "SÃO PAULO", 1, 10, "02/10/2022 17:20:00", 300, 250, {13: 120, 22: 100}),
    ("SP", 71072, "SÃO PAULO", 1, 11, "02/10/2022 18:05:00", 300, 260, {13: 110, 22: 130}),
    ("SP", 71072, "SÃO PAULO", 2, 20, "02/10/2022 17:45:00", 280, 200, {13: 90, 22: 95}),
    # zone 3 covers two municipalities
    ("SP", 61115, "GUARULHOS", 3, 30, "02/10/2022 19:10:00", 250, 210, {13: 100, 22: 90}),
    ("SP", 64777, "ARUJÁ", 3, 31, "02/10/2022 17:30:00", 200, 170, {13: 70, 22: 85}),
]


def _rows(sections, offices=((1, "Presidente", 544), (3, "Governador", 546))):
    for state, mun, name, zone, sec, received, eligible, turnout, votes in sections:
        for office_code, office_name, election in offices:
            for num, count in votes.items():
                yield {
                    "DT_GERACAO": "05/10/2022", "HH_GERACAO": "13:21:00",
                    "ANO_ELEICAO": "2022", "CD_TIPO_ELEICAO": "2",
                    "NM_TIPO_ELEICAO": "Eleição Ordinária", "CD_PLEITO": "406",
                    "DT_PLEITO": "02/10/2022", "NR_TURNO": "1", "CD_ELEICAO": str(election),
                    "DS_ELEICAO": "Eleição Geral", "SG_UF": state, "CD_MUNICIPIO": str(mun),
                    "NM_MUNICIPIO": name, "NR_ZONA": str(zone), "NR_SECAO": str(sec),
                    "NR_LOCAL_VOTACAO": "1000", "CD_CARGO_PERGUNTA": str(office_code),
                    "DS_CARGO_PERGUNTA": office_name, "NR_PARTIDO": str(num),
                    "SG_PARTIDO": f"P{num}", "NM_PARTIDO": f"Partido {num}",
                    "DT_BU_RECEBIDO": received, "QT_APTOS": str(eligible),
                    "QT_COMPARECIMENTO": str(turnout), "QT_ABSTENCOES": str(eligible - turnout),
                    "CD_TIPO_URNA": "1", "DS_TIPO_URNA": "Apurada", "CD_TIPO_VOTAVEL": "1",
                    "DS_TIPO_VOTAVEL": "Nominal", "NR_VOTAVEL": str(num),
                    "NM_VOTAVEL": f"CANDIDATO {num} ÇÃÉ", "QT_VOTOS": str(count),
                    "NR_URNA_EFETIVADA": "2000000", "DT_ABERTURA": "02/10/2022 08:00:00",
                    "DT_ENCERRAMENTO": "02/10/2022 17:00:00",
                    "DT_EMISSAO_BU": "02/10/2022 17:01:00",
                }


def _zip(dest: Path, name: str, sections, drop=(), rename=None,
         delim=";", ext=".csv") -> Path:
    """Builds a zip in the bweb layout, optionally dropping/renaming columns."""
    rename = rename or {}
    header = [c for c in HEADER if c not in drop]
    text = delim.join(f'"{rename.get(c, c)}"' for c in header) + "\n"
    for row in _rows(sections):
        text += delim.join(f'"{row[c]}"' for c in header) + "\n"
    archive = dest / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"{name}{ext}", text.encode("latin-1"))
        z.writestr("leiame.pdf", b"%PDF-1.4")
    return archive


@pytest.fixture
def db(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_051020221321", SECTIONS)
    db_path = tmp_path / "e.duckdb"
    load([zp], db_path, 2022)
    return db_path, zp


def _q(db, sql):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_basic_load(db):
    db_path, _ = db
    assert _q(db_path, "SELECT count(*) FROM votes")[0][0] == 5 * 2 * 2
    assert _q(db_path, "SELECT count(*) FROM sections")[0][0] == 5 * 2
    # accents read correctly from latin-1
    assert ("SÃO PAULO",) in _q(db_path, "SELECT DISTINCT municipality_name FROM votes")
    # time converted
    assert _q(db_path, "SELECT min(bu_received_at)::VARCHAR FROM votes")[0][0] == "2022-10-02 17:20:00"
    assert _q(db_path, "SELECT count(*) FROM sections WHERE bu_issued_at IS NULL")[0][0] == 0


def test_reload_does_not_duplicate(db):
    db_path, zp = db
    load([zp], db_path, 2022)
    assert _q(db_path, "SELECT count(*) FROM votes")[0][0] == 20
    assert _q(db_path, "SELECT sum(eligible_voters) FROM sections WHERE office_code = 1")[0][0] == 1330


def test_office_filter(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_x", SECTIONS)
    db_path = tmp_path / "f.duckdb"
    load([zp], db_path, 2022, offices=[1])
    assert _q(db_path, "SELECT DISTINCT office_code FROM votes") == [(1,)]


def test_evolution_ends_at_consolidated_total(db):
    db_path, _ = db
    sql = """
        WITH evolution AS (
            SELECT state, zone, candidate_number, bu_received_at,
                   sum(sum(vote_count)) OVER (PARTITION BY state, zone, candidate_number
                                              ORDER BY bu_received_at) AS cumulative
            FROM votes WHERE office_code = 1
            GROUP BY state, zone, candidate_number, bu_received_at)
        SELECT zone, candidate_number, max(cumulative) FROM evolution GROUP BY ALL ORDER BY 1, 2
    """
    final = _q(db_path, sql)
    consolidated = _q(db_path, """SELECT zone, candidate_number, sum(vote_count) FROM votes
                                  WHERE office_code = 1 GROUP BY ALL ORDER BY 1, 2""")
    assert final == consolidated
    # zone 3 aggregates two municipalities
    assert (3, 13, 170) in consolidated


def test_curve_has_correct_order(db):
    db_path, _ = db
    curve = _q(db_path, """
        SELECT bu_received_at::VARCHAR,
               sum(sum(vote_count)) OVER (ORDER BY bu_received_at)
        FROM votes WHERE office_code = 1 AND candidate_number = 13
        GROUP BY bu_received_at ORDER BY 1""")
    assert [v for _, v in curve] == [120, 190, 280, 390, 490]


def test_example_queries_run(db):
    db_path, _ = db
    sql = (Path(__file__).parent.parent / "queries.sql").read_text()
    with duckdb.connect(str(db_path), read_only=True) as con:
        for stmt in [s for s in sql.split(";") if "SELECT" in s]:
            assert con.execute(stmt).fetchall()


def test_validation(db, tmp_path):
    db_path, _ = db
    official = {"cand": [{"n": "13", "nm": "C13", "vap": "490"},
                         {"n": "22", "nm": "C22", "vap": "500"}]}
    json_file = tmp_path / "official.json"
    json_file.write_text(json.dumps(official))
    assert validate(db_path, "ele2022", 544, 1, "br", json_file)
    assert validate(db_path, "ele2022", 544, 1, "sp", json_file)
    official["cand"][0]["vap"] = "491"
    json_file.write_text(json.dumps(official))
    assert not validate(db_path, "ele2022", 544, 1, "br", json_file)
    assert not validate(db_path, "ele2022", 999, 1, "br", json_file)


# --- heterogeneity across years -------------------------------------------------

def test_missing_columns_become_null(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_2018", SECTIONS,
              drop=("ANO_ELEICAO", "DT_EMISSAO_BU", "DT_ABERTURA"),
              rename={"CD_CARGO_PERGUNTA": "CD_CARGO", "DS_CARGO_PERGUNTA": "DS_CARGO"})
    db_path = tmp_path / "h.duckdb"
    load([zp], db_path, 2018)
    assert _q(db_path, "SELECT DISTINCT election_year FROM votes") == [(2018,)]       # came from the parameter
    assert _q(db_path, "SELECT DISTINCT office_code FROM votes ORDER BY 1") == [(1,), (3,)]
    assert _q(db_path, "SELECT count(*) FROM sections WHERE bu_issued_at IS NOT NULL")[0][0] == 0
    assert _q(db_path, "SELECT count(*) FROM sections WHERE bu_received_at IS NULL")[0][0] == 0


def test_years_coexist_in_same_db(tmp_path):
    db_path = tmp_path / "m.duckdb"
    load([_zip(tmp_path, "a", SECTIONS)], db_path, 2022)
    load([_zip(tmp_path, "b", SECTIONS, drop=("ANO_ELEICAO", "DT_ENCERRAMENTO"))],
         db_path, 2018)
    assert _q(db_path, "SELECT election_year, count(*) FROM votes GROUP BY 1 ORDER BY 1") == \
        [(2018, 20), (2022, 20)]
    # reloading 2018 does not delete 2022
    load([_zip(tmp_path, "c", SECTIONS[:1], drop=("ANO_ELEICAO",))], db_path, 2018)
    assert _q(db_path, "SELECT election_year, count(*) FROM votes GROUP BY 1 ORDER BY 1") == \
        [(2018, 4), (2022, 20)]


def test_missing_required_column_fails(tmp_path):
    zp = _zip(tmp_path, "x", SECTIONS, drop=("NR_ZONA",))
    with pytest.raises(ValueError, match="zone"):
        load([zp], tmp_path / "e.duckdb", 2022)


def test_new_format_registered_for_future_year(tmp_path):
    from election_tally.formats import BwebCSV, format_for, register

    @register(2098)
    class Hypothetical(BwebCSV):
        delim = "|"
        data_extension = ".txt"
        mapping = {**BwebCSV.mapping, "section": ["NUM_SECAO"]}

    assert isinstance(format_for(2098), Hypothetical)
    assert type(format_for(2099)).__name__ == "Hypothetical"   # inherits the previous one
    zp = _zip(tmp_path, "f", SECTIONS, delim="|", ext=".txt",
              rename={"NR_SECAO": "NUM_SECAO"})
    db_path = tmp_path / "f.duckdb"
    load([zp], db_path, 2098)
    assert _q(db_path, "SELECT count(DISTINCT section) FROM votes")[0][0] == 5


def test_year_without_prior_format_fails():
    from election_tally.formats import format_for
    with pytest.raises(SystemExit):
        format_for(2010)


def test_schema_gains_new_columns(tmp_path):
    from election_tally.load import create_schema
    db_path = tmp_path / "s.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE votes (election_year SMALLINT, vote_count INTEGER)")
        con.execute("INSERT INTO votes VALUES (2014, 5)")
        create_schema(con)
        assert con.execute("SELECT election_year, bu_received_at FROM votes").fetchall() == [(2014, None)]


def test_discovery_skips_hash_and_accepts_uppercase(monkeypatch):
    from election_tally import download

    base = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2018/buweb/"
    urls = [base + "BWEB_1t_AC_081020181525.zip", base + "BWEB_1t_AC_081020181525.zip.sha512",
            base + "BWEB_2t_SP_301020181744.zip", base + "leiame.pdf"]

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"result": {"resources": [{"url": u} for u in urls]}}

    monkeypatch.setattr(download.requests, "get", lambda *a, **k: Resp())
    recs = download.list_resources(2018)
    assert [(r["round"], r["state"]) for r in recs] == [(1, "AC"), (2, "SP")]
    assert recs[0]["sha512_url"] == urls[1]
    assert recs[1]["sha512_url"] is None



def test_unmapped_city_abroad_fails_without_touching_data(tmp_path):
    from election_tally.countries import UnmappedCityError

    db_path = tmp_path / "zz.duckdb"
    load([_zip(tmp_path, "ok", SECTIONS)], db_path, 2022)
    abroad = [("ZZ", 99999, "ATLÂNTIDA", 1, 1, "02/10/2022 20:00:00", 50, 40, {13: 20, 22: 20})]
    with pytest.raises(UnmappedCityError, match="99999,ATLÂNTIDA"):
        load([_zip(tmp_path, "zz", abroad)], db_path, 2022)
    assert _q(db_path, "SELECT count(*) FROM votes") == [(20,)]
    assert _q(db_path, "SELECT count(*) FROM votes WHERE state = 'ZZ'") == [(0,)]
