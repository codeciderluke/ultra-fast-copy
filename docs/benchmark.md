# Benchmarking

Python cannot beat Robocopy on raw throughput, and this project does not claim
to. Measure your own environment before choosing a tool.

## Measured results

One machine, Windows 11, same-volume NVMe, source warm in the page cache.
`.\scripts\benchmark.ps1 -SmallCount 20000 -LargeCount 6 -LargeSizeMB 200`

**Scenario A — 20,000 files, 637 MB**

| Tool | Time | files/s | vs Explorer |
|---|---:|---:|---:|
| Explorer (shell) | 30.0 s | 667 | 1.0x |
| Robocopy /MT:16 | 3.6 s | 5,583 | **8.4x** |
| shutil.copytree | 7.1 s | 2,827 | 4.2x |
| Ultra Fast Copy (python) | 8.2 s | 2,445 | 3.7x |
| Ultra Fast Copy (exe) | 12.8 s | 1,564 | 2.4x |
| Ultra Fast Copy (exe) + xxhash | 23.8 s | 841 | 1.3x |

**Scenario C — 6 files, 1.2 GB**

| Tool | Time | MB/s | vs Explorer |
|---|---:|---:|---:|
| Explorer (shell) | 0.54 s | 2,237 | 1.0x |
| Robocopy /MT:16 | 0.17 s | 7,160 | 3.2x |
| shutil.copytree | 0.34 s | 3,545 | 1.6x |
| Ultra Fast Copy (python) | 0.39 s | 3,106 | 1.4x |
| Ultra Fast Copy (exe) | 0.81 s | 1,473 | 0.7x |

### Reading these numbers honestly

**Robocopy wins, and it is not close.** Native C, unbuffered I/O, 16 threads.
If raw speed on healthy local disks is all you need, use Robocopy. This tool
exists for the other things — resume, verification, per-file retry, a UI that
does not stall — not to beat it.

**The .exe is ~70% slower than the same code under `python.exe`** (12.1 s vs
7.1 s on scenario A, both at `--workers 8`). The code is identical, so this is
packaging, not the engine. What was measured:

| Packaging | Scenario A |
|---|---:|
| `python.exe -m fast_transfer.cli.app` | 7.1 s |
| PyInstaller **onedir** (unsigned) | 12.3 s |
| PyInstaller **onefile** (unsigned) | 12.1 s |

