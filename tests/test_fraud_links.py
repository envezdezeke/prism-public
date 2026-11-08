"""Independent link classes, clusters, linked_deals and history_flags (Plan 05-02 Task 1).

Synthetic only: 150 filler history deals (so a two-deal entity is under the 2% rarity
fraction), made-up names and 555 phones, X- history ids. No sample data.
"""
from __future__ import annotations

import dataclasses

from test_fraud_synth import deal_row, deposit_rows, filler_deals, iso_day
from test_move_support import at, make_state

from prism.contract import FAMILY_LABELS
from prism.engine.config import load_fraud_config
from prism.engine.state import EntityRef
from prism.fraud.baseline import Baseline, Calibration
from prism.fraud.decide import decide, evaluate
from prism.fraud.graph import build_graph
from prism.fraud.links import LinkContext, link_classes_for, shared_link_classes
from prism.fraud.rarity import FraudParams
from prism.fraud.signals import LINK_MARK, FamilySignal

CFG = load_fraud_config()
PARAMS = FraudParams.from_config(CFG)
T = "2026-03-02T10:00"

PREP = "Shady Preparer Services"
PHONE = "210-555-0177"
AGENT = "Rare Agent Holdings"
ACCT = "4321"
PROD = "Rare Producer Tool 9"


def calibrated(b: Baseline) -> Baseline:
    """Fixed thresholds so F3 and F5 can fire on synthetic deals."""
    b.calibration = Calibration(
        tau={"F3": 0.5, "F5": 0.5},
        reference={"round": [0.05 + 0.002 * i for i in range(60)]},
    )
    return b


def history(extra=(), deposits=(), **kw) -> Baseline:
    return calibrated(Baseline.from_rows(list(filler_deals(150)) + list(extra), list(deposits),
                                         params=kw.get("params", PARAMS)))


def state(folder, ents, deposits=False):
    st = make_state(folder)
    st.entities = [EntityRef(k, v, f"{folder}/app.pdf") for k, v in ents]
    if deposits:
        st.features["deposit_months"] = [
            {"deposit_count": 20, "pct_deposits_multiple_of_100": 1.0, "deposit_weekdays": "Mon-Fri"}
        ] * 6
    return st


def ctx_for(states, base, cfg=CFG):
    st = {s.folder: s for s in states}
    return LinkContext(build_graph(st, base), st, base, cfg)


def hist_row(i, **kw):
    return deal_row(f"X-9{i:02d}", iso_day(200 + i), **kw)


# ----------------------------------------------------------------------- Tests 1-4

def test_two_strong_classes_link_and_one_class_never_links():
    base = history()
    a = state("Acme Co", [("preparer", PREP), ("phone", PHONE), ("pdf_producer", PROD)])
    b = state("Bolt Inc", [("preparer", PREP), ("phone", PHONE)])
    c = state("Cove LLC", [("pdf_producer", PROD)])
    ctx = ctx_for([a, b, c], base)
    assert shared_link_classes("Acme Co", "Bolt Inc", ctx) == {"preparer", "phone"}
    assert ctx.qualified("Acme Co", "Bolt Inc")
    assert ctx.shared("Acme Co", "Cove LLC") == {"producer"}
    assert not ctx.qualified("Acme Co", "Cove LLC")


def test_history_deal_qualifies_on_two_classes_not_on_one():
    base = history([
        hist_row(1, registered_agent=AGENT, vendor_remit_acct_last4=ACCT),
        hist_row(2, pdf_producer=PROD),
        hist_row(3, registered_agent=AGENT),
    ])
    a = state("Acme Co", [("registered_agent", AGENT), ("remit_account", ACCT), ("pdf_producer", PROD)])
    ctx = ctx_for([a], base)
    assert ctx.shared("Acme Co", "X-901") == {"registered_agent", "remit_account"}
    assert ctx.qualified("Acme Co", "X-901")
    assert not ctx.qualified("Acme Co", "X-902")
    assert not ctx.qualified("Acme Co", "X-903")


