"""The stuck-deal ladder: exact crossing times, exactly once, recipients, reminders, messages."""
from __future__ import annotations

import dataclasses

import pytest
from test_move_support import GOOD_PROFILE, add_doc, at, fact, kinds, make_rules, make_state, tick

from prism.engine.config import Rules
from prism.move import inbox
from prism.move.runtime import get_runtime
from prism.move.settings import get_config, use_config
from prism.move.sla import sla_alerts, sla_pct

FAR = "2026-03-20T12:00"


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def responders(default="fast", by_type=None):
    cfg = get_config()
    r = dataclasses.replace(cfg.responder, default=default, by_person={}, by_type=by_type or {})
    return use_config(dataclasses.replace(cfg, responder=r))


def stuck_intake(**kw):
    kw.setdefault("profile", {"dba": "Acme Bakery"})
    return make_state(issues=["MISSING_DOC:ein_letter"], **kw)


def good(folder="Acme Co", **kw):
    st = make_state(folder, profile=dict(GOOD_PROFILE), **kw)
    st.facts["annual_revenue_stated"] = fact(700000)
    return st


def ladder(st, stage):
    return [(a.at, a.level, a.to) for a in st.alerts if a.stage == stage]


# ------------------------------------------------------------ the ladder


def test_clock_jump_emits_every_threshold_at_its_exact_time_once(rules):
    st = stuck_intake()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30", "2026-03-03T10:00")  # 25 hours in one jump past the 8h limit
    assert ladder(st, "Intake") == [
        ("2026-03-02T17:00-06:00", "alert", "ivy.one"),        # +8h, owner (J3)
        ("2026-03-02T17:00-06:00", "alert", "rm.alpha"),       # +8h, RM
        ("2026-03-02T21:00-06:00", "escalation", "ivy.boss"),  # +12h, owner's escalates_to
        ("2026-03-03T01:00-06:00", "escalation", "top.lead"),  # +16h, second escalation
    ]
    n = len(st.alerts)
    with responders("never"):
        tick(st, rules, "2026-03-03T10:00", "2026-03-10T10:00")
    assert len(st.alerts) == n
    assert sla_alerts(st, rules, at("2026-03-10T10:00")) == []


def test_sla_alerts_alone_returns_each_alert_once(rules):
    st = stuck_intake()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30")
    assert st.alerts == []
    got = sla_alerts(st, rules, at("2026-03-03T10:00"))
    assert [(a.level, a.to) for a in got] == [
        ("alert", "ivy.one"), ("alert", "rm.alpha"), ("escalation", "ivy.boss"), ("escalation", "top.lead")]
    assert sla_alerts(st, rules, at("2026-03-03T10:00")) == []
    # nothing is due before the limit
    early = stuck_intake()
    with responders("never"):
        tick(early, rules, "2026-03-02T09:30", "2026-03-02T16:59")
    assert early.alerts == [] and sla_alerts(early, rules, at("2026-03-02T16:59")) == []


def test_each_threshold_appears_only_when_crossed(rules):
    st = stuck_intake()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30", "2026-03-02T17:00")
        assert [a.level for a in st.alerts] == ["alert", "alert"]  # exactly at the limit counts
        tick(st, rules, "2026-03-02T20:59")
        assert len(st.alerts) == 2
        tick(st, rules, "2026-03-02T21:00")
        assert len(st.alerts) == 3
        tick(st, rules, "2026-03-03T01:00")
        assert len(st.alerts) == 4


def test_alert_messages_say_all_four_things(rules):
    st = stuck_intake()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30", "2026-03-03T10:00")
    first, second, third = st.alerts[0], st.alerts[2], st.alerts[3]
    assert first.message == (
        "Acme Company LLC (Acme Bakery), RM Rita Alpha, has been in Intake for 8h 0m, past its 8h limit.\n"
        "It is waiting on the borrower's IRS EIN letter.\n"
        "To unblock it: the borrower sends the IRS EIN letter (requested Mon 03/02 9:30 AM CT).")
    assert second.message == (
        "Escalation: Acme Company LLC (Acme Bakery), RM Rita Alpha, has been in Intake for 12h 0m, 1.5x its 8h limit.\n"
        "It is waiting on the borrower's IRS EIN letter; Ivy One was alerted Mon 03/02 5:00 PM CT with no movement since.\n"
        "To unblock it: the borrower sends the IRS EIN letter (requested Mon 03/02 9:30 AM CT). Please step in or reassign.")
    assert third.message.startswith("Second escalation: Acme Company LLC (Acme Bakery), RM Rita Alpha, has been in Intake for 16h 0m, 2x its 8h limit.")
    assert "Alerted Ivy One at Mon 03/02 5:00 PM CT, escalated to Ivy Boss at Mon 03/02 9:00 PM CT." in third.message
    for a in st.alerts:
        assert "{" not in a.message and "None" not in a.message


