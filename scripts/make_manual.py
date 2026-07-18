"""Render the English user manual to docs/UltraFastCopy-Manual.pdf.

Uses PySide6 (already a project dependency) to turn a styled HTML document into
a real PDF -- no extra tooling. Run: python scripts/make_manual.py
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "UltraFastCopy-Manual.pdf"

HTML = """
<html><head><style>
  body { font-family: 'Segoe UI', Arial, sans-serif; color: #1a1a1a; font-size: 10.5pt; }
  h1 { font-size: 22pt; color: #0b3d66; margin: 0 0 2pt 0; }
  .sub { color: #556; font-size: 11pt; margin: 0 0 14pt 0; }
  h2 { font-size: 14pt; color: #0b3d66; border-bottom: 1px solid #ccd; padding-bottom: 3pt; margin-top: 18pt; }
  h3 { font-size: 11.5pt; color: #234; margin-top: 12pt; }
  code, pre { font-family: 'Consolas', 'Courier New', monospace; font-size: 9.5pt; }
  pre { background: #f4f6f8; border: 1px solid #e0e4e8; padding: 8pt; }
  table { border-collapse: collapse; width: 100%; margin: 6pt 0; }
  th, td { border: 1px solid #cdd3da; padding: 4pt 7pt; text-align: left; font-size: 9.5pt; }
  th { background: #eef2f6; }
  .muted { color: #667; font-size: 9pt; }
  ul { margin: 4pt 0; }
</style></head><body>

<h1>Ultra Fast Copy</h1>
<p class="sub">User Manual &mdash; high-performance file copy/move for Windows (CLI + GUI)</p>

<p>Ultra Fast Copy targets the cases where Windows Explorer bogs down: hundreds
of thousands of files, network drives, and interrupted jobs. It adapts its
parallelism to file size, storage device, and network conditions, and adds
verification, retry, and resume on top of a shared core engine used by both the
command-line tool (<code>ufCopy</code>) and the graphical app
(<code>ufCopyTool</code>).</p>

<h2>1. Installation</h2>
<p><b>Easiest:</b> run <code>UltraFastCopy-Setup.exe</code>. It installs both
executables for the current user (no administrator rights needed) and adds the
<b>"Open with Ultra Fast Copy"</b> entry to the Explorer right-click menu. To
remove everything, run <code>UltraFastCopy-Setup.exe /uninstall</code>.</p>
<p>Otherwise, download the standalone executables from the release page, or build
from source:</p>
<pre>uv venv --python 3.12
uv pip install -e ".[dev]"
# or, without uv:
py -3.12 -m venv .venv
.venv\\Scripts\\activate
pip install -e ".[dev]"</pre>
<p>Two executables are provided:</p>
<table>
<tr><th>File</th><th>What it is</th><th>Qt bundled</th></tr>
<tr><td><code>ufCopy.exe</code></td><td>Command-line interface</td><td>No (permissive-only)</td></tr>
<tr><td><code>ufCopyTool.exe</code></td><td>Graphical interface</td><td>PySide6 (LGPL-3.0)</td></tr>
</table>

<h2>2. Command-line usage</h2>
<pre>ufCopy copy "D:\\Source" "E:\\Backup" --workers 12 --verify xxhash --conflict overwrite-if-newer --prescan
ufCopy move "D:\\Source" "E:\\Archive" --verify xxhash --retry 5
ufCopy jobs
ufCopy resume &lt;JOB_ID&gt;
ufCopy benchmark "D:\\Source" "E:\\Temp"
ufCopy config show
ufCopy config init
ufCopy gui</pre>

<h3>Key options</h3>
<table>
<tr><th>Option</th><th>Description</th></tr>
<tr><td><code>--workers N</code></td><td>Worker thread count. Automatic if omitted.</td></tr>
<tr><td><code>--buffer-size 8MiB</code></td><td>Copy buffer size.</td></tr>
<tr><td><code>--verify MODE</code></td><td>none | size | mtime_size | xxhash | sha256</td></tr>
<tr><td><code>--conflict POLICY</code></td><td>What to do when the destination exists.</td></tr>
<tr><td><code>--retry N</code></td><td>Retries for transient errors (exponential backoff).</td></tr>
<tr><td><code>--prescan / --streaming</code></td><td>Count everything first / start while scanning.</td></tr>
<tr><td><code>--include / --exclude</code></td><td>glob patterns (repeatable).</td></tr>
<tr><td><code>--dry-run</code></td><td>Report the plan without writing.</td></tr>
<tr><td><code>--json-output</code></td><td>Emit JSON events to stdout for automation.</td></tr>
<tr><td><code>--bandwidth-limit 10MiB</code></td><td>Cap transfer rate per second.</td></tr>
<tr><td><code>--preset fast|balanced|safe</code></td><td>Speed preset.</td></tr>
</table>

<h3>Exit codes</h3>
<table>
<tr><th>Code</th><th>Meaning</th></tr>
<tr><td>0</td><td>Success</td></tr>
<tr><td>1</td><td>Completed, but some files failed</td></tr>
<tr><td>2</td><td>Job failed</td></tr>
<tr><td>3</td><td>Cancelled by the user</td></tr>
<tr><td>4</td><td>Invalid usage</td></tr>
</table>

<h2>3. Graphical app</h2>
<pre>ufCopyTool
ufCopyTool --source "D:\\Source" --destination "E:\\Backup"</pre>

<p><img src="gui-main" width="640"></p>
<p class="muted">The main window: source tree (left), transfer controls (center),
destination tree (right), progress panel, and Log / Failed files / Queue tabs.</p>

<h3>Running a transfer</h3>
<ol>
<li>Pick the source folder in the <b>left</b> pane and the target in the
<b>right</b> pane (use the drive selector, the path box, or the "..." button).</li>
<li>Choose <b>Copy</b> (default) or <b>Move</b> with the toggle at the top. Move
asks for confirmation before it deletes anything.</li>
<li>Select one or more items in the source pane (Ctrl/Shift click for several).</li>
<li>Start the transfer either by pressing <b>Start copy</b>, by clicking the
center <b>Copy &rarr;</b> button, or by <b>dragging</b> the selection from the
left pane onto the right pane.</li>
<li>Watch progress, speed, average speed, ETA, and the failed count in the panel.
<b>Pause</b>, <b>Resume</b>, or <b>Cancel</b> at any time.</li>
</ol>

<h3>Other controls</h3>
<ul>
<li><b>Swap</b> exchanges the source and destination panes.</li>
<li><b>Options</b> opens the detailed settings (verification mode, conflict
policy, worker count, buffer size, speed preset, filters).</li>
<li>The <b>Log</b> tab shows messages, <b>Failed files</b> lists per-file errors,
and <b>Queue</b> shows pending jobs.</li>
<li>Dark theme, code-drawn icon. Date and size formatting follow the Windows locale.</li>
</ul>

<h2>4. Explorer right-click menu</h2>
<pre>ufCopy shell install     # register for the current user (no admin needed)
ufCopy shell status      # show registration state
ufCopy shell uninstall   # remove</pre>
<p>Adds an <b>"Open with Ultra Fast Copy"</b> entry for files, folders, folder
backgrounds, and drives. On Windows 11 registry-based entries appear under
<b>"Show more options"</b> (Shift+F10).</p>

<h2>5. Configuration</h2>
<p>Settings live in <code>%APPDATA%\\UltraFastCopy\\config.toml</code>; CLI
arguments take precedence. Logs are written to
<code>%LOCALAPPDATA%\\UltraFastCopy\\logs\\</code> and resume checkpoints to
<code>%LOCALAPPDATA%\\UltraFastCopy\\checkpoints\\</code>.</p>

<h2>6. Performance</h2>
<p>Ultra Fast Copy does not promise to beat Explorer or Robocopy everywhere. Its
value is low UI overhead on many-file jobs, environment-aware concurrency, and
data integrity through verification, retry, resume, and detailed logs. Measure
your own environment with <code>ufCopy benchmark</code>. A detailed study of how
to beat Robocopy on NTFS (directory-lock scatter, block-level imaging) ships in
<code>docs/benchmark.md</code>.</p>

<h2>7. License</h2>
<p>Ultra Fast Copy is released under the MIT License. The GUI executable bundles
PySide6 (LGPL-3.0); the CLI executable contains only permissive-licensed
components. See <code>LICENSE</code> and <code>THIRD_PARTY_LICENSES.md</code>.</p>

<p class="muted">Designed by Codecider Lab &middot; Ultra Fast Copy 0.1.0</p>

</body></html>
"""


def main() -> None:
    app = QApplication.instance() or QApplication([])
    OUT.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(OUT))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16, 16, 16, 16))
    writer.setResolution(150)

    doc = QTextDocument()
    shot = ROOT / "docs" / "img" / "gui-main.png"
    if shot.exists():
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QImage

        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("gui-main"), QImage(str(shot)))
    doc.setHtml(HTML)
    doc.setPageSize(writer.pageLayout().paintRectPixels(writer.resolution()).size().toSizeF())
    doc.print_(writer)

    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    if app is not None:
        app.quit()


if __name__ == "__main__":
    main()
