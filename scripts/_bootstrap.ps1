# =============================================================================
# ULTRON - shared Windows script bootstrap
# -----------------------------------------------------------------------------
# Resolves repository paths without hard-coding Windows-specific locations
# (spec section 36). Every ULTRON Windows script dot-sources this file.
#
# Dot-source it:
#   . (Join-Path $PSScriptRoot "_bootstrap.ps1")
# =============================================================================

$ErrorActionPreference = "Stop"

# server/ is the Python package root (spec section 4).
$UltronRepoRoot  = Split-Path -Parent $PSScriptRoot
$UltronServerDir = Join-Path $UltronRepoRoot "server"
$UltronVenvDir   = Join-Path $UltronServerDir ".venv"

if (-not (Test-Path -LiteralPath $UltronServerDir)) {
    throw "ULTRON: server directory not found at $UltronServerDir"
}

function Add-UltronToPath {
    <#
        winget installs tools into per-user directories that are absent from
        the PATH of shells opened before the install. Add them when they exist,
        so scripts work in a terminal that was opened beforehand.
    #>
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"),
        (Join-Path $env:USERPROFILE ".local\bin"),
        (Join-Path $env:USERPROFILE ".cargo\bin")
    )

    # winget unpacks uv into a versioned package directory; discover it.
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetPackages) {
        $uvPackage = Get-ChildItem -LiteralPath $wingetPackages -Filter "uv.exe" `
            -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty DirectoryName
        if ($uvPackage) { $candidates += $uvPackage }
    }

    # Installed CPython interpreters, newest first.
    $pythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $pythonRoot) {
        $pythons = Get-ChildItem -LiteralPath $pythonRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($p in $pythons) { $candidates += $p.FullName }
    }

    $current = $env:Path -split ";" | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate) -and
            ($current -notcontains $candidate)) {
            $env:Path = "$candidate;$env:Path"
            $current += $candidate
        }
    }
}

function Get-UltronUv {
    <#  Resolve the uv executable, or throw an actionable error. #>
    Add-UltronToPath
    $cmd = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetPackages) {
        $uv = Get-ChildItem -LiteralPath $wingetPackages -Filter "uv.exe" `
            -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
        if ($uv) { return $uv }
    }
    throw "ULTRON: 'uv' not found. Install it with: winget install astral-sh.uv"
}

$script:UltronExitCode = 0

function Invoke-UltronNative {
    <#
        Run a native executable from the server project directory.

        The tool's own output flows through the pipeline so it stays visible and
        remains redirectable, and the exit code is left in $script:UltronExitCode
        rather than being mixed into that output.

        PowerShell 5.1 wraps a native program's stderr into ErrorRecord objects
        and, under $ErrorActionPreference = 'Stop', promotes them to terminating
        errors. Tools such as uv and pytest legitimately write progress to
        stderr, so the preference is relaxed for the duration of the call.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$NativeArgs
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $script:UltronExitCode = 1
    Push-Location $UltronServerDir
    try {
        & $Exe @NativeArgs
        $script:UltronExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }
}

function Get-UltronUvArgs {
    <#  Prefix arguments with the uv project selector. #>
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string[]]$Arguments
    )
    return (@("run", "--project", $UltronServerDir) + $Arguments)
}

function Invoke-UltronUv {
    <#  Run `uv run` inside the server project. Result lands in $UltronExitCode. #>
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string[]]$Arguments
    )
    $uv = Get-UltronUv
    Invoke-UltronNative -Exe $uv -NativeArgs (Get-UltronUvArgs -Arguments $Arguments)
}

function Initialize-UltronEnvironment {
    <#
        Create/refresh the virtual environment and install dependencies.

        Only the core dependency set plus the 'dev' extra is installed by
        default. Heavy optional stacks (voice model runtimes, Playwright
        browsers) are opt-in so a development machine stays light:

            scripts\setup.ps1 -Extras browser
            $env:ULTRON_SYNC_EXTRAS = "browser,providers"
    #>
    param([string[]]$Extras = @("dev"))

    if ($env:ULTRON_SYNC_EXTRAS) {
        $Extras = @($Extras) + @(
            $env:ULTRON_SYNC_EXTRAS -split "," |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ }
        )
    }

    $uv = Get-UltronUv
    $syncArgs = @("sync", "--project", $UltronServerDir)
    foreach ($extra in $Extras) { $syncArgs += @("--extra", $extra) }

    Write-Host "ULTRON: syncing dependencies (server/.venv) [$($Extras -join ', ')]" -ForegroundColor DarkGray
    Invoke-UltronNative -Exe $uv -NativeArgs $syncArgs
    if ($script:UltronExitCode -ne 0) {
        throw "ULTRON: dependency sync failed (exit $script:UltronExitCode)"
    }
}

function Get-UltronApiPort {
    <#  Resolve the API port: explicit argument, then environment, then 8000. #>
    param([int]$Port = 0)
    if ($Port -gt 0) { return $Port }
    $fromEnv = $env:API_PORT
    if ($fromEnv) {
        $parsed = 0
        if ([int]::TryParse($fromEnv, [ref]$parsed) -and $parsed -gt 0) { return $parsed }
    }
    return 8000
}

function Assert-UltronSuccess {
    <#  Throw when a step reported a non-zero exit code. #>
    param(
        [int]$ExitCode,
        [string]$Step
    )
    if ($ExitCode -ne 0) {
        throw "ULTRON: '$Step' failed with exit code $ExitCode"
    }
}
