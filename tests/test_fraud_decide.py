"""decide(): hold rule, non-voting families, sticky holds, reason text, current-deal links.

Synthetic DealStates and hand-built FamilySignal tuples; no sample data needed.
"""
from __future__ import annotations

import dataclasses

from test_move_support import at, make_state

from prism.contract import StageLogEntry, format_sim_time
from prism.engine.config import load_fraud_config
from prism.fraud.decide import decide
from prism.fraud.signals import LINK_MARK, FamilySignal

CFG = load_fraud_config()
T1 = "2026-03-02T10:00"
T2 = "2026-03-03T15:00"
F1_EV = "shared rare preparer used by 2 of 40 history deals" + LINK_MARK + "Bolt Inc, H-HIST-A"


def sig(family, deals, fired=True, evidence=None):
    return FamilySignal(family=family, deals=tuple(deals), bits=5.0, fired=fired,
                        evidence=evidence if evidence is not None else family + " evidence text")


def ring_signals(folder="Acme Co", partners=("Bolt Inc", "H-HIST-A")):
    return [
        sig("F1", (folder, *partners), evidence=F1_EV),
        sig("F3", (folder,), evidence="vendor name differs from the usual supplier"),
        sig("F5", (folder,), evidence="deposits are all round hundreds"),
    ]


def states_of(*sts):
    return {s.folder: s for s in sts}


def run(signals, states, t=T1, cfg=CFG):
    return decide(signals, None, states, None, cfg, at(t))


def test_f1_f3_f5_flags_and_holds_with_time_stage_and_reason():
    st = make_state("Acme Co")
    out = run(ring_signals(), states_of(st, make_state("Bolt Inc")))
    fr = out.per_deal["Acme Co"]
    assert fr.flagged and fr.held
    assert fr.flagged_at == format_sim_time(at(T1))
    assert fr.stage_at_flag == "Intake"
    assert fr.reason and fr.reason.strip()


def test_stage_at_flag_comes_from_the_stage_log_not_state_stage():
    st = make_state("Acme Co")
    st.stage_log = [
        StageLogEntry(stage="Intake", entered_at="2026-03-02T09:00-06:00", exited_at="2026-03-02T09:30-06:00", why="w"),
        StageLogEntry(stage="Credit", entered_at="2026-03-02T09:30-06:00", why="w"),
        StageLogEntry(stage="Committee", entered_at="2026-03-02T11:00-06:00", why="w"),
    ]
    st.stage = "Committee"
    out = run(ring_signals(), states_of(st, make_state("Bolt Inc")))
    assert out.per_deal["Acme Co"].stage_at_flag == "Credit"


def test_hold_rule_needs_f1_plus_two_more():
    a = make_state("Acme Co")
    states = states_of(a, make_state("Bolt Inc"))
    f1, f3, f5 = ring_signals()
    cases = [
        [f1],
        [f1, f3],
        [f3, f5],
        [f1, dataclasses.replace(f3, fired=False), dataclasses.replace(f5, fired=False)],
    ]
    for signals in cases:
        assert "Acme Co" not in run(signals, states).per_deal


def test_non_voting_family_never_counts():
    cfg = dataclasses.replace(CFG, hold_additional_from=tuple(CFG.hold_additional_from) + ("F6",),
                              non_voting=("F6",))
    f1, f3, _f5 = ring_signals()
    signals = [f1, f3, sig("F6", ("Acme Co",))]
    out = run(signals, states_of(make_state("Acme Co"), make_state("Bolt Inc")), cfg=cfg)
    assert "Acme Co" not in out.per_deal


def test_hold_is_sticky_when_the_evidence_disappears():
    st = make_state("Acme Co")
    states = states_of(st, make_state("Bolt Inc"))
    first = run(ring_signals(), states).per_deal["Acme Co"]
    st.fraud = first
    second = run([], states, t=T2).per_deal["Acme Co"]
    assert second.flagged and second.held
    assert second.flagged_at == first.flagged_at
    assert second.stage_at_flag == first.stage_at_flag
    assert second.reason == first.reason


def test_reason_has_entity_part_and_family_evidence_but_no_partners():
    out = run(ring_signals(), states_of(make_state("Acme Co"), make_state("Bolt Inc")))
    reason = out.per_deal["Acme Co"].reason
    assert "shared rare preparer used by 2 of 40 history deals" in reason
    assert "vendor name differs from the usual supplier" in reason
    assert "deposits are all round hundreds" in reason
    assert LINK_MARK.strip("; ") not in reason
    assert "Bolt Inc" not in reason and "H-HIST-A" not in reason


def test_linked_deals_are_current_partners_only():
    states = states_of(make_state("Acme Co"), make_state("Bolt Inc"), make_state("Zed Co"))
    signals = ring_signals(partners=("Zed Co", "H-HIST-A", "Bolt Inc"))
    out = run(signals, states)
    assert out.per_deal["Acme Co"].linked_deals == ["Bolt Inc", "Zed Co"]


