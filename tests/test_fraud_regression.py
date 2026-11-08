"""FRD-07 trap suite in code: end-to-end evaluate() on synthetic histories (Plan 05-02 Task 2).

Zero holds on the good analogues (honest repeat borrower, common preparer), the history-only
hold (the Monday case), the decoy history ids, stickiness with the cluster pass, determinism.
"""
from __future__ import annotations

from test_fraud_links import AGENT, PHONE, PREP, PROD, calibrated, hist_row, history, state
from test_fraud_synth import filler_deals
from test_move_support import at

from prism.engine.config import load_fraud_config
from prism.fraud.baseline import Baseline
from prism.fraud.decide import evaluate
from prism.fraud.rarity import FraudParams

CFG = load_fraud_config()
T1 = "2026-03-02T10:00"
T2 = "2026-03-03T15:00"
VENDOR_ROWS = [hist_row(10 + i, equipment_vendor="Ironclad Equipment LLC", vendor_remit_acct_last4="1111")
               for i in range(4)]
LOOKALIKE = ("vendor", "Ironclad Equipment Inc")


def ring_history():
    return history(VENDOR_ROWS + [
        hist_row(1, preparer_name=PREP, preparer_phone=PHONE, registered_agent=AGENT, fraud_label="ring",
                 final_status="declined", repayment_status=""),
        hist_row(2, preparer_name=PREP, preparer_phone=PHONE, registered_agent=AGENT),
        hist_row(3, pdf_producer=PROD),  # decoy: shares only the producer
        hist_row(4, registered_agent=AGENT),  # decoy: shares only the registered agent
    ])


def ring_deal(folder="Monday Freight"):
    ents = [("preparer", PREP), ("phone", PHONE), ("registered_agent", AGENT), ("pdf_producer", PROD), LOOKALIKE]
    return state(folder, ents, deposits=True)


def test_honest_repeat_borrower_with_f5_is_watch_not_held():
    base = history([hist_row(5, business_address="77 Honest Road", guarantor_name="Pat Repeat",
                             repayment_status="paid_off")])
    st = state("Hale Diner", [("address", "77 Honest Road"), ("guarantor", "Pat Repeat")], deposits=True)
    out = evaluate({"Hale Diner": st}, base, CFG, at(T1))
    assert "Hale Diner" not in out.per_deal
    assert out.history_flags == []
    (c,) = out.clusters
    assert c.tier == "watch" and c.families["F1"].fired and c.families["F5"].fired


def test_common_preparer_is_never_strong():
    rows = filler_deals(30, preparer_name="Common Prep Co") + filler_deals(120, start=30)
    base = calibrated(Baseline.from_rows(rows, params=FraudParams.from_config(CFG)))
    st = state("Busy Shop", [("preparer", "Common Prep Co")], deposits=True)
    out = evaluate({"Busy Shop": st}, base, CFG, at(T1))
    assert out.per_deal == {}
    assert all(not c.families["F1"].fired for c in out.clusters)


def test_history_only_hold_on_first_tick_flags_both_history_deals():
    base = ring_history()
    st = ring_deal()
    out = evaluate({st.folder: st}, base, CFG, at(T1))
    fr = out.per_deal[st.folder]
    assert fr.flagged and fr.held and fr.stage_at_flag == "Intake"
    assert out.history_flags == ["X-901", "X-902"]
    assert fr.linked_deals == ["X-901", "X-902"]


def test_decoy_history_deals_are_not_flagged_or_linked():
    base = ring_history()
    st = ring_deal()
    out = evaluate({st.folder: st}, base, CFG, at(T1))
    for decoy in ("X-903", "X-904"):
        assert decoy not in out.history_flags
        assert decoy not in out.per_deal[st.folder].linked_deals
    hold = [c for c in out.clusters if c.tier == "hold"]
    assert hold and "X-903" not in hold[0].deals and "X-904" not in hold[0].deals


def test_hold_is_sticky_when_later_evidence_is_weaker():
    base = ring_history()
    st = ring_deal()
    first = evaluate({st.folder: st}, base, CFG, at(T1)).per_deal[st.folder]
    # Next tick: the lookalike vendor and the deposit data are gone (weaker evidence).
    st.fraud = first
    st.entities = [e for e in st.entities if e.kind != "vendor"]
    st.features.pop("deposit_months")
    later = evaluate({st.folder: st}, base, CFG, at(T2))
    fr = later.per_deal[st.folder]
    assert fr.flagged and fr.held
    assert fr.flagged_at == first.flagged_at and fr.stage_at_flag == first.stage_at_flag
    assert fr.reason == first.reason
    assert set(later.history_flags) <= set(["X-901", "X-902"])


def test_evaluate_is_deterministic():
    base = ring_history()
    honest = state("Hale Diner", [("address", "77 Honest Road")], deposits=True)
    states = {"Monday Freight": ring_deal(), "Second Freight": ring_deal("Second Freight"), "Hale Diner": honest}
    one = evaluate(states, base, CFG, at(T1))
    two = evaluate(states, base, CFG, at(T1))
    assert one.history_flags == two.history_flags
    assert [c.model_dump() for c in one.clusters] == [c.model_dump() for c in two.clusters]
    assert {k: v.model_dump() for k, v in one.per_deal.items()} == {k: v.model_dump() for k, v in two.per_deal.items()}
    ring = [c for c in one.clusters if c.tier == "hold"]
    assert len(ring) == 1 and ring[0].deals[:2] == ["Monday Freight", "Second Freight"]
    assert one.per_deal["Monday Freight"].linked_deals == ["Second Freight", "X-901", "X-902"]
