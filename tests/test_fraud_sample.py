"""Fraud end to end on the sample: tuning deals only (D-02), compared with eval/gold.json.

Skipped when the sample data pack is absent. No deal is named here; every expectation is
read from the answer key for the tuning folders.
"""
from __future__ import annotations

import json

import pytest

from prism.contract import parse_sim_time
from prism.engine.config import load_rules
from prism.engine.run import run_sim
from prism.engine.writer import build_submission, dumps
from prism.move import routing
from tests.real_data import ROOT, requires_sample, sample_root, tuning_folders


@pytest.fixture(scope="module")
def sample_run():
    root = sample_root()
    if root is None:
        pytest.skip("sample data not present")
    return run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)


@pytest.fixture(scope="module")
def gold():
    return json.loads((ROOT / "eval" / "gold.json").read_text(encoding="utf-8"))["deals"]


@pytest.fixture(scope="module")
def tuning():
    root = sample_root()
    if root is None:
        pytest.skip("sample data not present")
    folders = tuning_folders(root)
    assert folders
    return folders


def _flagged_gold(gold, tuning):
    return [f for f in tuning if gold[f]["fraud"]["flagged"]]


def _ring_gold(gold, tuning):
    """Gold-flagged tuning deals that have ring partners (the hold-rule path)."""
    return [f for f in _flagged_gold(gold, tuning) if gold[f]["fraud"]["linked_deals"]]


@requires_sample
def test_ring_tuning_deals_are_flagged_and_held(sample_run, gold, tuning):
    ring = _ring_gold(gold, tuning)
    assert ring
    for folder in ring:
        got = sample_run.states[folder].fraud
        assert got.flagged and got.held, "a ring tuning deal is not held"


@requires_sample
def test_good_tuning_deals_are_not_flagged(sample_run, gold, tuning):
    for folder in tuning:
        if not gold[folder]["fraud"]["flagged"]:
            got = sample_run.states[folder].fraud
            assert not got.flagged and not got.held, "a good tuning deal was flagged"


@requires_sample
def test_ring_tuning_deals_are_held_at_intake_in_the_sim_week(sample_run, gold, tuning):
    flagged = _ring_gold(gold, tuning)
    assert flagged
    for folder in flagged:
        st = sample_run.states[folder]
        assert st.fraud.stage_at_flag == "Intake"
        t = parse_sim_time(st.fraud.flagged_at)
        assert st.docs and t >= min(d.arrive_at for d in st.docs)
        assert all(e.stage == "Intake" for e in st.stage_log)


def _old_trick_gold(gold, tuning):
    """Gold-flagged tuning deals with no ring partners (the OLD_TRICK path)."""
    return [f for f in _flagged_gold(gold, tuning) if not gold[f]["fraud"]["linked_deals"]]


@requires_sample
def test_old_trick_tuning_deal_is_flagged_with_no_links(sample_run, gold, tuning):
    deals = _old_trick_gold(gold, tuning)
    assert deals
    for folder in deals:
        st = sample_run.states[folder]
        assert st.fraud.flagged and st.fraud.held
        assert st.fraud.linked_deals == []
        assert any(i.startswith("OLD_TRICK:") for i in st.issues + st.resolved_issues)


@requires_sample
def test_tuning_flag_and_held_match_the_answer_key(sample_run, gold, tuning):
    for folder in tuning:
        want = gold[folder]["fraud"]
        got = sample_run.states[folder].fraud
        assert got.flagged == want["flagged"], "flag differs from the answer key on a tuning deal"
        assert got.held == want["held"], "held differs from the answer key on a tuning deal"



# ---------------------------------------------------------------- Move does the rest (Task 3)

def _first_fraud_decider():
    rules = load_rules(sample_root())
    ids = routing.ids_in(rules.stages.fraud_hold.get("who_decides"), rules.teams)
    assert ids
    return ids[0]


@requires_sample
def test_flagged_tuning_deals_are_frozen_at_intake_with_one_fraud_hold_request(sample_run, gold, tuning):
    flagged = _flagged_gold(gold, tuning)
    assert flagged
    decider = _first_fraud_decider()
    for folder in flagged:
        st = sample_run.states[folder]
        assert st.fraud.stage_at_flag == "Intake"
        assert len(st.stage_log) == 1
        entry = st.stage_log[0]
        assert entry.stage == "Intake" and entry.decision == "held" and entry.exited_at is None
        assert all(e.stage != "Closed" for e in st.stage_log)
        holds = [r for r in st.human_requests if r.type == "fraud_hold" and r.at == st.fraud.flagged_at]
        assert len(holds) == 1, "expected exactly one fraud_hold request at flagged_at"
        first = min((r for r in st.human_requests if r.type == "fraud_hold"),
                    key=lambda r: parse_sim_time(r.at))
        assert first.to == decider and first.at == st.fraud.flagged_at


@requires_sample
def test_good_tuning_deals_get_no_fraud_hold_request(sample_run, gold, tuning):
    for folder in tuning:
        if gold[folder]["fraud"]["flagged"]:
            continue
        st = sample_run.states[folder]
        assert not st.fraud.flagged and not st.fraud.held
        assert st.fraud.stage_at_flag is None
        assert not any(r.type == "fraud_hold" for r in st.human_requests)


@requires_sample
def test_two_sample_runs_give_the_same_submission(sample_run):
    root = sample_root()
    again = run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)

    def dump(r):
        return dumps(build_submission(r.states, r.history_flags, r.errors, team="t", set_name="s"))

    assert dump(sample_run) == dump(again)


@requires_sample
def test_tuning_linked_deals_match_gold(sample_run, gold, tuning):
    """Every tuning deal's linked_deals equals the answer key's set (current peers plus history ids)."""
    for folder in tuning:
        got = set(sample_run.states[folder].fraud.linked_deals)
        assert got == set(gold[folder]["fraud"]["linked_deals"]), "tuning linked_deals differ from gold"


@requires_sample
def test_no_good_tuning_deal_gets_f1_with_f2_and_f4(sample_run, gold, tuning):
    """F2 and F4 add no false votes: no good tuning deal fires F1, F2 and F4 together."""
    from prism.engine.config import load_rules as _load_rules
    from prism.fraud.baseline import build_baseline
    from prism.fraud.graph import build_graph
    from prism.fraud.signals import compute_signals

    root = sample_root()
    rules = _load_rules(root)
    base = build_baseline(rules.history_dir, rules.fraud)
    states = sample_run.states
    sigs = compute_signals(build_graph(states, base), states, base, rules.fraud, None)
    fired = {}
    for s in sigs:
        if s.fired:
            fired.setdefault(s.deals[0], set()).add(s.family)
    for folder in tuning:
        if gold[folder]["fraud"]["flagged"]:
            continue
        assert not {"F1", "F2", "F4"} <= fired.get(folder, set()), "a good tuning deal fires F1+F2+F4"
        assert not states[folder].fraud.flagged
