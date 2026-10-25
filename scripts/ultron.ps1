# =============================================================================
# ULTRON - one-command launcher (Windows)
# -----------------------------------------------------------------------------
# Brings up the whole local stack in order, idempotently:
#
#   1. PostgreSQL  - the `ultron-postgres` Docker container (host service is
#                    broken on this machine: backends die with 0xC0000142)
#   2. Redis       - the `Redis` Windows service
#   3. API server  - FastAPI on 127.0.0.1:8000 with the voice env applied
#   4. Electron    - the desktop shell, served the published bundle
#
# Safe to run repeatedly: anything already healthy is reused, nothing is
# duplicated. Every step is logged to $env:TEMP\ultron\.
#
#   scripts\ultron.ps1                 start everything
#   scripts\ultron.ps1 -NoElectron     server + dependencies only
#   scripts\ultron.ps1 -Stop           stop Electron, server and Postgres
#
# The root .env stays authoritative for provider keys and DATABASE_URL; only
# the laptop-mode overrides (Redis RESP2, voice engines) are applied here, and
# only when the variable is not already set in the process environment.
# =============================================================================

param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$FixEnv,
    [switch]$NoElectron,
    [switch]$InstallShortcut,
    [int]$Port = 0
)

. (Join-Path $PSScriptRoot "_bootstrap.ps1")

$UltronContainer = "ultron-postgres"
$UltronElectronDir = Join-Path $UltronRepoRoot "clients\electron"
$UltronLogDir = Join-Path $env:TEMP "ultron"
$UltronPort = Get-UltronApiPort -Port $Port
$script:Failures = 0

New-Item -ItemType Directory -Path $UltronLogDir -Force | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "  - $Message" -ForegroundColor DarkGray
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  + $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  ! $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    $script:Failures++
    Write-Host "  x $Message" -ForegroundColor Red
}

function Invoke-UltronTool {
    <#
        PowerShell 5.1 turns a native tool's stderr into an ErrorRecord, and the
        bootstrap sets $ErrorActionPreference = 'Stop'. Docker and Alembic both
        write progress to stderr, which would abort the launcher, so the
        preference is relaxed for the duration of the call. Returns the exit
        code; output is either discarded or written to $LogPath.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$LogPath
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($LogPath) { & $Exe @Arguments *> $LogPath }
        else { & $Exe @Arguments *> $null }
        return $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
}

function Get-UltronToolOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return (& $Exe @Arguments 2>$null) }
    finally { $ErrorActionPreference = $previous }
}

