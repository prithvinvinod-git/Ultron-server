# =============================================================================
# ULTRON - lint + type check (Windows)
#
# Enforces the definition-of-done pipeline from spec section 52:
#   format -> lint -> type check -> unit tests -> integration tests
# This script runs the static half: lint and type check.
# =============================================================================

param(
    [switch]$Fix
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

$failed = $false

Write-Host "ULTRON: ruff check" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("ruff", "check", "app", "tests")
if ($script:UltronExitCode -ne 0) {
    $failed = $true
    if ($Fix) {
        Write-Host "ULTRON: applying ruff fixes" -ForegroundColor Yellow
        Invoke-UltronUv -Arguments @("ruff", "check", "--fix", "app", "tests")
    }
    else {
        Write-Host "ULTRON: run scripts\format.ps1, or scripts\lint.ps1 -Fix" -ForegroundColor Yellow
    }
}

Write-Host "ULTRON: ruff format --check" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("ruff", "format", "--check", "app", "tests")
if ($script:UltronExitCode -ne 0) { $failed = $true }

Write-Host "ULTRON: mypy" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("mypy", "app")
if ($script:UltronExitCode -ne 0) { $failed = $true }

if ($failed) {
    throw "ULTRON: lint or type check failed"
}

Write-Host "ULTRON: lint and type check clean" -ForegroundColor Green
