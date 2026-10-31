"""Discovers and downloads the polling-station ballot report (BU) zips from the TSE open data portal.

File names carry a timestamp (e.g. bweb_1t_MT_051020221321.zip), so the URL comes from the
portal's CKAN API. When the TSE publishes the .sha512 next to a file, the download is verified.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .formats import format_for

CKAN = "https://dadosabertos.tse.jus.br/api/3/action/package_show"


def list_resources(year: int, package: str | None = None) -> list[dict]:
    """[{round, state, url, sha512_url}] for the files of the year, oldest first per state."""
    fmt = format_for(year)
    package = package or fmt.package_name()
    r = requests.get(CKAN, params={"id": package}, timeout=60)
    if r.status_code == 404:
        raise SystemExit(f"Dataset '{package}' not found on the portal. "
                         "Check the name at dadosabertos.tse.jus.br and use --package.")
    r.raise_for_status()
    urls = [res.get("url", "") for res in r.json()["result"]["resources"]]
    hashes = {u[: -len(".sha512")] for u in urls if u.lower().endswith(".sha512")}
    # One state and round can have several files: in 2020, AP has one for the 15 municipalities
    # that voted in November and another for Macapa, postponed to December. Python's sort is
    # stable, so a republished file still comes after the original and replaces it on load.
    resources = {}
    # The TSE sometimes lists only a file's hash (2026, 2nd round, RR) though the file exists;
    # those go first so that a listed file takes precedence.
    for url in sorted(hashes.difference(urls)) + urls:
        m = fmt.file_pattern.search(url)
        if m:
            resources[url] = {"round": int(m["round"]), "state": m["state"].upper(), "url": url,
                               "sha512_url": url + ".sha512" if url in hashes else None}
    return sorted(resources.values(), key=lambda r: (r["round"], r["state"]))


class DownloadError(RuntimeError):
    """A file could not be downloaded or did not match its published SHA-512."""


def _verify_sha512(target: Path, sha512_url: str) -> None:
    expected = requests.get(sha512_url, timeout=60).text.split()[0].lower()
    h = hashlib.sha512()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        target.unlink()
        raise DownloadError("SHA-512 does not match the published one; file removed")


def _fetch(url: str, target: Path, sha512_url: str | None) -> Path:
    """Downloads one file to a .part name and renames it only when complete."""
    partial = target.with_name(target.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(partial, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)
    if sha512_url:
        _verify_sha512(target, sha512_url)
    return target


def download(year: int, dest: Path, rounds: list[int] | None = None,
             states: list[str] | None = None, package: str | None = None,
             jobs: int = 4) -> list[Path]:
    """Downloads the year's files, `jobs` at a time. Files already on disk are skipped."""
    if jobs < 1:
        raise ValueError("jobs must be at least 1")
    dest = dest / str(year)
    dest.mkdir(parents=True, exist_ok=True)
    states = [s.upper() for s in states] if states else None
    selected = [(rec, dest / rec["url"].rsplit("/", 1)[-1])
                for rec in list_resources(year, package)
                if not (rounds and rec["round"] not in rounds)
                and not (states and rec["state"] not in states)]
    if not selected:
        print("No file matched the filters.")
        return []
    pending = []
    for rec, target in selected:
        if target.exists():
            print(f"{target.name}: already exists, skipping")
        else:
            pending.append((rec, target))

    failures = []
    if pending:
        print(f"downloading {len(pending)} file(s), {min(jobs, len(pending))} at a time",
              flush=True)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_fetch, rec["url"], target, rec["sha512_url"]): target
                       for rec, target in pending}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    future.result()
                    print(f"{target.name}: done", flush=True)
                except Exception as exc:  # noqa: BLE001 - reported below, others keep going
                    failures.append(f"  {target.name}: {exc}")
    if failures:
        raise SystemExit("Some downloads failed (finished files are kept; run again to "
                         "retry the rest):\n" + "\n".join(sorted(failures)))
    return [target for _, target in selected]
