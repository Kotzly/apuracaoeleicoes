"""TSE file formats and their mapping to the canonical schema.

The pipeline is one; what changes between years is isolated in a `Format`:
  - where to find the files (package on the portal, file name pattern);
  - how to read the raw file (header, separator, encoding);
  - which raw column each canonical column comes from, and in what format dates come.

For a new year with a different layout, create a subclass that overrides only
what changed and register it with @register(year). Years without a registered
format use the format of the most recent registered year before them.

Layouts checked against real files (AC, 1st round). BU = boletim de urna, the
polling-station ballot report.
  2014  Same TXT layout as 2016, but DT_BU_RECEBIDO has only the date (no time), so
        bu_received_at stays NULL; office names in upper case.
  2016  TXT without header, 31 positional columns (see leia-meArquivo_BU-WEB.pdf),
        without round or vote-type description.
  2018  CSV with header; column "SG_ UF" (with a space); WITHOUT DT_BU_RECEBIDO and
        WITHOUT DT_EMISSAO_BU -> there is no way to rebuild the tally progress.
  2020  CSV; has badly escaped quotes in some names. 2022 has the same layout.
  2024  CSV with unquoted numbers and ISO dates (YYYY-MM-DD hh:mm:ss). 2026 has the same layout.

Time zones: opening/closing/issuing always come in the ballot box's local time.
The receipt comes in Brasília time from 2020 on, but in local time in 2016. Everything is stored in Brasília time.
"""

from __future__ import annotations

import re
from pathlib import Path

from .domain import OFFICES as DISPLAY_OFFICES
from .domain import sql_string

# Canonical schema: column -> type. Every load produces exactly these columns;
# what a year's file lacks becomes NULL.
CANONICAL: dict[str, str] = {
    "election_year": "SMALLINT",
    "round_number": "TINYINT",
    "election_code": "INTEGER",
    "state": "VARCHAR",            # 'ZZ' = abroad
    "municipality_code": "INTEGER",
    "municipality_name": "VARCHAR",
    "zone": "INTEGER",             # zone is unique only within the state: use (state, zone)
    "section": "INTEGER",
    "office_code": "SMALLINT",
    "office_name": "VARCHAR",
    "vote_type": "VARCHAR",        # Nominal, Legenda, Branco, Nulo
    "candidate_number": "INTEGER",
    "candidate_name": "VARCHAR",
    "party": "VARCHAR",
    "vote_count": "INTEGER",
    "eligible_voters": "INTEGER",
    "turnout": "INTEGER",
    "abstentions": "INTEGER",
    "opened_at": "TIMESTAMP",
    "closed_at": "TIMESTAMP",
    "bu_issued_at": "TIMESTAMP",
    "bu_received_at": "TIMESTAMP",
}

# Time zone of each UF, to bring the ballot box's local times to Brasília time.
# Simplification: uses the capital's time zone (e.g. western AM and Fernando de
# Noronha get the UF's main time zone). 'ZZ' (abroad) is not converted.
STATE_TIMEZONES = {
    "AC": "America/Rio_Branco", "AM": "America/Manaus", "RR": "America/Boa_Vista",
    "RO": "America/Porto_Velho", "MT": "America/Cuiaba", "MS": "America/Campo_Grande",
    "PA": "America/Belem", "AP": "America/Belem", "TO": "America/Araguaina",
    "MA": "America/Fortaleza", "PI": "America/Fortaleza", "CE": "America/Fortaleza",
    "RN": "America/Fortaleza", "PB": "America/Fortaleza", "PE": "America/Recife",
    "AL": "America/Maceio", "SE": "America/Maceio", "BA": "America/Bahia",
    "MG": "America/Sao_Paulo", "ES": "America/Sao_Paulo", "RJ": "America/Sao_Paulo",
    "SP": "America/Sao_Paulo", "PR": "America/Sao_Paulo", "SC": "America/Sao_Paulo",
    "RS": "America/Sao_Paulo", "GO": "America/Sao_Paulo", "DF": "America/Sao_Paulo",
}
BRASILIA_TZ = "America/Sao_Paulo"

# Missing-value markers used by the TSE.
NULL_MARKERS = ("", "#NULO#", "#NE#")

# Office code -> canonical name. Names in the files are matched case-insensitively
# (2014 writes "PRESIDENTE"); sections that were not installed come with the code but
# no name, and get it from here.
OFFICES = {code: name.title() for code, name in DISPLAY_OFFICES.items()}
OFFICE_NAMES = list(OFFICES.values())

_REGISTRY: dict[int, type[Format]] = {}


def register(*years: int):
    def deco(cls):
        for y in years:
            _REGISTRY[y] = cls
        return cls
    return deco


