# Compare block-level imaging (ntfs_block_copy) against the file-by-file tools
# -- Robocopy, native_copy, shutil.copytree, Ultra Fast Copy -- on IDENTICAL
# content, fairly.
#
# The fairness problem: block imaging is volume-granular, file copy is
# subtree-granular. You cannot point them at the same folder and call it even.
# So this builds a throwaway NTFS volume (an expandable VHD), fills it with test
# data, then races:
#   * file tools copying the volume's file TREE to a scratch folder
#   * ntfs_block_copy imaging the whole VOLUME (allocated clusters only)
# Same bytes on disk, two philosophies, one wall clock.
#
# Requires an elevated prompt: VHD attach and raw-volume/VSS reads need admin.
#
# Usage:
#   .\scripts\block_benchmark.ps1
#   .\scripts\block_benchmark.ps1 -SmallCount 50000 -VhdSizeMB 8192
#   .\scripts\block_benchmark.ps1 -UseVss        # block copier snapshots first
#   .\scripts\block_benchmark.ps1 -KeepVhd       # leave the VHD mounted for inspection
[CmdletBinding()]
param(
    [int]$SmallCount = 20000,
    [int]$VhdSizeMB  = 4096,
    [string]$Root    = (Join-Path $env:TEMP "ufc_block_bench"),
    [switch]$UseVss,
    [switch]$KeepVhd
)

$ErrorActionPreference = "Stop"

# -- preflight -------------------------------------------------------------

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run this from an elevated (Administrator) PowerShell -- VHD attach and raw volume reads require it." }

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$block  = Join-Path $repo "experiments\ntfs_block_copy.exe"
$native = Join-Path $repo "experiments\native_copy.exe"
$ufc    = Join-Path $repo "dist\ufCopy.exe"
if (-not (Test-Path $ufc)) { $ufc = Join-Path $repo ".venv\Scripts\ufCopy.exe" }

if (-not (Test-Path $block)) { throw "missing $block -- build it: cl /O2 /EHsc /std:c++17 experiments\ntfs_block_copy.cpp /link vssapi.lib ole32.lib" }

New-Item -ItemType Directory $Root -Force | Out-Null
$vhdPath = Join-Path $Root "bench.vhd"
$imgPath = Join-Path $Root "bench.img"

# -- helpers ---------------------------------------------------------------

function Invoke-Diskpart([string[]]$Lines) {
    $script = Join-Path $Root ("dp_{0}.txt" -f [Guid]::NewGuid().ToString('N'))
    Set-Content -Path $script -Value ($Lines -join "`r`n") -Encoding ASCII
    try {
        $out = & diskpart.exe /s $script 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "diskpart failed:`n$out" }
        return $out
    } finally { Remove-Item $script -ErrorAction SilentlyContinue }
}

function Get-FreeDriveLetter {
    $used = (Get-PSDrive -PSProvider FileSystem).Name
    foreach ($c in [char[]]([char]'Z'..[char]'G')) { if ($used -notcontains "$c") { return "$c" } }
    throw "no free drive letter"
}

function New-SmallFiles([string]$Dir, [int]$Count) {
    New-Item -ItemType Directory $Dir -Force | Out-Null
    $rand = [Random]::new(42)   # fixed seed: identical tree every run
    $sub = $Dir
    for ($i = 0; $i -lt $Count; $i++) {
        if ($i % 500 -eq 0) {
            $sub = Join-Path $Dir ("dir_{0:D3}" -f ($i / 500))
            New-Item -ItemType Directory $sub -Force | Out-Null
        }
        $bytes = New-Object byte[] $rand.Next(1024, 65536)
        $rand.NextBytes($bytes)
        [IO.File]::WriteAllBytes((Join-Path $sub ("f_{0:D6}.bin" -f $i)), $bytes)
    }
}

function Get-TreeStats([string]$Dir) {
    if (-not (Test-Path $Dir)) { return @{ Count = 0; Bytes = 0 } }
    $files = Get-ChildItem -Recurse -File $Dir -ErrorAction SilentlyContinue
    return @{
        Count = ($files | Measure-Object).Count
        Bytes = ($files | Measure-Object -Property Length -Sum).Sum
    }
}

