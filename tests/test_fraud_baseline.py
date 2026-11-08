"""History index, entity graph and F1 on synthetic history (nothing from data/)."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest
from test_fraud_synth import deal_row, deposit_rows, filler_deals, iso_day, write_history

from prism.engine.config import load_fraud_config
from prism.engine.state import DealState, EntityRef
from prism.fraud.baseline import (
    Baseline, Calibration, DealProfile, DepositFeatures, build_baseline, deposit_features,
)
from prism.fraud.decide import FraudOutcome, evaluate
from prism.fraud.graph import build_graph
from prism.fraud.rarity import FraudParams, fit_beta_mom, shrunk_share
from prism.fraud.signals import compute_signals, f5_statistics, score_f3, score_f5

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
# History size for the tracer: large enough that df = 2 is under the 2% rarity fraction.
HIST_N = 150


@pytest.fixture(scope="module")
def cfg():
    return load_fraud_config()


# ------------------------------------------------------------------------ index

def test_spellings_merge_and_stats(cfg):
    rows = [
        deal_row("X-1", iso_day(0), preparer_name="Zeta Works, L.L.C."),
        deal_row("X-2", iso_day(5), preparer_name="ZETA WORKS LLC", final_status="funded",
                 repayment_status="charged_off"),
        deal_row("X-3", iso_day(9), preparer_name="Other Person", fraud_label="old_trick"),
    ]
    b = Baseline.from_rows(rows)
    assert b.n_history_deals == 3
    df, repaid, first, last = b.entity_stats("preparer", "zeta works llc")
    assert (df, repaid) == (2, 1)
    assert first == date.fromisoformat(iso_day(0)) and last == date.fromisoformat(iso_day(5))
    assert b.deals_with("preparer", "zeta works llc") == ["X-1", "X-2"]
    assert b.df("preparer", "zeta works llc", exclude="X-1") == 1
    assert b.entity_stats("preparer", "zeta works llc", exclude="X-1")[1:] == (
        0, date.fromisoformat(iso_day(5)), date.fromisoformat(iso_day(5)))
    assert b.df("preparer", "nobody") == 0
    assert b.value_counts("preparer")["zeta works llc"] == 2
    assert b.ordered_ids() == ["X-1", "X-2", "X-3"]


def test_n_effective_leave_one_out():
    b = Baseline.from_rows([deal_row("X-1"), deal_row("X-2")])
    assert b.n_effective() == 2
    assert b.n_effective("X-1") == 1
    assert b.n_effective("unknown") == 2


def test_empty_baseline_is_safe():
    b = Baseline()
    assert b.n_history_deals == 0 and b.df("preparer", "x") == 0 and b.n_effective() == 0
    assert b.warnings == [] and b.ordered_ids() == []


def test_build_never_raises(tmp_path, cfg):
    assert build_baseline(None, cfg).n_history_deals == 0
    missing = build_baseline(tmp_path / "nope", cfg)
    assert missing.n_history_deals == 0 and missing.warnings
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert build_baseline(empty_dir, cfg).warnings
    rows = [deal_row("X-1", iso_day(0)), deal_row("X-2", "not-a-date"), deal_row("X-1", iso_day(3)),
            deal_row("", iso_day(4))]
    b = build_baseline(write_history(tmp_path, rows), cfg)
    assert b.n_history_deals == 1
    text = " | ".join(b.warnings)
    assert "X-2" in text and "duplicate" in text.lower()
    assert len(b.warnings) >= 3


def test_missing_columns_degrade_to_empty(tmp_path, cfg):
    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "deals.csv").write_text("deal_id,application_date\nX-1,2026-01-02\n", encoding="utf-8")
    b = build_baseline(hist, cfg)
    assert b.n_history_deals == 1 and b.df("preparer", "x") == 0


# ------------------------------------------------------------- tracer: F1 end to end

def tracer_history(tmp_path):
    rows = filler_deals(HIST_N)
    for r in rows[:30]:
        r["preparer_name"] = "Common Prep LLC"
    rows[3]["vendor_remit_acct_last4"] = "4321"
    rows[7]["vendor_remit_acct_last4"] = "4321"
    return write_history(tmp_path, rows)


def test_tracer_f1_hand_computed(tmp_path, cfg):
    base = build_baseline(tracer_history(tmp_path), cfg)
    assert base.n_history_deals == HIST_N
    cur = DealState(folder="cur-a", entities=[
        EntityRef("remit_account", "4321", "cur-a/x.pdf"),
        EntityRef("preparer", "common prep, l.l.c.", "cur-a/x.pdf"),
    ])
    states = {"cur-a": cur}
    graph = build_graph(states, base)
    assert graph.entity_weights[("remit_account", "4321")].strong
    assert not graph.entity_weights[("preparer", "common prep llc")].strong
    assert graph.entity_deals[("remit_account", "4321")] == ["X-003", "X-007", "cur-a"]

    sigs = [s for s in compute_signals(graph, states, base, cfg, NOW) if s.family == "F1"]
    assert len(sigs) == 1
    s = sigs[0]
    w = cfg.raw["families"]["F1"]["type_weights"]["remit_account"]
    beta = cfg.raw["families"]["F1"]["outcome_beta"]
    omega = 1 - beta * (2 + 1) / (2 + 2)  # both history deals repaid
    assert s.bits == pytest.approx(w * omega * -math.log2(3 / (HIST_N + 1)))
    assert s.fired and s.deals == ("cur-a", "X-003", "X-007")
    assert "remit account" in s.evidence.lower()


def test_current_vs_current_link_without_history(tmp_path, cfg):
    base = build_baseline(tracer_history(tmp_path), cfg)
    ent = [EntityRef("registered_agent", "Brand New Agent Co", "d/p.pdf")]
    states = {"a": DealState(folder="a", entities=list(ent)), "b": DealState(folder="b", entities=list(ent)),
              "c": DealState(folder="c", entities=[EntityRef("registered_agent", "Lonely Agent", "d")])}
    sigs = compute_signals(build_graph(states, base), states, base, cfg, NOW)
    by_deal = {s.deals[0]: s for s in sigs if s.family == "F1"}
    assert set(by_deal) == {"a", "b"}
    assert by_deal["a"].deals == ("a", "b") and by_deal["b"].deals == ("b", "a")
    w = cfg.raw["families"]["F1"]["type_weights"]["registered_agent"]
    beta = cfg.raw["families"]["F1"]["outcome_beta"]
    assert by_deal["a"].bits == pytest.approx(w * (1 - beta * 1 / 2) * math.log2(HIST_N + 1))


def test_errored_state_is_skipped_and_empty_baseline_gives_nothing(tmp_path, cfg):
    base = build_baseline(tracer_history(tmp_path), cfg)
    ent = [EntityRef("remit_account", "4321", "d")]
    states = {"a": DealState(folder="a", entities=ent, error="boom")}
    assert compute_signals(build_graph(states, base), states, base, cfg, NOW) == []
    states = {"a": DealState(folder="a", entities=ent)}
    assert compute_signals(build_graph(states, Baseline()), states, Baseline(), cfg, NOW) == []


def test_signals_are_deterministic(tmp_path, cfg):
    base = build_baseline(tracer_history(tmp_path), cfg)
    states = {k: DealState(folder=k, entities=[EntityRef("remit_account", "4321", "d")]) for k in "ba"}
    one = compute_signals(build_graph(states, base), states, base, cfg, NOW)
    two = compute_signals(build_graph(dict(reversed(states.items())), base),
                          dict(reversed(states.items())), base, cfg, NOW)
    assert one == two


def test_lone_f1_holds_nothing_and_is_a_watch_cluster(tmp_path, cfg):
    """decide() is real since Plan 05-01: a lone shared entity never holds, it is watched."""
    base = build_baseline(tracer_history(tmp_path), cfg)
    states = {"a": DealState(folder="a", entities=[EntityRef("remit_account", "4321", "d")])}
    out = evaluate(states, base, cfg, NOW)
    assert out.per_deal == {} and out.history_flags == []
    assert [c.tier for c in out.clusters] == ["watch"] and out.clusters[0].families["F1"].fired


# ------------------------------------------------------------- Task 2: deposits, F3, F5

OTHER_VENDORS = ["Omega Trucking LLC", "Sigma Tools LLC", "Delta Pumps LLC", "Theta Cranes LLC"]


@pytest.fixture(scope="module")
def params(cfg):
    return FraudParams.from_config(cfg)


def vendor_rows(extra=()):
    rows = [deal_row(f"X-{i:03d}", iso_day(i), equipment_vendor="Kappa Supply LLC",
                     vendor_remit_acct_last4="1111") for i in range(5)]
    rows += [deal_row(f"X-{5 + i:03d}", iso_day(5 + i), equipment_vendor=v,
                      vendor_remit_acct_last4=f"20{i}") for i, v in enumerate(OTHER_VENDORS)]
    rows += list(extra)
    return rows


def vendor_profile(vendor, account="1111"):
    ents = {"vendor": (vendor,)}
    if account:
        ents["remit_account"] = (account,)
    return DealProfile("q", ents)


def test_deposit_features_from_rows():
    rows = [
        {"deposit_count": "10", "pct_deposits_multiple_of_100": "0.5", "deposit_weekdays": "Mon-Fri"},
        {"deposit_count": "20", "pct_deposits_multiple_of_100": "0.25", "deposit_weekdays": "Mon,Thu"},
        {"deposit_count": "x", "pct_deposits_multiple_of_100": "0.9", "deposit_weekdays": "Mon-Fri"},
        {"deposit_count": "5", "pct_deposits_multiple_of_100": "1.0", "deposit_weekdays": "garbage"},
        {"nonsense": 1},
    ]
    f = deposit_features(rows)
    assert isinstance(f, DepositFeatures)
    assert f.n == 35 and f.months_used == 3
    assert f.round_mass == pytest.approx(5 + 5 + 5)
    assert f.day_mass == pytest.approx((12, 2, 2, 12, 2, 0, 0))
    assert deposit_features([]) is None
    assert deposit_features([{"deposit_count": "bad"}]) is None
    assert deposit_features("not rows") is None


def test_from_rows_attaches_deposits_and_warns_for_unknown_ids(params):
    deps = deposit_rows("X-000", [(10, 0.5, "Mon-Fri")] * 6) + deposit_rows("X-404", [(3, 0.1, "Mon-Fri")])
    b = Baseline.from_rows([deal_row("X-000"), deal_row("X-001")], deps, params)
    assert b.deals["X-000"].profile.deposits.n == 60
    assert b.deals["X-001"].profile.deposits is None
    assert any("X-404" in w for w in b.warnings)
    assert b.round_shares() == [pytest.approx(0.5)]
    assert b.round_shares(exclude="X-000") == []
    assert b.pooled_day_mass()[0] == pytest.approx(60 * 1 / 5)
    assert sum(b.pooled_day_mass(exclude="X-000")) == 0


def test_f3_lookalikes(params):
    b = Baseline.from_rows(vendor_rows(), params=params)
    expect = -math.log2(1 / 10)  # n_pat 0, n_vend 9 vendor observations
    suffix = score_f3(vendor_profile("kappa supply inc"), b, params)
    assert suffix is not None and suffix.bits == pytest.approx(expect)
    assert suffix.group == "kappa supply" and (suffix.n_pat, suffix.n_vend) == (0, 9)
    assert "suffix" in suffix.what_differs
    acct = score_f3(vendor_profile("kappa supply llc", "9999"), b, params)
    assert acct is not None and "account" in acct.what_differs
    typo = score_f3(vendor_profile("kappa suply llc"), b, params)
    assert typo is not None and "name" in typo.what_differs
    assert score_f3(vendor_profile("kappa supply llc"), b, params) is None  # the usual form
    assert score_f3(vendor_profile("kappa supply llc", ""), b, params) is None  # no account: never "different"
    assert score_f3(vendor_profile("zebra imports llc"), b, params) is None
    assert score_f3(DealProfile("q", {}), b, params) is None


def test_f3_needs_a_frequent_vendor(params):
    rows = vendor_rows()
    b = Baseline.from_rows(rows, params=params)
    # Omega appears once: below min_vendor_deals, so a lookalike of it is not a signal.
    assert score_f3(vendor_profile("omega trucking inc", "200"), b, params) is None


def test_f3_prior_pattern_lowers_bits_and_leave_one_out(params):
    prior = deal_row("X-100", iso_day(20), equipment_vendor="Kappa Supply Inc", vendor_remit_acct_last4="1111")
    base_bits = score_f3(vendor_profile("kappa supply inc"), Baseline.from_rows(vendor_rows(), params=params), params).bits
    b = Baseline.from_rows(vendor_rows([prior]), params=params)
    got = score_f3(vendor_profile("kappa supply inc"), b, params)
    assert (got.n_pat, got.n_vend) == (1, 10)
    assert got.bits == pytest.approx(-math.log2(2 / 11)) and got.bits < base_bits
    loo = score_f3(vendor_profile("kappa supply inc"), b, params, exclude="X-100")
    assert (loo.n_pat, loo.n_vend) == (0, 9) and loo.bits == pytest.approx(base_bits)
    # excluding a member of the usual group shrinks the group but keeps it frequent (4 >= 3)
    again = score_f3(vendor_profile("kappa supply inc"), b, params, exclude="X-000")
    assert again is not None and again.n_vend == 9


def test_f3_group_leave_one_out_can_dissolve_the_group(params):
    rows = vendor_rows()[:3] + [deal_row("X-200", iso_day(9), equipment_vendor="Other Co", vendor_remit_acct_last4="5")]
    b = Baseline.from_rows(rows, params=params)
    assert score_f3(vendor_profile("kappa supply inc"), b, params) is not None
    assert score_f3(vendor_profile("kappa supply inc"), b, params, exclude="X-000") is None


def f5_history(params):
    deals, deps = [], []
    for i in range(10):
        deals.append(deal_row(f"X-{i:03d}", iso_day(i)))
        deps += deposit_rows(f"X-{i:03d}", [(10, round(0.1 + 0.02 * i, 2), "Mon-Fri")] * 6)
    return Baseline.from_rows(deals, deps, params)


REF = {"round": [0.2, 0.4, 0.6, 0.8], "conc": [-2.6, -2.4, -2.2, -2.0, -1.8], "kl": [0.1, 0.2, 0.3]}


def deposits_profile(count, share, label):
    return DealProfile("q", {}, deposit_features([
        {"deposit_count": count, "pct_deposits_multiple_of_100": share, "deposit_weekdays": label}]))


def test_f5_statistics_match_formulas(params):
    b = f5_history(params)
    prof = deposits_profile("400", "0.5", "Mon,Thu")
    st = f5_statistics(prof, b, params)
    a, bb = fit_beta_mom([round(0.1 + 0.02 * i, 2) for i in range(10)])
    assert st["round"] == pytest.approx(shrunk_share(200, 400, a, bb))
    al = params.dirichlet_alpha
    q = [(m + al) / (400 + 7 * al) for m in (200, 0, 0, 200, 0, 0, 0)]
    assert st["conc"] == pytest.approx(sum(p * math.log2(p) for p in q))
    pooled = [120.0] * 5 + [0.0, 0.0]
    p0 = [(m + al) / (600 + 7 * al) for m in pooled]
    assert st["kl"] == pytest.approx(sum(p * math.log2(p / r) for p, r in zip(q, p0)))
    assert f5_statistics(DealProfile("q", {}), b, params) is None


def test_f5_shrinkage_and_concentration(params):
    b = f5_history(params)
    big = score_f5(deposits_profile("400", "1.0", "Mon-Fri"), b, params, REF)
    small = score_f5(deposits_profile("4", "1.0", "Mon-Fri"), b, params, REF)
    assert big.stat_bits["round"] > small.stat_bits["round"]  # same raw share, more evidence
    two = score_f5(deposits_profile("200", "0.2", "Mon,Thu"), b, params, REF)
    five = score_f5(deposits_profile("200", "0.2", "Mon-Fri"), b, params, REF)
    assert two.stat_bits["conc"] > five.stat_bits["conc"]
    assert two.bits == max(two.stat_bits.values()) == two.stat_bits[two.stat]
    assert score_f5(DealProfile("q", {}), b, params, REF) is None
    assert score_f5(deposits_profile("4", "1.0", "Mon-Fri"), b, params, {}) is None


def test_compute_signals_f3_f5_fired_rule(cfg, params):
    deps = []
    for i in range(9):
        deps += deposit_rows(f"X-{i:03d}", [(10, round(0.1 + 0.02 * i, 2), "Mon-Fri")] * 6)
    b = Baseline.from_rows(vendor_rows(), deps, params)
    months = [{"deposit_count": "400", "pct_deposits_multiple_of_100": "1.0", "deposit_weekdays": "Mon,Thu"}]
    cur = DealState(
        folder="cur",
        entities=[EntityRef("vendor", "Kappa Supply Inc", "d"), EntityRef("remit_account", "1111", "d")],
        features={"deposit_months": months},
    )
    states = {"cur": cur, "idle": DealState(folder="idle")}

    def run(calibration):
        b.calibration = calibration
        g = build_graph(states, b)
        return {s.family: s for s in compute_signals(g, states, b, cfg, NOW)}

    sigs = run(Calibration(tau={"F3": 0.0, "F5": 1.0}, reference=REF))
    assert set(sigs) == {"F3", "F5"}
    assert sigs["F3"].fired and sigs["F3"].deals == ("cur",) and sigs["F3"].bits == pytest.approx(math.log2(10))
    assert sigs["F5"].fired and sigs["F5"].bits >= 1.0
    high = run(Calibration(tau={"F3": 99.0, "F5": 99.0}, reference=REF))
    assert not high["F3"].fired and not high["F5"].fired
    untuned = run(Calibration(reference=REF))  # no tau: never fires, never raises
    assert not untuned["F3"].fired and not untuned["F5"].fired
    assert set(run(Calibration())) == {"F3"}  # no F5 reference: nothing to compare against
    ordered = compute_signals(build_graph(states, b), states, b, cfg, NOW)
    assert [(s.deals[0], s.family) for s in ordered] == sorted((s.deals[0], s.family) for s in ordered)
    # junk features are skipped, not fatal
    cur.features["deposit_months"] = [None, 3, {"deposit_count": object()}]
    assert "F5" not in run(Calibration(tau={"F5": 0.0}, reference=REF))
