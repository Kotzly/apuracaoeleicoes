import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.senador import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Senador", 5, 3000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == ["Resultado", "Território", "Mudança", "Candidatos", "Senado"]


def test_senate_charts():
    keys = {i.key for i in _items()}
    assert {"Senadores_eleitos_por_ano", "Cadeiras_por_partido_de_eleicao", "Senadores_eleitos_tabela", "renewal",
            "seats_by_year", "seats_state_year", "composition", "composition_line", "composition_state"} <= keys


def test_reused_charts_once():
    reused = [i.key for i in _items() if getattr(i, "reused", False)]
    assert len(reused) == len(set(reused)) == 3


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_no_runoff_subtab():
    assert "Entre turnos" not in [s.title for s in OFFICE.subtabs]


def test_explanatory_note():
    assert "senate_note" in {i.key for i in _items() if hasattr(i, "text")}
