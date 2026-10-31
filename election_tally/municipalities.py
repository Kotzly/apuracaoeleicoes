"""TSE municipality code -> IBGE municipality code.

The TSE and the IBGE number municipalities differently. Maps of municipalities use IBGE
shapes, so the load adds the IBGE code to every section in Brazil. The table comes from the
TSE file "Códigos oficiais de UF e municípios segundo o TSE e o IBGE"; a copy lives in
data/municipalities.csv and `python -m election_tally download-municipalities` refreshes it.
"""

from __future__ import annotations

import csv
import io
import zipfile
from importlib.resources import files
from pathlib import Path

import duckdb
import requests

URL = "https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip"
TABLE_PATH = files("election_tally").joinpath("data/municipalities.csv")
FIELDS = ["state", "municipality_code", "municipality_name", "ibge_code"]


def from_tse_zip(content: bytes) -> list[dict]:
    """Rows of the TSE zip, in our columns (codes as integers, names as published)."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("latin-1")
    rows = [{"state": r["SG_UF"], "municipality_code": int(r["CD_MUNICIPIO_TSE"]),
             "municipality_name": r["NM_MUNICIPIO_TSE"], "ibge_code": int(r["CD_MUNICIPIO_IBGE"]),
             "generated": r["DT_GERACAO"]}
            for r in csv.DictReader(io.StringIO(text), delimiter=";")]
    if not rows:
        raise ValueError("the TSE file has no rows")
    return rows


def download(output: Path | None = None) -> Path:
    """Downloads the TSE table and rewrites the copy kept in the repo (or `output`)."""
    output = Path(output or TABLE_PATH)
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    rows = from_tse_zip(r.content)
    with open(output, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["municipality_code"]))
    print(f"{output}: {len(rows):,} municipalities (TSE file generated on {rows[0]['generated']})")
    return output


def add_ibge_code(con: duckdb.DuckDBPyConnection, table: str = "staging") -> None:
    """Adds municipality_ibge_code to `table`; warns about municipalities in Brazil not found."""
    con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS municipality_ibge_code INTEGER")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE municipalities AS
        SELECT CAST(municipality_code AS INTEGER) AS municipality_code,
               CAST(ibge_code AS INTEGER) AS ibge_code
        FROM read_csv('{TABLE_PATH}', header = true, all_varchar = true)
    """)
    con.execute(f"""UPDATE {table} SET municipality_ibge_code = m.ibge_code FROM municipalities m
                    WHERE {table}.state IS DISTINCT FROM 'ZZ'
                      AND m.municipality_code = {table}.municipality_code""")
    missing = con.execute(f"""
        SELECT DISTINCT state, municipality_code, municipality_name FROM {table}
        WHERE state IS DISTINCT FROM 'ZZ' AND municipality_ibge_code IS NULL ORDER BY 1, 2
    """).fetchall()
    if missing:
        listed = ", ".join(f"{s}/{c} {n}" for s, c, n in missing[:10])
        print(f"  warning: {len(missing)} municipality(ies) without an IBGE code ({listed}); "
              "they won't appear on municipality maps. Try download-municipalities.")
