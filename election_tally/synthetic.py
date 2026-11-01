"""A small made-up dataset, for testing the dashboards and `build-derived` without the real one.

Places are real, so the maps work, but only about 70 municipalities take part. Years, candidates,
parties and every vote are invented, and the same seed always gives the same database. The years
stay below 2262 because Superset's pandas cannot represent later timestamps.

Rows go straight into the tables, so this tests neither the download nor the file parsers. The
marker table (`parties.SYNTHETIC_TABLE`) keeps this data and the real data apart: each load refuses
a file holding the other, and `build-derived` does not look for these years at the TSE.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from . import countries, municipalities
from .formats import OFFICES
from .load import COLUMN_TYPES, SECTIONS_COLUMNS, VOTES_COLUMNS, create_schema
from .parties import SYNTHETIC_FEDERAL_YEARS, SYNTHETIC_MUNICIPAL_YEARS, SYNTHETIC_TABLE

DEFAULT_DB = Path("synthetic.duckdb")
DEFAULT_SEED = 7
VERSION = 1

_DISTRICT = "DF"
_ISLAND = "FERNANDO DE NORONHA"  # the only place with district councillors (office 25)
_EXTRA_CITIES = {"SP": 4, "MG": 3, "RJ": 2, "BA": 2, "RS": 2, "PR": 2}  # beyond 2 per state
_ABROAD_CITIES = 5
_SECTIONS_TOTAL = 2000
_MIN_SECTIONS = 3
_SECTIONS_PER_ZONE = 40
_SECTION_TURNOVER = 0.03  # share of sections that close, and of new ones, in each election
_NOT_INSTALLED = 0.002  # sections where nobody voted (office -1)
_RUNOFF_CITIES = 8  # mayors have a second round only in the largest cities
_PARTY_LETTERS = "ABCDEFGHIJKLMNO"
_PARTY_NUMBERS = (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25)
_PRESIDENT, _VICE_PRESIDENT, _GOVERNOR, _VICE_GOVERNOR, _SENATOR = 1, 2, 3, 4, 5
_ALTERNATE_1, _ALTERNATE_2 = 9, 10
_FEDERAL_DEPUTY, _STATE_DEPUTY, _DISTRICT_DEPUTY = 6, 7, 8
_MAYOR, _VICE_MAYOR, _COUNCILLOR = 11, 12, 13
_DISTRICT_COUNCILLOR, _QUESTION = 25, 51
_COUNCILLOR_NAME = "Conselheiro Distrital"
_RUNNING_MATES = {_PRESIDENT: (_VICE_PRESIDENT,), _GOVERNOR: (_VICE_GOVERNOR,),
                  _SENATOR: (_ALTERNATE_1, _ALTERNATE_2), _MAYOR: (_VICE_MAYOR,)}
_MATE_NAMES = {_VICE_PRESIDENT: "VICE-PRESIDENTE", _VICE_GOVERNOR: "VICE-GOVERNADOR",
               _ALTERNATE_1: "1º SUPLENTE", _ALTERNATE_2: "2º SUPLENTE", _VICE_MAYOR: "VICE-PREFEITO"}
_BLANK, _NULL = 95, 96
_ORDINARY, _SUPPLEMENTARY = "ELEIÇÃO ORDINÁRIA", "ELEIÇÃO SUPLEMENTAR"
_QUESTION_TEXT = "Você é a favor da mudança do nome do município?"
_AGE_GROUPS = ("18 a 24 anos", "25 a 34 anos", "35 a 44 anos", "45 a 59 anos", "60 anos ou mais")
_EDUCATION = ("ENSINO FUNDAMENTAL COMPLETO", "ENSINO MÉDIO COMPLETO", "SUPERIOR INCOMPLETO",
              "SUPERIOR COMPLETO")
# Same columns and types as the tables `tse_tables` loads from the TSE.
_RACE_COLUMNS = {"election_year": "INTEGER", "election_code": "INTEGER", "election_name": "VARCHAR",
                 "election_type": "VARCHAR", "country": "VARCHAR", "country_code": "VARCHAR",
                 "state": "VARCHAR", "municipality_code": "INTEGER", "municipality_name": "VARCHAR",
                 "electoral_unit": "VARCHAR", "office_code": "INTEGER", "office_name": "VARCHAR"}
_WITH_ROUND = {"election_year": "INTEGER", "round_number": "INTEGER",
               **{k: v for k, v in _RACE_COLUMNS.items() if k != "election_year"}}
_SCHEMAS = {
    "candidates": {**_WITH_ROUND, "candidate_id": "BIGINT", "candidate_number": "INTEGER",
                   "candidate_name": "VARCHAR", "full_name": "VARCHAR", "party": "VARCHAR",
                   "candidacy_status": "VARCHAR", "result": "VARCHAR", "gender": "VARCHAR",
                   "race": "VARCHAR", "education": "VARCHAR", "occupation": "VARCHAR"},
    "seats": {**_RACE_COLUMNS, "seats": "INTEGER"},
    "coalitions": {**_WITH_ROUND, "group_type": "VARCHAR", "party_number": "INTEGER",
                   "party": "VARCHAR", "party_name": "VARCHAR", "federation": "VARCHAR",
                   "federation_name": "VARCHAR", "coalition_id": "BIGINT",
                   "coalition_name": "VARCHAR", "coalition_parties": "VARCHAR", "status": "VARCHAR"},
    "electorate_profile": {"election_year": "INTEGER", "state": "VARCHAR",
                           "municipality_code": "INTEGER", "municipality_name": "VARCHAR",
                           "gender": "VARCHAR", "age_group": "VARCHAR", "education": "VARCHAR",
                           "voters": "BIGINT"},
}


@dataclass
class City:
    state: str
    code: int
    name: str
    ibge: int | None
    country: str
    country_code: str
    size: float
    first_year: int = 0
    # (zone, section number) of the sections in the first election.
    sections: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Section:
    city: City
    zone: int
    number: int
    eligible: int
    turnout: int
    installed: bool
    received: dt.datetime | None
    opened: dt.datetime
    closed: dt.datetime
    issued: dt.datetime | None


@dataclass
class Candidate:
    number: int
    party: str | None
    weight: float
    name: str = ""
    votes: int = 0
    can_vote: bool = True


@dataclass
class Race:
    """One office in one place: the whole country, a state or a city."""
    office: int
    office_name: str
    state: str | None
    city: City | None
    candidates: list[Candidate]
    seats: int = 1
    proportional: bool = False
    runoff: bool = False
    # In a second round, a finalist's strength per state, so some of them lose votes.
    swing: dict[tuple[int, str], float] = field(default_factory=dict)


def _places(rng: random.Random) -> list[City]:
    with open(str(municipalities.TABLE_PATH), encoding="utf-8", newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: int(r["municipality_code"]))
    by_state: dict[str, list[dict]] = {}
    for row in rows:
        by_state.setdefault(row["state"], []).append(row)
    cities = []
    for state in sorted(by_state):
        pool = by_state[state]
        wanted = 1 if state == _DISTRICT else 2 + _EXTRA_CITIES.get(state, 0)
        chosen = [r for r in pool if r["municipality_name"].upper() == _ISLAND]
        rest = [r for r in pool if r not in chosen]
        chosen += rng.sample(rest, min(wanted, len(rest)))
        for r in chosen:
            cities.append(City(state, int(r["municipality_code"]), r["municipality_name"].upper(),
                               int(r["ibge_code"]), countries.BRAZIL[0], countries.BRAZIL[1],
                               rng.lognormvariate(0, 1)))
    with open(str(countries.TABLE_PATH), encoding="utf-8", newline="") as f:
        abroad = sorted(csv.DictReader(f), key=lambda r: int(r["municipality_code"]))
    for r in rng.sample(abroad, _ABROAD_CITIES):
        cities.append(City(countries.ABROAD_STATE, int(r["municipality_code"]), r["city"], None,
                           r["country"], r["country_code"], 0.2))
    # One city only exists from the second election on, as newly created municipalities do.
    newest = next(c for c in cities if c.state == "SP" and c.name != _ISLAND)
    newest.first_year = min(SYNTHETIC_FEDERAL_YEARS + SYNTHETIC_MUNICIPAL_YEARS) + 2
    _draw_zones(cities)
    return cities


def _draw_zones(cities: list[City]) -> None:
    """Sections per city by size, in zones numbered per state; two small cities share a zone."""
    total = sum(c.size for c in cities)
    for state in sorted({c.state for c in cities}):
        in_state = sorted((c for c in cities if c.state == state), key=lambda c: -c.size)
        zone = 0
        for i, city in enumerate(in_state):
            count = max(_MIN_SECTIONS, round(_SECTIONS_TOTAL * city.size / total))
            shares_zone = len(in_state) > 1 and i == len(in_state) - 1
            for n in range(count):
                if n % _SECTIONS_PER_ZONE == 0 and not shares_zone:
                    zone += 1
                city.sections.append((zone, n + 1 + (5000 if shares_zone else 0)))


def _sunday(year: int, month: int, nth: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def _day_sections(rng: random.Random, year: int, base: dict, day: dt.date, turnout_shift: float,
                  keep_times: bool) -> list[Section]:
    """Every section of one election day, with the map of that year (`base`)."""
    sections = []
    for city, zone, number, eligible in base[year]:
        rate = max(0.45, min(0.95, 0.78 + turnout_shift + rng.gauss(0, 0.04)))
        installed = rng.random() >= _NOT_INSTALLED
        opened = dt.datetime.combine(day, dt.time(8, 0, rng.randrange(60)))
        closed = dt.datetime.combine(day, dt.time(17, rng.randrange(30), rng.randrange(60)))
        issued = closed + dt.timedelta(seconds=rng.randrange(60, 300))
        received = issued + dt.timedelta(seconds=round(rng.expovariate(1 / 3600)))
        sections.append(Section(city, zone, number, eligible,
                                round(eligible * rate) if installed else 0, installed,
                                received if keep_times else None, opened, closed,
                                issued if year != SYNTHETIC_FEDERAL_YEARS[1] else None))
    return sections


def _year_maps(rng: random.Random, cities: list[City], years: list[int]) -> dict:
    """Sections of each year: most stay, a few close and a few new ones open."""
    maps, numbering = {}, {}
    for year in years:
        current = []
        for city in cities:
            if year < city.first_year:
                continue
            for zone, number in city.sections:
                if rng.random() >= _SECTION_TURNOVER:
                    current.append((city, zone, number, rng.randrange(150, 400)))
            for _ in range(max(1, round(len(city.sections) * _SECTION_TURNOVER))):
                zone = city.sections[0][0]
                numbering[city.code] = numbering.get(city.code, 0) + 1
                current.append((city, zone, 7000 + numbering[city.code], rng.randrange(150, 400)))
        maps[year] = current
    return maps


def _parties(rng: random.Random) -> dict[str, tuple[int, float]]:
    return {f"P-{letter}": (number, rng.lognormvariate(0, 0.8))
            for letter, number in zip(_PARTY_LETTERS, _PARTY_NUMBERS)}


def _majority_candidates(rng, parties, count: int, flat: float = 0.0) -> list[Candidate]:
    """One candidate per party, with the party's number. `flat` evens out their chances."""
    chosen = rng.sample(sorted(parties), count)
    return [Candidate(parties[p][0], p, (parties[p][1] + flat) * rng.lognormvariate(0, 0.3),
                      f"{'CANDIDATA' if rng.random() < 0.4 else 'CANDIDATO'} {parties[p][0]}")
            for p in chosen]


