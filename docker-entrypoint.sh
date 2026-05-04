#!/usr/bin/env sh
set -e

# Run DB migrations on every container start/restart.
# Alembic uses a transaction + version table, so re-running is a no-op
# when already at head.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] Running alembic upgrade head..."
  alembic upgrade head
fi

# If a command was passed (e.g. worker), run it. Otherwise start the API.
if [ "$#" -gt 0 ]; then
  exec "$@"
else
  exec uvicorn main:app --host 0.0.0.0 --port 8000
fi
