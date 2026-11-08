"""F2 (burst), F4 (document forensics) and F6 (terms shape, evidence only) (Plan 05-02 Task 3).

Synthetic only (made-up names, X- history ids); the sample check is in test_fraud_sample.py.
"""
from __future__ import annotations

import math
from pathlib import Path

from test_fraud_links import PREP, hist_row, history, state
from test_move_support import at, fact, make_state

from prism.engine.config import load_fraud_config
from prism.engine.state import Doc, EntityRef
from prism.fraud.decide import decide, evaluate
from prism.fraud.graph import build_graph
from prism.fraud.rarity import FraudParams
from prism.fraud.signals import FamilySignal, compute_signals, score_f2, score_f4, score_f6

CFG = load_fraud_config()
PARAMS = FraudParams.from_config(CFG)
NOV = "2026-11-02T09:00"
T = "2026-11-09T10:00"


def opened(folder, arrive, ents):
    st = make_state(folder, arrive)
    st.entities = [EntityRef(k, v, f"{folder}/app.pdf") for k, v in ents]
    return st


# ------------------------------------------------------------------------------ F2

def test_f2_fires_on_partners_opened_together_and_bits_grow_with_k():
    base = history([hist_row(1, preparer_name=PREP)])  # history partner months earlier
    a = opened("Acme Co", NOV, [("preparer", PREP)])
    b = opened("Bolt Inc", "2026-11-05T09:00", [("preparer", PREP)])
    c = opened("Cove LLC", "2026-11-06T09:00", [("preparer", PREP)])
    states = {s.folder: s for s in (a, b, c)}
    graph = build_graph(states, base)
    res = score_f2("Acme Co", graph, states, base, PARAMS)
    assert res is not None and res.partners == ("Bolt Inc", "Cove LLC")
    assert res.bits == math.log2(1 + 2)
    two = {s.folder: s for s in (a, b)}
    res2 = score_f2("Acme Co", build_graph(two, base), two, base, PARAMS)
    assert res2.partners == ("Bolt Inc",) and res2.bits == 1.0


def test_f2_ignores_partners_outside_the_window_and_honest_repeat_borrowers():
    base = history([hist_row(5, business_address="77 Honest Road", guarantor_name="Pat Repeat",
                             repayment_status="paid_off")])
    honest = opened("Hale Diner", NOV, [("address", "77 Honest Road"), ("guarantor", "Pat Repeat")])
    late = opened("Late Shop", "2026-12-20T09:00", [("preparer", PREP)])
    early = opened("Early Shop", NOV, [("preparer", PREP)])
    states = {s.folder: s for s in (honest, late, early)}
    graph = build_graph(states, base)
    assert score_f2("Hale Diner", graph, states, base, PARAMS) is None
    assert score_f2("Late Shop", graph, states, base, PARAMS) is None
    sigs = compute_signals(graph, states, base, CFG, at(T))
    assert not any(s.family == "F2" and s.fired for s in sigs)


# ------------------------------------------------------------------------------ F4

def doc(folder, name, cls, producer="Same Tool 3.1", creator="Same Maker", ocr=False, day=2):
    meta = {"ocr_used": ocr}
    if producer is not None:
        meta.update(producer=producer, creator=creator)
    return Doc(path=f"{folder}/{name}", name=name, arrive_at=at(f"2026-11-0{day}T09:00"),
               abs_path=Path(name), doc_class=cls, meta=meta)


def forensic_state(folder="Acme Co", n=3, **stmt):
    st = make_state(folder, NOV)
    st.docs = [doc(folder, "app.pdf", "application"), doc(folder, "letter.pdf", "request_letter")]
    st.docs += [doc(folder, f"stmt{i}.pdf", "bank_statement", **stmt) for i in range(n)]
    return st


def test_f4_same_tool_on_borrower_papers_and_statements_fires():
    res = score_f4(forensic_state(), PARAMS)
    assert res is not None and res.fired and res.matched == 3 and res.usable == 3
    assert res.bits == 3.0
    assert "3 of 3 bank statements" in res.evidence


def test_f4_does_not_fire_on_other_tool_few_docs_no_producer_or_ocr():
    assert not score_f4(forensic_state(producer="Bank Printer 9", creator="Bank Core"), PARAMS).fired
    assert not score_f4(forensic_state(n=2), PARAMS).fired
    assert score_f4(forensic_state(producer=None), PARAMS) is None
    assert score_f4(forensic_state(ocr=True), PARAMS) is None


# ------------------------------------------------------------------------------ F6

