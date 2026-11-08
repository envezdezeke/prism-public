"""The six-stage machine end to end: order, waiting, replies, personas, business hours."""
from __future__ import annotations

import dataclasses

import pytest
from test_move_support import (
    GOOD_PROFILE, add_doc, at, fact, kinds, make_rules, make_state, persona, tick,
)

from prism.move import inbox
from prism.move.runtime import get_runtime
from prism.move.settings import get_config, use_config
from prism.move.timeutil import business_add, business_sub, next_open

PIPE = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]
FAR = "2026-03-20T12:00"


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def good(folder="Acme Co", **kw):
    st = make_state(folder, profile=dict(GOOD_PROFILE, **kw.pop("profile", {})), **kw)
    st.facts["annual_revenue_stated"] = fact(700000)
    return st


def answers(**override):
    cfg = get_config()
    r = dataclasses.replace(cfg.responder, answers={**cfg.responder.answers, **override})
    return use_config(dataclasses.replace(cfg, responder=r))


def dump(st):
    return [e.model_dump() for e in st.stage_log], [r.model_dump() for r in st.human_requests]


# ------------------------------------------------------------ order


def test_clean_deal_with_fast_persona_closes_in_order(rules):
    st = good()
    with persona("fast"):
        tick(st, rules, FAR)
    assert st.error is None
    assert [e.stage for e in st.stage_log] == PIPE
    assert st.stage == "Closed"
    prev = None
    for e in st.stage_log:
        assert e.exited_at and e.decision and e.why
        if prev is not None:
            assert e.entered_at == prev.exited_at
        prev = e
    assert [(r.type, r.to, r.answer) for r in st.human_requests] == [
        ("credit_decision", "cal.credit", "approve"),
        ("committee_approval", "dele.gate", "approve"),
        ("commitment_signoff", "rm.alpha", "confirm"),
        ("funding_release", "close.er", "release"),
    ]
    assert get_runtime(st).terminal == "closed"
    # fast persona: 15 business minutes after each request, and the log records it
    assert st.human_requests[0].answered_at == "2026-03-02T09:45-06:00"
    assert st.stage_log[1].exited_at == "2026-03-02T09:45-06:00"
    assert st.stage_log[3].decision.startswith("standard pricing for Term Loan")
    assert "Cleo Closer" in st.stage_log[5].why


def test_commitment_and_funding_messages(rules):
    st = good(profile={"disbursement": {"kind": "vendor", "vendor": "Vend Co", "bank": "First Bank", "last4": "1234", "past_loans": 3}})
    with persona("fast"):
        tick(st, rules, FAR)
    commit, fund = st.human_requests[2].message, st.human_requests[3].message
    assert "approved by Dele Gate on Mon 03/02" in commit and "countersigned" in commit
    assert "Pay to: verified vendor Vend Co, First Bank ...1234 (3 past loans paid to this account)." in fund
    assert "Final fraud screen: clear as of Mon 03/02 10:15 AM CT" in fund and "Commitment signed Mon 03/02." in fund
    assert not any(r.to == "borrower" for r in st.human_requests)  # J10: no separate borrower request


# ------------------------------------------------------------ waiting


def test_never_persona_never_advances_past_the_first_gate(rules):
    st = good()
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00", "2026-03-09T12:00", FAR)
    assert st.stage == "Credit"
    # past 100% of the limit the same request is reminded and copied to the backup (J21), nothing more
    assert kinds(st) == [("credit_decision", "cal.credit"), ("credit_decision", "cal.credit"), ("credit_decision", "cass.credit")]
    assert all(r.answer is None for r in st.human_requests)


def test_slow_gate_not_satisfied_until_the_answer_lands(rules):
    st = good()
    with persona("slow"):
        tick(st, rules, "2026-03-02T20:00")
        assert st.stage == "Credit" and st.human_requests[0].answer is None
        tick(st, rules, "2026-03-04T08:00")  # 09:30 + 20 business hours = Wed 09:30
        assert st.stage == "Credit"
        tick(st, rules, "2026-03-04T09:30")
    assert st.human_requests[0].answer == "approve"
    assert st.human_requests[0].answered_at == "2026-03-04T09:30-06:00"
    assert st.stage != "Credit"


def test_open_blocking_issue_holds_the_deal_until_it_clears(rules):
    st = good(issues=["OLD_TRICK:DUP_EIN"])
    with persona("fast"):
        tick(st, rules, "2026-03-02T12:00")
        assert st.stage == "Intake"
        st.issues = []
        add_doc(st, "2026-03-02T13:00")
        tick(st, rules, "2026-03-02T13:00", "2026-03-02T13:30", "2026-03-02T15:00")
    assert st.stage != "Intake"
    assert st.stage_log[0].exited_at == "2026-03-02T13:30-06:00"


