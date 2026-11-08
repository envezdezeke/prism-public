"""Stage machine tracer: first document to a routed credit_decision, on synthetic rules."""
from __future__ import annotations

import pytest
from test_move_support import GOOD_PROFILE, add_doc, at, kinds, make_rules, make_state, persona, tick

from prism.engine.config import Rules
from prism.move import stage as move_stage


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def test_tracer_clean_deal_reaches_credit_with_one_routed_request(rules):
    st = make_state(profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-03T12:00")  # one call a day past the arrival, inside the 48h Credit limit
    assert st.error is None
    assert st.stage == "Credit"
    intake, credit = st.stage_log
    assert intake.stage == "Intake" and intake.entered_at == "2026-03-02T09:00-06:00"
    assert intake.exited_at == "2026-03-02T09:30-06:00"  # after the 30 minute quiet rule
    assert intake.decision == "advanced" and intake.why
    assert credit.stage == "Credit" and credit.entered_at == intake.exited_at and credit.exited_at is None
    assert kinds(st) == [("credit_decision", "cal.credit")]
    msg = st.human_requests[0].message
    for needle in ("Acme Company LLC", "Acme Bakery", "Rita Alpha", "$100,000", "Term Loan", "24 months",
                   "working capital", "approve, decline or request info"):
        assert needle in msg
    assert "{" not in msg and "None" not in msg
    assert st.human_requests[0].at == "2026-03-02T09:30-06:00"


def test_empty_rules_are_a_no_op(tmp_path):
    st = make_state()
    move_stage.step_deal(st, Rules(), at("2026-03-02T12:00"))
    assert st.error is None and st.stage_log == [] and st.human_requests == [] and st.alerts == []


def test_not_started_without_documents(rules):
    st = make_state()
    st.docs.clear()
    tick(st, rules, "2026-03-02T12:00")
    assert st.stage_log == []


def test_calling_twice_at_the_same_time_changes_nothing(rules):
    st = make_state(profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-02T11:00")
        before = ([e.model_dump() for e in st.stage_log], [r.model_dump() for r in st.human_requests])
        tick(st, rules, "2026-03-02T11:00")
    after = ([e.model_dump() for e in st.stage_log], [r.model_dump() for r in st.human_requests])
    assert before == after and len(before[1]) == 1


def test_nothing_is_judged_before_thirty_quiet_minutes(rules):
    st = make_state(profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-02T09:29")
        assert st.stage == "Intake" and len(st.stage_log) == 1 and st.stage_log[0].exited_at is None
        assert st.human_requests == []
        tick(st, rules, "2026-03-02T09:30")
    assert st.stage == "Credit"


def test_missing_intake_document_keeps_deal_in_intake_and_asks_borrower(rules):
    st = make_state(issues=["MISSING_DOC:ein_letter"], profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-03T12:00")
    assert st.stage == "Intake"
    assert kinds(st) == [("info_request", "borrower")]
    msg = st.human_requests[0].message
    assert "IRS EIN letter" in msg and msg.startswith("Thank you for your application for $100,000 Term Loan.")
    assert st.stage_log[0].exited_at is None


def test_missing_credit_document_blocks_the_credit_decision(rules):
    st = make_state(issues=["MISSING_DOC:bank_statement_2026-01", "MISSING_PAGE:financial_statements"],
                    profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-03T12:00")
    assert st.stage == "Credit"
    # everything missing is asked once, in one list (J11), and never a credit_decision
    assert kinds(st) == [("info_request", "borrower")]
    msg = st.human_requests[0].message
    assert "bank statement for January 2026" in msg and "financial statements" in msg
    assert msg.count("\n- ") == 2


def test_mismatch_gets_an_immediate_request_before_the_quiet_period(rules):
    st = make_state(issues=["MISMATCH:amount_requested"],
                    profile={"mismatch_evidence": {"amount_requested": {
                        "application": "$250,000", "document": "request letter", "value": "$25,000"}}})
    with persona("never"):
        tick(st, rules, "2026-03-02T09:10")
    assert kinds(st) == [("info_request", "borrower")]
    assert st.human_requests[0].at == "2026-03-02T09:10-06:00"
    msg = st.human_requests[0].message
    assert "your application shows $250,000, but your request letter shows $25,000" in msg
    assert st.stage == "Intake"
    # J15: the amount mismatch keeps the deal in Intake even after the quiet period
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00")
    assert st.stage == "Intake" and kinds(st) == [("info_request", "borrower")]


def test_late_arrival_clears_the_issue_and_credit_decision_goes_out_after(rules):
    st = make_state(issues=["MISSING_DOC:bank_statement_2026-01"], profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-02T10:00")
        assert kinds(st) == [("info_request", "borrower")]
        st.issues = []
        add_doc(st, "2026-03-02T12:00")
        tick(st, rules, "2026-03-02T12:00")
        assert kinds(st) == [("info_request", "borrower")]  # not yet: folder must be quiet again
        tick(st, rules, "2026-03-02T12:30")
    assert kinds(st) == [("info_request", "borrower"), ("credit_decision", "cal.credit")]
    assert st.human_requests[1].at == "2026-03-02T12:30-06:00"
    assert st.human_requests[0].at < st.human_requests[1].at


def test_missing_required_fact_waits_until_it_appears(rules):
    st = make_state(legal_name=None, profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00")
        assert st.stage == "Intake"
        from test_move_support import fact
        st.facts["legal_name"] = fact("Acme Company LLC")
        tick(st, rules, "2026-03-02T13:00", "2026-03-02T14:00")
    assert st.stage == "Credit"


def test_credit_request_covering_sentence_when_officer_out(rules):
    st = make_state("Bolt Inc", arrive="2026-03-04T09:00", profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-04T12:00")
    assert kinds(st) == [("credit_decision", "cal.credit")]
    assert "You are covering for Cora Credit, who is out of office until Fri 03/06." in st.human_requests[0].message


def test_braces_in_a_legal_name_pass_through(rules):
    st = make_state(legal_name="Brace {0} {name} Co", profile=GOOD_PROFILE)
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00")
    assert "Brace {0} {name} Co" in st.human_requests[0].message
