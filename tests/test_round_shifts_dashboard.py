"""The "Entre turnos" dashboard: dataset columns, charts and native filters."""

from pathlib import Path

import duckdb
import yaml

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"


def _load(relative: str) -> dict:
    return yaml.safe_load((DASHBOARDS / relative).read_text(encoding="utf-8"))


def _dashboard() -> dict:
    return _load("dashboards/Entre_turnos_4.yaml")


def _filters() -> dict:
    return {f["name"]: f for f in _dashboard()["metadata"]["native_filter_configuration"]}


def test_dataset_calculated_columns_compute_what_their_names_say():
    dataset = _load("datasets/Eleicoes_DuckDB/round_shifts_15.yaml")
    expr = {c["column_name"]: c["expression"] for c in dataset["columns"] if c["expression"]}
    row = ("SOBRAL", "CE", 0.5, 0.6, 0.1, -7)
    selects = ", ".join(f"{e} AS {n}" for n, e in expr.items())
    got = duckdb.sql(f"""SELECT {selects} FROM (VALUES {row}) t(municipality_name, state, share_round1,
                         share_round2, share_change, votes_change)""").fetchone()
    result = dict(zip(expr, got))
    assert result["label"] == "SOBRAL (CE)"
    assert result["pct_round1"] == 50 and result["pct_round2"] == 60
    assert round(result["share_change_pp"], 9) == 10 and round(result["abs_share_change_pp"], 9) == 10
    assert result["abs_votes_change"] == 7


def test_state_dataset_calculated_columns_match_the_city_ones():
    def calculated(relative):
        return {c["column_name"]: c["expression"] for c in _load(relative)["columns"] if c["expression"]}
    city = calculated("datasets/Eleicoes_DuckDB/round_shifts_15.yaml")
    del city["label"]  # only cities need the state next to the name
    assert calculated("datasets/Eleicoes_DuckDB/state_round_shifts_21.yaml") == city


def test_dashboard_has_city_state_and_zone_tabs_each_with_histogram_gains_drops_and_table():
    position = _dashboard()["position"]
    tabs = [position[t]["meta"]["text"] for t in position["TABS-main"]["children"]]
    assert tabs == ["Cidades, em porcentagem", "Cidades, em números absolutos",
                    "UF, em porcentagem", "UF, em números absolutos",
                    "Zonas, em porcentagem", "Zonas, em números absolutos"]
    for tab_id in position["TABS-main"]["children"]:
        charts = [position[c]["meta"]["sliceName"] for row in position[tab_id]["children"]
                  for c in position[row]["children"]]
        assert len(charts) == 4 and any("quedas" in n for n in charts) and any("altas" in n for n in charts)
    assert "Como ler" in position["MARKDOWN-note"]["meta"]["code"]


def test_state_tabs_only_use_state_charts_grouped_by_state():
    position = _dashboard()["position"]
    by_uuid = {c["uuid"]: c for c in map(lambda p: yaml.safe_load(p.read_text(encoding="utf-8")),
                                         (DASHBOARDS / "charts").glob("*.yaml"))}
    dataset = _load("datasets/Eleicoes_DuckDB/state_round_shifts_21.yaml")["uuid"]
    for tab_id in ("TAB-uf-pct", "TAB-uf-abs"):
        for row in position[tab_id]["children"]:
            for chart_id in position[row]["children"]:
                chart = by_uuid[position[chart_id]["meta"]["uuid"]]
                assert chart["dataset_uuid"] == dataset
                params = chart["params"]
                assert params.get("x_axis", "state") == "state"
                assert "label" not in params.get("all_columns", [])


