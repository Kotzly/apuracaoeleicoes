"""The votes dataset's `party_current` column must agree with data/party_renames.csv."""

from pathlib import Path

import duckdb
import yaml

from election_tally import parties

DASHBOARDS = Path(__file__).parent.parent / "docker" / "superset" / "dashboards"


def _load(relative: str) -> dict:
    return yaml.safe_load((DASHBOARDS / relative).read_text(encoding="utf-8"))


def _party_current() -> dict:
    dataset = _load("datasets/Eleicoes_DuckDB/votes_2.yaml")
    return next(c for c in dataset["columns"] if c["column_name"] == "party_current")


def test_yaml_case_is_the_one_generated_from_the_csv():
    assert _party_current()["expression"] == parties.canonical_party_sql("party")


def test_yaml_case_renames_exactly_what_the_csv_renames():
    final = parties.resolve(parties.read_renames())
    kept = ["DEM", "PSL", "UNIÃO", "PT", "MDB", "PCDOB", "PATRIOTA", "P17", "PCA1"]
    names = list(final) + kept
    values = ", ".join(f"('{n}')" for n in names)
    rows = duckdb.sql(f"""SELECT party, {_party_current()['expression']} FROM (VALUES {values}, (NULL))
                          t(party)""").fetchall()
    assert dict(rows) == {**final, **{n: n for n in kept}, None: None}


def _votes_expression(column):
    dataset = _load("datasets/Eleicoes_DuckDB/votes_2.yaml")
    return next(c["expression"] for c in dataset["columns"] if c["column_name"] == column)


def test_party_charts_show_blank_and_null_votes_and_leave_out_office_25():
    for relative in ("charts/Votos_por_partido_10.yaml", "charts/Votos_acumulados_por_partido_11.yaml"):
        params = _load(relative)["params"]
        assert "party_or_blank" in (params.get("groupby") or []) + [params.get("x_axis")]
        where = " ".join(f["sqlExpression"] for f in params["adhoc_filters"] if "sqlExpression" in f)
        assert "vote_type IN ('Branco', 'Nulo')" in where and "office_code <> 25" in where
    candidates = _load("charts/Evolucao_da_apuracao_votos_acumulados_3.yaml")["params"]["adhoc_filters"]
    assert next(f for f in candidates if f["subject"] == "vote_type")["comparator"] == ["Nominal", "Branco", "Nulo"]


def test_party_bars_are_horizontal_and_stack_candidate_and_party_votes():
    params = _load("charts/Votos_por_partido_10.yaml")["params"]
    assert params["orientation"] == "horizontal" and params["stack"] == "Stack"
    assert params["show_legend"] and not params["groupby"]
    assert [m["label"] for m in params["metrics"]] == ["Candidato (ou branco/nulo)", "Legenda"]
    assert params["x_axis_sort"] == params["metrics"][0]["label"]  # a dimension would leave the bars unsorted


def test_vote_columns_name_each_kind_and_keep_party_names_current():
    rows = duckdb.sql(f"""SELECT {_votes_expression('party_or_blank')}, {_votes_expression('vote_kind')}
        FROM (VALUES ('Nominal', 'PMDB'), ('Legenda', 'PT'), ('Branco', NULL), ('Nulo', NULL)) t(vote_type, party)""").fetchall()
    assert rows == [("MDB", "Candidato"), ("PT", "Legenda"), ("Branco", "Branco"), ("Nulo", "Nulo")]


def test_dashboard_has_two_tabs_and_the_filters_to_scan_little():
    dashboard = _load("dashboards/Evolucao_da_apuracao_1.yaml")
    position = dashboard["position"]
    tabs = [position[t]["meta"]["text"] for t in position["TABS-main"]["children"]]
    assert tabs == ["Por candidato", "Por partido"]
    filters = {f["name"]: f for f in dashboard["metadata"]["native_filter_configuration"]}
    assert {"Ano", "Turno", "Cargo", "UF", "Município", "Zona", "Seção", "Recebimento do BU"} <= set(filters)
    # explicit default masks: the charts load already filtered instead of scanning every row
    defaults = {"Ano": ("election_year", 2026), "Turno": ("round_number", 1), "Cargo": ("office_name", "Presidente")}
    for name, (column, value) in defaults.items():
        assert filters[name]["controlValues"]["enableEmptyFilter"]
        assert not filters[name]["controlValues"]["defaultToFirstItem"]
        mask = filters[name]["defaultDataMask"]
        assert mask["filterState"]["value"] == [value]
        assert mask["extraFormData"]["filters"] == [{"col": column, "op": "IN", "val": [value]}]
    assert filters["Município"]["cascadeParentIds"] == [filters["UF"]["id"]]
    # a dashboard-level categorical scheme would paint each state of the turnout map a random color
    assert dashboard["metadata"]["color_scheme"] == ""
