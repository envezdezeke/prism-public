"""Full application parser and the 9 facts."""
from __future__ import annotations

import json

import pytest

from prism.engine.run import run_sim
from prism.read import docmodel
from prism.read.docmodel import ParsedApplication
from prism.read.parse_application import parse_application
from prism.read.text import DocText
from tests.pdfgen import application_lines, application_pdf_pages, write_pdf
from tests.real_data import (
    holdout_names, load_score_split, requires_sample, sample_root,
)


def _dt(lines):
    text = "\n".join(lines)
    return DocText(kind="pdf", plain=text, layout=text, pages=(text,), n_pages=1)


def _layout_dt(layout, **over):
    """DocText from a real generated PDF in one of the three layouts."""
    import tempfile
    from pathlib import Path

    from prism.read.text import load_text

    with tempfile.TemporaryDirectory() as d:
        p = write_pdf(Path(d) / "a.pdf", application_pdf_pages(layout, **over))
        return load_text(p)


def test_three_layouts_identical():
    parsed = [parse_application(_layout_dt(l)) for l in ("next_line", "colon", "columns")]
    assert parsed[0] == parsed[1] == parsed[2]
    p = parsed[0]
    assert p.legal_name == "Nimbus Orchard Supply LLC"
    assert p.ein == "98-7654321" and p.amount_cents == 12500000
    assert p.term_months == 36 and p.product == "Term Loan"
    assert p.bank_name == "Sample Savings Bank" and p.bank_last4 == "4321"
    assert p.business_phone == "555-0100" and p.business_email == "hello@example.invalid"
    assert p.formation_date == "2015-04-02" and p.revenue_cents == 90000000


def test_two_guarantors_and_ssn_never_stored():
    lines = application_lines()
    i = lines.index("Affiliated businesses")
    extra = ["Name", "Bea Second", "Title / ownership", "Owner / 40%", "Date of birth", "1985-05-06",
             "SSN", "XXX-XX-1111", "Home address", "30 Other Rd, Testville, TX 75003",
             "Driver license", "TX 00099999"]
    lines = lines[:i] + extra + lines[i:]
    p = parse_application(_dt(lines))
    assert [g.name for g in p.guarantors] == ["Ada Quillfeather", "Bea Second"]
    assert p.guarantors[0].dob == "1980-01-02" and p.guarantors[0].dl_number == "01234567"
    assert p.guarantors[1].dl_number == "00099999"
    blob = repr(p)
    assert "1111" not in blob
    assert "XXX-XX" not in blob


def test_declared_none_versus_missing():
    p = parse_application(_dt(application_lines()))
    assert "needed_by" in p.declared_none and "needed_by" not in p.missing
    assert "dba" in p.declared_none
    lines = application_lines()
    i = lines.index("Requested term")
    del lines[i:i + 2]
    q = parse_application(_dt(lines))
    assert "term_months" in q.missing


def test_signature_variants():
    lines = application_lines()
    assert parse_application(_dt(lines)).signed is True
    assert parse_application(_dt(lines)).signature_date == "2026-11-02"
    unsigned = [ln for ln in lines if not ln.startswith("/s/")]
    assert parse_application(_dt(unsigned)).signed is None
    assert parse_application(_dt(unsigned + ["Signature", ""])).signed is False


def test_application_date_only_from_explicit_label():
    lines = application_lines()
    assert parse_application(_dt(lines)).application_date is None
    lines2 = lines + ["Application date", "2026-10-30"]
    assert parse_application(_dt(lines2)).application_date == "2026-10-30"


def test_needed_by_free_text_and_range_revenue():
    lines = application_lines()
    lines[lines.index("Funds needed by") + 1] = "Friday 2026-11-06 by 3:00 PM (payroll)"
    lines[lines.index("Annual revenue (most recent year)") + 1] = "approx 400-500k (annualized, note)"
    p = parse_application(_dt(lines))
    assert p.needed_by == "2026-11-06T15:00-06:00"
    assert p.revenue_cents is None and p.revenue_range_cents == (40000000, 50000000)
    assert "approx" in p.evidence["revenue"]


def _facts(tmp_path, mutate=None):
    lines = application_lines()
    if mutate:
        mutate(lines)
    folder = tmp_path / "shared"
    write_pdf(folder / "Deal One" / "x.pdf", [lines])
    arr = tmp_path / "arr.jsonl"
    arr.write_text(json.dumps({"path": "Deal One/x.pdf", "arrive_at": "2026-11-02T09:00-06:00"}) + "\n")
    docmodel.clear_cache()
    st = run_sim(folder, arr, data_root=tmp_path).states["Deal One"]
    return st.facts


def _set(lines, label, value):
    lines[lines.index(label) + 1] = value


def test_all_nine_facts(tmp_path):
    f = _facts(tmp_path)
    got = {k: v.value for k, v in f.items()}
    assert got == {
        "legal_name": "Nimbus Orchard Supply LLC", "ein": "98-7654321", "amount_requested": 125000,
        "purpose_category": "inventory", "product": "Term Loan", "term_months": 36,
        "guarantor_name": "Ada Quillfeather", "annual_revenue_stated": 900000,
    }
    assert "needed_by" not in f
    assert all(v.source_doc == "Deal One/x.pdf" for v in f.values())
    assert f["legal_name"].confidence == 0.95 and f["purpose_category"].confidence == 0.6


def test_product_term_purpose_and_amount_variants(tmp_path):
    def mut(lines):
        _set(lines, "Product", "Equipment Loan")
        _set(lines, "Use of proceeds", "Things for the shop")
        _set(lines, "Requested term", "3 years")
        _set(lines, "Amount requested", "$1.2M")
        _set(lines, "Annual revenue (most recent year)", "$400-500k range")
    f = _facts(tmp_path, mut)
    assert f["product"].value == "Equipment Loan" and f["term_months"].value == 36
    assert f["amount_requested"].value == 1200000
    assert f["purpose_category"].value == "equipment" and f["purpose_category"].confidence == 0.5
    assert f["annual_revenue_stated"].value == 450000 and f["annual_revenue_stated"].confidence == 0.5


def test_unknown_values_absent_not_guessed(tmp_path):
    def mut(lines):
        _set(lines, "Product", "Mystery Plan")
        _set(lines, "Use of proceeds", "Miscellaneous things")
        _set(lines, "Requested term", "soon")
    f = _facts(tmp_path, mut)
    assert "product" not in f and "term_months" not in f and "purpose_category" not in f


def test_purpose_keyword_order(tmp_path):
    def mut(lines):
        _set(lines, "Use of proceeds", "Cover payroll and refinance a note")
    assert _facts(tmp_path, mut)["purpose_category"].value == "payroll"


@requires_sample
def test_tuning_facts_at_least_88_of_90(tmp_path):
    from pathlib import Path

    from prism.engine.writer import build_submission

    root = sample_root()
    ss = load_score_split()
    result = run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)
    payload = build_submission(result.states, result.history_flags, result.errors, team="T", set_name="s")
    gold = json.loads((Path(__file__).resolve().parents[1] / "eval" / "gold.json").read_text(encoding="utf-8"))
    res = ss.score_tuning(payload, gold, holdout_names())
    t = res["totals"]["extracted"]
    bad = sorted(f"{f}" for d in res["deals"].values() for f, c in d["extracted"].items() if not c["ok"])
    assert t["ok"] >= 88, f"{t['ok']}/{t['total']}; failing fields: {bad}"
