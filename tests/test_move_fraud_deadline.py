"""Fraud freeze and chase, deadline risk, needed-by badge and priority."""
from __future__ import annotations

import dataclasses

import pytest
from test_move_support import GOOD_PROFILE, at, fact, kinds, make_rules, make_state, tick

from prism.contract import Fraud
from prism.move import deadline, inbox
from prism.move.runtime import get_runtime
from prism.move.settings import get_config, use_config
from prism.move.sla import sla_pct

FAR = "2026-03-20T12:00"


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def responders(default="fast", by_type=None):
    cfg = get_config()
    r = dataclasses.replace(cfg.responder, default=default, by_person={}, by_type=by_type or {})
    return use_config(dataclasses.replace(cfg, responder=r))


def good(folder="Acme Co", **kw):
    st = make_state(folder, profile=dict(GOOD_PROFILE), **kw)
    st.facts["annual_revenue_stated"] = fact(700000)
    return st


def flag(st, when, reason="the bookkeeper appears on two other recent loans; the vendor account differs", linked=("Bolt Inc",)):
    st.fraud = Fraud(flagged=True, held=True, flagged_at=when, stage_at_flag=st.stage, reason=reason, linked_deals=list(linked))


# ------------------------------------------------------------ freeze


def test_flagged_deal_freezes_at_once_and_asks_the_analyst(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00", linked=("Bolt Inc", "H-0000-001"))
        tick(st, rules, "2026-03-02T11:05")
    rt = get_runtime(st)
    assert st.stage == "Credit" and rt.frozen_at == at("2026-03-02T11:00")
    entry = st.stage_log[-1]
    assert entry.decision == "held" and "awaiting Fraud Ops" in entry.why and entry.exited_at is None
    fraud_reqs = [r for r in st.human_requests if r.type == "fraud_hold"]
    assert [(r.to, r.at) for r in fraud_reqs] == [("fraud.an", "2026-03-02T11:00-06:00")]
    msg = fraud_reqs[0].message
    assert msg.startswith("Fraud hold requested: Acme Company LLC (Acme Bakery), RM Rita Alpha, $100,000 Term Loan, "
                          "frozen at Credit as of Mon 03/02 11:00 AM CT. No money has gone out.")
    assert "Why: the bookkeeper appears on two other recent loans" in msg
    assert "Linked: Bolt Inc and past loans H-0000-001." in msg
    assert msg.endswith("Please place or release the hold. The deal stays frozen until you do.")
    assert "{" not in msg and "None" not in msg


def test_old_trick_hold_names_the_failed_check(rules):
    st = good(issues=["OLD_TRICK:DUP_EIN"])
    with responders("never"):
        tick(st, rules, "2026-03-02T09:10")
        flag(st, "2026-03-02T09:10-06:00", reason="the EIN was already used by a different legal name", linked=())
        tick(st, rules, "2026-03-02T09:20")
    msg = [r for r in st.human_requests if r.type == "fraud_hold"][0].message
    assert "the bank's known-fraud check DUP_EIN failed: the EIN was already used by a different legal name." in msg
    assert "Linked: no other deals so far." in msg


def test_frozen_deal_has_no_ladder_alerts_and_a_frozen_sla_pct(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00")
        tick(st, rules, "2026-03-02T11:05", FAR)
    assert [a for a in st.alerts if a.stage == "Credit" and "fraud hold request" not in a.message] == []
    p = sla_pct(st, rules, at("2026-03-02T11:05"))
    assert p == sla_pct(st, rules, at(FAR)) and 0 < p < 0.1
    assert st.stage == "Credit" and get_runtime(st).terminal is None


def test_chase_copy_after_two_hours_escalation_after_four_more(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00")
        tick(st, rules, "2026-03-02T12:59")
        assert [(r.type, r.to) for r in st.human_requests if r.type == "fraud_hold"] == [("fraud_hold", "fraud.an")]
        tick(st, rules, "2026-03-02T13:00")
        copies = [(r.to, r.at) for r in st.human_requests if r.type == "fraud_hold"]
        assert copies == [("fraud.an", "2026-03-02T11:00-06:00"), ("fraud.head", "2026-03-02T13:00-06:00")]
        assert not [a for a in st.alerts if "Fraud hold" in a.message or "fraud hold request" in a.message]
        tick(st, rules, "2026-03-02T16:59")
        assert not [a for a in st.alerts if "fraud hold request" in a.message]
        tick(st, rules, "2026-03-02T17:00", FAR)
    esc = [a for a in st.alerts if "fraud hold request" in a.message]
    assert [(a.at, a.level, a.to, a.stage) for a in esc] == [("2026-03-02T17:00-06:00", "escalation", "top.lead", "Credit")]
    assert "unanswered" in esc[0].message and "Fran Analyst" in esc[0].message and "Fern Head" in esc[0].message
    # the deal stays frozen and held through the whole chain
    assert st.fraud.held and st.stage == "Credit" and get_runtime(st).frozen_at is not None
    assert len([r for r in st.human_requests if r.type == "fraud_hold"]) == 2  # nothing repeated


def test_either_analyst_answering_closes_the_chase_and_the_other_copy(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00")
        tick(st, rules, "2026-03-02T13:30")
        idxs = [i for i, r in enumerate(st.human_requests) if r.type == "fraud_hold"]
        assert len(idxs) == 2
        inbox.record_reply(st, rules, idxs[1], "place_hold", at("2026-03-02T14:00"))
        tick(st, rules, "2026-03-02T14:05", FAR)
    label = get_config().responder.superseded_label
    assert [st.human_requests[i].answer for i in idxs] == [label, "place_hold"]
    assert not [a for a in st.alerts if "fraud hold request" in a.message]  # chase closed, no escalation
    assert st.stage == "Credit" and get_runtime(st).frozen_at is not None
    assert "Fern Head answered place hold on Mon 03/02 2:00 PM CT" in st.stage_log[-1].why


def test_persona_analyst_places_the_hold_and_the_deal_stays_frozen(rules):
    st = good()
    with responders("fast"):
        tick(st, rules, "2026-03-02T10:10")
        flag(st, "2026-03-02T10:05-06:00")
        tick(st, rules, FAR)
    fraud_req = [r for r in st.human_requests if r.type == "fraud_hold"][0]
    assert fraud_req.answer == "place_hold" and fraud_req.answered_at == "2026-03-02T10:20-06:00"
    assert st.stage == "Committed" and get_runtime(st).terminal is None  # never reaches Closed
    commit = [r for r in st.human_requests if r.type == "commitment_signoff"][0]
    assert commit.answer is None  # non-fraud answers are not applied while frozen
    assert not any(e.stage == "Closed" for e in st.stage_log)


def test_non_fraud_reply_is_recorded_but_not_applied_while_frozen(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00")
        inbox.record_reply(st, rules, 0, "approve", at("2026-03-02T11:30"))
        tick(st, rules, "2026-03-02T12:00", FAR)
    assert st.human_requests[0].answer == "approve" and st.stage == "Credit"


def test_move_resumes_only_when_the_flag_itself_is_cleared(rules):
    st = good()
    with responders("fast"):
        tick(st, rules, "2026-03-02T10:10")
        flag(st, "2026-03-02T10:05-06:00")
        tick(st, rules, "2026-03-02T12:00")
        assert st.stage == "Committed"
        st.fraud = Fraud()  # released outside Move
        tick(st, rules, "2026-03-02T13:00", FAR)
    assert get_runtime(st).terminal == "closed" and st.stage == "Closed"
    committed = [e for e in st.stage_log if e.stage == "Committed"][0]
    assert "Fraud hold cleared" in committed.why and committed.decision == "confirm"


def test_frozen_interval_is_excluded_from_the_stage_clock(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T10:00")
        flag(st, "2026-03-02T11:00-06:00")
        tick(st, rules, "2026-03-02T11:05")
        st.fraud = Fraud()
        tick(st, rules, "2026-03-02T15:00")  # frozen 4 hours
        assert get_runtime(st).frozen_total.total_seconds() == 4 * 3600
        assert sla_pct(st, rules, at("2026-03-03T09:30")) == 20 / 48
        tick(st, rules, "2026-03-04T10:00")
        assert [a for a in st.alerts if a.stage == "Credit" and "unanswered" not in a.message] == []
        tick(st, rules, "2026-03-04T13:30")
    first = [a for a in st.alerts if a.stage == "Credit" and a.level == "alert"]
    assert {a.at for a in first} == {"2026-03-04T13:30-06:00"}  # 09:30 + 48h + 4h frozen


# ------------------------------------------------------------ deadline

HISTORY = (
    "deal_id,stage,entered_at,exited_at,hours_in_stage,sla_hours\n"
    "a,Intake,x,y,1,8\na,Intake,x,y,2,8\na,Intake,x,y,3,8\n"
    "a,Credit,x,y,24,48\na,Credit,x,y,abc,48\na,Credit,x,y,,48\n"
    "a,Committee,x,y,12,24\na,Product,x,y,8,16\na,Committed,x,y,10,48\na,Closed,x,y,6,24\n"
)


def test_typical_hours_is_the_median_with_sla_fallback(tmp_path):
    r = make_rules(tmp_path / "with", history=HISTORY)
    assert deadline.typical_hours(r, "Intake") == 2.0
    assert deadline.typical_hours(r, "Credit") == 24.0  # bad rows skipped
    none = make_rules(tmp_path / "without")
    assert deadline.typical_hours(none, "Credit") == 48.0 and deadline.typical_hours(none, "Intake") == 8.0
    partial = make_rules(tmp_path / "partial", history="stage,hours_in_stage\nIntake,5\n")
    assert deadline.typical_hours(partial, "Intake") == 5.0 and deadline.typical_hours(partial, "Product") == 16.0
    broken = make_rules(tmp_path / "broken", history="not,a,csv\n\x00\x00")
    assert deadline.typical_hours(broken, "Credit") == 48.0


def deadline_alerts(st):
    return [a for a in st.alerts if a.message.startswith("Deadline at risk")]


def test_deadline_risk_fires_once_to_rm_and_owner(tmp_path):
    r = make_rules(tmp_path, history=HISTORY)
    st = good(needed_by="2026-03-04T12:00-06:00")
    with responders("never"):
        tick(st, r, "2026-03-02T09:00", "2026-03-02T09:40", "2026-03-05T09:00", FAR)
    got = deadline_alerts(st)
    assert [(a.at, a.level, a.to, a.stage) for a in got] == [
        ("2026-03-02T09:00-06:00", "alert", "rm.alpha", "Intake"),
        ("2026-03-02T09:00-06:00", "alert", "ivy.one", "Intake"),
    ]
    assert got[0].message == got[1].message
    lines = got[0].message.split("\n")
    assert lines[0] == "Deadline at risk: Acme Company LLC (Acme Bakery), RM Rita Alpha, needs $100,000 by Wed 03/04 12:00 PM CT."
    assert lines[1].startswith("It is in Intake; at typical speed the remaining steps (Intake, Credit, Committee, Product, Committed, Closed) finish around ")
    assert lines[1].endswith(", after the deadline.")
    assert lines[2].startswith("To make it: Ivy One needs to finish Intake by ")


def test_no_deadline_alert_when_the_deal_fits(tmp_path):
    r = make_rules(tmp_path, history=HISTORY)
    st = good(needed_by="2026-03-20T12:00-06:00")
    with responders("fast"):
        tick(st, r, "2026-03-02T09:00", "2026-03-02T09:40", FAR)
    assert deadline_alerts(st) == [] and get_runtime(st).terminal == "closed"
    plain = good()
    with responders("never"):
        tick(plain, r, "2026-03-02T09:00", FAR)
    assert deadline_alerts(plain) == []


def test_deadline_without_history_uses_sla_hours(rules):
    st = good(needed_by="2026-03-12T15:00-06:00")
    with responders("never"):
        tick(st, rules, "2026-03-02T09:00")
    assert len(deadline_alerts(st)) == 2  # 48h stages in business hours cannot fit in 10 days
    far = good(needed_by="2026-12-31T15:00-06:00")
    with responders("never"):
        tick(far, rules, "2026-03-02T09:00", FAR)
    assert deadline_alerts(far) == []


def test_risk_found_later_in_the_flow_fires_once(tmp_path):
    r = make_rules(tmp_path, history=HISTORY)
    st = good(needed_by="2026-03-09T15:00-06:00")
    with responders("never"):
        tick(st, r, "2026-03-02T09:00", "2026-03-02T09:40")
        assert deadline_alerts(st) == []  # fits while the credit officer is still inside the typical time
        tick(st, r, "2026-03-06T12:00", FAR)
    got = deadline_alerts(st)
    assert sorted(a.to for a in got) == ["cal.credit", "rm.alpha"]
    assert len({a.at for a in got}) == 1
    assert "To make it: Cal Credit needs to answer the credit decision by " in got[0].message


def test_committee_message_carries_answer_by_only_for_deadline_deals(tmp_path):
    r = make_rules(tmp_path, history=HISTORY)
    st = good(needed_by="2026-03-09T15:00-06:00")
    assert deadline.answer_by(st, r) == at("2026-03-06T01:00")  # needed_by minus Closed, Committed and Product
    with responders("fast"):
        tick(st, r, "2026-03-02T09:50")
        plain = good("Cedar Ltd")
        tick(plain, r, "2026-03-02T09:50")
    msg = [x for x in st.human_requests if x.type == "committee_approval"][0].message.split("\n")
    assert msg[3] == "Full memo on the deal page. Please approve or decline by Fri 03/06 1:00 AM CT."
    plain_msg = [x for x in plain.human_requests if x.type == "committee_approval"][0].message.split("\n")
    assert plain_msg[3] == "Full memo on the deal page. Please approve or decline."
    assert deadline.answer_by(plain, r) is None


def test_needed_by_badge_values(tmp_path):
    r = make_rules(tmp_path, history=HISTORY)
    now = at("2026-03-02T09:00")
    assert deadline.needed_by_badge(good(), r, now) == "none"
    assert deadline.needed_by_badge(good(needed_by="2026-12-31T15:00-06:00"), r, now) == "ok"
    assert deadline.needed_by_badge(good(needed_by="2026-03-04T12:00-06:00"), r, now) == "at_risk"
    assert deadline.needed_by_badge(good(needed_by="2026-03-01T12:00-06:00"), r, now) == "overdue"
    junk = good()
    junk.facts["needed_by"] = fact("No specific date")
    assert deadline.needed_by_badge(junk, r, now) == "none"
    done = good(needed_by="2026-03-20T12:00-06:00")
    with responders("fast"):
        tick(done, r, "2026-03-02T12:00")
    assert deadline.needed_by_badge(done, r, at("2026-03-25T00:00")) == "ok"  # funded before the date


def test_priority_key_puts_deadline_deals_first_by_date_then_name():
    a = make_state("Zulu Co", needed_by="2026-03-10T15:00-06:00")
    b = make_state("Alpha Co", needed_by="2026-03-05T15:00-06:00")
    c = make_state("Mike Co")
    d = make_state("Bravo Co")
    order = sorted([a, c, d, b], key=deadline.priority_key)
    assert [s.folder for s in order] == ["Alpha Co", "Zulu Co", "Bravo Co", "Mike Co"]
    assert kinds(a) == []
