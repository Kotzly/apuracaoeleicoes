"""Carrega boletins de urna de qualquer ano suportado num arquivo DuckDB.

Modelo: uma tabela fato `votos` (seção × cargo × votável) e uma tabela `secoes`
(seção × cargo). Nenhum acumulado é armazenado: a evolução sai da soma cumulativa
ordenada por ts_bu_recebido, agrupando pelo nível desejado.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

from .formatos import CANONICO, formato_para

CHAVE_SECAO = ["ano", "nr_turno", "cd_eleicao", "sg_uf", "cd_municipio",
               "nr_zona", "nr_secao", "cd_cargo"]
VOTOS = ["ano", "nr_turno", "cd_eleicao", "sg_uf", "cd_municipio", "nm_municipio",
         "nr_zona", "nr_secao", "cd_cargo", "ds_cargo", "ds_tipo_votavel",
         "nr_votavel", "nm_votavel", "sg_partido", "qt_votos", "ts_bu_recebido"]
# Aptos e comparecimento se repetem em toda linha de votos da seção; ficam aqui
# para não serem somados em dobro.
SECOES = CHAVE_SECAO + ["nm_municipio", "ds_cargo", "qt_aptos", "qt_comparecimento",
                        "qt_abstencoes", "ts_abertura", "ts_encerramento",
                        "ts_emissao_bu", "ts_bu_recebido"]
# Como cada coluna de `secoes` é reduzida a partir das linhas de votos da seção.
_AGREGA_SECAO = {"ts_abertura": "min", "ts_encerramento": "max",
                 "ts_emissao_bu": "min", "ts_bu_recebido": "min"}


def criar_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Cria as tabelas, ou acrescenta colunas novas do schema canônico (NULL nos dados antigos)."""
    for tabela, cols in (("votos", VOTOS), ("secoes", SECOES)):
        defs = ", ".join(f"{c} {CANONICO[c]}" for c in cols)
        con.execute(f"CREATE TABLE IF NOT EXISTS {tabela} ({defs})")
        for c in cols:
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {c} {CANONICO[c]}")


