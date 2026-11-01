"""Party names: renames are mapped to the current name, mergers are not.

`data/party_renames.csv` lists each old name and what it became. A party that changed name keeps
one name across years in the tables below (PMDB is MDB, PTN is PODE), but parties that merged
into another stay apart (DEM and PSL are not UNIÃO), so the charts show what the ballots said.
Names are matched exactly as written in the TSE files.
"""

from __future__ import annotations

import csv
import hashlib
from importlib.resources import files
from pathlib import Path

import duckdb

RENAMES_PATH = files("election_tally").joinpath("data/party_renames.csv")
_METHOD_VERSION = 3  # raise when a table changes, so existing ones get rebuilt
_STATE_TABLE = "party_names"

# Same name for the same code in every table below (the TSE spelling changes between files).
OFFICES = {1: "Presidente", 3: "Governador", 5: "Senador", 6: "Deputado federal",
           7: "Deputado estadual", 8: "Deputado distrital", 11: "Prefeito", 13: "Vereador"}
# Years of each cycle: federal offices and municipal offices.
FEDERAL_YEARS = (2014, 2018, 2022, 2026)
MUNICIPAL_YEARS = (2016, 2020, 2024)
MUNICIPAL_OFFICES = (11, 13)


def years_of(office: int) -> tuple[int, ...]:
    return MUNICIPAL_YEARS if office in MUNICIPAL_OFFICES else FEDERAL_YEARS


def read_renames(text: str | None = None) -> list[dict]:
    """Rows of the CSV (or of `text`, for tests)."""
    if text is None:
        text = RENAMES_PATH.read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def resolve(renames: list[dict]) -> dict[str, str]:
    """Maps every old name to the last name of its chain: PEN -> PATRI -> PATRIOTA."""
    step = {r["old_name"]: r["current_name"] for r in renames}
    final = {}
    for old in step:
        seen, name = {old}, step[old]
        while name in step:
            if name in seen:
                raise ValueError(f"rename cycle at {name!r}")
            seen.add(name)
            name = step[name]
        final[old] = name
    return final


def canonical_sql(column: str, mapping: dict[str, str]) -> str:
    """SQL expression turning an old party name into its current one."""
    if not mapping:
        return column
    whens = " ".join(f"WHEN '{old.replace(chr(39), chr(39) * 2)}' "
                     f"THEN '{new.replace(chr(39), chr(39) * 2)}'" for old, new in mapping.items())
    return f"(CASE {column} {whens} ELSE {column} END)"


def canonical_party_sql(column: str = "party") -> str:
    return canonical_sql(column, resolve(read_renames()))


def source_state(con: duckdb.DuckDBPyConnection, uses_candidates: bool = False) -> str:
    """Identifies what the tables below depend on: the results, the renames and the method."""
    results = con.execute("""SELECT comment FROM duckdb_tables()
                             WHERE table_name = 'national_results'""").fetchone()
    digest = hashlib.md5(RENAMES_PATH.read_bytes()).hexdigest()[:12]
    state = f"v{_METHOD_VERSION} renames={digest} results={results and results[0]}"
    if uses_candidates:
        state += f" candidates={con.execute('SELECT count(*) FROM candidates').fetchone()[0]}"
    return state


def has_tables(db: Path, *names: str) -> bool:
    con = duckdb.connect(str(db), read_only=True)
    try:
        n = con.execute(f"""SELECT count(*) FROM duckdb_tables()
                            WHERE table_name IN ({', '.join("'" + x + "'" for x in names)})""").fetchone()[0]
        return n == len(names)
    finally:
        con.close()


def is_current(db: Path, table: str, uses_candidates: bool = False) -> bool:
    """Read-only, so checking never conflicts with a running Superset."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        comment = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = ?",
                              [table]).fetchone()
        return bool(comment) and comment[0] == source_state(con, uses_candidates)
    finally:
        con.close()


def build(db: Path, force: bool = False) -> None:
    """Rebuilds `party_names` (the CSV), unless it and the results are unchanged."""
    if not has_tables(db, "national_results"):
        print("warning: party_names not built: the results tables are missing")
        return
    if not force and is_current(db, _STATE_TABLE):
        print("party_names: up to date")
        return
    renames = read_renames()
    final = resolve(renames)
    con = duckdb.connect(str(db))
    try:
        con.execute("""CREATE OR REPLACE TABLE party_names (
            old_name VARCHAR, current_name VARCHAR, year_of_change INTEGER, note VARCHAR,
            final_name VARCHAR)""")
        con.executemany("INSERT INTO party_names VALUES (?, ?, ?, ?, ?)",
                        [(r["old_name"], r["current_name"], int(r["year_of_change"]), r["note"],
                          final[r["old_name"]]) for r in renames])
        con.execute(f"COMMENT ON TABLE party_names IS '{source_state(con)}'")
        con.execute("CHECKPOINT")
        print(f"party_names: {len(renames)} rows")
    finally:
        con.close()
