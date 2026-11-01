import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docker" / "superset"))
sys.dont_write_bytecode = True

from office_specs import presidente  # noqa: E402
from office_specs.common import Chart  # noqa: E402

OFFICE = presidente.OFFICE


def _charts(title):
    return [i for tab in OFFICE.subtabs if tab.title == title for r in tab.rows for i in r if isinstance(i, Chart)]


def test_office_identity():
    assert (OFFICE.name, OFFICE.code, OFFICE.first_id) == ("Presidente", 1, 1000)


def test_subtabs():
    titles = [t.title for t in OFFICE.subtabs]
    assert titles[:4] == ["Resultado", "Território", "Mudança", "Candidatos"]
    assert "Entre turnos" in titles and "Fluxo dos mais votados" in titles


def test_between_rounds_reused_without_copy():
    charts = _charts("Entre turnos")
    reused = {c.template for c in charts if c.reused}
    assert len(reused) == 24
    assert {"Cidades_variacao_pct", "Maiores_altas_votos", "UF_por_variacao_pct",
            "Zonas_distribuicao_da_variacao_votos"} <= reused
    assert any(c.key == "blank_rounds" for c in charts)


def test_flow_sankeys():
    assert {c.template for c in _charts("Fluxo dos mais votados")} == {
        "Municipios_por_candidato_mais_votado", "Secoes_por_candidato_mais_votado"}


def test_winner_pivot():
    pivot = next(c for c in _charts("Vencedor por UF") if c.key == "winner_by_state")
    assert pivot.title == "Vencedor em cada UF por eleição" and pivot.viz == "pivot_table_v2"


def test_unique_keys():
    keys = [i.key for t in OFFICE.subtabs for r in t.rows for i in r]
    assert len(keys) == len(set(keys))
