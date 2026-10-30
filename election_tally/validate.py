"""Compares the sum of the polling-station ballot reports (BU) with the official published result."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import requests

URL = ("https://resultados.tse.jus.br/oficial/{cycle}/{election}/dados-simplificados/"
       "{scope}/{scope}-c{office:04d}-e{election:06d}-r.json")


def official(cycle: str, election: int, office: int, scope: str,
             json_file: Path | None = None) -> dict[int, tuple[str, int]]:
    """{number: (name, votes)} from the official JSON (downloaded or from a local file)."""
    if json_file:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    else:
        url = URL.format(cycle=cycle, election=election, office=office, scope=scope.lower())
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    return {int(c["n"]): (c.get("nm", ""), int(c["vap"])) for c in data["cand"]}


def _sums(con, election: int, office: int, scope: str):
    state_filter = "" if scope.lower() == "br" else "AND state = ?"
    params = [election, office] + ([scope.upper()] if state_filter else [])
    totals = dict(con.execute(f"""
        SELECT candidate_number, sum(vote_count)::BIGINT FROM votes
        WHERE election_code = ? AND office_code = ? {state_filter}
        GROUP BY candidate_number
    """, params).fetchall())
    present = con.execute(
        "SELECT DISTINCT election_code, office_code FROM votes ORDER BY 1, 2").fetchall()
    return totals, present


def validate(db: Path, cycle: str, election: int, office: int,
             scope: str = "br", json_file: Path | None = None) -> bool:
    official_results = official(cycle, election, office, scope, json_file)
    with duckdb.connect(str(db), read_only=True) as con:
        totals, present = _sums(con, election, office, scope)
    if not totals:
        print(f"No votes for election {election}, office {office}. "
              f"Present in the database (election, office): {present}")
        return False

    ok = True
    print(f"{'no.':>6}  {'candidate':<30} {'official':>14} {'ballots':>14} {'difference':>10}")
    for n, (name, votes) in sorted(official_results.items(), key=lambda x: -x[1][1]):
        total = totals.get(n, 0)
        diff = total - votes
        ok &= diff == 0
        print(f"{n:>6}  {name[:30]:<30} {votes:>14,} {total:>14,} {diff:>+10,}")
    print("OK: ballot totals match the official results." if ok else
          "MISMATCH: some states are not loaded, or there are ballots outside the file.")
    return ok
