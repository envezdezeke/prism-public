"""MISMATCH rules (J13), ID disagreements and the evidence hook (READ-07). Parsers for the compared documents
are faked through the docmodel registry; real-data confirmation is a separate (tuning-only) test."""
from __future__ import annotations

from datetime import timedelta

import pytest

from prism.engine.config import Rules
from prism.read import compare, requirements
from prism.read import issues as read_issues
from prism.read.docmodel import (Guarantor, Parsed7004, ParsedApplication, ParsedCert, ParsedFinancials,
                                 ParsedId, ParsedIrs, ParsedLetter, ParsedStatement, ParsedTax)
from tests.test_read_issues import World, app, complete_world

RULES = Rules()


def world_with(tmp_path, monkeypatch, application=None, **docs):
    """A clean deal (nothing missing) whose named single documents are replaced by the given parsed objects."""
    w = complete_world(tmp_path, monkeypatch, application=application or app())
    for cls, parsed in docs.items():
        cls = {"irs": "ein_letter", "cert": "certificate_of_formation", "tax": "tax_return", "fs": "financial_statements",
               "ids": "guarantor_id"}.get(cls, cls)
        have = [d for d in w.docs if d.doc_class == cls]
        if have:
            w.table[have[-1].path] = parsed
        else:
            w.add(cls, parsed)
    return w


def mismatches(w):
    return {k: v for k, v in compare.find_mismatches(w.state(), RULES).items()}


def all_issues(w):
    st = w.state()
    return read_issues.detect_issues(st, RULES, st.last_arrival_at + timedelta(minutes=45))


# ---------------------------------------------------------------- legal name

def test_name_suffix_punctuation_and_ampersand_are_tolerated(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(legal_name="Acme, L.L.C."),
                   irs=ParsedIrs(name="ACME LLC"), cert=ParsedCert(entity_name="Acme Llc"),
                   tax=ParsedTax(name="acme"))
    assert mismatches(w) == {}
    w2 = world_with(sub(tmp_path, "x"), monkeypatch,
                    application=app(legal_name="Smith & Sons Inc"), irs=ParsedIrs(name="Smith and Sons, Incorporated"))
    assert mismatches(w2) == {}


@pytest.mark.parametrize("cls,parsed", [
    ("irs", ParsedIrs(name="Different Company LLC")),
    ("cert", ParsedCert(entity_name="Different Company LLC")),
    ("tax", ParsedTax(name="Different Company LLC")),
])
def test_a_different_base_name_is_a_legal_name_mismatch(tmp_path, monkeypatch, cls, parsed):
    w = world_with(tmp_path, monkeypatch, application=app(legal_name="Acme LLC"), **{cls: parsed})
    got = mismatches(w)
    assert list(got) == ["legal_name"]
    assert got["legal_name"].application == "Acme LLC" and got["legal_name"].value == "Different Company LLC"
    assert "MISMATCH:legal_name" in all_issues(w)


def test_the_latest_document_is_the_one_compared(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(legal_name="Acme LLC"), irs=ParsedIrs(name="Wrong Name Inc"))
    w.add("ein_letter", ParsedIrs(name="Acme LLC"), minutes=500)  # a corrected paper arrives later
    assert mismatches(w) == {}


def test_missing_names_are_skipped(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(legal_name=None), irs=ParsedIrs(name="Anything"))
    assert mismatches(w) == {}
    w2 = world_with(sub(tmp_path, "b"), monkeypatch, application=app(legal_name="Acme LLC"), irs=ParsedIrs(name=None))
    assert mismatches(w2) == {}


