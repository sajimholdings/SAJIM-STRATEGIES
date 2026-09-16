# ============================================================
# Dukascopy Downloader — One-Click Setup & Run
# ============================================================
# Run this from the data_downloader directory:
#   .\setup.ps1
# Or to run without downloading (just setup):
#   .\setup.ps1 -SetupOnly
# ============================================================

param(
    [switch]$SetupOnly,
    [int]$Workers = 10,
    [string[]]$Symbols,
    [string[]]$Timeframes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DUKASCOPY DATA DOWNLOADER — SETUP" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ── 1. Locate Python ──────────────────────────────────────────
Write-Host "`n[1/4] Locating Python..." -ForegroundColor Yellow
$Python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3\.") {
            $Python = $candidate
            Write-Host "  Found: $ver ($candidate)" -ForegroundColor Green
            break
        }
    } catch {}
}
if (-not $Python) {
    Write-Host "  ERROR: Python 3 not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# ── 2. Create venv ────────────────────────────────────────────
$VenvDir = Join-Path $ScriptDir "venv"
Write-Host "`n[2/4] Setting up virtual environment..." -ForegroundColor Yellow

if (-not (Test-Path $VenvDir)) {
    Write-Host "  Creating venv at: $VenvDir" -ForegroundColor Gray
    & $Python -m venv $VenvDir
    Write-Host "  Venv created." -ForegroundColor Green
} else {
    Write-Host "  Venv already exists — skipping creation." -ForegroundColor Cyan
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

# ── 3. Install dependencies ───────────────────────────────────
Write-Host "`n[3/4] Installing dependencies..." -ForegroundColor Yellow
Write-Host "  Upgrading pip..." -ForegroundColor Gray
& $VenvPip install --upgrade pip --quiet

Write-Host "  Installing from requirements.txt..." -ForegroundColor Gray
& $VenvPip install -r (Join-Path $ScriptDir "requirements.txt") --quiet

# Verify dukascopy_python installed
$checkResult = & $VenvPython -c "import dukascopy_python; print('OK')" 2>&1
if ($checkResult -eq "OK") {
    Write-Host "  dukascopy_python: OK" -ForegroundColor Green
} else {
    Write-Host "  dukascopy_python import failed: $checkResult" -ForegroundColor Red
    Write-Host "  Trying alternate package name..." -ForegroundColor Yellow
    & $VenvPip install dukascopy --quiet
}

Write-Host "  All dependencies installed." -ForegroundColor Green

if ($SetupOnly) {
    Write-Host "`nSetup complete. Run the downloader with:" -ForegroundColor Cyan
    Write-Host "  .\setup.ps1 -Workers 10" -ForegroundColor White
    exit 0
}

# ── 4. Run downloader ─────────────────────────────────────────
Write-Host "`n[4/4] Starting download..." -ForegroundColor Yellow

$Args = @(
    (Join-Path $ScriptDir "download_all.py"),
    "--workers", $Workers
)

if ($DryRun)     { $Args += "--dry-run" }
if ($Symbols)    { $Args += @("--symbol") + $Symbols }
if ($Timeframes) { $Args += @("--tf") + $Timeframes }

Write-Host "  Command: $VenvPython $($Args -join ' ')" -ForegroundColor Gray
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

& $VenvPython @Args

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Download complete!" -ForegroundColor Green

$ReportPath = Join-Path $ScriptDir "download_report.md"
if (Test-Path $ReportPath) {
    Write-Host "  Report: $ReportPath" -ForegroundColor White
}

$DataDir = Join-Path $ScriptDir "..\data\dukascopy"
if (Test-Path $DataDir) {
    $TotalMB = [math]::Round((Get-ChildItem $DataDir -Recurse -File |
                Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host "  Data folder size: $TotalMB MB" -ForegroundColor White
}
Write-Host "============================================================" -ForegroundColor Cyan
