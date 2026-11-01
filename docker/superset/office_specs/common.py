"""The vocabulary of the "Cargos" dashboard specs, and the blocks several offices share.

A spec is a module named after the office, with `OFFICE = office("Presidente", 1, 1000, [subtab(...), ...])`.

- `clone(key, "UF_maiores_altas")` copies an existing chart, named by its file without the id, and pins it
  to the office: `office_code` filters of the original go away and `office_code = N` comes in.
- `reuse("Maiores_altas_pct")` places an existing chart that is already about one office, unchanged.
- `bar`, `line`, `table`, `aggregated_table`, `pivot`, `big_number` create a chart, also pinned.
- `history=True` marks a series across years: the year filter stays out of it.
- `ignore=("uf", "partido")` keeps those filters out of a chart (see FILTERS in build_office_dashboard.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Dataset uuids.
BLANK_NULL = "7b1fb97d-cf0e-5804-a2b4-d533ebc3146e"
CANDIDATES = "2c8e5f71-3a94-4d06-8e1b-c7f2a9d04b36"
CANDIDATE_RESULTS = "806e47fb-121a-5d49-85e6-05e638197cc5"
ELECTED_PARTY = "b92a5725-3a7e-425c-a7d4-c3352c23b05b"
MUNICIPALITY_RESULTS = "7a450a29-0b17-5534-b2c0-6527a4863b93"
OFFICE_SEATS = "ea1a05d0-442b-570d-93b1-cc535aab20b1"
RUNOFF_SHIFTS = "74708069-0d64-5a36-8660-da0cb48b46e5"
SENATE_SEATS = "c5a2813a-0a69-552d-92cc-aad1b8aa9be8"
STATE_RESULTS = "2a100bdc-ae38-5a39-9c19-05be0082dbed"
UNIT_WINNERS = "9cdb4b61-6e63-5675-8d23-7a0ebca406ff"

# Offices that come with the office holder's running mates in the ticket table.
_TICKET = {1: (1, 2), 3: (3, 4), 5: (5, 9, 10), 11: (11, 12)}
_RUNOFF_LEVELS = {"Município": ("Cidades", ""), "UF": ("UF", "UF_entre_turnos_"), "Zona": ("Zonas", "Zonas_entre_turnos_")}


@dataclass
class Chart:
    key: str
    title: str | None = None
    template: str | None = None  # file name without the id
    reused: bool = False
    dataset: str | None = None
    viz: str | None = None
    params: dict = field(default_factory=dict)  # for a clone, merged over the template's
    filters: list = field(default_factory=list)  # more WHERE filters
    rename: dict = field(default_factory=dict)  # column -> column, for a clone on another dataset
    pinned: bool = True  # whether office_code = N is added
    history: bool = False
    ignore: tuple = ()
    width: int = 6
    height: int = 50
    description: str | None = None


@dataclass
class Note:
    key: str
    text: str
    width: int = 12
    height: int = 12


@dataclass
class SubTab:
    title: str
    rows: list


@dataclass
class Office:
    name: str
    code: int
    first_id: int
    subtabs: list


def office(name: str, code: int, first_id: int, subtabs: list) -> Office:
    return Office(name, code, first_id, subtabs)


def subtab(title: str, *rows) -> SubTab:
    """Each row is a list of charts and notes; a block function returns a list of rows, which flatten here."""
    flat = []
    for row in rows:
        if isinstance(row, list) and row and isinstance(row[0], list):
            flat.extend(row)
        else:
            flat.append(row if isinstance(row, list) else [row])
    return SubTab(title, flat)


def row(*items) -> list:
    return list(items)


def note(key: str, text: str, width: int = 12, height: int = 12) -> Note:
    return Note(key, text, width, height)


def clone(key: str, template: str, title: str | None = None, **options) -> Chart:
    return Chart(key=key, template=template, title=title, **options)


def reuse(template: str, **options) -> Chart:
    return Chart(key=template, template=template, reused=True, pinned=False, **options)


def metric(sql: str, label: str) -> dict:
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label, "hasCustomLabel": True,
            "optionName": "metric_" + "".join(c for c in label.lower() if c.isalnum())}


def where(sql: str) -> dict:
    return {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": sql}


def equals(column: str, value) -> dict:
    return {"clause": "WHERE", "expressionType": "SIMPLE", "subject": column, "operator": "==",
            "operatorId": "EQUALS", "comparator": value}


_COMMON = {"annotation_layers": [], "color_scheme": "supersetColors", "show_legend": True, "rich_tooltip": True}


def bar(key, title, dataset, x, metrics, groupby=(), horizontal=False, stack=None, row_limit=30, y_format=".0f",
        sort=None, **options) -> Chart:
    params = {**_COMMON, "x_axis": x, "xAxisForceCategorical": True, "metrics": list(metrics), "groupby": list(groupby),
              "orientation": "horizontal" if horizontal else "vertical", "stack": stack, "row_limit": row_limit,
              "show_value": True, "y_axis_format": y_format, "truncate_metric": True, "order_desc": True,
              "x_axis_title": "", "y_axis_title": "", "x_axis_sort": sort or metrics[0]["label"], "x_axis_sort_asc": False}
    return Chart(key=key, title=title, dataset=dataset, viz="echarts_timeseries_bar", params=params, **options)


def line(key, title, dataset, x, metrics, groupby=(), y_format=".1f", row_limit=5000, **options) -> Chart:
    params = {**_COMMON, "x_axis": x, "xAxisForceCategorical": True, "metrics": list(metrics), "groupby": list(groupby),
              "row_limit": row_limit, "markerEnabled": True, "show_value": True, "y_axis_format": y_format,
              "truncate_metric": True, "order_desc": True, "x_axis_title": "", "y_axis_title": ""}
    return Chart(key=key, title=title, dataset=dataset, viz="echarts_timeseries_line", params=params,
                 **{"history": True, **options})


def table(key, title, dataset, columns, order=(), row_limit=5000, **options) -> Chart:
    """Raw rows. `order`: (column, ascending) pairs."""
    params = {"query_mode": "raw", "all_columns": list(columns), "row_limit": row_limit, "server_pagination": True,
              "server_page_length": 50, "include_search": True, "annotation_layers": [],
              "order_by_cols": [f'["{c}", {str(asc).lower()}]' for c, asc in order]}
    return Chart(key=key, title=title, dataset=dataset, viz="table", params=params, **{"width": 12, "height": 70, **options})


def aggregated_table(key, title, dataset, groupby, metrics, order_by=None, row_limit=1000, **options) -> Chart:
    params = {"query_mode": "aggregate", "groupby": list(groupby), "metrics": list(metrics), "row_limit": row_limit,
              "server_pagination": False, "include_search": True, "annotation_layers": [], "order_desc": True,
              "timeseries_limit_metric": order_by or metrics[0]}
    return Chart(key=key, title=title, dataset=dataset, viz="table", params=params, **{"width": 12, "height": 70, **options})


def pivot(key, title, dataset, rows, columns, measure, value_format=".0f", **options) -> Chart:
    params = {"groupbyRows": list(rows), "groupbyColumns": list(columns), "metrics": [measure], "aggregateFunction": "Sum",
              "metricsLayout": "COLUMNS", "row_limit": 50000, "valueFormat": value_format, "annotation_layers": [],
              "transposePivot": False, "combineMetric": False, "colOrder": "key_a_to_z", "rowOrder": "key_a_to_z"}
    return Chart(key=key, title=title, dataset=dataset, viz="pivot_table_v2", params=params, **{"width": 12, "height": 80, **options})


def big_number(key, title, dataset, measure, value_format=".1f", subheader="", **options) -> Chart:
    params = {"metric": measure, "subheader": subheader, "y_axis_format": value_format, "header_font_size": 0.4,
              "subheader_font_size": 0.15, "annotation_layers": []}
    return Chart(key=key, title=title, dataset=dataset, viz="big_number_total", params=params, **{"width": 3, "height": 25, **options})


REELECTION_NOTE = ("Aproximado: o TSE muda o identificador a cada eleição e não há CPF, então o cruzamento é pelo "
                   "nome completo, no mesmo cargo e local.")
VOTES = metric("SUM(votes)", "Votos")
ELECTED = metric("COUNT(*) FILTER (WHERE elected)", "Eleitos")
CANDIDACIES = metric("COUNT(*)", "Candidaturas")


def result_block(code: int) -> list:
    rows = [
        [clone("turnout", "Comparecimento", width=3, height=25), clone("sections", "Secoes", width=3, height=25)],
        [bar("party_votes", "Votos no 1º turno por partido", ELECTED_PARTY, "party_current", [metric("SUM(votes)", "Votos")],
             filters=[where("votes > 0")], row_limit=35, ignore=("partido",)),
         clone("elected_by_party", "Eleitos_por_partido_e_ano", history=True)],
        [table("candidates_table", "Resultado por candidato", CANDIDATE_RESULTS,
               ["election_year", "round_number", "uf", "label", "candidate_name", "party_current", "votes", "pct_of_valid", "result"],
               order=[("votes", False)], filters=[where("votes IS NOT NULL")], ignore=("partido",))],
        [clone("elected_table", "Candidatos_eleitos", width=12, height=70)],
    ]
    if code in _TICKET:
        offices = ", ".join(map(str, _TICKET[code]))
        rows.append([table("tickets", "Chapas", CANDIDATES,
                           ["election_year", "round_number", "state", "electoral_unit", "candidate_number", "office_name",
                            "candidate_name", "party_current", "result"],
                           order=[("election_year", False), ("state", True), ("electoral_unit", True), ("candidate_number", True)],
                           filters=[where(f"office_code IN ({offices})"), where("election_type = 'ELEIÇÃO ORDINÁRIA'")],
                           pinned=False)])
    return rows


def territory_block(maps: bool = True, by_state: bool = True) -> list:
    rows = []
    if maps:
        rows.append([clone("turnout_map", "Comparecimento_por_UF", width=6, height=70)])
    rows.append([clone("blank_by_election", "Brancos_e_nulos_por_eleicao", history=True, width=12)])
    if by_state:
        rows.append([clone("blank_by_state", "Brancos_e_nulos_por_UF_e_ano", history=True, width=12, height=80)])
        rows.append([clone("blank_up", "Brancos_e_nulos_altas_entre_eleicoes"),
                     clone("blank_down", "Brancos_e_nulos_quedas_entre_eleicoes")])
    return rows


def change_block(maps: bool = True, by_state: bool = True, by_city: bool = True) -> list:
    rows = []
    if maps:
        rows.append([clone("shift_map_state", "Variacao_por_UF_mapa", height=70),
                     clone("volatility_map", "Volatilidade_por_UF_mapa", height=70)])
    if by_state:
        rows += [[clone("state_up", "UF_maiores_altas"), clone("state_down", "UF_maiores_quedas")],
                 [clone("state_table", "UF_maiores_variacoes_tabela", width=12), ],
                 [clone("volatility_state", "Volatilidade_por_UF", width=12)]]
    if by_city:
        rows += [[clone("city_up", "Cidades_maiores_altas"), clone("city_down", "Cidades_maiores_quedas")],
                 [clone("city_table", "Cidades_maiores_variacoes_tabela", width=12)],
                 [clone("volatility_city", "Volatilidade_nas_cidades_maiores"),
                  clone("volatility_histogram", "Distribuicao_da_volatilidade_nas_cidades")]]
        if maps:
            rows.append([clone("shift_map_city", "Variacao_por_cidade_mapa", width=12, height=90)])
    rows += [[clone("zone_up", "Zonas_maiores_altas"), clone("zone_down", "Zonas_maiores_quedas")],
             [clone("zone_table", "Zonas_maiores_variacoes_tabela", width=12)]]
    return rows


def _profile(key: str, title: str, column: str, row_limit: int = 15) -> Chart:
    return bar(key, title, CANDIDATE_RESULTS, column,
               [metric("100.0 * COUNT(*) / SUM(COUNT(*)) OVER ()", "% das candidaturas"),
                metric("100.0 * COUNT(*) FILTER (WHERE elected) / NULLIF(SUM(COUNT(*) FILTER (WHERE elected)) OVER (), 0)", "% dos eleitos")],
               horizontal=True, row_limit=row_limit, y_format=".1f", filters=[equals("round_number", 1)], ignore=("turno",))


def candidates_block(seat_competition: bool = True) -> list:
    rows = [
        [bar("top_candidates", "Candidatos mais votados", CANDIDATE_RESULTS, "candidate_name", [VOTES], groupby=("party_current",),
             horizontal=True, stack="Stack", filters=[where("votes IS NOT NULL")], width=12, height=70)],
        [_profile("gender", "Gênero: candidaturas e eleitos", "gender"), _profile("race", "Cor/raça: candidaturas e eleitos", "race")],
        [_profile("education", "Escolaridade: candidaturas e eleitos", "education"),
         _profile("occupation", "Ocupações mais comuns: candidaturas e eleitos", "occupation")],
        [line("women_elected", "Mulheres entre os eleitos", CANDIDATE_RESULTS, "election_year",
              [metric("100.0 * COUNT(*) FILTER (WHERE gender = 'FEMININO') / NULLIF(COUNT(*), 0)", "% de mulheres")],
              filters=[where("elected")], ignore=("turno",))],
    ]
    if seat_competition:
        rows[-1].append(line("candidates_per_seat", "Candidatos por vaga", OFFICE_SEATS, "election_year",
                             [metric("SUM(candidates) * 1.0 / NULLIF(SUM(seats), 0)", "Candidatos por vaga")]))
    return rows


def reelection_line(key: str = "reelection") -> Chart:
    """Of those elected last time who ran again, how many won; and how many of the elected are returning."""
    return line(key, "Reeleição", CANDIDATE_RESULTS, "election_year",
                [metric("100.0 * COUNT(*) FILTER (WHERE elected AND elected_before) / NULLIF(COUNT(*) FILTER (WHERE elected_before), 0)",
                        "% dos que tentaram e venceram"),
                 metric("100.0 * COUNT(*) FILTER (WHERE elected AND elected_before) / NULLIF(COUNT(*) FILTER (WHERE elected), 0)",
                        "% dos eleitos que já tinham o cargo")],
                filters=[where("elected_before IS NOT NULL")], description=REELECTION_NOTE, ignore=("turno",))


def legislature_block(by_state: bool = True, municipal: bool = False) -> list:
    """Benches, votes against seats and the vote structure of a proportional office."""
    rows = [
        [clone("benches", "Bancadas_por_ano", history=True), clone("benches_line", "Evolucao_das_bancadas", history=True)],
        [clone("votes_seats", "Votos_e_cadeiras_por_partido"), clone("votes_seats_table", "Votos_e_cadeiras_tabela")],
    ]
    if by_state:
        rows.append([pivot("bench_by_state", "Bancada por UF e partido", ELECTED_PARTY, ["state"], ["party_current"],
                           metric("SUM(elected)", "Eleitos"), filters=[where("elected > 0")])])
    rows += [
        [line("vote_kinds", "Votos nominais e de legenda", MUNICIPALITY_RESULTS if municipal else STATE_RESULTS, "election_year",
              [metric("100.0 * SUM(votes) FILTER (WHERE vote_type = 'Legenda') / NULLIF(SUM(votes) FILTER (WHERE vote_type IN ('Nominal', 'Legenda')), 0)",
                      "% de legenda")], ignore=("partido",)),
         bar("elected_how", "Eleitos por quociente e por média", CANDIDATE_RESULTS, "election_year",
             [metric("COUNT(*) FILTER (WHERE result = 'ELEITO POR QP')", "Quociente partidário"),
              metric("COUNT(*) FILTER (WHERE result = 'ELEITO POR MÉDIA')", "Média"),
              metric("COUNT(*) FILTER (WHERE result = 'ELEITO')", "Sem detalhe")],
             filters=[where("elected")], stack="Stack", history=True, sort="Quociente partidário")],
        [table("vote_getters", "Puxadores de voto", CANDIDATE_RESULTS,
               ["election_year", "state", "label", "candidate_name", "party_current", "votes", "pct_of_valid", "result", "elected_before"],
               order=[("votes", False)], filters=[where("votes IS NOT NULL")], row_limit=500, height=60)],
        [reelection_line()],
    ]
    return rows


def runoff_block(levels: tuple = ("UF", "Município", "Zona")) -> list:
    """Between-round charts of governor or mayor: the president's ones, moved to `runoff_shifts`."""
    rows = []
    for level in levels:
        city, prefix = _RUNOFF_LEVELS[level]
        rename = {"label": "unit_label"} if level == "Zona" else {}
        filters = [equals("level", level)]
        tag = level.lower().replace("í", "i")
        up, down = (f"{prefix}maiores_altas_pct", f"{prefix}maiores_quedas_pct") if prefix else ("Maiores_altas_pct", "Maiores_quedas_pct")
        rows.append([clone(f"runoff_up_{tag}", up, f"{city}: maiores altas entre turnos (p.p.)", dataset=RUNOFF_SHIFTS,
                           filters=filters, rename=rename),
                     clone(f"runoff_down_{tag}", down, f"{city}: maiores quedas entre turnos (p.p.)", dataset=RUNOFF_SHIFTS,
                           filters=filters, rename=rename)])
    return rows


def flips_table(key: str = "flips") -> Chart:
    return table(key, "Viradas: líder do 1º turno derrotado", UNIT_WINNERS,
                 ["election_year", "state", "label", "round1_leader_name", "round1_leader_party", "winner_name", "party_current",
                  "winner_pct", "margin_pp"],
                 order=[("election_year", False), ("margin_pp", False)], filters=[where("flipped")], height=60)


def round_blank_block() -> list:
    return [[clone("blank_rounds", "Brancos_e_nulos_1o_e_2o_turno", history=True),
             clone("blank_rounds_table", "Brancos_e_nulos_UF_entre_turnos")],
            [clone("blank_rounds_up", "Brancos_e_nulos_altas_entre_turnos"),
             clone("blank_rounds_down", "Brancos_e_nulos_quedas_entre_turnos")]]
