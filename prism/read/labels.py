"""Label-anchored value extraction (D-01). Synonyms come from config; no layout is assumed.

A line is a label hit when its casefolded text (trailing colon stripped) equals a synonym, or it
starts with a synonym followed by a colon or by two or more spaces. The value is the rest of that
line, or the following non-empty lines (up to ``max_lines``) until the next label, stop or section
heading. Document text is never used as a pattern: synonyms are ``re.escape``d tokens.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional, Sequence

_WS = re.compile(r"\s+")
_MAX_LINES_SCANNED = 4000


def _fold(s: str) -> str:
    return _WS.sub(" ", s.strip().casefold())


def _syn_patterns(syn: str) -> Optional[tuple[str, re.Pattern[str], re.Pattern[str]]]:
    folded = _fold(syn).rstrip(":").strip()
    if not folded:
        return None
    body = r"\s+".join(re.escape(t) for t in folded.split(" "))
    whole = re.compile(rf"^\s*{body}\s*:?\s*$", re.IGNORECASE)
    inline = re.compile(rf"^\s*{body}\s*(?::\s*|\s{{2,}})(\S.*)$", re.IGNORECASE)
    return folded, whole, inline


def _compile(labels: Mapping[str, Sequence[str]]) -> list[tuple[str, int, re.Pattern[str], re.Pattern[str]]]:
    out = []
    for field, syns in labels.items():
        for syn in syns:
            p = _syn_patterns(syn)
            if p:
                out.append((field, len(p[0]), p[1], p[2]))
    out.sort(key=lambda t: -t[1])  # longest synonym wins
    return out


def _is_stop(line: str, stops: Sequence[str]) -> bool:
    f = _fold(line)
    for s in stops:
        sf = _fold(s)
        if sf.endswith("*"):
            if f.startswith(sf[:-1].rstrip()):
                return True
        elif f == sf.rstrip(":"):
            return True
    return False


def _scan(text: str, labels: Mapping[str, Sequence[str]], stops: Sequence[str], max_lines: int):
    """Yield (field, value) for every label hit in document order."""
    comp = _compile(labels)
    lines = (text or "").splitlines()[:_MAX_LINES_SCANNED]
    hits: list[Optional[tuple[str, Optional[str]]]] = []
    for ln in lines:
        hit = None
        for field, _n, whole, inline in comp:
            if whole.match(ln):
                hit = (field, None)
                break
            m = inline.match(ln)
            if m:
                hit = (field, m.group(1).strip())
                break
        hits.append(hit)
    for i, h in enumerate(hits):
        if h is None:
            continue
        field, val = h
        if val is not None:
            yield field, val
            continue
        parts: list[str] = []
        j = i + 1
        while j < len(lines) and len(parts) < max(1, max_lines):
            nxt = lines[j]
            if not nxt.strip():
                j += 1
                continue
            if hits[j] is not None or _is_stop(nxt, stops):
                break
            parts.append(nxt.strip())
            j += 1
        yield field, " ".join(parts)


def extract_labeled(text: str, labels: Mapping[str, Sequence[str]], stops: Sequence[str] = (),
                    max_lines: int = 4) -> dict[str, str]:
    """First value per field. A label present with no value gives ``""``."""
    out: dict[str, str] = {}
    for field, val in _scan(text, labels, stops, max_lines):
        out.setdefault(field, val)
    return out


def extract_labeled_all(text: str, labels: Mapping[str, Sequence[str]], field: str,
                        stops: Sequence[str] = (), max_lines: int = 4) -> list[str]:
    """Every value of ``field`` in document order (e.g. one Name per guarantor block)."""
    return [v for f, v in _scan(text, labels, stops, max_lines) if f == field]


def split_sections(text: str, headings: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Split text at heading lines. ``headings`` maps a section name to heading regexes (from config).

    Returns ``{name: text after that heading up to the next heading}``; text before the first
    heading is under ``""``. A name that never appears is absent.
    """
    comp = []
    for name, pats in headings.items():
        for p in pats:
            try:
                comp.append((name, re.compile(p, re.IGNORECASE)))
            except re.error:
                continue
    out: dict[str, list[str]] = {"": []}
    cur = ""
    for ln in (text or "").splitlines()[:_MAX_LINES_SCANNED]:
        name = next((n for n, rx in comp if rx.search(ln)), None)
        if name is not None:
            cur = name
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(ln)
    return {k: "\n".join(v) for k, v in out.items() if k == "" or v or k in out}


def scan_labeled(text: str, labels: Mapping[str, Sequence[str]], stops: Sequence[str] = (),
                 max_lines: int = 4) -> list[tuple[str, str]]:
    """Every (field, value) label hit in document order (used to group repeated blocks)."""
    return list(_scan(text, labels, stops, max_lines))
