# =============================================================================
# ULTRON - format (Windows)
# ruff format followed by ruff check --fix.
# =============================================================================

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

Write-Host "ULTRON: ruff format" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("ruff", "format", "app", "tests")

Write-Host "ULTRON: ruff check --fix" -ForegroundColor Cyan
Invoke-UltronUv -Arguments @("ruff", "check", "--fix", "app", "tests")

Write-Host "ULTRON: format complete" -ForegroundColor Green
