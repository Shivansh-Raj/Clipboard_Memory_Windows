"""Content classification (port of V1's ContentClassifier.swift)."""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlsplit

from .models import ContentKind

_URL = re.compile(r"^(https?|ftp|file|mailto):[^\s]+$|^www\.[^\s]+\.[a-z]{2,}[^\s]*$", re.I)
_EMAIL = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)

_CODE_PATTERNS = [
    r"(?m)^\s*(def|class|func|fn|function|import|from|package|using|public|private|protected|struct|enum|interface|impl|const|let|var|return|if|for|while|elif|else|switch|case|try|catch|async|await)\b.*[\{\(:;]\s*$",
    r"(?mi)^\s*(select\s.+\sfrom|insert\s+into|update\s+\w+\s+set|delete\s+from|create\s+table|alter\s+table|with\s+\w+\s+as)\b",
    r"=>|->|::|\+\+|--\s|&&|\|\||!==?|===?|\bnull\b|\bnil\b|\bNone\b",
    r"#include\s*<|^\s*#!/|\$\(|</?[a-z][a-z0-9]*[^>]*>",
    r"(?m)^\s*(//|#|/\*|\*)\s",
    r"\w+\([^)]*\)\s*[\{:]?",
    r"\b\w+\s*=\s*[^=\s].*;\s*$",
]
_CODE_RE = [re.compile(p) for p in _CODE_PATTERNS]

_MD_PATTERNS = [
    r"(?m)^#{1,6}\s+\S", r"(?m)^\s*[-*+]\s+\S", r"(?m)^\s*\d+\.\s+\S",
    r"\[[^\]]+\]\([^)]+\)", r"\*\*[^*\n]+\*\*", r"(?m)^```", r"(?m)^>\s+\S", r"`[^`\n]+`",
]
_MD_RE = [re.compile(p) for p in _MD_PATTERNS]
_MD_HEADING = re.compile(r"(?m)^#{1,6}\s+\S")


def classify(text: str) -> ContentKind:
    t = text.strip()
    if not t:
        return ContentKind.TEXT
    single_line = "\n" not in t
    if single_line and len(t) < 2048:
        if _URL.search(t):
            return ContentKind.LINK
        if _EMAIL.search(t):
            return ContentKind.EMAIL
    if is_json(t):
        return ContentKind.JSON
    if is_table(t):
        return ContentKind.CSV
    code, md = code_score(t), markdown_score(t)
    if md >= 3 and md > code:
        return ContentKind.MARKDOWN
    if code >= 3:
        return ContentKind.CODE
    if md >= 2:
        return ContentKind.MARKDOWN
    return ContentKind.TEXT


def is_json(t: str) -> bool:
    if not t or t[0] not in "{[" or t[-1] not in "}]":
        return False
    if len(t.encode("utf-8", "ignore")) >= 20_000_000:
        return True
    try:
        json.loads(t)
        return True
    except ValueError:
        return False


def is_table(t: str) -> bool:
    lines = [l for l in t.split("\n") if l][:50]
    if len(lines) < 2:
        return False
    for d in ("\t", ",", ";", "|"):
        counts = [l.count(d) for l in lines]
        first = counts[0]
        if first >= 1 and all(c == first for c in counts):
            # Commas alone also fit ordinary two-line sentences; demand short-ish cells.
            if d in (",", ";"):
                if any(len(l) > 400 for l in lines):
                    return False
                if first < 2 and len(lines) < 3:
                    return False
            return True
    return False


def code_score(t: str) -> int:
    lines = [l for l in t.split("\n") if l]
    if not lines:
        return 0
    score = 0
    sample = lines[:200]
    indented = sum(1 for l in sample if l.startswith("    ") or l.startswith("\t"))
    endings = sum(1 for l in sample if l[-1] in "{};)")
    if indented * 4 >= len(sample) and len(sample) > 1:
        score += 2
    if endings * 3 >= len(sample):
        score += 2
    for i, rx in enumerate(_CODE_RE):
        if rx.search(t):
            score += 3 if i <= 1 else 1
    symbols = sum(1 for c in t if c in "{}[]();=<>_$")
    if symbols / max(len(t), 1) > 0.04:
        score += 1
    return score


def markdown_score(t: str) -> int:
    score = sum(1 for rx in _MD_RE if rx.search(t))
    if _MD_HEADING.search(t):
        score += 1
    return score


def domain(text: str) -> Optional[str]:
    t = text.strip()
    candidate = "https://" + t if t.lower().startswith("www.") else t
    try:
        return urlsplit(candidate).hostname
    except ValueError:
        return None
