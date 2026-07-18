# Beating Robocopy on NTFS

Robocopy's multithreaded native engine is the bar for fast copying on Windows.
This project ships two techniques that measurably beat it on NTFS. Both are
reproduced by the native experiments in `experiments/` and the harness in
`scripts/`. Numbers below were measured on one machine (Windows 11, NVMe,
warm cache); reproduce them in your own environment.

## 1. Directory-lock scatter — ~1.4x faster (file-by-file)

Copying files in enumeration order makes many threads create files into the
*same* destination folder at once, and an NTFS directory is a single B-tree
behind a single lock, so they serialize. Round-robin the work across
directories instead — so at any instant the threads are inserting into
*different* folders — and the contention disappears.

Measured on 20,000 files / 637 MB across 40 directories, median of 3 shuffled
passes:

| Tool | Time (median) | vs Robocopy |
|---|---:|---:|
| **Ultra Fast Copy — directory scatter** | **3.15 s** | **1.41x faster** |
| Robocopy /MT:16 | 4.45 s | 1.0x |

Same `CopyFileW`, same 16 threads — only the *order* changes. The win runs
1.3–1.4x across repeated measurements. It applies to trees with multiple
directories to spread across.

Reproduce: `experiments/native_copy2.cpp` (build with
`scripts/build_experiments.ps1`), then run it against a source tree.

## 2. Block-level imaging — ~9.6x faster (whole volume)

For a whole-volume copy, files never need to be opened at all. NTFS reports
which clusters are allocated (`FSCTL_GET_VOLUME_BITMAP`); streaming only those
clusters sequentially drops the per-file metadata cost — MFT record, directory
index, journal, and the antivirus filter — to zero.

Measured on a 4 GB NTFS volume holding the same 20,000-file / 637 MB tree:

| Tool | Granularity | Time | vs Robocopy |
|---|---|---:|---:|
| **Ultra Fast Copy — block imaging (read)** | volume | **0.19 s** | **17.7x faster** |
| **Ultra Fast Copy — block imaging (→ image)** | volume | **0.35 s** | **9.6x faster** |
| Robocopy /MT:16 | file tree | 3.37 s | 1.0x |

Block imaging copies *more* bytes than Robocopy (the file data plus NTFS
metadata) and still wins by an order of magnitude, because bytes were never the
bottleneck — per-file metadata was. This is the whole-volume backup/migration
case; it produces a raw volume image.

Reproduce: `experiments/ntfs_block_copy.cpp` and `scripts/block_benchmark.ps1`
(elevated — raw-volume access and VSS need administrator rights).

## Running the comparison

```powershell
# Build the native experiments
.\scripts\build_experiments.ps1

# Directory-scatter vs Robocopy on a file tree, and block imaging on a volume
.\scripts\block_benchmark.ps1        # elevated

# Or measure the shipped tool against Robocopy in your own environment
ufCopy benchmark "D:\Source" "E:\Temp"
```

Report ratios rather than absolute numbers — absolute speed says more about the
disk than the software. Drop caches between runs (reboot, or use a fresh source
tree) so a warm file cache does not flatter the second run.
