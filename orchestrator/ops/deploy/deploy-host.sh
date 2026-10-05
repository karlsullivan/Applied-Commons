#!/usr/bin/env bash
# Deploy (or redeploy) the Applied Commons API and its PostgreSQL database
# as containers on a single host. Idempotent: re-running keeps the
# database, its data and its password, re-applies the schema and
# migrations (all IF NOT EXISTS), and replaces the API container with one
# built from the checked-out commit.
#
# Runs as a user in the docker group. Defaults match orchestrator-01:
#   AC_DATA      /srv/orchestrator/tenants/applied-commons/data
#   AC_API_PORT  8100   (published on 127.0.0.1 only; Apollo uses 8000)
#
# Rollback: docker rm -f ac-api ac-postgres  (data stays in $AC_DATA)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"          # .../orchestrator
DATA="${AC_DATA:-/srv/orchestrator/tenants/applied-commons/data}"
PORT="${AC_API_PORT:-8100}"
NET=applied-commons
ENV_FILE="$DATA/ac.env"
TAG="$(git -C "$SRC" rev-parse --short HEAD)"

mkdir -p "$DATA/postgres"
if [ ! -f "$ENV_FILE" ]; then
    (
        umask 077
        printf 'POSTGRES_USER=applied_commons\nPOSTGRES_DB=applied_commons\nPOSTGRES_PASSWORD=%s\n' \
            "$(openssl rand -hex 24)" > "$ENV_FILE"
    )
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

if ! docker container inspect ac-postgres >/dev/null 2>&1; then
    docker run -d --name ac-postgres --network "$NET" --restart unless-stopped \
        --env-file "$ENV_FILE" \
        -v "$DATA/postgres:/var/lib/postgresql/data" \
        postgres:16-alpine >/dev/null
fi
for _ in $(seq 60); do
    docker exec ac-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 && break
    sleep 1
done

for sql in "$SRC/database/schema.sql" "$SRC"/database/migrations/*.sql; do
    docker exec -i -e PGOPTIONS=--client-min-messages=warning ac-postgres \
        psql -v ON_ERROR_STOP=1 -q \
        -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$sql"
done

docker build -q -t "applied-commons-api:$TAG" "$SRC/app" >/dev/null
docker rm -f ac-api >/dev/null 2>&1 || true
docker run -d --name ac-api --network "$NET" --restart unless-stopped \
    -e "DATABASE_URL=postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@ac-postgres/$POSTGRES_DB" \
    -p "127.0.0.1:$PORT:8000" \
    "applied-commons-api:$TAG" >/dev/null

for _ in $(seq 60); do
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        echo "applied-commons-api:$TAG healthy on 127.0.0.1:$PORT"
        exit 0
    fi
    sleep 1
done
echo "API did not become healthy; see: docker logs ac-api" >&2
exit 1
