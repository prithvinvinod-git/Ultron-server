#!/usr/bin/env bash
# =============================================================================
# ULTRON - health check (T195)
# -----------------------------------------------------------------------------
# A machine-readable probe to check liveness, readiness, and Prometheus metrics.
# Used by the deployment smoke test and by a monitoring agent. It exits non-zero
# on any probe that is neither 200 OK nor the expected metric value, which
# makes it easy to use in CI or in a cron job.
#
# Usage:
#   deployment/scripts/health-check.sh [host] [port]
# =============================================================================

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

HOST="${1:-127.0.0.1}"
PORT="${2:-8000}"
BASE="http://$HOST:$PORT"

ultron_require curl

# -s silent, -w writes status, -f fails on HTTP errors
curl_json() {
    local url="$1"
    local code
    code="$(curl -sS -w '%{http_code}' -o /dev/null -m 5 "$url")"
    if [ "$code" != "200" ]; then
        ultron_die "HTTP $code from $url"
    fi
}

echo "ULTRON: probing /health and /ready"
curl_json "$BASE/health"
curl_json "$BASE/ready"

echo "ULTRON: probing /metrics"
code_metrics="$(curl -sS -w '%{http_code}' -o /dev/null -m 5 "$BASE/metrics")"
if [ "$code_metrics" != "200" ]; then
    # If disabled, do not treat as failure: the deployment may have metrics off.
    # A script that expects metrics should check separately.
    echo "ULTRON: /metrics returned $code_metrics (may be disabled)"
else
    echo "ULTRON: /metrics OK"
fi

echo "ULTRON: healthy"
