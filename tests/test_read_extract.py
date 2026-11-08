"""Facts with the D-15 rules: IRS-letter EIN, application first, fallbacks only for missing fields (03-02 Task 2)."""
from __future__ import annotations

import pytest

from prism.engine.state import DealState
from prism.read import docmodel
from prism.read.docmodel import ParsedId
from prism.read.extract import extract_entities, extract_facts, extract_features
from tests.read_fixtures import F7004_ROWS, LETTER_LINES, make_state

APP_EIN = "98-7654321"


def _facts(tmp_path, parts=None, **kw):
    state = make_state(tmp_path, parts, **kw)
    return extract_facts(state, None)


def test_irs_letter_ein_wins_over_the_application(tmp_path):
    facts = _facts(tmp_path, app_over={"ein": "98-7654322"})
    assert facts["ein"].value == APP_EIN
    assert facts["ein"].source_doc.endswith("c_irs.pdf") and facts["ein"].confidence == pytest.approx(0.95)


def test_application_ein_without_a_letter(tmp_path):
    facts = _facts(tmp_path, {"irs": None}, app_over={"ein": "98-7654322"})
    assert facts["ein"].value == "98-7654322" and facts["ein"].source_doc.endswith("a_app.pdf")


def test_ein_falls_back_to_tax_return_then_form_7004(tmp_path):
    facts = _facts(tmp_path, {"irs": None}, app_over={"ein": ""})
    assert facts["ein"].value == APP_EIN and facts["ein"].source_doc.endswith("e_tax.pdf")
    assert facts["ein"].confidence == pytest.approx(0.5)
    facts = _facts(tmp_path, {"irs": None, "tax": None, "f7004": F7004_ROWS}, app_over={"ein": ""})
    assert facts["ein"].value == APP_EIN and facts["ein"].source_doc.endswith("e_7004.pdf")
    assert "ein" not in _facts(tmp_path, {"irs": None, "tax": None}, app_over={"ein": ""})


def test_amount_and_guarantor_name_stay_with_the_application(tmp_path):
    other_letter = ["Re: Loan request", "We are requesting $99,000.00 to buy things."]
    id_rows = ["Texas Driver License — Copy", ("Name", "Someone Else"), ("Date of birth", "1980-01-02"),
               ("DL number", "01234567"), ("Expires", "2029-04-11")]
    facts = _facts(tmp_path, {"letter": other_letter, "id": id_rows})
    assert facts["amount_requested"].value == 125000 and facts["amount_requested"].source_doc.endswith("a_app.pdf")
    assert facts["guarantor_name"].value == "Ada Quillfeather"
    assert facts["legal_name"].value == "Nimbus Orchard Supply LLC"


def test_legal_name_falls_back_to_certificate_then_irs_letter(tmp_path):
    facts = _facts(tmp_path, app_over={"legal": ""})
    assert facts["legal_name"].value == "Nimbus Orchard Supply LLC"
    assert facts["legal_name"].source_doc.endswith("d_cert.pdf") and facts["legal_name"].confidence == pytest.approx(0.5)
    facts = _facts(tmp_path, {"cert": None}, app_over={"legal": ""})
    assert facts["legal_name"].value == "NIMBUS ORCHARD SUPPLY LLC" and facts["legal_name"].source_doc.endswith("c_irs.pdf")


def test_declared_none_is_not_filled_from_another_document(tmp_path):
    facts = _facts(tmp_path, app_over={"legal": "N/A"})
    assert "legal_name" not in facts


def test_guarantor_name_falls_back_to_a_readable_text_id(tmp_path):
    facts = _facts(tmp_path, app_over={"guarantor": ""})
    assert facts["guarantor_name"].value == "Ada Quillfeather"
    assert facts["guarantor_name"].source_doc.endswith("b_id.pdf") and facts["guarantor_name"].confidence == pytest.approx(0.5)
    assert "guarantor_name" not in _facts(tmp_path, {"id": None}, app_over={"guarantor": ""})


def test_ocr_id_name_is_never_a_fact(tmp_path, monkeypatch):
    state = make_state(tmp_path, app_over={"guarantor": ""})
    real = docmodel.parsed_all

    def fake(st, cls):
        if cls == "guarantor_id":
            doc = next(d for d in st.docs if d.doc_class == "guarantor_id")
            return [(doc, ParsedId(name="ADAQUILLFEATHER", dob="1980-01-02", dl_number="01234567", readable=True, via_ocr=True))]
        return real(st, cls)

    monkeypatch.setattr(docmodel, "parsed_all", fake)
    assert "guarantor_name" not in extract_facts(state, None)


def test_no_application_gives_no_facts_and_never_raises(tmp_path, monkeypatch):
    assert extract_facts(make_state(tmp_path, {"application": None}), None) == {}
    state = make_state(tmp_path)
    monkeypatch.setattr(docmodel, "parsed_all", lambda *_a: (_ for _ in ()).throw(RuntimeError("x")))
    assert extract_facts(state, None) == {}
    assert extract_entities(state, None) == []
    assert extract_features(state, None) == {}


def test_all_nine_facts_still_come_from_the_application(tmp_path):
    facts = _facts(tmp_path)
    assert set(facts) >= {"legal_name", "ein", "amount_requested", "product", "term_months", "guarantor_name",
                          "annual_revenue_stated", "purpose_category"}
    assert facts["term_months"].value == 36 and facts["product"].value == "Term Loan"


def test_unknown_values_stay_absent(tmp_path):
    state = DealState(folder="Empty")
    assert extract_facts(state, None) == {}
    assert extract_entities(state, None) == []
