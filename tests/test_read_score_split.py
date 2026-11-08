"""score_split: tuning-only output; the holdout is only ever one aggregate line."""
from __future__ import annotations

import json

import pytest

from prism.engine.score import score_submission
from tests.real_data import load_score_split

ss = load_score_split()

EXT = {"legal_name": "A Co", "ein": None, "amount_requested": 10, "purpose_category": None,
       "product": None, "term_months": None, "guarantor_name": None, "annual_revenue_stated": None,
       "needed_by": None}


def _gold():
    deal = {"status": "done", "extracted": EXT, "issues": [], "routing": {}, "final_stage": None,
            "fraud": {"flagged": False, "held": False, "linked_deals": []}}
    return {"deals": {"Tune One": deal, "Tune Two": deal, "Hold Secret": deal},
            "history_flags": [], "history_must_not_flag": []}


def _sub(bad=()):
    deals = {}
    for n in ("Tune One", "Tune Two", "Hold Secret"):
        ext = dict(EXT)
        if n in bad:
            ext["legal_name"] = "Wrong"
        deals[n] = {"extracted": ext, "issues": [], "stage_log": [], "human_requests": [],
                    "alerts": [], "fraud": {"flagged": False, "held": False, "linked_deals": []}}
    return {"deals": deals, "history_flags": []}


def _files(tmp_path, bad=()):
    g, s, h = tmp_path / "g.json", tmp_path / "s.json", tmp_path / "h.json"
    g.write_text(json.dumps(_gold()))
    s.write_text(json.dumps(_sub(bad)))
    h.write_text(json.dumps({"holdout": ["Hold Secret"]}))
    return g, s, h


def test_default_output_has_no_holdout(tmp_path, capsys):
    g, s, h = _files(tmp_path, bad=("Hold Secret", "Tune Two"))
    assert ss.main([str(s), "--gold", str(g), "--holdout", str(h)]) == 0
    out = capsys.readouterr().out
    assert "Tune One" in out and "Tune Two" in out
    assert "Hold Secret" not in out and "HOLDOUT" not in out
    assert "TUNING facts 17/18" in out


def test_holdout_summary_is_one_aggregate_line(tmp_path, capsys):
    g, s, h = _files(tmp_path, bad=("Hold Secret",))
    ss.main([str(s), "--gold", str(g), "--holdout", str(h), "--holdout-summary"])
    out = capsys.readouterr().out
    assert [ln for ln in out.splitlines() if "HOLDOUT" in ln] == ["HOLDOUT facts 8/9 issues_exact 1/1 fp0 fn0"]
    assert "Hold Secret" not in out


def test_no_option_prints_holdout_rows():
    with pytest.raises(SystemExit):
        ss.main(["x.json", "--holdout-detail"])


def test_tuning_summary_equals_manual_score():
    gold, sub = _gold(), _sub(("Tune One",))
    manual = score_submission(sub, {**gold, "deals": {k: v for k, v in gold["deals"].items() if k != "Hold Secret"}})
    res = ss.score_tuning(sub, gold, ["Hold Secret"])
    assert res["totals"] == manual["totals"]
    assert ss.tuning_summary(res).startswith("TUNING facts 17/18")


def test_fields_restriction():
    res = ss.score_tuning(_sub(("Tune One",)), _gold(), ["Hold Secret"], ["legal_name"])
    assert res["totals"]["extracted"] == {"ok": 1, "total": 2}