def format_for(year: int) -> Format:
    if year in _REGISTRY:
        return _REGISTRY[year](year)
    earlier = [y for y in _REGISTRY if y < year]
    if not earlier:
        raise SystemExit(f"No format registered for {year} or earlier. "
                         f"Supported years: {sorted(_REGISTRY)}")
    base = max(earlier)
    print(f"warning: {year} has no format of its own; using the one for {base}. "
          "Run `python -m election_tally inspect` on a file to check it.")
    return _REGISTRY[base](year)


class Format:
    """Base: ';'-delimited text files inside zips, one per state and round, on the TSE CKAN portal."""

    # Discovery
    package = "resultados-{year}-boletim-de-urna"
    file_pattern = re.compile(r"bweb_(?P<round>\d)t_(?P<state>[A-Z]{2})_\d+\.zip$", re.I)
    data_extension = ".csv"

    # Reading
    delim = ";"
    encoding = "latin-1"
    column_names: list[str] | None = None   # for files without a header: name of each position

    # Mapping: canonical column -> candidate raw columns (the first one present wins)
    mapping: dict[str, list[str]] = {}
    # Without these the load fails; the others become NULL if absent.
    required = ("round_number", "state", "municipality_code", "zone", "section",
                "office_code", "candidate_number", "vote_count")
    date_formats = ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    # Canonical columns that the file brings in the ballot box's local time. On load
    # they are converted to Brasília time, so the national curve uses a single clock.
    # The others already come in Brasília time (server time).
    local_time_columns: tuple[str, ...] = ("opened_at", "closed_at", "bu_issued_at")

    def __init__(self, year: int):
        self.year = year

    def package_name(self) -> str:
        return self.package.format(year=self.year)

    def source_sql(self, files: list[Path]) -> str:
        items = ", ".join(sql_string(a.as_posix()) for a in files)
        # strict_mode=false: the TSE sometimes publishes inner quotes without escaping
        # (e.g. ""NICKNAME""); strict mode would reject the whole line.
        options = (f"delim='{self.delim}', quote='\"', escape='\"', all_varchar=true, "
                   f"encoding='{self.encoding}', strict_mode=false")
        if self.column_names:
            names = ", ".join(f"'{n}'" for n in self.column_names)
            options += f", header=false, names=[{names}]"
        else:
            options += ", header=true, union_by_name=true"
        return f"read_csv([{items}], {options})"

    def header_lines(self) -> int:
        return 0 if self.column_names else 1

    def constants(self, files: list[Path]) -> dict[str, str]:
        """Values for canonical columns missing from the file, taken from the context."""
        const = {"election_year": str(self.year)}
        m = self.file_pattern.search(files[0].name.rsplit(".", 1)[0] + ".zip")
        if m:
            const["round_number"] = m["round"]
        return const

    def resolve(self, present: set[str]) -> dict[str, str | None]:
        """Canonical column -> raw column used (or None if absent)."""
        return {c: next((b for b in self.mapping.get(c, []) if b in present), None)
                for c in CANONICAL}

    @staticmethod
    def _quote(column: str) -> str:
        return '"' + column.replace('"', '""') + '"'

    def expression(self, canonical: str, raw: str | None, constant: str | None) -> str:
        """SQL expression that produces the canonical column from the raw one."""
        sql_type = CANONICAL[canonical]
        if raw is None:
            return f"'{constant}'::{sql_type}" if constant is not None else f"NULL::{sql_type}"
        markers = ", ".join(f"'{n}'" for n in NULL_MARKERS)
        value = f"CASE WHEN trim({self._quote(raw)}) IN ({markers}) THEN NULL " \
                f"ELSE trim({self._quote(raw)}) END"
        if sql_type == "TIMESTAMP":
            attempts = ", ".join(f"try_strptime({value}, '{f}')" for f in self.date_formats)
            return f"coalesce({attempts})"
        if canonical == "office_name":
            # Same spelling in every year (2014 writes "PRESIDENTE", later years "Presidente").
            cases = " ".join(f"WHEN '{o.upper()}' THEN '{o}'" for o in OFFICE_NAMES)
            return f"CASE upper({value}) {cases} ELSE {value} END"
        if sql_type == "VARCHAR":
            return value
        expr = f"TRY_CAST({value} AS {sql_type})"
        return f"coalesce({expr}, '{constant}'::{sql_type})" if constant is not None else expr

    def select_sql(self, source: str, present: set[str], files: list[Path],
                   offices: list[int] | None = None) -> tuple[str, list[str]]:
        """SELECT into the canonical schema, and the list of canonical columns that ended up NULL."""
        res = self.resolve(present)
        const = self.constants(files)
        missing = [c for c in self.required if res[c] is None and c not in const]
        if missing:
            raise ValueError(
                f"{self.year}: required columns with no match in the file: {missing}. "
                "Run `python -m election_tally inspect` and adjust the Format for this year.")
        cols = ",\n  ".join(f"{self.expression(c, b, const.get(c))} AS {c}"
                            for c, b in res.items())
        filter_sql = ""
        if offices:
            filter_sql = (f"WHERE TRY_CAST({self._quote(res['office_code'])} AS INTEGER) IN "
                          f"({','.join(map(str, offices))})")
        null_cols = [c for c, b in res.items() if b is None and c not in const]
        sql = f"SELECT\n  {cols}\nFROM {source}\n{filter_sql}"
        local = [c for c in self.local_time_columns if res[c] is not None]
        if local:
            tz_case = "CASE state " + " ".join(f"WHEN '{uf}' THEN '{tz}'"
                                               for uf, tz in STATE_TIMEZONES.items()) + " END"
            replacement = ", ".join(
                f"CASE WHEN {tz_case} IS NULL THEN {c} ELSE "
                f"timezone('{BRASILIA_TZ}', timezone({tz_case}, {c})) END AS {c}"
                for c in local)
            sql = f"SELECT * REPLACE ({replacement}) FROM (\n{sql}\n)"
        return sql, null_cols


