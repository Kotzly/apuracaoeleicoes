"""Small lookup tables, so finding a code does not mean scanning the huge `votes` table."""

from __future__ import annotations

from pathlib import Path

from . import derived

_METHOD_VERSION = 1  # raise when a table changes, so existing ones get rebuilt
_STATE_TABLE = "elections"
_TABLES = {
    # Questions (codes 51+) reuse codes with different text, so one code can have many names.
    # Vices and alternates have candidates but no section of their own.
    "offices": """
        SELECT office_code, office_name, min(election_year) AS first_year,
               max(election_year) AS last_year, office_code >= 51 AS is_question
        FROM (SELECT DISTINCT office_code, office_name, election_year FROM sections
              WHERE office_code > 0 AND office_name IS NOT NULL
              UNION
              SELECT DISTINCT office_code, office_name, election_year FROM candidates c
              WHERE office_code > 0 AND NOT EXISTS
                  (SELECT 1 FROM sections s WHERE s.office_code = c.office_code))
        GROUP BY office_code, office_name ORDER BY 1, 2""",
    "elections": """
        SELECT s.election_code, s.election_year, s.round_number, c.election_name, c.election_type
        FROM (SELECT DISTINCT election_code, election_year, round_number FROM sections
              WHERE election_code IS NOT NULL) s
        LEFT JOIN (SELECT election_code, election_year, any_value(election_name) AS election_name,
                          any_value(election_type) AS election_type
                   FROM candidates GROUP BY 1, 2) c USING (election_code, election_year)
        ORDER BY 2, 3, 1""",
    # A section number can change place between elections, so the year is part of the key.
    "section_locations": """
        SELECT election_year, round_number, state, municipality_code,
               any_value(municipality_name) AS municipality_name,
               any_value(municipality_ibge_code) AS municipality_ibge_code, zone, section,
               any_value(country) AS country, any_value(country_code) AS country_code
        FROM sections GROUP BY election_year, round_number, state, municipality_code, zone, section
        ORDER BY 1, 2, 3, 4, 7, 8""",
}


def _source_state(con: derived.Connection) -> str:
    sections = con.execute("SELECT count(*), sum(eligible_voters) FROM sections").fetchone()
    candidates = con.execute("SELECT count(*) FROM candidates").fetchone()[0]
    return f"v{_METHOD_VERSION} sections={sections} candidates={candidates}"


def build(db: Path, force: bool = False) -> None:
    """Rebuilds the lookup tables, unless `sections` and `candidates` are unchanged."""
    if not derived.has_tables(db, "candidates"):
        print("warning: lookups not built: the table `candidates` could not be loaded")
        return
    if not force and derived.is_current(db, _STATE_TABLE, _source_state):
        print("lookups: up to date")
        return
    with derived.rebuilding(db) as con:
        for name, select in _TABLES.items():
            con.execute(f"CREATE OR REPLACE TABLE {name} AS {select}")
            derived.report_rows(con, name)
        derived.mark_current(con, _STATE_TABLE, _source_state(con))
