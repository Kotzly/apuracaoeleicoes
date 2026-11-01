"""Blank and null votes, and the dashboard that reads them, on a small hand-made election."""

from pathlib import Path

import duckdb
import pytest
import yaml

from election_tally import blank_votes, results
from election_tally.load import create_schema

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"
# (year, round, office, state, municipality, vote type, candidate, votes)
ROWS = [(2022, 1, 3, "SP", 1, "Nominal", 13, 80), (2022, 1, 3, "SP", 1, "Branco", 95, 15),
        (2022, 1, 3, "SP", 1, "Nulo", 96, 5),
        (2022, 2, 3, "SP", 1, "Nominal", 13, 90), (2022, 2, 3, "SP", 1, "Nulo", 96, 10),
        (2022, 1, 3, "SP", 2, "Nominal", 13, 10),                     # no second round here
        (2022, 1, 6, "SP", 1, "Legenda", 13, 5), (2022, 1, 6, "SP", 1, "Branco", 95, 5),
        (2018, 1, 3, "SP", 1, "Anulado e apurado em separado", 13, 7),
        (2018, 1, 3, "SP", 1, "Nominal", 13, 3)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "b.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    for year, rnd, office, state, mun, kind, number, count in ROWS:
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                       municipality_name, zone, section, office_code, vote_type, candidate_number,
                       candidate_name, party, vote_count)
                       VALUES (?, ?, ?, ?, 'X', 1, 1, ?, ?, ?, 'N', 'PT', ?)""",
                    [year, rnd, state, mun, office, kind, number, count])
    con.close()
    results.build(path)
    blank_votes.build(path)
    return path


def _rows(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_counts_blank_null_and_all_votes_and_marks_places_with_both_rounds(db):
    got = _rows(db, """SELECT election_year, round_number, office_name, municipality_code, votes_blank,
                       votes_null, votes_blank_null, votes_valid, votes_total, has_second_round
                       FROM blank_null_votes ORDER BY 1, 3, 4, 2""")
    assert got == [(2018, 1, "Governador", 1, 0, 0, 0, 3, 3, False),      # anulado counts as neither
                   (2022, 1, "Deputado federal", 1, 5, 0, 5, 5, 10, False),  # legenda is a valid vote
                   (2022, 1, "Governador", 1, 15, 5, 20, 80, 100, True),
                   (2022, 2, "Governador", 1, 0, 10, 10, 90, 100, True),
                   (2022, 1, "Governador", 2, 0, 0, 0, 10, 10, False)]


def test_up_to_date_table_is_not_rebuilt(db, capsys):
    capsys.readouterr()
    blank_votes.build(db)
    assert "up to date" in capsys.readouterr().out


def _load(relative):
    return yaml.safe_load((DASHBOARDS / relative).read_text(encoding="utf-8"))


def _dashboard():
    return _load("dashboards/Brancos_e_nulos_7.yaml")


def _charts():
    return {c["uuid"]: c for c in map(lambda p: yaml.safe_load(p.read_text(encoding="utf-8")),
                                      (DASHBOARDS / "charts").glob("*.yaml"))}


def _tabs():
    position, charts = _dashboard()["position"], _charts()
    return {position[t]["meta"]["text"]: {
        position[c]["meta"]["chartId"]: charts[position[c]["meta"]["uuid"]]
        for row in position[t]["children"] for c in position[row]["children"]
        if position[c]["type"] == "CHART"} for t in position["TABS-main"]["children"]}


def _filters():
    return {f["name"]: f for f in _dashboard()["metadata"]["native_filter_configuration"]}


def test_every_metric_runs_on_the_table_and_rounds_compare_only_places_with_both(db):
    tabs = _tabs()
    assert list(tabs) == ["Entre anos", "Entre turnos"]
    for chart in tabs["Entre turnos"].values():
        wheres = [f["sqlExpression"] for f in chart["params"]["adhoc_filters"] if f["clause"] == "WHERE"]
        assert "has_second_round" in wheres
    variation = next(c for c in tabs["Entre turnos"].values() if c["slice_name"] == "UF por variação entre turnos")
    metric = next(m["sqlExpression"] for m in variation["params"]["metrics"] if m["label"] == "Variação (p.p.)")
    got = _rows(db, f"""SELECT {metric} FROM blank_null_votes
                        WHERE has_second_round AND office_name = 'Governador'""")
    assert got == [(pytest.approx(-10),)]  # 20% in round 1, 10% in round 2; city 2 stays out


def test_filters_reach_the_charts_where_their_column_means_something():
    tabs, filters = _tabs(), _filters()
    everything = {i for tab in tabs.values() for i in tab}
    scoped = {n: everything - set(f["scope"]["excluded"]) for n, f in filters.items()}
    names = {i: c["slice_name"] for tab in tabs.values() for i, c in tab.items()}
    assert scoped["Cargo"] == scoped["UF"] == everything
    assert {names[i] for i in scoped["Turno"]} == {"Brancos e nulos por eleição", "% de brancos e nulos por UF e ano"}
    assert {names[i] for i in scoped["Transição"]} == {"UF: maiores altas entre eleições", "UF: maiores quedas entre eleições"}
    assert names.keys() & scoped["Ano"] == set(tabs["Entre turnos"]) - {
        i for i, c in tabs["Entre turnos"].items() if c["slice_name"] == "1º e 2º turno por ano"}
    for name in ("Cargo", "Turno", "Ano", "Transição"):
        assert filters[name]["controlValues"]["enableEmptyFilter"] and filters[name]["defaultDataMask"]["filterState"]["value"]


def test_note_explains_the_caveats():
    text = " ".join(v["meta"]["code"] for v in _dashboard()["position"].values()
                    if isinstance(v, dict) and v.get("type") == "MARKDOWN")
    for phrase in ("todos os votos", "Senador", "mesmos locais", "2016, 2020 e 2024"):
        assert phrase in text, phrase