def sub(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return p


# ---------------------------------------------------------------- EIN

def test_ein_differing_by_a_digit_or_transposition_is_a_mismatch(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(ein="98-7654321"), irs=ParsedIrs(ein="98-7654312"))
    got = mismatches(w)
    assert list(got) == ["ein"] and got["ein"].value == "98-7654312"
    w2 = world_with(sub(tmp_path, "b"), monkeypatch, application=app(ein="98-7654321"), irs=ParsedIrs(ein="98-7654322"))
    assert list(mismatches(w2)) == ["ein"]


def test_ein_is_compared_with_the_tax_return_and_the_7004(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(ein="98-7654321"), irs=ParsedIrs(ein="98-7654321"),
                   tax=ParsedTax(ein="11-1111111"))
    assert list(mismatches(w)) == ["ein"]
    w2 = world_with(sub(tmp_path, "b"), monkeypatch, application=app(ein="98-7654321"),
                    irs=ParsedIrs(ein="98-7654321"), tax=ParsedTax(ein="98-7654321"))
    w2.add("form_7004", Parsed7004(ein="22-2222222"))
    assert list(mismatches(w2)) == ["ein"]


def test_equal_or_unparseable_eins_raise_nothing(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(ein="987654321"), irs=ParsedIrs(ein="98-7654321"))
    assert mismatches(w) == {}
    w2 = world_with(sub(tmp_path, "b"), monkeypatch, application=app(ein="98-7654321"), irs=ParsedIrs(ein="9-76"))
    assert mismatches(w2) == {}
    w3 = world_with(sub(tmp_path, "c"), monkeypatch, application=app(ein="98-7654321"), irs=ParsedIrs(ein=None))
    assert mismatches(w3) == {}


# ---------------------------------------------------------------- amount requested

def test_amount_disagreement_to_the_cent_is_a_mismatch(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(amount_cents=25_000_000),
                   request_letter=ParsedLetter(amount_cents=20_500_000))
    got = mismatches(w)
    assert list(got) == ["amount_requested"]
    assert got["amount_requested"].application == "$250,000.00" and got["amount_requested"].value == "$205,000.00"
    w2 = world_with(sub(tmp_path, "b"), monkeypatch, application=app(amount_cents=12_000_000),
                    request_letter=ParsedLetter(amount_cents=12_000_000))
    assert mismatches(w2) == {}
    w3 = world_with(sub(tmp_path, "c"), monkeypatch, application=app(amount_cents=12_000_000),
                    request_letter=ParsedLetter(amount_cents=12_000_001))
    assert list(mismatches(w3)) == ["amount_requested"]


def test_a_letter_without_an_amount_is_skipped(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(amount_cents=12_000_000), request_letter=ParsedLetter())
    assert mismatches(w) == {}


# ---------------------------------------------------------------- annual revenue

def rev_world(tmp_path, monkeypatch, *, stated=100_000_000, receipts=100_000_000, year=2025, formation="2015-04-02",
              fs_rev=None, fs_year=2025, range_=None, cert=None):
    return world_with(tmp_path, monkeypatch, **({"cert": cert} if cert is not None else {}),
                      application=app(revenue_cents=None if range_ else stated, revenue_range_cents=range_,
                                      formation_date=formation),
                      tax=ParsedTax(gross_receipts_cents=receipts, tax_year=year),
                      fs=ParsedFinancials(has_pl=True, has_balance_sheet=True, revenue_fy_cents=fs_rev, fy_year=fs_year))


def test_revenue_beyond_one_percent_for_the_same_year_is_a_mismatch(tmp_path, monkeypatch):
    assert list(mismatches(rev_world(tmp_path, monkeypatch, receipts=102_000_000))) == ["annual_revenue"]
    ok = rev_world(sub(tmp_path, "b"), monkeypatch, receipts=100_500_000)
    assert mismatches(ok) == {}
    edge = rev_world(sub(tmp_path, "c"), monkeypatch, receipts=101_000_000)  # exactly one percent: not more than
    assert mismatches(edge) == {}


def test_revenue_is_compared_with_the_financial_statements_too(tmp_path, monkeypatch):
    w = rev_world(tmp_path, monkeypatch, fs_rev=130_000_000)
    got = mismatches(w)
    assert list(got) == ["annual_revenue"] and got["annual_revenue"].document == "financial statements"


def test_revenue_never_compares_ranges_other_years_or_partial_years(tmp_path, monkeypatch):
    assert mismatches(rev_world(tmp_path, monkeypatch, range_=(40_000_000, 50_000_000), receipts=1)) == {}
    assert mismatches(rev_world(sub(tmp_path, "b"), monkeypatch, receipts=999_999_999, year=2024, fs_year=2024,
                                fs_rev=5)) == {}
    assert mismatches(rev_world(sub(tmp_path, "c"), monkeypatch, receipts=47_040_000, formation="2025-06-23")) == {}
    assert mismatches(rev_world(sub(tmp_path, "d"), monkeypatch, receipts=47_040_000, formation=None,
                                cert=ParsedCert())) == {}


# ---------------------------------------------------------------- bank balance

def bal_world(tmp_path, monkeypatch, *, cash=1_000_000, closing=1_000_000, end="2026-10-31", date="2026-10-31",
              markers=()):
    w = world_with(tmp_path, monkeypatch,
                   fs=ParsedFinancials(has_pl=True, has_balance_sheet=True, balance_date=date, cash_cents=cash))
    w.add("bank_statement", ParsedStatement(period="2026-10", period_end=end, closing_cents=closing, opening_cents=1,
                                            signs_verified=True), period="2026-10", markers=markers)
    return w


def test_balance_sheet_cash_must_match_the_statement_within_a_dollar(tmp_path, monkeypatch):
    assert mismatches(bal_world(tmp_path, monkeypatch, closing=1_000_100)) == {}
    got = mismatches(bal_world(sub(tmp_path, "b"), monkeypatch, closing=1_000_101))
    assert list(got) == ["bank_balance"] and got["bank_balance"].document == "bank statement"


def test_balance_without_a_statement_ending_that_day_is_skipped(tmp_path, monkeypatch):
    assert mismatches(bal_world(tmp_path, monkeypatch, closing=5, end="2026-10-30")) == {}
    assert mismatches(bal_world(sub(tmp_path, "b"), monkeypatch, closing=5, date=None)) == {}


# ---------------------------------------------------------------- evidence hook and isolation

def test_evidence_matches_the_raised_issues_and_uses_plain_labels(tmp_path, monkeypatch):
    w = world_with(tmp_path, monkeypatch, application=app(legal_name="Acme LLC", ein="98-7654321"),
                   irs=ParsedIrs(name="Acme LLC", ein="98-7654312"))
    st = w.state()
    ev = read_issues.mismatch_evidence(st, RULES)
    assert ev == {"ein": {"application": "98-7654321", "document": "IRS EIN letter", "value": "98-7654312"}}
    assert [i for i in all_issues(w) if i.startswith("MISMATCH")] == ["MISMATCH:ein"]


def test_no_mismatch_means_empty_evidence_and_never_raises(tmp_path, monkeypatch):
    w = complete_world(tmp_path, monkeypatch)
    assert read_issues.mismatch_evidence(w.state(), RULES) == {}
    for d in w.docs:
        w.table[d.path] = object()
    assert read_issues.mismatch_evidence(w.state(), RULES) == {}
    assert compare.find_mismatches(w.state(), RULES) == {}


# ---------------------------------------------------------------- ID disagreements

def guarantor(**kw):
    base = dict(name="Robert Smith", dob="1980-01-02", address="20 Sample Lane, Testville, TX 75002")
    base.update(kw)
    return Guarantor(**base)


def id_world(tmp_path, monkeypatch, id_, *, guarantors=None):
    return world_with(tmp_path, monkeypatch, application=app(guarantors=tuple(guarantors or [guarantor()])), ids=id_)


def good_id(**kw):
    base = dict(name="Robert Smith", dob="1980-01-02", address="20 Sample Lane, Testville, TX 75002", readable=True,
                via_ocr=False)
    base.update(kw)
    return ParsedId(**base)


def id_fields(w):
    return [r.fields for r in compare.id_disagreements(w.state())]


def test_matching_id_has_no_disagreement(tmp_path, monkeypatch):
    assert id_fields(id_world(tmp_path, monkeypatch, good_id())) == [()]


@pytest.mark.parametrize("name", ["Bob Smith", "Robert J Smith", "SMITH ROBERT", "robert smith"])
def test_nickname_middle_name_and_case_differences_are_not_disagreements(tmp_path, monkeypatch, name):
    assert id_fields(id_world(tmp_path, monkeypatch, good_id(name=name))) == [()]


def test_a_different_person_name_dob_or_address_is_a_disagreement(tmp_path, monkeypatch):
    assert id_fields(id_world(tmp_path, monkeypatch, good_id(name="Walter Jones"))) == [("name",)]
    assert id_fields(id_world(sub(tmp_path, "b"), monkeypatch, good_id(dob="1981-01-02"))) == [("dob",)]
    assert id_fields(id_world(sub(tmp_path, "c"), monkeypatch, good_id(address="999 Other Road, Elsewhere, TX 75001"))) == [
        ("address",)]


def test_address_abbreviation_and_suite_tolerance(tmp_path, monkeypatch):
    g = guarantor(address="20 Sample Ln Ste 4, Testville, TX 75002")
    w = id_world(tmp_path, monkeypatch, good_id(address="20 Sample Lane #4, Testville, TX 75002-1234"), guarantors=[g])
    assert id_fields(w) == [()]


def test_non_ocr_id_raises_on_one_field_and_ocr_needs_two(tmp_path, monkeypatch):
    w = id_world(tmp_path, monkeypatch, good_id(name="Walter Jones"))
    assert "OLD_TRICK:ID_MISMATCH" in all_issues(w)
    ocr_one = id_world(sub(tmp_path, "b"), monkeypatch, good_id(name="WALTERJONES", via_ocr=True))
    assert id_fields(ocr_one) == [("name",)] and "OLD_TRICK:ID_MISMATCH" not in all_issues(ocr_one)
    ocr_two = id_world(sub(tmp_path, "c"), monkeypatch, good_id(name="WALTERJONES", dob="1999-09-09", via_ocr=True))
    assert "OLD_TRICK:ID_MISMATCH" in all_issues(ocr_two)


def test_ocr_text_with_no_spaces_still_matches(tmp_path, monkeypatch):
    ocr = good_id(name="ROBERTSMITH", address="20SAMPLELANE TESTVILLE,TX75002", via_ocr=True)
    assert id_fields(id_world(tmp_path, monkeypatch, ocr)) == [()]
    ocr_nick = good_id(name="BOBBYJSMITH", address="20SAMPLELANE TESTVILLE,TX75002", via_ocr=True)
    assert id_fields(id_world(sub(tmp_path, "b"), monkeypatch, ocr_nick)) == [()]


@pytest.mark.parametrize("readable", [False])
def test_unreadable_ids_are_unverified_not_mismatched(tmp_path, monkeypatch, readable):
    w = id_world(tmp_path, monkeypatch, ParsedId(name="Walter Jones", dob="1999-09-09", readable=readable, via_ocr=True))
    assert id_fields(w) == []
    assert "OLD_TRICK:ID_MISMATCH" not in all_issues(w)


def test_each_id_is_matched_to_the_closest_guarantor(tmp_path, monkeypatch):
    other = guarantor(name="Maria Gomez", dob="1975-05-05", address="1 Other Way, Testville, TX 75003")
    w = id_world(tmp_path, monkeypatch, good_id(name="Maria Gomez", dob="1975-05-05", address="1 Other Way, Testville, TX 75003"),
                 guarantors=[guarantor(), other])
    assert id_fields(w) == [()]


def test_id_disagreement_also_raises_guarantor_mismatches_only_when_flagged(tmp_path, monkeypatch):
    w = id_world(tmp_path, monkeypatch, good_id(name="Walter Jones", dob="1999-09-09"))
    assert not [i for i in all_issues(w) if i.startswith("MISMATCH:guarantor")]
    monkeypatch.setattr(compare, "_id_flag", lambda: True)
    got = [i for i in all_issues(w) if i.startswith("MISMATCH:guarantor")]
    assert got == ["MISMATCH:guarantor_dob", "MISMATCH:guarantor_name"]
    ev = read_issues.mismatch_evidence(w.state(), RULES)
    assert "1999" not in str(ev) and "1980" not in str(ev)  # a date of birth is never put in evidence
