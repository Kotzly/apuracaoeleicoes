"""Superset configuration for the election-tally dashboards.

Loaded through SUPERSET_CONFIG_PATH (set in the Dockerfile).
"""

import os

# Required: Superset refuses to start with the default key.
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Superset's own metadata (users, charts, dashboards) lives in SQLite on a volume.
# Good enough for a single-instance local dashboard; switch to Postgres for a shared server.
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SUPERSET_METADATA_URI", "sqlite:////app/superset_home/superset.db"
)

# The dashboards read a local DuckDB file mounted into the container (read-only).
PREVENT_UNSAFE_DB_CONNECTIONS = False

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": True,
}

# The evolution chart returns one row per minute per candidate.
ROW_LIMIT = 50000

# Single container: keep rate-limit counters in memory (silences Flask-Limiter's warning).
RATELIMIT_STORAGE_URI = "memory://"
