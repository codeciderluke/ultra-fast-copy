# Third-Party Licenses

Ultra Fast Copy is distributed under the MIT License (see [LICENSE](LICENSE)).
It depends on the third-party components below. Their licenses are all
compatible with distributing both the source and the built executables.

## Runtime dependencies

| Component | License | Bundled in |
|---|---|---|
| PySide6 (Qt for Python) | LGPL-3.0 | GUI (`ufCopyTool.exe`) |
| shiboken6 | LGPL-3.0 | GUI (`ufCopyTool.exe`) |
| Typer | MIT | CLI + GUI |
| Click | BSD-3-Clause | CLI + GUI |
| Rich | MIT | CLI + GUI |
| markdown-it-py | MIT | CLI + GUI |
| mdurl | MIT | CLI + GUI |
| Pygments | BSD-2-Clause | CLI + GUI |
| psutil | BSD-3-Clause | CLI + GUI |
| xxhash | BSD-2-Clause | CLI + GUI |
| Send2Trash | BSD-3-Clause | CLI + GUI |
| pywin32 | PSF (Python Software Foundation) | CLI + GUI |
| shellingham | ISC | CLI + GUI |
| typing_extensions | PSF | CLI + GUI |

The **CLI** executable (`ufCopy.exe`) excludes Qt entirely, so it contains only
permissive-licensed (MIT / BSD / PSF / ISC) components.

## Qt / PySide6 (LGPL-3.0) notice — GUI only

The GUI executable (`ufCopyTool.exe`) bundles PySide6, which is the official
Qt for Python binding, licensed under the **GNU Lesser General Public License
v3 (LGPL-3.0)**. Ultra Fast Copy uses PySide6 as an unmodified library through
its public API; it does not modify Qt or PySide6.

To comply with the LGPL for the distributed GUI binary:

- The full text of the LGPL-3.0 (and the GPL-3.0 it references) is available at
  <https://www.gnu.org/licenses/lgpl-3.0.html> and
  <https://www.gnu.org/licenses/gpl-3.0.html>.
- The Qt and PySide6 source is available from
  <https://download.qt.io/official_releases/QtForPython/> and
  <https://code.qt.io/>.
- Because `ufCopyTool.exe` is a PyInstaller bundle, the Qt/PySide6 shared
  libraries it ships can be extracted and replaced with a compatible build, as
  the LGPL requires.

If you prefer a fully permissive distribution with no LGPL obligations, use the
CLI executable (`ufCopy.exe`), which does not include Qt.

## Note

License identifiers above were read from the installed package metadata at build
time. Always consult each project's own license file for the authoritative text.