def colunas_brutas(con: duckdb.DuckDBPyConnection, fonte: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {fonte}").fetchall()]


def carregar_arquivos(con: duckdb.DuckDBPyConnection, arquivos: list[Path], ano: int,
                      cargos: list[int] | None = None) -> int:
    """Carrega arquivos já extraídos. Substitui o que existia para o mesmo ano/turno/UF."""
    fmt = formato_para(ano)
    fonte = fmt.fonte_sql(arquivos)
    select, nulas = fmt.select_sql(fonte, set(colunas_brutas(con, fonte)), arquivos, cargos)
    nome = ", ".join(a.name for a in arquivos)
    if nulas:
        print(f"  {nome}: sem correspondência, preenchidas com NULL: {nulas}")
    if "ts_bu_recebido" in nulas:
        print(f"  aviso: {ano} não tem horário de recebimento; a evolução não sai para este ano.")

    _conferir_linhas(con, fonte, arquivos, fmt.linhas_de_cabecalho())
    con.execute(f"CREATE OR REPLACE TEMP TABLE staging AS {select}")
    n = con.execute("SELECT count(*) FROM staging").fetchone()[0]
    sem_horario = con.execute(
        "SELECT count(*) FROM staging WHERE ts_bu_recebido IS NULL").fetchone()[0]
    if sem_horario and "ts_bu_recebido" not in nulas:
        print(f"  aviso: {sem_horario:,} de {n:,} linhas com horário de recebimento inválido")

    chaves = con.execute("SELECT DISTINCT ano, nr_turno, sg_uf FROM staging").fetchall()
    agregados = ", ".join(
        f"{_AGREGA_SECAO.get(c, 'max' if c.startswith('qt_') else 'any_value')}({c})"
        for c in SECOES if c not in CHAVE_SECAO)
    con.begin()
    try:
        for tabela in ("votos", "secoes"):
            for chave in chaves:
                con.execute(f"DELETE FROM {tabela} WHERE ano IS NOT DISTINCT FROM ? AND "
                            "nr_turno IS NOT DISTINCT FROM ? AND sg_uf IS NOT DISTINCT FROM ?",
                            list(chave))
        con.execute(f"INSERT INTO votos ({', '.join(VOTOS)}) "
                    f"SELECT {', '.join(VOTOS)} FROM staging")
        con.execute(f"INSERT INTO secoes ({', '.join(SECOES)}) "
                    f"SELECT {', '.join(CHAVE_SECAO)}, {agregados} FROM staging "
                    f"GROUP BY {', '.join(CHAVE_SECAO)}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("DROP TABLE IF EXISTS staging")
    return n


def _conferir_linhas(con, fonte: str, arquivos: list[Path], cabecalho: int) -> None:
    """Garante que o parser não descartou nem juntou linhas (o TSE às vezes erra as aspas)."""
    fisicas = 0
    for a in arquivos:
        with open(a, "rb") as f:
            fisicas += sum(b.count(b"\n") for b in iter(lambda: f.read(1 << 24), b"")) - cabecalho
    lidas = con.execute(f"SELECT count(*) FROM {fonte}").fetchone()[0]
    if lidas != fisicas:
        print(f"  ATENÇÃO: {fisicas:,} linhas no arquivo, {lidas:,} lidas. "
              "Confira aspas/quebras de linha no arquivo bruto.")


def _dados(arquivo: Path, extensao: str, destino: Path) -> list[Path]:
    """Arquivos de dados: os de dentro do zip, ou o próprio arquivo se não for zip."""
    if arquivo.suffix.lower() != ".zip":
        return [arquivo]
    with zipfile.ZipFile(arquivo) as z:
        membros = [m for m in z.namelist() if m.lower().endswith(extensao)]
        return [Path(z.extract(m, destino)) for m in membros]


def carregar(arquivos: list[Path], db: Path, ano: int,
             cargos: list[int] | None = None) -> None:
    """Carrega .zip (como vêm do TSE) ou arquivos já extraídos de um ano."""
    fmt = formato_para(ano)
    con = duckdb.connect(str(db))
    criar_schema(con)
    try:
        for arq in arquivos:
            tmp = Path(tempfile.mkdtemp(prefix="bweb_"))
            try:
                dados = _dados(arq, fmt.extensao_dados, tmp)
                if not dados:
                    print(f"{arq.name}: nenhum arquivo {fmt.extensao_dados} no zip, ignorado")
                    continue
                n = carregar_arquivos(con, dados, ano, cargos)
                print(f"{arq.name}: {n:,} linhas")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        con.execute("CHECKPOINT")
    finally:
        con.close()


def inspecionar(arquivo: Path, ano: int) -> None:
    """Mostra como as colunas do arquivo bruto se encaixam no schema canônico."""
    fmt = formato_para(ano)
    tmp = Path(tempfile.mkdtemp(prefix="insp_"))
    try:
        dados = _dados(arquivo, fmt.extensao_dados, tmp)
        if not dados:
            raise SystemExit(f"Nenhum arquivo {fmt.extensao_dados} em {arquivo.name}")
        con = duckdb.connect()
        fonte = fmt.fonte_sql(dados)
        brutas = colunas_brutas(con, fonte)
        res = fmt.resolver(set(brutas))
        const = fmt.constantes(dados)
        print(f"Formato: {type(fmt).__name__} (ano {ano})\n")
        for c, b in res.items():
            if b:
                marca = ""
            elif c in const:
                marca = f"  (constante do contexto: {const[c]})"
            else:
                marca = "  <-- OBRIGATÓRIA" if c in fmt.obrigatorias else "  (NULL)"
            print(f"  {c:<20} <- {b or '-'}{marca}")
        usadas = {b for b in res.values() if b}
        print(f"\nColunas brutas não usadas: {[b for b in brutas if b not in usadas]}")
        linha = con.execute(f"SELECT * FROM {fonte} LIMIT 1").fetchone()
        print("\nPrimeira linha:")
        for b, v in zip(brutas, linha or []):
            print(f"  {b:<28} {v}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
