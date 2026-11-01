"""Tables loaded from other TSE open-data files: candidates, seats, coalitions and electorate.

Each file has one zip per year. Years come from `votes`; a year already in a table is not
downloaded again unless forced or the table's definition changes (`version`). A failure only
warns: the dashboards must still start, for example without internet.

Scope of the rows tied to a race: `country` is set only for national races (president), `state`
for races of a state or municipality, `municipality_*` only for municipal races.
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import requests

from . import derived, parties
from .domain import sql_string
from .repair import extract_repaired

URL = "https://cdn.tse.jus.br/estatistica/sead/odsele/{folder}/{folder}_{year}.zip"
NATIONAL = "BR"
_NULL_MARKERS = ("#NULO#", "#NULO", "#NE", "")
_TEXT_BLOCK = 1 << 24
_RACE_SCOPE = f"""
    CASE WHEN raw_state = '{NATIONAL}' THEN 'Brasil' END AS country,
    CASE WHEN raw_state = '{NATIONAL}' THEN 'BRA' END AS country_code,
    nullif(raw_state, '{NATIONAL}') AS state,
    TRY_CAST(raw_unit AS INTEGER) AS municipality_code,
    CASE WHEN TRY_CAST(raw_unit AS INTEGER) IS NOT NULL THEN electoral_unit END
        AS municipality_name,
    electoral_unit"""
_RACE_COLUMNS = {
    "election_year": (("ANO_ELEICAO",), "INTEGER"), "election_code": (("CD_ELEICAO",), "INTEGER"),
    "election_name": (("DS_ELEICAO",), "VARCHAR"),
    "election_type": (("NM_TIPO_ELEICAO",), "VARCHAR"), "raw_state": (("SG_UF",), "VARCHAR"),
    "raw_unit": (("SG_UE",), "VARCHAR"), "electoral_unit": (("NM_UE",), "VARCHAR"),
    "office_code": (("CD_CARGO",), "INTEGER"), "office_name": (("DS_CARGO",), "VARCHAR"),
}


@dataclass(frozen=True)
class Table:
    name: str
    folder: str
    # column -> (accepted raw columns, type). Personal data such as CPF is left out on purpose.
    columns: dict
    select: str  # from a subquery with the columns above
    group_by: str | None = None
    version: int = 1


TABLES = [
    Table("candidates", "consulta_cand", {
        **_RACE_COLUMNS, "round_number": (("NR_TURNO",), "INTEGER"),
        "candidate_id": (("SQ_CANDIDATO",), "BIGINT"),
        "candidate_number": (("NR_CANDIDATO",), "INTEGER"),
        "candidate_name": (("NM_URNA_CANDIDATO",), "VARCHAR"),
        "full_name": (("NM_CANDIDATO",), "VARCHAR"), "party": (("SG_PARTIDO",), "VARCHAR"),
        "candidacy_status": (("DS_SITUACAO_CANDIDATURA",), "VARCHAR"),
        "result": (("DS_SIT_TOT_TURNO",), "VARCHAR"), "gender": (("DS_GENERO",), "VARCHAR"),
        "race": (("DS_COR_RACA",), "VARCHAR"), "education": (("DS_GRAU_INSTRUCAO",), "VARCHAR"),
        "occupation": (("DS_OCUPACAO",), "VARCHAR"),
    }, f"""SELECT election_year, round_number, election_code, election_name, election_type,
                  {_RACE_SCOPE}, office_code, office_name, candidate_id, candidate_number,
                  candidate_name, full_name, party, candidacy_status, result, gender, race,
                  education, occupation""", version=2),
    Table("seats", "consulta_vagas", {
        **_RACE_COLUMNS, "seats": (("QT_VAGA", "QT_VAGAS"), "INTEGER"),
    }, f"""SELECT election_year, election_code, election_name, election_type, {_RACE_SCOPE},
                  office_code, upper(office_name) AS office_name, seats""", version=2),
    Table("coalitions", "consulta_coligacao", {
        **_RACE_COLUMNS, "round_number": (("NR_TURNO",), "INTEGER"),
        "group_type": (("TP_AGREMIACAO",), "VARCHAR"),
        "party_number": (("NR_PARTIDO",), "INTEGER"), "party": (("SG_PARTIDO",), "VARCHAR"),
        "party_name": (("NM_PARTIDO",), "VARCHAR"), "federation": (("SG_FEDERACAO",), "VARCHAR"),
        "federation_name": (("NM_FEDERACAO",), "VARCHAR"),
        "coalition_id": (("SQ_COLIGACAO",), "BIGINT"), "coalition_name": (("NM_COLIGACAO",), "VARCHAR"),
        "coalition_parties": (("DS_COMPOSICAO_COLIGACAO",), "VARCHAR"),
        "status": (("DS_SITUACAO",), "VARCHAR"),
    }, f"""SELECT election_year, round_number, election_code, election_name, election_type,
                  {_RACE_SCOPE}, office_code, upper(office_name) AS office_name, group_type,
                  party_number, party, party_name, federation, federation_name, coalition_id,
                  coalition_name, coalition_parties, status"""),
    # Summed over zones, marital status and race (known only in recent years): the raw file
    # has up to 2 GB per year.
    Table("electorate_profile", "perfil_eleitorado", {
        "election_year": (("ANO_ELEICAO", "AA_ELEICAO"), "INTEGER"),
        "state": (("SG_UF",), "VARCHAR"), "municipality_code": (("CD_MUNICIPIO",), "INTEGER"),
        "municipality_name": (("NM_MUNICIPIO",), "VARCHAR"), "gender": (("DS_GENERO",), "VARCHAR"),
        "age_group": (("DS_FAIXA_ETARIA",), "VARCHAR"),
        "education": (("DS_GRAU_ESCOLARIDADE",), "VARCHAR"),
        "voters": (("QT_ELEITORES_PERFIL", "QT_ELEITORES"), "BIGINT"),
    }, """SELECT election_year, state, municipality_code, any_value(municipality_name)
                     AS municipality_name, gender, age_group, education, sum(voters)::BIGINT AS voters""",
          group_by="election_year, state, municipality_code, gender, age_group, education"),
]


def _fetch(url: str, target: Path) -> None:
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(target, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def _national_file(archive: Path, dest: Path) -> Path:
    """The file with all states, in UTF-8: these files also hold Windows-1252 bytes."""
    with zipfile.ZipFile(archive) as z:
        data = [m for m in z.namelist() if m.lower().endswith(".csv")]
        member = next((m for m in data if m.upper().endswith("_BRASIL.CSV")), None)
        if member is None and len(data) == 1:
            member = data[0]
        if member is None:
            raise zipfile.BadZipFile(f"no national file among {len(data)} files")
        repaired = extract_repaired(z, member, dest / "repaired.csv")
    target = dest / "national.csv"
    with open(repaired, encoding="cp1252", errors="replace", newline="") as source, \
            open(target, "w", encoding="utf-8", newline="") as out:
        while block := source.read(_TEXT_BLOCK):
            out.write(block)
    repaired.unlink()
    return target


def _pending(db: Path, table: Table, force: bool) -> tuple[list[int], bool]:
    """Years to load and whether the table must be recreated. Read-only: never conflicts with Superset."""
    with derived.reading(db) as con:
        years = {r[0] for r in con.execute(
            "SELECT DISTINCT election_year FROM votes WHERE election_year IS NOT NULL").fetchall()}
        if force or derived.table_comment(con, table.name) != f"v{table.version}":
            return sorted(years), True
        loaded = {r[0] for r in con.execute(
            f"SELECT DISTINCT election_year FROM {table.name}").fetchall()}
        return sorted(years - loaded), False


def _load_year(con: duckdb.DuckDBPyConnection, table: Table, file: Path, year: int,
               recreate: bool) -> int:
    source = (f"read_csv({sql_string(file.as_posix())}, delim=';', quote='\"', escape='\"', header=true, "
              "all_varchar=true, strict_mode=false)")
    present = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
    markers = ", ".join(f"'{m}'" for m in _NULL_MARKERS)
    fields, absent = [], []
    for column, (raws, kind) in table.columns.items():
        raw = next((r for r in raws if r in present), None)
        if raw is None:
            absent.append(column)
        fields.append(f"""CASE WHEN trim("{raw}") IN ({markers}) THEN NULL
                          ELSE TRY_CAST(trim("{raw}") AS {kind}) END AS {column}"""
                      if raw else f"NULL::{kind} AS {column}")
    if absent:
        print(f"  {table.name} of {year}: no match in the file, filled with NULL: {absent}")
    select = f"{table.select} FROM (SELECT {', '.join(fields)} FROM {source})"
    if table.group_by:
        select += f" GROUP BY {table.group_by}"
    con.begin()
    try:
        if recreate:
            con.execute(f"CREATE OR REPLACE TABLE {table.name} AS {select}")
            con.execute(f"COMMENT ON TABLE {table.name} IS 'v{table.version}'")
        else:
            con.execute(f"DELETE FROM {table.name} WHERE election_year = ?", [year])
            con.execute(f"INSERT INTO {table.name} BY NAME {select}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    return con.execute(f"SELECT count(*) FROM {table.name} WHERE election_year = ?",
                       [year]).fetchone()[0]


def build_table(db: Path, table: Table, force: bool = False) -> list[int]:
    """Loads the years missing from one table; returns the years loaded."""
    years, recreate = _pending(db, table, force)
    if not years:
        print(f"{table.name}: up to date")
        return []
    loaded = []
    con = duckdb.connect(str(db))
    try:
        for year in years:
            with tempfile.TemporaryDirectory(prefix="tse_") as tmp:
                archive = Path(tmp) / f"{year}.zip"
                try:
                    _fetch(URL.format(folder=table.folder, year=year), archive)
                    file = _national_file(archive, Path(tmp))
                    # Replaces the old table only when a year is ready to take its place.
                    n = _load_year(con, table, file, year, recreate and not loaded)
                    print(f"{table.name}: {year}: {n:,} rows", flush=True)
                    loaded.append(year)
                # Anything wrong with one file must not keep the dashboards from starting.
                except (requests.RequestException, zipfile.BadZipFile, duckdb.Error, OSError,
                        ValueError) as exc:
                    print(f"warning: {table.name} of {year} not loaded: {exc}", flush=True)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return loaded


def build(db: Path, force: bool = False) -> None:
    if derived.has_tables(db, parties.SYNTHETIC_TABLE):
        print("TSE tables: synthetic dataset, nothing to download")
        return
    for table in TABLES:
        build_table(db, table, force)
