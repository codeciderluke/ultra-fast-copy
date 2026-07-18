# Ultra Fast Copy

A high-performance file copy/move tool for Windows. It ships a CLI and a
PySide6 GUI on top of one shared core transfer engine.

It is built for the cases where Explorer bogs down — hundreds of thousands of
files, network drives, interrupted jobs. Rather than just copying in parallel,
it adapts to file size, storage device, and network conditions, and adds
verification, retry, and resume.

## Features

- **Environment-aware parallelism** — worker count is chosen automatically from
  the source/destination device type (HDD, SSD, network) and the average file size
- **Safe move** — same-volume is a rename; cross-volume is copy → verify → delete,
  and the original is kept if verification fails
- **Resume** — SQLite checkpoint. After a hard stop, already-finished files are not
  copied again
- **Verification** — `none` / `size` / `mtime_size` / `xxhash` / `sha256`
- **Conflict policies** — `skip` / `overwrite` / `overwrite_if_newer` /
  `overwrite_if_different` / `rename` / `ask`
- **Partial-file isolation** — written as `.fasttransfer.partial` and atomically
  renamed to the final name only after verification succeeds
- **Windows-aware** — long paths (`\\?\`), UNC, reserved device names,
  case-insensitivity, Unicode paths
- **Pause / resume / cancel** — every operation is interruptible
- **Per-file failure isolation** — one file failing does not stop the whole job

## How it compares

Measured on 20,000 files / 637 MB on one NVMe volume (warm cache). Against the
tool most people actually copy with — **Windows Explorer** — Ultra Fast Copy is
several times faster while adding verification, resume, and retry that Explorer
does not have at all.

| Tool | 20,000 files (637 MB) | Files/s | vs Explorer |
|---|---:|---:|---:|
| Windows Explorer (shell) | 30.0 s | 667 | 1.0x |
| **Ultra Fast Copy** | **8.2 s** | **2,445** | **3.7x faster** |

Feature-for-feature, it does things the common tools do not:

| Capability | Explorer | Robocopy | shutil | **Ultra Fast Copy** |
|---|:---:|:---:|:---:|:---:|
| Graphical app | ✅ | ❌ | ❌ | ✅ |
| Resume an interrupted job | ❌ | partial | ❌ | ✅ |
| Per-file verify (xxhash / sha256) | ❌ | ❌ | ❌ | ✅ |
| Auto worker tuning by device | ❌ | manual | ❌ | ✅ |
| Pause / resume / cancel | limited | ❌ | ❌ | ✅ |
| Per-file failure isolation | ❌ | ✅ | ❌ | ✅ |
| Explorer right-click integration | — | ❌ | ❌ | ✅ |
| Faster than Explorer | 1.0x | ✅ | ✅ | ✅ (3.7x) |

On raw small-file throughput, Robocopy's native engine is still the one to beat.
This project ships the two techniques that actually *do* beat it on NTFS —
directory-lock scatter (~1.4x) and block-level imaging (~9.6x on a whole
volume) — with full measurements and honest limits in
[`docs/benchmark.md`](docs/benchmark.md).

## Installation

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Without `uv`:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## CLI usage

```powershell
# Copy
ufCopy copy "D:\Source" "E:\Backup" --workers 12 --verify xxhash --conflict overwrite-if-newer --prescan

# Move (cross-volume: copy -> verify -> delete original)
ufCopy move "D:\Source" "E:\Archive" --verify xxhash --retry 5

# Resume an interrupted job
ufCopy jobs
ufCopy resume <JOB_ID>

# Measure throughput at several worker counts
ufCopy benchmark "D:\Source" "E:\Temp"

# Configuration
ufCopy config show
ufCopy config init

# Launch the GUI
ufCopy gui
```

### Key options

| Option | Description |
|---|---|
| `--workers N` | Worker thread count. Automatic if omitted |
| `--buffer-size 8MiB` | Copy buffer size |
| `--verify MODE` | `none` \| `size` \| `mtime_size` \| `xxhash` \| `sha256` |
| `--conflict POLICY` | What to do when the destination exists |
| `--retry N` | Retries for transient errors (exponential backoff) |
| `--prescan` / `--streaming` | Count everything first / start while scanning |
| `--include` / `--exclude` | glob patterns (repeatable) |
| `--dry-run` | Report the plan without writing |
| `--json-output` | Emit JSON events to stdout for automation |
| `--bandwidth-limit 10MiB` | Cap transfer rate per second |
| `--preset fast\|balanced\|safe` | Speed preset |

### Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Completed, but some files failed |
| 2 | Job failed |
| 3 | Cancelled by the user |
| 4 | Invalid usage |

## GUI

```powershell
ufCopyTool
ufCopyTool --source "D:\Source" --destination "E:\Backup"
```

- **Dual pane** — source tree on the left, destination tree on the right
- **Drag and drop** — drag left to right to start a transfer
- **Multi-select** — Ctrl/Shift click to pick several files and folders at once
- **Copy (default) / Move** segmented control. Move shows a confirmation dialog
- Overall progress, current file, speed, average speed, ETA, failure count
- Log / failed-files / job-list tabs
- Speed presets (Fast / Balanced / Safe) and a detailed options panel
- Dark theme, code-drawn icon (no separate image files)

UI strings are English. Date and size formatting follow the Windows locale.

## Explorer right-click menu integration

```powershell
ufCopy shell install     # register for the current user (no admin needed)
ufCopy shell status      # show registration state and target path
ufCopy shell uninstall   # remove
```

An **"Open with Ultra Fast Copy"** entry is added for files, folders, folder
backgrounds, and drives; clicking it opens the GUI with the clicked item
preselected in the source pane.

> On Windows 11, registry-based entries appear under **"Show more options"**
> (Shift+F10). That is how Windows works; putting an entry in the top-level menu
> requires a package-signed `IExplorerCommand` extension.

Registration locations (HKCU, no admin required):

```
HKCU\Software\Classes\*\shell\UltraFastCopy
HKCU\Software\Classes\Directory\shell\UltraFastCopy
HKCU\Software\Classes\Directory\Background\shell\UltraFastCopy
HKCU\Software\Classes\Drive\shell\UltraFastCopy
```

## Configuration file

`%APPDATA%\UltraFastCopy\config.toml` — CLI arguments take precedence over the
config file.

```toml
[transfer]
workers = 0          # 0 = automatic
buffer_size = "4MiB"
verify = "size"
conflict = "skip"
retry_count = 3
prescan = true
checkpoint = true

[ui]
theme = "dark"
language = "en"

[logging]
level = "INFO"
retention_days = 30
```

Logs: `%LOCALAPPDATA%\UltraFastCopy\logs\ufcopy-YYYYMMDD.log`
Checkpoints: `%LOCALAPPDATA%\UltraFastCopy\checkpoints\<job_id>.db`

## Tests

```powershell
pytest                        # unit + integration
ruff check src tests
mypy
```

## Build

```powershell
.\scripts\build.ps1            # CLI + GUI
.\scripts\build.ps1 -Cli       # CLI only
.\scripts\build.ps1 -Gui -Clean
```

This produces `dist\ufCopy.exe` (CLI, ~14 MB), `dist\ufCopyTool.exe` (GUI,
~40 MB), and `dist\SHA256SUMS.txt`. The icon is rendered from code by
`scripts\make_icon.py` at build time.

## Layout

```
src/fast_transfer/
├─ core/      transfer engine (no Qt dependency)
│  ├─ engine.py      scan -> queue -> thread-pool orchestration
│  ├─ scanner.py     recursive scanner over os.scandir
│  ├─ planner.py     path mapping, pattern filters, pre-validation
│  ├─ copier.py      streaming copy, partial files, bandwidth limit
│  ├─ mover.py       rename / copy-verify-delete
│  ├─ verifier.py    size, mtime, xxhash, sha256
│  ├─ conflict.py    conflict policies
│  ├─ checkpoint.py  SQLite resume
│  ├─ control.py     thread-safe cancel/pause
│  ├─ events.py      progress events and aggregation
│  └─ errors.py      error classification
├─ cli/       Typer + Rich
├─ gui/       PySide6 (dark theme)
├─ config/    TOML settings
└─ utils/     paths, formatting, system info, logging
```

**Design principle**: the core has no Qt dependency, progress is delivered
outward as events, and file I/O never runs on the GUI main thread.

## About performance

The Python implementation does not promise to beat Explorer or Robocopy in every
environment. Its goals are low UI overhead on many-file jobs, environment-aware
concurrency control, and data integrity and recovery through verification,
retry, resume, and detailed logs.

Compare it in your own environment with `ufCopy benchmark`.

How to beat Robocopy on NTFS (~25% by avoiding directory-lock contention, an
order of magnitude by block-level imaging), with measurements and limits, is
written up in [`docs/benchmark.md`](docs/benchmark.md). The reproducible native
experiments live in `experiments/` (build with
`scripts/build_experiments.ps1`); the comparison harness is
`scripts/block_benchmark.ps1`.

## License

MIT — see [LICENSE](LICENSE). Third-party components and their licenses are
listed in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
