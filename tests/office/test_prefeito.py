import sys
from pathlib import Path

SUPERSET = Path(__file__).resolve().parents[2] / "docker" / "superset"
sys.path.insert(0, str(SUPERSET))

from office_specs.prefeito import OFFICE  # noqa: E402


def _items():
    return [item for sub in OFFICE.subtabs for row in sub.rows for item in row]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Prefeito", 11, 7000)


def test_subtabs():
    assert [s.title for s in OFFICE.subtabs] == [
        "Resultado", "Território", "Mudança", "Candidatos", "Fluxo de prefeitos", "Porte do município", "Entre turnos",
        "Viradas e continuidade"]


def test_office_specific_charts():
    keys = {i.key for i in _items()}
    assert {"Prefeitos_de_um_partido_para_outro", "Fluxos_entre_partidos_de_prefeitos", "band_winners", "band_margin",
            "band_continuity", "band_table", "runoff_up_municipio", "runoff_down_zona", "blank_rounds", "flips",
            "governing_parties", "continuity_by_year", "reelection", "margin_by_year"} <= keys


def test_runoff_has_no_uf_level():
    assert not {i.key for i in _items()} & {"runoff_up_uf", "runoff_down_uf"}


def test_governing_party_is_the_holder():
    chart = next(i for i in _items() if i.key == "governing_parties")
    assert chart.params["groupby"] == ["continuity_party"]


def test_keys_unique():
    keys = [i.key for i in _items()]
    assert len(keys) == len(set(keys))


def test_explanatory_notes():
    notes = {i.key for i in _items() if hasattr(i, "text")}
    assert {"flow_note", "band_note", "runoff_note", "flips_note"} <= notes