$results = @()
function Add-Result([string]$Tool, [string]$Kind, [double]$Seconds, [double]$MB, [int]$Files) {
    $script:results += [PSCustomObject]@{
        Tool     = $Tool
        Kind     = $Kind
        Seconds  = [math]::Round($Seconds, 2)
        MBs      = if ($Seconds -gt 0) { [math]::Round($MB / $Seconds, 0) } else { 0 }
        FilesSec = if ($Seconds -gt 0 -and $Files -gt 0) { [math]::Round($Files / $Seconds, 0) } else { 0 }
        DataMB   = [math]::Round($MB, 0)
    }
}

# -- build the test volume -------------------------------------------------

$letter = Get-FreeDriveLetter
Write-Host "Block-imaging benchmark" -ForegroundColor Cyan
Write-Host "VHD: $vhdPath  ->  ${letter}:  (${VhdSizeMB} MB expandable, NTFS)" -ForegroundColor DarkGray

if (Test-Path $vhdPath) { Invoke-Diskpart @("select vdisk file=`"$vhdPath`"", "detach vdisk") 2>$null | Out-Null; Remove-Item $vhdPath -Force -ErrorAction SilentlyContinue }

Invoke-Diskpart @(
    "create vdisk file=`"$vhdPath`" maximum=$VhdSizeMB type=expandable",
    "attach vdisk",
    "create partition primary",
    "format fs=ntfs quick label=ufcbench",
    "assign letter=$letter"
) | Out-Null

# Windows may pop an AutoPlay/format dialog on a fresh volume; give it a beat.
Start-Sleep -Milliseconds 500
$vol = "${letter}:"
if (-not (Test-Path "$vol\")) { throw "volume $vol did not mount" }

try {
    $dataDir = Join-Path "$vol\" "data"
    Write-Host "generating $SmallCount small files on $vol ..." -ForegroundColor DarkGray
    New-SmallFiles $dataDir $SmallCount
    $stats = Get-TreeStats $dataDir
    $dataMB = $stats.Bytes / 1MB
    Write-Host ("data: {0:N0} files, {1:N1} MB" -f $stats.Count, $dataMB) -ForegroundColor DarkGray

    # -- file-by-file tools: copy the TREE off the volume ------------------

    $destRoot = Join-Path $Root "dest"

    Remove-Item -Recurse -Force (Join-Path $destRoot "robocopy") -ErrorAction SilentlyContinue
    $dst = Join-Path $destRoot "robocopy"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    robocopy $dataDir $dst /E /MT:16 /NFL /NDL /NJH /NJS /NP | Out-Null
    $sw.Stop(); Add-Result "Robocopy /MT:16" "file-tree" $sw.Elapsed.TotalSeconds $dataMB $stats.Count

    if (Test-Path $native) {
        Remove-Item -Recurse -Force (Join-Path $destRoot "native") -ErrorAction SilentlyContinue
        $dst = Join-Path $destRoot "native"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        & $native $dataDir $dst 16 | Out-Null
        $sw.Stop(); Add-Result "native_copy (C++, 16t)" "file-tree" $sw.Elapsed.TotalSeconds $dataMB $stats.Count
    }

    if (Test-Path $python) {
        Remove-Item -Recurse -Force (Join-Path $destRoot "shutil") -ErrorAction SilentlyContinue
        $dst = Join-Path $destRoot "shutil"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        & $python -c "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])" $dataDir $dst | Out-Null
        $sw.Stop(); Add-Result "shutil.copytree" "file-tree" $sw.Elapsed.TotalSeconds $dataMB $stats.Count
    }

    if (Test-Path $ufc) {
        Remove-Item -Recurse -Force (Join-Path $destRoot "ufc") -ErrorAction SilentlyContinue
        $dst = Join-Path $destRoot "ufc"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        & $ufc copy $dataDir $dst --verify none --conflict overwrite --no-resume --quiet | Out-Null
        $sw.Stop(); Add-Result "UltraFastCopy (exe)" "file-tree" $sw.Elapsed.TotalSeconds $dataMB $stats.Count
    }

    # -- block imager: image the whole VOLUME ------------------------------
    # Parse "used=NNNMB ... time=X.XXs" from the tool's own report.

    $mode = if ($UseVss) { @() } else { @("--live") }

    Remove-Item $imgPath -Force -ErrorAction SilentlyContinue
    $line = (& $block $vol $imgPath @mode) | Select-Object -Last 1
    Write-Host "  block(image): $line" -ForegroundColor DarkGray
    if ($line -match "used=([\d.]+)MB.*time=([\d.]+)s") {
        Add-Result "ntfs_block_copy (image)" $(if ($UseVss) { "volume/vss" } else { "volume/live" }) ([double]$Matches[2]) ([double]$Matches[1]) 0
    }

    $line = (& $block $vol "--read-only" @mode) | Select-Object -Last 1
    Write-Host "  block(read) : $line" -ForegroundColor DarkGray
    if ($line -match "used=([\d.]+)MB.*time=([\d.]+)s") {
        Add-Result "ntfs_block_copy (read only)" $(if ($UseVss) { "volume/vss" } else { "volume/live" }) ([double]$Matches[2]) ([double]$Matches[1]) 0
    }

    # -- report ------------------------------------------------------------

    Write-Host ""
    $sorted = $results | Sort-Object Seconds
    $sorted | Format-Table Tool, Kind, Seconds, MBs, FilesSec, DataMB -AutoSize | Out-Host

    # Persist results so a non-interactive reader can pick them up (the repo path
    # survives the $Root cleanup in finally).
    $csv = Join-Path $repo "experiments\block_bench_result.csv"
    $txt = Join-Path $repo "experiments\block_bench_result.txt"
    $sorted | Export-Csv -Path $csv -NoTypeInformation -Encoding UTF8
    $header = "# block_benchmark  SmallCount=$SmallCount  VhdSizeMB=$VhdSizeMB  mode=$(if($UseVss){'vss'}else{'live'})"
    ($header, ($sorted | Format-Table Tool, Kind, Seconds, MBs, FilesSec, DataMB -AutoSize | Out-String)) | Set-Content -Path $txt -Encoding UTF8

    $fastFile = ($results | Where-Object Kind -eq "file-tree" | Sort-Object Seconds | Select-Object -First 1)
    $blockImg = ($results | Where-Object Tool -like "*image*" | Select-Object -First 1)
    if ($fastFile -and $blockImg -and $blockImg.Seconds -gt 0) {
        $ratio = [math]::Round($fastFile.Seconds / $blockImg.Seconds, 2)
        $verdict = "block image vs fastest file tool ({0}): {1}x" -f $fastFile.Tool, $ratio
        Write-Host $verdict -ForegroundColor Green
        Write-Host "Note: block copies used clusters (data + NTFS metadata); file tools copy file data only." -ForegroundColor DarkGray
        Write-Host "The honest metric is wall-clock to produce a usable copy of the same volume." -ForegroundColor DarkGray
        Add-Content -Path $txt -Value $verdict -Encoding UTF8
    }
    Write-Host "results written to: $csv" -ForegroundColor DarkGray
}
finally {
    Remove-Item -Recurse -Force (Join-Path $Root "dest") -ErrorAction SilentlyContinue
    Remove-Item $imgPath -Force -ErrorAction SilentlyContinue
    if (-not $KeepVhd) {
        Write-Host "detaching and deleting VHD ..." -ForegroundColor DarkGray
        Invoke-Diskpart @("select vdisk file=`"$vhdPath`"", "detach vdisk") | Out-Null
        Remove-Item $vhdPath -Force -ErrorAction SilentlyContinue
        if (-not $KeepVhd) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
    } else {
        Write-Host "VHD left mounted at ${letter}: (--KeepVhd). Detach: diskpart> select vdisk file=`"$vhdPath`" / detach vdisk" -ForegroundColor DarkGray
    }
}