def test_zone_tabs_only_use_zone_charts_and_zone_labels_tell_states_apart():
    position = _dashboard()["position"]
    by_uuid = {c["uuid"]: c for c in map(lambda p: yaml.safe_load(p.read_text(encoding="utf-8")),
                                         (DASHBOARDS / "charts").glob("*.yaml"))}
    dataset = _load("datasets/Eleicoes_DuckDB/zone_round_shifts_23.yaml")
    for tab_id in ("TAB-zone-pct", "TAB-zone-abs"):
        for row in position[tab_id]["children"]:
            for chart_id in position[row]["children"]:
                assert by_uuid[position[chart_id]["meta"]["uuid"]]["dataset_uuid"] == dataset["uuid"]
    expr = {c["column_name"]: c["expression"] for c in dataset["columns"] if c["expression"]}
    selects = ", ".join(f"{e} AS {n}" for n, e in expr.items())
    got = duckdb.sql(f"""SELECT {selects} FROM (VALUES (7, 'CE', 0.5, 0.6, 0.1, -7))
                         t(zone, state, share_round1, share_round2, share_change, votes_change)""").fetchone()
    assert dict(zip(expr, got))["label"] == "Zona 7 (CE)"


def test_filters_of_cities_stay_out_of_the_tabs_without_their_column():
    filters = _filters()
    assert filters["Município"]["scope"]["rootPath"] == ["TAB-pct", "TAB-abs"]
    assert filters["Mínimo de votos no 1º turno"]["scope"]["rootPath"] == ["TAB-pct", "TAB-abs",
                                                                           "TAB-zone-pct", "TAB-zone-abs"]
    for name in ("Ano", "Candidato", "UF"):
        assert filters[name]["scope"]["rootPath"] == ["ROOT_ID"]


def test_position_ids_are_consistent():
    position = _dashboard()["position"]
    chart_ids = [v["meta"]["chartId"] for v in position.values()
                 if isinstance(v, dict) and v.get("type") == "CHART"]
    assert len(chart_ids) == len(set(chart_ids)) == 24
    for key, node in position.items():
        if isinstance(node, dict) and "children" in node:
            assert node["id"] == key and all(child in position for child in node["children"])


def test_ranked_bars_pick_the_top_30_cities_by_label_in_opposite_directions():
    for gains_file, drops_file in (("Maiores_altas_pct_13", "Maiores_quedas_pct_14"),
                                   ("Maiores_altas_votos_17", "Maiores_quedas_votos_18")):
        gains = _load(f"charts/{gains_file}.yaml")["params"]
        drops = _load(f"charts/{drops_file}.yaml")["params"]
        for params in (gains, drops):
            assert params["x_axis"] == "label" and params["row_limit"] == 30
        assert gains["x_axis_sort_asc"] is False and drops["x_axis_sort_asc"] is False
        # drops are plotted as positive magnitudes so "top 30 by metric desc" means the 30 largest drops
        assert drops["metrics"][0]["column"]["column_name"].startswith("abs_")
        assert drops["order_desc"] is True


def test_histograms_read_all_rows():
    for relative in ("charts/Distribuicao_da_variacao_pct_12.yaml", "charts/Distribuicao_da_variacao_votos_16.yaml"):
        chart = _load(relative)
        assert chart["viz_type"] == "histogram_v2" and chart["params"]["row_limit"] >= 10000


def test_year_and_candidate_are_required_single_selects_with_a_default():
    filters = _filters()
    for name in ("Ano", "Candidato"):
        control = filters[name]["controlValues"]
        assert control["enableEmptyFilter"] and not control["defaultToFirstItem"] and not control["multiSelect"]
        assert filters[name]["defaultDataMask"]["filterState"]["value"]  # applied at load, not pending
    assert filters["Candidato"]["cascadeParentIds"] == [filters["Ano"]["id"]]
    assert filters["Ano"]["controlValues"]["sortAscending"] is False


def test_state_filter_excludes_selected_states_and_starts_with_none():
    state = _filters()["UF"]
    assert state["controlValues"]["inverseSelection"] and state["controlValues"]["multiSelect"]
    assert not state["controlValues"]["defaultToFirstItem"]
    assert state["defaultDataMask"]["filterState"] == {}


def test_city_filter_cascades_from_state_and_min_votes_is_a_range_on_round_one():
    filters = _filters()
    assert filters["Município"]["cascadeParentIds"] == [filters["UF"]["id"]]
    assert filters["Município"]["targets"][0]["column"]["name"] == "label"
    minimum = filters["Mínimo de votos no 1º turno"]
    assert minimum["filterType"] == "filter_range"
    assert minimum["targets"][0]["column"]["name"] == "votes_round1"
