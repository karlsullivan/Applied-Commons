#!/usr/bin/env bash

set -u

TIMESTAMP="$(date --iso-8601=seconds)"
LOG="/srv/orchestrator/shared/logs/health.log"

echo "===== ${TIMESTAMP} =====" >> "$LOG"

echo "--- MEMORY ---" >> "$LOG"
free -h >> "$LOG"

echo "--- DISK ---" >> "$LOG"
df -h / /srv/orchestrator >> "$LOG"

echo "--- DOCKER ---" >> "$LOG"
docker ps \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' \
  >> "$LOG"

echo "--- APPLIED COMMONS API ---" >> "$LOG"
if curl -fsS http://127.0.0.1:8000/health >> "$LOG"; then
    echo >> "$LOG"
else
    echo "ERROR: Applied Commons API unavailable" >> "$LOG"
fi

echo "--- QWEN ---" >> "$LOG"
if curl -fsS http://127.0.0.1:8081/health >> "$LOG"; then
    echo >> "$LOG"
else
    echo "ERROR: Qwen unavailable" >> "$LOG"
fi

echo "--- POSTGRES ---" >> "$LOG"
if docker exec ac-postgres \
    pg_isready -U ac_service -d applied_commons >> "$LOG" 2>&1
then
    :
else
    echo "ERROR: PostgreSQL unavailable" >> "$LOG"
fi

echo "--- JOB QUEUE ---" >> "$LOG"

docker exec ac-postgres \
  psql -U ac_service -d applied_commons -Atc "
    SELECT
      'queued=' || COUNT(*) FILTER (WHERE status='queued') ||
      ' running=' || COUNT(*) FILTER (WHERE status='running') ||
      ' completed=' || COUNT(*) FILTER (WHERE status='completed') ||
      ' failed=' || COUNT(*) FILTER (WHERE status='failed') ||
      ' stale_leases=' || COUNT(*) FILTER (
          WHERE status='running'
            AND lease_until IS NOT NULL
            AND lease_until < NOW()
      )
    FROM jobs;
  " >> "$LOG"

echo >> "$LOG"
