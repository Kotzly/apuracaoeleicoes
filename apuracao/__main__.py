"""Evolução da apuração a partir dos boletins de urna do TSE.

  python -m apuracao pipeline --ano 2024            baixa e carrega um ano inteiro
  python -m apuracao baixar --ano 2022 --uf SP      só baixa
  python -m apuracao carregar --ano 2022 data/raw/2022/*.zip
  python -m apuracao inspecionar --ano 2018 arquivo.zip
  python -m apuracao validar --ciclo ele2022 --eleicao 544 --cargo 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _lista_int(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apuracao", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def filtros(sp, carga=False, download=False):
        sp.add_argument("--ano", type=int, required=True)
        if download:
            sp.add_argument("--turno", type=_lista_int, help="ex.: 1 ou 1,2 (padrão: todos)")
            sp.add_argument("--uf", type=lambda s: s.split(","), help="ex.: SP,RJ,ZZ (padrão: todas)")
            sp.add_argument("--destino", type=Path, default=Path("data/raw"))
            sp.add_argument("--pacote", help="id do conjunto no portal, se diferente do padrão")
        if carga:
            sp.add_argument("--db", type=Path, default=Path("eleicoes.duckdb"))
            sp.add_argument("--cargos", type=_lista_int,
                            help="só estes códigos de cargo, ex.: 1,3 (presidente, governador)")

    filtros(sub.add_parser("pipeline", help="baixa e carrega um ano"), carga=True, download=True)
    filtros(sub.add_parser("baixar", help="baixa os zips de boletim de urna"), download=True)
    c = sub.add_parser("carregar", help="carrega zips/arquivos no DuckDB")
    filtros(c, carga=True)
    c.add_argument("arquivos", type=Path, nargs="+")
    i = sub.add_parser("inspecionar", help="mostra o mapeamento de colunas de um arquivo")
    filtros(i)
    i.add_argument("arquivo", type=Path)

    v = sub.add_parser("validar", help="compara a soma dos boletins com o JSON oficial")
    v.add_argument("--db", type=Path, default=Path("eleicoes.duckdb"))
    v.add_argument("--ciclo", required=True, help="ex.: ele2022")
    v.add_argument("--eleicao", type=int, required=True, help="ex.: 544 (1º turno federal 2022)")
    v.add_argument("--cargo", type=int, required=True, help="ex.: 1 (presidente)")
    v.add_argument("--abrangencia", default="br", help="br ou sigla da UF")
    v.add_argument("--json", type=Path, help="usar um JSON oficial já baixado")

    a = p.parse_args(argv)
    if a.cmd in ("pipeline", "baixar"):
        from .baixar import baixar
        arquivos = baixar(a.ano, a.destino, a.turno, a.uf, a.pacote)
        if a.cmd == "pipeline" and arquivos:
            from .carregar import carregar
            carregar(arquivos, a.db, a.ano, a.cargos)
    elif a.cmd == "carregar":
        from .carregar import carregar
        carregar(a.arquivos, a.db, a.ano, a.cargos)
    elif a.cmd == "inspecionar":
        from .carregar import inspecionar
        inspecionar(a.arquivo, a.ano)
    elif a.cmd == "validar":
        from .validar import validar
        return 0 if validar(a.db, a.ciclo, a.eleicao, a.cargo, a.abrangencia, a.json) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