def _list_candidates(rng, parties, count: int, digits: int) -> list[Candidate]:
    """Candidates of proportional offices: the party's number and more digits, unique in the race."""
    out, used = [], set()
    names = sorted(parties)
    while len(out) < count:
        party = rng.choice(names)
        number = parties[party][0] * 10 ** (digits - 2) + rng.randrange(1, 10 ** (digits - 2))
        if number in used:
            continue
        used.add(number)
        out.append(Candidate(number, party, parties[party][1] * rng.lognormvariate(0, 0.7),
                             f"{'CANDIDATA' if rng.random() < 0.4 else 'CANDIDATO'} {number}",
                             can_vote=rng.random() >= 0.02))
    return out


def _split(rng: random.Random, total: int, weights: list[float]) -> list[int]:
    """`total` votes shared by noisy weights, always adding up to exactly `total`."""
    if total <= 0 or not weights:
        return [0] * len(weights)
    noisy = [w * rng.gammavariate(4, 0.25) for w in weights]
    scale = total / sum(noisy)
    counts = [int(v * scale) for v in noisy]
    for i in sorted(range(len(noisy)), key=lambda i: counts[i] - noisy[i] * scale)[:total - sum(counts)]:
        counts[i] += 1
    return counts


class _Writer:
    """Rows of one table in a temporary CSV; loading from it is far faster than inserts."""

    def __init__(self, folder: Path, table: str, columns: dict[str, str]):
        self.table, self.columns, self.path = table, columns, folder / f"{table}.csv"
        self.file = open(self.path, "w", encoding="utf-8", newline="")
        self.csv = csv.writer(self.file)

    def add(self, *row) -> None:
        self.csv.writerow(["" if v is None else v for v in row])

    def load(self, con: duckdb.DuckDBPyConnection, create: bool) -> None:
        self.file.close()
        columns = ", ".join(f"'{c}': '{kind}'" for c, kind in self.columns.items())
        select = (f"SELECT * FROM read_csv('{self.path.as_posix()}', header=false, delim=',', "
                  f"quote='\"', escape='\"', auto_detect=false, nullstr='', "
                  f"timestampformat='%Y-%m-%d %H:%M:%S', columns={{{columns}}})")
        if create:
            con.execute(f"CREATE TABLE {self.table} AS {select}")
        else:
            con.execute(f"INSERT INTO {self.table} ({', '.join(self.columns)}) {select}")