onedir and onefile are the same, which rules out onefile's ~0.6 s temp
extraction as the cause (that startup cost is real, but it is a constant and
only matters on tiny jobs — it is most of scenario C's gap).

The likeliest remaining cause is Defender's real-time scanning treating a
freshly built, packed, unsigned binary's file writes more aggressively than
those of a known interpreter. That is **not proven**: the exclusion list needs
administrator rights to read, and an unsigned native C++ binary (see
`experiments/native_copy.cpp`) came within 15% of signed Robocopy — so a plain
"unsigned exe" tax alone does not account for 70%. Treat the number as measured
but the explanation as open. Code-signing the release is the first thing to try.

The `(python)` row is the like-for-like comparison against `shutil`.

**Against `shutil.copytree` (8.2 s vs 7.1 s)** we are 15% slower — while also
writing through a partial file, verifying, honouring cancel/pause, and keeping
a resume checkpoint. With those off, the same engine finishes in 5.1 s, ~1.4x
faster than `shutil`. The 15% is what the safety guarantees cost.

**Verification costs a full extra read** (8.2 s → 23.8 s with xxhash on 20k
files, which also re-reads both sides). On a copy, `size` is usually enough. On
a cross-volume move, where the original is deleted, the extra read buys the
guarantee that the copy is intact.

## Would a C++ rewrite beat Robocopy?

Measured, not assumed. `experiments/native_copy.cpp` is a deliberately minimal
native copier — `FindFirstFileExW` enumeration, pre-created directories, then
`CopyFileW` from a thread pool, with no verification, retry, progress, or
partial files. It is the ceiling a C++ rewrite would be chasing.

| Threads | native C++ | Robocopy |
|---:|---:|---:|
| 8 | 3.98 s (5,029 f/s) | 3.56 s (5,616 f/s) |
| 16 | 4.08 s (4,906 f/s) | 3.55 s (5,632 f/s) |
| 32 | 7.14 s (2,803 f/s) | 3.43 s (5,837 f/s) |
| 64 | 7.42 s (2,695 f/s) | 3.22 s (6,202 f/s) |
| 128 | 7.42 s (2,696 f/s) | 3.11 s (6,435 f/s) |

**The naive C++ version loses to Robocopy**, and collapses past 16 threads.
Robocopy itself plateaus around 6,400 files/s: 16x more threads buys 15%.

That plateau is the point. 20,000 files / 637 MB at ~6,000 files/s is 178 MB/s
on a disk that streams 7,159 MB/s on large files. The small-file scenario is not
bandwidth-bound at all — it is bound by NTFS metadata (MFT record, directory
index insert, journal) and the antivirus filter driver, per file, at roughly
160 µs each. Every user-mode API — `CopyFileW`, `CreateFile`+`WriteFile`,
overlapped I/O, IOCP — pays that same cost, so rewriting the copy loop in a
faster language optimises the part that is not the bottleneck.

Enumeration, incidentally, took **0.01 s** for 20,000 files. Any effort spent
making scanning faster is wasted.

**What would actually beat it**, in rough order of payoff:

1. **ReFS block cloning** (`FSCTL_DUPLICATE_EXTENTS_TO_FILE`). A same-volume
   copy becomes a metadata operation — orders of magnitude faster, not
   percentages. Requires ReFS (e.g. a Windows 11 Dev Drive); impossible on NTFS,
   which has no clone primitive. This is the only order-of-magnitude win that
   keeps a browsable file tree. On NTFS, the order-of-magnitude win exists too,
   but only if you drop below the filesystem — see "Below the filesystem" below.
2. **Not copying at all** — same-volume moves are already renames, and resume
   already skips finished files. Both beat any copy loop by definition.
3. **Excluding the destination from real-time AV**, a deployment decision with
   real security cost, not a code change.
4. **Scattering concurrent writes across directories** — the one file-by-file
   trick that measurably beats Robocopy (~25%). See "Beating Robocopy" below.
5. A C++ port of this engine would reclaim the Python interpreter overhead
   (~356 µs/file → ~199 µs/file, about 1.8x) and land it around Robocopy
   parity — and, with the scatter trick above, a bit past it.

The conclusion the numbers support: **if raw small-file throughput on NTFS is
the goal, shell out to Robocopy** (see §24 of the spec) rather than rewrite the
engine — unless you are willing to give up restart/mirror/retry for the ~25% the
scatter trick buys. Ultra Fast Copy's value is resume, verification, per-file
retry and a UI that does not stall — none of which needs C++, and none of which
Robocopy does well.

## Beating Robocopy: scatter the writes

There is one file-by-file trick that does beat Robocopy on NTFS, and it is not
a faster copy loop. `experiments/native_copy2.cpp` isolates it.

Start from the naive copier (`native_copy.cpp`: `CopyFileW` from a thread pool,
files handed out in enumeration order). It loses to Robocopy. The same program
with the work reordered — one file from each directory in rotation, so the 16
threads are always writing into 16 *different* destination folders — wins.
Measured on the 20,000-file tree (40 directories), warm cache, median of 3
shuffled passes:

| Order | Time (median) | vs Robocopy |
|---|---:|---:|
| **scatter across directories** | **3.15 s** | **1.41x faster** |
| Robocopy /MT:16 | 4.45 s | 1.0x |
| enumeration order (`--noscatter`) | 5.53 s | 0.80x (loses) |

The scatter win runs 1.3–1.4x across repeated measurements; absolute times drift
with cache and machine state, the ordering does not. Same binary, same
`CopyFileW`, same 16 threads — only the *order* changes, and it moves 5.5 s to
3.2 s. The cause is not the copy, it is the destination
directory. An NTFS directory is a single B-tree behind a single lock. Files
enumerated in tree order are grouped by folder, so 16 threads creating them
concurrently pile into the *same* folder's index and serialize on its lock.
Robocopy copies directory-by-directory, so its `/MT` threads hit the same wall.
Round-robin the work across folders and the 16 inserts land in 16 different
B-trees — the lock contention vanishes.

Two things that looked like improvements and were not, measured on the same tree:

| Variant | Time | Verdict |
|---|---:|---|
| CopyFileW + scatter | 3.5 s | the win |
| Hand-rolled read/write loop (`--lean`) | 7.5 s | **1.8x slower** — CopyFileW's kernel path beats hand code |
| Largest-file-first sort | ≈ scatter | only helped by *accidentally* scattering directories |

The honest limits, so this is not oversold:

- **It drops Robocopy's engine.** No restartable mode, no mirror, no per-file
  retry, no change comparison — it just overwrites. The 25% buys raw throughput
  by doing less bookkeeping.
- **It needs directories to scatter across.** Measured on the same 20,000 files
  in *one* flat folder, scatter (5.16 s), `--noscatter` (5.21 s) and Robocopy
  (5.22 s) converge within 1% — all three serialize on that single directory's
  lock, and there is nothing to round-robin. Note the flat-folder time (~5.2 s)
  matches the *contended* time from the sub-directory tree (~5.5 s): the win was
  never speed at the file, it was spreading the lock. No directories, no win.
- **25% is still 25%.** It is a better use of the same threads against the same
  metadata floor — not the order of magnitude the next section gets by leaving
  the file abstraction entirely.

## The opposite extreme: a few large files

Everything above is the many-small-files regime, where per-file metadata is the
wall. Flip the shape — 6 files × 256 MB (1.5 GB), warm cache, median of 3 —
and the wall is gone; the only thing left is bandwidth, and every method piles
up against it:

| Tool | Time | MB/s |
|---|---:|---:|
| Robocopy /MT:16 | 0.22 s | 6,982 |
| native_copy2, 8 threads | 0.23 s | 6,678 |
| native_copy2 `--lean` | 0.27 s | 5,689 |
| native_copy2 scatter, 16 threads | 0.50 s | 3,072 |
| Robocopy /MT:8 **/J** (unbuffered) | 2.23 s | 689 |

Three things worth carrying away:

- **Scatter is pointless here.** Six files in one folder — no directory lock to
  spread, no metadata floor to beat. The trick from the last section buys
  nothing, and 16 threads on 6 files (scatter row) is actively *worse* than 8:
  when files < threads, over-subscription just adds contention. Match the thread
  count to the file count for big-file jobs.
- **`/J` (unbuffered I/O) was 10x *slower* here — and that is a cache artifact,
  not a verdict.** Unbuffered I/O bypasses the Windows cache; with the source
  warm in RAM, that means re-reading from disk instead of memory. `/J`'s real
  win is the *cold* case — moving data larger than RAM, where the buffered path
  would thrash the cache and double-buffer. So "use /J for large files" is true
  only when the data is not already cached; a warm benchmark inverts it. This is
  the one number in this document a cold-cache run would flip.
- **The engines converge.** At 6–7 GB/s the differences between Robocopy,
  CopyFileW, and a hand-rolled loop are within noise — the disk sets the pace,
  not the copier. This is the "few huge files on fast local disks" case the
  intro warns Ultra Fast Copy will not win, and neither does anything else.

## Below the filesystem: block-level imaging

Everything above copies files individually, so it pays the per-file NTFS
metadata cost no language can dodge. The one way to beat Robocopy on NTFS is to
stop copying files. `experiments/ntfs_block_copy.cpp` reads the allocated
clusters straight off the raw volume — it asks NTFS which clusters are in use
(`FSCTL_GET_VOLUME_BITMAP`) and streams only those, sequentially, opening not a
single file. Per-file cost drops to zero.

Measured against the file-by-file tools on identical content — a throwaway 4 GB
NTFS volume (an expandable VHD) holding the same 20,000-file / 637 MB tree, all
racing on one machine. `.\scripts\block_benchmark.ps1`, elevated.

| Tool | Granularity | Time | MB/s | files/s | Copied |
|---|---|---:|---:|---:|---:|
| **ntfs_block_copy** (read only) | volume | **0.19 s** | 3,937 | — | 748 MB |
| **ntfs_block_copy** (→ image) | volume | **0.35 s** | 2,137 | — | 748 MB |
| Robocopy /MT:16 | file tree | 3.37 s | 189 | 5,932 | 637 MB |
| native_copy (C++, 16t) | file tree | 3.87 s | 165 | 5,174 | 637 MB |
| shutil.copytree | file tree | 5.93 s | 107 | 3,370 | 637 MB |
| Ultra Fast Copy (exe) | file tree | 11.33 s | 56 | 1,765 | 637 MB |

**Block imaging finishes 9.6x faster than Robocopy** (17.7x if you only measure
the read). The tell is the last column: block imaging copies **more** bytes —
748 MB, the 637 MB of file data plus ~111 MB of NTFS metadata ($MFT, $LogFile,
indexes) — and still wins by an order of magnitude. Bytes were never the
bottleneck. Robocopy is capped at ~5,900 files/s by the per-file metadata rate;
block imaging never touches a file, so 20,000 of them cost nothing beyond the
sequential read of the clusters they occupy.

This is the same NTFS metadata floor from the section above, seen from the other
side: you cannot make per-file creation cheaper, so the win is to not create
files at all.

What the speed costs, and why this is not the default copier:

- **Volume-granular.** It images a whole volume, not `D:\project`. There is no
  "copy this subtree" — the bitmap describes the entire volume.
- **The output is a raw image, not a tree.** You get a `.img` you mount or
  restore, not a folder you can open. Different deliverable entirely.
- **Administrator required.** Opening `\\.\Volume` and taking a VSS snapshot
  both need elevation; unprivileged, it fails with access-denied by design.
- **Consistency is your choice.** The numbers above are `--live` (read the
  mounted volume; fine when nothing writes during the copy). The default path
  takes a VSS snapshot first for a coherent point in time — crash-consistent,
  since writer metadata is not gathered, so NTFS recovers via its journal as it
  would after a power loss. Snapshot creation adds a fixed ~1–3 s that dominates
  on a tiny volume and vanishes on a large one.

So the honest placement: for **whole-volume backup or migration on NTFS**,
block imaging is the order-of-magnitude win Robocopy cannot match, and
`ntfs_block_copy.cpp` is the reproducer. For **copying an arbitrary subtree** —
the job this tool actually does — files must materialize individually, the
metadata floor returns, and Robocopy parity is the ceiling.

Reproduce with `.\scripts\block_benchmark.ps1` (elevated): it builds the VHD,
fills it, races all six tools, and writes `experiments/block_bench_result.csv`.

## Running

```powershell
fast-transfer benchmark "D:\Source" "E:\Temp"
fast-transfer benchmark "D:\Source" "E:\Temp" --workers 1 --workers 8 --workers 24
```

The command copies the same tree at each worker count with verification off,
reports elapsed time, files/s, and throughput, then deletes its copies.

## Comparing against other tools

Drop caches between runs by rebooting or using a different source tree —
otherwise the second run reads from the Windows file cache and wins for free.

```powershell
# Explorer: copy the folder by hand and time it.

# Robocopy, 16 threads, quiet
Measure-Command { robocopy "D:\Source" "E:\Dest" /E /MT:16 /NFL /NDL /NJH /NJS }

# Naive Python
Measure-Command { python -c "import shutil; shutil.copytree(r'D:\Source', r'E:\Dest')" }

# This tool
Measure-Command { fast-transfer copy "D:\Source" "E:\Dest" --verify none --quiet }
```

Report the ratio against Explorer and `shutil.copytree`, not absolute numbers —
absolute speed says more about the disk than about the software.

## Scenarios worth measuring

| Scenario | Shape | What it stresses |
|---|---|---|
| A | 100,000 files, 1–64 KB | Metadata and per-file overhead |
| B | 10,000 files, 1–10 MB | Balanced |
| C | 20 files, 1 GB | Raw throughput |
| D | 100,000 files, 100 GB mixed | Realistic backup |
| E | SSD→SSD, SSD→HDD, SSD→network | Device and latency effects |

Generate test data:

```powershell
# Scenario A
$dir = "D:\bench\small"; New-Item -ItemType Directory $dir -Force | Out-Null
1..100000 | ForEach-Object {
    $bytes = New-Object byte[] (Get-Random -Minimum 1024 -Maximum 65536)
    [IO.File]::WriteAllBytes("$dir\f$_.bin", $bytes)
}
```

## Interpreting the results

**More workers is not always faster.** On one spinning disk, parallel reads
fight the head and throughput collapses; the auto policy uses 2 workers there.
On NVMe or a network share, concurrency hides latency and more workers help.
If `--workers 1` beats `--workers 16` on your hardware, that is a real result —
the disk is the bottleneck, not the code.

**Verification costs a full extra read.** `--verify xxhash` roughly doubles read
I/O. On a copy you can afford `size`; on a cross-volume move, where the original
is deleted, the extra read buys the guarantee that the copy is intact.

**Where this tool wins:** many-small-file jobs, where Explorer's per-file UI
work dominates; interrupted jobs, which resume instead of restarting; unreliable
sources, where per-file retries beat a whole-job failure.

**Where it will not win:** a handful of huge files on fast local disks. There,
Robocopy's tuned native I/O is hard to beat and the Python overhead shows.

## Metrics to record

Total time, files/s, MB/s, peak memory, CPU%, failures, retries, and whether the
UI stayed responsive. Memory should stay flat as file count grows — if it does
not, the bounded queue has a leak.
