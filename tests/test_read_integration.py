"""Post-merge checks that the wave-2 Read plans could not make alone (03-04 Task 1).

Synthetic cases cover the seams between plans (statements to Fraud's deposit features, compare to Move's
borrower message) and stay useful without data. Real-data cases use the sample and look at tuning deals
only (D-02): nothing here opens or asserts on a holdout deal.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prism.contract import ISSUE_RE
from prism.engine.config import Rules
from prism.engine.run import run_sim
from prism.engine.state import DealState, Doc
from prism.engine.writer import build_submission, dumps
from prism.fraud.baseline import deposit_features
from prism.fraud.rarity import parse_weekdays
from prism.move import messages
from prism.read import docmodel
from prism.read import issues as read_issues
from prism.read.classify import DOC_CLASSES, classify
from prism.read.extract import extract_entities, extract_facts, extract_features
from tests.read_fixtures import BASE, make_state
from tests.real_data import ROOT, requires_sample, sample_root, tuning_folders
from tests.statement_fixtures import DEFAULT_TXNS, write_statement
from tests.test_read_forms import ID_ROWS

TZ = timezone(timedelta(hours=-6))
MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
OLD_TRICK_CODES = ("OLD_TRICK:ID_MISMATCH", "OLD_TRICK:DUP_EIN", "OLD_TRICK:SHELL_ADDRESS")


def _read_all(state: DealState, rules=None) -> DealState:
    """What run._ingest does after classification: facts, entities, features, then issues."""
    rules = rules or Rules()
    state.facts = extract_facts(state, rules)
    state.entities = extract_entities(state, rules)
    state.features.update(extract_features(state, rules))
    return state


# ---------------------------------------------------------------- synthetic seams

def _with_statements(tmp_path: Path, months=MONTHS) -> DealState:
    state = make_state(tmp_path, folder="Deal")
    folder_dir = tmp_path / "Deal"
    opening = 1_000_000
    for i, m in enumerate(months):
        name = f"stmt_{m}.pdf"
        opening = write_statement(folder_dir, name, m, DEFAULT_TXNS, opening=opening)
        doc = Doc(path=f"Deal/{name}", name=name, arrive_at=BASE + timedelta(minutes=100 + i),
                  abs_path=folder_dir / name)
        classify(doc)
        state.docs.append(doc)
    state.docs.sort(key=lambda d: (d.arrive_at, d.path))
    return state


def test_every_class_except_other_has_a_registered_parser():
    docmodel.clear_cache()
    docmodel._load_modules()
    wanted = {c for c in DOC_CLASSES if c != "other"}
    assert wanted <= set(docmodel._PARSERS)


def test_statement_months_feed_the_fraud_deposit_features_and_the_annualized_figure(tmp_path):
    state = _read_all(_with_statements(tmp_path))
    rows = state.features["deposit_months"]
    assert [r["month"] for r in rows] == MONTHS
    feats = deposit_features(rows)
    assert feats is not None and feats.months_used == len(rows)
    for r in rows:
        assert parse_weekdays(r["deposit_weekdays"]) is not None
    profile = state.features["profile"]
    assert profile["statement_months"] == len(MONTHS)
    six_month_cents = sum(round(r["total_deposits"] * 100) for r in rows[-6:])
    assert profile["deposits_annualized_cents"] == 2 * six_month_cents


def test_fewer_than_six_months_gives_rows_but_no_annualized_figure(tmp_path):
    state = _read_all(_with_statements(tmp_path, MONTHS[:5]))
    assert len(state.features["deposit_months"]) == 5
    assert "deposits_annualized_cents" not in state.features["profile"]
    assert state.features["profile"]["statement_months"] == 5


def test_ein_mismatch_reaches_the_profile_and_the_borrower_message(tmp_path):
    state = make_state(tmp_path, app_over={"ein": "11-2223334"}, folder="Deal")
    _read_all(state)
    state.last_arrival_at = max(d.arrive_at for d in state.docs)
    found = read_issues.detect_issues(state, Rules(), state.last_arrival_at + timedelta(minutes=30))
    assert "MISMATCH:ein" in found
    ev = state.features["profile"]["mismatch_evidence"]["ein"]
    assert ev["application"] == "11-2223334" and ev["value"] == "98-7654321"
    assert ev["document"]  # plain words, not a class code
    assert "_" not in ev["document"]
    text = messages.info_request_message(state, ["MISMATCH:ein"])
    assert "your application shows 11-2223334" in text
    assert f"your {ev['document']} shows 98-7654321" in text


def test_clean_synthetic_deal_has_no_mismatch_and_no_old_trick_alarm(tmp_path):
    state = make_state(tmp_path, parts={"id": [("Address", "20 Sample Lane, Testville, TX 75002")
                                               if isinstance(r, tuple) and r[0] == "Address" else r
                                               for r in ID_ROWS]}, folder="Deal")
    _read_all(state)
    state.last_arrival_at = max(d.arrive_at for d in state.docs)
    found = read_issues.detect_issues(state, Rules(), state.last_arrival_at + timedelta(minutes=30))
    assert not [i for i in found if i.startswith(("MISMATCH:", "OLD_TRICK:"))]
    assert all(ISSUE_RE.fullmatch(i) for i in found)


# ---------------------------------------------------------------- real tuning deals

@pytest.fixture(scope="module")
def sample_run():
    """One full run over the sample with a spy on detect_issues (tuning deals are the only ones asserted on)."""
    root = sample_root()
    if root is None:
        pytest.skip("sample data not present")
    calls: list[dict] = []
    real = read_issues.detect_issues

    def spy(state, rules, now):
        before = list(state.issues)
        last = state.last_arrival_at
        out = real(state, rules, now)
        calls.append({"folder": state.folder, "now": now, "last": last, "before": before, "out": list(out)})
        return out

    mp = pytest.MonkeyPatch()
    mp.setattr(read_issues, "detect_issues", spy)
    try:
        result = run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)
    finally:
        mp.undo()
    return result, calls


@pytest.fixture(scope="module")
def gold():
    return json.loads((ROOT / "eval" / "gold.json").read_text(encoding="utf-8"))["deals"]


@requires_sample
def test_tuning_issues_equal_the_answer_key_with_zero_false_alarms(sample_run, gold):
    result, _calls = sample_run
    root = sample_root()
    folders = tuning_folders(root)
    assert folders
    for folder in folders:
        g = gold[folder]
        want = set(g["issues"])
        optional = set(g.get("issues_optional") or [])
        got = set(result.states[folder].issues)
        assert want <= got, "a gold issue is missing"
        assert got <= want | optional, "a false alarm"
        assert result.states[folder].error is None


@requires_sample
def test_old_trick_checks_without_a_tuning_gold_case_stay_quiet_on_tuning_deals(sample_run, gold):
    result, _calls = sample_run
    for folder in tuning_folders(sample_root()):
        wanted = set(gold[folder]["issues"])
        got = set(result.states[folder].issues)
        for code in OLD_TRICK_CODES:
            if code not in wanted:
                assert code not in got


@requires_sample
def test_every_classified_tuning_document_has_a_parser_result(sample_run):
    result, _calls = sample_run
    seen = set()
    for folder in tuning_folders(sample_root()):
        for doc in result.states[folder].docs:
            if doc.doc_class and doc.doc_class != "other":
                seen.add(doc.doc_class)
                assert docmodel.parsed(doc) is not None, doc.doc_class
    assert seen  # the loop looked at something


@requires_sample
def test_statement_derived_features_are_real_on_tuning_deals(sample_run):
    result, _calls = sample_run
    with_rows = 0
    for folder in tuning_folders(sample_root()):
        f = result.states[folder].features
        rows = f.get("deposit_months") or []
        profile = f.get("profile") or {}
        if not rows:
            continue
        with_rows += 1
        feats = deposit_features(rows)
        assert feats is not None and feats.months_used == len(rows)
        assert profile.get("statement_months") == len(rows)
        for r in rows:
            assert parse_weekdays(r.get("deposit_weekdays")) is not None
        annual = profile.get("deposits_annualized_cents")
        if len(rows) >= 6:
            assert annual == 2 * sum(round(r["total_deposits"] * 100) for r in rows[-6:])
        else:
            assert annual is None
    assert with_rows >= 1


@requires_sample
def test_tuning_mismatch_deals_carry_evidence_into_the_borrower_message(sample_run, gold):
    result, _calls = sample_run
    checked = 0
    for folder in tuning_folders(sample_root()):
        fields = [i.split(":", 1)[1] for i in gold[folder]["issues"] if i.startswith("MISMATCH:")]
        if not fields:
            continue
        state = result.states[folder]
        evidence = (state.features.get("profile") or {}).get("mismatch_evidence") or {}
        assert sorted(evidence) == sorted(fields)
        text = messages.info_request_message(state, [f"MISMATCH:{f}" for f in fields])
        for f in fields:
            e = evidence[f]
            assert f"your application shows {e['application']}" in text
            assert str(e["value"]) in text
        checked += 1
    assert checked >= 1


@requires_sample
def test_missing_doc_never_appears_before_the_folder_has_been_quiet_30_minutes(sample_run):
    _result, calls = sample_run
    quiet = timedelta(minutes=30)
    tuning = set(tuning_folders(sample_root()))
    early = 0
    for c in calls:
        if c["folder"] not in tuning or c["last"] is None or c["now"] - c["last"] >= quiet:
            continue
        early += 1
        new = [i for i in c["out"] if i.startswith(("MISSING_DOC:", "MISSING_PAGE:")) and i not in c["before"]]
        assert new == []
    assert early >= 1


@requires_sample
def test_two_runs_give_the_same_submission(sample_run):
    result, _calls = sample_run
    root = sample_root()
    again = run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)

    def dump(r):
        return dumps(build_submission(r.states, r.history_flags, r.errors, team="t", set_name="s"))

    assert dump(result) == dump(again)
