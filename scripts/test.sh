#!/usr/bin/env bash
# =============================================================================
# ULTRON - test suite (Linux)
#
#   ./scripts/test.sh            unit only (no external services needed)
#   ./scripts/test.sh --all      unit + integration
#   ./scripts/test.sh --coverage coverage report
#   ./scripts/test.sh --e2e      end-to-end
#
# The suite must pass on a bare checkout with no Docker and no paid API keys
# (spec sections 38, 54). Integration tests skip themselves when PostgreSQL or
# Redis are unreachable.
# =============================================================================

set -euo pipefail

# shellcheck source=_bootstrap.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"

ARGS=()
MARKER_EXPR="not integration and not e2e and not slow"
QUIET="-q"

while [ $# -gt 0 ]; do
    case "$1" in
        --all)     MARKER_EXPR="not slow" ;;
        --e2e)     MARKER_EXPR="e2e or integration" ;;
        --coverage)
            ARGS+=("--cov=app" "--cov-report=term-missing" "--cov-report=xml:coverage.xml")
            ;;
        -v|--verbose) QUIET="" ;;
        -*) echo "ULTRON: unknown option $1" >&2; exit 2 ;;
        *)  ARGS+=("$1") ;;
    esac
    shift
done

if [ -n "$QUIET" ]; then ARGS+=("$QUIET"); else ARGS+=("-vv"); fi
ARGS+=("-m" "$MARKER_EXPR")

echo "ULTRON: pytest ${ARGS[*]}"

( cd "$ULTRON_SERVER_DIR" && "$ULTRON_UV" run --project "$ULTRON_SERVER_DIR" pytest "${ARGS[@]}" )
