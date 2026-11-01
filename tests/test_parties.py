"""Party renames: one name per party across years, mergers left alone."""

import duckdb
import pytest

from election_tally import parties

CSV = """old_name,current_name,year_of_change,note
PEN,PATRI,2018,a
PATRI,PATRIOTA,2019,b
PMDB,MDB,2017,c
"""


def test_chain_resolves_to_the_last_name():
    final = parties.resolve(parties.read_renames(CSV))
    assert final == {"PEN": "PATRIOTA", "PATRI": "PATRIOTA", "PMDB": "MDB"}


def test_rename_cycle_is_an_error():
    with pytest.raises(ValueError):
        parties.resolve([{"old_name": "A", "current_name": "B"},
                         {"old_name": "B", "current_name": "A"}])


def test_sql_renames_old_names_and_keeps_the_rest():
    expr = parties.canonical_sql("p", parties.resolve(parties.read_renames(CSV)))
    rows = duckdb.sql(f"""SELECT p, {expr} FROM (VALUES ('PEN'), ('PATRI'), ('PMDB'), ('MDB'),
                          ('DEM'), ('UNIÃO'), (NULL)) t(p)""").fetchall()
    assert dict(rows) == {"PEN": "PATRIOTA", "PATRI": "PATRIOTA", "PMDB": "MDB", "MDB": "MDB",
                          "DEM": "DEM", "UNIÃO": "UNIÃO", None: None}


def test_the_packaged_table_renames_but_does_not_merge():
    final = parties.resolve(parties.read_renames())
    assert final["PMDB"] == "MDB" and final["PEN"] == "PATRIOTA" and final["PC do B"] == "PCDOB"
    # mergers stay apart, and no name maps to itself
    for merged in ("DEM", "PSL", "PRP", "PHS", "PPL", "PROS", "PSC", "PTB", "PATRIOTA"):
        assert merged not in final
    assert all(old != new for old, new in final.items())
    assert not set(final) & set(final.values())
