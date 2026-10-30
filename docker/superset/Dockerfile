# Superset with the DuckDB driver, for the election-tally dashboards.
ARG SUPERSET_VERSION=6.1.0
FROM apache/superset:${SUPERSET_VERSION}

USER root

# The published image ships without database drivers. Keep duckdb on 1.x: a database
# file written by DuckDB 2.x cannot be opened by 1.x, and the pipeline
# (pyproject.toml) is pinned to the same range.
RUN . /app/.venv/bin/activate && \
    uv pip install --no-cache "duckdb>=1.4.2,<2" "duckdb-engine>=0.17"

COPY --chown=superset superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset --chmod=755 bootstrap.sh /app/bootstrap.sh
ENV SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py

USER superset
CMD ["/app/bootstrap.sh"]
