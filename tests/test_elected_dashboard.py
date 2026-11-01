"""The "Eleitos e bancadas" dashboard: datasets, charts, tabs and the scope of each native filter."""

from pathlib import Path

import duckdb
import yaml

from election_tally.parties import OFFICES, canonical_party_sql

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"
DATASETS = DASHBOARDS / "datasets" / "Eleicoes_DuckDB"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dashboard() -> dict:
    return _load(DASHBOARDS / "dashboards" / "Eleitos_e_bancadas_5.yaml")


def _datasets() -> dict:
    return {d["uuid"]: d for d in map(_load, DATASETS.glob("*.yaml"))}


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
                meta = position[child]["meta"]
                if position[child]["type"] == "CHART":
                    found[meta["chartId"]] = charts[meta["uuid"]]
        result[position[tab_id]["meta"]["text"]] = found
    return result


def _scoped_chart_ids(flt: dict) -> set:
    everything = {i for tab in _tab_charts().values() for i in tab}
    return everything - set(flt["scope"]["excluded"])


def test_old_dashboard_is_gone_and_its_table_moved_to_the_first_tab_with_the_same_uuid():
    assert not (DASHBOARDS / "dashboards" / "Candidatos_eleitos_3.yaml").exists()
    tabs = _tab_charts()
    assert list(tabs) == ["Eleitos", "Prefeitos", "Bancadas", "Votos × cadeiras"]
    first = tabs["Eleitos"].values()
    table = next(c for c in first if c["viz_type"] == "table")
    assert table["uuid"] == "7ab1d3c5-0e68-4f29-93d7-1b4e6c8f2a50"
    assert _dashboard()["slug"] == "eleitos-e-bancadas"


def test_every_chart_of_a_tab_reads_the_expected_dataset_and_viz():
    datasets = _datasets()
    tabs = _tab_charts()
    tables = {t: {datasets[c["dataset_uuid"]]["table_name"] for c in cs.values()} for t, cs in tabs.items()}
    assert tables == {"Eleitos": {"elected_party", "candidates"}, "Prefeitos": {"mayor_flows"},
                      "Bancadas": {"elected_party"}, "Votos × cadeiras": {"elected_party"}}
    sankey = next(c for c in tabs["Prefeitos"].values() if c["viz_type"] == "sankey_v2")["params"]
    assert (sankey["source"], sankey["target"]) == ("source", "target")
    assert sankey["metric"]["aggregate"] == "SUM" and sankey["metric"]["column"]["column_name"] == "units"
    assert sankey["row_limit"] >= 5000  # ~300 aggregated links nationally, never truncated
    flows = next(c for c in tabs["Prefeitos"].values() if c["viz_type"] == "table")["params"]
    assert {"source_party", "target_party"} <= set(flows["groupby"])


def test_chart_columns_exist_in_their_datasets():
    datasets = _datasets()
    for charts in _tab_charts().values():
        for chart in charts.values():
            params = chart["params"]
            columns = {c["column_name"] for c in datasets[chart["dataset_uuid"]]["columns"]}
            used = set(params.get("groupby") or []) | set(params.get("all_columns") or [])
            used |= {params[k] for k in ("x_axis", "source", "target") if params.get(k)}
            for m in params.get("metrics") or [params.get("metric")]:
                if m and m["expressionType"] == "SIMPLE":
                    used.add(m["column"]["column_name"])
            assert used <= columns, (chart["slice_name"], used - columns)


def test_proportional_charts_are_limited_to_deputies_and_councillors_by_a_chart_filter():
    tabs = _tab_charts()
    for tab in ("Bancadas", "Votos × cadeiras"):
        for chart in tabs[tab].values():
            wheres = [f["sqlExpression"] for f in chart["params"]["adhoc_filters"] if f["expressionType"] == "SQL"]
            assert "office_code IN (6, 7, 8, 13)" in wheres


def test_votes_vs_seats_compares_national_shares_by_contribution_and_never_filters_on_seats():
    tabs = _tab_charts()["Votos × cadeiras"]
    bars = next(c for c in tabs.values() if c["viz_type"].startswith("echarts"))["params"]
    assert bars["contributionMode"] == "column"
    assert [m["column"]["column_name"] for m in bars["metrics"]] == ["votes", "elected"]
    assert {m["aggregate"] for m in bars["metrics"]} == {"SUM"}
    table = next(c for c in tabs.values() if c["viz_type"] == "table")["params"]
    assert len(table["percent_metrics"]) == 2
    for chart in tabs.values():  # dropping zero-seat parties would inflate the vote shares
        wheres = [f["sqlExpression"] for f in chart["params"]["adhoc_filters"] if f["expressionType"] == "SQL"]
        assert "votes > 0 OR elected > 0" in wheres and not any(w.startswith("elected >") for w in wheres)


