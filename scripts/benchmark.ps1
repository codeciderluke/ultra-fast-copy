# Compare Ultra Fast Copy against Explorer, Robocopy, and shutil.copytree.
#
# Usage:
#   .\scripts\benchmark.ps1                       # small-file + large-file scenarios
#   .\scripts\benchmark.ps1 -SmallCount 50000
#   .\scripts\benchmark.ps1 -SkipExplorer         # Explorer's COM copy is the slow one
#
# Absolute numbers describe this disk, not the software. Compare the ratios.
[CmdletBinding()]
param(
    [int]$SmallCount = 20000,
    [int]$LargeCount = 6,
    [int]$LargeSizeMB = 200,
    [string]$Root = (Join-Path $env:TEMP "ufc_bench"),
    [switch]$SkipExplorer,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$ufc = Join-Path $repo "dist\ufCopy.exe"
if (-not (Test-Path $ufc)) { $ufc = Join-Path $repo ".venv\Scripts\ufCopy.exe" }

function New-SmallFiles([string]$Dir, [int]$Count) {
    New-Item -ItemType Directory $Dir -Force | Out-Null
    $rand = [Random]::new(42)  # fixed seed: same data every run
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

function New-LargeFiles([string]$Dir, [int]$Count, [int]$SizeMB) {
    New-Item -ItemType Directory $Dir -Force | Out-Null
    $chunk = New-Object byte[] (1MB)
    [Random]::new(7).NextBytes($chunk)
    for ($i = 0; $i -lt $Count; $i++) {
        $stream = [IO.File]::Create((Join-Path $Dir ("big_{0:D2}.bin" -f $i)))
        try { for ($m = 0; $m -lt $SizeMB; $m++) { $stream.Write($chunk, 0, $chunk.Length) } }
        finally { $stream.Close() }
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

function Copy-WithExplorer([string]$Src, [string]$Dst, [int]$Expected) {
    # Shell.Application IS the Explorer copy engine. CopyHere is asynchronous,
    # so completion is detected by polling the destination file count.
    New-Item -ItemType Directory $Dst -Force | Out-Null
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.NameSpace($Dst)
    # 4 = no progress UI, 16 = yes to all, 512 = no confirm dir, 1024 = no error UI
    $folder.CopyHere($Src, 4 + 16 + 512 + 1024)

    $landed = Join-Path $Dst (Split-Path $Src -Leaf)
    $deadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
        if ((Get-TreeStats $landed).Count -ge $Expected) { break }
    }
    [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

function Invoke-Bench {
    param([string]$Name, [string]$Dst, [int]$Expected, [long]$ExpectedBytes, [scriptblock]$Action)

    Remove-Item -Recurse -Force $Dst -ErrorAction SilentlyContinue
    [GC]::Collect()
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $Action
    $sw.Stop()

    $stats = Get-TreeStats $Dst
    $ok = ($stats.Count -eq $Expected) -and ($stats.Bytes -eq $ExpectedBytes)
    [PSCustomObject]@{
        Tool     = $Name
        Seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        FilesSec = if ($sw.Elapsed.TotalSeconds -gt 0) { [math]::Round($stats.Count / $sw.Elapsed.TotalSeconds, 0) } else { 0 }
        MBs      = if ($sw.Elapsed.TotalSeconds -gt 0) { [math]::Round(($stats.Bytes / 1MB) / $sw.Elapsed.TotalSeconds, 1) } else { 0 }
        Files    = $stats.Count
        Intact   = if ($ok) { "yes" } else { "NO" }
    }
}

function Invoke-Scenario {
    param([string]$Title, [string]$Src, [string]$DstRoot)

    $expect = Get-TreeStats $Src
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
    Write-Host ("source: {0:N0} files, {1:N1} MB" -f $expect.Count, ($expect.Bytes / 1MB)) -ForegroundColor DarkGray

    $results = @()

    if (-not $SkipExplorer) {
        $dst = Join-Path $DstRoot "explorer"
        $results += Invoke-Bench "Explorer (shell)" (Join-Path $dst (Split-Path $Src -Leaf)) $expect.Count $expect.Bytes {
            Copy-WithExplorer $Src $dst $expect.Count
        }
    }

    $dst = Join-Path $DstRoot "robocopy"
    $results += Invoke-Bench "Robocopy /MT:16" $dst $expect.Count $expect.Bytes {
        robocopy $Src $dst /E /MT:16 /NFL /NDL /NJH /NJS /NP | Out-Null
    }

    $dst = Join-Path $DstRoot "shutil"
    $results += Invoke-Bench "shutil.copytree" $dst $expect.Count $expect.Bytes {
        & $python -c "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])" $Src $dst | Out-Null
    }

    # Run under python.exe too. shutil and robocopy are trusted binaries, while a
    # freshly built unsigned .exe is scanned harder by real-time antivirus, so
    # this is the only like-for-like row against shutil.
    $dst = Join-Path $DstRoot "ufc_py"
    $results += Invoke-Bench "UltraFastCopy (python)" (Join-Path $dst (Split-Path $Src -Leaf)) $expect.Count $expect.Bytes {
        & $python -m fast_transfer.cli.app copy $Src $dst --verify none --conflict overwrite --no-resume --quiet | Out-Null
    }

    $dst = Join-Path $DstRoot "ufc_none"
    $results += Invoke-Bench "UltraFastCopy (exe)" (Join-Path $dst (Split-Path $Src -Leaf)) $expect.Count $expect.Bytes {
        & $ufc copy $Src $dst --verify none --conflict overwrite --no-resume --quiet | Out-Null
    }

    $dst = Join-Path $DstRoot "ufc_hash"
    $results += Invoke-Bench "UltraFastCopy (exe) +xxhash" (Join-Path $dst (Split-Path $Src -Leaf)) $expect.Count $expect.Bytes {
        & $ufc copy $Src $dst --verify xxhash --conflict overwrite --no-resume --quiet | Out-Null
    }

    $results | Format-Table -AutoSize | Out-Host

    $baseline = $results | Where-Object { $_.Tool -eq "Explorer (shell)" } | Select-Object -First 1
    if (-not $baseline) { $baseline = $results | Where-Object { $_.Tool -eq "shutil.copytree" } | Select-Object -First 1 }
    if ($baseline -and $baseline.Seconds -gt 0) {
        Write-Host ("relative to {0} ({1}s):" -f $baseline.Tool, $baseline.Seconds) -ForegroundColor DarkGray
        foreach ($r in $results) {
            $ratio = $baseline.Seconds / [math]::Max($r.Seconds, 0.01)
            $color = if ($ratio -ge 1) { "Green" } else { "Yellow" }
            Write-Host ("  {0,-24} {1,6:N2}x" -f $r.Tool, $ratio) -ForegroundColor $color
        }
    }
    Remove-Item -Recurse -Force $DstRoot -ErrorAction SilentlyContinue
}

# -- run -------------------------------------------------------------------

Write-Host "Ultra Fast Copy benchmark" -ForegroundColor Cyan
Write-Host "root: $Root" -ForegroundColor DarkGray
$defender = try { (Get-MpComputerStatus -ErrorAction Stop).RealTimeProtectionEnabled } catch { $null }
if ($defender) {
    Write-Host "note: Defender real-time scanning is ON; it taxes the unsigned .exe rows." -ForegroundColor DarkGray
}

$smallSrc = Join-Path $Root "small_src"
$largeSrc = Join-Path $Root "large_src"

if (-not (Test-Path $smallSrc)) {
    Write-Host "generating $SmallCount small files..." -ForegroundColor DarkGray
    New-SmallFiles $smallSrc $SmallCount
}
if (-not (Test-Path $largeSrc)) {
    Write-Host "generating $LargeCount x ${LargeSizeMB}MB files..." -ForegroundColor DarkGray
    New-LargeFiles $largeSrc $LargeCount $LargeSizeMB
}

Invoke-Scenario "Scenario A: many small files" $smallSrc (Join-Path $Root "out_a")
Invoke-Scenario "Scenario C: few large files" $largeSrc (Join-Path $Root "out_c")

if (-not $KeepData) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }

Write-Host "`nNote: the source is warm in the page cache, which helps every tool equally." -ForegroundColor DarkGray
Write-Host "Absolute numbers describe this disk; compare the ratios." -ForegroundColor DarkGray