def test_config_can_merge_two_kinds_into_one_class():
    raw = dict(CFG.raw)
    raw["history_flags"] = {"min_links": 2, "link_classes": {"preparer_office": ["preparer", "phone"]}}
    cfg = dataclasses.replace(CFG, raw=raw)
    params = FraudParams.from_config(cfg)
    assert link_classes_for(params)["phone"] == link_classes_for(params)["preparer"] == "preparer_office"
    assert link_classes_for(cfg)["pdf_producer"] == "producer"
    base = history(params=params)
    a = state("Acme Co", [("preparer", PREP), ("phone", PHONE)])
    b = state("Bolt Inc", [("preparer", PREP), ("phone", PHONE)])
    ctx = ctx_for([a, b], base, cfg)
    assert ctx.shared("Acme Co", "Bolt Inc") == {"preparer_office"}
    assert not ctx.qualified("Acme Co", "Bolt Inc")


def test_bad_link_classes_shape_is_a_config_error():
    import pytest

    from prism.engine.config import ConfigError

    for bad in (["preparer"], {"x": []}, {"x": ["not_a_kind"]}, {"x": ["phone"], "y": ["phone"]}):
        raw = dict(CFG.raw)
        raw["history_flags"] = {"min_links": 2, "link_classes": bad}
        with pytest.raises(ConfigError):
            FraudParams.from_config(dataclasses.replace(CFG, raw=raw))


def test_same_lookalike_group_and_same_deposit_statistic_are_classes():
    vend = [hist_row(10 + i, equipment_vendor="Ironclad Equipment LLC", vendor_remit_acct_last4="1111")
            for i in range(4)]
    ring_hist = hist_row(20, equipment_vendor="Ironclad Equipment Inc", vendor_remit_acct_last4="9999")
    deps = deposit_rows("X-920", [(20, 1.0, "Mon-Fri")] * 6)
    base = history(vend + [ring_hist], deps)
    a = state("Acme Co", [("vendor", "Ironclad Equipment Inc")], deposits=True)
    b = state("Bolt Inc", [("vendor", "Ironclad Equipment Inc")], deposits=True)
    c = state("Cove LLC", [("vendor", "Ironclad Equipment Inc")])
    ctx = ctx_for([a, b, c], base)
    assert {"f3", "f5"} <= ctx.shared("Acme Co", "Bolt Inc")
    assert "f5" not in ctx.shared("Acme Co", "Cove LLC")  # no deposit data, no f5 class
    assert "f3" in ctx.shared("Acme Co", "Cove LLC")
    # History deal scored leave-one-out: its own lookalike vendor and deposits still count.
    assert {"f3", "f5"} <= ctx.shared("Acme Co", "X-920")


# ----------------------------------------------------------------------- Tests 5-8

def _sigs_ring(folders, hist_ids):
    out = []
    for f in folders:
        partners = tuple(sorted(set(folders) - {f})) + tuple(hist_ids)
        out += [
            FamilySignal("F1", (f,) + partners, 5.0, True,
                         "shared rare preparer used by 2 of 152 history deals" + LINK_MARK + ", ".join(partners)),
            FamilySignal("F3", (f,), 5.0, True, "vendor resembles a usual vendor"),
            FamilySignal("F5", (f,), 5.0, True, "deposit pattern unusual"),
        ]
    return out


def _ring_world():
    base = history([
        hist_row(1, preparer_name=PREP, preparer_phone=PHONE, registered_agent=AGENT),
        hist_row(2, preparer_name=PREP, preparer_phone=PHONE),
        hist_row(3, pdf_producer=PROD),  # decoy: producer only
        hist_row(4, registered_agent=AGENT),  # decoy: agent only
    ])
    ents = [("preparer", PREP), ("phone", PHONE), ("registered_agent", AGENT), ("pdf_producer", PROD)]
    a, b = state("Acme Co", ents), state("Bolt Inc", ents)
    honest = state("Hale Diner", [("address", "77 Honest Road"), ("guarantor", "Pat Repeat")])
    return base, a, b, honest


