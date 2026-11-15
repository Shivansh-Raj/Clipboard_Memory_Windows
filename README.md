# Clipboard Memory V2 (Windows)

A searchable history of everything you copy, one global shortcut away. This is the Windows counterpart of the macOS
Clipboard Memory V1 (kept in a separate repository).

* Copy as usual (`Ctrl+C`). Press **`Ctrl+Shift+V`** from any app to open the history, search, and press **Enter** to put an
  entry back on the clipboard.
* Text, code, links, emails, JSON, tables, Markdown, rich text, images and copied files; pin, favourite, tag, transform
  (format/minify JSON, format SQL, case, whitespace) and extract text from images (offline OCR with bundled Tesseract).
* Local only: no network code, no telemetry, clipboard contents are never logged, configurable retention, sensitive-content
  detection (best effort), per-app exclusion, pause from the tray.

## Install (no administrator rights)

1. Download `ClipboardMemory-<version>-win64.zip` from the **Actions** run artifacts (or Releases) and unzip it anywhere.
2. Run `ClipboardMemory.exe --install` from that folder (e.g. `ClipboardMemory.exe --install` in Command Prompt), or just run
   `ClipboardMemory.exe` for a portable, no-install use.
   `--install` copies the app to `%LOCALAPPDATA%\Programs\Clipboard Memory`, adds a Start Menu entry and starts it with Windows.
3. Uninstall with `ClipboardMemory.exe --uninstall` (history in `%APPDATA%\Clipboard Memory` is kept until you delete it).

Settings, the database and images live in `%APPDATA%\Clipboard Memory`.

### Keyboard

| Key | Action |
|---|---|
| `Ctrl+Shift+V` (customisable) | Open clipboard history from anywhere |
| `Esc` | Close |
| `Ctrl+F` | Focus search |
| `↑` `↓` | Navigate |
| `Enter` | Copy selected entry |
| `Ctrl+Enter` | Copy and close |
| `Alt+Enter` | Copy as plain text |
| `Ctrl+P` / `Ctrl+D` | Pin / favourite |
| `Del` | Delete (when the search box is empty, or `Ctrl+Del`) |
| `Tab` / `Shift+Tab`, `Ctrl+1..8` | Next / previous filter, pick a filter |

### Windows notes and limits

* **`Ctrl+Shift+V` is also "paste as plain text"** in Chrome, Edge, VS Code, Slack and Windows Terminal. While Clipboard Memory
  runs it opens history instead; change it in Settings (for example `Ctrl+Alt+V`). If another program already owns a
  shortcut, the app says so and keeps your previous one.
* **Unsigned executable.** SmartScreen, antivirus, AppLocker or WDAC on managed PCs can block an unsigned `.exe` (and the
  bundled `tesseract.exe`). This app does not try to bypass them; if blocked, ask IT to allow it. OCR failing never affects
  the rest of the app.
* Elevated (run-as-administrator) windows can't be pasted into from a non-elevated app, as for any clipboard tool.
* Sensitive-content detection is heuristic. It misses some secrets and flags some harmless text. Copies marked "don't record"
  by password managers and Windows are always ignored.
* Pasting restores the entry to the clipboard; you press `Ctrl+V` yourself. Line endings are stored as `\n` and written back as
  `\r\n`.

## Develop

```
python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m clipboard_memory            # run from source
```

Layout: `clipboard_memory/core` (classifier, sensitive detector, transforms, SQLite store: a port of V1's logic, with V1's
tests ported to `tests/test_core.py`), `ocr/` (engine interface, Tesseract, background queue), `platform/` (Windows `ctypes`
backend and a dev backend for non-Windows machines), `ui/` (PySide6).

### Build the Windows package

```
python packaging/fetch_tesseract.py     # downloads the pinned Tesseract build, verifies SHA-256 (needs 7-Zip)
python packaging/build.py               # PyInstaller -> dist\ClipboardMemory and a zip
dist\ClipboardMemory\ClipboardMemory.exe --selftest-ocr ocr.json       # OCR from the packaged app
dist\ClipboardMemory\ClipboardMemory.exe --selftest-windows win.json   # real clipboard + hotkey layer
```

`.github/workflows/build.yml` runs all of that on a Windows runner for every push.

Third-party licences: see `THIRD_PARTY_NOTICES.md` (and `tesseract/licenses/` in the package).
