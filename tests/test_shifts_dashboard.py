"""The "Mudança entre eleições" dashboard: datasets, charts, tabs and the scope of each native filter."""

from pathlib import Path

import duckdb
import yaml

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"
DATASETS = DASHBOARDS / "datasets" / "Eleicoes_DuckDB"
FLOW_CHARTS = {"e91b7f30-4c26-4a8d-8f15-0d2c6b7a9e52", "6d3f8a14-92c7-4e6b-b1d0-58a7e2f4c963"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dashboard() -> dict:
    return _load(DASHBOARDS / "dashboards" / "Mudanca_entre_eleicoes_6.yaml")


def _datasets() -> dict:
    return {d["uuid"]: d for d in map(_load, DATASETS.glob("*.yaml"))}


def _dataset(table: str) -> dict:
    return next(d for d in _datasets().values() if d["table_name"] == table)


def _charts() -> dict:
    return {c["uuid"]: c for c in map(_load, (DASHBOARDS / "charts").glob("*.yaml"))}


def _filters() -> dict:
    return {f["name"]: f for f in _dashboard()["metadata"]["native_filter_configuration"]}


def _tab_charts() -> dict:
    """Tab title -> {chartId: chart yaml}."""
    position, charts = _dashboard()["position"], _charts()
    result = {}
    for tab_id in position["TABS-main"]["children"]:
        found = {}
        for row in position[tab_id]["children"]:
            for child in position[row]["children"]:
                if position[child]["type"] == "CHART":
                    meta = position[child]["meta"]
                    found[meta["chartId"]] = charts[meta["uuid"]]
        result[position[tab_id]["meta"]["text"]] = found
    return result


def _all_charts() -> dict:
    return {i: c for tab in _tab_charts().values() for i, c in tab.items()}


def _scoped(flt: dict) -> set:
    return set(_all_charts()) - set(flt["scope"]["excluded"])


def _names(ids: set) -> set:
    return {_all_charts()[i]["slice_name"] for i in ids}


def _expressions(table: str) -> dict:
    return {c["column_name"]: c["expression"] for c in _dataset(table)["columns"] if c["expression"]}


def test_tabs_and_slug_and_the_old_flow_dashboard_is_gone():
    assert list(_tab_charts()) == ["Mapa", "Estados e cidades", "Zonas e seções", "Volatilidade",
                                   "Fluxo dos mais votados"]
    assert _dashboard()["slug"] == "mudanca-entre-eleicoes"
    assert not (DASHBOARDS / "dashboards" / "Fluxo_dos_mais_votados_2.yaml").exists()


def test_flow_charts_moved_keeping_their_uuids_and_have_their_own_filters():
    flow = _tab_charts()["Fluxo dos mais votados"]
    assert {c["uuid"] for c in flow.values()} == FLOW_CHARTS
    own = {n: f for n, f in _filters().items() if _scoped(f) <= set(flow)}
    assert set(own) == {"Transição (fluxo)", "UF (fluxo)", "Município (fluxo)", "Mais votado na origem",
                        "Mais votado no destino"}
    for flt in own.values():
        assert _scoped(flt) == set(flow)
    assert own["Município (fluxo)"]["cascadeParentIds"] == [own["UF (fluxo)"]["id"]]
    for name, flt in _filters().items():
        if name not in own:
            assert not _scoped(flt) & set(flow), name


def test_every_chart_has_a_chart_level_filter_on_level_matching_its_tab():
    expected = {"Estados e cidades": {"UF", "Município"}, "Zonas e seções": {"Zona", "Seção"},
                "Volatilidade": {"UF", "Município"}, "Mapa": {"UF", "Município"}}
    for tab, charts in _tab_charts().items():
        for chart in charts.values():
            if tab == "Fluxo dos mais votados":
                continue
            levels = [f["comparator"] for f in chart["params"]["adhoc_filters"] if f.get("subject") == "level"]
            assert len(levels) == 1 and levels[0] in expected[tab], chart["slice_name"]


def test_each_level_has_gains_drops_and_a_table():
    by_level = {}
    for chart in _all_charts().values():
        for f in chart["params"].get("adhoc_filters", []):
            if f.get("subject") == "level":
                by_level.setdefault(f["comparator"], []).append(chart["viz_type"])
    for level in ("UF", "Município", "Zona", "Seção"):
        assert sorted(by_level[level]).count("table") == 1, level
        assert by_level[level].count("echarts_timeseries_bar") >= 2, level
    for chart in _all_charts().values():
        if chart["viz_type"] == "table":
            assert chart["params"]["row_limit"] == 30 and chart["params"]["query_mode"] == "raw"
            assert chart["params"]["order_by_cols"] == ['["abs_share_change_pp", false]']


def test_rankings_are_top_30_and_drops_rank_a_positive_magnitude():
    for chart in _all_charts().values():
        params = chart["params"]
        if chart["viz_type"] == "echarts_timeseries_bar":
            assert params["row_limit"] in (30, 40) and params["order_desc"] and params["x_axis"] == "unit_label"
            metric = params["metrics"][0]["column"]["column_name"]
            wheres = [f.get("sqlExpression") for f in params["adhoc_filters"]]
            if metric == "drop_pp":
                assert "share_change < 0" in wheres
            if metric == "share_change_pp":
                assert "share_change > 0" in wheres


def test_maps_use_brazil_state_iso_a_diverging_scheme_for_shifts_and_leave_out_abroad():
    maps = [c for c in _all_charts().values() if c["viz_type"] == "country_map"]
    assert len(maps) == 2
    for chart in maps:
        params = chart["params"]
        assert params["select_country"] == "brazil" and params["entity"] == "state_iso"
        assert any(f.get("sqlExpression") == "state <> 'ZZ'" for f in params["adhoc_filters"])
    shift = next(c for c in maps if c["params"]["metric"]["column"]["column_name"] == "share_change_pp")
    assert shift["params"]["linear_color_scheme"] == "schemeRdBu"  # red low, blue high
    layout = _dashboard()["position"]
    assert "MARKDOWN-municipal-map-placeholder" not in layout and "TAB-map" in layout["TAB-map"]["id"]


def test_city_map_is_a_polygon_chart_over_the_shapes_joined_to_municipality_shifts():
    dataset = _dataset("party_shift_map")
    sql = " ".join(dataset["sql"].split())
    assert "FROM party_shifts p JOIN municipality_shapes s ON s.municipality_ibge_code = p.municipality_ibge_code" in sql
    assert "WHERE p.level = 'Município'" in sql and "p.*" in sql
    columns = {c["column_name"] for c in dataset["columns"]}
    # the native filters apply by column name, so the dataset must expose all of them
    assert {"office_name", "transition_label", "party", "state", "label", "share_change_pp", "polygon"} <= columns
    chart = next(c for c in _charts().values() if c["viz_type"] == "deck_polygon")
    assert chart["dataset_uuid"] == dataset["uuid"]
    params = chart["params"]
    # JSON: the 6.1 browser only decodes this format of the polygon column
    assert params["line_column"] == "polygon" and params["line_type"] == "json" and not params["reverse_long_lat"]
    assert "share_change" in params["metric"]["sqlExpression"] and params["row_limit"] >= 5600
    # colors come from fixed breakpoints (no JavaScript), diverging and centered on zero
    assert params["color_scheme_type"] == "color_breakpoints"
    breaks = params["color_breakpoints"]
    assert breaks[0]["minValue"] <= -100 and breaks[-1]["maxValue"] >= 100
    assert all(a["maxValue"] == b["minValue"] for a, b in zip(breaks, breaks[1:]))
    middle = breaks[len(breaks) // 2]
    assert middle["minValue"] == -middle["maxValue"] < 0
    assert all(b["minValue"] == -breaks[-1 - i]["maxValue"] for i, b in enumerate(breaks))
    assert all(b["color"]["r"] > b["color"]["b"] for b in breaks[:len(breaks) // 2])  # losses red
    assert all(b["color"]["r"] < b["color"]["b"] for b in breaks[len(breaks) // 2 + 1:])  # gains blue
    assert not params.get("js_data_mutator") and not params.get("js_tooltip") and not params.get("mapbox_style")


def test_city_map_is_in_scope_of_the_party_filters_and_has_a_legend_note():
    position = _dashboard()["position"]
    chart = next(c for c in _charts().values() if c["viz_type"] == "deck_polygon")
    meta = next(v["meta"] for v in position.values()
                if isinstance(v, dict) and v.get("meta", {}).get("uuid") == chart["uuid"])
    filters = _filters()
    for name in ("Cargo", "Transição", "Partido", "UF", "Município"):
        assert meta["chartId"] not in filters[name]["scope"]["excluded"]
    ids = [v["meta"]["chartId"] for v in position.values()
           if isinstance(v, dict) and "chartId" in v.get("meta", {})]
    assert len(ids) == len(set(ids))
    note = next(v for k, v in position.items() if k.startswith("MARKDOWN-map_city"))
    assert "legenda" in note["meta"]["code"] and "IBGE" in note["meta"]["code"]


def test_calculated_columns_compute_what_their_names_say():
    shifts = _expressions("party_shifts")
    rows = [("Município", 1, "SOBRAL", "CE", 24, 616, 0.2, 0.3, 0.1, 2022, 2026),
            ("UF", 1, None, "CE", None, None, 0.5, 0.4, -0.1, 2018, 2022),
            ("Zona", 1, None, "CE", 24, None, 0.5, 0.4, -0.1, 2018, 2022),
            ("Seção", 11, "SOBRAL", "CE", 24, 616, 0.5, 0.4, -0.1, 2020, 2024)]
    selects = ", ".join(f"{e} AS {n}" for n, e in shifts.items())
    got = duckdb.sql(f"""SELECT {selects} FROM (VALUES {', '.join(str(r).replace('None', 'NULL') for r in rows)}) t(level, round_number,
        municipality_name, state, zone, section, share_from, share_to, share_change, from_year, to_year)
        """).fetchall()
    names = list(shifts)
    result = [dict(zip(names, r)) for r in got]
    assert [r["unit_label"] for r in result] == [
        "SOBRAL (CE)", "CE", "CE · zona 24", "SOBRAL (CE) · zona 24 · seção 616"]
    assert result[0]["label"] == "SOBRAL (CE)"
    assert result[0]["transition_label"] == "2022 → 2026 · votação principal (T1)"
    assert round(result[0]["share_change_pp"], 9) == 10 and round(result[0]["share_to_pp"], 9) == 30
    assert round(result[1]["drop_pp"], 9) == 10 and round(result[1]["abs_share_change_pp"], 9) == 10
    volatility = _expressions("unit_volatility")
    assert volatility["unit_label"] == shifts["unit_label"]
    assert volatility["transition_label"] == shifts["transition_label"]
    row = duckdb.sql(f"SELECT {volatility['pedersen_pp']} FROM (VALUES (0.25)) t(pedersen_index)").fetchone()
    assert row[0] == 25


def test_latest_transition_sorts_first_descending_with_the_main_vote_before_the_reversal():
    labels = ["2022 → 2026 · reversão (T2)", "2022 → 2026 · votação principal (T1)",
              "2018 → 2022 · votação principal (T1)", "2018 → 2022 · reversão (T2)"]
    assert sorted(labels, reverse=True)[0] == "2022 → 2026 · votação principal (T1)"
    expression = _expressions("party_shifts")["transition_label"]
    t1, t2 = (duckdb.sql(f"SELECT {expression} FROM (VALUES (2022, 2026, {n})) t(from_year, to_year, round_number)")
              .fetchone()[0] for n in (1, 2))
    assert (t1, t2) == (labels[1], labels[0])


def test_chart_and_filter_columns_exist_in_their_datasets():
    datasets = _datasets()
    for chart in _all_charts().values():
        params = chart["params"]
        columns = {c["column_name"] for c in datasets[chart["dataset_uuid"]]["columns"]}
        used = set(params.get("groupby") or []) | set(params.get("all_columns") or [])
        used |= {params[k] for k in ("x_axis", "entity", "column") if params.get(k)}
        used |= {f["subject"] for f in params.get("adhoc_filters", []) if f["expressionType"] == "SIMPLE"}
        for m in params.get("metrics") or [params.get("metric")]:
            if isinstance(m, dict) and m["expressionType"] == "SIMPLE":
                used.add(m["column"]["column_name"])
        assert used <= columns, (chart["slice_name"], used - columns)
    for name, flt in _filters().items():
        column = flt["targets"][0]["column"]["name"]
        assert _scoped(flt), f"{name} applies to no chart"
        for chart_id in _scoped(flt):
            chart = _all_charts()[chart_id]
            columns = {c["column_name"] for c in datasets[chart["dataset_uuid"]]["columns"]}
            assert column in columns, (name, chart["slice_name"])


def test_office_and_transition_are_required_single_selects_and_transition_cascades_from_office():
    filters = _filters()
    office, transition = filters["Cargo"], filters["Transição"]
    for flt in (office, transition, filters["Partido"]):
        assert flt["controlValues"]["enableEmptyFilter"] and not flt["controlValues"]["multiSelect"]
    assert office["defaultDataMask"]["filterState"]["value"] == ["Presidente"]
    assert transition["cascadeParentIds"] == [office["id"]]
    assert not transition["controlValues"]["defaultToFirstItem"]
    assert transition["defaultDataMask"]["filterState"]["value"] == ["2022 \u2192 2026 \xb7 vota\xe7\xe3o principal (T1)"]
    assert set(filters["Partido"]["cascadeParentIds"]) == {office["id"], transition["id"]}
    for flt in (office, transition, filters["Partido"], filters["UF"], filters["Município"]):
        assert flt["adhoc_filters"][0]["subject"] == "level"  # cheap option queries on the 53M-row table


def test_filters_reach_exactly_the_charts_where_they_mean_something():
    filters = _filters()
    everything = {c["slice_name"] for c in _all_charts().values()} - {
        "Municípios por candidato mais votado", "Seções por candidato mais votado"}
    assert _names(_scoped(filters["Cargo"])) - {"Municípios por candidato mais votado",
                                                "Seções por candidato mais votado"} == everything
    assert _scoped(filters["Cargo"]) == _scoped(filters["Transição"]) == _scoped(filters["UF"])
    volatility = {c["slice_name"] for c in _tab_charts()["Volatilidade"].values()} | {"Volatilidade por UF (mapa)"}
    assert not _names(_scoped(filters["Partido"])) & volatility  # unit_volatility has no party
    city = _names(_scoped(filters["Município"]))
    assert city == {"Cidades: maiores altas", "Cidades: maiores quedas", "Cidades: maiores variações (tabela)",
                    "Seções: maiores altas", "Seções: maiores quedas", "Seções: maiores variações (tabela)",
                    "Volatilidade nas cidades (maiores)", "Distribuição da volatilidade nas cidades", "Variação por cidade (mapa)"}
    assert filters["Município"]["cascadeParentIds"] == [filters["UF"]["id"]]
    assert filters["UF"]["controlValues"]["inverseSelection"] and filters["UF"]["controlValues"]["multiSelect"]


def test_notes_explain_the_caveats():
    position = _dashboard()["position"]
    text = " ".join(v["meta"]["code"] for v in position.values()
                    if isinstance(v, dict) and v.get("type") == "MARKDOWN").lower()
    for phrase in ("fusões não são unificadas", "uniÃo".lower(), "não disputou conta 0%", "psl em 2018 → pl",
                   "presidente, governador, senador e prefeito", "82%", "75%", "96%", "97%", "98%",
                   "redesenhados", "0% = mesma distribuição", "100% = nenhum voto ficou no mesmo partido",
                   "reversão", "zz", "mapa por cidade"):
        assert phrase in text, phrase


def test_dashboard_color_scheme_is_empty_so_country_maps_use_the_linear_scheme():
    # a dashboard-level categorical scheme overrides linear_color_scheme and paints each state a random color
    assert _dashboard()["metadata"]["color_scheme"] == ""