def test_honest_repeat_borrower_is_not_flagged_and_links_nothing():
    base = history([hist_row(5, business_address="77 Honest Road", guarantor_name="Pat Repeat",
                             repayment_status="paid_off")])
    honest = state("Hale Diner", [("address", "77 Honest Road"), ("guarantor", "Pat Repeat")])
    states = {"Hale Diner": honest}
    out = evaluate(states, base, CFG, at(T))
    assert "Hale Diner" not in out.per_deal or not out.per_deal["Hale Diner"].flagged
    assert out.history_flags == []
    assert all(not fr.linked_deals for fr in out.per_deal.values())
    watch = [c for c in out.clusters if "Hale Diner" in c.deals]
    assert watch and watch[0].tier == "watch" and watch[0].families["F1"].fired


def test_held_ring_links_peers_and_qualified_history_only():
    base, a, b, honest = _ring_world()
    states = {s.folder: s for s in (a, b, honest)}
    graph = build_graph(states, base)
    sigs = _sigs_ring(["Acme Co", "Bolt Inc"], ["X-901", "X-902", "X-903", "X-904"])
    sigs.append(FamilySignal("F1", ("Hale Diner", "X-999"), 2.0, True, "shared rare address" + LINK_MARK + "X-999"))
    out = decide(sorted(sigs, key=lambda s: (s.deals[0], s.family)), graph, states, base, CFG, at(T))
    assert out.per_deal["Acme Co"].linked_deals == ["Bolt Inc", "X-901", "X-902"]
    assert out.per_deal["Bolt Inc"].linked_deals == ["Acme Co", "X-901", "X-902"]
    assert out.history_flags == ["X-901", "X-902"]
    assert "Hale Diner" not in out.per_deal
    # Clusters: ids by first member name, all six families, tiers.
    assert [c.id for c in out.clusters] == ["C-001", "C-002"]
    hold, watch = out.clusters
    assert hold.tier == "hold" and hold.deals == ["Acme Co", "Bolt Inc", "X-901", "X-902"]
    assert watch.tier == "watch" and watch.deals == ["Hale Diner", "X-999"]
    for c in out.clusters:
        assert set(c.families) == set(FAMILY_LABELS)
    assert hold.families["F1"].fired and "used by 2 of 152 history deals" in hold.families["F1"].evidence
    assert LINK_MARK not in hold.families["F1"].evidence
    assert hold.families["F2"].evidence == "not fired"  # all six families computed since Task 3
    assert watch.families["F3"].evidence == "not fired"


def test_non_held_deal_in_watch_cluster_keeps_empty_links():
    base, a, b, honest = _ring_world()
    states = {s.folder: s for s in (a, b)}
    graph = build_graph(states, base)
    # Only F1 fired on both: watch tier, no holds, no links, no history flags.
    sigs = [s for s in _sigs_ring(["Acme Co", "Bolt Inc"], ["X-901"]) if s.family == "F1"]
    out = decide(sigs, graph, states, base, CFG, at(T))
    assert out.per_deal == {}
    assert out.history_flags == []
    assert len(out.clusters) == 1 and out.clusters[0].tier == "watch"


def test_old_trick_only_held_deal_is_a_cluster_of_itself():
    base = history()
    st = state("Gate Supply", [])
    st.issues = ["OLD_TRICK:DUP_EIN"]
    states = {"Gate Supply": st}
    out = decide([], build_graph(states, base), states, base, CFG, at(T))
    fr = out.per_deal["Gate Supply"]
    assert fr.flagged and fr.linked_deals == []
    assert out.history_flags == []
    assert len(out.clusters) == 1
    c = out.clusters[0]
    assert c.tier == "hold" and c.deals == ["Gate Supply"]
    assert not any(r.fired for r in c.families.values())


def test_cluster_pass_is_deterministic():
    base, a, b, honest = _ring_world()
    states = {s.folder: s for s in (a, b, honest)}
    sigs = _sigs_ring(["Acme Co", "Bolt Inc"], ["X-901", "X-902", "X-903"])
    one = decide(sigs, build_graph(states, base), states, base, CFG, at(T))
    two = decide(sigs, build_graph(states, base), states, base, CFG, at(T))
    assert one.history_flags == two.history_flags
    assert [c.model_dump() for c in one.clusters] == [c.model_dump() for c in two.clusters]
    assert {k: v.model_dump() for k, v in one.per_deal.items()} == {k: v.model_dump() for k, v in two.per_deal.items()}