class _Generator:
    def __init__(self, folder: Path, seed: int):
        self.rng = random.Random(seed)
        self.votes = _Writer(folder, "votes", {c: COLUMN_TYPES[c] for c in VOTES_COLUMNS})
        self.sections = _Writer(folder, "sections", {c: COLUMN_TYPES[c] for c in SECTIONS_COLUMNS})
        self.others = {t: _Writer(folder, t, cols) for t, cols in _SCHEMAS.items()}
        self.cities = _places(self.rng)
        self.parties = _parties(self.rng)
        self.next_candidate_id = 900000000001
        self.next_coalition_id = 800000000001
        self.party_names = {p: f"PARTIDO {p[2:]}" for p in self.parties}
        self.local_strength: dict[tuple[int, int], float] = {}

    def writers(self) -> list[_Writer]:
        return [self.votes, self.sections, *self.others.values()]

    # ---- one race on one election day ----

    def _cast(self, section: Section, year, round_number, code, race: Race) -> None:
        """Votes of one section for one race: they add up to the section's turnout."""
        city, name = section.city, race.office_name
        blank = round(section.turnout * self.rng.uniform(0.01, 0.05))
        null = round(section.turnout * self.rng.uniform(0.01, 0.05))
        valid = section.turnout - blank - null
        self._section_row(section, year, round_number, code, race.office, name)
        running = [c for c in race.candidates if c.can_vote]
        party_only = round(valid * 0.05) if race.proportional else 0
        for candidate, count in zip(running, _split(self.rng, valid - party_only,
                                                    [self._local_weight(c, city, race) for c in running])):
            candidate.votes += count
            if count:
                self._vote_row(section, year, round_number, code, race.office, name, "Nominal",
                               candidate.number, candidate.name, candidate.party, count)
        if party_only:
            names = sorted({c.party for c in running if c.party})
            for party, count in zip(names, _split(self.rng, party_only,
                                                  [self.parties[p][1] for p in names])):
                if count:
                    self._vote_row(section, year, round_number, code, race.office, name, "Legenda",
                                   self.parties[party][0], party, party, count)
        for vote_type, number, count in (("Branco", _BLANK, blank), ("Nulo", _NULL, null)):
            if count:
                self._vote_row(section, year, round_number, code, race.office, name, vote_type,
                               number, vote_type, None, count)

    def _local_weight(self, candidate: Candidate, city: City, race: Race) -> float:
        """A candidate is stronger in some places, always the same ones, so maps have a pattern."""
        key = (candidate.number, city.code)
        if key not in self.local_strength:
            self.local_strength[key] = self.rng.lognormvariate(0, 0.5)
        weight = candidate.weight * self.local_strength[key]
        if race.runoff:
            swing = (candidate.number, city.state)
            if swing not in race.swing:
                race.swing[swing] = self.rng.lognormvariate(0, 0.8)
            weight *= race.swing[swing]
        return weight

    def _section_row(self, s: Section, year, round_number, code, office, office_name) -> None:
        self.sections.add(year, round_number, code, s.city.state, s.city.code, s.zone, s.number,
                          office, s.city.name, office_name, s.eligible, s.turnout,
                          s.eligible - s.turnout, s.opened, s.closed, s.issued, s.received,
                          s.city.country, s.city.country_code, s.city.ibge)

    def _vote_row(self, s: Section, year, round_number, code, office, office_name, vote_type,
                  number, name, party, count) -> None:
        self.votes.add(year, round_number, code, s.city.state, s.city.code, s.city.name, s.zone,
                       s.number, office, office_name, vote_type, number, name, party, count,
                       s.received, s.city.country, s.city.country_code, s.city.ibge)

    def _not_installed(self, sections: list[Section], year, round_number, code) -> None:
        """As in the real files: one row with office -1 and no votes."""
        for s in sections:
            self._section_row(s, year, round_number, code, -1, None)
            self._vote_row(s, year, round_number, code, -1, None, None, -1, None, None, 0)

    # ---- the other TSE tables ----

    def _scope(self, race: Race, election_name, election_type, code, year, with_round=None) -> list:
        head = [year] if with_round is None else [year, with_round]
        national = race.state is None and race.city is None
        unit = "BRASIL" if national else (race.city.name if race.city else race.state)
        return head + [code, election_name, election_type,
                       countries.BRAZIL[0] if national else None,
                       countries.BRAZIL[1] if national else None,
                       race.state, race.city.code if race.city else None,
                       race.city.name if race.city else None, unit]

    def _candidate_row(self, race, candidate, office, office_name, election_name, election_type,
                       code, year, round_number, result, candidate_id=None) -> None:
        identifier = candidate_id or self.next_candidate_id
        if candidate_id is None:
            self.next_candidate_id += 1
        female = candidate.name.startswith("CANDIDATA")
        status = "APTO" if candidate.can_vote else "INAPTO"
        self.others["candidates"].add(
            *self._scope(race, election_name, election_type, code, year, round_number),
            office, office_name.upper(), identifier, candidate.number, candidate.name,
            f"{candidate.name} DE TESTE", candidate.party, status, result,
            "FEMININO" if female else "MASCULINO", "NÃO INFORMADA", "ENSINO SUPERIOR COMPLETO",
            "OUTROS")

    def _record_race(self, race: Race, results: dict, election_name, election_type, code, year,
                     round_number) -> None:
        """Candidates with their results, their running mates, seats and party registrations."""
        for candidate in race.candidates:
            result = results.get(candidate.number, "NÃO ELEITO") if candidate.can_vote else None
            self._candidate_row(race, candidate, race.office, race.office_name, election_name,
                                election_type, code, year, round_number, result)
            for mate in _RUNNING_MATES.get(race.office, ()):
                self._candidate_row(race, candidate, mate, _MATE_NAMES[mate], election_name,
                                    election_type, code, year, round_number, result)
        if round_number == 1:
            self.others["seats"].add(*self._scope(race, election_name, election_type, code, year),
                                     race.office, race.office_name.upper(), race.seats)
            for mate in _RUNNING_MATES.get(race.office, ()):
                self.others["seats"].add(*self._scope(race, election_name, election_type, code, year),
                                         mate, _MATE_NAMES[mate], race.seats)
            for party in sorted({c.party for c in race.candidates if c.party}):
                self.others["coalitions"].add(
                    *self._scope(race, election_name, election_type, code, year, round_number),
                    race.office, race.office_name.upper(), "PARTIDO ISOLADO",
                    self.parties[party][0], party, self.party_names[party], None, None,
                    self.next_coalition_id, None, party, "DEFERIDO")
                self.next_coalition_id += 1

    def _electorate(self, year: int, sections: list[Section]) -> None:
        by_city: dict[int, tuple[City, int]] = {}
        for s in sections:
            city, total = by_city.get(s.city.code, (s.city, 0))
            by_city[s.city.code] = (city, total + s.eligible)
        for code in sorted(by_city):
            city, total = by_city[code]
            cells = [(g, a, e) for g in ("FEMININO", "MASCULINO") for a in _AGE_GROUPS
                     for e in _EDUCATION]
            for (gender, age, education), voters in zip(cells, _split(self.rng, total,
                                                                      [1.0] * len(cells))):
                self.others["electorate_profile"].add(year, city.state, code, city.name, gender,
                                                      age, education, voters)

    # ---- whole elections ----

    def _run_rounds(self, races: list[Race], first: list[Section], second: list[Section], year,
                    election_name, codes: tuple[int, int], runoff) -> None:
        """Both rounds of a group of races that share an election name and its codes."""
        for race in races:
            local = [s for s in first if s.installed and _in_race(s, race)]
            for s in local:
                self._cast(s, year, 1, codes[0], race)
            results, finalists = _results(race, runoff(race))
            self._record_race(race, results, election_name, _ORDINARY, codes[0], year, 1)
            if not finalists:
                continue
            finals = Race(race.office, race.office_name, race.state, race.city,
                          [Candidate(c.number, c.party, c.weight + 0.4 * sum(
                              o.weight for o in race.candidates if o not in finalists) / 2, c.name)
                           for c in finalists], race.seats, runoff=True)
            for s in (s for s in second if s.installed and _in_race(s, finals)):
                self._cast(s, year, 2, codes[1], finals)
            winner = max(finals.candidates, key=lambda c: (c.votes, c.number))
            self._record_race(finals, {winner.number: "ELEITO"}, election_name, _ORDINARY,
                              codes[1], year, 2)

    def federal(self, year: int, base_code: int, maps: dict, position: int) -> None:
        rng, parties = self.rng, self.parties
        day1, day2 = _sunday(year, 10, 1), _sunday(year, 10, 4)
        times = position >= 2  # like 2014 and 2018, which have no receipt time
        first = _day_sections(rng, year, maps, day1, 0.0, times)
        second = _day_sections(rng, year, maps, day2, -0.03, times)
        national, state_codes = (base_code, base_code + 1), (base_code + 2, base_code + 3)
        self._not_installed([s for s in first if not s.installed], year, 1, national[0])
        self._not_installed([s for s in second if not s.installed], year, 2, national[1])
        self._electorate(year, first)

        president = Race(_PRESIDENT, OFFICES[_PRESIDENT], None, None,
                         _majority_candidates(rng, parties, 8, flat=1.0))
        self._run_rounds([president], first, second, year, f"Eleição Geral Federal {year}",
                         national, lambda race: True)

        states = sorted({c.state for c in self.cities if c.state != countries.ABROAD_STATE})
        races = []
        for state in states:
            races.append(Race(_GOVERNOR, OFFICES[_GOVERNOR], state, None,
                              _majority_candidates(rng, parties, 4)))
            races.append(Race(_SENATOR, OFFICES[_SENATOR], state, None,
                              _majority_candidates(rng, parties, 4), seats=1 + position % 2))
            races.append(Race(_FEDERAL_DEPUTY, OFFICES[_FEDERAL_DEPUTY], state, None,
                              _list_candidates(rng, parties, 15, 4), seats=8, proportional=True))
            office = _DISTRICT_DEPUTY if state == _DISTRICT else _STATE_DEPUTY
            races.append(Race(office, OFFICES[office], state, None,
                              _list_candidates(rng, parties, 15, 5), seats=10, proportional=True))
        self._run_rounds(races, first, second, year, f"Eleições Gerais Estaduais {year}",
                         state_codes, lambda race: race.office == _GOVERNOR)

        # Councillors of the island: on the ballot, but missing from `candidates`, as in the real data.
        island = [s for s in first if s.installed and s.city.name == _ISLAND]
        councillors = Race(_DISTRICT_COUNCILLOR, _COUNCILLOR_NAME, "PE", None, [Candidate(n, f"P{n}", 1.0, f"CANDIDATO {n}") for n in (74, 85, 78)])
        for s in island:
            self._cast(s, year, 1, base_code + 4, councillors)

    def municipal(self, year: int, base_code: int, maps: dict, position: int) -> None:
        rng, parties = self.rng, self.parties
        day1, day2 = _sunday(year, 10, 1), _sunday(year, 10, 4)
        first = [s for s in _day_sections(rng, year, maps, day1, 0.0, True) if _has_mayor(s.city)]
        second = [s for s in _day_sections(rng, year, maps, day2, -0.03, True) if _has_mayor(s.city)]
        codes = (base_code, base_code + 1)
        self._not_installed([s for s in first if not s.installed], year, 1, codes[0])
        self._electorate(year, first)

        running = [c for c in self.cities if _has_mayor(c) and year >= c.first_year]
        largest = {c.code for c in sorted(running, key=lambda c: -c.size)[:_RUNOFF_CITIES]}
        races = []
        for city in running:
            # Evened out so that small parties win too: the mayors' flow needs its `Outros` node.
            races.append(Race(_MAYOR, OFFICES[_MAYOR], city.state, city,
                              _majority_candidates(rng, parties, 4, flat=1.0)))
            races.append(Race(_COUNCILLOR, OFFICES[_COUNCILLOR], city.state, city,
                              _list_candidates(rng, parties, 12, 5), seats=5, proportional=True))
        self._run_rounds(races, first, second, year, f"Eleições Municipais {year}", codes,
                         lambda race: race.office == _MAYOR and race.city.code in largest)
        self._supplementary(year, base_code + 4, races)
        if position == 0:
            self._question(first, year, base_code + 5)

    def _supplementary(self, year: int, code: int, races: list[Race]) -> None:
        """A court replaced one mayor: only `candidates` knows, as `elected.py` expects."""
        mayors = [r for r in races if r.office == _MAYOR]
        race = self.rng.choice(mayors)
        replacement = Race(_MAYOR, race.office_name, race.state, race.city,
                           _majority_candidates(self.rng, self.parties, 2))
        results = {replacement.candidates[0].number: "ELEITO"}
        self._record_race(replacement, results, f"Eleição Suplementar de {race.city.name.title()}",
                          _SUPPLEMENTARY, code, year, 1)

    def _question(self, sections: list[Section], year: int, code: int) -> None:
        """A referendum in one city, with the answers as candidates, as the TSE files have them."""
        city = next(s.city for s in sections if s.installed)
        question = Race(_QUESTION, _QUESTION_TEXT, city.state, city,
                        [Candidate(1, None, 1.5, "SIM"), Candidate(2, None, 1.0, "NÃO")])
        for s in sections:
            if s.installed and s.city.code == city.code:
                self._cast(s, year, 1, code, question)


