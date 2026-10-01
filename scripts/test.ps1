# =============================================================================
# ULTRON - test suite (Windows)
#
#   .\scripts\test.ps1                unit only, no external services required
#   .\scripts\test.ps1 -All           unit + integration
#   .\scripts\test.ps1 -E2E           end-to-end
#   .\scripts\test.ps1 -Coverage      with a coverage report
#   .\scripts\test.ps1 -Path tests\unit\test_health.py
#
# The suite must pass on a bare checkout with no Docker and no paid API keys
# (spec sections 38, 54). Integration tests skip themselves when PostgreSQL or
# Redis are unreachable.
# =============================================================================

param(
    [switch]$All,
    [switch]$Coverage,
    [switch]$E2E,
    [string[]]$Path = @(),
    [switch]$Verbose
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

$pytestArgs = @()

if ($Coverage) {
    $pytestArgs += @("--cov=app", "--cov-report=term-missing", "--cov-report=xml:coverage.xml")
}

if ($Verbose) { $pytestArgs += "-vv" } else { $pytestArgs += "-q" }

if ($E2E) {
    $pytestArgs += @("-m", "e2e or integration")
}
elseif ($All) {
    $pytestArgs += @("-m", "not slow")
}
else {
    # Default: only tests that need nothing external.
    $pytestArgs += @("-m", "not integration and not e2e and not slow")
}

if ($Path.Count -gt 0) { $pytestArgs += $Path }

Write-Host "ULTRON: pytest $($pytestArgs -join ' ')" -ForegroundColor DarkGray

Invoke-UltronUv -Arguments (@("pytest") + $pytestArgs)
exit $script:UltronExitCode
