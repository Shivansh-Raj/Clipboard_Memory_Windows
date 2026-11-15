"""Download the pinned Tesseract Windows build, verify its SHA-256 and stage it in vendor/tesseract/.

Runs at *build* time only (needs internet and 7-Zip, which GitHub's Windows runners include). The finished app
ships these files itself and never downloads or installs anything.

    python packaging/fetch_tesseract.py            # stage vendor/tesseract
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "tesseract"
CACHE = ROOT / "build" / "cache"

# UB Mannheim publishes the standard Windows builds of Tesseract (Apache-2.0).
VERSION = "5.4.0.20240606"
URL = f"https://github.com/UB-Mannheim/tesseract/releases/download/v{VERSION}/tesseract-ocr-w64-setup-{VERSION}.exe"
SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
LICENSE_URL = "https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.4.0/LICENSE"


def find_7z() -> str:
    for cand in (shutil.which("7z"), shutil.which("7zz"), r"C:\Program Files\7-Zip\7z.exe",
                 r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if cand and Path(cand).exists():
            return cand
    sys.exit("7-Zip is required to unpack the Tesseract installer at build time (not needed to run the app).")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def main() -> int:
    installer = CACHE / f"tesseract-setup-{VERSION}.exe"
    if not installer.exists() or sha256(installer) != SHA256:
        download(URL, installer)
    digest = sha256(installer)
    if digest != SHA256:
        installer.unlink(missing_ok=True)
        sys.exit(f"checksum mismatch for the Tesseract download:\n  expected {SHA256}\n  got      {digest}")
    print("checksum OK")

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([find_7z(), "x", "-y", f"-o{tmp}", str(installer)], check=True, stdout=subprocess.DEVNULL)
        root = Path(tmp)
        exe = next(root.rglob("tesseract.exe"), None)
        if exe is None:
            sys.exit("tesseract.exe not found in the installer package; layout changed? contents:\n" +
                     "\n".join(sorted(str(p.relative_to(root)) for p in root.iterdir())))
        src = exe.parent
        if VENDOR.exists():
            shutil.rmtree(VENDOR)
        VENDOR.mkdir(parents=True)
        shutil.copy2(exe, VENDOR / "tesseract.exe")
        for dll in src.glob("*.dll"):
            shutil.copy2(dll, VENDOR / dll.name)
        tessdata = src / "tessdata"
        if not (tessdata / "eng.traineddata").exists():
            sys.exit("tessdata/eng.traineddata not found in the installer package.")
        (VENDOR / "tessdata").mkdir()
        shutil.copy2(tessdata / "eng.traineddata", VENDOR / "tessdata" / "eng.traineddata")
        if (tessdata / "configs").is_dir():
            shutil.copytree(tessdata / "configs", VENDOR / "tessdata" / "configs")
        if (tessdata / "tessconfigs").is_dir():
            shutil.copytree(tessdata / "tessconfigs", VENDOR / "tessdata" / "tessconfigs")
        lic = VENDOR / "licenses"
        lic.mkdir()
        for p in root.rglob("*"):
            if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS")):
                shutil.copy2(p, lic / f"{p.parent.name}-{p.name}")
    try:
        download(LICENSE_URL, VENDOR / "licenses" / "tesseract-LICENSE-Apache-2.0.txt")
    except OSError as e:
        sys.exit(f"couldn't fetch the Tesseract licence text ({e}); the package must include it.")
    (VENDOR / "SOURCE.txt").write_text(
        f"Tesseract OCR {VERSION} (Windows build by UB Mannheim)\n{URL}\nSHA-256 of the installer: {SHA256}\n"
        "Licence: Apache-2.0 (see licenses/). Bundled DLLs carry their own licences, listed in THIRD_PARTY_NOTICES.\n",
        "utf-8")
    size = sum(f.stat().st_size for f in VENDOR.rglob("*") if f.is_file()) / 1e6
    print(f"staged {VENDOR} ({size:.0f} MB, {len(list(VENDOR.glob('*.dll')))} DLLs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
