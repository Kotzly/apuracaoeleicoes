"""The generated "Cargos" dashboard: tabs, the office of each chart, filter scopes and reproducibility."""

import shutil
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
SUPERSET = ROOT / "docker" / "superset"
SOURCE = SUPERSET / "dashboards"
GENERATOR = SUPERSET / "build_office_dashboard.py"
OFFICE_CODES = {"Presidente": 1, "Governador": 3, "Senador": 5, "Deputado federal": 6, "Deputado estadual": 7,
                "Deputado distrital": 8, "Prefeito": 11, "Vereador": 13}


def _generate(assets):
    subprocess.run([sys.executable, str(GENERATOR), "--assets", str(assets)], check=True, capture_output=True)


@cache
def _assets():
    """A copy of dashboards/ with the dashboard generated, as bootstrap.sh does."""
    assets = Path(tempfile.mkdtemp(prefix="cargos")) / "dashboards"
    shutil.copytree(SOURCE, assets, ignore=shutil.ignore_patterns("Cargos__*", "Cargos_8.yaml"))
    _generate(assets)
    return assets


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@cache
def _dashboard():
    return _load(_assets() / "dashboards" / "Cargos_8.yaml")


@cache
def _charts():
    return {c["uuid"]: c for c in map(_load, (_assets() / "charts").glob("*.yaml"))}


@cache
def _datasets():
    return {d["uuid"]: d for d in map(_load, (_assets() / "datasets" / "Eleicoes_DuckDB").glob("*.yaml"))}


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


def _output():
    found = {p.name: p.read_bytes() for p in (_assets() / "charts").glob("Cargos__*.yaml")}
    found["dashboard"] = (_assets() / "dashboards" / "Cargos_8.yaml").read_bytes()
    return found


def test_generating_again_changes_nothing():
    before = _output()
    _generate(_assets())
    assert before == _output()


def test_an_export_renaming_the_chart_files_does_not_change_the_output(tmp_path):
    assets = tmp_path / "dashboards"
    shutil.copytree(SOURCE, assets, ignore=shutil.ignore_patterns("Cargos__*", "Cargos_8.yaml"))
    for n, path in enumerate(sorted((assets / "charts").glob("*.yaml"))):
        path.rename(path.with_name(f"renamed_{n}.yaml"))
    _generate(assets)
    assert (assets / "dashboards" / "Cargos_8.yaml").read_bytes() == _output()["dashboard"]
    assert len(list((assets / "charts").glob("Cargos__*.yaml"))) == len(_output()) - 1


def _generated_uuids():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from build_office_dashboard import generated_uuids; "
            "print(*generated_uuids())")
    return set(subprocess.run([sys.executable, "-c", code, str(SUPERSET)], check=True, capture_output=True,
                              text=True).stdout.split())


def test_export_leaves_out_exactly_what_the_generator_makes():
    """generated_uuids is what export-dashboards.sh filters by: a chart that is reused must stay versioned."""
    uuids = _generated_uuids()
    output = {_load(p)["uuid"] for p in (_assets() / "charts").glob("Cargos__*.yaml")} | {_dashboard()["uuid"]}
    assert uuids == output
    versioned = {_load(p)["uuid"] for p in (SOURCE / "charts").glob("*.yaml") if not p.name.startswith("Cargos__")}
    assert not uuids & versioned
    assert {n["meta"]["uuid"] for n in _chart_nodes()} - uuids <= versioned


def _in_city_filter(chart_node):
    filters = _dashboard()["metadata"]["native_filter_configuration"]
    city = next(f for f in filters if f["name"] == "Município")
    return chart_node["meta"]["chartId"] not in city["scope"]["excluded"]


def test_municipal_charts_answer_to_the_city_filter_except_the_state_map():
    charts = _charts()
    outside = {charts[n["meta"]["uuid"]]["slice_name"] for n in _chart_nodes()
               if _tab_of(n) in ("Prefeito", "Vereador") and not _in_city_filter(n)}
    assert outside == {"Prefeito · Comparecimento por UF", "Vereador · Comparecimento por UF"}


def test_party_votes_read_a_table_with_the_city_and_only_the_first_round():
    charts = _charts()
    for office in OFFICE_CODES:
        chart = next(c for c in charts.values() if c["slice_name"] == f"{office} · Votos no 1º turno por partido")
        assert "label" in _dataset_columns(chart)
        assert [f["comparator"] for f in chart["params"]["adhoc_filters"] if f.get("subject") == "round_number"] == [1]


def test_check_rejects_a_municipal_chart_outside_the_city_filter():
    sys.path.insert(0, str(SUPERSET))
    import build_office_dashboard as builder
    from office_specs.common import Chart
    datasets = {"state_only": {"table_name": "elected_party", "columns": [{"column_name": "state"}]}}
    chart = ({"slice_name": "Vereador · X"}, 1, False)
    facts = {1: dict(office=13, subtab="Resultado", dataset="state_only", spec=Chart(key="x"))}
    assert "Vereador · X" in builder._check_city_filter({"u": chart}, facts, datasets)[0]
    facts[1]["spec"] = Chart(key="x", ignore=("municipio",))
    assert builder._check_city_filter({"u": chart}, facts, datasets) == []


def test_mayor_share_distribution_has_one_point_per_candidacy_and_answers_to_the_state_filter():
    charts = [_load(path) for path in (_assets() / "charts").glob("Cargos__*.yaml")]
    chart = next(c for c in charts if c["slice_name"] == "Prefeito · Distribuição do % de votos dos candidatos, por partido")
    assert chart["viz_type"] == "box_plot"
    assert chart["params"]["groupby"] == ["party_current"]
    assert chart["params"]["columns"] == ["municipality_code", "candidate_number"]
    assert "state" in {c["column_name"] for d in _load_datasets(chart) for c in d["columns"]}


def _load_datasets(chart):
    return [d for d in map(_load, (_assets() / "datasets" / "Eleicoes_DuckDB").glob("*.yaml")) if d["uuid"] == chart["dataset_uuid"]]


def test_winner_maps_give_each_party_its_own_color_and_answer_to_year_and_state():
    charts = [_load(path) for path in (_assets() / "charts").glob("Cargos__*.yaml")]
    maps = {c["slice_name"]: c for c in charts if "Partido eleito por" in c["slice_name"]}
    assert set(maps) == {"Governador · Partido eleito por estado (mapa)", "Prefeito · Partido eleito por município (mapa)"}
    for chart in maps.values():
        points = chart["params"]["color_breakpoints"]
        assert [p["minValue"] for p in points] == [p["maxValue"] for p in points] == list(range(1, len(points) + 1))
        colors = [(p["color"]["r"], p["color"]["g"], p["color"]["b"]) for p in points]
        assert len(set(colors)) == len(colors)
    chart_id = {c["meta"]["uuid"]: c["meta"]["chartId"] for c in _dashboard()["position"].values()
                if isinstance(c, dict) and c.get("type") == "CHART"}
    filters = {f["name"]: f for f in _dashboard()["metadata"]["native_filter_configuration"]}
    for chart in maps.values():
        for name in ("UF", "Partido"):
            assert chart_id[chart["uuid"]] not in filters[name]["scope"]["excluded"]
        assert chart_id[chart["uuid"]] in filters["Turno"]["scope"]["excluded"]

