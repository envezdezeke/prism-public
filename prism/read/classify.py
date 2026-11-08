"""Document classification (owner: Phase 3, READ-01).

Contract: classify by CONTENT (never the filename), fill ``doc_class``, ``period`` and
``meta``, return the same Doc, and never raise: an unreadable file becomes class "other".

Each class is scored from weighted signals in config/read.yaml (phrase, regex, csv_headers; a
signal may carry a negative weight and a ``where`` of ``title`` or ``any``). The top class wins
when it reaches ``classify.min_score``; otherwise the class is ``other`` (D-03). The score and the
runner-up are recorded in ``doc.meta``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from prism.engine.state import Doc
from prism.read.labels import extract_labeled
from prism.read.normalize import dates_in, parse_date
from prism.read.settings import get_config
from prism.read.text import DocText, load_text, page_markers

DOC_CLASSES: tuple[str, ...] = (
    "application",
    "guarantor_id",
    "certificate_of_formation",
    "ein_letter",
    "bank_statement",
    "financial_statements",
    "tax_return",
    "form_7004",
    "request_letter",
    "equipment_quote",
    "other",
)

_WS = re.compile(r"\s+")
_REGEX_CACHE: dict[str, Optional[re.Pattern[str]]] = {}


def _fold(s: str) -> str:
    return _WS.sub(" ", s.casefold()).strip()


def _regex(pattern: str) -> Optional[re.Pattern[str]]:
    if pattern not in _REGEX_CACHE:
        try:
            _REGEX_CACHE[pattern] = re.compile(pattern, re.IGNORECASE)
        except re.error:
            _REGEX_CACHE[pattern] = None
    return _REGEX_CACHE[pattern]


def _title_region(text: str, n: int) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:n]
    return "\n".join(lines)


def _csv_header_tokens(dt: DocText) -> set[str]:
    out: set[str] = set()
    for row in dt.rows[:6]:
        for cell in row:
            out.add(_fold(cell))
    return out


def score_classes(dt: DocText) -> list[tuple[str, float]]:
    """Score every class (``other`` excluded), sorted by score descending then DOC_CLASSES order."""
    cfg = get_config()
    title_n = int(cfg.classify["title_lines"])
    body = dt.plain or dt.layout
    body_f = _fold(body)
    title_f = _fold(_title_region(body, title_n))
    headers = _csv_header_tokens(dt) if dt.kind == "csv" else set()
    scores: dict[str, float] = {}
    for cls, signals in cfg.class_signals.items():
        total = 0.0
        for sig in signals or ():
            try:
                kind, w = sig["kind"], float(sig.get("weight", 1.0))
                region_f = title_f if sig.get("where") == "title" else body_f
                if kind == "phrase":
                    hit = _fold(str(sig["text"])) in region_f
                elif kind == "regex":
                    rx = _regex(str(sig["pattern"]))
                    region = _title_region(body, title_n) if sig.get("where") == "title" else body
                    hit = bool(rx and rx.search(region[:200_000]))
                elif kind == "csv_headers":
                    hit = bool(headers) and all(any(_fold(str(t)) in h for h in headers) for t in sig["tokens"])
                else:
                    hit = False
            except (KeyError, TypeError, ValueError):
                hit = False
            if hit:
                total += w
        scores[cls] = total
    order = {c: i for i, c in enumerate(DOC_CLASSES)}
    return sorted(((c, s) for c, s in scores.items() if c != "other"),
                  key=lambda cs: (-cs[1], order.get(cs[0], len(order))))


_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_LINE_US_DATE = re.compile(r"(?m)^\s*(\d{1,2}/\d{1,2}/\d{2,4})\b")


def _dominant_month(dates: list[str]) -> Optional[str]:
    counts: dict[str, int] = {}
    for d in dates:
        counts[d[:7]] = counts.get(d[:7], 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda m: (counts[m], m))


def statement_period(dt: DocText) -> Optional[str]:
    """``YYYY-MM`` of a statement from CONTENT: the month of the printed period's end date (PDF) or the
    dominant posted-date month (CSV). None when neither is readable."""
    try:
        if dt.kind == "csv":
            idx = None
            dates: list[str] = []
            for row in dt.rows:
                if idx is None:
                    cells = [_fold(c) for c in row]
                    cand = [i for i, c in enumerate(cells) if "date" in c]
                    if cand and any("amount" in c for c in cells):
                        idx = cand[0]
                    continue
                if idx < len(row):
                    iso = parse_date(row[idx])
                    if iso:
                        dates.append(iso)
            return _dominant_month(dates)
        labels = {"period": list((get_config().classify.get("period") or {}).get("labels") or [])}
        text = dt.plain or dt.layout
        found = extract_labeled(text, labels, max_lines=2).get("period", "")
        parsed = dates_in(found)
        if parsed:
            return parsed[-1][:7]
        us = [d for d in (parse_date(m) for m in _LINE_US_DATE.findall(text)) if d]
        return _dominant_month(us)
    except Exception:  # noqa: BLE001
        return None


def _classify(doc: Doc) -> None:
    cfg = get_config()
    dt = load_text(doc)
    ranked = score_classes(dt)
    top = ranked[0] if ranked else ("other", 0.0)
    runner = [ranked[1][0], ranked[1][1]] if len(ranked) > 1 else None
    doc.meta.update({
        "producer": dt.producer, "creator": dt.creator, "n_pages": dt.n_pages,
        "class_score": top[1], "class_runner_up": runner,
        "page_markers": [[a, b] for a, b in page_markers(dt.plain)],
        "ocr_used": dt.ocr_used,
    })
    doc.meta.pop("error", None)
    doc.meta.pop("unverified", None)
    doc.period = None
    if top[1] >= float(cfg.classify["min_score"]):
        doc.doc_class = top[0]
        if dt.kind == "image":
            doc.meta["unverified"] = False
    elif dt.kind == "image":
        # a photo we cannot read is present-but-unverified, never a missing document (D-05, READ-04)
        doc.doc_class = str(cfg.classify["image_default_class"])
        doc.meta["unverified"] = True
    else:
        doc.doc_class = "other"
    if doc.doc_class == "bank_statement":
        doc.period = statement_period(dt)


def classify(doc: Doc) -> Doc:
    """Fill doc_class, period and meta from the file content. Never raises."""
    try:
        _classify(doc)
    except Exception as e:  # noqa: BLE001
        doc.doc_class = "other"
        doc.meta["error"] = f"classify: {type(e).__name__}"
    return doc
