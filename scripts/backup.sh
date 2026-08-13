#!/usr/bin/env bash
# Backs up the production database (pg_dump, custom format, gzip'd) and
# the media directory (tar.gz), timestamped, and prunes backups older
# than the retention window. Meant to run from a daily cron/systemd timer
# on the server — see docs/BACKUP.md.
#
# Env vars (all optional, sensible defaults shown):
#   APP_DIR=/srv/shoporbit
#   BACKUP_DIR=$APP_DIR/backups
#   RETENTION_DAYS=14
#   DATABASE_URL     Read from $APP_DIR/shared/.env if not already exported.

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [[ -z "${DATABASE_URL:-}" && -f "$APP_DIR/shared/.env" ]]; then
    # shellcheck disable=SC1090
    set -a && source <(grep -E '^DATABASE_URL=' "$APP_DIR/shared/.env") && set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL not set and not found in $APP_DIR/shared/.env." >&2
    exit 1
fi

DB_BACKUP_FILE="$BACKUP_DIR/db_$TIMESTAMP.dump.gz"
MEDIA_BACKUP_FILE="$BACKUP_DIR/media_$TIMESTAMP.tar.gz"

echo "==> Dumping database to $DB_BACKUP_FILE"
pg_dump --dbname="$DATABASE_URL" --format=custom | gzip > "$DB_BACKUP_FILE"

echo "==> Verifying the dump is readable"
gunzip -t "$DB_BACKUP_FILE"

if [[ -d "$APP_DIR/shared/media" ]]; then
    echo "==> Archiving media to $MEDIA_BACKUP_FILE"
    tar -czf "$MEDIA_BACKUP_FILE" -C "$APP_DIR/shared" media
fi

echo "==> Pruning backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -name '*.dump.gz' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name '*.tar.gz' -mtime "+$RETENTION_DAYS" -delete

echo "==> Backup complete: $DB_BACKUP_FILE"