def test_second_escalation_carries_the_needed_by_sentence(rules):
    st = stuck_intake(needed_by="2026-03-12T15:00-06:00")
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30", "2026-03-03T10:00")
    third = [a for a in st.alerts if a.level == "escalation"][-1]
    assert third.message.endswith("Funds are needed by Thu 03/12 3:00 PM CT.")


def test_waiting_on_a_mismatch_names_both_values(rules):
    st = make_state(issues=["MISMATCH:ein"], profile={"mismatch_evidence": {"ein": {
        "application": "12-3456789", "document": "EIN letter", "value": "12-3456780"}}})
    with responders("never"):
        tick(st, rules, "2026-03-02T09:10", "2026-03-03T10:00")
    msg = st.alerts[0].message
    assert "the borrower to confirm the EIN: the application says 12-3456789, the EIN letter says 12-3456780." in msg
    assert "confirms which is correct, with a corrected document" in msg


def test_owner_equal_to_rm_gets_one_entry_and_j4_still_sends_the_200_percent(rules):
    st = good()
    with responders("fast", {"commitment_signoff": "never"}):
        tick(st, rules, "2026-03-02T10:10", "2026-03-10T10:00")
    assert st.stage == "Committed"
    # entered Committed at 10:00; 48h limit
    assert ladder(st, "Committed") == [
        ("2026-03-04T10:00-06:00", "alert", "rm.alpha"),         # one entry: owner is the RM
        ("2026-03-05T10:00-06:00", "escalation", "top.lead"),    # +72h
        ("2026-03-06T10:00-06:00", "escalation", "top.lead"),    # +96h, still sent (J4)
    ]
    assert st.alerts[-1].message.startswith("Second escalation:")
    assert st.alerts[-2].message.startswith("Escalation:")


def test_committee_limits_follow_the_route(rules):
    for amount, limits in ((100_000, (24, 36, 48)), (300_000, (48, 72, 96))):
        st = good(amount=amount)
        with responders("fast", {"committee_approval": "never"}):
            tick(st, rules, "2026-03-02T09:50", "2026-03-30T10:00")
        entered = at("2026-03-02T09:45")
        got = [a.at for a in st.alerts if a.stage == "Committee"]
        want = []
        for h in limits:
            when = entered.timestamp() + h * 3600
            want.append(at("2026-03-02T09:45").fromtimestamp(when, tz=entered.tzinfo).isoformat(timespec="minutes"))
        assert sorted(set(got)) == want
        to = {(a.level, a.to) for a in st.alerts if a.stage == "Committee"}
        approver = "dele.gate" if amount < 250_000 else "chair.gate"
        assert to == {("alert", approver), ("alert", "rm.alpha"), ("escalation", "chief.credit"), ("escalation", "top.lead")}
        assert get_runtime(st).limit_hours == limits[0]


def test_committee_150_message_names_the_unanswered_signoff(rules):
    st = good()
    with responders("fast", {"committee_approval": "never"}):
        tick(st, rules, "2026-03-02T09:50", "2026-03-04T10:00")
    esc = [a for a in st.alerts if a.stage == "Committee" and a.to == "chief.credit"][0]
    assert esc.message == (
        "Escalation: Acme Company LLC (Acme Bakery), RM Rita Alpha, has been in Committee for 36h 0m, 1.5x its 24h limit.\n"
        "It is waiting on Dele Gate's committee approval (sent Mon 03/02 9:45 AM CT); Dele Gate was alerted "
        "Tue 03/03 9:45 AM CT with no movement since.\n"
        "To unblock it: Dele Gate or backup Chief Credit answers approve / decline. Please step in or reassign.")


