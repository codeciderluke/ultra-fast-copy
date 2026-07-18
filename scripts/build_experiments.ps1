# Build the native benchmark experiments with MSVC (cl.exe).
#
# These are throwaway measurement tools, not shipped binaries -- the .exe/.obj
# they produce are git-ignored. Run this to reproduce docs/benchmark.md.
#
# Needs Visual Studio (or Build Tools) with the C++ workload. The script finds
# it via vswhere and enters the x64 developer environment automatically.
#
# Usage:
#   .\scripts\build_experiments.ps1            # build all three
#   .\scripts\build_experiments.ps1 -Clean     # delete .exe/.obj and exit
[CmdletBinding()]
param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$exp  = Join-Path $repo 'experiments'

if ($Clean) {
    Get-ChildItem $exp -Include *.exe, *.obj -File -Recurse | ForEach-Object {
        Remove-Item $_.FullName -Force; Write-Host "removed $($_.Name)" -ForegroundColor DarkGray
    }
    Write-Host "clean done." -ForegroundColor Green
    return
}

# Each experiment and the libraries it links.
$targets = @(
    @{ src = 'native_copy.cpp';     libs = @() }
    @{ src = 'native_copy2.cpp';    libs = @() }
    @{ src = 'ntfs_block_copy.cpp'; libs = @('vssapi.lib', 'ole32.lib') }
)

# Enter the MSVC x64 developer shell.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere not found -- install Visual Studio or the C++ Build Tools." }
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw "no MSVC C++ toolset found -- install the 'Desktop development with C++' workload." }
Import-Module (Join-Path $vsPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -no_logo' | Out-Null

Push-Location $exp
try {
    $fail = 0
    foreach ($t in $targets) {
        Write-Host "building $($t.src) ..." -ForegroundColor Cyan
        $cl = @('/nologo', '/O2', '/EHsc', '/std:c++17', '/W4', $t.src)
        if ($t.libs) { $cl += '/link'; $cl += $t.libs }
        & cl @cl
        if ($LASTEXITCODE -ne 0) { Write-Warning "FAILED: $($t.src)"; $fail++ }
    }
    Write-Host ""
    if ($fail) { throw "$fail target(s) failed to build." }
    Get-ChildItem $exp -Filter *.exe | Select-Object Name, @{n = 'KB'; e = { [math]::Round($_.Length / 1KB, 0) } } |
        Format-Table -AutoSize | Out-Host
    Write-Host "all experiments built." -ForegroundColor Green
} finally {
    Pop-Location
}
