# =============================================================================
# ULTRON - setup (Windows)
# Creates server/.venv and installs the dependency set.
#
#   .\scripts\setup.ps1                       core + dev
#   .\scripts\setup.ps1 -Extras browser,providers
#   $env:ULTRON_SYNC_EXTRAS = "browser"      same, via environment
# =============================================================================

param(
    [string[]]$Extras = @("dev")
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

Initialize-UltronEnvironment -Extras $Extras

Write-Host ""
Write-Host "ULTRON environment ready." -ForegroundColor Green
Write-Host "  project : $UltronServerDir"
Write-Host ""
Write-Host "Next: scripts\start.ps1   (API + WebSocket)"
Write-Host "      scripts\test.ps1    (unit + integration tests)"
