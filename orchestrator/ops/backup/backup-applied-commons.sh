#!/usr/bin/env bash
# Nightly backup of Applied Commons on orchestrator-01 (applied-commons-
# backup.timer, as root: it needs docker).
#
# - The database: pg_dump custom format from the ac-postgres container,
#   checked by listing it with pg_restore before it is kept.
# - The private site profiles ($AC_DATA/sites; never in this repository).
#
# Kept in $AC_BACKUP_DIR (default /var/backups/applied-commons, root only,
# outside every path agents can write) for $AC_BACKUP_DAYS days (14).
# Design archives ($AC_EXPORTS) are not copied locally (they are pinned
# copies of public sources); they go off-site with the dumps.
#
# Off-site: when AC_BACKUP_OFFSITE is set (in /etc/applied-commons/
# backup.env, e.g. user@host:/path for rsync over ssh), the dumps, site
# profiles and design archives are copied there after each backup.
#
# On failure, one push message (the ntfy topic in /etc/apollo/alerts.env)
# and a non-zero exit, so the unit shows as failed.
#
# Restore (into a running, empty ac-postgres; then re-run deploy-host.sh):
#   docker exec -i ac-postgres pg_restore -U applied_commons -d applied_commons \
#       --no-owner --no-acl --clean --if-exists < applied_commons_<stamp>.dump
set -euo pipefail

DATA="${AC_DATA:-/srv/orchestrator/tenants/applied-commons/data}"
EXPORTS="${AC_EXPORTS:-/srv/orchestrator/tenants/applied-commons/exports}"
DIR="${AC_BACKUP_DIR:-/var/backups/applied-commons}"
DAYS="${AC_BACKUP_DAYS:-14}"
OFFSITE="${AC_BACKUP_OFFSITE:-}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

alert() {
    local url="${APOLLO_ALERTS_NTFY_URL:-}"
    [ -n "$url" ] || return 0
    curl -fsS -m 10 -H "Title: Apollo: backup failed" -H "Priority: 4" -H "Tags: warning" \
        ${APOLLO_ALERTS_NTFY_TOKEN:+-H "Authorization: Bearer $APOLLO_ALERTS_NTFY_TOKEN"} \
        -d "Applied Commons backup failed at $STAMP; see journalctl -u applied-commons-backup" \
        "$url" >/dev/null || true
}
trap 'echo "backup failed (line $LINENO)" >&2; alert' ERR

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"

user="$(docker exec ac-postgres printenv POSTGRES_USER)"
db="$(docker exec ac-postgres printenv POSTGRES_DB)"
tmp="$DIR/.applied_commons_$STAMP.dump.tmp"
docker exec ac-postgres pg_dump -U "$user" -d "$db" -Fc --no-owner --no-acl > "$tmp"
tables="$(docker exec -i ac-postgres pg_restore --list < "$tmp" | grep -c ' TABLE DATA ' || true)"
[ "$tables" -gt 0 ] || { echo "dump lists no tables" >&2; false; }
mv "$tmp" "$DIR/applied_commons_$STAMP.dump"
echo "database: $DIR/applied_commons_$STAMP.dump ($(du -h "$DIR/applied_commons_$STAMP.dump" | cut -f1), $tables tables)"

if [ -r "$DATA/sites" ]; then
    tar -czf "$DIR/sites_$STAMP.tar.gz" -C "$DATA" sites
    echo "site profiles: $DIR/sites_$STAMP.tar.gz"
fi

find "$DIR" -maxdepth 1 -type f \( -name 'applied_commons_*.dump' -o -name 'sites_*.tar.gz' \) \
    -mtime +"$DAYS" -delete
find "$DIR" -maxdepth 1 -type f -name '.*.tmp' -mmin +120 -delete

if [ -n "$OFFSITE" ]; then
    rsync -a --delete-after "$DIR/" "$OFFSITE/backups/"
    [ ! -d "$EXPORTS" ] || rsync -a "$EXPORTS/" "$OFFSITE/exports/"
    echo "off-site: copied to $OFFSITE"
else
    echo "off-site: not configured (AC_BACKUP_OFFSITE)"
fi