def test_product_stage_manager_is_the_final_escalation_person(rules):
    st = good(product="Equipment Loan", purpose="equipment")
    st.features["profile"].update(vendor_verified=False, quote={"vendor": "Vend Co", "remit_last4": "4321"})
    with responders("fast"):
        tick(st, rules, "2026-03-02T10:10", "2026-03-10T10:00")
    assert st.stage == "Product"
    got = ladder(st, "Product")
    assert [(lvl, to) for _, lvl, to in got] == [
        ("alert", "prod.one"), ("alert", "rm.alpha"), ("escalation", "top.lead"), ("escalation", "top.lead")]
    assert "verification of vendor Vend Co and remit account ...4321" in st.alerts[0].message
    assert "a callback to Vend Co confirming the remit account" in st.alerts[0].message
    assert st.alerts[-1].message.startswith("Second escalation:")


# ------------------------------------------------------------ reminders and the backup (J21)


def test_never_answers_chain_reminds_copies_the_backup_and_first_answer_wins(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:40", "2026-03-04T09:30", "2026-03-08T09:30")
    assert st.stage == "Credit"
    assert kinds(st) == [("credit_decision", "cal.credit"), ("credit_decision", "cal.credit"), ("credit_decision", "cass.credit")]
    original, reminder, copy = st.human_requests
    assert reminder.at == copy.at == "2026-03-04T09:30-06:00"
    assert reminder.message == copy.message == (
        "Reminder: Acme Company LLC (Acme Bakery) still needs your credit decision, sent Mon 03/02 9:30 AM CT (48h 0m ago).\n"
        "Cass Credit, you are copied as backup: whichever of you answers first decides it.\n"
        "Please reply approve, decline or request info.")
    assert [a.level for a in st.alerts if a.stage == "Credit"] == ["alert", "alert", "escalation", "escalation"]
    pending = [i for i, _ in inbox.pending_requests(st)]
    assert pending == [0, 1, 2]
    with responders("never"):
        inbox.record_reply(st, rules, 2, "approve", at("2026-03-08T10:00"))
        tick(st, rules, "2026-03-08T10:30")
    label = get_config().responder.superseded_label
    assert [r.answer for r in st.human_requests[:3]] == [label, label, "approve"]
    assert st.stage == "Committee" and st.stage_log[1].exited_at == "2026-03-08T10:00-06:00"


def test_reminder_is_sent_once_per_group(rules):
    st = good()
    with responders("never"):
        tick(st, rules, "2026-03-02T09:40", "2026-03-04T09:30", "2026-03-05T09:30", "2026-03-30T09:30")
    assert len([r for r in st.human_requests if r.type == "credit_decision"]) == 3


def test_out_of_office_primary_gets_the_reminder_and_the_available_backup(rules):
    st = good("Bolt Inc", arrive="2026-03-02T09:00")  # officer is out from Tue 08:00, covered by cal.credit
    with responders("never"):
        tick(st, rules, "2026-03-02T09:40", "2026-03-02T09:40")
    assert kinds(st) == [("credit_decision", "cora.credit")]
    st2 = good("Bolt Inc", arrive="2026-03-04T09:00")
    with responders("never"):
        tick(st2, rules, "2026-03-04T09:40", "2026-03-06T09:30")
    assert kinds(st2) == [("credit_decision", "cal.credit"), ("credit_decision", "cal.credit"), ("credit_decision", "cass.credit")]


# ------------------------------------------------------------ sla_pct


def test_sla_pct_is_elapsed_over_limit(rules):
    st = stuck_intake()
    now = at("2026-03-02T09:00")
    assert sla_pct(make_state(), rules, now) == 0.0  # not started: no stage entered yet
    with responders("never"):
        tick(st, rules, "2026-03-02T09:30")
    assert sla_pct(st, rules, at("2026-03-02T09:00")) == 0.0
    assert sla_pct(st, rules, at("2026-03-02T13:00")) == 0.5
    assert sla_pct(st, rules, at("2026-03-02T17:00")) == 1.0
    assert sla_pct(st, rules, at("2026-03-03T05:00")) == 2.5


def test_sla_pct_stops_when_the_deal_ends(rules):
    st = good()
    with responders("fast"):
        tick(st, rules, "2026-03-20T12:00")
    assert get_runtime(st).terminal == "closed"
    p = sla_pct(st, rules, at("2026-03-20T12:00"))
    assert p == sla_pct(st, rules, at("2026-03-30T12:00")) and p < 1


def test_empty_rules_give_zero_and_nothing(rules):
    st = make_state()
    assert sla_pct(st, Rules(), at("2026-03-05T12:00")) == 0.0
    assert sla_alerts(st, Rules(), at("2026-03-05T12:00")) == []
