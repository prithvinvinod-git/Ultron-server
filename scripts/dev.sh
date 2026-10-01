#!/usr/bin/env bash
# =============================================================================
# ULTRON - development server (Linux)
#
# Runs the API with auto-reload. PostgreSQL and Redis are expected to come from
# Docker Compose (see docker-compose.yml) or from system services.
# =============================================================================

set -euo pipefail

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

HOST="${ULTRON_HOST:-0.0.0.0}"
PORT="$(ultron_port "${1:-}")"

if [ ! -d "$ULTRON_VENV_DIR" ]; then
    echo "ULTRON: virtual environment missing, creating it"
    ultron_setup
fi

echo "ULTRON dev server -> http://127.0.0.1:$PORT"
echo "  health   : http://127.0.0.1:$PORT/health"
echo "  ready    : http://127.0.0.1:$PORT/ready"
echo "  metrics  : http://127.0.0.1:$PORT/metrics"
echo "  websocket: ws://127.0.0.1:$PORT/ws"
echo

exec "$ULTRON_UV" run --project "$ULTRON_SERVER_DIR" \
    uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