function Get-UltronDatabaseUrl {
    <#  Read DATABASE_URL from the root .env without loading the whole file. #>
    $envFile = Join-Path $UltronRepoRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) { return $null }
    $line = Select-String -LiteralPath $envFile -Pattern "^\s*DATABASE_URL\s*=" |
        Select-Object -First 1
    if (-not $line) { return $null }
    return (($line.Line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Get-UltronDatabaseParts {
    <#  Split a local PostgreSQL URL into user/password/port/database. #>
    param([string]$Url)
    if (-not $Url) { return $null }
    if ($Url -notmatch '^postgres(?:ql)?(\+asyncpg)?://(?<u>[^:]+):(?<p>[^@]+)@(?<h>[^:/]+):(?<port>\d+)/(?<db>[^?]+)') {
        return $null
    }
    return [pscustomobject]@{
        User     = $Matches.u
        Password = [uri]::UnescapeDataString($Matches.p)
        Host     = $Matches.h
        Port     = [int]$Matches.port
        Name     = $Matches.db
    }
}

function Test-TcpPort {
    param([int]$TcpPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("127.0.0.1", $TcpPort)
        if (-not $task.Wait(500)) { return $false }
        return $client.Connected
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutMs = 2000)
    try {
        $request = [System.Net.WebRequest]::Create($Url)
        $request.Timeout = $TimeoutMs
        $response = $request.GetResponse()
        $status = [int]$response.StatusCode
        $response.Close()
        return ($status -ge 200 -and $status -lt 400)
    }
    catch { return $false }
}

function Wait-UltronHttp {
    param([string]$Url, [int]$Seconds = 90, [string]$Label = "service")
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Fail "$Label did not become ready at $Url (see $UltronLogDir)"
    return $false
}

function Get-UltronListeningPid {
    param([int]$TcpPort)
    $conn = Get-NetTCPConnection -LocalPort $TcpPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return $conn.OwningProcess }
    return 0
}

function Remove-UltronStaleEnv {
    <#
        Older setup instructions used `setx` for DATABASE_URL/REDIS_URL and the
        voice engines. Those user-level values outrank every .env file, so a
        stale one silently points the server at the wrong database. The launcher
        supplies the right values per process; clearing the user copies removes
        the trap for shells, IDEs and the Electron shell alike.
    #>
    $names = @(
        "DATABASE_URL", "REDIS_URL", "VOICE_ENABLED", "STT_ENGINE",
        "STT_FALLBACK_ENGINE", "TTS_ENGINE", "TTS_FALLBACK_ENGINE",
        "MODEL_FALLBACK_CHAIN"
    )
    $cleared = @()
    foreach ($name in $names) {
        $value = [Environment]::GetEnvironmentVariable($name, "User")
        if ($null -ne $value) {
            [Environment]::SetEnvironmentVariable($name, $null, "User")
            $cleared += $name
        }
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
    if ($cleared) {
        Write-Ok "cleared stale user environment: $($cleared -join ', ')"
    }
    else {
        Write-Ok "no stale user environment found"
    }
} 

function Start-UltronDocker {
    <#  Docker Desktop is the one dependency that is not a service. #>
    if ((Invoke-UltronTool -Exe "docker" -Arguments @("info")) -eq 0) {
        Write-Ok "Docker engine running"
        return $true
    }
    # Docker Desktop installs per-machine (Program Files) or per-user
    # (%LOCALAPPDATA%). Checking only Program Files made the launcher refuse to
    # start on a per-user install even though the engine was installed.
    $desktop = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe")
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $desktop) {
        Write-Fail "Docker engine is not running and Docker Desktop is not installed"
        return $false
    }
    Write-Step "starting Docker Desktop"
    Start-Process -FilePath $desktop | Out-Null
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        if ((Invoke-UltronTool -Exe "docker" -Arguments @("info")) -eq 0) {
            Write-Ok "Docker engine ready"
            return $true
        }
        Start-Sleep -Seconds 2
    }
    Write-Fail "Docker did not become ready within 120s"
    return $false
}

function Start-UltronPostgres {
    <#
        Host PostgreSQL is unusable here (its backends crash with
        0xC0000142), so the container is the supported local database. If the
        configured URL is not a local PostgreSQL URL, leave it alone.
    #>
    $url = Get-UltronDatabaseUrl
    $parts = Get-UltronDatabaseParts -Url $url
    if (-not $parts) {
        Write-Warn "DATABASE_URL is not a local PostgreSQL URL; leaving the database to .env"
        return
    }
    if ($parts.Host -notin @("localhost", "127.0.0.1", "::1")) {
        Write-Warn "database host '$($parts.Host)' is remote; not starting a container"
        $env:DATABASE_URL = $url
        return
    }

    # Pin the process environment to the root .env value. A stale value in the
    # ambient environment (old `setx`, an IDE, another shell) is the reason a
    # start used to hit the wrong database with the wrong password.
    $env:DATABASE_URL = $url

    if (Test-TcpPort -TcpPort $parts.Port) {
        Write-Ok "PostgreSQL already listening on 127.0.0.1:$($parts.Port)"
        return
    }

    $existing = Get-UltronToolOutput -Exe "docker" -Arguments @("ps", "-a", "--filter", "name=^/$UltronContainer$", "--format", "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "cannot talk to Docker; start Docker Desktop and re-run"
        return
    }

    if ($existing) {
        Write-Step "starting container $UltronContainer"
        Invoke-UltronTool -Exe "docker" -Arguments @("start", $UltronContainer) | Out-Null
    }
    else {
        Write-Step "creating container $UltronContainer"
        Invoke-UltronTool -Exe "docker" -Arguments @("run", "-d", "--name", $UltronContainer, `
            "-e", "POSTGRES_USER=$($parts.User)", `
            "-e", "POSTGRES_PASSWORD=$($parts.Password)", `
            "-e", "POSTGRES_DB=$($parts.Name)", `
            "-p", "$($parts.Port):5432", `
            "-v", "ultron_pgdata:/var/lib/postgresql/data", `
            "--restart", "unless-stopped", "postgres:17") | Out-Null
    }

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if ((Invoke-UltronTool -Exe "docker" -Arguments @("exec", $UltronContainer, "pg_isready", "-U", $parts.User, "-d", $parts.Name)) -eq 0) {
            Write-Ok "PostgreSQL ready on 127.0.0.1:$($parts.Port)"
            return
        }
        Start-Sleep -Milliseconds 750
    }
    Write-Fail "PostgreSQL container did not become ready (docker logs $UltronContainer)"
}

