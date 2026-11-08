"""Bank statement parser (PDF and CSV), page gaps and footing helpers (READ-06, READ-08)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prism.engine.state import Doc
from prism.read import docmodel, statements
from prism.read import text as read_text
from prism.read.classify import classify
from prism.read.docmodel import ParsedStatement, Txn
from tests.pdfgen import write_csv, write_pdf
from tests.statement_fixtures import (DEFAULT_TXNS, closing_of, statement_pages, totals, write_statement,
                                      write_statement_csv)

TZ = timezone(timedelta(hours=-6))
BASE = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)


def load(path: Path, minutes: int = 0) -> tuple[Doc, ParsedStatement | None]:
    read_text.clear_cache()
    docmodel.clear_cache()
    doc = Doc(path=f"Deal/{path.name}", name=path.name, arrive_at=BASE + timedelta(minutes=minutes), abs_path=path)
    classify(doc)
    return doc, docmodel.parsed(doc)


def pdf(tmp_path: Path, name: str = "s.pdf", period: str = "2026-05", **kw):
    write_statement(tmp_path, name, period, **kw)
    return load(tmp_path / name)


# ---------------------------------------------------------------- PDF parse

def test_pdf_summary_rows_and_page_markers_are_read(tmp_path):
    doc, ps = pdf(tmp_path, opening=1_000_000)
    credits, debits = totals(DEFAULT_TXNS)
    assert doc.doc_class == "bank_statement" and doc.period == "2026-05"
    assert ps.source == "pdf" and ps.period == "2026-05"
    assert (ps.opening_cents, ps.total_credits_cents, ps.total_debits_cents) == (1_000_000, credits, debits)
    assert ps.closing_cents == closing_of(1_000_000, DEFAULT_TXNS)
    assert ps.holder == "Example Holder LLC" and ps.account_last4 == "4321"
    assert ps.period_start == "2026-05-01" and ps.period_end == "2026-05-28"
    assert [t.amount_cents for t in ps.txns] == [c for _d, _s, c in DEFAULT_TXNS]
    assert ps.txns[0].date == "2026-05-02" and ps.txns[-1].balance_cents == ps.closing_cents
    assert ps.signs_verified is True
    assert ps.pages_found == (1,) and ps.pages_total == 1
    assert statements.is_complete(ps) and statements.footing_errors(ps) == []


def test_two_physical_pages_with_markers_are_complete(tmp_path):
    _doc, ps = pdf(tmp_path, n_pages=2)
    assert ps.pages_found == (1, 2) and ps.pages_total == 2
    assert len(ps.txns) == len(DEFAULT_TXNS) and statements.is_complete(ps)


def test_three_physical_pages_where_the_first_has_no_marker_are_complete(tmp_path):
    _doc, ps = pdf(tmp_path, n_pages=3, markers=[None, (1, 2), (2, 2)])
    assert ps.pages_found == (1, 2) and ps.pages_total == 2
    assert statements.missing_pages(ps) == () and len(ps.txns) == len(DEFAULT_TXNS)


def test_a_single_page_marked_one_of_two_is_missing_page_two(tmp_path):
    doc, ps = pdf(tmp_path, markers=[(1, 2)])
    assert doc.meta["page_markers"] == [[1, 2]]
    assert statements.missing_pages(ps) == (2,) and not statements.is_complete(ps)


def test_no_marker_at_all_means_nothing_is_missing(tmp_path):
    _doc, ps = pdf(tmp_path, markers=[None])
    assert ps.pages_total is None and statements.missing_pages(ps) == ()


def test_pdf_with_unparseable_row_is_not_sign_verified(tmp_path):
    pages = statement_pages("2026-05")
    row = pages[0][-3]  # a transaction row; drop its balance so only one money cell is left
    assert isinstance(row, list)
    pages[0][-3] = row[:3]
    p = write_pdf(tmp_path / "s.pdf", pages)
    _doc, ps = load(p)
    assert ps.signs_verified is False and len(ps.txns) == len(DEFAULT_TXNS) - 1
    assert statements.footing_errors(ps) == []  # untrusted: never reported as tampering


def test_row_whose_amount_disagrees_with_the_balance_is_a_footing_failure(tmp_path):
    pages = statement_pages("2026-05")
    row = pages[0][-4]
    assert isinstance(row, list)
    cells = list(row)
    cells[2] = (cells[2][0], cells[2][1].replace("$", "$9"))  # edit the printed amount only
    cells[3] = (cells[3][0], cells[3][1].replace("$", "$9"))
    pages[0][-4] = cells
    _doc, ps = load(write_pdf(tmp_path / "s.pdf", pages))
    assert ps.signs_verified is True  # every row parsed; the disagreement is a finding, not an ambiguity
    assert "row_amount" in statements.footing_errors(ps)


def test_pdf_with_summary_but_unparsed_rows_is_not_row_verified(tmp_path):
    pages = statement_pages("2026-05")
    for i, ln in enumerate(pages[0]):  # drop the year from every row date: 05/03/2026 -> 05/03
        if isinstance(ln, list) and ln and ln[0][1].count("/") == 2:
            pages[0][i] = [(ln[0][0], ln[0][1].rsplit("/", 1)[0])] + list(ln[1:])
    _doc, ps = load(write_pdf(tmp_path / "s.pdf", pages))
    assert ps.txns == () and ps.opening_cents is not None
    assert ps.signs_verified is not True
    assert statements.footing_errors(ps) == []


def test_pdf_without_summary_or_rows_is_not_a_statement(tmp_path):
    p = write_pdf(tmp_path / "s.pdf", [["Business Checking Statement", "Opening balance", "nothing else here"]])
    _doc, ps = load(p)
    assert ps is None


@pytest.mark.parametrize("size", [0, 17])
def test_garbage_files_give_none_not_an_exception(tmp_path, size):
    p = tmp_path / "g.dat"
    p.write_bytes(b"\x00" * size)
    doc = Doc(path="Deal/g.dat", name="g.dat", arrive_at=BASE, abs_path=p, doc_class="bank_statement")
    read_text.clear_cache()
    docmodel.clear_cache()
    assert docmodel.parsed(doc) is None


def test_very_long_lines_and_many_rows_stay_bounded(tmp_path):
    long_line = "05/01/2026 " + "X" * 5000 + " $1.00 $2.00"
    many = [f"05/01/2026 ROW {i} $1.00 $2.00" for i in range(3000)]
    p = write_pdf(tmp_path / "s.pdf", [[long_line] + many[:55]] * 1)
    _doc, ps = load(p)
    assert ps is None or len(ps.txns) <= 2000  # no hang, no crash


def test_table_fallback_used_only_when_no_line_rows_parse(tmp_path, monkeypatch):
    pages = [[
        "Business Checking Statement",
        [(72, "Account holder"), (300, "Example Holder LLC")],
        [(72, "Statement period"), (300, "2026-05-01 to 2026-05-31")],
        [(72, "Opening balance"), (300, "$100.00")],
        [(72, "Closing balance"), (300, "$130.00")],
        "Page 1 of 1",
    ]]
    p = write_pdf(tmp_path / "s.pdf", pages)
    table = (("Date", "Description", "Credit", "Debit", "Balance"),
             ("05/03/2026", "ACH CREDIT X", "$50.00", "", "$150.00"),
             ("05/04/2026", "ACH DEBIT Y", "", "$20.00", "$130.00"))
    monkeypatch.setattr(statements, "pdf_tables", lambda path: (table,))
    _doc, ps = load(p)
    assert [t.amount_cents for t in ps.txns] == [5000, -2000] and ps.signs_verified is True


# ---------------------------------------------------------------- CSV parse

def test_csv_statement_is_parsed_with_implied_opening(tmp_path):
    write_statement_csv(tmp_path, "e.csv", "2026-05", opening=500_000)
    doc, ps = load(tmp_path / "e.csv")
    assert doc.doc_class == "bank_statement" and doc.period == "2026-05"
    assert ps.source == "csv" and ps.period == "2026-05"
    assert ps.opening_cents == 500_000 and ps.closing_cents == closing_of(500_000, DEFAULT_TXNS)
    assert ps.total_credits_cents is None and ps.total_debits_cents is None
    assert [t.amount_cents for t in ps.txns] == [c for _d, _s, c in DEFAULT_TXNS]
    assert ps.txns[0].date == "2026-05-02"
    assert ps.holder == "Example Holder LLC" and ps.account_last4 == "7720" and ps.bank == "Example Test Bank"
    assert ps.signs_verified is True and ps.pages_total is None
    assert statements.footing_errors(ps) == []


def test_csv_running_balance_break_is_a_footing_error(tmp_path):
    write_statement_csv(tmp_path, "e.csv", "2026-05", bad_balance_at=2)
    _doc, ps = load(tmp_path / "e.csv")
    assert statements.footing_errors(ps) == ["running_balance"]


def test_csv_in_newest_first_order_is_read_chronologically(tmp_path):
    rows = [["Posted Date", "Details", "Amount", "Running Bal"],
            ["5/9/26", "B", "-10.00", "140.00"], ["5/3/26", "A", "50.00", "150.00"]]
    rows = [rows[0], rows[1], rows[2]]
    write_csv(tmp_path / "e.csv", ["Example Bank - acct ...1111 - Example Holder"] + rows)
    _doc, ps = load(tmp_path / "e.csv")
    assert [t.amount_cents for t in ps.txns] == [5000, -1000] and statements.footing_errors(ps) == []
    assert ps.opening_cents == 10000 and ps.closing_cents == 14000


def test_csv_with_a_bad_row_is_untrusted(tmp_path):
    rows = [["Posted Date", "Details", "Amount", "Running Bal"],
            ["5/3/26", "A", "50.00", "150.00"], ["5/9/26", "B", "oops", "140.00"], ["5/10/26", "C", "5.00", "145.00"]]
    write_csv(tmp_path / "e.csv", rows)
    _doc, ps = load(tmp_path / "e.csv")
    assert ps.signs_verified is False and statements.footing_errors(ps) == []


# ---------------------------------------------------------------- pure helpers

def ps_of(**kw) -> ParsedStatement:
    base = dict(source="pdf", period="2026-05", opening_cents=1000, total_credits_cents=500, total_debits_cents=200,
                closing_cents=1300, signs_verified=True,
                txns=(Txn(amount_cents=500, balance_cents=1500), Txn(amount_cents=-200, balance_cents=1300)))
    base.update(kw)
    return ParsedStatement(**base)


def test_footing_clean_and_each_failure_reason():
    assert statements.footing_errors(ps_of()) == []
    assert statements.footing_errors(ps_of(total_credits_cents=501, closing_cents=1301)) == ["credits_sum"]
    assert statements.footing_errors(ps_of(total_debits_cents=199, closing_cents=1301)) == ["debits_sum"]
    assert statements.footing_errors(ps_of(closing_cents=1301)) == ["summary_closing"]
    assert statements.footing_errors(ps_of(total_credits_cents=1, total_debits_cents=1, closing_cents=7)) == [
        "credits_sum", "debits_sum", "summary_closing"]


def test_footing_is_to_the_cent_and_skips_untrusted_or_incomplete():
    assert statements.footing_errors(ps_of(closing_cents=1300 + 1)) != []
    assert statements.footing_errors(ps_of(closing_cents=1301, signs_verified=False)) == []
    assert statements.footing_errors(ps_of(closing_cents=1301, signs_verified=None)) == []
    assert statements.footing_errors(ps_of(closing_cents=1301, pages_total=2, pages_found=(1,))) == []


def test_footing_with_missing_summary_values_checks_only_what_is_printed():
    assert statements.footing_errors(ps_of(total_credits_cents=None, total_debits_cents=None)) == []
    assert statements.footing_errors(ps_of(opening_cents=None)) == []


def test_missing_pages_reads_the_markers_only():
    assert statements.missing_pages(ps_of(pages_found=(1, 3), pages_total=3)) == (2,)
    assert statements.missing_pages(ps_of(pages_found=(), pages_total=2)) == (1, 2)
    assert statements.missing_pages(ps_of(pages_found=(1,), pages_total=None)) == ()
    assert statements.missing_pages(ps_of(pages_found=(1,), pages_total=1)) == ()


def test_marker_gap_union_across_copies():
    assert statements.marker_gap([[(1, 2)], [(2, 2)]]) == ()
    assert statements.marker_gap([[(1, 3)], [(1, 3), (2, 3)]]) == (3,)
    assert statements.marker_gap([[(1, 2)], []]) == ()           # a copy with no markers is no evidence of a gap
    assert statements.marker_gap([[(1, 2)]]) == (2,)
    assert statements.marker_gap([]) == ()
