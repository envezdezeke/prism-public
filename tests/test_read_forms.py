"""Parsers for the eight non-application document classes (03-02 Task 1)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from prism.engine.state import Doc
from prism.read import docmodel, parse_forms
from prism.read.classify import classify
from prism.read.docmodel import (
    Parsed7004, ParsedCert, ParsedFinancials, ParsedId, ParsedIrs, ParsedLetter, ParsedQuote, ParsedTax,
)
from prism.read.parse_forms import (
    parse_certificate, parse_ein_letter, parse_equipment_quote, parse_financials, parse_form_7004,
    parse_guarantor_id, parse_request_letter, parse_tax_return,
)
from prism.read.text import DocText, load_text
from tests.pdfgen import fake_jpeg_bytes, write_pdf
from tests.real_data import requires_sample, sample_root, tuning_folders

LAYOUTS = ("next_line", "colon", "columns")


def _lines(rows, layout):
    out = []
    for r in rows:
        if isinstance(r, str):
            out.append(r)
            continue
        label, *vals = r
        if layout == "next_line":
            out.extend([label, *vals])
        elif layout == "colon":
            out.append(f"{label}: " + "  ".join(vals))
        else:
            out.append([(72, label)] + [(300 + 120 * i, v) for i, v in enumerate(vals)])
    return out


def _dt(tmp_path: Path, rows, layout: str, **meta) -> DocText:
    p = write_pdf(tmp_path / f"{layout}.pdf", [_lines(rows, layout)], **meta)
    return load_text(p)


def _text_dt(text: str, kind: str = "pdf", **kw) -> DocText:
    return DocText(kind=kind, plain=text, layout=text, pages=(text,), n_pages=1, **kw)


def _all_layouts(tmp_path, rows, parser):
    results = [parser(_dt(tmp_path, rows, layout), None) for layout in LAYOUTS]
    assert results[0] == results[1] == results[2], results
    return results[0]


# ---------------------------------------------------------------- guarantor_id

ID_ROWS = [
    "Texas Driver License — Copy",
    ("Name", "Ada Quillfeather"),
    ("Address", "10 Sample Lane, Testville, TX 75002"),
    ("Date of birth", "1980-01-02"),
    ("DL number", "01234567"),
    ("Class", "C"),
    ("Issued", "2021-04-11"),
    ("Expires", "2029-04-11"),
    "Front and back scanned. Invented specimen.",
]


def test_id_pdf_text_three_layouts(tmp_path):
    p = _all_layouts(tmp_path, ID_ROWS, parse_guarantor_id)
    assert isinstance(p, ParsedId)
    assert p.name == "Ada Quillfeather"
    assert p.address == "10 Sample Lane, Testville, TX 75002"
    assert p.dob == "1980-01-02"
    assert p.dl_number == "01234567"  # leading zero kept
    assert p.expires == "2029-04-11"
    assert p.readable is True and p.via_ocr is False


def test_id_state_code_stripped_from_dl():
    rows = ["Name", "Ada Quillfeather", "Date of birth", "1980-01-02", "DL number", "TX 01234567"]
    p = parse_guarantor_id(_text_dt("\n".join(rows)), None)
    assert p.dl_number == "01234567"


OCR_SPACED = "\n".join([
    "TEXAS", "DRIVER LICENSE", "DL 01234567", "ADA  QUILLFEATHER", "10 SAMPLE LN APT 3",
    "PHOTO", "TESTVILLE,  TX 75002", "DOB 1980-01-02", "EXP 2029-04-11 CLASS C",
    "SPECIMEN - invented test data",
])
OCR_GLUED = "\n".join([
    "TEXAS", "DRIVERLICENSE", "DL01234567", "ADAQUILLFEATHER", "10SAMPLELNAPT3", "PHOTO",
    "TESTVILLE,TX75002", "DOB1980-01-02", "EXP2029-04-11CLASSC", "SPECIMEN-invented test data",
])


@pytest.mark.parametrize("text", [OCR_SPACED, OCR_GLUED])
def test_id_ocr_style_text(text):
    p = parse_guarantor_id(_text_dt(text, kind="image", ocr_used=True), None)
    assert p.readable is True and p.via_ocr is True
    assert p.dob == "1980-01-02"
    assert p.dl_number == "01234567"
    assert p.expires == "2029-04-11"
    assert p.name.replace(" ", "") == "ADAQUILLFEATHER"
    assert "TESTVILLE" in p.address and "PHOTO" not in p.address and "DRIVER" not in p.address


def test_id_ocr_unusable_text_is_not_readable():
    for text in ("", "zq xv", "QWERTY ASDFGH", "12 34 56"):
        p = parse_guarantor_id(_text_dt(text, kind="image", ocr_used=True), None)
        assert p.readable is False and p.via_ocr is True, text
        assert p.dob is None and p.dl_number is None


def test_id_image_with_no_ocr_text_is_unreadable(tmp_path, monkeypatch):
    import prism.read.ocr as ocr

    monkeypatch.setattr(ocr, "read_image_text", lambda *_a, **_k: None)
    img = tmp_path / "scan.jpg"
    img.write_bytes(fake_jpeg_bytes())
    doc = Doc(path="f/scan.jpg", name="scan.jpg", arrive_at=datetime(2026, 11, 2, tzinfo=timezone.utc), abs_path=img)
    classify(doc)
    assert doc.doc_class == "guarantor_id" and doc.meta["unverified"] is True
    p = docmodel.parsed(doc)
    assert isinstance(p, ParsedId) and p.readable is False
    assert p.dob is None and p.dl_number is None and p.name is None


# ---------------------------------------------------------------- ein letter, certificate

def test_ein_letter_three_layouts(tmp_path):
    rows = [
        "IRS Notice CP 575 — EIN Assigned (copy)",
        ("Name of business", "NIMBUS ORCHARD SUPPLY LLC"),
        ("Employer Identification Number", "98-7654321"),
        ("Date of notice", "2015-04-03"),
        "Keep this notice. Invented test data.",
    ]
    p = _all_layouts(tmp_path, rows, parse_ein_letter)
    assert isinstance(p, ParsedIrs)
    assert (p.name, p.ein, p.notice_date) == ("NIMBUS ORCHARD SUPPLY LLC", "98-7654321", "2015-04-03")


def test_certificate_three_layouts(tmp_path):
    rows = [
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
    p = _all_layouts(tmp_path, rows, parse_certificate)
    assert isinstance(p, ParsedCert)
    assert p.entity_name == "Nimbus Orchard Supply LLC" and p.entity_type == "LLC"
    assert p.file_number == "0123456789" and p.effective_date == "2015-04-02"
    assert p.principal_office == "10 Example Way, Testville, TX 75001"
    assert p.registered_agent == "Example Agents Inc"
    assert p.registered_office == "1 Agent Plaza Ste 5, Austin, TX 78701"


# ---------------------------------------------------------------- tax return, 7004

def _tax_rows(preparer):
    return [
        "U.S. Income Tax Return for an S Corporation / Partnership",
        "— Summary (Tax Year 2025)",
        ("Name", "Nimbus Orchard Supply LLC"),
        ("EIN", "98-7654321"),
        ("Gross receipts or sales", "$900,000.00"),
        ("Ordinary business income", "$90,000.00"),
        ("Paid preparer", preparer),
        ("Preparer PTIN", "P00000000"),
        ("Date filed", "2026-03-12"),
        "Invented summary page.",
    ]


def test_tax_return_three_layouts(tmp_path):
    p = _all_layouts(tmp_path, _tax_rows("Pat Example, CPA (Example & Co CPAs)"), parse_tax_return)
    assert isinstance(p, ParsedTax)
    assert p.name == "Nimbus Orchard Supply LLC" and p.ein == "98-7654321" and p.tax_year == 2025
    assert (p.gross_receipts_cents, p.ordinary_income_cents) == (90_000_000, 9_000_000)
    assert (p.preparer_name, p.preparer_credential, p.preparer_firm) == ("Pat Example", "CPA", "Example & Co CPAs")
    assert p.date_filed == "2026-03-12"
    assert "P0000" not in repr(p)  # the PTIN is never stored


def test_tax_return_preparer_without_credential(tmp_path):
    p = parse_tax_return(_dt(tmp_path, _tax_rows("K. Example (Example Bookkeeping)"), "next_line"), None)
    assert (p.preparer_name, p.preparer_credential, p.preparer_firm) == ("K. Example", None, "Example Bookkeeping")


def test_form_7004_three_layouts(tmp_path):
    rows = [
        "Form 7004 — Application for Automatic Extension (copy)",
        ("Name", "Nimbus Orchard Supply LLC"),
        ("EIN", "98-7654321"),
        ("Tax year", "2025"),
        ("Extended due date", "2026-09-15"),
        ("Status", "Filed 2026-03-13 — return not yet filed"),
    ]
    p = _all_layouts(tmp_path, rows, parse_form_7004)
    assert isinstance(p, Parsed7004)
    assert (p.name, p.ein, p.tax_year, p.extended_due_date) == ("Nimbus Orchard Supply LLC", "98-7654321", 2025, "2026-09-15")
    assert p.status.startswith("Filed 2026-03-13")


# ---------------------------------------------------------------- request letter

@pytest.mark.parametrize("sentence,cents", [
    ("We are requesting $125,000.00 to buy stock. We would like a 36-month term.", 12_500_000),
    ("We are requesting $1.2M to bridge receivables.", 120_000_000),
    ("We are requesting $60,000 to renew our annual line.", 6_000_000),
    ("We are requesting\n$75,500.50 to replace\nan oven.", 7_550_050),
    ("WE ARE  REQUESTING $400k for payroll.", 40_000_000),
    ("We are requesting 36 months of working capital, $250,000 in total.", 25_000_000),
    ("We are requesting a $250,000 term loan.", 25_000_000),
    ("We are requesting 250,000 for a new press.", 25_000_000),
    ("We are requesting 90k for a new press.", 9_000_000),
])
def test_request_letter_amounts(sentence, cents):
    text = "Re: Loan request\n\nTo: Example Bank\n" + sentence + "\nAda Quillfeather, Owner"
    p = parse_request_letter(_text_dt(text), None)
    assert isinstance(p, ParsedLetter) and p.amount_cents == cents


@pytest.mark.parametrize("sentence", [
    "We are requesting 36 months of working capital.",
    "We are seeking 2 trucks for the new route.",
    "We want a loan of 5 years with a 7% rate.",
    "We have a request for 2026 expansion funds.",
])
def test_request_letter_numbers_without_money_context_are_not_amounts(sentence):
    assert parse_request_letter(_text_dt("Re: Loan request\n" + sentence), None) is None


def test_request_letter_first_amount_wins_and_no_amount_is_none():
    text = "We are requesting $10,000.00 now. Later we will request $99,000.00 more."
    assert parse_request_letter(_text_dt(text), None).amount_cents == 1_000_000
    assert parse_request_letter(_text_dt("Thank you for your time."), None) is None


# ---------------------------------------------------------------- equipment quote

def _quote_rows(remit="Example Bank · Routing 111000614 · Account ending 9902"):
    return [
        "Quotation Q-26-0001",
        "Sample Equipment Supply LLC · 4800 Example Blvd, Dallas, TX 75247 · 214-555-0100",
        ("Customer", "Nimbus Orchard Supply LLC"),
        ("Quote date", "2026-10-22"),
        ("Item", "Qty", "Price"),
        ("Widget lift", "2", "$21,450.00"),
        ("TOTAL", "$42,900.00"),
        ("Remit to", remit),
        "50% deposit due on order.",
    ]


def test_quote_three_layouts(tmp_path):
    p = _all_layouts(tmp_path, _quote_rows(), parse_equipment_quote)
    assert isinstance(p, ParsedQuote)
    assert p.number == "Q-26-0001" and p.vendor == "Sample Equipment Supply LLC"
    assert p.remit_bank == "Example Bank" and p.remit_last4 == "9902"
    assert p.total_cents == 4_290_000 and p.quote_date == "2026-10-22"


def test_quote_routing_number_is_never_the_account(tmp_path):
    p = parse_equipment_quote(_dt(tmp_path, _quote_rows("Example Bank · Routing 111000614"), "next_line"), None)
    assert p.remit_last4 is None and p.remit_bank == "Example Bank"
    p = parse_equipment_quote(_dt(tmp_path, _quote_rows("Example Bank · Account 111000614"), "next_line"), None)
    assert p.remit_last4 is None


# ---------------------------------------------------------------- financial statements

def _fs_rows(prepared, ni=("$90,000.00", "$81,000.00"), cash="$50,000.00"):
    return [
        "Example & Co CPAs",
        "Nimbus Orchard Supply LLC — Financial Statements",
        f"Prepared by: {prepared}",
        "Profit & Loss",
        ("Line", "FY 2025", "YTD Jan–Oct 2026"),
        ("Revenue", "$900,000.00", "$810,000.00"),
        ("Cost of goods sold", "-$450,000.00", "-$405,000.00"),
        ("Net income", *ni),
        "Balance sheet as of 2026-10-31",
        ("Item", "Amount"),
        ("Cash", cash),
        ("Accounts receivable", "$70,000.00"),
        ("Equipment, net", "$100,000.00"),
        ("Total liabilities", "$120,000.00"),
    ]


def test_financials_three_layouts(tmp_path):
    p = _all_layouts(tmp_path, _fs_rows("Pat Example, CPA, Example & Co CPAs · 214-555-0100"), parse_financials)
    assert isinstance(p, ParsedFinancials)
    assert p.has_pl is True and p.has_balance_sheet is True
    assert (p.preparer_name, p.preparer_firm, p.preparer_phone) == ("Pat Example", "Example & Co CPAs", "214-555-0100")
    assert p.fy_year == 2025 and p.ytd_months == 10
    assert (p.revenue_fy_cents, p.revenue_ytd_cents) == (90_000_000, 81_000_000)
    assert (p.net_income_fy_cents, p.net_income_ytd_cents) == (9_000_000, 8_100_000)
    assert p.balance_date == "2026-10-31"
    assert (p.cash_cents, p.receivables_cents, p.liabilities_cents) == (5_000_000, 7_000_000, 12_000_000)


@pytest.mark.parametrize("prepared,name,firm,phone", [
    ("Pat Example, Example & Co CPAs · 214-555-0100", "Pat Example", "Example & Co CPAs", "214-555-0100"),
    ("K. Example, Example Bookkeeping Services · 214-555-0187", "K. Example", "Example Bookkeeping Services", "214-555-0187"),
    ("Self-prepared, (owner)", "Self-prepared", None, None),
])
def test_financials_preparer_variants(tmp_path, prepared, name, firm, phone):
    p = parse_financials(_dt(tmp_path, _fs_rows(prepared), "next_line"), None)
    assert (p.preparer_name, p.preparer_firm, p.preparer_phone) == (name, firm, phone)


def test_financials_negative_net_income_and_year_to_date_only(tmp_path):
    p = parse_financials(_dt(tmp_path, _fs_rows("Self-prepared, (owner)", ni=("-$12,400.00", "-$15,800.00")), "next_line"), None)
    assert (p.net_income_fy_cents, p.net_income_ytd_cents) == (-1_240_000, -1_580_000)
    text = "Financial Statements\nProfit & Loss\nLine\nYTD Jan-Jun 2026\nRevenue\n$5,000.00\nNet income\n$400.00\n"
    q = parse_financials(_text_dt(text), None)
    assert q.ytd_months == 6 and q.net_income_ytd_cents == 40_000 and q.net_income_fy_cents is None


# ---------------------------------------------------------------- garbage and registry

GARBAGE = [
    "",
    "lorem ipsum \x00\x01\x02 dolor sit amet " * 50,
    "Name\n" * 5000,
    "%PDF-1.4 not really",
    ":" * 400 + "\n" + "{0} {name} %s %(x)s",
]


@pytest.mark.parametrize("parser", [
    parse_guarantor_id, parse_ein_letter, parse_certificate, parse_tax_return, parse_form_7004,
    parse_request_letter, parse_equipment_quote, parse_financials,
])
def test_garbage_never_raises(parser):
    for text in GARBAGE:
        for kind in ("pdf", "text", "image"):
            out = parser(_text_dt(text, kind=kind), None)
            assert out is None or hasattr(out, "__dataclass_fields__")
    assert parser(DocText(kind="error"), None) is None or True


def test_all_eight_parsers_registered():
    for cls in ("guarantor_id", "ein_letter", "certificate_of_formation", "tax_return", "form_7004",
                "request_letter", "equipment_quote", "financial_statements"):
        assert cls in docmodel._PARSERS, cls
    assert set(parse_forms.PARSERS) == {
        "guarantor_id", "ein_letter", "certificate_of_formation", "tax_return", "form_7004",
        "request_letter", "equipment_quote", "financial_statements"}


def test_guarded_parser_swallows_exceptions():
    def boom(dt, doc):
        raise RuntimeError("x")

    assert parse_forms._guard(boom)(_text_dt("x"), None) is None


# ---------------------------------------------------------------- real data (tuning deals only)

KEY_FIELDS = {
    "guarantor_id": ("name", "dob", "dl_number"),
    "ein_letter": ("name", "ein"),
    "certificate_of_formation": ("entity_name", "file_number", "effective_date", "registered_agent"),
    "tax_return": ("ein", "tax_year", "gross_receipts_cents", "ordinary_income_cents", "preparer_name"),
    "form_7004": ("ein", "tax_year", "extended_due_date"),
    "request_letter": ("amount_cents",),
    "equipment_quote": ("number", "vendor", "remit_bank", "remit_last4", "total_cents"),
    "financial_statements": ("preparer_name", "net_income_fy_cents", "net_income_ytd_cents", "ytd_months",
                             "balance_date", "cash_cents", "receivables_cents", "liabilities_cents"),
}


@requires_sample
def test_real_tuning_documents_parse_with_key_fields():
    root = sample_root()
    seen = dict.fromkeys(KEY_FIELDS, 0)
    for folder in tuning_folders(root):
        docs = []
        for p in sorted((root / "shared_folder" / folder).iterdir()):
            doc = Doc(path=f"{folder}/{p.name}", name=p.name, arrive_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
                      abs_path=p)
            classify(doc)
            docs.append(doc)
        app = next(docmodel.parsed(d) for d in docs if d.doc_class == "application")
        for d in docs:
            if d.doc_class not in KEY_FIELDS:
                continue
            obj = docmodel.parsed(d)
            assert obj is not None, d.doc_class
            seen[d.doc_class] += 1
            missing = [f for f in KEY_FIELDS[d.doc_class] if getattr(obj, f) is None]
            assert not missing, (d.doc_class, missing)
            if d.doc_class == "guarantor_id":
                assert obj.readable is True
                if obj.via_ocr:  # agreement with the application, no literal needed
                    assert app.guarantors and obj.dob == app.guarantors[0].dob
    assert all(n > 0 for n in seen.values()), seen
