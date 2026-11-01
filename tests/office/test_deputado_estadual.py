import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.deputado_estadual import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Deputado estadual", 7, 5000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == ["Resultado", "Território", "Mudança", "Candidatos", "Bancadas"]


def test_office_specific_charts():
    keys = {i.key for i in _items()}
    assert {"benches", "benches_line", "votes_seats", "votes_seats_table", "bench_by_state", "vote_kinds",
            "elected_how", "vote_getters", "reelection", "seats_by_state", "candidates_per_seat_state"} <= keys


def test_no_runoff_charts():
    assert not any(i.key.startswith(("runoff", "blank_rounds")) for i in _items())


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_explanatory_notes():
    notes = {i.key: i.text for i in _items() if hasattr(i, "text")}
    assert "bench_note" in notes
    assert "26" in notes["bench_note"] and "Distrito Federal" in notes["bench_note"]
