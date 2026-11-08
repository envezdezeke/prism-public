"""Pure-function tests for scripts/seal_blind_run.py. Synthetic names only; no data/ needed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seal_blind_run.py"
_spec = importlib.util.spec_from_file_location("seal_blind_run", _SCRIPT)
seal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seal)

NAMES = ["Zeta Holdings", "Omega Freight", "Kappa Foods", "Sigma Tools", "Delta Clinic"]
HIST = ["H-9001", "H-9002", "H-9003"]


def _sub():
    def deal(held, flagged, stage_at, stages):
        return {
            "fraud": {"held": held, "flagged": flagged, "stage_at_flag": stage_at},
            "stage_log": [{"stage": s} for s in stages],
        }

    return {
        "deals": {
            NAMES[0]: deal(True, True, "Credit", ["Intake", "Credit"]),
            NAMES[1]: deal(True, True, "Intake", ["Intake", "Credit", "Closed"]),
            NAMES[2]: deal(False, True, "Credit", ["Intake", "Credit"]),
            NAMES[3]: deal(False, False, None, ["Intake", "Credit", "Committee", "Closed"]),
            NAMES[4]: deal(False, False, None, ["Intake"]),
        },
        "history_flags": [HIST[0], HIST[1]],
    }


def _score():
    def d(flag_want, flag_got, held_got):
        return {"fraud": {"flagged": {"want": flag_want, "got": flag_got, "ok": flag_want == flag_got},
                          "held": {"want": flag_want, "got": held_got, "ok": flag_want == held_got}}}

    return {
        "deals": {
            NAMES[0]: d(True, True, True),    # gold flagged, held
            NAMES[1]: d(True, True, True),    # gold flagged, held (but reached Closed)
            NAMES[2]: d(True, True, False),   # gold flagged, flagged not held
            NAMES[3]: d(False, False, False),
            NAMES[4]: d(False, True, False),  # false flag
        },
        "history": {"missed": [HIST[2]], "extra": [HIST[1]], "must_not_flagged": [HIST[1]], "ok": False},
    }


def test_aggregate_submission_counts_only():
    agg = seal.aggregate_submission(_sub())
    assert agg == {"deals": 5, "held": 2, "flagged": 3, "history_flags": 2}


def test_refusal_reason_cases():
    ok = dict(record_exists=False, gold_exists=False, tree_dirty=False, tag=None, tag_commit=None, head="a" * 40)
    assert seal.refusal_reason(**ok) is None
    assert seal.refusal_reason(**{**ok, "record_exists": True})
    assert seal.refusal_reason(**{**ok, "gold_exists": True})
    assert seal.refusal_reason(**{**ok, "tree_dirty": True})
    assert seal.refusal_reason(**{**ok, "tag": "v1", "tag_commit": "b" * 40})
    assert seal.refusal_reason(**{**ok, "tag": "v1", "tag_commit": None})  # tag does not resolve
    assert seal.refusal_reason(**{**ok, "tag": "v1", "tag_commit": "a" * 40}) is None


def test_sha256_and_verify(tmp_path):
    p = tmp_path / "f.json"
    p.write_bytes(b"hello")
    want = hashlib.sha256(b"hello").hexdigest()
    assert seal.sha256_file(p) == want
    assert seal.verify_sha(p, want) is True
    p.write_bytes(b"hellO")
    assert seal.verify_sha(p, want) is False


def test_aggregate_score():
    agg = seal.aggregate_score(_score(), _sub())
    assert agg["gold_flagged"] == 3
    assert agg["held_of_gold_flagged"] == 2
    assert agg["false_flags"] == 1
    assert agg["history_missed"] == 1
    assert agg["history_extra"] == 1
    assert agg["history_must_not_flagged"] == 1
    assert agg["stage_at_flag"] == {"Credit": 1, "Intake": 1}
    assert agg["held_before_closed"] == 1


def test_no_names_or_ids_in_aggregates():
    blob = json.dumps(
        {"sub": seal.aggregate_submission(_sub()), "score": seal.aggregate_score(_score(), _sub())}
    )
    for token in NAMES + HIST:
        assert token not in blob


def test_data_dir_output_refused():
    assert seal.inside_data_dir(Path("/x/data/drop/out.json"))
    assert not seal.inside_data_dir(Path("/x/out/blind_drop/out.json"))


def test_parse_run_line():
    assert seal.parse_run_errors("run: 13 deals, 173 files, 5 ticks, 2 errors -> out/x.json") == 2
    assert seal.parse_run_errors("nothing here") is None
