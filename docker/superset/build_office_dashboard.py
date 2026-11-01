#!/usr/bin/env python3
"""Generates the "Cargos" dashboard: one tab per office, from the specs in office_specs/.

    python docker/superset/build_office_dashboard.py                        # writes the YAML, ignored by git
    python docker/superset/build_office_dashboard.py --assets /tmp/copy     # ...into a copy of dashboards/
    python docker/superset/build_office_dashboard.py --check --office governador
    python docker/superset/build_office_dashboard.py --check --office governador --db elections.duckdb

bootstrap.sh runs this on every start, on a copy of dashboards/, because that folder is mounted read-only
and the output is not versioned. export-dashboards.sh leaves the output out (see generated_uuids).

The specs are the source of truth: a change to one of these charts made in the interface and exported
is overwritten the next time this runs, unless it also goes into the spec. `--check` writes nothing;
with `--db` it also runs each chart's columns, metrics and filters on the data, and an empty result fails.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import re
import sys
import uuid
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from office_specs.common import Chart, Note, Office  # noqa: E402
from office_specs.templates import TEMPLATES  # noqa: E402

ASSETS = HERE / "dashboards"
CHARTS = ASSETS / "charts"
DATASETS = ASSETS / "datasets" / "Eleicoes_DuckDB"
DASHBOARD_FILE = ASSETS / "dashboards" / "Cargos_8.yaml"
NAMESPACE = uuid.UUID("3f3a1d0e-6f1a-5b7c-8a43-1f9d7c2e4b60")
GENERATED_PREFIX = "Cargos__"
OFFICES = ("presidente", "governador", "senador", "deputado_federal", "deputado_estadual", "deputado_distrital",
           "prefeito", "vereador")
FEDERAL, MUNICIPAL = (1, 3, 5, 6, 7, 8), (11, 13)
# Tables whose `state` or `label` is empty for these offices, so the filter would blank the chart.
_NATIONAL_ROWS = {"candidate_results", "candidates", "elected_party", "office_seats"}
_CITY_LEVEL_ONLY = {"candidate_results", "office_seats", "unit_winners"}
BLANK_NULL = "7b1fb97d-cf0e-5804-a2b4-d533ebc3146e"
ELECTED_PARTY = "b92a5725-3a7e-425c-a7d4-c3352c23b05b"
CANDIDATE_RESULTS = "806e47fb-121a-5d49-85e6-05e638197cc5"
PARTY_SHIFTS = "bbdc79ef-c388-417b-bdef-d3cf4e4665d2"


def _sql_filter(sql: str) -> dict:
    return {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": sql}


# key, name, column, dataset for the options, offices, default, subtabs it stays out of or is limited to.
FILTERS = [
    dict(key="ano", name="Ano", column="election_year", dataset=BLANK_NULL, offices=FEDERAL, default=2026,
         options=f"office_code IN {FEDERAL}"),
    dict(key="ano_municipal", ignore_as="ano", name="Ano (municipal)", column="election_year", dataset=BLANK_NULL, offices=MUNICIPAL,
         default=2024, options=f"office_code IN {MUNICIPAL}"),
    dict(key="transicao", name="Transição", column="transition_label", dataset=PARTY_SHIFTS, offices=FEDERAL,
         default="2022 → 2026 · votação principal (T1)", options=f"level = 'Brasil' AND office_code IN {FEDERAL}"),
    dict(key="transicao_municipal", ignore_as="transicao", name="Transição (municipal)", column="transition_label", dataset=PARTY_SHIFTS,
         offices=MUNICIPAL, default="2020 → 2024 · votação principal (T1)", options=f"level = 'Brasil' AND office_code IN {MUNICIPAL}"),
    dict(key="turno", name="Turno", column="round_number", dataset=BLANK_NULL, offices=(1, 3, 11), default=1,
         only_subtabs=("Resultado", "Território", "Candidatos")),
    dict(key="base", name="Base do percentual", column="vote_base", dataset=PARTY_SHIFTS, offices=FEDERAL + MUNICIPAL,
         default="Votos válidos"),
    dict(key="uf", name="UF", column="state", dataset=BLANK_NULL, offices=(1, 3, 5, 6, 7, 11, 13)),
    dict(key="municipio", name="Município", column="label", dataset=BLANK_NULL, offices=FEDERAL[:-1] + MUNICIPAL, parent="uf"),
    dict(key="partido", name="Partido", column="party_current", dataset=ELECTED_PARTY, offices=FEDERAL + MUNICIPAL),
    dict(key="partido_mudanca", ignore_as="partido", name="Partido (variação)", column="party", dataset=PARTY_SHIFTS,
         offices=FEDERAL + MUNICIPAL, only_subtabs=("Mudança",), options="level = 'Brasil'"),
    dict(key="candidato", name="Candidato", column="candidate_name", dataset=CANDIDATE_RESULTS, offices=(1, 3, 11),
         only_subtabs=("Entre turnos",), options="office_code IN (1, 3, 11) AND round_number = 2"),
]


def set_assets(path: Path) -> None:
    global ASSETS, CHARTS, DATASETS, DASHBOARD_FILE
    ASSETS, CHARTS = path, path / "charts"
    DATASETS = path / "datasets" / "Eleicoes_DuckDB"
    DASHBOARD_FILE = path / "dashboards" / DASHBOARD_FILE.name


def dashboard_uuid() -> str:
    return str(uuid.uuid5(NAMESPACE, "dashboard"))


def generated_uuids() -> set:
    """What this script produces, by uuid: the dashboard and the charts it does not just reuse."""
    _, charts, _ = build_tabs([load_office(m) for m in OFFICES], load_templates())
    return {dashboard_uuid()} | {uid for uid, (_, _, reused) in charts.items() if not reused}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_templates() -> dict:
    """Name in the specs -> chart. By uuid, because an export changes the names of the files."""
    by_uuid = {chart["uuid"]: chart for chart in map(load_yaml, CHARTS.glob("*.yaml"))}
    return {name: by_uuid[uid] for name, uid in TEMPLATES.items() if uid in by_uuid}


def load_datasets() -> dict:
    return {d["uuid"]: d for d in map(load_yaml, DATASETS.glob("*.yaml"))}


def load_office(module: str) -> Office:
    return importlib.import_module(f"office_specs.{module}").OFFICE


def _office_filter(code: int) -> dict:
    return {"clause": "WHERE", "expressionType": "SIMPLE", "subject": "office_code", "operator": "==",
            "operatorId": "EQUALS", "comparator": code}


def _mentions_office(item: dict) -> bool:
    return item.get("subject") == "office_code" or "office_code" in (item.get("sqlExpression") or "")


def _rename(value, names: dict):
    if isinstance(value, str):
        return names.get(value, value)
    if isinstance(value, list):
        return [_rename(v, names) for v in value]
    if isinstance(value, dict):
        return {k: (_rename(v, names) if k in ("x_axis", "column_name", "subject", "column", "groupby", "all_columns") else
                    _rename(v, names) if isinstance(v, (list, dict)) else v) for k, v in value.items()}
    return value


def build_chart(spec: Chart, office: Office, templates: dict) -> dict:
    """The chart's YAML. A reused one is the template itself."""
    if spec.template:
        if spec.template not in templates:
            raise ValueError(f"{office.name}/{spec.key}: no chart for the template {spec.template}")
        template = templates[spec.template]
        if spec.reused:
            return template
        params = copy.deepcopy(template["params"])
        viz, dataset = template["viz_type"], spec.dataset or template["dataset_uuid"]
        title = spec.title or template["slice_name"]
        description = spec.description or template.get("description")
        if spec.rename:
            params = _rename(params, spec.rename)
    else:
        params, viz, dataset, title, description = {}, spec.viz, spec.dataset, spec.title, spec.description
    params.update(copy.deepcopy(spec.params))
    filters = [f for f in params.get("adhoc_filters") or [] if not _mentions_office(f)]
    if spec.pinned:
        filters.insert(0, _office_filter(office.code))
    params["adhoc_filters"] = filters + copy.deepcopy(spec.filters)
    params["viz_type"], params["datasource"] = viz, "0__table"
    return dict(slice_name=f"{office.name} · {title}", description=description, certified_by=None,
                certification_details=None, viz_type=viz, params=params, query_context=None, cache_timeout=None,
                uuid=str(uuid.uuid5(NAMESPACE, f"{office.name}/{spec.key}")), version="1.0.0", dataset_uuid=dataset)