function Start-UltronRedis {
    $service = Get-Service -Name "Redis" -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Warn "Redis service not installed; continuing without it"
        return
    }
    if ($service.Status -eq "Running") {
        Write-Ok "Redis service running"
        return
    }
    Write-Step "starting Redis service"
    try {
        Start-Service -Name "Redis" -ErrorAction Stop
        Write-Ok "Redis service started"
    }
    catch {
        Write-Fail "could not start Redis (run as administrator): $($_.Exception.Message)"
    }
}

function Start-UltronServer {
    if (Get-UltronListeningPid -TcpPort $UltronPort) {
        Write-Ok "API already listening on 127.0.0.1:$UltronPort"
        return
    }

    if (-not (Test-Path -LiteralPath $UltronVenvDir)) {
        Write-Step "creating the virtual environment (first run)"
        Initialize-UltronEnvironment
    }

    $python = Join-Path $UltronVenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $UltronVenvDir "bin\python" }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Fail "no Python interpreter in $UltronVenvDir"
        return
    }

    # Laptop-mode overrides (runapp.md 3.1). Process env beats .env, so only
    # fill in values the caller has not already chosen.
    # Always set these: an ambient value (old `setx`, IDE, parent shell) is
    # exactly how a start silently picked the wrong Redis protocol/engine.
    $env:REDIS_URL = "redis://127.0.0.1:6379/0?protocol=2"
    $env:VOICE_ENABLED = "true"
    $env:STT_ENGINE = "faster-whisper"
    $env:STT_FALLBACK_ENGINE = "groq"
    $env:TTS_ENGINE = "fish-audio"
    $env:TTS_FALLBACK_ENGINE = "edge-tts"

    Write-Step "starting uvicorn on 127.0.0.1:$UltronPort"
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$UltronPort") `
        -WorkingDirectory $UltronServerDir `
        -RedirectStandardOutput (Join-Path $UltronLogDir "server-out.log") `
        -RedirectStandardError (Join-Path $UltronLogDir "server-err.log") `
        -WindowStyle Hidden | Out-Null
}

function Start-UltronElectron {
    if (-not (Test-Path -LiteralPath $UltronElectronDir)) {
        Write-Fail "Electron shell not found at $UltronElectronDir"
        return
    }
    $electronExe = Join-Path $UltronElectronDir "node_modules\electron\dist\electron.exe"
    if (-not (Test-Path -LiteralPath $electronExe)) {
        Write-Step "installing Electron dependencies (first run)"
        Push-Location $UltronElectronDir
        try { & npm install --no-audit --no-fund } finally { Pop-Location }
    }

    $distMain = Join-Path $UltronElectronDir "dist\main.js"
    $needsBuild = -not (Test-Path -LiteralPath $distMain)
    if (-not $needsBuild) {
        $newestSource = Get-ChildItem -LiteralPath (Join-Path $UltronElectronDir "src") -Recurse -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newestSource -and $newestSource.LastWriteTime -gt (Get-Item $distMain).LastWriteTime) {
            $needsBuild = $true
        }
    }
    if ($needsBuild) {
        Write-Step "building the Electron shell"
        Push-Location $UltronElectronDir
        try { & npm run build } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Electron build failed"
            return
        }
    }

    $running = Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($UltronRepoRoot, "OrdinalIgnoreCase") }
    if ($running) {
        Write-Ok "Electron already running (pid $($running[0].ProcessId))"
        return
    }

    Write-Step "launching Electron"
    Start-Process -FilePath $electronExe -ArgumentList "." `
        -WorkingDirectory $UltronElectronDir `
        -RedirectStandardOutput (Join-Path $UltronLogDir "electron-out.log") `
        -RedirectStandardError (Join-Path $UltronLogDir "electron-err.log") | Out-Null
}

