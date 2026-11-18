# Builds the Windows installer:  dist\installer\MinecraftServerMaster-Setup-<version>.exe
#
# Requirements on the build machine:
#   * Python 3.10+ (from python.org, "Add to PATH" ticked)
#   * Inno Setup 6.3+ (https://jrsoftware.org/isinfo.php) - or pass -SkipInstaller
#
# Usage (PowerShell, from the repository root):
#   powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1
#
# Steps: fresh virtual environment -> run tests -> PyInstaller bundle -> Inno Setup.
# See docs/INSTALLER.md for details.

param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Version comes from mcsm/__init__.py so there is a single source of truth.
$Version = (Select-String -Path "mcsm\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Building Minecraft Server Master $Version" -ForegroundColor Cyan

# 1. Isolated build environment
$Venv = Join-Path $Root "build\venv"
if (-not (Test-Path $Venv)) { & $Python -m venv $Venv }
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install --upgrade --quiet pip
& $Py -m pip install --quiet pyinstaller tzdata pytest
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. Tests
if (-not $SkipTests) {
    & $Py -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

# 3. Windows version resource for the .exe
$Parts = ($Version -split '[.\-+]')[0..2] | ForEach-Object { [int]($_ -replace '\D', '') }
while ($Parts.Count -lt 3) { $Parts += 0 }
$Tuple = "($($Parts[0]), $($Parts[1]), $($Parts[2]), 0)"
(Get-Content installer\version_info.txt -Raw) `
    -replace 'filevers=\([^)]*\)', "filevers=$Tuple" `
    -replace 'prodvers=\([^)]*\)', "prodvers=$Tuple" `
    -replace "'(FileVersion|ProductVersion)', '[^']*'", "'`$1', '$Version'" |
    Set-Content installer\version_info.txt -Encoding UTF8

# 4. PyInstaller bundle -> dist\MinecraftServerMaster\
& $Py -m PyInstaller installer\mcsm.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Smoke test the bundled command-line tool.
& "dist\MinecraftServerMaster\mcsm.exe" --version
if ($LASTEXITCODE -ne 0) { throw "bundled mcsm.exe does not run" }

if ($SkipInstaller) { Write-Host "Bundle ready in dist\MinecraftServerMaster (installer skipped)"; exit 0 }

# 5. Inno Setup
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($Candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                             "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $Candidate) { $Iscc = $Candidate; break }
    }
}
if (-not $Iscc) { throw "Inno Setup (ISCC.exe) not found. Install it from https://jrsoftware.org/isinfo.php or use -SkipInstaller." }
& $Iscc "/DMyAppVersion=$Version" installer\mcsm.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-ChildItem dist\installer\*.exe | ForEach-Object { Write-Host "Installer: $($_.FullName)" -ForegroundColor Green }
