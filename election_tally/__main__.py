"""Vote-count evolution from the TSE ballot reports (BU: polling-station ballot report).

  python -m election_tally pipeline --year 2024             download and load a whole year
  python -m election_tally download --year 2022 --state SP  download only
  python -m election_tally load --year 2022 data/raw/2022/*.zip
  python -m election_tally inspect --year 2018 file.zip
  python -m election_tally validate --cycle ele2022 --election 544 --office 1
  python -m election_tally download-municipalities  refresh the TSE -> IBGE code table
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x]


def _positive_int(s: str) -> int:
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="election_tally", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_options(sp, load=False, download=False):
        sp.add_argument("--year", type=int, required=True)
        if download:
            sp.add_argument("--round", type=_int_list,
                            help="e.g.: 1 or 1,2 (default: all)")
            sp.add_argument("--state", type=lambda s: s.split(","),
                            help="e.g.: SP,RJ,ZZ (default: all)")
            sp.add_argument("--dest", type=Path, default=Path("data/raw"))
            sp.add_argument("--package",
                            help="dataset id on the portal, if different from the default")
            sp.add_argument("--jobs", type=_positive_int, default=4,
                            help="parallel downloads (default: 4)")
        if load:
            sp.add_argument("--db", type=Path, default=Path("elections.duckdb"))
            sp.add_argument("--offices", type=_int_list,
                            help="only these office codes, e.g.: 1,3 (president, governor)")

    add_options(sub.add_parser("pipeline", help="download and load one year"),
                load=True, download=True)
    add_options(sub.add_parser("download", help="download the ballot report zips"),
                download=True)
    c = sub.add_parser("load", help="load zips/files into DuckDB")
    add_options(c, load=True)
    c.add_argument("files", type=Path, nargs="+")
    i = sub.add_parser("inspect", help="show the column mapping of a file")
    add_options(i)
    i.add_argument("file", type=Path)

    mu = sub.add_parser("download-municipalities",
                        help="refresh the TSE -> IBGE municipality code table from the TSE")
    mu.add_argument("--output", type=Path,
                    help="where to write it (default: the copy kept in the package)")

    v = sub.add_parser("validate", help="compare the sum of the ballot reports with the official JSON")
    v.add_argument("--db", type=Path, default=Path("elections.duckdb"))
    v.add_argument("--cycle", required=True, help="e.g.: ele2022")
    v.add_argument("--election", type=int, required=True,
                   help="e.g.: 544 (1st round, 2022 federal)")
    v.add_argument("--office", type=int, required=True, help="e.g.: 1 (president)")
    v.add_argument("--scope", default="br", help="br or a state abbreviation")
    v.add_argument("--json", type=Path, help="use an already downloaded official JSON")

    a = p.parse_args(argv)
    if a.cmd in ("pipeline", "download"):
        from .download import download
        files = download(a.year, a.dest, a.round, a.state, a.package, a.jobs)
        if a.cmd == "pipeline" and files:
            from .load import load
            load(files, a.db, a.year, a.offices)
    elif a.cmd == "load":
        from .load import load
        load(a.files, a.db, a.year, a.offices)
    elif a.cmd == "inspect":
        from .load import inspect
        inspect(a.file, a.year)
    elif a.cmd == "download-municipalities":
        from .municipalities import download as download_municipalities
        download_municipalities(a.output)
    elif a.cmd == "validate":
        from .validate import validate
        return 0 if validate(a.db, a.cycle, a.election, a.office, a.scope, a.json) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
