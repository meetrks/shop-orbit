#!/usr/bin/env bash
# Restores a database dump (and optionally a media archive) produced by
# backup.sh. Destructive — requires --yes to skip the confirmation
# prompt, so it stays safe to run interactively but is still scriptable
# for a documented disaster-recovery drill. See docs/RESTORE.md.
#
# Usage:
#   scripts/restore.sh <db_backup_file.dump.gz> [media_backup_file.tar.gz] [--yes]
#
# Env vars:
#   APP_DIR=/srv/shoporbit
#   DATABASE_URL     Read from $APP_DIR/shared/.env if not already exported.

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
DB_BACKUP_FILE=""
MEDIA_BACKUP_FILE=""
ASSUME_YES=false

for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=true ;;
        *.dump.gz) DB_BACKUP_FILE="$arg" ;;
        *.tar.gz) MEDIA_BACKUP_FILE="$arg" ;;
    esac
done

if [[ -z "$DB_BACKUP_FILE" ]]; then
    echo "Usage: $0 <db_backup_file.dump.gz> [media_backup_file.tar.gz] [--yes]" >&2
    exit 1
fi

if [[ -z "${DATABASE_URL:-}" && -f "$APP_DIR/shared/.env" ]]; then
    # shellcheck disable=SC1090
    set -a && source <(grep -E '^DATABASE_URL=' "$APP_DIR/shared/.env") && set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL not set and not found in $APP_DIR/shared/.env." >&2
    exit 1
fi

if [[ "$ASSUME_YES" != true ]]; then
    echo "This will OVERWRITE the current database with $DB_BACKUP_FILE."
    read -r -p "Type 'yes' to continue: " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo "==> Restoring database from $DB_BACKUP_FILE"
gunzip -c "$DB_BACKUP_FILE" | pg_restore --dbname="$DATABASE_URL" --clean --if-exists

if [[ -n "$MEDIA_BACKUP_FILE" ]]; then
    echo "==> Restoring media from $MEDIA_BACKUP_FILE"
    tar -xzf "$MEDIA_BACKUP_FILE" -C "$APP_DIR/shared"
fi

echo "==> Verifying the restored database is reachable"
psql "$DATABASE_URL" -c "SELECT 1;" >/dev/null

echo "==> Restore complete."
