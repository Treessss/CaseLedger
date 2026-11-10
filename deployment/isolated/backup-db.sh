#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=/opt/caseledger-copy/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$BACKUP_DIR/auto-$STAMP.dump"
TEMPORARY="$TARGET.incomplete"

umask 077
mkdir -p "$BACKUP_DIR"
trap 'rm -f "$TEMPORARY"' EXIT
docker exec caseledger-copy-db-1 pg_dump -U caseledger -Fc caseledger > "$TEMPORARY"
test -s "$TEMPORARY"
mv "$TEMPORARY" "$TARGET"
trap - EXIT

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'auto-*.dump' -mtime +30 -delete
echo "CaseLedger-copy database backup completed: $TARGET"
