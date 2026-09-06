#!/usr/bin/env bash

set -euo pipefail

BACKUP_DIR="/srv/orchestrator/shared/backups/applied-commons"
LOG="/srv/orchestrator/shared/logs/backup.log"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

DB_TMP="${BACKUP_DIR}/applied_commons_${TIMESTAMP}.dump.tmp"
DB_FINAL="${BACKUP_DIR}/applied_commons_${TIMESTAMP}.dump"

echo "===== ${TIMESTAMP} =====" >> "$LOG"

mkdir -p "$BACKUP_DIR"

echo "Backing up PostgreSQL..." >> "$LOG"

docker exec ac-postgres \
    pg_dump \
    -U ac_service \
    -d applied_commons \
    -Fc \
    --no-owner \
    --no-acl \
    > "$DB_TMP"

mv "$DB_TMP" "$DB_FINAL"
chmod 600 "$DB_FINAL"

echo "Database backup created: ${DB_FINAL}" >> "$LOG"

echo "Backing up deployment configuration..." >> "$LOG"

tar -czf "${BACKUP_DIR}/config_${TIMESTAMP}.tar.gz" \
    -C /srv/orchestrator/shared/compose \
    applied-commons/compose.yml \
    applied-commons/.env

chmod 600 "${BACKUP_DIR}/config_${TIMESTAMP}.tar.gz"

# Keep 14 days of local backups.
find "$BACKUP_DIR" \
    -type f \
    \( -name '*.dump' -o -name 'config_*.tar.gz' \) \
    -mtime +14 \
    -delete

echo "Backup completed successfully." >> "$LOG"
echo >> "$LOG"