def test_open_issue_at_committed_blocks_the_commitment_exit(rules):
    st = good()
    with persona("fast"):
        tick(st, rules, "2026-03-02T10:10")
        assert st.stage == "Committed"
        st.issues = ["MISSING_DOC:equipment_quote"]
        add_doc(st, "2026-03-02T10:12")
        tick(st, rules, "2026-03-02T10:12", "2026-03-02T11:00")
        assert st.stage == "Committed" and st.human_requests[2].answer == "confirm"
        st.issues = []
        add_doc(st, "2026-03-02T12:00")
        tick(st, rules, "2026-03-02T12:00", "2026-03-02T12:30", "2026-03-02T15:00")
    assert st.stage == "Closed" and get_runtime(st).terminal == "closed"


# ------------------------------------------------------------ reply path


def test_inbox_reply_moves_a_deal_exactly_like_the_persona(rules):
    a, b = good(), good()
    with persona("fast"):
        tick(a, rules, "2026-03-02T09:50")
    with persona("never"):
        tick(b, rules, "2026-03-02T09:40")
        assert b.stage == "Credit"
        (idx, _), = inbox.pending_requests(b)
        inbox.record_reply(b, rules, idx, "approve", at("2026-03-02T09:45"))
        tick(b, rules, "2026-03-02T09:50")
    assert a.stage == b.stage == "Committee"
    assert dump(a) == dump(b)
    assert a.human_requests[1].message == b.human_requests[1].message


def test_reply_validation_rejects_bad_input(rules):
    st = good()
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00", "2026-03-02T12:00")
        info_idx = None
        with pytest.raises(inbox.ReplyError):
            inbox.record_reply(st, rules, 0, "not_an_option", at("2026-03-02T12:00"))
        with pytest.raises(inbox.ReplyError):
            inbox.record_reply(st, rules, 99, "approve", at("2026-03-02T12:00"))
        inbox.record_reply(st, rules, 0, "approve", at("2026-03-02T12:00"))
        with pytest.raises(inbox.ReplyError):
            inbox.record_reply(st, rules, 0, "decline", at("2026-03-02T12:30"))
    assert st.human_requests[0].answer == "approve"
    assert info_idx is None


def test_borrower_requests_cannot_be_answered_by_reply(rules):
    st = good(issues=["MISSING_DOC:ein_letter"])
    with persona("never"):
        tick(st, rules, "2026-03-02T12:00")
    assert kinds(st) == [("info_request", "borrower")]
    with pytest.raises(inbox.ReplyError):
        inbox.record_reply(st, rules, 0, "approve", at("2026-03-02T12:00"))
    assert inbox.pending_requests(st) == []


def test_first_answer_wins_and_the_group_is_closed(rules):
    st = good()
    rt = get_runtime(st)
    with persona("never"):
        tick(st, rules, "2026-03-02T10:00")
        i2 = inbox.send_request(st, rt, "credit_decision", "cass.credit", at("2026-03-02T11:00"), "copy", "Credit",
                                "Credit:credit_decision", "copy")
        inbox.record_reply(st, rules, i2, "approve", at("2026-03-02T11:30"))
    first, copy = st.human_requests
    assert copy.answer == "approve" and copy.answered_at == "2026-03-02T11:30-06:00"
    assert first.answer == get_config().responder.superseded_label
    assert first.answered_at == copy.answered_at
    assert inbox.pending_requests(st) == []
    with pytest.raises(inbox.ReplyError):
        inbox.record_reply(st, rules, 0, "approve", at("2026-03-02T12:00"))


def test_decline_ends_the_deal_with_a_reason_and_no_more_requests(rules):
    st = good()
    with persona("fast"), answers(credit_decision="decline"):
        tick(st, rules, FAR)
    assert st.stage == "Credit" and get_runtime(st).terminal == "declined"
    entry = st.stage_log[-1]
    assert entry.stage == "Credit" and entry.exited_at is None and entry.decision == "decline"
    assert "Cal Credit answered decline" in entry.why and "declined" in entry.why
    assert kinds(st) == [("credit_decision", "cal.credit")]


