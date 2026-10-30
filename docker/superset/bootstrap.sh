#!/bin/sh
# Container entrypoint: prepare Superset, load the dashboards kept in the repo,
# then start the regular Superset server.
set -e

DASHBOARDS_DIR="${DASHBOARDS_DIR:-/app/dashboards}"
ELECTIONS_DB="${ELECTIONS_DB:-/data/elections.duckdb}"
ADMIN_USERNAME="${SUPERSET_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${SUPERSET_ADMIN_PASSWORD:-admin}"

echo "== Upgrading Superset metadata database"
superset db upgrade

echo "== Creating admin user '${ADMIN_USERNAME}' (skipped if it already exists)"
superset fab create-admin \
    --username "${ADMIN_USERNAME}" --password "${ADMIN_PASSWORD}" \
    --firstname Admin --lastname User --email "${ADMIN_USERNAME}@example.com" \
    || echo "   admin not created"

echo "== Initializing roles and permissions"
superset init

if [ -d "${DASHBOARDS_DIR}" ] && [ -f "${DASHBOARDS_DIR}/metadata.yaml" ]; then
    echo "== Importing dashboards from ${DASHBOARDS_DIR} (overwrites earlier imports)"
    # superset import-dashboards takes a ZIP whose files sit under one top-level folder.
    python - "${DASHBOARDS_DIR}" /tmp/dashboards.zip <<'EOF'
import pathlib, sys, zipfile
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w") as z:
    for f in sorted(src.rglob("*.yaml")):
        z.write(f, pathlib.Path("dashboards") / f.relative_to(src))
EOF
    superset import-dashboards --path /tmp/dashboards.zip --username "${ADMIN_USERNAME}"
else
    echo "== No dashboards found in ${DASHBOARDS_DIR}; skipping import"
fi

if [ ! -f "${ELECTIONS_DB}" ]; then
    echo "WARNING: ${ELECTIONS_DB} not found. Build it with the pipeline and mount it;" \
         "the charts will fail until then."
fi

echo "== Starting Superset"
exec /app/docker/entrypoints/run-server.sh
