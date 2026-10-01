#!/usr/bin/env bash
# =============================================================================
# ULTRON - shared POSIX script bootstrap
# -----------------------------------------------------------------------------
# Resolves repository paths portably. No Windows-specific paths, no hard-coded
# absolute locations (spec section 36).
#
# Source it:
#   . "$(dirname "$0")/_bootstrap.sh"
# =============================================================================

set -euo pipefail

ULTRON_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ULTRON_REPO_ROOT="$(cd "$ULTRON_SCRIPT_DIR/.." && pwd)"
ULTRON_SERVER_DIR="$ULTRON_REPO_ROOT/server"
ULTRON_VENV_DIR="$ULTRON_SERVER_DIR/.venv"

if [ ! -d "$ULTRON_SERVER_DIR" ]; then
    echo "ULTRON: server directory not found at $ULTRON_SERVER_DIR" >&2
    exit 1
fi

# Locate uv, or fail with an actionable message.
ULTRON_UV=""
if command -v uv >/dev/null 2>&1; then
    ULTRON_UV="$(command -v uv)"
else
    for candidate in \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv" \
        "/usr/local/bin/uv"
    do
        if [ -x "$candidate" ]; then
            ULTRON_UV="$candidate"
            break
        fi
    done
fi

if [ -z "$ULTRON_UV" ]; then
    echo "ULTRON: 'uv' not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# Sync dependencies (creates server/.venv on first run).
#
# Only the core dependency set plus the 'dev' extra is installed by default.
# Heavy optional stacks (voice runtimes, Playwright browsers) are opt-in so a
# development machine stays light:
#
#   ULTRON_SYNC_EXTRAS="browser,providers" ./scripts/setup.sh
ultron_setup() {
    local extras="${ULTRON_SYNC_EXTRAS:-dev}"
    echo "ULTRON: syncing dependencies ($ULTRON_VENV_DIR) [$extras]"

    local args=("sync" "--project" "$ULTRON_SERVER_DIR")
    local IFS_OLD="$IFS"
    IFS=','
    for extra in $extras; do
        extra="$(echo "$extra" | tr -d '[:space:]')"
        if [ -n "$extra" ]; then
            args+=("--extra" "$extra")
        fi
    done
    IFS="$IFS_OLD"

    "$ULTRON_UV" "${args[@]}"
}

# Run uv from the server project directory.
ultron_run() {
    ( cd "$ULTRON_SERVER_DIR" && "$ULTRON_UV" run --project "$ULTRON_SERVER_DIR" "$@" )
}

# Resolve the API port: argument, then environment, then default.
ultron_port() {
    local port="${1:-}"
    if [ -z "$port" ]; then
        port="${API_PORT:-}"
    fi
    if [ -z "$port" ]; then
        port=8000
    fi
    echo "$port"
}
