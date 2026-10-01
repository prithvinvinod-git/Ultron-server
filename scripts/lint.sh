#!/usr/bin/env bash
# =============================================================================
# ULTRON - lint + type check (Linux)
#
# Enforces the definition-of-done pipeline from spec section 52:
#   format -> lint -> type check -> unit tests -> integration tests
# =============================================================================

set -euo pipefail

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

FIX="${1:-}"

echo "ULTRON: ruff check"
if ! ultron_run ruff check app tests; then
    if [ "$FIX" = "--fix" ]; then
        echo "ULTRON: applying ruff fixes"
        ultron_run ruff check --fix app tests
    else
        echo "ULTRON: ruff check failed (re-run with --fix to auto-repair)" >&2
        exit 1
    fi
fi

echo "ULTRON: mypy"
ultron_run mypy app tests

echo "ULTRON: lint and type check clean"
