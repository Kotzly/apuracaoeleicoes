"""Loads polling-station ballot reports (BU) of any supported year into a DuckDB file.

Model: one fact table `votes` (section × office × votable item) and one table `sections`
(section × office). No running totals are stored: the evolution comes from the cumulative
sum ordered by bu_received_at, grouped by the desired level.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

from . import countries, municipalities
from .formats import CANONICAL, OFFICES, format_for

# Canonical columns plus the ones derived during the load.
COLUMN_TYPES = {**CANONICAL, **countries.COLUMNS, "municipality_ibge_code": "INTEGER"}

SECTION_KEY = ["election_year", "round_number", "election_code", "state", "municipality_code",
               "zone", "section", "office_code"]
VOTES_COLUMNS = ["election_year", "round_number", "election_code", "state", "municipality_code",
                 "municipality_name", "zone", "section", "office_code", "office_name",
                 "vote_type", "candidate_number", "candidate_name", "party", "vote_count",
                 "bu_received_at", "country", "country_code", "municipality_ibge_code"]
# Eligible voters and turnout repeat on every votes row of a section; they live here
# so they are not summed twice.
SECTIONS_COLUMNS = SECTION_KEY + ["municipality_name", "office_name", "eligible_voters",
                                  "turnout", "abstentions", "opened_at", "closed_at",
                                  "bu_issued_at", "bu_received_at", "country", "country_code",
                                  "municipality_ibge_code"]
# How each column of `sections` is reduced from the section's votes rows.
_SECTION_AGGREGATION = {"opened_at": "min", "closed_at": "max",
                        "bu_issued_at": "min", "bu_received_at": "min"}


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Creates the tables, or adds new columns of the canonical schema (NULL for old data)."""
    for table, cols in (("votes", VOTES_COLUMNS), ("sections", SECTIONS_COLUMNS)):
        defs = ", ".join(f"{c} {COLUMN_TYPES[c]}" for c in cols)
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({defs})")
        for c in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {c} {COLUMN_TYPES[c]}")


def raw_columns(con: duckdb.DuckDBPyConnection, source: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]


