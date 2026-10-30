"""Import the dashboards kept in the repo, overwriting what already exists.

`superset import-dashboards` only overwrites the dashboard itself: databases, datasets
and charts that already exist are kept, so changes to them in the repo would never
reach an existing installation. ImportAssetsCommand overwrites all of them.

Usage: python import_assets.py DASHBOARDS_DIR USERNAME
"""

import pathlib
import sys

import yaml
from flask import g

from superset.app import create_app

src, username = pathlib.Path(sys.argv[1]), sys.argv[2]
contents = {p.relative_to(src).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(src.rglob("*.yaml"))}
# Exports are tagged "Dashboard"; the assets importer requires "assets".
metadata = yaml.safe_load(contents["metadata.yaml"])
metadata["type"] = "assets"
contents["metadata.yaml"] = yaml.safe_dump(metadata)

app = create_app()
with app.app_context():
    from superset import security_manager
    from superset.commands.importers.v1.assets import ImportAssetsCommand

    g.user = security_manager.find_user(username=username)
    ImportAssetsCommand(contents).run()
print(f"Imported {len(contents) - 1} files from {src}")
