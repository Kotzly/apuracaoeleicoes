"""Municipality outlines: conversion of the IBGE mesh, the derived table and the shipped copy."""

import csv
import json

import duckdb
import pytest

from election_tally import shapes
from election_tally.load import create_schema

SQUARE = [[-46.0, -23.0], [-45.0, -23.0], [-45.0, -22.0], [-46.0, -22.0], [-46.0, -23.0]]
HOLE = [[-45.8, -22.8], [-45.2, -22.8], [-45.2, -22.2], [-45.8, -22.2], [-45.8, -22.8]]
ISLAND = [[-40.0, -10.0], [-39.9, -10.0], [-39.9, -9.9], [-40.0, -10.0]]


def _mesh():
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"codarea": "3500105"},
         "geometry": {"type": "Polygon", "coordinates": [SQUARE, HOLE]}},
        {"type": "Feature", "properties": {"codarea": "2900207"},
         "geometry": {"type": "MultiPolygon", "coordinates": [[SQUARE], [ISLAND]]}},
    ]})


def test_polyline_is_the_google_format_and_round_trips():
    assert shapes.encode_polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    points = [(-23.55, -46.63), (-22.9, -43.17), (0.0, 0.0), (-0.00001, 0.00001)]
    assert shapes.decode_polyline(shapes.encode_polyline(points)) == points


def test_from_geojson_keeps_outer_rings_only_one_row_per_part_without_the_closing_point():
    rows = shapes.from_geojson(_mesh())
    assert [(r["municipality_ibge_code"], r["part"]) for r in rows] == [(3500105, 1), (2900207, 1), (2900207, 2)]
    ring = shapes.decode_polyline(rows[0]["polyline"])
    assert ring == [(-23.0, -46.0), (-23.0, -45.0), (-22.0, -45.0), (-22.0, -46.0)]  # (lat, lon); hole dropped
    assert len(shapes.decode_polyline(rows[2]["polyline"])) == 3


def test_from_geojson_rejects_an_empty_mesh_and_reads_only_json():
    with pytest.raises(ValueError):
        shapes.from_geojson('{"type": "FeatureCollection", "features": []}')
    with pytest.raises(ValueError):  # not JSON: never evaluated as anything else
        shapes.from_geojson("__import__('os').getcwd()")


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=shapes.FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def _db(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    # municipality_results is what build() uses to count cities without an outline.
    con.execute("CREATE TABLE municipality_results (election_year INTEGER, municipality_ibge_code INTEGER)")
    con.execute("INSERT INTO municipality_results VALUES (2024, 3500105), (2024, 2900207), (2024, 5101837)")
    con.close()
    return path


def test_build_loads_the_table_and_is_up_to_date_next_time(tmp_path, capsys):
    db = _db(tmp_path)
    source = _write_csv(tmp_path / "s.csv", shapes.from_geojson(_mesh()))
    shapes.build(db, path=source)
    out = capsys.readouterr().out
    assert "3 rings of 2 municipalities" in out
    assert "2024: 2 of 3 municipalities have an outline (1 without)" in out  # the third has no shape
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT municipality_ibge_code, part FROM municipality_shapes ORDER BY 1, 2").fetchall() \
        == [(2900207, 1), (2900207, 2), (3500105, 1)]
    # JSON [longitude, latitude] pairs, the closing point not repeated
    assert json.loads(con.execute("SELECT polygon FROM municipality_shapes WHERE municipality_ibge_code = 3500105")
                      .fetchone()[0]) == [[-46.0, -23.0], [-45.0, -23.0], [-45.0, -22.0], [-46.0, -22.0]]
    con.close()
    shapes.build(db, path=source)
    assert "up to date" in capsys.readouterr().out
    shapes.build(db, force=True, path=source)
    assert "3 rings" in capsys.readouterr().out
    _write_csv(source, shapes.from_geojson(_mesh())[:1])  # the file changed: rebuilt
    shapes.build(db, path=source)
    assert "1 rings of 1 municipalities" in capsys.readouterr().out


def test_missing_or_broken_file_only_warns_and_keeps_the_old_table(tmp_path, capsys):
    db = _db(tmp_path)
    shapes.build(db, path=tmp_path / "missing.csv")
    assert "warning: municipality_shapes not built" in capsys.readouterr().out
    source = _write_csv(tmp_path / "s.csv", shapes.from_geojson(_mesh()))
    shapes.build(db, path=source)
    capsys.readouterr()
    broken = tmp_path / "broken.csv"
    broken.write_text("municipality_ibge_code,part,polyline\nnot-a-number,1,abc\n", encoding="utf-8")
    shapes.build(db, path=broken)
    assert "warning: municipality_shapes not built" in capsys.readouterr().out
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM municipality_shapes").fetchone()[0] == 3
    con.close()


def test_download_failure_raises_but_leaves_the_copy_alone(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise shapes.requests.ConnectionError("offline")
    monkeypatch.setattr(shapes.requests, "get", fail)
    target = tmp_path / "s.csv"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(shapes.requests.ConnectionError):
        shapes.download(target)
    assert target.read_text(encoding="utf-8") == "keep"


def test_the_shipped_copy_has_every_brazilian_municipality_with_valid_rings():
    rows = list(csv.DictReader(open(shapes.TABLE_PATH, encoding="utf-8")))
    codes = {int(r["municipality_ibge_code"]) for r in rows}
    assert len(codes) == 5570 and all(1100000 <= c <= 5400000 for c in codes)
    municipalities = list(csv.DictReader(open(shapes.files("election_tally").joinpath("data/municipalities.csv"),
                                              encoding="utf-8")))
    known = {int(r["ibge_code"]) for r in municipalities}
    assert codes <= known  # every outline belongs to a municipality of the TSE -> IBGE table
    for r in rows[:200]:
        ring = shapes.decode_polyline(r["polyline"])
        assert len(ring) >= 3 and all(-35 < lat < 6 and -75 < lon < -30 for lat, lon in ring)


def test_download_writes_the_mesh_sorted_by_municipality(tmp_path, monkeypatch):
    class Resp:
        content = _mesh().encode()
        def raise_for_status(self): pass

    monkeypatch.setattr(shapes.requests, "get", lambda url, timeout: Resp())
    target = tmp_path / "s.csv"
    assert shapes.download(target) == target
    rows = list(csv.DictReader(open(target, encoding="utf-8")))
    assert [(r["municipality_ibge_code"], r["part"]) for r in rows] == [("2900207", "1"), ("2900207", "2"), ("3500105", "1")]
