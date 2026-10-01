#!/usr/bin/env bash
# =============================================================================
# ULTRON - dependency setup (Linux)
# Creates server/.venv and installs the dependency set.
#
#   ./scripts/setup.sh                              core + dev
#   ULTRON_SYNC_EXTRAS="browser,providers" ./scripts/setup.sh
# =============================================================================

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

ultron_setup

echo
echo "ULTRON environment ready."
echo "  project : $ULTRON_SERVER_DIR"
echo "  uv      : $ULTRON_UV"
echo "  python  : $(ultron_run python -V)"
echo
echo "Next: scripts/start.sh   (API + WebSocket)"
echo "      scripts/test.sh    (unit + integration tests)"
