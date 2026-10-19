#!/usr/bin/env bash
# =============================================================================
# ULTRON - migrate (T195)
# -----------------------------------------------------------------------------
# Runs database migrations (alembic upgrade head) under the deployed checkout.
# Do not run this while a half-restarted service may be accepting traffic: it is
# safe to run, but the deployment runbook usually stops the API first or runs
# against an idling node. It does require the virtualenv and uv to be present.
#
# Usage:
#   deployment/scripts/migrate.sh
# =============================================================================

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

# Needs uv and the server venv as deployed. Locate uv first (matches
# scripts/_bootstrap.sh logic).
ultron_find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv; return 0
    fi
    for candidate in \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv" \
        "/usr/local/bin/uv"
    do
        [ -x "$candidate" ] && { echo "$candidate"; return 0; }
    done
    ultron_die "'uv' not found"
}

ULTRON_UV="$(ultron_find_uv)"

echo "ULTRON: running alembic upgrade head"
cd "$ULTRON_SERVER_DIR"
"$ULTRON_UV" run --project "$ULTRON_SERVER_DIR" alembic upgrade head

echo "ULTRON: migrations complete"
