"""Text transformations (port of V1's Transforms.swift). Each returns a *new* string."""
from __future__ import annotations

import json
import re
from typing import Optional


def _valid_json(t: str) -> bool:
    try:
        json.loads(t)
        return True
    except ValueError:
        return False


def format_json(text: str, indent: str = "  ") -> Optional[str]:
    """Order- and number-preserving pretty printer (works on the raw text, not a re-serialisation)."""
    t = text.strip()
    if not _valid_json(t):
        return None
    out: list[str] = []
    depth, in_string, escaped = 0, False, False
    i = 0
    n = len(t)

    def newline() -> None:
        out.append("\n" + indent * depth)

    while i < n:
        c = t[i]
        if in_string:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
                out.append(c)
            elif c in "{[":
                j = i + 1
                while j < n and t[j].isspace():
                    j += 1
                if j < n and ((c == "{" and t[j] == "}") or (c == "[" and t[j] == "]")):
                    out.append(c)
                    out.append(t[j])
                    i = j
                else:
                    out.append(c)
                    depth += 1
                    newline()
            elif c in "}]":
                depth -= 1
                newline()
                out.append(c)
            elif c == ",":
                out.append(c)
                newline()
            elif c == ":":
                out.append(": ")
            elif c in " \n\t\r":
                pass
            else:
                out.append(c)
        i += 1
    return "".join(out)


def minify_json(text: str) -> Optional[str]:
    t = text.strip()
    if not _valid_json(t):
        return None
    out: list[str] = []
    in_string, escaped = False, False
    for c in t:
        if in_string:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif not c.isspace():
            out.append(c)
    return "".join(out)


def uppercased(t: str) -> str:
    return t.upper()


def lowercased(t: str) -> str:
    return t.lower()


def clean_whitespace(text: str) -> str:
    """Trims line ends, collapses repeated inner spaces and runs of blank lines. Indentation is kept."""
    lines: list[str] = []
    blank = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip(" \t")
        if not line:
            if blank:
                continue
            blank = True
        else:
            blank = False
        stripped = line.lstrip(" \t")
        indent = line[: len(line) - len(stripped)]
        lines.append(indent + re.sub(r"[ \t]{2,}", " ", stripped))
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- SQL

_MAJOR = {"SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET", "UNION", "UNION ALL",
          "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM", "RETURNING", "WITH", "EXCEPT", "INTERSECT"}
_JOIN = {"JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER", "NATURAL"}
_KEYWORDS = _MAJOR | _JOIN | {"AND", "OR", "ON", "AS", "IN", "NOT", "NULL", "IS", "LIKE", "BETWEEN", "DISTINCT", "BY",
                              "ASC", "DESC", "CASE", "WHEN", "THEN", "ELSE", "END", "EXISTS", "ALL", "INTO", "USING",
                              "COUNT", "SUM", "AVG", "MIN", "MAX"}
_COMMENT_MARK = "\u0000"


def _tokenize_sql(text: str) -> list[str]:
    tokens: list[str] = []
    chars = text
    n = len(chars)
    i = 0
    while i < n:
        c = chars[i]
        if c.isspace():
            i += 1
            continue
        if c in "'\"`":
            j = i + 1
            while j < n:
                if chars[j] == c:
                    if j + 1 < n and chars[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            tokens.append(chars[i:min(j + 1, n)])
            i = j + 1
        elif c == "-" and i + 1 < n and chars[i + 1] == "-":
            j = i
            while j < n and chars[j] != "\n":
                j += 1
            tokens.append(chars[i:j] + _COMMENT_MARK)
            i = j
        elif c.isalpha() or c.isdigit() or c in "_.@$:":
            j = i
            while j < n and (chars[j].isalpha() or chars[j].isdigit() or chars[j] in "_.@$:*"):
                j += 1
            if j == i:
                j = i + 1
            tokens.append(chars[i:j])
            i = j
        elif c in "<>=!":
            j = i + 1
            while j < n and chars[j] in "<>=":
                j += 1
            tokens.append(chars[i:j])
            i = j
        else:
            tokens.append(c)
            i += 1
    return tokens


def format_sql(text: str) -> str:
    tokens = _tokenize_sql(text)
    out = ""
    depth = 0
    clause = ""
    line_start = True

    def emit(s: str, space: bool = True) -> None:
        nonlocal out, line_start
        if not line_start and space and not out.endswith("(") and not out.endswith("\n"):
            out += " "
        out += s
        line_start = False

    def newline(indent: int) -> None:
        nonlocal out, line_start
        out += "\n" + "  " * indent
        line_start = True

    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        up = tok.upper()
        if tok.endswith(_COMMENT_MARK):
            emit(tok[:-1])
            newline(0)
            idx += 1
            continue
        if tok == "(":
            depth += 1
            emit("(")
            idx += 1
            continue
        if tok == ")":
            depth = max(0, depth - 1)
            out += ")"
            line_start = False
            idx += 1
            continue
        if tok == ";":
            out += ";"
            newline(0)
            clause = ""
            idx += 1
            continue
        if tok == ",":
            out += ","
            if depth == 0 and clause in ("SELECT", "SET", "GROUP BY", "ORDER BY"):
                newline(1)
            idx += 1
            continue
        if tok in (".", "*"):
            emit(tok)
            idx += 1
            continue

        word, consumed = up, 1
        if idx + 1 < len(tokens):
            two = up + " " + tokens[idx + 1].upper()
            if two in ("GROUP BY", "ORDER BY", "INSERT INTO", "DELETE FROM", "UNION ALL"):
                word, consumed = two, 2
        if depth == 0 and word in _MAJOR:
            if out and not out.endswith("\n") and not line_start:
                newline(0)
            emit(word)
            clause = word
            idx += consumed
            if word in ("SELECT", "SET"):
                if word == "SELECT" and idx < len(tokens) and tokens[idx].upper() == "DISTINCT":
                    emit("DISTINCT")
                    idx += 1
                newline(1)
            continue
        if depth == 0 and up in _JOIN:
            parts = [up]
            k = idx + 1
            while parts[-1] != "JOIN" and k < len(tokens) and tokens[k].upper() in _JOIN:
                parts.append(tokens[k].upper())
                k += 1
            if not out.endswith("\n"):
                newline(0)
            emit(" ".join(parts))
            clause = "JOIN"
            idx = k
            continue
        if depth == 0 and up in ("AND", "OR") and clause in ("WHERE", "JOIN", "HAVING"):
            newline(1)
            emit(up)
            idx += 1
            continue
        if depth == 0 and up == "ON":
            newline(1)
            emit(up)
            idx += 1
            continue
        emit(up if up in _KEYWORDS else tok)
        idx += 1
    return out.strip()
