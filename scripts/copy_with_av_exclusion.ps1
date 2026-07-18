# Copy a tree with the destination temporarily excluded from Microsoft Defender
# real-time scanning, then remove the exclusion again -- always, even on error.
#
# Why: the antivirus minifilter is a large part of the per-file cost measured in
# docs/benchmark.md. Excluding the destination removes that tax for every copy
# tool equally. It is a deployment decision with a real security cost, not a
# code speed-up -- read the warnings below before using it.
#
# About elevation (the honest version):
#   * You CANNOT force-elevate a running process or elevate it silently. UAC
#     prompts at process launch, by design. This script therefore RELAUNCHES
#     itself elevated (one UAC prompt) if you did not start it elevated.
#   * "Releasing admin afterwards" is not an in-place privilege drop -- the
#     elevated helper simply finishes its work and exits. Your original shell
#     was never elevated.
#
# SECURITY WARNINGS -- understand these:
#   * While the exclusion is active, anything written into the destination is NOT
#     scanned in real time. Do NOT use this when copying from an untrusted source.
#   * If this script is hard-killed (Task Manager, power loss) between adding and
#     removing the exclusion, the exclusion is LEFT BEHIND -- a persistent hole.
#     The finally block guards normal exit and Ctrl-C; it cannot guard kill -9.
#   * Defender only. Third-party AV cannot be controlled through Add-MpPreference.
#   * Defender Tamper Protection (default ON in Win11) may block or silently
#     ignore exclusion changes; the script warns if it detects this.
#
# Usage (from any prompt -- it self-elevates):
#   .\scripts\copy_with_av_exclusion.ps1 -Source "D:\src" -Dest "E:\dst"
#   .\scripts\copy_with_av_exclusion.ps1 -Source "D:\src" -Dest "E:\dst" -Engine robocopy
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Source,
    [Parameter(Mandatory)] [string]$Dest,
    [int]$Threads = 16,
    [ValidateSet('scatter', 'robocopy')] [string]$Engine = 'scatter'
)

$ErrorActionPreference = 'Stop'

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- self-elevate (one UAC prompt) if needed --------------------------------
if (-not (Test-Admin)) {
    Write-Host "Not elevated -- relaunching with a UAC prompt ..." -ForegroundColor Yellow
    $relaunch = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $PSCommandPath,
        '-Source', $Source, '-Dest', $Dest, '-Threads', "$Threads", '-Engine', $Engine
    )
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $relaunch
    } catch {
        Write-Warning "UAC was declined or elevation failed. Nothing was changed."
    }
    return   # the non-elevated instance stops here; the elevated one does the work
}

# --- elevated from here -----------------------------------------------------
$repo = Split-Path -Parent $PSScriptRoot
$n2   = Join-Path $repo 'experiments\native_copy2.exe'
$destFull = [IO.Path]::GetFullPath($Dest)

if ($Engine -eq 'scatter' -and -not (Test-Path $n2)) {
    throw "missing $n2 -- build it: cl /O2 /EHsc /std:c++17 experiments\native_copy2.cpp"
}

$tp = try { (Get-MpComputerStatus).IsTamperProtected } catch { $null }
if ($tp) {
    Write-Warning "Defender Tamper Protection is ON -- the exclusion may be blocked or ignored."
    Write-Warning "If the copy is not faster, that is why. Disable Tamper Protection in Windows Security to test."
}

New-Item -ItemType Directory $destFull -Force | Out-Null

$added = $false
try {
    Write-Host "Adding Defender real-time exclusion: $destFull" -ForegroundColor Cyan
    Add-MpPreference -ExclusionPath $destFull
    $added = $true

    Write-Host "Copying ($Engine, $Threads threads) ..." -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if ($Engine -eq 'scatter') {
        & $n2 $Source $destFull $Threads
    } else {
        robocopy $Source $destFull /E /MT:$Threads /NFL /NDL /NJH /NJS /NP | Out-Null
    }
    $sw.Stop()
    Write-Host ("copy finished in {0:N2}s" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
}
finally {
    # Remove the exclusion no matter what -- success, exception, or Ctrl-C.
    if ($added) {
        Write-Host "Removing Defender exclusion ..." -ForegroundColor Cyan
        try { Remove-MpPreference -ExclusionPath $destFull } catch {
            Write-Warning "FAILED to remove exclusion for $destFull -- REMOVE IT MANUALLY."
        }
        $still = (Get-MpPreference).ExclusionPath -contains $destFull
        if ($still) {
            Write-Warning "SECURITY: exclusion STILL PRESENT for $destFull. Remove it:"
            Write-Warning "  Remove-MpPreference -ExclusionPath '$destFull'"
        } else {
            Write-Host "exclusion removed and verified." -ForegroundColor DarkGray
        }
    }
}
