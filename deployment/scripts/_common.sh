#!/usr/bin/env bash
# =============================================================================
# ULTRON deployment scripts - shared helpers (T195)
# -----------------------------------------------------------------------------
# These scripts run on the server, against a deployed ULTRON, not against a dev
# checkout. They deliberately do not source scripts/_bootstrap.sh: that file
# creates a virtualenv and requires uv, which a *backup* has no business doing.
#
# Source it:
#   . "$(dirname "$0")/_common.sh"
# =============================================================================

set -euo pipefail

# Deployment root (spec §47). Overridable for a non-standard install.
ULTRON_ROOT="${ULTRON_ROOT:-/opt/ultron}"
ULTRON_SERVER_DIR="$ULTRON_ROOT/server"
ULTRON_CONFIG_DIR="$ULTRON_ROOT/config"
ULTRON_DATA_DIR="$ULTRON_ROOT/data"
ULTRON_LOG_DIR="$ULTRON_ROOT/logs"
ULTRON_ENV_FILE="$ULTRON_CONFIG_DIR/.env"
ULTRON_BACKUP_DIR="$ULTRON_ROOT/data/backups"

# Read one value from the env file without exporting the whole thing. Used to
# discover DATABASE_URL, which the scripts need but must not print.
ultron_env() {
    local key="$1"
    if [ ! -f "$ULTRON_ENV_FILE" ]; then
        return 1
    fi
    # Match the last assignment, strip surrounding quotes, ignore comments.
    sed -n "s/^[[:space:]]*${key}=//p" "$ULTRON_ENV_FILE" \
        | tail -n 1 \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

# Fail with an actionable message.
ultron_die() {
    echo "ULTRON: $*" >&2
    exit 1
}

# Require a command to exist.
ultron_require() {
    command -v "$1" >/dev/null 2>&1 || ultron_die "'$1' is required but not found"
}

# Timestamp usable in a filename.
ultron_timestamp() {
    date -u +%Y%m%dT%H%M%SZ
}
