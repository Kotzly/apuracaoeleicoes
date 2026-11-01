import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.vereador import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Vereador", 13, 8000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == ["Resultado", "Território", "Mudança", "Candidatos", "Bancadas"]


def test_office_specific_charts():
    keys = {i.key for i in _items()}
    assert {"benches", "votes_seats", "bench_by_state", "vote_kinds", "elected_how", "vote_getters", "reelection",
            "vote_getters_share", "candidates_per_seat_state", "contested_cities", "largest_councils",
            "parties_cities", "parties_cities_line"} <= keys


def test_vote_kinds_read_municipal_results():
    chart = next(i for i in _items() if i.key == "vote_kinds")
    assert chart.dataset == "7a450a29-0b17-5534-b2c0-6527a4863b93"


def test_no_runoff_charts():
    assert not any(i.key.startswith(("runoff", "blank_rounds")) for i in _items())


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_series_are_history():
    items = {i.key: i for i in _items()}
    assert items["parties_cities_line"].history
    assert not items["parties_cities"].history


def test_explanatory_notes():
    assert "bench_note" in {i.key for i in _items() if hasattr(i, "text")}
