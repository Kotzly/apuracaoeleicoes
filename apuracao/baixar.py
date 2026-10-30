"""Descobre e baixa os zips de boletim de urna pelo portal de dados abertos do TSE.

Os nomes têm um carimbo de data/hora (ex.: bweb_1t_MT_051020221321.zip), então a URL
vem da API CKAN do portal. Quando o TSE publica o .sha512 ao lado, o download é conferido.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from .formatos import formato_para

CKAN = "https://dadosabertos.tse.jus.br/api/3/action/package_show"


def listar(ano: int, pacote: str | None = None) -> list[dict]:
    """[{turno, uf, url, sha512_url}] dos arquivos do ano."""
    fmt = formato_para(ano)
    pacote = pacote or fmt.nome_pacote()
    r = requests.get(CKAN, params={"id": pacote}, timeout=60)
    if r.status_code == 404:
        raise SystemExit(f"Conjunto '{pacote}' não encontrado no portal. "
                         "Confira o nome em dadosabertos.tse.jus.br e use --pacote.")
    r.raise_for_status()
    urls = [res.get("url", "") for res in r.json()["result"]["resources"]]
    hashes = {u[: -len(".sha512")] for u in urls if u.lower().endswith(".sha512")}
    # Se o TSE republicou um arquivo, o recurso mais recente costuma vir por último.
    recursos = {}
    for url in urls:
        m = fmt.padrao_arquivo.search(url)
        if m:
            chave = (int(m["turno"]), m["uf"].upper())
            recursos[chave] = {"turno": chave[0], "uf": chave[1], "url": url,
                               "sha512_url": url + ".sha512" if url in hashes else None}
    return [recursos[k] for k in sorted(recursos)]


def _conferir(arq: Path, sha512_url: str) -> None:
    esperado = requests.get(sha512_url, timeout=60).text.split()[0].lower()
    h = hashlib.sha512()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    if h.hexdigest() != esperado:
        arq.unlink()
        raise SystemExit(f"{arq.name}: SHA-512 não confere com o publicado; arquivo removido.")


def baixar(ano: int, destino: Path, turnos: list[int] | None = None,
           ufs: list[str] | None = None, pacote: str | None = None) -> list[Path]:
    destino = destino / str(ano)
    destino.mkdir(parents=True, exist_ok=True)
    ufs = [u.upper() for u in ufs] if ufs else None
    baixados = []
    for rec in listar(ano, pacote):
        if (turnos and rec["turno"] not in turnos) or (ufs and rec["uf"] not in ufs):
            continue
        arq = destino / rec["url"].rsplit("/", 1)[-1]
        if arq.exists():
            print(f"{arq.name}: já existe, pulando")
        else:
            print(f"baixando {arq.name} ...", flush=True)
            parcial = arq.with_name(arq.name + ".part")
            with requests.get(rec["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(parcial, "wb") as f:
                    for bloco in r.iter_content(1 << 20):
                        f.write(bloco)
            parcial.rename(arq)
            if rec["sha512_url"]:
                _conferir(arq, rec["sha512_url"])
        baixados.append(arq)
    if not baixados:
        print("Nenhum arquivo correspondeu aos filtros.")
    return baixados
