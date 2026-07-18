# Architecture

## Layering

```
gui/  ──┐
        ├──> core/  ──> utils/
cli/  ──┘
```

The core engine never imports PyQt, Typer, or Rich. Front-ends subscribe to
core events and render them however they like. This is what lets the CLI and
GUI share one transfer implementation with zero duplicated logic.

## Data flow

```
sources ──> Scanner ──> Planner ──> bounded queue ──> worker pool ──> Copier/Mover
               │            │             │                │              │
               │            │             │                │              └─> Verifier
               │            │             │                └─> ProgressAggregator ──> EventEmitter
               │            │             └─ back-pressure (workers × 8)
               │            └─ path mapping, pattern filters, validation
               └─ os.scandir, cancellable, symlink-loop safe
```

One producer thread walks the tree and pre-creates destination directories.
A fixed pool of workers copies files. Nothing scales with file count except
the queue, which is bounded — a 1,000,000-file job uses the same memory as a
100-file job in streaming mode.

## Threading

| Component | Thread |
|---|---|
| Scanner / planner | one producer thread |
| Copy / move | fixed `ThreadPoolExecutor` |
| Progress aggregation | whichever worker triggers it, guarded by a lock |
| Logging | background `QueueListener` |
| GUI | main thread only; never touches the filesystem |

`TransferControl` is the single cancel/pause object shared by every thread.
Workers call `control.checkpoint()` between chunks and between files, so both
pause and cancel take effect within one buffer read rather than at file
boundaries.

## Key invariants

1. **A move never deletes an unverified source.** Cross-volume moves copy,
   verify, then delete. If verification fails, the source stays and the file is
   reported as failed. `VerifyMode.NONE` is silently upgraded to `SIZE` for
   cross-volume moves.
2. **An incomplete file never wears a real filename.** Copies write to
   `<name>.fasttransfer.partial` and are renamed onto the final name only after
   verification passes.
3. **One file's failure never fails the job.** Errors are classified, retried if
   transient, then recorded in the failure list. The pool moves on.
4. **Paths hit the filesystem through `extended_path()`.** Long paths and UNC
   work regardless of the machine's LongPathsEnabled setting. The `\\?\` prefix
   never enters the data model — `ScanEntry.path` is always a plain path,
   because a prefixed path silently corrupts every `relative_to()` downstream.

## Why these choices

**Bounded queue over a materialised list.** A pre-scan of a million files
would otherwise hold a million `TransferItem`s in memory before the first byte
moves. Pre-scan mode instead walks the tree twice — once to count, once to
copy — trading I/O for constant memory.

**Aggregated progress instead of per-file events.** Emitting an event per file
makes the UI the bottleneck on small-file jobs. The engine coalesces onto a
timer (default 200 ms); the GUI throttles again to ~8/s.

**Directories created by the producer.** Creating them in workers means every
worker racing on the same `mkdir` for every file in a folder. The producer
creates each destination folder once, tracked in a set.

**Same-volume moves are renames.** A metadata operation, independent of file
size. Volume identity comes from `st_dev` rather than the drive letter, because
mount points mean one letter can span volumes and two letters can share one.

**SQLite for checkpoints.** Batched inserts, atomic transactions, and fast
completion lookups on lists too large to hold in memory.

## Module map

| Module | Responsibility |
|---|---|
| `core/engine.py` | Orchestration: scan → queue → pool, retry, results |
| `core/scanner.py` | `os.scandir` walk, cancellable, loop-safe |
| `core/planner.py` | Destination mapping, pattern filters, pre-flight validation |
| `core/copier.py` | Streaming copy, partial files, rate limiting |
| `core/mover.py` | Rename vs copy-verify-delete |
| `core/verifier.py` | size / mtime / xxhash / sha256, sampled verification |
| `core/conflict.py` | Conflict policies |
| `core/checkpoint.py` | SQLite resume store |
| `core/control.py` | Thread-safe cancel and pause |
| `core/events.py` | Event types and progress aggregation |
| `core/errors.py` | OSError → `ErrorCode` + user message |
| `core/winapi.py` | File attributes via ctypes, degrading safely |
| `utils/paths.py` | Long paths, UNC, reserved names, volume identity |
| `utils/system.py` | Device probing and the auto worker policy |
| `gui/worker.py` | QThread adapter: core events → Qt signals |
| `integration/context_menu.py` | Explorer right-click verb (HKCU) |

## Extending

**A new verification mode**: add to `VerifyMode`, handle it in
`verifier.verify()`, add a label in `settings_dialog.VERIFY_LABELS`.

**A new conflict policy**: add to `ConflictPolicy`, handle it in
`ConflictResolver._apply()`, add a label.

**Multiple concurrent jobs**: `JobQueueModel` already carries the state a
scheduler needs; today the GUI runs one job at a time. The change is a
scheduler in `MainWindow`, not a change to the engine.