def test_every_filter_column_exists_in_every_chart_dataset_in_its_scope():
    datasets = _datasets()
    charts = {i: c for tab in _tab_charts().values() for i, c in tab.items()}
    for name, flt in _filters().items():
        column = flt["targets"][0]["column"]["name"]
        scoped = _scoped_chart_ids(flt)
        assert scoped, f"{name} applies to no chart"
        for chart_id in scoped:
            columns = {c["column_name"] for c in datasets[charts[chart_id]["dataset_uuid"]]["columns"]}
            assert column in columns, (name, charts[chart_id]["slice_name"])


def test_filters_reach_exactly_the_tabs_where_their_column_makes_sense():
    tabs = _tab_charts()
    by_tab = {tab: set(cs) for tab, cs in tabs.items()}
    filters = _filters()

    def tabs_of(name):
        scoped = _scoped_chart_ids(filters[name])
        return {t for t, ids in by_tab.items() if ids & scoped}, all(ids <= scoped or not ids & scoped
                                                                      for ids in by_tab.values())

    assert tabs_of("Cargo")[0] == {"Eleitos"}
    assert tabs_of("Ano")[0] == {"Eleitos", "Bancadas"}
    assert tabs_of("Cargo (proporcional)")[0] == {"Bancadas", "Votos × cadeiras"}
    assert tabs_of("Ano da comparação")[0] == {"Votos × cadeiras"}
    assert tabs_of("UF")[0] == set(by_tab)
    assert tabs_of("Município")[0] == tabs_of("Transição")[0] == {"Prefeitos"}
    # Shares need every party: a party filter in that tab would distort them.
    assert "Votos × cadeiras" not in tabs_of("Partido")[0] and "Prefeitos" not in tabs_of("Partido")[0]
    assert _scoped_chart_ids(filters["Local"]) == _scoped_chart_ids(filters["Tipo de eleição"])
    assert len(_scoped_chart_ids(filters["Local"])) == 1  # the candidates table only


def test_year_and_office_pickers_for_one_comparison_are_required_single_selects():
    filters = _filters()
    for name in ("Cargo", "Cargo (proporcional)", "Ano da comparação"):
        control = filters[name]["controlValues"]
        assert control["enableEmptyFilter"] and not control["multiSelect"]
    assert filters["Cargo"]["defaultDataMask"]["filterState"]["value"] == ["Deputado federal"]
    # explicit default mask: defaultToFirstItem leaves the value "pending" behind the Apply button
    assert not filters["Ano da comparação"]["controlValues"]["defaultToFirstItem"]
    assert filters["Ano da comparação"]["defaultDataMask"]["filterState"]["value"] == [2022]
    assert filters["Ano da comparação"]["cascadeParentIds"] == [filters["Cargo (proporcional)"]["id"]]
    assert filters["Cargo (proporcional)"]["adhoc_filters"][0]["comparator"] == [6, 7, 8, 13]


def test_city_filter_cascades_from_state_and_names_the_state_to_tell_homonyms_apart():
    filters = _filters()
    city = filters["Município"]
    assert city["cascadeParentIds"] == [filters["UF"]["id"]] and city["targets"][0]["column"]["name"] == "label"
    mayors = _datasets()[city["targets"][0]["datasetUuid"]]
    expression = next(c["expression"] for c in mayors["columns"] if c["column_name"] == "label")
    row = duckdb.sql(f"""SELECT {expression} FROM (VALUES ('SANTA RITA', 'PB')) t(municipality_name, state)""")
    assert row.fetchone()[0] == "SANTA RITA (PB)"


def test_calculated_columns_align_the_candidates_table_with_elected_party():
    candidates = {c["column_name"]: c["expression"] for c in
                  next(d for d in _datasets().values() if d["table_name"] == "candidates")["columns"]}
    names = ", ".join(f"({code}, '{name.upper()}')" for code, name in OFFICES.items())
    got = duckdb.sql(f"""SELECT {candidates['office_label']}, {candidates['uf']}, {candidates['party_current']}
        FROM (SELECT * FROM (VALUES {names}) o(office_code, office_name),
              (VALUES (NULL, 'PMDB')) s(state, party) WHERE office_code IN (1, 6)) ORDER BY office_code""").fetchall()
    assert got == [("Presidente", "BR", "MDB"), ("Deputado federal", "BR", "MDB")]
    assert " ".join(candidates["party_current"].split()) == canonical_party_sql("party")
    elected = {c["column_name"]: c["expression"] for c in
               next(d for d in _datasets().values() if d["table_name"] == "elected_party")["columns"]}
    assert (elected["office_label"], elected["uf"], elected["party_current"]) == ("office_name", "state", "party")


def test_notes_explain_the_caveats():
    position = _dashboard()["position"]
    text = " ".join(v["meta"]["code"] for v in position.values()
                    if isinstance(v, dict) and v.get("type") == "MARKDOWN")
    for phrase in ("Como ler", "Sankey", "fusões não são unificadas", "Sem eleito", "2012", "Outros",
                   "cidades, não eleitores", "suplementar", "2014, 2018, 2022 e 2026", "2016, 2020 e 2024"):
        assert phrase.lower() in text.lower(), phrase