def _node(position: dict, node_id: str, kind: str, parents: list, meta=None) -> dict:
    node = {"type": kind, "id": node_id, "children": [], "parents": parents}
    if meta is not None:
        node["meta"] = meta
    position[node_id] = node
    return node


def build_tabs(offices: list[Office], templates: dict):
    """The layout, the charts to write and, per chart id, what the filters need to know."""
    position = {"DASHBOARD_VERSION_KEY": "v2",
                "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
                "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": ["TABS-main"], "parents": ["ROOT_ID"]},
                "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": "Cargos"}}}
    main = _node(position, "TABS-main", "TABS", ["ROOT_ID", "GRID_ID"])
    charts, facts = {}, {}
    for office in offices:
        slug = re.sub(r"\W", "", office.name.lower().replace(" ", "_").encode("ascii", "ignore").decode())
        tab_id, tabs_id = f"TAB-{slug}", f"TABS-{slug}"
        parents = ["ROOT_ID", "GRID_ID", "TABS-main"]
        main["children"].append(tab_id)
        tab = _node(position, tab_id, "TAB", parents, {"text": office.name})
        tab["children"].append(tabs_id)
        inner = _node(position, tabs_id, "TABS", parents + [tab_id])
        next_id = office.first_id + 1
        for n, sub in enumerate(office.subtabs):
            sub_id = f"TAB-{slug}-{n}"
            inner["children"].append(sub_id)
            sub_parents = parents + [tab_id, tabs_id]
            sub_tab = _node(position, sub_id, "TAB", sub_parents, {"text": sub.title})
            for r, items in enumerate(sub.rows):
                row_id = f"ROW-{slug}-{n}-{r}"
                sub_tab["children"].append(row_id)
                row_parents = sub_parents + [sub_id]
                row = _node(position, row_id, "ROW", row_parents, {"background": "BACKGROUND_TRANSPARENT"})
                if sum(i.width for i in items) > 12:
                    raise ValueError(f"{office.name}/{sub.title}: row {r} is wider than 12")
                for item in items:
                    if isinstance(item, Note):
                        item_id = f"MARKDOWN-{slug}-{item.key}"
                        row["children"].append(item_id)
                        _node(position, item_id, "MARKDOWN", row_parents + [row_id],
                              {"width": item.width, "height": item.height, "code": item.text})
                        continue
                    chart = build_chart(item, office, templates)
                    if chart["uuid"] in charts and not item.reused:
                        raise ValueError(f"{office.name}: key {item.key} used twice")
                    chart_id = next_id
                    next_id += 1
                    item_id = f"CHART-{chart_id}"
                    row["children"].append(item_id)
                    _node(position, item_id, "CHART", row_parents + [row_id],
                          {"chartId": chart_id, "width": item.width, "height": item.height, "uuid": chart["uuid"],
                           "sliceName": chart["slice_name"]})
                    charts[chart["uuid"]] = (chart, chart_id, item.reused)
                    facts[chart_id] = dict(office=office.code, subtab=sub.title, spec=item, dataset=chart["dataset_uuid"])
    return position, charts, facts


