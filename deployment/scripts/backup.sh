#!/usr/bin/env bash
# =============================================================================
# ULTRON - backup (T195)
# -----------------------------------------------------------------------------
# Dumps the PostgreSQL database and archives the persistent directories. Redis
# is not backed up on purpose: it holds locks, queues and transient state, and
# restoring a stale Redis can resurrect a lock for a task that no longer exists.
# PostgreSQL is the source of truth (A§22); everything else here is derived.
#
# Usage:
#   deployment/scripts/backup.sh [destination-dir]
# =============================================================================

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

DEST="${1:-$ULTRON_BACKUP_DIR}"
STAMP="$(ultron_timestamp)"
ARCHIVE="$DEST/ultron-$STAMP.tar.gz"

ultron_require pg_dump
ultron_require tar
ultron_require gzip

DATABASE_URL="$(ultron_env DATABASE_URL || true)"
[ -n "$DATABASE_URL" ] || ultron_die "DATABASE_URL not found in $ULTRON_ENV_FILE"

mkdir -p "$DEST"
# Stage everything under one directory so the archive extracts as ultron-<stamp>/.
STAGING="$(mktemp -d)"
ROOT_NAME="ultron-$STAMP"
PAYLOAD="$STAGING/$ROOT_NAME"
mkdir -p "$PAYLOAD"
trap 'rm -rf "$STAGING"' EXIT

echo "ULTRON: dumping database"
# --clean --if-exists makes the dump self-contained for restore.
pg_dump --clean --if-exists --no-owner --no-privileges \
    --dbname="$DATABASE_URL" > "$PAYLOAD/database.sql"

echo "ULTRON: staging persistent directories"
# Only what ULTRON owns. data/backups is excluded so a backup never contains a
# previous backup and grows without bound.
for dir in workspaces data logs; do
    source_dir="$ULTRON_ROOT/$dir"
    if [ -d "$source_dir" ]; then
        mkdir -p "$PAYLOAD/$dir"
        # Exclude the backups directory wherever it sits under data/.
        if [ "$dir" = "data" ] && [ -d "$source_dir/backups" ]; then
            tar --create --file=- --exclude='backups' --directory="$source_dir" . \
                | tar --extract --file=- --directory="$PAYLOAD/$dir"
        else
            tar --create --file=- --directory="$source_dir" . \
                | tar --extract --file=- --directory="$PAYLOAD/$dir"
        fi
    fi
done

echo "ULTRON: compressing -> $ARCHIVE"
tar --create --file=- --directory="$STAGING" "$ROOT_NAME" | gzip > "$ARCHIVE"

echo "ULTRON: backup written -> $ARCHIVE"
echo "ULTRON: restore with deployment/scripts/restore.sh $ARCHIVE"
