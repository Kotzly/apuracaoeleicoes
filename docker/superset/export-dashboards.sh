#!/bin/sh
# Save every asset of the running Superset back into the repo: dashboards, charts, datasets,
# connections and saved queries, even the ones no dashboard uses. They survive the next start
# because bootstrap.sh re-imports superset/dashboards/, overwriting edits made in the interface.
#
# Run from anywhere:  docker/superset/export-dashboards.sh
set -e
cd "$(dirname "$0")/.."

docker compose exec -T superset sh -c '
rm -rf /tmp/export
python - <<EOF
import os, pathlib
from flask import g
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset import security_manager
    from superset.commands.export.assets import ExportAssetsCommand

    g.user = security_manager.find_user(username=os.environ["SUPERSET_ADMIN_USERNAME"])
    files = [(name, content() if callable(content) else content)
             for name, content in ExportAssetsCommand().run()]
out = pathlib.Path("/tmp/export")
for name, content in files:
    target = out / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
EOF'

# File names carry internal ids that change between exports: replace, don't merge. The folder
# itself stays, because it is mounted in the container, and is touched only after a full export.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
docker compose exec -T superset tar -C /tmp/export -cf - . | tar -C "$tmp" --no-same-owner -xf -
[ -f "$tmp/metadata.yaml" ] || { echo "Export failed: nothing changed." >&2; exit 1; }
find superset/dashboards -mindepth 1 -delete
cp -R "$tmp/." superset/dashboards/
echo "Superset assets saved to docker/superset/dashboards/. Review with git diff and commit."
