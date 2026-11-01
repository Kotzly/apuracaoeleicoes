"""Vote-count evolution from the TSE ballot reports (BU: polling-station ballot report).

  python -m election_tally pipeline --year 2024             download and load a whole year
  python -m election_tally download --year 2022 --state SP  download only
  python -m election_tally load --year 2022 data/raw/2022/*.zip
  python -m election_tally load --synthetic         small made-up dataset, in synthetic.duckdb
  python -m election_tally inspect --year 2018 file.zip
  python -m election_tally validate --cycle ele2022 --election 544 --office 1
  python -m election_tally download-municipalities  refresh the TSE -> IBGE code table
  python -m election_tally download-municipality-shapes  refresh the IBGE municipality outlines
  python -m election_tally build-derived            build derived tables (see the README)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_DEFAULT_DB = Path("elections.duckdb")
_DEFAULT_DEST = Path("data/raw")
_DEFAULT_JOBS = 4


def _int_list(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x]


def _positive_int(s: str) -> int:
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _check_synthetic(parser: argparse.ArgumentParser, a: argparse.Namespace) -> None:
    """--synthetic stands alone; without it, the year (and for `load`, the files) are required."""
    if not a.synthetic:
        if a.seed is not None:
            parser.error("--seed needs --synthetic")
        if a.year is None:
            parser.error("--year is required (unless with --synthetic)")
        if a.cmd == "load" and not a.files:
            parser.error("load needs files (unless with --synthetic)")
        return
    given = [name for name in ("year", "round", "state", "dest", "package", "jobs", "offices",
                               "files", "skip_line_check") if getattr(a, name, None)]
    if given:
        parser.error(f"--synthetic does not combine with: {', '.join(given)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="election_tally", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_options(sp, load=False, download=False, synthetic=False):
        # With --synthetic the year is not given; `_check_synthetic` requires it otherwise.
        sp.add_argument("--year", type=int, required=not synthetic)
        if synthetic:
            sp.add_argument("--synthetic", action="store_true",
                            help="load a small made-up dataset instead of TSE files (see the README)")
            sp.add_argument("--seed", type=int,
                            help="with --synthetic: the same seed gives the same data")
        if download:
            sp.add_argument("--round", type=_int_list,
                            help="e.g.: 1 or 1,2 (default: all)")
            sp.add_argument("--state", type=lambda s: s.split(","),
                            help="e.g.: SP,RJ,ZZ (default: all)")
            sp.add_argument("--dest", type=Path, help="default: data/raw")
            sp.add_argument("--package",
                            help="dataset id on the portal, if different from the default")
            sp.add_argument("--jobs", type=_positive_int,
                            help="parallel downloads (default: 4)")
        if load:
            sp.add_argument("--db", type=Path,
                            help="default: elections.duckdb, or synthetic.duckdb with --synthetic")
            sp.add_argument("--offices", type=_int_list,
                            help="only these office codes, e.g.: 1,3 (president, governor)")
            sp.add_argument("--skip-line-check", action="store_true",
                            help="faster, but no warning if the parser drops or merges lines")

    add_options(sub.add_parser("pipeline", help="download and load one year"),
                load=True, download=True, synthetic=True)
    add_options(sub.add_parser("download", help="download the ballot report zips"),
                download=True)
    c = sub.add_parser("load", help="load zips/files into DuckDB")
    add_options(c, load=True, synthetic=True)
    c.add_argument("files", type=Path, nargs="*")
    i = sub.add_parser("inspect", help="show the column mapping of a file")
    add_options(i)
    i.add_argument("file", type=Path)

    mu = sub.add_parser("download-municipalities",
                        help="refresh the TSE -> IBGE municipality code table from the TSE")
    mu.add_argument("--output", type=Path,
                    help="where to write it (default: the copy kept in the package)")

    sh = sub.add_parser("download-municipality-shapes",
                        help="refresh the IBGE municipality outlines used by the maps")
    sh.add_argument("--output", type=Path,
                    help="where to write it (default: the copy kept in the package)")

    de = sub.add_parser("build-derived",
                        help="build the tables computed from the loaded data, if out of date")
    de.add_argument("--db", type=Path, default=Path("elections.duckdb"))
    de.add_argument("--force", action="store_true", help="rebuild even if up to date")

    v = sub.add_parser("validate", help="compare the sum of the ballot reports with the official JSON")
    v.add_argument("--db", type=Path, default=Path("elections.duckdb"))
    v.add_argument("--cycle", required=True, help="e.g.: ele2022")
    v.add_argument("--election", type=int, required=True,
                   help="e.g.: 544 (1st round, 2022 federal)")
    v.add_argument("--office", type=int, required=True, help="e.g.: 1 (president)")
    v.add_argument("--scope", default="br", help="br or a state abbreviation")
    v.add_argument("--json", type=Path, help="use an already downloaded official JSON")

    a = p.parse_args(argv)
    if a.cmd in ("pipeline", "load"):
        _check_synthetic(p, a)
        if a.synthetic:
            from .synthetic import DEFAULT_DB, DEFAULT_SEED, generate
            generate(a.db or DEFAULT_DB, DEFAULT_SEED if a.seed is None else a.seed)
            return 0
    db = getattr(a, "db", None) or _DEFAULT_DB
    if a.cmd in ("pipeline", "download"):
        from .download import download
        files = download(a.year, a.dest or _DEFAULT_DEST, a.round, a.state, a.package,
                         a.jobs or _DEFAULT_JOBS)
        if a.cmd == "pipeline" and files:
            from .load import load
            load(files, db, a.year, a.offices, not a.skip_line_check)
    elif a.cmd == "load":
        from .load import load
        load(a.files, db, a.year, a.offices, not a.skip_line_check)
    elif a.cmd == "inspect":
        from .load import inspect
        inspect(a.file, a.year)
    elif a.cmd == "download-municipalities":
        from .municipalities import download as download_municipalities
        download_municipalities(a.output)
    elif a.cmd == "download-municipality-shapes":
        from .shapes import download as download_shapes
        download_shapes(a.output)
    elif a.cmd == "build-derived":
        # docker compose runs this before Superset starts; add new derived tables here.
        from . import elected, leader_flows, lookups, parties, results, shapes, shifts, tse_tables
        leader_flows.build(a.db, force=a.force)
        tse_tables.build(a.db, force=a.force)
        lookups.build(a.db, force=a.force)
        results.build(a.db, force=a.force)
        shapes.build(a.db, force=a.force)
        # These depend on the results (and on candidates), so they come last.
        parties.build(a.db, force=a.force)
        shifts.build(a.db, force=a.force)
        elected.build(a.db, force=a.force)
    elif a.cmd == "validate":
        from .validate import validate
        return 0 if validate(a.db, a.cycle, a.election, a.office, a.scope, a.json) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
