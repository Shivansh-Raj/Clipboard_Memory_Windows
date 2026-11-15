"""Data model shared by the store, the capture pipeline and the UI (mirrors V1's Models.swift)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ContentKind(str, Enum):
    TEXT = "text"
    CODE = "code"
    LINK = "link"
    EMAIL = "email"
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"
    RICH_TEXT = "richText"
    IMAGE = "image"
    FILE = "file"

    @property
    def label(self) -> str:
        return _KIND_INFO[self][0]

    @property
    def icon(self) -> str:
        return _KIND_INFO[self][1]

    @property
    def tint(self) -> str:
        return _KIND_INFO[self][2]


_KIND_INFO = {
    ContentKind.TEXT: ("Text", "text", "#8E8E93"),
    ContentKind.CODE: ("Code", "code", "#AF52DE"),
    ContentKind.LINK: ("Link", "link", "#0A84FF"),
    ContentKind.EMAIL: ("Email", "mail", "#30B0C7"),
    ContentKind.JSON: ("JSON", "braces", "#FF9F0A"),
    ContentKind.CSV: ("Table", "table", "#30D158"),
    ContentKind.MARKDOWN: ("Markdown", "hash", "#5E5CE6"),
    ContentKind.RICH_TEXT: ("Rich Text", "type", "#FF2D96"),
    ContentKind.IMAGE: ("Image", "image", "#FF453A"),
    ContentKind.FILE: ("Files", "file", "#A2845E"),
}

MONOSPACE_KINDS = {ContentKind.CODE, ContentKind.JSON, ContentKind.CSV, ContentKind.MARKDOWN,
                   ContentKind.LINK, ContentKind.FILE}


class HistoryFilter(str, Enum):
    ALL = "all"
    TEXT = "text"
    CODE = "code"
    LINKS = "links"
    IMAGES = "images"
    PINNED = "pinned"
    FAVORITES = "favorites"
    RECENT = "recent"

    @property
    def title(self) -> str:
        return {"all": "All", "text": "Text", "code": "Code", "links": "Links", "images": "Images",
                "pinned": "Pinned", "favorites": "Favourites", "recent": "Recent"}[self.value]

    @property
    def sql(self) -> Optional[str]:
        return {
            "all": None,
            "text": "kind IN ('text','csv','markdown','richText','file')",
            "code": "kind IN ('code','json')",
            "links": "kind IN ('link','email')",
            "images": "kind = 'image'",
            "pinned": "pinned = 1",
            "favorites": "favorite = 1",
            "recent": "last_copied_at > ?",
        }[self.value]


# OCR states stored in entries.ocr_status
OCR_NONE = ""          # not applicable / not requested
OCR_PENDING = "pending"
OCR_DONE = "done"
OCR_FAILED = "failed"


@dataclass
class ClipEntry:
    id: int
    kind: ContentKind
    text: str                     # a prefix of the content (see HistoryStore.PREVIEW_LIMIT)
    text_length: int
    image_file: Optional[str]
    image_width: int
    image_height: int
    byte_size: int
    created_at: float
    last_copied_at: float
    copy_count: int
    source_app: Optional[str]
    source_id: Optional[str]
    pinned: bool
    favorite: bool
    tags: list[str] = field(default_factory=list)
    ocr_text: Optional[str] = None
    has_rich_text: bool = False
    ocr_status: str = OCR_NONE

    @property
    def is_truncated(self) -> bool:
        return self.text_length > len(self.text)


@dataclass
class ClipPayload:
    """What gets written back to the clipboard."""
    kind: ContentKind
    text: Optional[str] = None
    rtf: Optional[bytes] = None
    html: Optional[bytes] = None
    image_path: Optional[str] = None
    file_paths: list[str] = field(default_factory=list)


@dataclass
class NewClip:
    kind: ContentKind
    text: Optional[str] = None
    rtf: Optional[bytes] = None
    html: Optional[bytes] = None
    image_png: Optional[bytes] = None
    image_width: int = 0
    image_height: int = 0
    source_app: Optional[str] = None
    source_id: Optional[str] = None
    ocr_pending: bool = False     # mark a newly stored image as awaiting OCR
