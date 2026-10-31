"""Loads polling-station ballot reports (BU) of any supported year into a DuckDB file.

Model: one fact table `votes` (section × office × votable item) and one table `sections`
(section × office). No running totals are stored: the evolution comes from the cumulative
sum ordered by bu_received_at, grouped by the desired level.
"""

from __future__ import annotations

import re
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
RAM_DIR = Path("/dev/shm")
_MEMINFO = Path("/proc/meminfo")
_RAM_FOR_DUCKDB = 2 * 1024 ** 3  # left free for parsing and inserting
# Measured on 7.7 GB of RAM: a 2 GB CSV loads faster from RAM, a 3.3 GB one slower than from disk.
_RAM_EXTRACT_LIMIT = int(2.5 * 1024 ** 3)
_REPAIR_BLOCK = 1 << 24
_SEPARATOR = b'";"'
_C1_BYTES = bytes(range(0x80, 0xA0))
_UTF8_IN_LATIN1 = re.compile(rb"[\xc2-\xdf][\x80-\x9f]")


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
               offices: list[int] | None = None, check_lines: bool = True) -> int:
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

    con.execute(f"CREATE OR REPLACE TEMP TABLE staging AS {select}")
    if check_lines:
        # Staging already holds every parsed row, unless the office filter dropped some.
        _check_line_count(con, source if offices else "staging", files, fmt.header_lines())
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


def _check_line_count(con, parsed_from: str, files: list[Path], header: int) -> None:
    """Makes sure the parser neither dropped nor merged lines (TSE sometimes gets quotes wrong)."""
    physical = 0
    for a in files:
        with open(a, "rb") as f:
            physical += sum(b.count(b"\n") for b in iter(lambda: f.read(1 << 24), b"")) - header
    parsed = con.execute(f"SELECT count(*) FROM {parsed_from}").fetchone()[0]
    if parsed != physical:
        print(f"  WARNING: {physical:,} lines in the file, {parsed:,} parsed. "
              "Check quotes/line breaks in the raw file.")


def _extraction_dir(file: Path, extension: str) -> str | None:
    """RAM if the zip's data files fit there (None: the default temp dir, on disk).

    Writing the CSVs to disk is the slowest step on a slow SSD, and they are deleted right after.
    """
    if file.suffix.lower() != ".zip" or not RAM_DIR.is_dir() or not _MEMINFO.is_file():
        return None
    with zipfile.ZipFile(file) as z:
        size = sum(i.file_size for i in z.infolist() if i.filename.lower().endswith(extension))
    disk = shutil.disk_usage(RAM_DIR)
    available = next(int(line.split()[1]) * 1024 for line in _MEMINFO.read_text().splitlines()
                     if line.startswith("MemAvailable:"))
    limit = min(_RAM_EXTRACT_LIMIT, disk.free, available - _RAM_FOR_DUCKDB)
    return str(RAM_DIR) if size <= limit else None


def _repair_line(line: bytes) -> bytes:
    """Escapes the inner quotes of the fields of a line in which every field is quoted."""
    body = line.rstrip(b"\r\n")
    separators = body.count(_SEPARATOR)
    if body[:1] != b'"' or body[-1:] != b'"' or not separators or body.count(b";") != separators:
        return line
    fields = [f.replace(b'""', b'"').replace(b'"', b'""') for f in body[1:-1].split(_SEPARATOR)]
    return b'"' + _SEPARATOR.join(fields) + b'"' + line[len(body):]


def _repair_chunk(chunk: bytes) -> bytes:
    """Fixes the TSE's defects in whole lines of a Latin-1 file; both would stop the parser.

    Unescaped quotes in names (2018, 2020), and UTF-8 text such as IRMÃO (2016), whose bytes
    0x80-0x9F DuckDB refuses as Latin-1. Clean chunks cost two counts, not a pass per line.
    """
    if len(chunk.translate(None, _C1_BYTES)) != len(chunk):
        chunk = _UTF8_IN_LATIN1.sub(lambda m: m[0].decode().encode("latin-1"), chunk)
    if chunk.count(b'"') == 2 * (chunk.count(_SEPARATOR) + chunk.count(b"\n")):
        return chunk
    first = chunk[:chunk.find(b"\n") + 1]
    if first.count(b";") != first.count(_SEPARATOR):  # layout with unquoted numbers (2024 on)
        return chunk
    return b"".join(_repair_line(line) for line in chunk.splitlines(keepends=True))


def _extract_repaired(z: zipfile.ZipFile, member: str, target: Path) -> Path:
    with z.open(member) as source, open(target, "wb") as out:
        rest = b""
        while block := source.read(_REPAIR_BLOCK):
            cut = block.rfind(b"\n") + 1
            if cut:
                out.write(_repair_chunk(rest + block[:cut]))
                rest = b""
            rest += block[cut:]
        out.write(_repair_chunk(rest))
    return target


def _data_files(file: Path, extension: str, dest: Path) -> list[Path]:
    """Data files: the ones inside the zip, repaired, or the file itself if it is not a zip."""
    if file.suffix.lower() != ".zip":
        return [file]
    with zipfile.ZipFile(file) as z:
        members = [m for m in z.namelist() if m.lower().endswith(extension)]
        return [_extract_repaired(z, m, dest / Path(m).name) for m in members]


def load(files: list[Path], db: Path, year: int,
         offices: list[int] | None = None, check_lines: bool = True) -> None:
    """Loads .zip files (as they come from the TSE) or already extracted files of a year."""
    fmt = format_for(year)
    con = duckdb.connect(str(db))
    create_schema(con)
    try:
        for file in files:
            tmp = Path(tempfile.mkdtemp(prefix="bweb_",
                                        dir=_extraction_dir(file, fmt.data_extension)))
            try:
                data = _data_files(file, fmt.data_extension, tmp)
                if not data:
                    print(f"{file.name}: no {fmt.data_extension} file in the zip, skipped")
                    continue
                n = load_files(con, data, year, offices, check_lines)
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
