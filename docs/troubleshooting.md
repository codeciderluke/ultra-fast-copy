# Troubleshooting

Logs: `%LOCALAPPDATA%\UltraFastCopy\logs\fast-transfer-YYYYMMDD.log`
Checkpoints: `%LOCALAPPDATA%\UltraFastCopy\checkpoints\<job_id>.db`
Config: `%APPDATA%\UltraFastCopy\config.toml`

## Files failed with "access denied"

Usually the file is open in another program, or an antivirus scanner is holding
it. This error is treated as transient and retried with backoff.

```powershell
fast-transfer copy SRC DST --retry 5
```

If it persists, the file may need higher privileges. The app deliberately does
not run as administrator by default; run the terminal as administrator for that
specific job instead.

## "The file is locked by another process"

Find the holder with Resource Monitor (`resmon` → CPU → Associated Handles) or
`handle.exe`. Locked files stay in the failure list rather than silently
disappearing. Copying live databases or open Outlook files will not work —
Volume Shadow Copy would be needed, which is out of scope for this version.

## Paths longer than 260 characters

Should just work: every filesystem call goes through the `\\?\` prefix. If some
*other* tool then cannot read the result, enable the OS policy:

```powershell
# Administrator, then reboot
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -Type DWord
```

## The job was interrupted

Nothing is lost. Completed files stay, incomplete ones were written under
`.fasttransfer.partial` and removed.

```powershell
fast-transfer jobs
fast-transfer resume <JOB_ID>
```

Resume re-verifies each recorded file against the destination's size and
re-transfers anything uncertain.

If `.fasttransfer.partial` files survive a hard power loss, they are safe to
delete — they are, by definition, incomplete.

## The transfer is slower than Explorer

Try fewer workers first, especially on a single spinning disk:

```powershell
fast-transfer copy SRC DST --workers 2 --verify none
```

Then check what the auto policy sees:

```powershell
fast-transfer benchmark SRC DST
```

Verification costs a full extra read; `--verify none` is the fastest and the
least safe. See `benchmark.md`.

## Move did not delete the source

By design. A cross-volume move deletes the original only after its copy is
verified. A file left behind means its verification failed — check the failure
list and the log. This is the intended outcome, not a bug.

## The context menu entry is missing

Windows 11 hides registry-based verbs behind **Show more options** (or
Shift+F10). That is a platform behaviour, not a registration failure.

```powershell
fast-transfer shell status     # is it registered, and where does it point?
fast-transfer shell install    # (re)register for the current user
fast-transfer shell uninstall
```

If `status` reports a command pointing at a moved or deleted install, re-run
`shell install`. Explorer caches verbs; restart it if needed:

```powershell
Stop-Process -Name explorer -Force   # Explorer restarts itself
```

## The GUI will not start

```powershell
fast-transfer gui   # run via the CLI to see the traceback
```

A missing `PyQt6` means the environment is not installed: `uv pip install -e ".[dev]"`.

## Config changes have no effect

CLI arguments take precedence over `config.toml`. Verify what is actually in
effect:

```powershell
fast-transfer config show
```

A malformed config falls back to defaults and prints the parse error rather
than refusing to run.

## Reporting a problem

Include `fast-transfer config show`, the failing command, and the log tail.
Logs contain full paths; anonymise them first if that matters:

```python
from pathlib import Path
from fast_transfer.utils.logging import anonymize_log_file, default_log_file
anonymize_log_file(default_log_file(), Path("safe-to-share.log"))
```
