# =============================================================================
# ULTRON - development server (Windows)
#
# Runs the API with auto-reload. PostgreSQL and Redis are expected to come from
# Docker (see docker-compose.yml). The API reports unhealthy dependencies
# through /health and /ready rather than crashing (spec section 33).
# =============================================================================

param(
    [int]$Port = 0,
    [string]$ListenHost = "0.0.0.0",
    [switch]$NoReload
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

$Port = Get-UltronApiPort -Port $Port

if (-not (Test-Path -LiteralPath $UltronVenvDir)) {
    Write-Host "ULTRON: virtual environment missing, creating it" -ForegroundColor Yellow
    Initialize-UltronEnvironment
}

$uvArgs = @("uvicorn", "app.main:app", "--host", $ListenHost, "--port", "$Port")
if (-not $NoReload) { $uvArgs += "--reload" }

Write-Host "ULTRON dev server -> http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "  health   : http://127.0.0.1:$Port/health" -ForegroundColor DarkGray
Write-Host "  ready    : http://127.0.0.1:$Port/ready" -ForegroundColor DarkGray
Write-Host "  metrics  : http://127.0.0.1:$Port/metrics" -ForegroundColor DarkGray
Write-Host "  websocket: ws://127.0.0.1:$Port/ws" -ForegroundColor DarkGray
Write-Host ""

Invoke-UltronUv -Arguments $uvArgs
exit $script:UltronExitCode
