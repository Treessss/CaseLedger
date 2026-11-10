#!/bin/bash
# Database backup helper.
#   PostgreSQL -> pg_dump custom format (compressed, restore with pg_restore)
#   SQLite     -> sqlite3 .backup (safe while the app is running, unlike cp)
#
# Usage:  ./scripts/backup_db.sh [retention_days]
# Cron:   0 3 * * * /opt/caseledger/scripts/backup_db.sh 30 >> /var/log/caseledger-backup.log 2>&1

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RETENTION_DAYS="${1:-30}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

# Read DATABASE_URL from .env (fall back to the process environment)
if [ -f "$APP_DIR/.env" ]; then
    DATABASE_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | head -1 | cut -d'=' -f2- || true)"
fi
DATABASE_URL="${DATABASE_URL:-}"

if [ -z "$DATABASE_URL" ]; then
    echo "[backup] DATABASE_URL is empty, assuming the default SQLite file"
    DATABASE_URL="sqlite:///$APP_DIR/instance/caseLedger.db"
fi

case "$DATABASE_URL" in
    postgres://*|postgresql://*)
        command -v pg_dump >/dev/null 2>&1 || { echo "[backup] pg_dump not found"; exit 1; }
        TARGET="$BACKUP_DIR/auto_caseledger_pg_$STAMP.dump"
        echo "[backup] pg_dump -> $TARGET"
        pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > "$TARGET"
        ;;
    sqlite://*)
        DB_PATH="${DATABASE_URL#sqlite:///}"
        DB_PATH="${DB_PATH#sqlite://}"
        [ -f "$DB_PATH" ] || { echo "[backup] SQLite file not found: $DB_PATH"; exit 1; }
        TARGET="$BACKUP_DIR/auto_caseLedger_$STAMP.db"
        echo "[backup] sqlite3 .backup -> $TARGET"
        if command -v sqlite3 >/dev/null 2>&1; then
            sqlite3 "$DB_PATH" ".backup '$TARGET'"
        else
            echo "[backup] sqlite3 not found, falling back to cp (stop the app first)"
            cp "$DB_PATH" "$TARGET"
        fi
        ;;
    *)
        echo "[backup] unsupported DATABASE_URL: $DATABASE_URL"
        exit 1
        ;;
esac

echo "[backup] done: $(du -h "$TARGET" | cut -f1)"

# Remove backups older than the retention window.
# Only files created by this script (auto_ prefix) are pruned, so manual
# backups you keep by hand are never touched.
if [ "$RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'auto_caseledger_pg_*.dump' -o -name 'auto_caseLedger_*.db' \) \
        -mtime "+$RETENTION_DAYS" -print -delete | sed 's/^/[backup] removed old: /'
fi
