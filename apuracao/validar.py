"""Compara a soma dos boletins de urna com o resultado oficial publicado na divulgação."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import requests

URL = ("https://resultados.tse.jus.br/oficial/{ciclo}/{eleicao}/dados-simplificados/"
       "{abr}/{abr}-c{cargo:04d}-e{eleicao:06d}-r.json")


def oficial(ciclo: str, eleicao: int, cargo: int, abrangencia: str,
            arquivo: Path | None = None) -> dict[int, tuple[str, int]]:
    """{número: (nome, votos)} do JSON oficial (baixado ou de um arquivo local)."""
    if arquivo:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    else:
        url = URL.format(ciclo=ciclo, eleicao=eleicao, cargo=cargo, abr=abrangencia.lower())
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        dados = r.json()
    return {int(c["n"]): (c.get("nm", ""), int(c["vap"])) for c in dados["cand"]}


def _somas(con, eleicao: int, cargo: int, abrangencia: str):
    filtro_uf = "" if abrangencia.lower() == "br" else "AND sg_uf = ?"
    params = [eleicao, cargo] + ([abrangencia.upper()] if filtro_uf else [])
    bu = dict(con.execute(f"""
        SELECT nr_votavel, sum(qt_votos)::BIGINT FROM votos
        WHERE cd_eleicao = ? AND cd_cargo = ? {filtro_uf}
        GROUP BY nr_votavel
    """, params).fetchall())
    presentes = con.execute(
        "SELECT DISTINCT cd_eleicao, cd_cargo FROM votos ORDER BY 1, 2").fetchall()
    return bu, presentes


def validar(db: Path, ciclo: str, eleicao: int, cargo: int,
            abrangencia: str = "br", arquivo: Path | None = None) -> bool:
    ofi = oficial(ciclo, eleicao, cargo, abrangencia, arquivo)
    with duckdb.connect(str(db), read_only=True) as con:
        bu, presentes = _somas(con, eleicao, cargo, abrangencia)
    if not bu:
        print(f"Nenhum voto para eleição {eleicao}, cargo {cargo}. "
              f"Presentes no banco (eleição, cargo): {presentes}")
        return False

    ok = True
    print(f"{'nº':>6}  {'candidato':<30} {'oficial':>14} {'boletins':>14} {'diferença':>10}")
    for n, (nome, votos) in sorted(ofi.items(), key=lambda x: -x[1][1]):
        soma = bu.get(n, 0)
        dif = soma - votos
        ok &= dif == 0
        print(f"{n:>6}  {nome[:30]:<30} {votos:>14,} {soma:>14,} {dif:>+10,}")
    print("OK: boletins batem com o oficial." if ok else
          "DIVERGÊNCIA: faltam UFs carregadas ou há boletins fora do arquivo.")
    return ok
