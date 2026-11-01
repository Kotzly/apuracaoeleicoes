"""Municipality outlines for the map of the Superset dashboards.

`municipality_shapes` has one row per outer ring of each municipality (`part`; a few have more than
one, such as islands), keyed by the IBGE code that `party_shifts` and the other tables already carry.
The file keeps each ring as a Google-encoded polyline (compact); the table has it as a JSON list
of [longitude, latitude] pairs in `polygon`, because Superset 6.1's deck.gl Polygon chart only
decodes the JSON format in the browser (its polyline decoder depends on a global that is not
loaded). Holes (enclaves) are dropped.

The outlines come from the IBGE mesh API at the lowest quality and are kept in
data/municipality_shapes.csv (about 0.9 MB), so building the table needs no internet.
`python -m election_tally download-municipality-shapes` refreshes that copy. Abroad has no outline.
"""

from __future__ import annotations

import csv
import hashlib
import json
from importlib.resources import files
from pathlib import Path

import duckdb
import requests

from . import derived

URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
       "?intrarregiao=municipio&formato=application/vnd.geo+json&qualidade=minima")
TABLE_PATH = files("election_tally").joinpath("data/municipality_shapes.csv")
FIELDS = ["municipality_ibge_code", "part", "polyline"]
TABLE = "municipality_shapes"
VERSION = 3  # raise when the table definition changes, so existing ones get rebuilt


def encode_polyline(points: list[tuple[float, float]]) -> str:
    """Google polyline (precision 5) of (latitude, longitude) points."""
    out, last = [], (0, 0)
    for lat, lon in points:
        current = (round(lat * 1e5), round(lon * 1e5))
        for value, before in zip(current, last):
            delta = value - before
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        last = current
    return "".join(out)


def decode_polyline(text: str) -> list[tuple[float, float]]:
    points, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        for axis in (0, 1):
            shift = result = 0
            while True:
                byte = ord(text[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))
    return points


def from_geojson(content: bytes | str) -> list[dict]:
    """Rows (code, part, polyline) of the IBGE mesh. Treats the content as data: parsed as JSON only."""
    features = json.loads(content)["features"]
    rows = []
    for feature in features:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        code = int(feature["properties"]["codarea"])
        for part, rings in enumerate(polygons, start=1):
            # GeoJSON is (longitude, latitude); the outer ring is the first one. The closing
            # point repeats the first and is left out.
            outer = [(lat, lon) for lon, lat in rings[0][:-1]]
            rows.append({"municipality_ibge_code": code, "part": part, "polyline": encode_polyline(outer)})
    if not rows:
        raise ValueError("the IBGE mesh has no municipalities")
    return rows


def download(output: Path | None = None) -> Path:
    """Downloads the IBGE mesh and rewrites the copy kept in the repo (or `output`)."""
    output = Path(output or TABLE_PATH)
    r = requests.get(URL, timeout=300)
    r.raise_for_status()
    rows = from_geojson(r.content)
    with open(output, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["municipality_ibge_code"], r["part"])))
    print(f"{output}: {len({r['municipality_ibge_code'] for r in rows}):,} municipalities, "
          f"{len(rows):,} rings, {output.stat().st_size / 1e6:.2f} MB")
    return output


def _source_state(path: Path) -> str:
    return f"v{VERSION} md5={hashlib.md5(path.read_bytes()).hexdigest()[:12]}"


def _report_unmatched(con: derived.Connection) -> None:
    """Cities with results but no outline, per election year (abroad has none and is not counted)."""
    rows = con.execute(f"""
        SELECT election_year, count(DISTINCT municipality_ibge_code) AS cities,
               count(DISTINCT municipality_ibge_code) FILTER (WHERE municipality_ibge_code NOT IN
                   (SELECT municipality_ibge_code FROM {TABLE})) AS without_shape
        FROM municipality_results WHERE municipality_ibge_code IS NOT NULL
        GROUP BY election_year ORDER BY election_year""").fetchall()
    for year, cities, missing in rows:
        print(f"  {year}: {cities - missing:,} of {cities:,} municipalities have an outline"
              + (f" ({missing} without)" if missing else ""))


def build(db: Path, force: bool = False, path: Path | None = None) -> None:
    """Loads the outlines into `municipality_shapes`, unless the table is already current."""
    path = Path(path or TABLE_PATH)
    try:
        state = _source_state(path)
    except OSError as exc:
        print(f"warning: {TABLE} not built: {exc}")
        return
    if not force and derived.is_current(db, TABLE, lambda _: state):
        print(f"{TABLE}: up to date")
        return
    con = duckdb.connect(str(db))
    try:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                rows = [(int(r["municipality_ibge_code"]), int(r["part"]),
                         json.dumps([[round(lon, 4), round(lat, 4)] for lat, lon in
                                     decode_polyline(r["polyline"])], separators=(",", ":")))
                        for r in csv.DictReader(f)]
            con.execute(f"""CREATE OR REPLACE TABLE {TABLE} (municipality_ibge_code INTEGER,
                            part SMALLINT, polygon VARCHAR)""")
            con.executemany(f"INSERT INTO {TABLE} VALUES (?, ?, ?)", sorted(rows))
        except (duckdb.Error, OSError, KeyError, ValueError, IndexError) as exc:
            print(f"warning: {TABLE} not built: {exc}")
            return
        derived.mark_current(con, TABLE, state)
        n, cities = con.execute(f"SELECT count(*), count(DISTINCT municipality_ibge_code) FROM {TABLE}").fetchone()
        print(f"{TABLE}: {n:,} rings of {cities:,} municipalities")
        if con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'municipality_results'").fetchone()[0]:
            _report_unmatched(con)
        con.execute("CHECKPOINT")
    finally:
        con.close()
