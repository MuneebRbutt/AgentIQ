<#
.SYNOPSIS
    Runs every quality gate CI runs: format, lint, type check, tests and build.

.DESCRIPTION
    Run from anywhere:  .\scripts\check.ps1
    Backend only:       .\scripts\check.ps1 -Backend
    Frontend only:      .\scripts\check.ps1 -Frontend
    Auto-fix format:    .\scripts\check.ps1 -Fix

    Integration tests run when `docker compose up -d` is up and are skipped otherwise.
    They use a separate agentiq_test database, never your development data.
    Expects backend\.venv to exist (see README).
#>
param(
    [switch]$Backend,
    [switch]$Frontend,
    [switch]$Fix
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runAll = -not ($Backend -or $Frontend)

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if ($runAll -or $Backend) {
    Push-Location (Join-Path $root "backend")
    try {
        $python = Join-Path ".venv" "Scripts\python.exe"
        if (-not (Test-Path $python)) { $python = "python" }

        if ($Fix) {
            Invoke-Step "ruff format" { & $python -m ruff format . }
            Invoke-Step "ruff check --fix" { & $python -m ruff check --fix . }
        } else {
            Invoke-Step "ruff format --check" { & $python -m ruff format --check . }
            Invoke-Step "ruff check" { & $python -m ruff check . }
        }
        Invoke-Step "mypy" { & $python -m mypy }
        Invoke-Step "pytest" { & $python -m pytest }
    } finally {
        Pop-Location
    }
}

if ($runAll -or $Frontend) {
    Push-Location (Join-Path $root "frontend")
    try {
        Invoke-Step "API client in sync" { npm run gen:api; git diff --exit-code src/api/schema.d.ts }
        Invoke-Step "oxlint" { npm run lint }
        Invoke-Step "tsc" { npm run typecheck }
        Invoke-Step "vitest" { npm test }
        Invoke-Step "vite build" { npm run build }
    } finally {
        Pop-Location
    }
}

Write-Host "`nAll checks passed." -ForegroundColor Green