def test_request_info_asks_the_borrower_and_resends_only_after_a_later_arrival(rules):
    st = good()
    with persona("fast"), answers(credit_decision="request_info"):
        tick(st, rules, FAR)
    assert kinds(st) == [("credit_decision", "cal.credit"), ("info_request", "borrower")]
    assert st.human_requests[1].at == "2026-03-02T09:45-06:00"
    assert "Cal Credit" in st.human_requests[1].message
    with persona("never"):
        tick(st, rules, "2026-03-21T12:00")
        assert len(st.human_requests) == 2  # nothing until something new arrives
        add_doc(st, "2026-03-22T10:00")
        tick(st, rules, "2026-03-22T10:00")
        assert len(st.human_requests) == 2  # and the folder must be quiet again
        tick(st, rules, "2026-03-22T10:30")
    assert kinds(st)[-1] == ("credit_decision", "cal.credit")
    assert st.human_requests[-1].at == "2026-03-22T10:30-06:00"
    assert st.stage == "Credit" and st.stage_log[-1].exited_at is None


# ------------------------------------------------------------ business hours and personas


def test_business_hours_arithmetic():
    assert business_add(at("2026-03-02T17:50"), 15) == at("2026-03-03T08:05")
    assert business_add(at("2026-03-06T17:50"), 15) == at("2026-03-09T08:05")  # Friday evening -> Monday
    assert business_add(at("2026-03-07T12:00"), 15) == at("2026-03-09T08:15")  # Saturday
    assert business_add(at("2026-03-02T07:00"), 0) == at("2026-03-02T08:00")  # zero still snaps into hours
    assert business_add(at("2026-03-02T09:00"), 540) == at("2026-03-02T18:00")
    assert next_open(at("2026-03-02T18:00")) == at("2026-03-03T08:00")
    assert business_sub(at("2026-03-03T08:05"), 15) == at("2026-03-02T17:50")
    assert business_sub(at("2026-03-09T07:00"), 30) == at("2026-03-06T17:30")
    assert business_sub(at("2026-03-07T12:00"), 0) == at("2026-03-06T18:00")


def test_persona_timings(rules):
    t0 = at("2026-03-02T09:00")
    with persona("fast"):
        assert inbox.due_time("cal.credit", "credit_decision", t0) == at("2026-03-02T09:15")
        assert inbox.due_time("borrower", "credit_decision", t0) is None
        assert inbox.due_time("cal.credit", "info_request", t0) is None
    with persona("slow"):
        assert inbox.due_time("cal.credit", "credit_decision", t0) == at("2026-03-04T09:00")
    with persona("ooo"):
        assert inbox.due_time("cal.credit", "credit_decision", t0) == at("2026-03-05T09:15")
    with persona("never"):
        assert inbox.due_time("cal.credit", "credit_decision", t0) is None
    cfg = get_config()
    r = dataclasses.replace(cfg.responder, default="slow", by_person={"cal.credit": "fast"}, by_type={"funding_release": "never"})
    with use_config(dataclasses.replace(cfg, responder=r)):
        assert inbox.due_time("cal.credit", "credit_decision", t0) == at("2026-03-02T09:15")
        assert inbox.due_time("rm.alpha", "commitment_signoff", t0) == at("2026-03-04T09:00")
        assert inbox.due_time("close.er", "funding_release", t0) is None


def test_requests_after_hours_are_answered_next_business_morning(rules):
    st = good(arrive="2026-03-02T17:20")
    with persona("fast"):
        tick(st, rules, FAR)
    # sent 17:50 (arrival + 30 min), 10 business minutes left today, 5 more next morning
    assert st.human_requests[0].at == "2026-03-02T17:50-06:00"
    assert st.human_requests[0].answered_at == "2026-03-03T08:05-06:00"


# ------------------------------------------------------------ product stage


@pytest.mark.parametrize("verified,advances", [(False, False), (True, True), (None, True)])
def test_equipment_vendor_check_blocks_only_when_explicitly_false(rules, verified, advances):
    prof = {} if verified is None else {"vendor_verified": verified}
    st = good(product="Equipment Loan", purpose="equipment", profile=prof)
    with persona("fast"):
        tick(st, rules, FAR)
    assert (st.stage != "Product") == advances
    if not advances:
        assert st.stage == "Product" and st.stage_log[-1].exited_at is None
        st.features["profile"]["vendor_verified"] = True
        add_doc(st, "2026-03-21T09:00")
        with persona("fast"):
            tick(st, rules, "2026-03-21T09:00", "2026-03-21T09:30", "2026-03-22T12:00")
        assert st.stage != "Product"


def test_non_equipment_product_ignores_vendor_flag(rules):
    st = good(profile={"vendor_verified": False})
    with persona("fast"):
        tick(st, rules, FAR)
    assert st.stage == "Closed"


def test_committee_exit_requires_the_memo(rules):
    st = good()
    with persona("fast"):
        tick(st, rules, "2026-03-02T09:50")
        assert st.stage == "Committee" and get_runtime(st).credit_memo is not None
        get_runtime(st).credit_memo = None
        with pytest.raises(RuntimeError, match="credit memo"):
            tick(st, rules, FAR)
