#!/bin/sh
# Save the dashboards from the running Superset back into the repo, so they survive
# the next start (bootstrap.sh re-imports superset/dashboards/ and overwrites edits).
#
# Run from anywhere:  docker/superset/export-dashboards.sh
set -e
cd "$(dirname "$0")/.."

docker compose exec -T superset sh -c '
rm -rf /tmp/dashboards.zip /tmp/export
superset export-dashboards --dashboard-file /tmp/dashboards.zip > /dev/null
python - <<EOF
import pathlib, zipfile
out = pathlib.Path("/tmp/export")
with zipfile.ZipFile("/tmp/dashboards.zip") as z:
    for name in z.namelist():
        # drop the top-level "dashboard_export_<timestamp>/" folder
        target = out.joinpath(*pathlib.PurePosixPath(name).parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(name))
EOF'

# File names carry internal ids that change between exports: replace, don't merge.
rm -rf superset/dashboards
docker compose cp superset:/tmp/export/. superset/dashboards
echo "Dashboards saved to docker/superset/dashboards/. Review with git diff and commit."
