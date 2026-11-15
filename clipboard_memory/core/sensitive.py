"""Best-effort detection of secrets (port of V1's SensitiveDetector.swift).

This is heuristic. It will miss some secrets and flag some harmless text.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

_PATTERNS = [
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("AWS access key", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    ("API key", r"\bsk-[A-Za-z0-9_\-]{20,}\b|\b[sr]k_(live|test)_[A-Za-z0-9]{16,}\b"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("JSON web token", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    ("bearer token", r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"),
    ("credential assignment",
     r"""(?i)\b(password|passwd|pwd|secret|api[_\-]?key|access[_\-]?token|auth[_\-]?token|client[_\-]?secret)\b["']?\s*[:=]\s*["']?[^\s"']{6,}"""),
]
_COMPILED = [(n, re.compile(p)) for n, p in _PATTERNS]


def detect(text: str) -> Optional[str]:
    """Return a short human-readable reason when the text looks like a secret."""
    if not text or len(text.encode("utf-8", "ignore")) >= 200_000:
        return None
    for name, rx in _COMPILED:
        if rx.search(text):
            return name
    t = text.strip()
    if is_card_number(t):
        return "card number"
    if looks_like_secret_token(t):
        return "high-entropy token"
    return None


def is_card_number(t: str) -> bool:
    digits = [c for c in t if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    if not all(c.isdigit() or c in " -" for c in t):
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def looks_like_secret_token(t: str) -> bool:
    if not 24 <= len(t) <= 200 or any(c.isspace() or c in "/.:" for c in t):
        return False
    if not (any(c.isupper() for c in t) and any(c.islower() for c in t) and any(c.isdigit() for c in t)):
        return False
    n = len(t)
    entropy = -sum((c / n) * math.log2(c / n) for c in Counter(t).values())
    return entropy > 4.0
