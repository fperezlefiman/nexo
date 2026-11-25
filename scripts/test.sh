#!/usr/bin/env bash
# Full test suite. The queue tests need a real Postgres, so this script creates a throwaway
# database, applies the schema and runs everything with NEXO_TEST_DATABASE_URL set.
#
#   ./scripts/test.sh            # suite completa
#   ./scripts/test.sh -k queue   # cualquier argumento se pasa a pytest
set -euo pipefail

cd "$(dirname "$0")/.."
CONTAINER="${NEXO_TEST_CONTAINER:-nexo-postgres}"
DB_URL_FILE="${NEXO_DATABASE_URL_FILE:-$HOME/.config/nexo/database_url}"

if [[ ! -f "$DB_URL_FILE" ]]; then
  echo "falta el archivo con la URL: $DB_URL_FILE" >&2
  exit 1
fi

BASE_URL=$(cat "$DB_URL_FILE")
TEST_URL="${BASE_URL%/*}/nexo_test"

if ! docker exec "$CONTAINER" psql -U nexo -d nexo -tAc \
      "SELECT 1 FROM pg_database WHERE datname='nexo_test'" | grep -q 1; then
  docker exec "$CONTAINER" psql -U nexo -d nexo -qc "CREATE DATABASE nexo_test"
fi

# El esquema se aplica siempre: es idempotente y evita correr contra un esquema viejo.
NEXO_DATABASE_URL="$TEST_URL" ./.venv/bin/nexo migrate >/dev/null

NEXO_TEST_DATABASE_URL="$TEST_URL" exec ./.venv/bin/python -m pytest "$@"