@register(2018, 2020, 2022, 2024, 2026)
class BwebCSV(Format):
    """Ballot report on the web (bweb), CSV with header, published since 2018."""

    mapping = {
        "election_year": ["ANO_ELEICAO"],
        "round_number": ["NR_TURNO"],
        "election_code": ["CD_ELEICAO"],
        "state": ["SG_UF", "SG_ UF"],            # 2018 has a space in the name
        "municipality_code": ["CD_MUNICIPIO"],
        "municipality_name": ["NM_MUNICIPIO"],
        "zone": ["NR_ZONA"],
        "section": ["NR_SECAO"],
        "office_code": ["CD_CARGO_PERGUNTA", "CD_CARGO"],
        "office_name": ["DS_CARGO_PERGUNTA", "DS_CARGO"],
        "vote_type": ["DS_TIPO_VOTAVEL"],
        "candidate_number": ["NR_VOTAVEL"],
        "candidate_name": ["NM_VOTAVEL"],
        "party": ["SG_PARTIDO"],
        "vote_count": ["QT_VOTOS"],
        "eligible_voters": ["QT_APTOS"],
        "turnout": ["QT_COMPARECIMENTO"],
        "abstentions": ["QT_ABSTENCOES"],
        "opened_at": ["DT_ABERTURA"],
        "closed_at": ["DT_ENCERRAMENTO"],
        "bu_issued_at": ["DT_EMISSAO_BU"],
        "bu_received_at": ["DT_BU_RECEBIDO"],
    }


# Vote-type codes (same in 2014–2026).
VOTE_TYPES = {1: "Nominal", 2: "Branco", 3: "Nulo", 4: "Legenda"}


@register(2016)
class BwebTXT2016(BwebCSV):
    """2016: TXT without header; columns per the "BU na Web" 2015 specification."""

    data_extension = ".txt"
    # In 2016 the BU receipt time also comes in local time (the first BU of AC
    # arrives at 17:05, minutes after the 17:00 local closing).
    local_time_columns = Format.local_time_columns + ("bu_received_at",)
    column_names = [
        "DT_GERACAO", "HH_GERACAO", "CD_PLEITO", "CD_ELEICAO", "SG_UF",
        "CD_CARGO_PERGUNTA", "DS_CARGO_PERGUNTA", "NR_ZONA", "NR_SECAO",
        "NR_LOCAL_VOTACAO", "NR_PARTIDO", "SG_PARTIDO", "CD_MUNICIPIO",
        "NM_MUNICIPIO", "DT_BU_RECEBIDO", "QT_APTOS", "QT_ABSTENCOES",
        "QT_COMPARECIMENTO", "CD_TIPO_ELEICAO", "CD_TIPO_URNA", "DS_TIPO_URNA",
        "NR_VOTAVEL", "NM_VOTAVEL", "QT_VOTOS", "CD_TIPO_VOTAVEL",
        "NR_URNA_EFETIVADA", "CD_CARGA_1_URNA_EFETIVADA", "CD_CARGA_2_URNA_EFETIVADA",
        "DT_CARGA_URNA_EFETIVADA", "CD_FLASHCARD_URNA_EFETIVADA", "DS_CARGO_PERGUNTA_SECAO",
    ]
    mapping = {**BwebCSV.mapping, "vote_type": ["CD_TIPO_VOTAVEL"]}

    def expression(self, canonical, raw, constant):
        if canonical == "vote_type" and raw:
            cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in VOTE_TYPES.items())
            return f"CASE trim({self._quote(raw)}) {cases} END"
        return super().expression(canonical, raw, constant)


@register(2014)
class BwebTXT2014(BwebTXT2016):
    """2014: the 2016 TXT layout, but DT_BU_RECEBIDO holds only the date (e.g. "05/10/2014").

    A date without time can't place a BU on the tally curve, so the receipt time is left
    NULL on purpose: 2014 gives the consolidated totals only.
    """

    mapping = {k: v for k, v in BwebTXT2016.mapping.items() if k != "bu_received_at"}
