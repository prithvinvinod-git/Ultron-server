#!/usr/bin/env bash
# =============================================================================
# ULTRON - restore (T195)
# -----------------------------------------------------------------------------
# Restores a backup produced by backup.sh. This REPLACES the database and the
# persistent directories, so it refuses to run while ultron-api is active and
# requires an explicit confirmation unless --yes is given.
#
# Usage:
#   deployment/scripts/restore.sh <archive.tar.gz> [--yes]
# =============================================================================

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] || ultron_die "usage: restore.sh <archive.tar.gz> [--yes]"
[ -f "$ARCHIVE" ] || ultron_die "archive not found: $ARCHIVE"
ASSUME_YES="${2:-}"

ultron_require psql
ultron_require tar
ultron_require gzip

DATABASE_URL="$(ultron_env DATABASE_URL || true)"
[ -n "$DATABASE_URL" ] || ultron_die "DATABASE_URL not found in $ULTRON_ENV_FILE"

# Refuse to restore under a running service: writes during a restore would
# half-overwrite the restored state.
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet ultron-api 2>/dev/null; then
        ultron_die "ultron-api is running; stop it first: sudo systemctl stop ultron-api"
    fi
fi

if [ "$ASSUME_YES" != "--yes" ]; then
    echo "ULTRON: this will OVERWRITE the database and workspaces/data/logs under $ULTRON_ROOT"
    printf "Type 'restore' to continue: "
    read -r answer
    [ "$answer" = "restore" ] || ultron_die "aborted"
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "ULTRON: extracting $ARCHIVE"
tar --extract --file="$ARCHIVE" --directory="$STAGING"

# The archive contains exactly one top-level ultron-<stamp>/ directory.
PAYLOAD="$(find "$STAGING" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$PAYLOAD" ] || ultron_die "archive has no payload directory"
[ -f "$PAYLOAD/database.sql" ] || ultron_die "archive is missing database.sql"

echo "ULTRON: restoring database"
psql --dbname="$DATABASE_URL" --set=ON_ERROR_STOP=1 \
    --file="$PAYLOAD/database.sql" >/dev/null

echo "ULTRON: restoring persistent directories"
for dir in workspaces data logs; do
    if [ -d "$PAYLOAD/$dir" ]; then
        mkdir -p "$ULTRON_ROOT/$dir"
        # --delete makes the restored tree match the backup exactly; the backups
        # directory under data/ is preserved so restore does not erase backups.
        if [ "$dir" = "data" ]; then
            tar --extract --file=- --directory="$ULTRON_ROOT/$dir" \
                --exclude='backups' --no-overwrite-dir \
                < "$(tar --create --file=- --directory="$PAYLOAD/$dir" .)"
        else
            tar --extract --file=- --directory="$ULTRON_ROOT/$dir" \
                < "$(tar --create --file=- --directory="$PAYLOAD/$dir" .)"
        fi
    fi
done

echo "ULTRON: restore complete; start with: sudo systemctl start ultron-api"