def test_decide_does_not_mutate_states():
    st = make_state("Acme Co")
    states = states_of(st, make_state("Bolt Inc"))
    run(ring_signals(), states)
    assert not st.fraud.flagged


# ---------------------------------------------------------------- OLD_TRICK path (Task 2)

def test_old_trick_issue_alone_flags_and_holds():
    st = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER"])
    out = run([], states_of(st))
    fr = out.per_deal["Acme Co"]
    assert fr.flagged and fr.held
    assert fr.flagged_at == format_sim_time(at(T1))
    assert fr.stage_at_flag == "Intake"
    assert fr.linked_deals == []


def test_old_trick_reason_names_codes_sorted_and_fraud_ops():
    st = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER", "OLD_TRICK:REV_INFLATION",
                                       "OLD_TRICK:ID_MISMATCH"])
    reason = run([], states_of(st)).per_deal["Acme Co"].reason
    assert "ID_MISMATCH, REV_INFLATION and STMT_TAMPER" in reason
    assert "Fraud Operations" in reason
    two = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER", "OLD_TRICK:REV_INFLATION"])
    assert "REV_INFLATION and STMT_TAMPER" in run([], states_of(two)).per_deal["Acme Co"].reason


def test_old_trick_and_cluster_give_one_flag_checks_first():
    st = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER"])
    out = run(ring_signals(), states_of(st, make_state("Bolt Inc")))
    fr = out.per_deal["Acme Co"]
    assert fr.flagged and fr.held
    reason = fr.reason
    assert reason.index("STMT_TAMPER") < reason.index("deposits are all round hundreds")
    assert fr.linked_deals == ["Bolt Inc"]


def test_ordinary_issues_never_flag():
    st = make_state("Acme Co", issues=["MISSING_DOC:tax_return", "MISSING_PAGE:bank_statement",
                                       "MISMATCH:legal_name"])
    assert "Acme Co" not in run([], states_of(st)).per_deal


def test_old_trick_flag_is_sticky_when_the_issue_goes_away():
    st = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER"])
    states = states_of(st)
    first = run([], states).per_deal["Acme Co"]
    st.fraud = first
    st.issues = []
    second = run([], states, t=T2).per_deal["Acme Co"]
    assert second == first


def test_sticky_old_trick_deal_does_not_gain_links_from_a_lone_f1():
    st = make_state("Acme Co", issues=["OLD_TRICK:STMT_TAMPER"])
    states = states_of(st, make_state("Bolt Inc"))
    st.fraud = run([], states).per_deal["Acme Co"]
    f1 = ring_signals()[0]
    assert run([f1], states, t=T2).per_deal["Acme Co"].linked_deals == []


# --- strong-link hold path (F1 + one more when two independent party links join the deals) ---

import pytest

from prism.engine.config import ConfigError
from prism.fraud import decide as decide_mod
from prism.fraud import links as fraud_links


class _Ctx:
    """Stand-in LinkContext: returns fixed link classes for a pair."""

    def __init__(self, classes):
        self._classes = set(classes)

    def shared(self, a, b):
        return set(self._classes)


SL = dataclasses.replace(CFG, hold_strong_link_min_additional=1)


def _met(families, classes, cfg=SL):
    return decide_mod._strong_link_met("Acme Co", set(families), ["Bolt Inc"], _Ctx(classes), cfg)


def test_strong_link_two_party_classes_plus_one_family_holds():
    assert _met({"F1", "F3"}, {"preparer", "remit_account"})
    assert _met({"F1", "F4"}, {"registered_agent", fraud_links.CLASS_PRODUCER})


def test_strong_link_needs_f1_and_one_more_family():
    assert not _met({"F3", "F5"}, {"preparer", "remit_account"})
    assert not _met({"F1"}, {"preparer", "remit_account"})


def test_strong_link_one_party_class_is_not_enough():
    assert not _met({"F1", "F3"}, {"preparer"})


def test_strong_link_phone_and_preparer_are_one_fact():
    assert not _met({"F1", "F3"}, {"preparer", "phone"})


def test_strong_link_deposit_pattern_and_weak_kinds_never_count_as_a_link():
    assert not _met({"F1", "F5"}, {"preparer", fraud_links.CLASS_F5})
    assert not _met({"F1", "F3"}, {"preparer", "address", "bank"})


def test_strong_link_off_when_setting_absent_or_no_context():
    off = dataclasses.replace(CFG, hold_strong_link_min_additional=None)
    assert not _met({"F1", "F3"}, {"preparer", "remit_account"}, cfg=off)
    assert not decide_mod._strong_link_met("Acme Co", {"F1", "F3"}, ["Bolt Inc"], None, SL)


def test_strong_link_setting_is_validated(tmp_path):
    import yaml
    raw = yaml.safe_load(open("config/fraud.yaml", encoding="utf-8"))
    raw["hold_rule"]["strong_link_min_additional"] = 3
    p = tmp_path / "fraud.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fraud_config(p)
