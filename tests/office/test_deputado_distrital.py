import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.deputado_distrital import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Deputado distrital", 8, 6000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == ["Resultado", "Território", "Mudança", "Candidatos", "Bancadas"]


def test_office_specific_charts():
    keys = {i.key for i in _items()}
    assert {"benches", "benches_line", "votes_seats", "votes_seats_table", "vote_kinds", "elected_how", "vote_getters",
            "reelection", "zone_up", "zone_down", "zone_table", "blank_by_election"} <= keys


def test_no_maps():
    assert not any("map" in i.key for i in _items())
    assert not any(getattr(i, "viz", None) == "country_map" for i in _items())


def test_no_comparison_between_states_or_cities():
    keys = {i.key for i in _items()}
    assert not keys & {"bench_by_state", "blank_by_state", "blank_up", "blank_down", "state_up", "state_down",
                       "state_table", "volatility_state", "city_up", "city_down", "city_table", "volatility_city",
                       "volatility_histogram"}


def test_no_runoff_charts():
    assert not any(i.key.startswith(("runoff", "blank_rounds")) for i in _items())


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_explanatory_notes():
    notes = {i.key: i.text for i in _items() if hasattr(i, "text")}
    assert "24" in notes["district_note"]
