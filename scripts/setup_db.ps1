<#
.SYNOPSIS
  Creates the amaze database user and the amaze and amaze_test databases
  in the local PostgreSQL. Safe to run again: it never drops anything.

.DESCRIPTION
  Reads DATABASE_URL and TEST_DATABASE_URL from .env in the repository root.
  Asks once for the PostgreSQL superuser password (hidden), unless PGPASSWORD
  is already set. The password is used by this script's process only.

  This file is plain ASCII on purpose, so Windows PowerShell 5.1 reads it
  correctly.

.EXAMPLE
  .\scripts\setup_db.ps1
  .\scripts\setup_db.ps1 -SuperUser postgres
#>
[CmdletBinding()]
param(
    [string]$SuperUser = 'postgres',
    [string]$PsqlPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile  = Join-Path $RepoRoot '.env'

function Fail([string]$Message) {
    Write-Host "FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $eq = $t.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $t.Substring(0, $eq).Trim()
        $val = $t.Substring($eq + 1)
        $val = ($val -replace '\s+#.*$', '').Trim()
        $values[$key] = $val
    }
    return $values
}

function Parse-DbUrl([string]$Name, [string]$Url) {
    $m = [regex]::Match($Url, '^postgresql(\+[a-z0-9]+)?://([^:/@]+):([^@]*)@([^:/@]+):(\d+)/([^/?#]+)$')
    if (-not $m.Success) {
        Fail "$Name is not in the form postgresql+psycopg://user:password@host:port/database"
    }
    $parsed = [pscustomobject]@{
        User     = $m.Groups[2].Value
        Password = $m.Groups[3].Value
        DbHost   = $m.Groups[4].Value
        Port     = $m.Groups[5].Value
        Database = $m.Groups[6].Value
    }
    if ($parsed.Password -eq '' -or $parsed.Password -eq 'CHANGE_ME') {
        Fail "$Name still has the password CHANGE_ME (or none). Set a password in .env first."
    }
    if ($parsed.Password -notmatch '^[A-Za-z0-9._~-]+$') {
        Fail "$Name password may contain only letters, digits and . _ ~ - (so it needs no URL encoding)."
    }
    foreach ($ident in @($parsed.User, $parsed.Database)) {
        if ($ident -notmatch '^[a-z_][a-z0-9_]*$') {
            Fail "$Name has '$ident'; user and database names must be lower-case letters, digits and _."
        }
    }
    return $parsed
}

function Find-Psql([string]$Explicit) {
    if ($Explicit -ne '') {
        if (Test-Path -LiteralPath $Explicit) { return $Explicit }
        Fail "psql not found at $Explicit"
    }
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
    if (Test-Path -LiteralPath $default) { return $default }
    Fail 'psql not found. Add the PostgreSQL bin folder to PATH or pass -PsqlPath.'
}

# Runs one SQL statement and returns its trimmed output. Uses the password in $env:PGPASSWORD.
function Invoke-PsqlQuery([string]$User, [string]$Database, [string]$Sql) {
    $out = & $script:Psql -X -w -q -t -A -v ON_ERROR_STOP=1 `
        -h $script:Db.DbHost -p $script:Db.Port -U $User -d $Database -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed (user $User, database $Database)." }
    return ("$out").Trim()
}

# Runs a SQL script given on stdin, so passwords never appear on the command line.
function Invoke-PsqlScript([string]$User, [string]$Database, [string]$Sql) {
    $Sql | & $script:Psql -X -w -q -v ON_ERROR_STOP=1 `
        -h $script:Db.DbHost -p $script:Db.Port -U $User -d $Database -f - | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "psql script failed (user $User, database $Database)." }
}

# ---- 1. Read and check .env ----
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Fail ".env not found in $RepoRoot. Run: Copy-Item .env.example .env   then set the password."
}
$envValues = Read-DotEnv $EnvFile
foreach ($k in 'DATABASE_URL', 'TEST_DATABASE_URL') {
    if (-not $envValues.ContainsKey($k) -or $envValues[$k] -eq '') { Fail "$k is missing from .env" }
}
$Db     = Parse-DbUrl 'DATABASE_URL' $envValues['DATABASE_URL']
$TestDb = Parse-DbUrl 'TEST_DATABASE_URL' $envValues['TEST_DATABASE_URL']
if ($Db.User -ne $TestDb.User -or $Db.Password -ne $TestDb.Password -or
    $Db.DbHost -ne $TestDb.DbHost -or $Db.Port -ne $TestDb.Port) {
    Fail 'DATABASE_URL and TEST_DATABASE_URL must use the same user, password, host and port.'
}
if ($Db.Database -eq $TestDb.Database) {
    Fail 'DATABASE_URL and TEST_DATABASE_URL must name different databases.'
}

$Psql = Find-Psql $PsqlPath
Write-Host "psql:      $Psql"
Write-Host ("server:    {0}:{1}" -f $Db.DbHost, $Db.Port)
Write-Host "app user:  $($Db.User)"
Write-Host "databases: $($Db.Database), $($TestDb.Database)"

# ---- 2. Superuser password (this process only) ----
$hadPgPassword = Test-Path Env:PGPASSWORD
$previousPgPassword = $null
if ($hadPgPassword) { $previousPgPassword = $env:PGPASSWORD }

try {
    if (-not $hadPgPassword) {
        $secure = Read-Host -AsSecureString "Password for PostgreSQL superuser '$SuperUser'"
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }

    try { $null = Invoke-PsqlQuery $SuperUser 'postgres' 'SELECT 1' }
    catch { Fail "Cannot connect as superuser '$SuperUser'. Check the password and that the service is running." }

    # ---- 3. Role ----
    $roleExists = Invoke-PsqlQuery $SuperUser 'postgres' "SELECT 1 FROM pg_roles WHERE rolname = '$($Db.User)'"
    if ($roleExists -eq '1') {
        Invoke-PsqlScript $SuperUser 'postgres' "ALTER ROLE `"$($Db.User)`" WITH LOGIN PASSWORD '$($Db.Password)';"
        Write-Host "role $($Db.User): already exists; password set to match .env"
    } else {
        Invoke-PsqlScript $SuperUser 'postgres' "CREATE ROLE `"$($Db.User)`" WITH LOGIN PASSWORD '$($Db.Password)';"
        Write-Host "role $($Db.User): created"
    }

    # ---- 4. Databases ----
    foreach ($name in @($Db.Database, $TestDb.Database)) {
        $dbExists = Invoke-PsqlQuery $SuperUser 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$name'"
        if ($dbExists -eq '1') {
            Write-Host "database ${name}: already exists"
        } else {
            $null = Invoke-PsqlQuery $SuperUser 'postgres' "CREATE DATABASE `"$name`" OWNER `"$($Db.User)`" ENCODING 'UTF8' TEMPLATE template0"
            Write-Host "database ${name}: created"
        }
    }

    # ---- 5. Connect as the app user ----
    $env:PGPASSWORD = $Db.Password
    foreach ($name in @($Db.Database, $TestDb.Database)) {
        try {
            $who = Invoke-PsqlQuery $Db.User $name "SELECT current_user || ' @ ' || current_database() || ' (server ' || current_setting('server_version') || ')'"
        } catch {
            Fail "User $($Db.User) cannot connect to database $name."
        }
        Write-Host "connect:   OK  $who" -ForegroundColor Green
    }
    Write-Host 'setup_db.ps1 finished.' -ForegroundColor Green
}
finally {
    if ($hadPgPassword) { $env:PGPASSWORD = $previousPgPassword }
    else { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
}
