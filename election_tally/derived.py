"""What every derived table has in common: when to rebuild it, and how to do it safely.

The comment of a table records what it was built from (its "source state"). When the state computed
now differs, the table is out of date. Checking opens the database read-only, so it never conflicts
with a running Superset; a build opens it for writing, inside one transaction, so a failure leaves
the previous tables in place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

Connection = duckdb.DuckDBPyConnection


@contextmanager
def reading(db: Path) -> Iterator[Connection]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        yield con
    finally:
        con.close()


@contextmanager
def rebuilding(db: Path) -> Iterator[Connection]:
    """A transaction: closing without the commit, after an error, discards all of it."""
    con = duckdb.connect(str(db))
    try:
        con.begin()
        yield con
        con.commit()
        con.execute("CHECKPOINT")
    finally:
        con.close()


def has_tables(db: Path, *names: str) -> bool:
    with reading(db) as con:
        found = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name IN "
                            "(SELECT unnest(?))", [list(names)]).fetchone()[0]
    return found == len(names)


def table_comment(con: Connection, table: str) -> str | None:
    row = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = ?", [table]).fetchone()
    return row[0] if row else None


def is_current(db: Path, table: str, source_state: Callable[[Connection], str]) -> bool:
    with reading(db) as con:
        comment = table_comment(con, table)
        return comment is not None and comment == source_state(con)


def mark_current(con: Connection, table: str, state: str) -> None:
    con.execute(f"COMMENT ON TABLE {table} IS '{state}'")


def report_rows(con: Connection, *tables: str) -> None:
    """Prints the row counts. For tables filled by many INSERTs, call it after the commit: on 65
    million rows, `count(*)` within the transaction gave 1.1 million (DuckDB 1.5.6)."""
    for table in tables:
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n:,} rows", flush=True)