def _dataset_columns(dataset: dict) -> set:
    return {c["column_name"] for c in dataset["columns"]}


def _in_scope(flt: dict, fact: dict, datasets: dict) -> bool:
    dataset = datasets[fact["dataset"]]
    table = dataset["table_name"]
    spec = fact["spec"]
    if fact["office"] not in flt["offices"] or flt["column"] not in _dataset_columns(dataset):
        return False
    if flt.get("ignore_as", flt["key"]) in spec.ignore:
        return False
    if flt.get("only_subtabs") and fact["subtab"] not in flt["only_subtabs"]:
        return False
    if flt["column"] == "election_year" and spec.history:
        return False
    if flt["column"] == "state" and fact["office"] == 1 and table in _NATIONAL_ROWS:
        return False
    if flt["column"] == "label" and fact["office"] not in MUNICIPAL and table in _CITY_LEVEL_ONLY:
        return False
    return True


def build_filters(facts: dict, datasets: dict) -> list:
    result = []
    for flt in FILTERS:
        excluded = sorted(i for i, fact in facts.items() if not _in_scope(flt, fact, datasets))
        if len(excluded) == len(facts):
            continue  # e.g. no dataset has the column yet
        default = flt.get("default")
        mask = {"extraFormData": {}, "filterState": {}, "ownState": {}}
        if default is not None:
            mask["extraFormData"] = {"filters": [{"col": flt["column"], "op": "IN", "val": [default]}]}
            mask["filterState"] = {"value": [default]}
        item = {"id": f"NATIVE_FILTER-{flt['key']}", "name": flt["name"], "filterType": "filter_select", "type": "NATIVE_FILTER",
                "description": "", "targets": [{"column": {"name": flt["column"]}, "datasetUuid": flt["dataset"]}],
                "controlValues": {"enableEmptyFilter": default is not None, "defaultToFirstItem": False,
                                  "multiSelect": default is None, "searchAllOptions": False, "inverseSelection": False,
                                  "sortAscending": False},
                "defaultDataMask": mask, "cascadeParentIds": [f"NATIVE_FILTER-{flt['parent']}"] if flt.get("parent") else [],
                "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded}}
        if flt.get("options"):
            item["adhoc_filters"] = [_sql_filter(flt["options"])]
        result.append(item)
    return result


def _source(dataset: dict) -> str:
    """The dataset as a subquery with its calculated columns, as Superset would see them."""
    base = f"({dataset['sql']})" if dataset.get("sql") else f"main.{dataset['table_name']}"
    calculated = [f"{c['expression']} AS {c['column_name']}" for c in dataset["columns"] if c.get("expression")]
    return f"(SELECT *{', ' if calculated else ''}{', '.join(calculated)} FROM {base})"


def _expression(item: dict) -> str:
    if item.get("expressionType") == "SQL":
        return item["sqlExpression"]
    if item.get("column"):
        return f"{item['aggregate']}({item['column']['column_name']})"
    comparator = item["comparator"]
    value = f"'{comparator}'" if isinstance(comparator, str) else comparator
    operator = {"==": "="}.get(item["operator"], item["operator"])
    if operator == "TEMPORAL_RANGE":
        return "true"
    return f"{item['subject']} {operator} {value}"


def _referenced_columns(params: dict) -> set:
    names = set(params.get("all_columns") or []) | set(params.get("groupby") or []) | set(params.get("groupbyRows") or [])
    names |= set(params.get("groupbyColumns") or []) | set(params.get("columns") or [])
    for key in ("x_axis", "line_column", "entity"):
        if isinstance(params.get(key), str):
            names.add(params[key])
    for item in params.get("adhoc_filters") or []:
        if item.get("expressionType") == "SIMPLE":
            names.add(item["subject"])
    return names


def check(position: dict, charts: dict, facts: dict, datasets: dict, db: str | None) -> list:
    """Problems found, as text."""
    problems = []
    for chart, chart_id, _ in charts.values():
        name = chart["slice_name"]
        dataset = datasets.get(chart["dataset_uuid"])
        if dataset is None:
            problems.append(f"{name}: unknown dataset {chart['dataset_uuid']}")
            continue
        missing = _referenced_columns(chart["params"]) - _dataset_columns(dataset)
        if missing:
            problems.append(f"{name}: columns not in {dataset['table_name']}: {sorted(missing)}")
    if db and not problems:
        problems += _check_on_data(charts, datasets, db)
    return problems


def _check_on_data(charts: dict, datasets: dict, db: str) -> list:
    import duckdb

    con = duckdb.connect(db, read_only=True)
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")
    problems = []
    for chart, _, _ in charts.values():
        params, name = chart["params"], chart["slice_name"]
        dataset = datasets[chart["dataset_uuid"]]
        metrics = list(params.get("metrics") or [])
        if params.get("metric"):
            metrics.append(params["metric"])
        if params.get("timeseries_limit_metric"):
            metrics.append(params["timeseries_limit_metric"])
        saved = {m["metric_name"]: m["expression"] for m in dataset.get("metrics") or []}
        metrics = [{"expressionType": "SQL", "sqlExpression": saved[m]} if isinstance(m, str) else m for m in metrics]
        keys = [k for k in [params.get("x_axis"), *(params.get("groupby") or []), *(params.get("groupbyRows") or []),
                            *(params.get("groupbyColumns") or [])] if isinstance(k, str)]
        wheres = " AND ".join(f"({_expression(f)})" for f in params.get("adhoc_filters") or [] if f.get("clause") != "HAVING") or "true"
        raw = params.get("all_columns") or []
        if metrics:
            selected = ", ".join(keys + [_expression(m) for m in metrics])
            group = f"GROUP BY {', '.join(map(str, range(1, len(keys) + 1)))}" if keys else ""
        else:
            selected, group = ", ".join(raw) or "*", ""
        sql = f"SELECT {selected} FROM {_source(dataset)} WHERE {wheres} {group} LIMIT 5"
        try:
            if not con.execute(sql).fetchall():
                problems.append(f"{name}: no rows\n    {sql[:300]}")
        except duckdb.Error as error:
            problems.append(f"{name}: {str(error)[:300]}\n    {sql[:300]}")
    con.close()
    return problems


def write(position: dict, charts: dict, filters: list) -> None:
    for path in CHARTS.glob(f"{GENERATED_PREFIX}*.yaml"):
        path.unlink()
    generated = {uid for uid, (_, _, reused) in charts.items() if not reused}
    for path in CHARTS.glob("*.yaml"):  # an export renames generated charts: don't leave two copies
        if load_yaml(path)["uuid"] in generated:
            path.unlink()
    for uid, (chart, chart_id, reused) in charts.items():
        if not reused:
            safe = re.sub(r"\W+", "_", chart["slice_name"].encode("ascii", "ignore").decode()).strip("_")
            (CHARTS / f"{GENERATED_PREFIX}{safe}_{chart_id}.yaml").write_text(
                yaml.safe_dump(chart, sort_keys=False), encoding="utf-8")
    dashboard = dict(dashboard_title="Cargos", description=None, css=None, slug="cargos", certified_by=None,
                     certification_details=None, published=True, uuid=dashboard_uuid(),
                     position=position,
                     metadata=dict(native_filter_configuration=filters, cross_filters_enabled=False,
                                   color_scheme="supersetColors", refresh_frequency=0, timed_refresh_immune_slices=[],
                                   expanded_slices={}, label_colors={}, shared_label_colors=[], chart_configuration={},
                                   map_label_colors={}, color_scheme_domain=[]),
                     version="1.0.0")
    DASHBOARD_FILE.write_text(yaml.safe_dump(dashboard, sort_keys=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only, writing nothing")
    parser.add_argument("--assets", type=Path, help="read and write this copy of dashboards/ instead")
    parser.add_argument("--office", choices=OFFICES, help="only this office (requires --check)")
    parser.add_argument("--db", help="with --check, also run each chart on this DuckDB file, read-only")
    args = parser.parse_args()
    if args.office and not args.check:
        parser.error("--office requires --check: a partial dashboard is never written")
    if args.assets:
        set_assets(args.assets)
    modules = [args.office] if args.office else OFFICES
    offices = [load_office(m) for m in modules]
    templates, datasets = load_templates(), load_datasets()
    position, charts, facts = build_tabs(offices, templates)
    filters = build_filters(facts, datasets)
    problems = check(position, charts, facts, datasets, args.db)
    for problem in problems:
        print("PROBLEM", problem)
    print(f"{len(offices)} offices, {len(charts)} charts ({sum(1 for c in charts.values() if not c[2])} generated), "
          f"{len(filters)} filters, {len(problems)} problems")
    if problems:
        return 1
    if not args.check:
        write(position, charts, filters)
        print(f"written: {DASHBOARD_FILE.name} and the charts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
