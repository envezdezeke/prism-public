"""MISSING_DOC and the J11 quiet rule inside Read (READ-05, D-11). Statement, compare and old-trick rules
have their own test files; here every other rule group is kept quiet by giving the deal clean documents."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from prism.contract import ISSUE_RE
from prism.engine.config import Rules
from prism.engine.run import run_sim
from prism.engine.state import DealState, Doc
from prism.engine.visibility import parse_time
from prism.read import docmodel, requirements
from prism.read import issues as read_issues
from prism.read.docmodel import (Guarantor, Parsed7004, ParsedApplication, ParsedCert, ParsedFinancials,
                                 ParsedId, ParsedStatement, ParsedTax)
from tests.read_fixtures import make_state
from tests.test_read_forms import ID_ROWS
from tests.statement_fixtures import write_statement

TZ = timezone(timedelta(hours=-6))
BASE = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)
RULES = Rules()
QUIET = timedelta(minutes=30)


# ---------------------------------------------------------------- fake world (no files worth reading)

class World:
    """Docs with chosen classes and chosen parsed objects, no real parsing involved."""

    def __init__(self, tmp_path: Path, monkeypatch):
        docmodel._load_modules()  # so the real parser modules cannot overwrite the fakes later
        docmodel.clear_cache()
        self.dir = tmp_path
        self.table: dict[str, object] = {}
        self.docs: list[Doc] = []
        for cls in docmodel_classes():
            monkeypatch.setitem(docmodel._PARSERS, cls, lambda dt, doc: self.table.get(doc.path))

    def add(self, cls: str, parsed=None, minutes: int | None = None, period: str | None = None,
            markers=()) -> Doc:
        n = len(self.docs)
        path = self.dir / f"{n}.dat"
        path.write_bytes(b"x")
        at = BASE + timedelta(minutes=n if minutes is None else minutes)
        doc = Doc(path=f"Deal/{n}.dat", name=f"{n}.dat", arrive_at=at, abs_path=path, doc_class=cls,
                  period=period, meta={"page_markers": [list(m) for m in markers]})
        self.table[doc.path] = parsed
        self.docs.append(doc)
        return doc

    def state(self, issues=()) -> DealState:
        docs = sorted(self.docs, key=lambda d: (d.arrive_at, d.path))
        st = DealState(folder="Deal", docs=docs, issues=list(issues))
        st.last_arrival_at = max((d.arrive_at for d in docs), default=None)
        return st


def sub(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.mkdir()
    return p


def docmodel_classes() -> list[str]:
    from prism.read.classify import DOC_CLASSES
    return list(DOC_CLASSES)


def app(**over) -> ParsedApplication:
    base = dict(signature_date="2026-11-02", signed=True, product="Term Loan", formation_date="2015-04-02",
                guarantors=(Guarantor(name="A One"),))
    base.update(over)
    return ParsedApplication(**base)


MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


def complete_world(tmp_path, monkeypatch, *, months=MONTHS, application=None, fs=None, extra=True) -> World:
    w = World(tmp_path, monkeypatch)
    w.add("application", application or app())
    w.add("guarantor_id", ParsedId(readable=True))
    w.add("certificate_of_formation", ParsedCert(effective_date="2015-04-02"))
    w.add("ein_letter")
    w.add("financial_statements", fs or ParsedFinancials(has_pl=True, has_balance_sheet=True))
    w.add("tax_return", ParsedTax())
    for m in months:
        w.add("bank_statement", None, period=m)
    return w


def detect(state, minutes_after=30):
    return read_issues.detect_issues(state, RULES, state.last_arrival_at + timedelta(minutes=minutes_after))


def missing_docs(state, **kw):
    return [i for i in detect(state, **kw) if i.startswith("MISSING_DOC")]


# ---------------------------------------------------------------- reference date and window

def test_statement_window_is_the_six_full_months_before_the_reference_month():
    assert requirements.statement_window(date(2026, 11, 2)) == MONTHS
    assert requirements.statement_window(date(2026, 11, 30)) == MONTHS
    assert requirements.statement_window(date(2026, 1, 5)) == ["2025-07", "2025-08", "2025-09", "2025-10",
                                                               "2025-11", "2025-12"]


def test_reference_date_prefers_application_date_then_signature_then_last_arrival(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.add("application", app(application_date="2026-10-20", signature_date="2026-11-02"))
    assert requirements.reference_date(w.state()) == date(2026, 10, 20)
    w2 = World(sub(tmp_path, "b"), monkeypatch)
    w2.add("application", app(application_date=None, signature_date="2026-11-02"))
    assert requirements.reference_date(w2.state()) == date(2026, 11, 2)
    w3 = World(sub(tmp_path, "c"), monkeypatch)
    w3.add("application", app(application_date=None, signature_date=None))
    assert requirements.reference_date(w3.state()) == BASE.date()


def test_business_age_rule_for_7004():
    ref = date(2026, 11, 2)
    assert requirements.business_age_ok_for_7004("2025-03-10", ref) is True
    assert requirements.business_age_ok_for_7004("2024-11-03", ref) is True      # one day under two years
    assert requirements.business_age_ok_for_7004("2024-11-02", ref) is False     # exactly two years
    assert requirements.business_age_ok_for_7004("2015-04-02", ref) is False
    assert requirements.business_age_ok_for_7004(None, ref) is False
    assert requirements.business_age_ok_for_7004("not a date", ref) is False


# ---------------------------------------------------------------- the clean deal and single documents

def test_complete_deal_has_no_missing_doc(tmp_path, monkeypatch):
    assert detect(complete_world(tmp_path, monkeypatch).state()) == []


def test_no_docs_returns_empty_and_never_raises():
    assert read_issues.detect_issues(DealState(folder="X"), RULES, BASE) == []


@pytest.mark.parametrize("cls,label", [
    ("application", "application"), ("certificate_of_formation", "certificate_of_formation"),
    ("ein_letter", "ein_letter"), ("guarantor_id", "guarantor_id"), ("financial_statements", "financial_statements"),
    ("tax_return", "tax_return"),
])
def test_dropping_one_document_class_raises_its_label(tmp_path, monkeypatch, cls, label):
    w = complete_world(tmp_path, monkeypatch)
    w.docs = [d for d in w.docs if d.doc_class != cls]
    assert missing_docs(w.state()) == [f"MISSING_DOC:{label}"]


def test_other_class_documents_count_for_nothing(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch)
    w.docs = [d for d in w.docs if d.doc_class != "ein_letter"]
    w.add("other")
    assert missing_docs(w.state()) == ["MISSING_DOC:ein_letter"]


def test_unsigned_application_counts_as_missing_but_unknown_signature_does_not(tmp_path, monkeypatch):
    unsigned = complete_world(tmp_path, monkeypatch, application=app(signed=False))
    assert missing_docs(unsigned.state()) == ["MISSING_DOC:application"]
    unknown = complete_world(sub(tmp_path, "u"), monkeypatch, application=app(signed=None))
    assert missing_docs(unknown.state()) == []


# ---------------------------------------------------------------- statements

def test_one_missing_statement_month_is_named(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, months=[m for m in MONTHS if m != "2026-08"])
    assert missing_docs(w.state()) == ["MISSING_DOC:bank_statement_2026-08"]


def test_statement_with_unknown_period_counts_for_nothing(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, months=MONTHS[:5])
    w.add("bank_statement", None, period=None)
    assert missing_docs(w.state()) == ["MISSING_DOC:bank_statement_2026-10"]


def test_statement_month_found_by_the_statement_parser_counts_when_classify_found_none(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, months=MONTHS[:5])
    w.add("bank_statement", ParsedStatement(period="2026-10", signs_verified=True), period=None)
    assert missing_docs(w.state()) == []


def test_window_anchors_on_last_arrival_when_no_application_date(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.add("application", app(signature_date=None, application_date=None))
    got = missing_docs(w.state())
    assert [i for i in got if "bank_statement" in i] == [f"MISSING_DOC:bank_statement_{m}" for m in MONTHS]


# ---------------------------------------------------------------- the quiet rule (J11, D-11)

def _five_of_six(tmp_path, monkeypatch):
    return complete_world(tmp_path, monkeypatch, months=[m for m in MONTHS if m != "2026-08"])


def test_quiet_boundary_is_inclusive(tmp_path, monkeypatch):
    st = _five_of_six(tmp_path, monkeypatch).state()
    label = "MISSING_DOC:bank_statement_2026-08"
    assert label not in detect(st, 29)
    assert label in detect(st, 30)
    assert label in detect(st, 31)


def test_reported_issue_is_sticky_through_later_arrivals(tmp_path, monkeypatch):
    w = _five_of_six(tmp_path, monkeypatch)
    label = "MISSING_DOC:bank_statement_2026-08"
    st = w.state()
    assert label in detect(st, 300)
    # an unrelated paper arrives later; "now" is only a minute after it, but the issue is already open
    w.add("other", minutes=600)
    st2 = w.state(issues=[label])
    assert label in read_issues.detect_issues(st2, RULES, st2.last_arrival_at + timedelta(minutes=1))
    # the same deal without the earlier report stays quiet at that instant
    st3 = w.state()
    assert label not in read_issues.detect_issues(st3, RULES, st3.last_arrival_at + timedelta(minutes=1))


def test_late_arrival_resolves_the_issue(tmp_path, monkeypatch):
    w = _five_of_six(tmp_path, monkeypatch)
    label = "MISSING_DOC:bank_statement_2026-08"
    w.add("bank_statement", None, minutes=500, period="2026-08")
    st = w.state(issues=[label])
    assert label not in read_issues.detect_issues(st, RULES, st.last_arrival_at + timedelta(minutes=45))


def test_every_returned_string_is_valid_and_sorted(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.add("other")
    got = detect(w.state())
    assert got == sorted(set(got)) and all(ISSUE_RE.fullmatch(i) for i in got)
    assert "MISSING_DOC:application" in got


# ---------------------------------------------------------------- tax return or extension (J12)

def _tax_world(tmp_path, monkeypatch, *, tax=False, f7004=False, formation="2025-03-10", cert_date=None):
    w = complete_world(tmp_path, monkeypatch, application=app(formation_date=formation))
    w.docs = [d for d in w.docs if d.doc_class != "tax_return"]
    if cert_date is not None:
        w.table[next(d.path for d in w.docs if d.doc_class == "certificate_of_formation")] = ParsedCert(
            effective_date=cert_date)
    if tax:
        w.add("tax_return", ParsedTax())
    if f7004:
        w.add("form_7004", Parsed7004())
    return w


def test_7004_satisfies_a_business_under_two_years(tmp_path, monkeypatch):
    assert missing_docs(_tax_world(tmp_path, monkeypatch, f7004=True).state()) == []


def test_7004_does_not_satisfy_a_business_two_years_old_or_more(tmp_path, monkeypatch):
    w = _tax_world(tmp_path, monkeypatch, f7004=True, formation="2020-01-01")
    assert missing_docs(w.state()) == ["MISSING_DOC:tax_return"]


def test_7004_with_unknown_formation_date_does_not_satisfy(tmp_path, monkeypatch):
    w = _tax_world(tmp_path, monkeypatch, f7004=True, formation=None)
    w.table[next(d.path for d in w.docs if d.doc_class == "certificate_of_formation")] = ParsedCert()
    assert missing_docs(w.state()) == ["MISSING_DOC:tax_return"]


def test_formation_date_falls_back_to_the_certificate(tmp_path, monkeypatch):
    young = _tax_world(tmp_path, monkeypatch, f7004=True, formation=None, cert_date="2025-06-01")
    assert missing_docs(young.state()) == []


def test_a_tax_return_of_any_age_satisfies(tmp_path, monkeypatch):
    assert missing_docs(_tax_world(tmp_path, monkeypatch, tax=True, formation="1999-01-01").state()) == []


def test_neither_return_nor_extension_is_missing(tmp_path, monkeypatch):
    assert missing_docs(_tax_world(tmp_path, monkeypatch).state()) == ["MISSING_DOC:tax_return"]


# ---------------------------------------------------------------- product, guarantors, financials

def test_equipment_loan_without_a_quote_is_missing_it(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, application=app(product="Equipment Loan"))
    assert missing_docs(w.state()) == ["MISSING_DOC:equipment_quote"]
    w.add("equipment_quote")
    assert missing_docs(w.state()) == []


@pytest.mark.parametrize("product", ["Term Loan", "Working Capital Loan", None])
def test_other_or_unknown_product_needs_no_quote(tmp_path, monkeypatch, product):
    w = complete_world(tmp_path, monkeypatch, application=app(product=product))
    assert missing_docs(w.state()) == []


def test_one_id_per_guarantor(tmp_path, monkeypatch):
    two = app(guarantors=(Guarantor(name="A One"), Guarantor(name="B Two")))
    w = complete_world(tmp_path, monkeypatch, application=two)
    assert missing_docs(w.state()) == ["MISSING_DOC:guarantor_id"]
    w.add("guarantor_id", ParsedId(readable=False, via_ocr=True))  # unreadable image still counts as present
    assert missing_docs(w.state()) == []


def test_application_without_guarantors_still_needs_one_id(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, application=app(guarantors=()))
    w.docs = [d for d in w.docs if d.doc_class != "guarantor_id"]
    assert missing_docs(w.state()) == ["MISSING_DOC:guarantor_id"]


def test_financials_without_a_registered_parser_count_by_class_presence(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, fs=None)
    w.table[next(d.path for d in w.docs if d.doc_class == "financial_statements")] = None
    assert missing_docs(w.state()) == []


def test_financials_missing_a_balance_sheet_are_incomplete(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch, fs=ParsedFinancials(has_pl=True, has_balance_sheet=False))
    assert missing_docs(w.state()) == ["MISSING_DOC:financial_statements"]
    w.add("financial_statements", ParsedFinancials(has_pl=False, has_balance_sheet=True))  # the other half arrives
    assert missing_docs(w.state()) == []


def test_garbage_parsed_objects_never_raise(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch)
    for d in w.docs:
        w.table[d.path] = object()
    got = detect(w.state())
    assert all(ISSUE_RE.fullmatch(i) for i in got)


# ---------------------------------------------------------------- through run_sim on generated PDFs

# monthly deposits large enough that the generated application's stated revenue is not inflated
BIG_TXNS = ((2, "ACH CREDIT EXAMPLE CUSTOMER", 6_000_000), (10, "ACH DEBIT EXAMPLE LANDLORD", -1_000_000))


def _arrivals(tmp_path, rows):
    arr = tmp_path / "arrivals.jsonl"
    arr.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return arr


def _generated_deal(tmp_path, *, skip="2026-08", late_minutes=None):
    """Intake and Credit papers from the generators plus a statement per window month (one withheld)."""
    # the shared fixture ID shows a different house number than the application, which is a real ID_MISMATCH
    id_rows = [("Address", "20 Sample Lane, Testville, TX 75002") if isinstance(r, tuple) and r[0] == "Address" else r
               for r in ID_ROWS]
    st = make_state(tmp_path, parts={"id": id_rows}, folder="Deal")
    folder_dir = tmp_path / "Deal"
    rows = [{"path": d.path, "arrive_at": d.arrive_at.isoformat()} for d in st.docs]
    last = max(d.arrive_at for d in st.docs)
    opening = 1_000_000
    for i, m in enumerate(MONTHS):
        if m == skip and late_minutes is None:
            continue
        name = f"stmt_{m}.pdf"
        opening = write_statement(folder_dir, name, m, BIG_TXNS, opening=opening)
        at = last + timedelta(minutes=2 + i) if m != skip else last + timedelta(minutes=late_minutes)
        rows.append({"path": f"Deal/{name}", "arrive_at": at.isoformat()})
    return rows


def _spy(monkeypatch):
    calls: list[tuple[datetime, list[str]]] = []
    real = read_issues.detect_issues

    def spy(state, rules, now):
        out = real(state, rules, now)
        calls.append((now, list(out)))
        return out

    monkeypatch.setattr(read_issues, "detect_issues", spy)
    return calls


def test_run_sim_reports_a_missing_statement_exactly_at_quiet_expiry(tmp_path, monkeypatch):
    rows = _generated_deal(tmp_path)
    calls = _spy(monkeypatch)
    result = run_sim(tmp_path, _arrivals(tmp_path, rows), data_root=tmp_path)
    label = "MISSING_DOC:bank_statement_2026-08"
    last = max(parse_time(r["arrive_at"]) for r in rows)
    before = [out for now, out in calls if now < last + QUIET]
    at_expiry = [out for now, out in calls if now == last + QUIET]
    assert before and all(label not in out for out in before)
    assert at_expiry and label in at_expiry[0]
    assert label in calls[-1][1] and calls[-1][1] == at_expiry[0]  # the end tick sees the same set
    assert result.states["Deal"].issues == [label]
    assert result.states["Deal"].error is None


def test_run_sim_resolves_the_issue_when_the_statement_arrives_late(tmp_path, monkeypatch):
    rows = _generated_deal(tmp_path, late_minutes=120)
    result = run_sim(tmp_path, _arrivals(tmp_path, rows), data_root=tmp_path)
    st = result.states["Deal"]
    label = "MISSING_DOC:bank_statement_2026-08"
    assert st.issues == []
    assert label in st.resolved_issues
