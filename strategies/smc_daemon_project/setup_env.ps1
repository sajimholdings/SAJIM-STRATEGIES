# setup_env.ps1
# Automates the setup of a localized, 100% compatible Python 3.10 environment inside smc_daemon_project.
# Runs with standard user privileges (NO Admin required).

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "     SMC TRADING DAEMON LOCAL PY3.10 ENVIRONMENT SETUP" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$ProjectDir = "c:\Users\Arnold\Desktop\smc_daemon_project"
$EnvDir = Join-Path $ProjectDir "python_env"
$ZipPath = Join-Path $EnvDir "python_embed.zip"
$PthFile = Join-Path $EnvDir "python310._pth"

# 1. Create target directory
if (-not (Test-Path $EnvDir)) {
    Write-Host "[1/6] Creating python_env directory..." -ForegroundColor Green
    New-Item -ItemType Directory -Force -Path $EnvDir | Out-Null
} else {
    Write-Host "[1/6] python_env directory already exists. Re-initializing..." -ForegroundColor Yellow
}

# 2. Download Python 3.10.11 Embeddable Runtime
Write-Host "[2/6] Downloading Python 3.10.11 embeddable runtime..." -ForegroundColor Green
$DownloadUrl = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath

# 3. Extract Python
Write-Host "[3/6] Extracting Python package..." -ForegroundColor Green
Expand-Archive -Path $ZipPath -DestinationPath $EnvDir -Force
Remove-Item -Path $ZipPath -Force

# 4. Enable site-packages in python310._pth
Write-Host "[4/6] Configuring Python path configuration to support external packages..." -ForegroundColor Green
if (Test-Path $PthFile) {
    # Check if 'import site' is already present
    $Content = Get-Content $PthFile
    if ($Content -notcontains "import site") {
        Add-Content -Path $PthFile -Value "import site"
    }
} else {
    Write-Host "Warning: python310._pth not found! Creating default..." -ForegroundColor Yellow
    $DefaultPth = @(
        "python310.zip",
        ".",
        "",
        "import site"
    )
    $DefaultPth | Out-File -FilePath $PthFile -Encoding ascii -Force
}

# 5. Install pip
Write-Host "[5/6] Downloading and installing pip..." -ForegroundColor Green
$GetPipScript = Join-Path $EnvDir "get-pip.py"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipScript

$PythonExe = Join-Path $EnvDir "python.exe"
Write-Host "Running get-pip.py..." -ForegroundColor Cyan
& $PythonExe $GetPipScript --no-warn-script-location
Remove-Item -Path $GetPipScript -Force

# 6. Install Dependencies
Write-Host "[6/6] Installing daemon dependencies (MetaTrader5, pandas, numpy, python-dotenv)..." -ForegroundColor Green
$PipExe = Join-Path $EnvDir "Scripts\pip.exe"
& $PipExe install MetaTrader5 pandas numpy python-dotenv

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " SUCCESS: Localized Python 3.10 Environment Setup Complete!" -ForegroundColor Green
Write-Host " To run your script, use:" -ForegroundColor Cyan
Write-Host "   .\python_env\python.exe smc_daemon.py" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
