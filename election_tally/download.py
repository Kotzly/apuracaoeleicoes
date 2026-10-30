"""Discovers and downloads the polling-station ballot report (BU) zips from the TSE open data portal.

File names carry a timestamp (e.g. bweb_1t_MT_051020221321.zip), so the URL comes from the
portal's CKAN API. When the TSE publishes the .sha512 next to a file, the download is verified.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from .formats import format_for

CKAN = "https://dadosabertos.tse.jus.br/api/3/action/package_show"


def list_resources(year: int, package: str | None = None) -> list[dict]:
    """[{round, state, url, sha512_url}] for the files of the year."""
    fmt = format_for(year)
    package = package or fmt.package_name()
    r = requests.get(CKAN, params={"id": package}, timeout=60)
    if r.status_code == 404:
        raise SystemExit(f"Dataset '{package}' not found on the portal. "
                         "Check the name at dadosabertos.tse.jus.br and use --package.")
    r.raise_for_status()
    urls = [res.get("url", "") for res in r.json()["result"]["resources"]]
    hashes = {u[: -len(".sha512")] for u in urls if u.lower().endswith(".sha512")}
    # If the TSE republished a file, the most recent resource usually comes last.
    resources = {}
    for url in urls:
        m = fmt.file_pattern.search(url)
        if m:
            key = (int(m["round"]), m["state"].upper())
            resources[key] = {"round": key[0], "state": key[1], "url": url,
                              "sha512_url": url + ".sha512" if url in hashes else None}
    return [resources[k] for k in sorted(resources)]


def _verify_sha512(target: Path, sha512_url: str) -> None:
    expected = requests.get(sha512_url, timeout=60).text.split()[0].lower()
    h = hashlib.sha512()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        target.unlink()
        raise SystemExit(f"{target.name}: SHA-512 does not match the published one; file removed.")


def download(year: int, dest: Path, rounds: list[int] | None = None,
             states: list[str] | None = None, package: str | None = None) -> list[Path]:
    dest = dest / str(year)
    dest.mkdir(parents=True, exist_ok=True)
    states = [s.upper() for s in states] if states else None
    downloaded = []
    for rec in list_resources(year, package):
        if (rounds and rec["round"] not in rounds) or (states and rec["state"] not in states):
            continue
        target = dest / rec["url"].rsplit("/", 1)[-1]
        if target.exists():
            print(f"{target.name}: already exists, skipping")
        else:
            print(f"downloading {target.name} ...", flush=True)
            partial = target.with_name(target.name + ".part")
            with requests.get(rec["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(partial, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            partial.rename(target)
            if rec["sha512_url"]:
                _verify_sha512(target, rec["sha512_url"])
        downloaded.append(target)
    if not downloaded:
        print("No file matched the filters.")
    return downloaded