def load_files(con: duckdb.DuckDBPyConnection, files: list[Path], year: int,
               offices: list[int] | None = None) -> int:
    """Loads already extracted files. Replaces what existed for the same year/round/state."""
    fmt = format_for(year)
    source = fmt.source_sql(files)
    select, nulls = fmt.select_sql(source, set(raw_columns(con, source)), files, offices)
    name = ", ".join(a.name for a in files)
    if nulls:
        print(f"  {name}: no match, filled with NULL: {nulls}")
    if "bu_received_at" in nulls:
        print(f"  warning: {year} has no receipt time; the evolution is not produced "
              "for this year.")

    _check_line_count(con, source, files, fmt.header_lines())
    con.execute(f"CREATE OR REPLACE TEMP TABLE staging AS {select}")
    # Sections that were not installed have the office code but no office name.
    con.execute("UPDATE staging SET office_name = o.name FROM (SELECT unnest(?) AS code, "
                "unnest(?) AS name) o WHERE staging.office_name IS NULL "
                "AND staging.office_code = o.code", [list(OFFICES), list(OFFICES.values())])
    # Fails before anything is replaced if a city abroad has no known country.
    countries.add_country(con, "staging")
    municipalities.add_ibge_code(con, "staging")
    n = con.execute("SELECT count(*) FROM staging").fetchone()[0]
    missing_time = con.execute(
        "SELECT count(*) FROM staging WHERE bu_received_at IS NULL").fetchone()[0]
    if missing_time and "bu_received_at" not in nulls:
        print(f"  warning: {missing_time:,} of {n:,} rows with invalid receipt time")

    keys = con.execute(
        "SELECT DISTINCT election_year, round_number, state FROM staging").fetchall()
    count_columns = {"eligible_voters", "turnout", "abstentions"}
    aggregates = ", ".join(
        f"{_SECTION_AGGREGATION.get(c, 'max' if c in count_columns else 'any_value')}({c})"
        for c in SECTIONS_COLUMNS if c not in SECTION_KEY)
    con.begin()
    try:
        for table in ("votes", "sections"):
            for key in keys:
                con.execute(f"DELETE FROM {table} WHERE election_year IS NOT DISTINCT FROM ? AND "
                            "round_number IS NOT DISTINCT FROM ? AND state IS NOT DISTINCT FROM ?",
                            list(key))
        con.execute(f"INSERT INTO votes ({', '.join(VOTES_COLUMNS)}) "
                    f"SELECT {', '.join(VOTES_COLUMNS)} FROM staging")
        con.execute(f"INSERT INTO sections ({', '.join(SECTIONS_COLUMNS)}) "
                    f"SELECT {', '.join(SECTION_KEY)}, {aggregates} FROM staging "
                    f"GROUP BY {', '.join(SECTION_KEY)}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("DROP TABLE IF EXISTS staging")
    return n


def _check_line_count(con, source: str, files: list[Path], header: int) -> None:
    """Makes sure the parser neither dropped nor merged lines (TSE sometimes gets quotes wrong)."""
    physical = 0
    for a in files:
        with open(a, "rb") as f:
            physical += sum(b.count(b"\n") for b in iter(lambda: f.read(1 << 24), b"")) - header
    parsed = con.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
    if parsed != physical:
        print(f"  WARNING: {physical:,} lines in the file, {parsed:,} parsed. "
              "Check quotes/line breaks in the raw file.")


def _data_files(file: Path, extension: str, dest: Path) -> list[Path]:
    """Data files: the ones inside the zip, or the file itself if it is not a zip."""
    if file.suffix.lower() != ".zip":
        return [file]
    with zipfile.ZipFile(file) as z:
        members = [m for m in z.namelist() if m.lower().endswith(extension)]
        return [Path(z.extract(m, dest)) for m in members]


def load(files: list[Path], db: Path, year: int,
         offices: list[int] | None = None) -> None:
    """Loads .zip files (as they come from the TSE) or already extracted files of a year."""
    fmt = format_for(year)
    con = duckdb.connect(str(db))
    create_schema(con)
    try:
        for file in files:
            tmp = Path(tempfile.mkdtemp(prefix="bweb_"))
            try:
                data = _data_files(file, fmt.data_extension, tmp)
                if not data:
                    print(f"{file.name}: no {fmt.data_extension} file in the zip, skipped")
                    continue
                n = load_files(con, data, year, offices)
                print(f"{file.name}: {n:,} rows")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        con.execute("CHECKPOINT")
    finally:
        con.close()


def inspect(file: Path, year: int) -> None:
    """Shows how the raw file's columns map onto the canonical schema."""
    fmt = format_for(year)
    tmp = Path(tempfile.mkdtemp(prefix="insp_"))
    try:
        data = _data_files(file, fmt.data_extension, tmp)
        if not data:
            raise SystemExit(f"No {fmt.data_extension} file in {file.name}")
        con = duckdb.connect()
        source = fmt.source_sql(data)
        raw = raw_columns(con, source)
        resolved = fmt.resolve(set(raw))
        constants = fmt.constants(data)
        print(f"Format: {type(fmt).__name__} (year {year})\n")
        for c, b in resolved.items():
            if b:
                mark = ""
            elif c in constants:
                mark = f"  (context constant: {constants[c]})"
            else:
                mark = "  <-- REQUIRED" if c in fmt.required else "  (NULL)"
            print(f"  {c:<20} <- {b or '-'}{mark}")
        used = {b for b in resolved.values() if b}
        print(f"\nUnused raw columns: {[b for b in raw if b not in used]}")
        row = con.execute(f"SELECT * FROM {source} LIMIT 1").fetchone()
        print("\nFirst row:")
        for b, v in zip(raw, row or []):
            print(f"  {b:<28} {v}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
