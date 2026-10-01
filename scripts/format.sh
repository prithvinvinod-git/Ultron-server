#!/usr/bin/env bash
# =============================================================================
# ULTRON - format (Linux)
# =============================================================================

set -euo pipefail

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

echo "ULTRON: ruff format"
ultron_run ruff format app tests migrations

echo "ULTRON: ruff check --fix"
ultron_run ruff check --fix app tests migrations

echo "ULTRON: format complete"
