# =============================================================================
# ULTRON - start the server (Windows, no reload)
# Used by the deployment smoke test and by the Windows equivalent of the
# systemd unit.
# =============================================================================

param(
    [int]$Port = 0,
    [string]$ListenHost = "0.0.0.0"
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

$Port = Get-UltronApiPort -Port $Port

if (-not (Test-Path -LiteralPath $UltronVenvDir)) {
    Initialize-UltronEnvironment
}

Write-Host "ULTRON starting -> http://127.0.0.1:$Port" -ForegroundColor Cyan

Invoke-UltronUv -Arguments @(
    "uvicorn", "app.main:app", "--host", $ListenHost, "--port", "$Port"
)
exit $script:UltronExitCode