def terms_history():
    rows = [hist_row(30 + i, amount_requested=str(10000 + 1000 * i), annual_revenue_stated="100000")
            for i in range(20)]
    return history(rows)


def test_f6_fires_above_the_clean_tail_and_never_votes():
    base = terms_history()
    assert base.terms_ratios() == sorted(base.terms_ratios()) and len(base.terms_ratios()) == 20
    st = make_state("Acme Co", NOV, amount=900000)
    st.facts["annual_revenue_stated"] = fact(1000000)
    res = score_f6(st, base, PARAMS)
    assert res is not None and res.fired and res.bits > 0
    low = make_state("Bolt Inc", NOV, amount=10000)
    low.facts["annual_revenue_stated"] = fact(1000000)
    assert not score_f6(low, base, PARAMS).fired
    # F1 + F6 + one other voting family: not held (F6 never votes).
    sigs = [
        FamilySignal("F1", ("Acme Co", "X-999"), 5.0, True, "shared rare preparer"),
        FamilySignal("F3", ("Acme Co",), 5.0, True, "lookalike vendor"),
        FamilySignal("F6", ("Acme Co",), res.bits, True, res.evidence),
    ]
    assert "Acme Co" not in decide(sigs, None, {"Acme Co": st}, None, CFG, at(T)).per_deal


def test_history_revenue_parsing_never_raises():
    base = history([hist_row(40, amount_requested="abc", annual_revenue_stated="0"),
                    hist_row(41, amount_requested="$50,000", annual_revenue_stated="-3")])
    assert base.deals["X-940"].amount is None and base.deals["X-940"].revenue is None
    assert base.deals["X-941"].amount == 50000.0 and base.deals["X-941"].revenue is None


# ------------------------------------------------------------------------ hold rule

def test_hold_rule_can_use_f2_and_f4():
    st = make_state("Acme Co", NOV)
    f1 = FamilySignal("F1", ("Acme Co", "Bolt Inc"), 5.0, True, "shared rare preparer")
    f2 = FamilySignal("F2", ("Acme Co", "Bolt Inc"), 1.0, True, "opened together")
    f4 = FamilySignal("F4", ("Acme Co",), 3.0, True, "same tool")
    states = {"Acme Co": st, "Bolt Inc": make_state("Bolt Inc", NOV)}
    assert decide([f1, f2, f4], None, states, None, CFG, at(T)).per_deal["Acme Co"].flagged
    assert "Acme Co" not in decide([f1, f2], None, states, None, CFG, at(T)).per_deal


def test_clusters_show_new_families_and_signals_stay_sorted():
    base = history([hist_row(1, preparer_name=PREP)])
    a = forensic_state("Acme Co")
    a.entities = [EntityRef("preparer", PREP, "Acme Co/app.pdf")]
    b = opened("Bolt Inc", "2026-11-04T09:00", [("preparer", PREP)])
    states = {"Acme Co": a, "Bolt Inc": b}
    graph = build_graph(states, base)
    sigs = compute_signals(graph, states, base, CFG, at(T))
    assert [(s.deals[0], s.family) for s in sigs] == sorted((s.deals[0], s.family) for s in sigs)
    assert sigs == compute_signals(graph, states, base, CFG, at(T))
    fams = {(s.deals[0], s.family) for s in sigs if s.fired}
    assert {("Acme Co", "F1"), ("Acme Co", "F2"), ("Acme Co", "F4")} <= fams
    out = evaluate(states, base, CFG, at(T))
    assert out.per_deal["Acme Co"].flagged  # F1 + F2 + F4, no F3 or F5
    for c in out.clusters:
        for fam in ("F2", "F4", "F6"):
            assert c.families[fam].evidence != "not evaluated"


def test_bad_family_keys_are_config_errors():
    import dataclasses

    import pytest

    from prism.engine.config import ConfigError

    bad = [("F2", {"burst_window_days": -1}), ("F2", {"burst_min_partners": 0}), ("F4", {"min_docs": 0}),
           ("F4", {"min_share": 1.5}), ("F4", {"third_party_classes": []}), ("F6", {"tail_percentile": 1.0})]
    for fam, upd in bad:
        raw = dict(CFG.raw)
        fams = {k: dict(v) for k, v in raw["families"].items()}
        fams[fam].update(upd)
        raw["families"] = fams
        with pytest.raises(ConfigError):
            FraudParams.from_config(dataclasses.replace(CFG, raw=raw))
    assert PARAMS.burst_window_days == 7 and PARAMS.f4_min_docs == 3 and PARAMS.f6_tail_percentile == 0.95