def _has_mayor(city: City) -> bool:
    """Brasília and sections abroad do not vote in municipal elections."""
    return city.state not in (_DISTRICT, countries.ABROAD_STATE)


def _in_race(section: Section, race: Race) -> bool:
    if race.city is not None:
        return section.city.code == race.city.code
    if race.state is not None:
        return section.city.state == race.state
    return True  # the president is voted for everywhere, abroad too


def _results(race: Race, can_have_runoff: bool) -> tuple[dict[int, str], list[Candidate]]:
    """Result of each candidate number from the votes of the first round, and the finalists."""
    ranked = sorted((c for c in race.candidates if c.can_vote), key=lambda c: (-c.votes, c.number))
    if race.proportional:
        results = {}
        for i, candidate in enumerate(ranked):
            if i < race.seats:
                results[candidate.number] = "ELEITO POR QP" if i < race.seats * 0.7 else "ELEITO POR MÉDIA"
            elif i < race.seats + 2:
                results[candidate.number] = "SUPLENTE"
        return results, []
    total = sum(c.votes for c in ranked)
    if race.office == _SENATOR or not can_have_runoff or ranked[0].votes * 2 > total:
        return {c.number: "ELEITO" for c in ranked[:race.seats]}, []
    return {c.number: "2º TURNO" for c in ranked[:2]}, ranked[:2]


