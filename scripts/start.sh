#!/usr/bin/env bash
# =============================================================================
# ULTRON - start the server (Linux, no reload)
# Used by the systemd units and by the deployment smoke test.
# =============================================================================

set -euo pipefail

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

HOST="${ULTRON_HOST:-0.0.0.0}"
PORT="$(ultron_port "${1:-}")"

if [ ! -d "$ULTRON_VENV_DIR" ]; then
    ultron_setup
fi

echo "ULTRON starting -> http://127.0.0.1:$PORT"

exec "$ULTRON_UV" run --project "$ULTRON_SERVER_DIR" \
    uvicorn app.main:app --host "$HOST" --port "$PORT"
