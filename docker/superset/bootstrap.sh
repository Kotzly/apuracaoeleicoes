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
    # The Cargos dashboard is not versioned, and the mounted folder is read-only: generate it on a copy.
    IMPORT_DIR="$(mktemp -d)"
    cp -R "${DASHBOARDS_DIR}/." "${IMPORT_DIR}/"
    echo "== Generating the Cargos dashboard"
    python /app/build_office_dashboard.py --assets "${IMPORT_DIR}"
    echo "== Importing dashboards from ${IMPORT_DIR} (overwrites earlier imports)"
    python /app/import_assets.py "${IMPORT_DIR}" "${ADMIN_USERNAME}"
    rm -rf "${IMPORT_DIR}"
else
    echo "== No dashboards found in ${DASHBOARDS_DIR}; skipping import"
fi

if [ ! -f "${ELECTIONS_DB}" ]; then
    echo "WARNING: ${ELECTIONS_DB} not found. Build it with the pipeline and mount it;" \
         "the charts will fail until then."
fi

echo "== Starting Superset"
exec /app/docker/entrypoints/run-server.sh
