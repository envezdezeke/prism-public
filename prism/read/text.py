"""Per-file text layer (READ-01): pypdf plain and layout text, CSV rows, image OCR, metadata.

``load_text`` never raises and never looks at the file name: the kind comes from the leading
bytes. Results are cached by (absolute path, mtime_ns, size). Input is untrusted: every parse is
wrapped in try/except and capped by ``limits`` in config/read.yaml.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from prism.read import ocr as _ocr
from prism.read.settings import get_config

logging.getLogger("pypdf").setLevel(logging.ERROR)

_CACHE_MAX = 512
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_MARKER_RE = re.compile(r"\bpage\s{1,5}(\d{1,4})\s{1,5}of\s{1,5}(\d{1,4})\b", re.IGNORECASE)
_CSV_SNIFF_LINES = 6
_MAX_CSV_ROWS = 20000


@dataclass(frozen=True)
class DocText:
    kind: str  # pdf | csv | image | text | empty | error
    plain: str = ""
    layout: str = ""
    pages: tuple[str, ...] = ()  # layout text per physical page (plain as fallback)
    producer: Optional[str] = None
    creator: Optional[str] = None
    n_pages: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    ocr_used: bool = False
    error: Optional[str] = None


_CACHE: dict[tuple[str, int, int], DocText] = {}


def clear_cache() -> None:
    _CACHE.clear()
    _TABLE_CACHE.clear()


def page_markers(text: str) -> list[tuple[int, int]]:
    """``Page X of N`` markers in order of appearance (bounded, case-insensitive)."""
    return [(int(a), int(b)) for a, b in _MARKER_RE.findall((text or "")[:2_000_000])]


def _path_of(doc_or_path: Any) -> Path:
    p = getattr(doc_or_path, "abs_path", doc_or_path)
    return Path(p)


def _meta_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _embedded_images(page: Any, cap: int) -> list[bytes]:
    """Raw bytes of up to ``cap`` images embedded in a pypdf page (never raises)."""
    out: list[bytes] = []
    try:
        for img in page.images:
            if len(out) >= cap:
                break
            data = getattr(img, "data", None)
            if data:
                out.append(bytes(data))
    except Exception:  # noqa: BLE001
        pass
    return out


def _plumber_pages(data: bytes, wanted: Optional[set[int]], max_pages: int) -> dict[int, str]:
    """pdfplumber layout text for the wanted page indexes (all when None). Never raises."""
    out: dict[int, str] = {}
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages[:max_pages]):
                if wanted is not None and i not in wanted:
                    continue
                try:
                    out[i] = page.extract_text(layout=True) or ""
                except Exception:  # noqa: BLE001
                    out[i] = ""
    except Exception:  # noqa: BLE001
        pass
    return out


def _read_pdf(data: bytes, limits: dict[str, int]) -> DocText:
    import pypdf

    max_pages = limits["max_pages"]
    reader = None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return DocText(kind="error", error="encrypted")
        pages = list(reader.pages)
    except Exception as e:  # noqa: BLE001
        # pypdf could not open it: let pdfplumber have a go before giving up
        got = _plumber_pages(data, None, max_pages)
        text = "\n".join(got[i] for i in sorted(got))[: limits["max_chars"]]
        if text.strip():
            return DocText(kind="pdf", plain=text, layout=text, pages=tuple(got[i] for i in sorted(got)),
                           n_pages=len(got))
        return DocText(kind="error", error=f"pdf: {type(e).__name__}")
    producer = creator = None
    try:
        md = reader.metadata
        if md is not None:
            producer, creator = _meta_str(md.producer), _meta_str(md.creator)
    except Exception:  # noqa: BLE001
        pass
    total = len(pages)
    plain_pages: list[str] = []
    layout_pages: list[str] = []
    for page in pages[:max_pages]:
        try:
            plain = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            plain = ""
        try:
            layout = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # noqa: BLE001
            layout = ""
        plain_pages.append(plain)
        layout_pages.append(layout)
    ocr_used = False
    empty = {i for i, (a, b) in enumerate(zip(plain_pages, layout_pages)) if not (a.strip() or b.strip())}
    if empty:
        for i, txt in _plumber_pages(data, empty, max_pages).items():
            if txt.strip():
                plain_pages[i] = layout_pages[i] = txt
                empty.discard(i)
    budget = int(limits.get("max_images", 6))
    for i in sorted(empty):
        if budget <= 0:
            break
        found: list[str] = []
        for blob in _embedded_images(pages[i], budget):
            budget -= 1
            got = _ocr.read_image_text(blob)
            if got and got.strip():
                found.append(got.strip())
        if found:
            plain_pages[i] = layout_pages[i] = "\n".join(found)
            ocr_used = True
    plain_all = "\n".join(plain_pages)[: limits["max_chars"]]
    layout_all = "\n".join(layout_pages)[: limits["max_chars"]]
    out_pages = tuple((lp if lp.strip() else pp) for lp, pp in zip(layout_pages, plain_pages))
    return DocText(kind="pdf", plain=plain_all, layout=layout_all, pages=out_pages,
                   producer=producer, creator=creator, n_pages=total, ocr_used=ocr_used)


_TABLE_CACHE: dict[tuple[str, int, int], tuple] = {}


def pdf_tables(path: Any) -> tuple:
    """Tables per pdfplumber, flattened: a tuple of tables, each a tuple of rows of cell text.
    Lazy and cached by file version; returns ``()`` on any error."""
    try:
        p = _path_of(path)
        st = os.stat(p)
        key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
        if key in _TABLE_CACHE:
            return _TABLE_CACHE[key]
        limits = get_config().limits
        if st.st_size > limits["max_bytes"]:
            return ()
        import pdfplumber

        tables: list[tuple] = []
        with pdfplumber.open(str(p)) as pdf:
            for page in pdf.pages[: limits["max_pages"]]:
                for tbl in page.extract_tables() or []:
                    tables.append(tuple(tuple("" if c is None else str(c) for c in row) for row in tbl))
        result = tuple(tables)
    except Exception:  # noqa: BLE001
        return ()
    if len(_TABLE_CACHE) >= _CACHE_MAX:
        _TABLE_CACHE.pop(next(iter(_TABLE_CACHE)))
    _TABLE_CACHE[key] = result
    return result


def _read_textual(data: bytes, limits: dict[str, int]) -> DocText:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return DocText(kind="error", error="binary")
    text = text[: limits["max_chars"]]
    lines = [ln for ln in text.splitlines() if ln.strip()][:_CSV_SNIFF_LINES]
    wide = 0
    for ln in lines:
        try:
            if len(next(csv.reader([ln]))) >= 3:
                wide += 1
        except (csv.Error, StopIteration):
            continue
    if wide >= 2:
        rows: list[tuple[str, ...]] = []
        try:
            for i, row in enumerate(csv.reader(io.StringIO(text))):
                if i >= _MAX_CSV_ROWS:
                    break
                rows.append(tuple(c.strip() for c in row))
        except csv.Error:
            pass
        return DocText(kind="csv", plain=text, layout=text, pages=(text,), rows=tuple(rows), n_pages=1)
    return DocText(kind="text", plain=text, layout=text, pages=(text,), n_pages=1)


def _load_uncached(path: Path, size: int) -> DocText:
    limits = dict(get_config().limits)
    if size == 0:
        return DocText(kind="empty")
    if size > limits["max_bytes"]:
        return DocText(kind="error", error="oversize")
    with open(path, "rb") as f:
        data = f.read()
    head = data[:16]
    if head.startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        try:
            return _read_pdf(data, limits)
        except Exception as e:  # noqa: BLE001
            return DocText(kind="error", error=f"pdf: {type(e).__name__}")
    if head.startswith(b"\xff\xd8\xff") or head.startswith(_PNG_SIG):
        got = _ocr.read_image_text(str(path))
        text = (got or "")[: limits["max_chars"]]
        return DocText(kind="image", plain=text, layout=text, pages=(text,), n_pages=1, ocr_used=bool(text))
    return _read_textual(data, limits)


def load_text(doc_or_path: Any) -> DocText:
    """Text layer for a Doc or a path. Never raises; cached per file version."""
    try:
        path = _path_of(doc_or_path)
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return DocText(kind="error", error="unreadable: not a file")
        key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except Exception as e:  # noqa: BLE001
        return DocText(kind="error", error=f"unreadable: {type(e).__name__}")
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        result = _load_uncached(path, key[2])
    except Exception as e:  # noqa: BLE001
        result = DocText(kind="error", error=f"read: {type(e).__name__}")
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result
