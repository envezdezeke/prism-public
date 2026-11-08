"""Test-time document builders for the Read tests (owned by plan 03-01, shared by every later Read test).

Nothing here is committed as a binary: PDFs, CSVs and fake images are written into tmp folders at
test time. Text is invented. ``make_pdf`` builds a small valid PDF by hand (Helvetica, WinAnsi,
correct xref offsets) so pypdf can read it.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Optional, Sequence, Union

Line = Union[str, Sequence[tuple[int, str]]]  # a str is drawn at x=72; a list of (x, text) cells is a column row

_TOP = 775
_STEP = 12
_LINES_PER_PAGE = 62


def _esc(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("cp1252", "replace")


def _page_stream(lines: Sequence[Line]) -> bytes:
    parts: list[bytes] = [b"BT", b"/F1 11 Tf"]
    y = _TOP
    for line in lines[:_LINES_PER_PAGE]:
        cells = [(72, line)] if isinstance(line, str) else list(line)
        for x, text in cells:
            if text:
                parts.append(b"1 0 0 1 %d %d Tm (" % (x, y) + _esc(text) + b") Tj")
        y -= _STEP
    parts.append(b"ET")
    return b"\n".join(parts)


def make_pdf(pages: Sequence[Sequence[Line]], producer: Optional[str] = None,
             creator: Optional[str] = None) -> bytes:
    """Build a valid minimal PDF. Each page is a list of lines (str or list of (x, text) cells)."""
    n = len(pages)
    page_ids = [4 + 2 * i for i in range(n)]
    info_id = 4 + 2 * n
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % p for p in page_ids)
        + b"] /Count %d >>" % n,
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for i, lines in enumerate(pages):
        pid, cid = page_ids[i], page_ids[i] + 1
        objs[pid] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % cid)
        stream = _page_stream(lines)
        objs[cid] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    info = []
    if producer is not None:
        info.append(b"/Producer (" + _esc(producer) + b")")
    if creator is not None:
        info.append(b"/Creator (" + _esc(creator) + b")")
    objs[info_id] = b"<< " + b" ".join(info) + b" >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objs):
        offsets[oid] = out.tell()
        out.write(b"%d 0 obj\n" % oid + objs[oid] + b"\nendobj\n")
    xref = out.tell()
    size = info_id + 1
    out.write(b"xref\n0 %d\n" % size)
    out.write(b"0000000000 65535 f \n")
    for oid in range(1, size):
        out.write(b"%010d 00000 n \n" % offsets[oid])
    out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, info_id, xref))
    return out.getvalue()


def write_pdf(path: Path, pages: Sequence[Sequence[Line]], **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_pdf(pages, **kw))
    return path


def write_csv(path: Path, rows: Sequence[Union[str, Sequence[object]]]) -> Path:
    """Write rows as CSV. A ``str`` row is written verbatim (for free-text header lines)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in rows:
        if isinstance(row, str):
            buf.write(row + "\n")
        else:
            w.writerow(row)
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def fake_jpeg_bytes() -> bytes:
    """A JPEG signature plus filler: enough for kind detection when OCR is monkeypatched."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"


def fake_png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ---- invented application used by several tests -------------------------------------------------

APP_FIELDS_DEFAULT = {
    "legal": "Nimbus Orchard Supply LLC",
    "dba": "-",
    "ein": "98-7654321",
    "amount": "$125,000.00",
    "product": "Term Loan",
    "term": "36 months",
    "guarantor": "Ada Quillfeather",
}


def application_lines(**over: str) -> list[str]:
    """Plain-extraction-style application: each label on one line, its value on the next."""
    f = {**APP_FIELDS_DEFAULT, **over}
    return [
        "Example Test Bank - Commercial Lending",
        "Small Business Loan Application",
        "Section A - Business",
        "Legal business name", f["legal"],
        "DBA / trade name", f["dba"],
        "Federal EIN", f["ein"],
        "Entity type / state", "LLC / Texas",
        "Date business established", "2015-04-02",
        "Business address", "10 Example Way, Testville, TX 75001",
        "Business phone / email", "555-0100 / hello@example.invalid",
        "Industry (NAICS)", "Wholesale Trade (424490)",
        "Number of employees", "12",
        "Annual revenue (most recent year)", "$900,000.00",
        "Primary bank / account", "Sample Savings Bank ...4321",
        "Section B - Loan request",
        "Amount requested", f["amount"],
        "Product", f["product"],
        "Use of proceeds", "Buy seasonal inventory",
        "Requested term", f["term"],
        "Collateral offered", "Inventory and receivables",
        "Funds needed by", "No specific date",
        "Section C - Owner / guarantor",
        "Name", f["guarantor"],
        "Title / ownership", "Owner / 100%",
        "Date of birth", "1980-01-02",
        "SSN", "XXX-XX-0000",
        "Home address", "20 Sample Lane, Testville, TX 75002",
        "Driver license", "TX 01234567",
        "Affiliated businesses", "None",
        "I certify the information in this application is true and complete.",
        "/s/ " + f["guarantor"] + " Date: 2026-11-02",
    ]


def application_pdf_pages(layout: str = "next_line", **over: str) -> list[list[Line]]:
    """One-page application in one of three layouts: next_line, colon, columns."""
    base = application_lines(**over)
    labels = {
        "Legal business name", "DBA / trade name", "Federal EIN", "Entity type / state",
        "Date business established", "Business address", "Business phone / email", "Industry (NAICS)",
        "Number of employees", "Annual revenue (most recent year)", "Primary bank / account",
        "Amount requested", "Product", "Use of proceeds", "Requested term", "Collateral offered",
        "Funds needed by", "Name", "Title / ownership", "Date of birth", "SSN", "Home address",
        "Driver license", "Affiliated businesses",
    }
    if layout == "next_line":
        return [list(base)]
    out: list[Line] = []
    i = 0
    while i < len(base):
        cur = base[i]
        if cur in labels and i + 1 < len(base):
            val = base[i + 1]
            out.append(f"{cur}: {val}" if layout == "colon" else [(72, cur), (300, val)])
            i += 2
        else:
            out.append(cur)
            i += 1
    return [out]
