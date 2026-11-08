"""Shared builders for the 03-02 Read tests: generated deal folders as classified ``DealState`` objects.

Everything is invented and generated at test time (PDFs via tests/pdfgen.py); nothing is committed as a binary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from prism.engine.state import DealState, Doc
from prism.read import docmodel, text as read_text
from prism.read.classify import classify
from tests.pdfgen import application_pdf_pages, write_pdf
from tests.test_read_forms import ID_ROWS, _fs_rows, _lines, _quote_rows, _tax_rows

TZ = timezone(timedelta(hours=-6))
BASE = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)

IRS_ROWS = [
    "IRS Notice CP 575 — EIN Assigned (copy)",
    ("Name of business", "NIMBUS ORCHARD SUPPLY LLC"),
    ("Employer Identification Number", "98-7654321"),
    ("Date of notice", "2015-04-03"),
]
CERT_ROWS = [
    "Certificate of Formation — Filing Acknowledgment",
    "Office of the Secretary of State (invented record)",
    ("Entity name", "Nimbus Orchard Supply LLC"),
    ("Entity type", "LLC"),
    ("File number", "0123456789"),
    ("Effective date", "2015-04-02"),
    ("Principal office", "10 Example Way, Testville, TX 75001"),
    ("Registered agent", "Example Agents Inc"),
    ("Registered office", "1 Agent Plaza Ste 5, Austin, TX 78701"),
    ("Organizer", "Ada Quillfeather"),
]
LETTER_LINES = ["Re: Loan request", "To: Example Bank", "We are requesting $125,000.00 to buy stock.",
                "Ada Quillfeather, Owner"]
F7004_ROWS = [
    "Form 7004 — Application for Automatic Extension (copy)",
    ("Name", "Nimbus Orchard Supply LLC"), ("EIN", "98-7654321"), ("Tax year", "2025"),
    ("Extended due date", "2026-09-15"), ("Status", "Filed 2026-03-13"),
]

DEFAULT_PARTS: dict[str, Any] = {
    "application": "default", "id": ID_ROWS, "irs": IRS_ROWS, "cert": CERT_ROWS,
    "tax": _tax_rows("Pat Example, CPA (Example & Co CPAs)"),
    "fs": _fs_rows("Pat Example, CPA, Example & Co CPAs · 214-555-0100"),
    "letter": LETTER_LINES,
}


def clear_caches() -> None:
    read_text.clear_cache()
    docmodel.clear_cache()


def make_doc(folder_dir: Path, folder: str, name: str, lines: Optional[Sequence] = None, *,
             pages: Optional[Sequence] = None, minutes: int = 0, **meta) -> Doc:
    """Write one PDF (``lines`` for a one-page doc, ``pages`` for several), classify it, return the Doc."""
    page_list = list(pages) if pages is not None else [_lines(lines, "next_line")]
    path = write_pdf(folder_dir / name, page_list, **meta)
    doc = Doc(path=f"{folder}/{name}", name=name, arrive_at=BASE + timedelta(minutes=minutes), abs_path=path)
    classify(doc)
    return doc


def make_state(tmp_path: Path, parts: Optional[dict[str, Any]] = None, *, folder: str = "Deal",
               app_over: Optional[dict[str, str]] = None, producer: Optional[str] = "Producer One",
               creator: Optional[str] = "Creator One") -> DealState:
    """A classified deal. ``parts`` maps part name to rows (None drops it, missing keys use defaults).

    Part names: application, id, irs, cert, tax, fs, letter, f7004, quote."""
    clear_caches()
    merged = dict(DEFAULT_PARTS)
    merged.update(parts or {})
    d = tmp_path / folder
    d.mkdir(parents=True, exist_ok=True)
    docs: list[Doc] = []
    minute = 0
    app = merged["application"]
    if app is not None:
        pages = application_pdf_pages("next_line", **(app_over or {})) if app == "default" else app
        docs.append(make_doc(d, folder, "a_app.pdf", pages=pages, minutes=minute, producer=producer, creator=creator))
    for key, name in (("id", "b_id.pdf"), ("irs", "c_irs.pdf"), ("cert", "d_cert.pdf"), ("tax", "e_tax.pdf"),
                      ("f7004", "e_7004.pdf"), ("fs", "f_fs.pdf"), ("letter", "g_letter.pdf"),
                      ("quote", "h_quote.pdf")):
        rows = merged.get(key)
        if rows is None:
            continue
        minute += 5
        docs.append(make_doc(d, folder, name, rows, minutes=minute, producer="Other Producer"))
    state = DealState(folder=folder, docs=sorted(docs, key=lambda x: (x.arrive_at, x.path)))
    return state


def quote_rows(**kw):
    return _quote_rows(**kw)
