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
Invoke-UltronUv -Arguments @("ruff", "check", "app", "tests", "migrations")
if ($script:UltronExitCode -ne 0) {
    $failed = $true
    if ($Fix) {
        Write-Host "ULTRON: applying ruff fixes" -ForegroundColor Yellow
        Invoke-UltronUv -Arguments @("ruff", "check", "--fix", "app", "tests", "migrations")
    }
    else {
        Write-Host "ULTRON: run scripts\format.ps1, or scripts\lint.ps1 -Fix" -ForegroundColor Yellow
    }
}

Write-Host "ULTRON: ruff format --check" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("ruff", "format", "--check", "app", "tests", "migrations")
if ($script:UltronExitCode -ne 0) { $failed = $true }

# `migrations/versions/` is excluded by pyproject: a revision imports helpers
# from the models it is meant to predate, and mypy would then hold the migration
# to whatever those helpers return today rather than to the schema it froze.
Write-Host "ULTRON: mypy" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("mypy", "app", "tests", "migrations/env.py")
if ($script:UltronExitCode -ne 0) { $failed = $true }

if ($failed) {
    throw "ULTRON: lint or type check failed"
}

Write-Host "ULTRON: lint and type check clean" -ForegroundColor Green