def _prepare(db: Path) -> None:
    """Refuses a file with other data; empties an earlier synthetic one."""
    con = duckdb.connect(str(db))
    try:
        tables = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        if tables and SYNTHETIC_TABLE not in tables:
            raise SystemExit(f"{db} already has data that is not synthetic; refusing to touch it. "
                             "Choose another file with --db.")
        for table in tables:
            con.execute(f"DROP TABLE {table}")
    finally:
        con.close()


def generate(db: Path = DEFAULT_DB, seed: int = DEFAULT_SEED) -> None:
    """Writes the whole dataset into `db`. Run `build-derived` afterwards, as with real data."""
    _prepare(db)
    with tempfile.TemporaryDirectory(prefix="synthetic_") as tmp:
        generator = _Generator(Path(tmp), seed)
        years = sorted(SYNTHETIC_FEDERAL_YEARS + SYNTHETIC_MUNICIPAL_YEARS)
        maps = _year_maps(generator.rng, generator.cities, years)
        code = 9000
        for position, year in enumerate(SYNTHETIC_FEDERAL_YEARS):
            generator.federal(year, code, maps, position)
            code += 10
        for position, year in enumerate(SYNTHETIC_MUNICIPAL_YEARS):
            generator.municipal(year, code, maps, position)
            code += 10
        con = duckdb.connect(str(db))
        try:
            create_schema(con)
            con.begin()
            for writer in generator.writers():
                writer.load(con, create=writer.table in _SCHEMAS)
            con.execute(f"CREATE TABLE {SYNTHETIC_TABLE} AS SELECT {VERSION} AS version, {seed} AS seed")
            con.commit()
            con.execute("CHECKPOINT")
            for table in ("votes", "sections", *_SCHEMAS):
                n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"{table}: {n:,} rows")
        finally:
            con.close()
