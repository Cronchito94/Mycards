#!/usr/bin/env bash
# Attend Postgres, applique les migrations, puis lance la commande demandée.
set -euo pipefail

echo "[entrypoint] Attente de PostgreSQL..."
python - <<'PY'
import os, sys, time
import psycopg

dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
deadline = time.time() + 60
last = None
while time.time() < deadline:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            print("[entrypoint] PostgreSQL est joignable.")
            sys.exit(0)
    except Exception as exc:
        last = exc
        time.sleep(1)
print(f"[entrypoint] PostgreSQL injoignable apres 60s: {last}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] Application des migrations Alembic..."
alembic upgrade head

echo "[entrypoint] Demarrage: $*"
exec "$@"
