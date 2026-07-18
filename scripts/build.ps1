# Build both executables into dist\. Usage: .\scripts\build.ps1 [-Cli] [-Gui]
[CmdletBinding()]
param(
    [switch]$Cli,
    [switch]$Gui,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Neither switch given means build both.
if (-not $Cli -and -not $Gui) { $Cli = $true; $Gui = $true }

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if ($Clean) {
    Write-Host "Cleaning build\ and dist\..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

Write-Host "Rendering the icon..." -ForegroundColor Cyan
& $python scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

if ($Cli) {
    Write-Host "Building ufCopy.exe (CLI)..." -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm ufCopy.spec
    if ($LASTEXITCODE -ne 0) { throw "CLI build failed." }
}

if ($Gui) {
    Write-Host "Building ufCopyTool.exe (GUI)..." -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm ufCopyTool.spec
    if ($LASTEXITCODE -ne 0) { throw "GUI build failed." }
}

Write-Host "`nBuilt:" -ForegroundColor Green
Get-ChildItem dist\*.exe | ForEach-Object {
    "{0,-24} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)
}

# Checksums are part of the release bundle.
$checksums = Join-Path $root "dist\SHA256SUMS.txt"
Get-ChildItem dist\*.exe | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } |
    Set-Content $checksums -Encoding utf8
Write-Host "Checksums written to $checksums" -ForegroundColor Green
