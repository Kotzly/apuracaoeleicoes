"""The generated "Cargos" dashboard: tabs, the office of each chart, filter scopes and reproducibility."""

import subprocess
import sys
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
SUPERSET = ROOT / "docker" / "superset"
ASSETS = SUPERSET / "dashboards"
OFFICE_CODES = {"Presidente": 1, "Governador": 3, "Senador": 5, "Deputado federal": 6, "Deputado estadual": 7,
                "Deputado distrital": 8, "Prefeito": 11, "Vereador": 13}


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def _dashboard():
    return _load(ASSETS / "dashboards" / "Cargos_8.yaml")


@cache
def _charts():
    return {c["uuid"]: c for c in map(_load, (ASSETS / "charts").glob("*.yaml"))}


@cache
def _datasets():
    return {d["uuid"]: d for d in map(_load, (ASSETS / "datasets" / "Eleicoes_DuckDB").glob("*.yaml"))}


def _chart_nodes():
    return [n for n in _dashboard()["position"].values() if isinstance(n, dict) and n.get("type") == "CHART"]


def _tab_of(node):
    position = _dashboard()["position"]
    return position[node["parents"][3]]["meta"]["text"]


def test_each_office_is_one_tab():
    position = _dashboard()["position"]
    tabs = [position[t]["meta"]["text"] for t in position["TABS-main"]["children"]]
    assert tabs == list(OFFICE_CODES)


def test_every_chart_exists_and_its_id_is_unique():
    nodes, charts = _chart_nodes(), _charts()
    assert all(n["meta"]["uuid"] in charts for n in nodes)
    ids = [n["meta"]["chartId"] for n in nodes]
    assert len(ids) == len(set(ids))


def test_every_generated_chart_is_pinned_to_the_office_of_its_tab():
    position, charts = _dashboard()["position"], _charts()
    for name in (n for n in position if n.startswith("CHART-")):
        node = position[name]
        chart = charts[node["meta"]["uuid"]]
        tab = position[node["parents"][3]]["meta"]["text"]
        if not chart["slice_name"].startswith(tab + " · "):
            continue  # reused as it is
        fixed = [f for f in chart["params"]["adhoc_filters"] if f.get("subject") == "office_code"]
        sql = " ".join(f.get("sqlExpression", "") for f in chart["params"]["adhoc_filters"])
        if "office_code" in _dataset_columns(chart):
            assert fixed == [] or fixed[0]["comparator"] == OFFICE_CODES[tab], chart["slice_name"]
            assert fixed or "office_code IN" in sql, chart["slice_name"]


def _dataset_columns(chart):
    return {c["column_name"] for c in _datasets()[chart["dataset_uuid"]]["columns"]}


def test_filters_only_reach_charts_whose_dataset_has_the_column():
    charts, datasets = _charts(), _datasets()
    nodes = {n["meta"]["chartId"]: n for n in _chart_nodes()}
    for flt in _dashboard()["metadata"]["native_filter_configuration"]:
        column = flt["targets"][0]["column"]["name"]
        for chart_id in set(nodes) - set(flt["scope"]["excluded"]):
            chart = charts[nodes[chart_id]["meta"]["uuid"]]
            columns = {c["column_name"] for c in datasets[chart["dataset_uuid"]]["columns"]}
            assert column in columns, (flt["name"], chart["slice_name"])


def test_year_and_transition_defaults_never_sum_elections_and_match_the_offices():
    filters = {f["name"]: f for f in _dashboard()["metadata"]["native_filter_configuration"]}
    assert filters["Ano"]["defaultDataMask"]["filterState"]["value"] == [2026]
    assert filters["Ano (municipal)"]["defaultDataMask"]["filterState"]["value"] == [2024]
    assert filters["Transição"]["defaultDataMask"]["filterState"]["value"] == ["2022 → 2026 · votação principal (T1)"]
    assert filters["Transição (municipal)"]["defaultDataMask"]["filterState"]["value"] == ["2020 → 2024 · votação principal (T1)"]


def test_vote_base_filter_reaches_every_chart_on_a_dataset_that_doubles_its_rows():
    """Those datasets have one row per base: outside the filter they would count everything twice."""
    charts, datasets = _charts(), _datasets()
    flt = next(f for f in _dashboard()["metadata"]["native_filter_configuration"] if f["name"] == "Base do percentual")
    assert flt["controlValues"]["enableEmptyFilter"]
    for node in _chart_nodes():
        chart = charts[node["meta"]["uuid"]]
        columns = {c["column_name"] for c in datasets[chart["dataset_uuid"]]["columns"]}
        if "vote_base" in columns:
            assert node["meta"]["chartId"] not in flt["scope"]["excluded"], chart["slice_name"]


def test_generating_again_changes_nothing():
    before = {p.name: p.read_bytes() for p in (ASSETS / "charts").glob("Cargos__*.yaml")}
    before["dashboard"] = (ASSETS / "dashboards" / "Cargos_8.yaml").read_bytes()
    subprocess.run([sys.executable, str(SUPERSET / "build_office_dashboard.py")], check=True, capture_output=True)
    after = {p.name: p.read_bytes() for p in (ASSETS / "charts").glob("Cargos__*.yaml")}
    after["dashboard"] = (ASSETS / "dashboards" / "Cargos_8.yaml").read_bytes()
    assert before == after
