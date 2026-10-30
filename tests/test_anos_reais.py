"""Testes com amostras reais (AC, 1º turno) de cada layout publicado pelo TSE."""

from pathlib import Path

import duckdb
import pytest

from apuracao.carregar import carregar

DADOS = Path(__file__).parent / "dados"
AMOSTRAS = {
    2016: "bweb_1t_AC_04102016204813.zip",
    2018: "bweb_1t_AC_101020181938.zip",
    2020: "bweb_1t_AC_181120201549.zip",
    2024: "bweb_1t_AC_091020241636.zip",
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    caminho = tmp_path_factory.mktemp("real") / "e.duckdb"
    for ano, arq in AMOSTRAS.items():
        carregar([DADOS / arq], caminho, ano)
    return caminho


def _q(db, sql):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_todos_os_anos_no_mesmo_schema(db):
    assert _q(db, "SELECT ano, count(*) FROM votos GROUP BY 1 ORDER BY 1") == \
        [(2016, 42), (2018, 40), (2020, 41), (2024, 40)]
    nulos = _q(db, """SELECT ano FROM votos WHERE nr_turno IS NULL OR sg_uf IS NULL
                      OR cd_municipio IS NULL OR nr_zona IS NULL OR nr_secao IS NULL
                      OR cd_cargo IS NULL OR qt_votos IS NULL""")
    assert nulos == []


def test_2016_turno_do_nome_e_tipo_derivado(db):
    assert _q(db, "SELECT DISTINCT nr_turno FROM votos WHERE ano = 2016") == [(1,)]
    tipos = {r[0] for r in _q(db, "SELECT DISTINCT ds_tipo_votavel FROM votos WHERE ano = 2016")}
    assert {"Nominal", "Nulo"} <= tipos
    # seção não instalada: entra com 0 votos e sem votável
    assert _q(db, """SELECT qt_votos, nr_votavel FROM votos
                     WHERE ano = 2016 AND nr_zona = 9 AND nr_secao = 170""") == [(0, None)] * 2


def test_2018_sem_horario_de_recebimento(db):
    assert _q(db, "SELECT count(*) FROM secoes WHERE ano = 2018 AND ts_bu_recebido IS NOT NULL") \
        == [(0,)]
    assert _q(db, "SELECT DISTINCT sg_uf FROM votos WHERE ano = 2018") == [("AC",)]  # "SG_ UF"
    assert _q(db, "SELECT count(*) FROM votos WHERE ano = 2018 AND sg_partido = '#NULO#'") == [(0,)]


def test_2020_aspas_mal_escapadas(db):
    assert _q(db, "SELECT nm_votavel, qt_votos FROM votos WHERE ano = 2020 AND nr_votavel = 65133") \
        == [("DÉIA FILHA DO MARTILHO", 2)]


def test_horarios_em_brasilia(db):
    # Acre é UTC-5: horários locais viram +2h (em 2016 inclusive o recebimento:
    # 18:19:29 local na amostra).
    assert _q(db, "SELECT min(ts_bu_recebido)::VARCHAR FROM secoes WHERE ano = 2016") == \
        [("2016-10-02 20:19:29",)]
    assert _q(db, """SELECT min(ts_abertura)::VARCHAR, min(ts_encerramento) >= '2024-10-06 17:00'
                     FROM secoes WHERE ano = 2024""") == [("2024-10-06 08:00:01", True)]
    # recebimento depois do encerramento em todo ano que tem os dois
    assert _q(db, """SELECT count(*) FROM secoes
                     WHERE ts_bu_recebido < ts_encerramento""") == [(0,)]


def test_aptos_consistentes(db):
    assert _q(db, """SELECT count(*) FROM secoes
                     WHERE qt_aptos <> qt_comparecimento + qt_abstencoes""") == [(0,)]
