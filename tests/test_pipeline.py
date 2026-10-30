"""Testes com um CSV sintético no layout bweb do TSE (latin-1, ';', campos entre aspas)."""

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from apuracao.carregar import carregar
from apuracao.validar import validar

CABECALHO = [
    "DT_GERACAO", "HH_GERACAO", "ANO_ELEICAO", "CD_TIPO_ELEICAO", "NM_TIPO_ELEICAO",
    "CD_PLEITO", "DT_PLEITO", "NR_TURNO", "CD_ELEICAO", "DS_ELEICAO", "SG_UF",
    "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA", "NR_SECAO", "NR_LOCAL_VOTACAO",
    "CD_CARGO_PERGUNTA", "DS_CARGO_PERGUNTA", "NR_PARTIDO", "SG_PARTIDO", "NM_PARTIDO",
    "DT_BU_RECEBIDO", "QT_APTOS", "QT_COMPARECIMENTO", "QT_ABSTENCOES", "CD_TIPO_URNA",
    "DS_TIPO_URNA", "CD_TIPO_VOTAVEL", "DS_TIPO_VOTAVEL", "NR_VOTAVEL", "NM_VOTAVEL",
    "QT_VOTOS", "NR_URNA_EFETIVADA", "DT_ABERTURA", "DT_ENCERRAMENTO", "DT_EMISSAO_BU",
]

# (uf, cod_mun, nome_mun, zona, secao, horario_recebido, aptos, comparec, votos{cand: qt})
SECOES = [
    ("SP", 71072, "SÃO PAULO", 1, 10, "02/10/2022 17:20:00", 300, 250, {13: 120, 22: 100}),
    ("SP", 71072, "SÃO PAULO", 1, 11, "02/10/2022 18:05:00", 300, 260, {13: 110, 22: 130}),
    ("SP", 71072, "SÃO PAULO", 2, 20, "02/10/2022 17:45:00", 280, 200, {13: 90, 22: 95}),
    # zona 3 abrange dois municípios
    ("SP", 61115, "GUARULHOS", 3, 30, "02/10/2022 19:10:00", 250, 210, {13: 100, 22: 90}),
    ("SP", 64777, "ARUJÁ", 3, 31, "02/10/2022 17:30:00", 200, 170, {13: 70, 22: 85}),
]


def _linhas(secoes, cargos=((1, "Presidente", 544), (3, "Governador", 546))):
    for uf, mun, nome, zona, sec, ts, aptos, comp, votos in secoes:
        for cd_cargo, ds_cargo, eleicao in cargos:
            for nr, qt in votos.items():
                yield {
                    "DT_GERACAO": "05/10/2022", "HH_GERACAO": "13:21:00",
                    "ANO_ELEICAO": "2022", "CD_TIPO_ELEICAO": "2",
                    "NM_TIPO_ELEICAO": "Eleição Ordinária", "CD_PLEITO": "406",
                    "DT_PLEITO": "02/10/2022", "NR_TURNO": "1", "CD_ELEICAO": str(eleicao),
                    "DS_ELEICAO": "Eleição Geral", "SG_UF": uf, "CD_MUNICIPIO": str(mun),
                    "NM_MUNICIPIO": nome, "NR_ZONA": str(zona), "NR_SECAO": str(sec),
                    "NR_LOCAL_VOTACAO": "1000", "CD_CARGO_PERGUNTA": str(cd_cargo),
                    "DS_CARGO_PERGUNTA": ds_cargo, "NR_PARTIDO": str(nr),
                    "SG_PARTIDO": f"P{nr}", "NM_PARTIDO": f"Partido {nr}",
                    "DT_BU_RECEBIDO": ts, "QT_APTOS": str(aptos),
                    "QT_COMPARECIMENTO": str(comp), "QT_ABSTENCOES": str(aptos - comp),
                    "CD_TIPO_URNA": "1", "DS_TIPO_URNA": "Apurada", "CD_TIPO_VOTAVEL": "1",
                    "DS_TIPO_VOTAVEL": "Nominal", "NR_VOTAVEL": str(nr),
                    "NM_VOTAVEL": f"CANDIDATO {nr} ÇÃÉ", "QT_VOTOS": str(qt),
                    "NR_URNA_EFETIVADA": "2000000", "DT_ABERTURA": "02/10/2022 08:00:00",
                    "DT_ENCERRAMENTO": "02/10/2022 17:00:00",
                    "DT_EMISSAO_BU": "02/10/2022 17:01:00",
                }


