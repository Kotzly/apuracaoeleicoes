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


def test_dashboard_has_two_tabs_each_with_histogram_gains_drops_and_table():
    dashboard = _dashboard()
    position = dashboard["position"]
    tabs = [position[t]["meta"]["text"] for t in position["TABS-main"]["children"]]
    assert tabs == ["Em porcentagem", "Em números absolutos"]
    for tab_id, column in (("TAB-pct", "share_change_pp"), ("TAB-abs", "votes_change")):
        charts = [position[c]["meta"]["sliceName"] for row in position[tab_id]["children"]
                  for c in position[row]["children"]]
        assert len(charts) == 4 and any("quedas" in n for n in charts) and any("altas" in n for n in charts)
    assert "Como ler" in position["MARKDOWN-note"]["meta"]["code"]


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
