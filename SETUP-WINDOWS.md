# Clipboard Memory V2: Windows setup

Three ways to get it running, from easiest to most hands-on. None needs administrator rights.

| Path | You need | Best for |
|---|---|---|
| **A. Use the prebuilt zip** | Nothing installed | Normal use |
| **B. Build the zip yourself** | Python 3.12, 7-Zip, internet (once) | When you can't get the CI artifact |
| **C. Run from source** | Python 3.12 | Development, or if `.exe` files are blocked |

---

## A. Use the prebuilt zip (recommended)

1. On GitHub open the repo's **Actions** tab, pick the latest green **build-windows** run, and download the
   **ClipboardMemory-win64** artifact. Unzip the download to get `ClipboardMemory-2.0.0-win64.zip`.
2. Unzip that into a folder you control, for example `C:\Users\<you>\Apps\ClipboardMemory`.
3. Choose one:
   - **Portable:** double-click `ClipboardMemory.exe`.
   - **Install for your user:** open Command Prompt in that folder and run

     ```
     ClipboardMemory.exe --install
     ```

     This copies the app to `%LOCALAPPDATA%\Programs\Clipboard Memory`, adds a Start Menu entry, turns on start-with-Windows,
     and launches it.
4. Look for the clipboard icon in the notification area (click `^` if it is hidden). A welcome message appears on first run.
5. Copy something with `Ctrl+C`, then press **`Ctrl+Shift+V`** anywhere.

No separate Tesseract install, PATH change, or internet connection is needed. Everything, including OCR, ships in the folder.

To remove it: `ClipboardMemory.exe --uninstall` (your history in `%APPDATA%\Clipboard Memory` is kept; delete that folder to
remove it too).

---

## B. Build the zip yourself

You need Windows, **Python 3.12** (python.org "Windows installer", tick *Install for me only*, no admin) and **7-Zip**
(only to unpack the Tesseract package; the portable 7-Zip is fine if `7z.exe` is on PATH or in `C:\Program Files\7-Zip`).

In Command Prompt or PowerShell, from the repo folder:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python packaging\fetch_tesseract.py
.venv\Scripts\python packaging\build.py
```

Result: `dist\ClipboardMemory\ClipboardMemory.exe` and `dist\ClipboardMemory-2.0.0-win64.zip`.

`fetch_tesseract.py` downloads the pinned Tesseract build and refuses to continue if its SHA-256 doesn't match. This is the only
step that needs internet.

**Check the build** (results are written to a file because the app is windowed):

```
dist\ClipboardMemory\ClipboardMemory.exe --selftest-ocr ocr.json
dist\ClipboardMemory\ClipboardMemory.exe --selftest-windows win.json
type ocr.json
type win.json
```

Both should end with `"passed": true`. If one fails, the JSON shows which step.

---

## C. Run from source

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m clipboard_memory
```

OCR works from source only if a Tesseract is available: run `packaging\fetch_tesseract.py` once (stages `vendor\tesseract`), or
set the environment variable `CLIPBOARD_MEMORY_TESSERACT` to a `tesseract.exe` path. Without it the app runs normally and OCR
shows "engine unavailable".

---

## First-run checklist

1. Tray icon visible.
2. Copy some text, press `Ctrl+Shift+V`: it appears in the list.
3. Press `Enter`, then paste elsewhere with `Ctrl+V`.
4. Take a screenshot with `Win+Shift+S`, open history, wait a moment for "reading text…" to finish, then search for a word
   that was in the screenshot.
5. Settings (tray icon, right-click, **Settings…**): shortcut, start with Windows, retention, ignored apps, OCR.

## Changing the shortcut

`Ctrl+Shift+V` is also "paste as plain text" in Chrome, Edge, VS Code, Slack and Windows Terminal. While Clipboard Memory runs it
opens history instead. If that bothers you, open **Settings → General**, click the shortcut, and press a new one such as
`Ctrl+Alt+V`. If Windows or another program already owns a combination, the app tells you and keeps the old one.

## Where things are stored

| What | Where |
|---|---|
| History database and images | `%APPDATA%\Clipboard Memory` |
| Settings | `%APPDATA%\Clipboard Memory\settings.json` |
| Log (events and errors only, never clipboard content) | `%APPDATA%\Clipboard Memory\clipboard-memory.log` |
| Installed app (with `--install`) | `%LOCALAPPDATA%\Programs\Clipboard Memory` |
| Start-with-Windows entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `ClipboardMemory` |

Nothing is sent over the network, and no telemetry is collected.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Windows or antivirus blocks `ClipboardMemory.exe` | The exe is unsigned. Ask IT to allow it, or use path **C**. The app does not try to get around this. |
| "Couldn't register the shortcut" | Another program owns it. Pick another in Settings. You can still open history from the tray icon. |
| Hotkey does nothing in an admin window | Windows blocks input between normal and elevated programs. Run neither elevated, or use the tray icon. |
| Image shows "Text extraction failed" | Click **Retry OCR**. If it keeps failing, security software may be blocking `tesseract\tesseract.exe`; Settings → Images shows the reason. Everything else keeps working. |
| Nothing is being saved | Check the tray menu for **Resume Monitoring**; check the app isn't in Settings → Privacy → Ignored apps; password-manager copies are ignored by design. |
| A copied password or key wasn't saved | Sensitive-content detection is on by default (best effort). Turn it off in Settings → Privacy if you don't want it. |
| Start with Windows won't turn on | Company policy may block per-user startup entries. |
| Need a clean slate | Quit from the tray, then delete `%APPDATA%\Clipboard Memory`. |

## Limits worth knowing

- It restores items to the clipboard; you press `Ctrl+V` yourself.
- Sensitive-content detection is heuristic. It misses some secrets and flags some harmless text.
- Text is saved with `\n` line endings and restored with `\r\n`.
- Windows' own history (`Win+V`) keeps running separately and doesn't conflict.