def _zip(destino: Path, nome: str, secoes, remover=(), renomear=None,
         delim=";", ext=".csv") -> Path:
    """Gera um zip no layout bweb, opcionalmente removendo/renomeando colunas."""
    renomear = renomear or {}
    cab = [c for c in CABECALHO if c not in remover]
    texto = delim.join(f'"{renomear.get(c, c)}"' for c in cab) + "\n"
    for linha in _linhas(secoes):
        texto += delim.join(f'"{linha[c]}"' for c in cab) + "\n"
    arq = destino / f"{nome}.zip"
    with zipfile.ZipFile(arq, "w") as z:
        z.writestr(f"{nome}{ext}", texto.encode("latin-1"))
        z.writestr("leiame.pdf", b"%PDF-1.4")
    return arq


@pytest.fixture
def db(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_051020221321", SECOES)
    caminho = tmp_path / "e.duckdb"
    carregar([zp], caminho, 2022)
    return caminho, zp


def _q(db, sql):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_carga_basica(db):
    caminho, _ = db
    assert _q(caminho, "SELECT count(*) FROM votos")[0][0] == 5 * 2 * 2
    assert _q(caminho, "SELECT count(*) FROM secoes")[0][0] == 5 * 2
    # acentos lidos corretamente do latin-1
    assert ("SÃO PAULO",) in _q(caminho, "SELECT DISTINCT nm_municipio FROM votos")
    # horário convertido
    assert _q(caminho, "SELECT min(ts_bu_recebido)::VARCHAR FROM votos")[0][0] == "2022-10-02 17:20:00"
    assert _q(caminho, "SELECT count(*) FROM secoes WHERE ts_emissao_bu IS NULL")[0][0] == 0


def test_recarga_nao_duplica(db):
    caminho, zp = db
    carregar([zp], caminho, 2022)
    assert _q(caminho, "SELECT count(*) FROM votos")[0][0] == 20
    assert _q(caminho, "SELECT sum(qt_aptos) FROM secoes WHERE cd_cargo = 1")[0][0] == 1330


def test_filtro_de_cargo(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_x", SECOES)
    caminho = tmp_path / "f.duckdb"
    carregar([zp], caminho, 2022, cargos=[1])
    assert _q(caminho, "SELECT DISTINCT cd_cargo FROM votos") == [(1,)]


def test_evolucao_termina_no_consolidado(db):
    caminho, _ = db
    sql = """
        WITH evo AS (
            SELECT sg_uf, nr_zona, nr_votavel, ts_bu_recebido,
                   sum(sum(qt_votos)) OVER (PARTITION BY sg_uf, nr_zona, nr_votavel
                                            ORDER BY ts_bu_recebido) AS acum
            FROM votos WHERE cd_cargo = 1
            GROUP BY sg_uf, nr_zona, nr_votavel, ts_bu_recebido)
        SELECT nr_zona, nr_votavel, max(acum) FROM evo GROUP BY ALL ORDER BY 1, 2
    """
    final = _q(caminho, sql)
    consolidado = _q(caminho, """SELECT nr_zona, nr_votavel, sum(qt_votos) FROM votos
                                 WHERE cd_cargo = 1 GROUP BY ALL ORDER BY 1, 2""")
    assert final == consolidado
    # zona 3 agrega dois municípios
    assert (3, 13, 170) in consolidado


def test_curva_tem_a_ordem_certa(db):
    caminho, _ = db
    curva = _q(caminho, """
        SELECT ts_bu_recebido::VARCHAR,
               sum(sum(qt_votos)) OVER (ORDER BY ts_bu_recebido)
        FROM votos WHERE cd_cargo = 1 AND nr_votavel = 13
        GROUP BY ts_bu_recebido ORDER BY 1""")
    assert [v for _, v in curva] == [120, 190, 280, 390, 490]


def test_consultas_de_exemplo_rodam(db):
    caminho, _ = db
    sql = (Path(__file__).parent.parent / "consultas.sql").read_text()
    with duckdb.connect(str(caminho), read_only=True) as con:
        for stmt in [s for s in sql.split(";") if "SELECT" in s]:
            assert con.execute(stmt).fetchall()


def test_validacao(db, tmp_path):
    caminho, _ = db
    oficial = {"cand": [{"n": "13", "nm": "C13", "vap": "490"},
                        {"n": "22", "nm": "C22", "vap": "500"}]}
    js = tmp_path / "of.json"
    js.write_text(json.dumps(oficial))
    assert validar(caminho, "ele2022", 544, 1, "br", js)
    assert validar(caminho, "ele2022", 544, 1, "sp", js)
    oficial["cand"][0]["vap"] = "491"
    js.write_text(json.dumps(oficial))
    assert not validar(caminho, "ele2022", 544, 1, "br", js)
    assert not validar(caminho, "ele2022", 999, 1, "br", js)


# --- heterogeneidade entre anos -------------------------------------------------

def test_layout_com_colunas_a_menos_vira_null(tmp_path):
    zp = _zip(tmp_path, "bweb_1t_SP_2018", SECOES,
              remover=("ANO_ELEICAO", "DT_EMISSAO_BU", "DT_ABERTURA"),
              renomear={"CD_CARGO_PERGUNTA": "CD_CARGO", "DS_CARGO_PERGUNTA": "DS_CARGO"})
    caminho = tmp_path / "h.duckdb"
    carregar([zp], caminho, 2018)
    assert _q(caminho, "SELECT DISTINCT ano FROM votos") == [(2018,)]       # veio do parâmetro
    assert _q(caminho, "SELECT DISTINCT cd_cargo FROM votos ORDER BY 1") == [(1,), (3,)]
    assert _q(caminho, "SELECT count(*) FROM secoes WHERE ts_emissao_bu IS NOT NULL")[0][0] == 0
    assert _q(caminho, "SELECT count(*) FROM secoes WHERE ts_bu_recebido IS NULL")[0][0] == 0


def test_anos_diferentes_convivem_no_mesmo_banco(tmp_path):
    caminho = tmp_path / "m.duckdb"
    carregar([_zip(tmp_path, "a", SECOES)], caminho, 2022)
    carregar([_zip(tmp_path, "b", SECOES, remover=("ANO_ELEICAO", "DT_ENCERRAMENTO"))],
             caminho, 2018)
    assert _q(caminho, "SELECT ano, count(*) FROM votos GROUP BY 1 ORDER BY 1") == \
        [(2018, 20), (2022, 20)]
    # recarregar 2018 não apaga 2022
    carregar([_zip(tmp_path, "c", SECOES[:1], remover=("ANO_ELEICAO",))], caminho, 2018)
    assert _q(caminho, "SELECT ano, count(*) FROM votos GROUP BY 1 ORDER BY 1") == \
        [(2018, 4), (2022, 20)]


def test_coluna_obrigatoria_ausente_falha_com_mensagem(tmp_path):
    zp = _zip(tmp_path, "x", SECOES, remover=("NR_ZONA",))
    with pytest.raises(ValueError, match="nr_zona"):
        carregar([zp], tmp_path / "e.duckdb", 2022)


def test_formato_novo_registrado_para_ano_futuro(tmp_path):
    from apuracao.formatos import BwebCSV, formato_para, registrar

    @registrar(2098)
    class Hipotetico(BwebCSV):
        delim = "|"
        extensao_dados = ".txt"
        mapa = {**BwebCSV.mapa, "nr_secao": ["NUM_SECAO"]}

    assert isinstance(formato_para(2098), Hipotetico)
    assert type(formato_para(2099)).__name__ == "Hipotetico"   # herda o anterior
    zp = _zip(tmp_path, "f", SECOES, delim="|", ext=".txt",
              renomear={"NR_SECAO": "NUM_SECAO"})
    caminho = tmp_path / "f.duckdb"
    carregar([zp], caminho, 2098)
    assert _q(caminho, "SELECT count(DISTINCT nr_secao) FROM votos")[0][0] == 5


def test_ano_sem_formato_anterior_falha():
    from apuracao.formatos import formato_para
    with pytest.raises(SystemExit):
        formato_para(2010)


def test_schema_ganha_colunas_novas(tmp_path):
    from apuracao.carregar import criar_schema
    caminho = tmp_path / "s.duckdb"
    with duckdb.connect(str(caminho)) as con:
        con.execute("CREATE TABLE votos (ano SMALLINT, qt_votos INTEGER)")
        con.execute("INSERT INTO votos VALUES (2014, 5)")
        criar_schema(con)
        assert con.execute("SELECT ano, ts_bu_recebido FROM votos").fetchall() == [(2014, None)]


def test_descoberta_filtra_hash_e_aceita_maiusculas(monkeypatch):
    from apuracao import baixar

    base = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2018/buweb/"
    urls = [base + "BWEB_1t_AC_081020181525.zip", base + "BWEB_1t_AC_081020181525.zip.sha512",
            base + "BWEB_2t_SP_301020181744.zip", base + "leiame.pdf"]

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"result": {"resources": [{"url": u} for u in urls]}}

    monkeypatch.setattr(baixar.requests, "get", lambda *a, **k: Resp())
    recs = baixar.listar(2018)
    assert [(r["turno"], r["uf"]) for r in recs] == [(1, "AC"), (2, "SP")]
    assert recs[0]["sha512_url"] == urls[1]
    assert recs[1]["sha512_url"] is None
