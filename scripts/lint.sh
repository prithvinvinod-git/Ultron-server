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
if ! ultron_run ruff check app tests migrations; then
    if [ "$FIX" = "--fix" ]; then
        echo "ULTRON: applying ruff fixes"
        ultron_run ruff check --fix app tests migrations
    else
        echo "ULTRON: ruff check failed (re-run with --fix to auto-repair)" >&2
        exit 1
    fi
fi

echo "ULTRON: ruff format --check"
ultron_run ruff format --check app tests migrations

# `migrations/versions/` is excluded by pyproject: a revision imports helpers
# from the models it is meant to predate, and mypy would then hold the migration
# to whatever those helpers return today rather than to the schema it froze.
echo "ULTRON: mypy"
ultron_run mypy app tests migrations/env.py

echo "ULTRON: lint and type check clean"
