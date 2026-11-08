"""Content classifier: 11 classes, traps, images, period, rename invariance."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from prism.engine.state import Doc
from prism.read import ocr
from prism.read import text as read_text
from prism.read.classify import DOC_CLASSES, classify, score_classes, statement_period
from prism.read.text import DocText, load_text
from tests.pdfgen import application_lines, fake_jpeg_bytes, write_csv, write_pdf
from tests.real_data import holdout_names, requires_sample, sample_root, tuning_folders

TZ = timezone(timedelta(hours=-6))

TEXTS: dict[str, list[str]] = {
    "application": application_lines(),
    "guarantor_id": ["Texas Driver License - Copy", "Name", "Ada Q", "Address", "1 A St", "Date of birth",
                     "1980-01-02", "DL number", "1234", "Class", "C", "Expires", "2030-01-01"],
    "certificate_of_formation": ["Certificate of Formation - Filing Acknowledgment",
                                 "Office of the Secretary of State (synthetic record)", "Entity name", "X LLC",
                                 "File number", "123", "Registered agent", "Y Agents"],
    "ein_letter": ["IRS Notice CP 575 - EIN Assigned (copy)", "Name of business", "X LLC",
                   "Employer Identification Number", "12-3456789", "Date of notice", "2016-03-03"],
    "bank_statement": ["Sample Bank", "Business Checking Statement", "Account holder", "X", "Opening balance",
                       "$1.00", "Closing balance", "$2.00", "Statement period", "2026-08-01 to 2026-08-31"],
    "financial_statements": ["Alpha Tax & Accounting Services", "Nimbus LLC - Financial Statements",
                             "Prepared by: A Person", "Profit & Loss", "Revenue", "Balance sheet as of 2026-10-31"],
    "tax_return": ["U.S. Income Tax Return for an S Corporation / Partnership", "- Summary (Tax Year 2025)",
                   "Name", "X", "EIN", "12-3456789", "Gross receipts or sales", "$1",
                   "Ordinary business income", "$2", "Paid preparer", "A Person"],
    "form_7004": ["Form 7004 - Application for Automatic Extension (copy)", "Name", "X", "EIN", "12-3456789",
                  "Tax year", "2025", "Extended due date", "2026-09-15", "Status", "Accepted"],
    "request_letter": ["Re: Loan request", "To: Example Bank Commercial Lending",
                       "We are requesting $10,000.00 to buy things.", "A. Owner, Owner, X LLC"],
    "equipment_quote": ["Quotation Q-100", "Vendor Co - 1 Rd - 555-0100", "Remit to:",
                        "Bank - Routing 123456789 - Account ending 4321", "TOTAL $1,000.00"],
    "other": ["Active Client Contracts - Summary", "Client", "Value", "A", "10"],
}


def _doc(path: Path) -> Doc:
    return Doc(path=f"Deal/{path.name}", name=path.name, arrive_at=datetime(2026, 11, 2, 9, 0, tzinfo=TZ),
               abs_path=path)


@pytest.fixture(autouse=True)
def _fresh():
    read_text.clear_cache()


def test_doc_classes_complete():
    assert set(TEXTS) == set(DOC_CLASSES) and len(DOC_CLASSES) == 11
    assert "form_7004" in DOC_CLASSES and "request_letter" in DOC_CLASSES


@pytest.mark.parametrize("cls", sorted(TEXTS))
def test_each_class_is_chosen(tmp_path, cls):
    d = classify(_doc(write_pdf(tmp_path / "x.pdf", [TEXTS[cls]])))
    assert d.doc_class == cls, (d.doc_class, d.meta.get("class_score"), d.meta.get("class_runner_up"))
    assert "class_score" in d.meta and "class_runner_up" in d.meta


def test_returns_same_doc_and_records_score(tmp_path):
    doc = _doc(write_pdf(tmp_path / "x.pdf", [TEXTS["application"]], producer="P1"))
    assert classify(doc) is doc
    assert doc.meta["class_score"] >= 3.0 and doc.meta["producer"] == "P1"
    assert doc.meta["class_runner_up"] is None or len(doc.meta["class_runner_up"]) == 2


def test_below_min_score_is_other_with_meta(tmp_path):
    d = classify(_doc(write_pdf(tmp_path / "x.pdf", [["hello world", "nothing to see"]])))
    assert d.doc_class == "other" and d.meta["class_score"] < 3.0
    assert d.meta["class_runner_up"] is not None


def test_never_raises_on_bad_files(tmp_path):
    (tmp_path / "z").write_bytes(b"")
    (tmp_path / "r").write_bytes(bytes(range(256)) * 10)
    for name in ("z", "r", "missing"):
        d = classify(_doc(tmp_path / name))
        assert d.doc_class == "other"


def test_csv_statement_and_period(tmp_path):
    rows = ["Some Bank - acct ...1234 - Owner", ["Posted Date", "Details", "Amount", "Running Bal"],
            ["5/1/26", "DEP", "100.00", "1100.00"], ["5/9/26", "DEP", "50.00", "1150.00"],
            ["6/1/26", "DEP", "5.00", "1155.00"]]
    d = classify(_doc(write_csv(tmp_path / "s.dat", rows)))
    assert d.doc_class == "bank_statement" and d.period == "2026-05"


def test_pdf_statement_period_is_end_date_month(tmp_path):
    d = classify(_doc(write_pdf(tmp_path / "s.pdf", [TEXTS["bank_statement"]])))
    assert d.period == "2026-08"
    assert classify(_doc(write_pdf(tmp_path / "o.pdf", [TEXTS["application"]]))).period is None


@pytest.mark.parametrize("value", [
    "05/01/2026 - 05/31/2026", "May 1, 2026 - May 31, 2026", "1 May 2026 to 31 May 2026",
    "2026-05-01 to 2026-05-31", "5/1/26 through 5/31/26",
])
def test_statement_period_reads_non_iso_period_dates(value):
    dt = DocText(kind="pdf", plain=f"Business Checking Statement\nStatement period: {value}\nOpening balance $1.00")
    assert statement_period(dt) == "2026-05"


def test_statement_period_none():
    assert statement_period(DocText(kind="pdf", plain="nothing here")) is None
    assert statement_period(DocText(kind="csv", rows=(("a", "b", "c"),))) is None


def test_image_unreadable_is_unverified_guarantor_id(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "read_image_text", lambda src: None)
    p = tmp_path / "photo.dat"
    p.write_bytes(fake_jpeg_bytes())
    d = classify(_doc(p))
    assert d.doc_class == "guarantor_id" and d.meta["unverified"] is True


def test_image_with_license_text_is_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "read_image_text", lambda src: "TEXAS DRIVER LICENSE\nDL 12345\nDOB 1980-01-02")
    p = tmp_path / "photo.dat"
    p.write_bytes(fake_jpeg_bytes())
    d = classify(_doc(p))
    assert d.doc_class == "guarantor_id" and d.meta["unverified"] is False


def test_file_name_is_never_consulted(tmp_path):
    a = classify(_doc(write_pdf(tmp_path / "loan application final.pdf", [TEXTS["application"]])))
    b = classify(_doc(write_pdf(tmp_path / "bank statement 2026-08.pdf", [TEXTS["application"]])))
    assert a.doc_class == b.doc_class == "application"
    c = classify(_doc(write_pdf(tmp_path / "irs ein.pdf", [TEXTS["bank_statement"]])))
    assert c.doc_class == "bank_statement"


def test_score_classes_sorted_and_stable(tmp_path):
    dt = load_text(write_pdf(tmp_path / "x.pdf", [TEXTS["tax_return"]]))
    ranked = score_classes(dt)
    assert ranked[0][0] == "tax_return"
    assert [s for _, s in ranked] == sorted((s for _, s in ranked), reverse=True)


@requires_sample
def test_tuning_class_histogram_and_periods():
    root = sample_root()
    tuning = set(tuning_folders(root))
    arrivals = [json.loads(ln) for ln in (root / "sim" / "arrivals.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    counts: Counter = Counter()
    periods: set[str] = set()
    no_period = 0
    for row in arrivals:
        folder = row["path"].split("/", 1)[0]
        if folder not in tuning or "/" not in row["path"]:
            continue
        d = classify(_doc(root / "shared_folder" / Path(*row["path"].split("/"))))
        counts[d.doc_class] += 1
        if d.doc_class == "bank_statement":
            if d.period:
                periods.add(d.period)
            else:
                no_period += 1
    assert no_period == 0
    assert periods and all("2026-05" <= p <= "2026-10" for p in periods)
    assert dict(counts) == {
        "application": 10, "guarantor_id": 10, "certificate_of_formation": 10, "ein_letter": 10,
        "financial_statements": 10, "request_letter": 10, "tax_return": 9, "form_7004": 1,
        "equipment_quote": 4, "other": 1, "bank_statement": 59,  # 03-RESEARCH S1 said 5 quotes; the 10 tuning folders hold 134 files and 4 quotes
    }
    assert holdout_names()  # tuning only: the holdout deals were skipped above
