<#
.SYNOPSIS
  Starts the Amaze PoC locally: the API (port 8000), backend-ui (5173) and consumer-ui (5174).

.DESCRIPTION
  Opens three titled PowerShell windows, waits for the three ports, checks /api/health,
  and prints OK or FAILED for each. Close the three windows to stop everything.
  Exits with code 1 if anything is missing or does not start.

  This file is plain ASCII on purpose, so Windows PowerShell 5.1 reads it correctly.

.EXAMPLE
  .\scripts\dev.ps1
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot '.venv\Scripts\python.exe'

function Fail([string]$Message) {
    Write-Host "FAILED: $Message" -ForegroundColor Red
    exit 1
}

# ---- 1. Prerequisites ----
$missing = @()
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.env'))) {
    $missing += '.env is missing. Fix: Copy-Item .env.example .env   (then set the password and run .\scripts\setup_db.ps1)'
}
if (-not (Test-Path -LiteralPath $Python)) {
    $missing += '.venv is missing. Fix: py -3.14 -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements-dev.txt'
}
foreach ($ui in 'backend-ui', 'consumer-ui') {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "apps\$ui\node_modules"))) {
        $missing += "apps\$ui\node_modules is missing. Fix: npm --prefix apps\$ui install"
    }
}
if ($missing.Count -gt 0) {
    foreach ($m in $missing) { Write-Host "MISSING: $m" -ForegroundColor Red }
    exit 1
}

$pyVersion = (& $Python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($pyVersion -ne '3.14') {
    Fail ".venv uses Python $pyVersion, not 3.14. Fix: Remove-Item -Recurse .venv; py -3.14 -m venv .venv; then reinstall requirements."
}

# ---- 2. Ports must be free ----
$services = @(
    @{ Name = 'Amaze API';         Port = 8000; Dir = $RepoRoot;
       Cmd  = "& '$Python' -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir apps --reload-dir core --reload-dir ports --reload-dir agents" },
    @{ Name = 'Amaze backend-ui';  Port = 5173; Dir = (Join-Path $RepoRoot 'apps\backend-ui');  Cmd = 'npm run dev' },
    @{ Name = 'Amaze consumer-ui'; Port = 5174; Dir = (Join-Path $RepoRoot 'apps\consumer-ui'); Cmd = 'npm run dev' }
)

foreach ($s in $services) {
    $listener = Get-NetTCPConnection -LocalPort $s.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        Fail ("Port {0} ({1}) is already in use by process {2}. Close it first." -f $s.Port, $s.Name, $listener.OwningProcess)
    }
}

# ---- 3. Start three windows ----
foreach ($s in $services) {
    $command = "`$Host.UI.RawUI.WindowTitle = '$($s.Name)'; Set-Location -LiteralPath '$($s.Dir)'; $($s.Cmd)"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $s.Dir `
        -ArgumentList '-NoExit', '-NoProfile', '-EncodedCommand', $encoded | Out-Null
    Write-Host ("started:  {0} (port {1})" -f $s.Name, $s.Port)
}

# ---- 4. Wait for the ports ----
function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($connect.AsyncWaitHandle.WaitOne(500)) { $client.EndConnect($connect); return $true }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$up = @{}
foreach ($s in $services) { $up[$s.Port] = $false }
while ((Get-Date) -lt $deadline -and ($up.Values -contains $false)) {
    foreach ($s in $services) {
        if (-not $up[$s.Port]) { $up[$s.Port] = Test-Port $s.Port }
    }
    if ($up.Values -contains $false) { Start-Sleep -Milliseconds 500 }
}

# ---- 5. Report ----
$failed = $false
foreach ($s in $services) {
    if ($up[$s.Port]) {
        Write-Host ("OK      {0,-18} http://localhost:{1}" -f $s.Name, $s.Port) -ForegroundColor Green
    } else {
        Write-Host ("FAILED  {0,-18} port {1} not listening after {2}s; see its window" -f $s.Name, $s.Port, $TimeoutSeconds) -ForegroundColor Red
        $failed = $true
    }
}

if ($up[8000]) {
    try {
        $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 10
        Write-Host ("OK      {0,-18} {1}" -f 'API health', $health.Content) -ForegroundColor Green
    } catch {
        $body = ''
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $body = $reader.ReadToEnd()
        }
        Write-Host ("FAILED  {0,-18} {1} {2}" -f 'API health', $_.Exception.Message, $body) -ForegroundColor Red
        $failed = $true
    }
}

Write-Host ''
Write-Host 'API health:   http://localhost:8000/api/health'
Write-Host 'Backend UI:   http://localhost:5173'
Write-Host 'Consumer UI:  http://localhost:5174'
Write-Host 'Stop: close the three windows.'

if ($failed) { exit 1 }
exit 0