function Invoke-UltronMigrations {
    <#
        The server does not create tables; Alembic owns the schema. Running this
        on every start is a sub-second no-op when it is already current, and it
        is what stops a fresh container (or a wiped volume) from serving 500s.
    #>
    $python = Join-Path $UltronVenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $UltronVenvDir "bin\python" }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Warn "no virtual environment yet; skipping migrations"
        return
    }

    $log = Join-Path $UltronLogDir "alembic.log"
    Write-Step "applying database migrations"
    Push-Location $UltronServerDir
    try {
        $code = Invoke-UltronTool -Exe $python -Arguments @("-m", "alembic", "upgrade", "head") -LogPath $log
    }
    finally { Pop-Location }

    if ($code -eq 0) {
        Write-Ok "database schema up to date"
    }
    else {
        Write-Fail "migrations failed (see $log)"
        Get-Content -LiteralPath $log -Tail 15 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkRed }
    }
}

function Show-UltronStatus {
    $parts = Get-UltronDatabaseParts -Url (Get-UltronDatabaseUrl)
    $dbPort = if ($parts) { $parts.Port } else { 5432 }
    if (Test-TcpPort -TcpPort $dbPort) { Write-Ok "PostgreSQL listening on $dbPort" }
    else { Write-Fail "PostgreSQL not listening on $dbPort" }

    $redis = Get-Service -Name "Redis" -ErrorAction SilentlyContinue
    if ($redis -and $redis.Status -eq "Running") { Write-Ok "Redis service running" }
    else { Write-Fail "Redis service not running" }

    if (Test-HttpOk -Url "http://127.0.0.1:$UltronPort/health") {
        try {
            $health = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 `
                -Uri "http://127.0.0.1:$UltronPort/health").Content | ConvertFrom-Json
            Write-Ok "API healthy ($($health.status)) on 127.0.0.1:$UltronPort"
        }
        catch { Write-Ok "API responding on 127.0.0.1:$UltronPort" }
    }
    else { Write-Fail "API not responding on 127.0.0.1:$UltronPort" }

    $electron = Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($UltronRepoRoot, "OrdinalIgnoreCase") }
    if ($electron) { Write-Ok "Electron running (pid $(($electron | Select-Object -First 1).ProcessId))" }
    else { Write-Fail "Electron not running" }

    $container = Get-UltronToolOutput -Exe "docker" -Arguments @("ps", "--filter", "name=^/$UltronContainer$", "--format", "{{.Status}}")
    if ($container) { Write-Ok "container $UltronContainer ($container)" }
    else { Write-Step "container $UltronContainer is not running" }
}

function Stop-Ultron {
    Write-Host "Stopping ULTRON..." -ForegroundColor Cyan

    $electron = Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($UltronRepoRoot, "OrdinalIgnoreCase") }
    foreach ($process in $electron) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($electron) { Write-Ok "Electron stopped" } else { Write-Step "Electron was not running" }

    $apiPid = Get-UltronListeningPid -TcpPort $UltronPort
    if ($apiPid) {
        Stop-Process -Id $apiPid -Force -ErrorAction SilentlyContinue
        Write-Ok "API server stopped"
    }
    else {
        Write-Step "API server was not running"
    }

    $container = Get-UltronToolOutput -Exe "docker" -Arguments @("ps", "--filter", "name=^/$UltronContainer$", "--format", "{{.Names}}")
    if ($container) {
        Invoke-UltronTool -Exe "docker" -Arguments @("stop", $UltronContainer) | Out-Null
        Write-Ok "PostgreSQL container stopped"
    }
    else {
        Write-Step "PostgreSQL container was not running"
    }

    Write-Step "Redis service left running (shared, cheap to keep)"
}

function Install-UltronShortcut {
    <#  Put "ULTRON.lnk" on the desktop: one double-click starts the stack. #>
    $desktop = [Environment]::GetFolderPath("Desktop")
    $path = Join-Path $desktop "ULTRON.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$PSCommandPath`""
    $link.WorkingDirectory = $UltronRepoRoot
    $link.Description = "Start ULTRON (PostgreSQL, Redis, API, Electron)"
    $icon = Join-Path $UltronElectronDir "node_modules\electron\dist\electron.exe"
    if (Test-Path -LiteralPath $icon) { $link.IconLocation = "$icon,0" }
    $link.Save()
    Write-Ok "desktop shortcut created: $path"
}

if ($InstallShortcut) {
    Install-UltronShortcut
    exit 0
}

if ($Stop) {
    Stop-Ultron
    exit 0
}

if ($FixEnv) {
    Remove-UltronStaleEnv
    Write-Host ""
}

Write-Host ""
Write-Host "ULTRON launcher" -ForegroundColor Cyan
Write-Host "  logs: $UltronLogDir" -ForegroundColor DarkGray
Write-Host ""

if ($Status) {
    Show-UltronStatus
    exit $(if ($script:Failures -gt 0) { 1 } else { 0 })
}

if (-not (Start-UltronDocker)) {
    # Docker is only one way to provide PostgreSQL. If something is already
    # listening on the configured port (for example the native Windows service),
    # continuing is correct: Start-UltronPostgres reuses it instead of creating
    # a container that could not bind the same port anyway.
    $dbParts = Get-UltronDatabaseParts -Url (Get-UltronDatabaseUrl)
    if ($dbParts -and (Test-TcpPort -TcpPort $dbParts.Port)) {
        Write-Warn "Docker unavailable; reusing the PostgreSQL already listening on 127.0.0.1:$($dbParts.Port)"
    }
    else {
        exit 1
    }
}
Start-UltronPostgres
Start-UltronRedis
Invoke-UltronMigrations
Start-UltronServer

$apiReady = Wait-UltronHttp -Url "http://127.0.0.1:$UltronPort/health" -Seconds 90 -Label "API"
if (-not $apiReady) {
    Write-Host "  last server errors:" -ForegroundColor DarkGray
    Get-Content -LiteralPath (Join-Path $UltronLogDir "server-err.log") -Tail 20 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
}
if ($apiReady) {
    $health = $null
    try {
        $health = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 `
            -Uri "http://127.0.0.1:$UltronPort/health").Content | ConvertFrom-Json
    }
    catch { }
    if ($health) {
        $colour = if ($health.status -eq "ok") { "Green" } else { "Yellow" }
        Write-Host "  + API status: $($health.status)" -ForegroundColor $colour
        foreach ($warning in @($health.warnings)) {
            if ($warning) { Write-Warn $warning }
        }
    }
}

if ($apiReady -and -not $NoElectron) { Start-UltronElectron }
elseif (-not $apiReady -and -not $NoElectron) {
    Write-Warn "Electron not started because the API is not healthy"
}

Write-Host ""
Write-Host "  UI      http://127.0.0.1:$UltronPort/" -ForegroundColor Cyan
Write-Host "  Docs    http://127.0.0.1:$UltronPort/docs" -ForegroundColor DarkGray
Write-Host "  Health  http://127.0.0.1:$UltronPort/health" -ForegroundColor DarkGray
Write-Host "  Stop    scripts\ultron.ps1 -Stop" -ForegroundColor DarkGray
Write-Host ""

if ($script:Failures -gt 0) {
    Write-Host "$($script:Failures) step(s) failed; see the messages above." -ForegroundColor Red
    exit 1
}
exit 0


