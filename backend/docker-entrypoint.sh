#!/bin/sh
# Container start-up: wait for Postgres, apply migrations, seed lookups, then
# hand over to the CMD (gunicorn in prod, runserver in the dev override).
#
# Skip the prep steps with SKIP_MIGRATE=1 (e.g. for one-off `manage.py` runs).
set -eu

# In dev the working tree is bind-mounted over the image, hiding the baked static dir.
mkdir -p staticfiles media

if [ -n "${DATABASE_URL:-}" ]; then
  echo "[entrypoint] waiting for database..."
  i=0
  until pg_isready -d "$DATABASE_URL" -q; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
      echo "[entrypoint] database not reachable after 60s" >&2
      exit 1
    fi
    sleep 2
  done
fi

if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
  echo "[entrypoint] applying migrations"
  python manage.py migrate --noinput
  echo "[entrypoint] seeding lookup tables (idempotent)"
  python manage.py seed_lookups
  echo "[entrypoint] embedding new/changed products for search"
  python manage.py embed_products
fi

exec "$@"
