"""SQLite-backed clipboard history (port of V1's HistoryStore.swift, same schema plus ocr_status)."""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .models import (OCR_NONE, OCR_PENDING, ClipEntry, ClipPayload, ContentKind, HistoryFilter, NewClip)

log = logging.getLogger("clipboard_memory.store")

PREVIEW_LIMIT = 4000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT,
    rtf BLOB,
    html BLOB,
    image_file TEXT,
    image_w INTEGER NOT NULL DEFAULT 0,
    image_h INTEGER NOT NULL DEFAULT 0,
    byte_size INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_copied_at REAL NOT NULL,
    copy_count INTEGER NOT NULL DEFAULT 1,
    source_app TEXT,
    source_bundle TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',
    ocr TEXT,
    ocr_status TEXT NOT NULL DEFAULT ''
)
"""

_COLUMNS = (f"id, kind, substr(text, 1, {PREVIEW_LIMIT}), length(text), image_file, image_w, image_h, byte_size, "
            "created_at, last_copied_at, copy_count, source_app, source_bundle, pinned, favorite, tags, "
            f"substr(ocr, 1, {PREVIEW_LIMIT}), (rtf IS NOT NULL OR html IS NOT NULL), ocr_status")


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_to_entry(r: tuple) -> ClipEntry:
    try:
        kind = ContentKind(r[1])
    except ValueError:
        kind = ContentKind.TEXT
    return ClipEntry(
        id=r[0], kind=kind, text=r[2] or "", text_length=r[3] or 0, image_file=r[4], image_width=r[5],
        image_height=r[6], byte_size=r[7], created_at=r[8], last_copied_at=r[9], copy_count=r[10],
        source_app=r[11], source_id=r[12], pinned=bool(r[13]), favorite=bool(r[14]),
        tags=[t for t in (r[15] or "").split(",") if t], ocr_text=r[16], has_rich_text=bool(r[17]),
        ocr_status=r[18] or OCR_NONE)


class HistoryStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.images_dir = self.directory / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        db_path = self.directory / "history.sqlite"
        try:
            self._open(db_path)
        except sqlite3.DatabaseError:
            # Never destroy a database we can't read: move it aside and start fresh.
            self._close()
            stamp = int(time.time())
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(db_path) + suffix)
                if src.exists():
                    try:
                        src.rename(Path(str(db_path) + f".corrupt-{stamp}" + suffix))
                    except OSError:
                        pass
            self._open(db_path)

    # ---------------------------------------------------------------- plumbing

    def _open(self, path: Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=3.0)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.execute(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(entries)")}
        if "ocr_status" not in cols:
            self._db.execute("ALTER TABLE entries ADD COLUMN ocr_status TEXT NOT NULL DEFAULT ''")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_entries_recent ON entries(last_copied_at)")
        self._db.execute("SELECT count(*) FROM entries LIMIT 1").fetchall()   # integrity probe

    def _close(self) -> None:
        db = getattr(self, "_db", None)
        if db is not None:
            try:
                db.close()
            except sqlite3.Error:
                pass
            self._db = None

    def close(self) -> None:
        with self._lock:
            self._close()

    def _run(self, sql: str, params: tuple | list = ()) -> int:
        with self._lock:
            return self._db.execute(sql, params).rowcount

    def _query(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- writing

    def save(self, clip: NewClip, now: Optional[float] = None) -> tuple[int, bool]:
        """Stores a clip, or bumps the existing entry when identical content was already saved."""
        now = time.time() if now is None else now
        with self._lock:
            if clip.kind == ContentKind.IMAGE and clip.image_png is not None:
                digest = "img:" + sha256_hex(clip.image_png)
                byte_size = len(clip.image_png)
            else:
                t = clip.text or ""
                data = t.encode("utf-8", "surrogatepass")
                digest = "txt:" + sha256_hex(data)
                byte_size = len(data)

            existing = self._query("SELECT id FROM entries WHERE hash = ?", (digest,))
            if existing:
                eid = existing[0][0]
                self._run("""UPDATE entries SET last_copied_at = ?, copy_count = copy_count + 1,
                             source_app = COALESCE(?, source_app), source_bundle = COALESCE(?, source_bundle),
                             rtf = COALESCE(rtf, ?), html = COALESCE(html, ?) WHERE id = ?""",
                          (now, clip.source_app, clip.source_id, clip.rtf, clip.html, eid))
                return eid, False

            image_file = None
            status = OCR_NONE
            if clip.kind == ContentKind.IMAGE and clip.image_png is not None:
                image_file = digest[4:] + ".png"
                tmp = self.images_dir / (image_file + ".tmp")
                tmp.write_bytes(clip.image_png)
                os.replace(tmp, self.images_dir / image_file)
                status = OCR_PENDING if clip.ocr_pending else OCR_NONE

            cur = self._db.execute(
                """INSERT INTO entries(kind, text, rtf, html, image_file, image_w, image_h, byte_size, hash,
                                       created_at, last_copied_at, source_app, source_bundle, ocr_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (clip.kind.value, clip.text, clip.rtf, clip.html, image_file, clip.image_width, clip.image_height,
                 byte_size, digest, now, now, clip.source_app, clip.source_id, status))
            return cur.lastrowid, True

    def touch(self, entry_id: int, now: Optional[float] = None) -> None:
        self._run("UPDATE entries SET last_copied_at = ?, copy_count = copy_count + 1 WHERE id = ?",
                  (time.time() if now is None else now, entry_id))

    def set_pinned(self, entry_id: int, value: bool) -> None:
        self._run("UPDATE entries SET pinned = ? WHERE id = ?", (int(value), entry_id))

    def set_favorite(self, entry_id: int, value: bool) -> None:
        self._run("UPDATE entries SET favorite = ? WHERE id = ?", (int(value), entry_id))

    def set_ocr(self, entry_id: int, text: str, status: str = "done") -> None:
        self._run("UPDATE entries SET ocr = ?, ocr_status = ? WHERE id = ?", (text, status, entry_id))

    def set_ocr_status(self, entry_id: int, status: str) -> None:
        self._run("UPDATE entries SET ocr_status = ? WHERE id = ?", (status, entry_id))

    def pending_ocr_ids(self) -> list[int]:
        return [r[0] for r in self._query(
            "SELECT id FROM entries WHERE kind = 'image' AND ocr_status = ? ORDER BY last_copied_at DESC", (OCR_PENDING,))]

    def failed_ocr_ids(self) -> list[int]:
        return [r[0] for r in self._query(
            "SELECT id FROM entries WHERE kind = 'image' AND ocr_status = 'failed' ORDER BY last_copied_at DESC")]

    def set_tags(self, entry_id: int, tags: list[str]) -> None:
        clean = [t.strip().replace(",", " ") for t in tags]
        clean = [t for t in clean if t]
        self._run("UPDATE entries SET tags = ? WHERE id = ?", (",".join(clean), entry_id))

    def delete(self, ids: list[int]) -> None:
        with self._lock:
            for eid in ids:
                files = [r[0] for r in self._query("SELECT image_file FROM entries WHERE id = ?", (eid,)) if r[0]]
                self._run("DELETE FROM entries WHERE id = ?", (eid,))
                for f in files:
                    (self.images_dir / f).unlink(missing_ok=True)

    def clear(self, keep_pinned: bool) -> None:
        """Removes history. Pinned entries survive when keep_pinned is true."""
        with self._lock:
            where = " AND pinned = 0" if keep_pinned else ""
            files = [r[0] for r in self._query(
                "SELECT image_file FROM entries WHERE image_file IS NOT NULL" + where)]
            self._run("DELETE FROM entries" + (" WHERE pinned = 0" if keep_pinned else ""))
            for f in files:
                (self.images_dir / f).unlink(missing_ok=True)
            try:
                self._db.execute("VACUUM")
            except sqlite3.Error:
                pass

    def purge(self, older_than_days: int, max_entries: int, now: Optional[float] = None) -> int:
        """Applies retention. Pinned entries are never removed. Returns the number of removed entries."""
        now = time.time() if now is None else now
        ids: set[int] = set()
        with self._lock:
            if older_than_days > 0:
                cutoff = now - older_than_days * 86400
                ids.update(r[0] for r in self._query(
                    "SELECT id FROM entries WHERE pinned = 0 AND last_copied_at < ?", (cutoff,)))
            if max_entries > 0:
                ids.update(r[0] for r in self._query(
                    "SELECT id FROM entries WHERE pinned = 0 ORDER BY last_copied_at DESC LIMIT -1 OFFSET ?",
                    (max_entries,)))
            self.delete(list(ids))
        return len(ids)

    # ---------------------------------------------------------------- reading

    def _where(self, search: str, flt: HistoryFilter, now: float) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if flt.sql:
            clauses.append(flt.sql)
            if flt == HistoryFilter.RECENT:
                params.append(now - 86400)
        for term in search.split():
            like = "%" + _escape_like(term) + "%"
            clauses.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'"
                                              for c in ("text", "ocr", "source_app", "tags", "kind")) + ")")
            params.extend([like] * 5)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    def fetch(self, search: str = "", flt: HistoryFilter = HistoryFilter.ALL, limit: int = 100, offset: int = 0,
              now: Optional[float] = None) -> list[ClipEntry]:
        where, params = self._where(search, flt, time.time() if now is None else now)
        sql = (f"SELECT {_COLUMNS} FROM entries {where} "
               "ORDER BY pinned DESC, last_copied_at DESC, id DESC LIMIT ? OFFSET ?")
        try:
            return [_row_to_entry(r) for r in self._query(sql, params + [limit, offset])]
        except sqlite3.Error:
            log.exception("fetch failed")
            return []

    def count(self, search: str = "", flt: HistoryFilter = HistoryFilter.ALL, now: Optional[float] = None) -> int:
        where, params = self._where(search, flt, time.time() if now is None else now)
        try:
            return self._query(f"SELECT count(*) FROM entries {where}", params)[0][0]
        except sqlite3.Error:
            return 0

    def entry(self, entry_id: int) -> Optional[ClipEntry]:
        rows = self._query(f"SELECT {_COLUMNS} FROM entries WHERE id = ?", (entry_id,))
        return _row_to_entry(rows[0]) if rows else None

    def payload(self, entry_id: int) -> Optional[ClipPayload]:
        rows = self._query("SELECT kind, text, rtf, html, image_file FROM entries WHERE id = ?", (entry_id,))
        if not rows:
            return None
        kind_s, text, rtf, html, image = rows[0]
        try:
            kind = ContentKind(kind_s)
        except ValueError:
            kind = ContentKind.TEXT
        return ClipPayload(kind=kind, text=text, rtf=rtf, html=html,
                           image_path=str(self.images_dir / image) if image else None,
                           file_paths=(text or "").split("\n") if kind == ContentKind.FILE else [])

    def full_text(self, entry_id: int) -> Optional[str]:
        rows = self._query("SELECT text FROM entries WHERE id = ?", (entry_id,))
        return rows[0][0] if rows else None

    def total_count(self) -> int:
        return self.count()

    def storage_bytes(self) -> int:
        total = 0
        for p in [self.directory / "history.sqlite", self.directory / "history.sqlite-wal"]:
            if p.exists():
                total += p.stat().st_size
        for p in self.images_dir.glob("*"):
            total += p.stat().st_size
        return total
