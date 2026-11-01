import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.governador import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Governador", 3, 2000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == [
        "Resultado", "Território", "Mudança", "Candidatos", "Entre turnos", "Viradas e continuidade"]


def test_office_specific_charts():
    keys = {i.key for i in _items()}
    assert {"runoff_up_uf", "runoff_down_municipio", "runoff_up_zona", "blank_rounds", "flips",
            "governing_parties", "governing_table", "continuity_by_year", "reelection", "margin_by_year"} <= keys


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_explanatory_notes():
    notes = {i.key for i in _items() if hasattr(i, "text")}
    assert {"runoff_note", "flips_note"} <= notes
