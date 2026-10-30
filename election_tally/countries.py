"""Country of each section: Brazil, or the country of the city abroad.

For sections abroad (state 'ZZ') the TSE files give the city as the "municipality" and no
country. The country comes from data/abroad_cities.csv, keyed by the TSE municipality code
(stable across years, unlike the city spelling). A city missing from that table stops the
load, so it can be added by hand instead of silently getting no country.
"""

from __future__ import annotations

from importlib.resources import files

import duckdb

ABROAD_STATE = "ZZ"
BRAZIL = ("Brasil", "BRA")
COLUMNS = {"country": "VARCHAR", "country_code": "VARCHAR"}  # ISO 3166-1 alpha-3
TABLE_PATH = files("election_tally").joinpath("data/abroad_cities.csv")


class UnmappedCityError(ValueError):
    """A city abroad that is not in data/abroad_cities.csv."""


def add_country(con: duckdb.DuckDBPyConnection, table: str = "staging") -> None:
    """Adds and fills the country columns of `table` (which must have state and municipality)."""
    for column, kind in COLUMNS.items():
        con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE abroad_cities AS
        SELECT CAST(municipality_code AS INTEGER) AS municipality_code, city, country, country_code
        FROM read_csv('{TABLE_PATH}', header = true, all_varchar = true)
    """)
    missing = con.execute(f"""
        SELECT DISTINCT t.municipality_code, t.municipality_name FROM {table} t
        LEFT JOIN abroad_cities a USING (municipality_code)
        WHERE t.state = '{ABROAD_STATE}' AND a.country IS NULL
        ORDER BY 1
    """).fetchall()
    if missing:
        listed = "\n".join(f"  {code},{name},<country>,<ISO alpha-3>" for code, name in missing)
        raise UnmappedCityError(
            f"{len(missing)} city/cities abroad without a country. Add them to "
            f"election_tally/data/abroad_cities.csv and load again:\n{listed}")
    con.execute(f"""UPDATE {table} SET country = ?, country_code = ?
                    WHERE state IS DISTINCT FROM '{ABROAD_STATE}'""", list(BRAZIL))
    con.execute(f"""UPDATE {table} SET country = a.country, country_code = a.country_code
                    FROM abroad_cities a
                    WHERE {table}.state = '{ABROAD_STATE}'
                      AND a.municipality_code = {table}.municipality_code""")
