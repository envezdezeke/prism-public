"""Self-scorer semantics (eval/README.md). Toy data only, invented names."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from prism.cli import main
from prism.engine.score import format_table, run_score, score_submission, values_match

ROOT = Path(__file__).resolve().parents[1]

EXTRACTED = {
    "legal_name": "Acme Widgets LLC", "ein": "12-3456789", "amount_requested": 120000,
    "purpose_category": "equipment", "product": "Equipment Loan", "term_months": 60,
    "guarantor_name": "Jane Q. Example", "annual_revenue_stated": 1500000,
    "needed_by": "2026-11-06T15:00-06:00",
}


def gold_deal(**over):
    d = {
        "status": "done",
        "extracted": dict(EXTRACTED),
        "extracted_accept": {},
        "issues": ["MISSING_DOC:tax_return"],
        "issues_optional": ["MISMATCH:ein"],
        "routing": {"credit_decision": "p.credit", "committee_approval": None,
                    "commitment_signoff": None, "funding_release": None},
        "final_stage": "Credit",
        "fraud": {"flagged": False, "held": False, "latest_ok_stage": None, "linked_deals": []},
    }
    d.update(over)
    return d


def sub_deal(**over):
    d = {
        "extracted": dict(EXTRACTED),
        "issues": ["MISSING_DOC:tax_return"],
        "stage_log": [{"stage": "Intake", "entered_at": "2026-11-02T09:00-06:00"},
                      {"stage": "Credit", "entered_at": "2026-11-02T10:00-06:00"}],
        "human_requests": [{"type": "credit_decision", "to": "p.credit", "at": "2026-11-02T11:00-06:00"}],
        "alerts": [],
        "fraud": {"flagged": False, "held": False},
    }
    d.update(over)
    return d


def gold_of(**deals):
    return {"deals": deals, "history_flags": ["H-1"], "history_must_not_flag": ["H-9"]}


def sub_of(deals, flags=("H-1",)):
    return {"team": "T", "set": "s", "deals": deals, "history_flags": list(flags)}


def score(gold_deals, sub_deals, flags=("H-1",)):
    return score_submission(sub_of(sub_deals, flags), gold_of(**gold_deals))


def test_identical_scores_all_ok():
    r = score({"Acme": gold_deal()}, {"Acme": sub_deal()})
    d = r["deals"]["Acme"]
    assert all(c["ok"] for c in d["extracted"].values())
    assert d["issues"]["ok"] and d["final_stage"]["ok"]
    assert all(c["ok"] for c in d["routing"].values())
    assert d["fraud"]["flagged"]["ok"] and d["fraud"]["held"]["ok"] and d["fraud"]["linked_deals"]["ok"]
    assert "latest_ok_stage" not in d["fraud"]
    assert r["history"]["ok"]
    assert r["totals"]["extracted"] == {"ok": 9, "total": 9}


def test_value_matching():
    assert values_match("ACME LLC ", "acme llc")
    assert values_match(5, 5.0) and not values_match(5, 6)
    assert values_match(None, None)
    assert not values_match(None, "x") and not values_match("x", None)
    assert not values_match("5", 5) and not values_match(True, 1)


def test_extracted_trim_casefold_and_accept():
    g = gold_deal(extracted_accept={"annual_revenue_stated": [1450000]})
    s = sub_deal()
    s["extracted"].update(legal_name="  ACME WIDGETS llc ", annual_revenue_stated=1450000,
                          ein="12-3456780")
    r = score({"Acme": g}, {"Acme": s})["deals"]["Acme"]["extracted"]
    assert r["legal_name"]["ok"] and r["annual_revenue_stated"]["ok"]
    assert not r["ein"]["ok"] and r["ein"]["got"] == "12-3456780"


def test_issues_missed_false_alarms_and_optional():
    s = sub_deal(issues=["MISMATCH:ein", "MISMATCH:legal_name"])  # optional ignored, one false alarm
    r = score({"Acme": gold_deal()}, {"Acme": s})["deals"]["Acme"]["issues"]
    assert r["missed"] == ["MISSING_DOC:tax_return"]
    assert r["false_alarms"] == ["MISMATCH:legal_name"]
    assert not r["ok"]
    r2 = score({"Acme": gold_deal()}, {"Acme": sub_deal(issues=["MISSING_DOC:tax_return", "MISMATCH:ein"])})
    assert r2["deals"]["Acme"]["issues"]["ok"]


def test_routing_first_request_by_time_ignores_borrower():
    reqs = [
        {"type": "credit_decision", "to": "borrower", "at": "2026-11-02T08:00-06:00"},
        {"type": "credit_decision", "to": "p.late", "at": "2026-11-03T11:00-06:00"},
        {"type": "credit_decision", "to": "p.credit", "at": "2026-11-02T11:00-06:00"},
    ]
    r = score({"Acme": gold_deal()}, {"Acme": sub_deal(human_requests=reqs)})["deals"]["Acme"]["routing"]
    assert r["credit_decision"]["ok"] and r["credit_decision"]["got"] == "p.credit"


def test_routing_null_gate_passes_only_with_no_request():
    g = gold_deal(routing={"credit_decision": None, "committee_approval": None,
                           "commitment_signoff": None, "funding_release": None})
    ok = score({"A": g}, {"A": sub_deal(human_requests=[])})["deals"]["A"]["routing"]
    assert all(c["ok"] for c in ok.values())
    bad = score({"A": g}, {"A": sub_deal()})["deals"]["A"]["routing"]
    assert not bad["credit_decision"]["ok"] and bad["credit_decision"]["got"] == "p.credit"


def test_final_stage_is_furthest_in_log():
    log = [{"stage": "Committee"}, {"stage": "Intake"}, {"stage": "Credit"}]
    r = score({"A": gold_deal(final_stage="Committee")}, {"A": sub_deal(stage_log=log)})
    assert r["deals"]["A"]["final_stage"] == {"ok": True, "got": "Committee", "want": "Committee"}
    r = score({"A": gold_deal(final_stage=None)}, {"A": sub_deal(stage_log=[])})
    assert r["deals"]["A"]["final_stage"] == {"ok": True, "got": None, "want": None}


def test_fraud_flags_timing_and_links():
    gf = {"flagged": True, "held": True, "latest_ok_stage": "Credit", "linked_deals": ["B", "H-1"]}
    g = gold_deal(fraud=gf)
    on_time = sub_deal(fraud={"flagged": True, "held": True, "stage_at_flag": "Intake",
                              "flagged_at": "2026-11-02T09:30-06:00", "linked_deals": ["H-1", "B"]})
    f = score({"A": g}, {"A": on_time})["deals"]["A"]["fraud"]
    assert f["flagged"]["ok"] and f["held"]["ok"] and f["latest_ok_stage"]["ok"] and f["linked_deals"]["ok"]
    late = copy.deepcopy(on_time)
    late["fraud"]["stage_at_flag"] = "Committee"
    late["fraud"]["linked_deals"] = ["B", "C"]
    f = score({"A": g}, {"A": late})["deals"]["A"]["fraud"]
    assert not f["latest_ok_stage"]["ok"]
    assert f["linked_deals"] == {"ok": False, "missed": ["H-1"], "extra": ["C"]}
    nostage = copy.deepcopy(on_time)
    nostage["fraud"]["stage_at_flag"] = None
    assert not score({"A": g}, {"A": nostage})["deals"]["A"]["fraud"]["latest_ok_stage"]["ok"]


def test_false_flag_and_missed_flag():
    s_flag = sub_deal(fraud={"flagged": True, "held": True, "stage_at_flag": "Intake"})
    f = score({"A": gold_deal()}, {"A": s_flag})["deals"]["A"]["fraud"]
    assert not f["flagged"]["ok"] and not f["held"]["ok"]
    g = gold_deal(fraud={"flagged": True, "held": True, "latest_ok_stage": "Credit", "linked_deals": []})
    f = score({"A": g}, {"A": sub_deal()})["deals"]["A"]["fraud"]
    assert not f["flagged"]["ok"] and not f["latest_ok_stage"]["ok"]
    table = format_table(score({"A": gold_deal()}, {"A": s_flag}))
    assert "FALSE FLAG" in table
    assert "MISSED" in format_table(score({"A": g}, {"A": sub_deal()}))


def test_status_skip_and_absent_deal_all_miss():
    r = score({"Todo Co": gold_deal(status="todo"), "Gone Co": gold_deal(status="draft")}, {})
    assert r["unscored"] == ["Todo Co"]
    d = r["deals"]["Gone Co"]
    assert not any(c["ok"] for c in d["extracted"].values())
    assert d["issues"]["missed"] == ["MISSING_DOC:tax_return"]
    assert not d["final_stage"]["ok"] and not d["routing"]["credit_decision"]["ok"]
    assert r["totals"]["deals"] == 1


def test_history_flags_set_compare_and_must_not():
    r = score({"A": gold_deal()}, {"A": sub_deal()}, flags=["H-9", "H-2"])
    h = r["history"]
    assert h["missed"] == ["H-1"] and h["extra"] == ["H-2", "H-9"]
    assert h["must_not_flagged"] == ["H-9"] and not h["ok"]
    assert "MISMATCH" in format_table(r)


def test_table_is_ascii_and_truncates_long_names():
    name = "A Very Long Deal Folder Name That Overflows"
    table = format_table(score({name: gold_deal()}, {name: sub_deal()}))
    table.encode("ascii")
    assert name not in table and name[:20] in table


def test_cli_score_writes_reproducible_file(tmp_path, capsys):
    sub = tmp_path / "sub.json"
    gold = tmp_path / "gold.json"
    sub.write_text(json.dumps(sub_of({"Acme": sub_deal()})), encoding="utf-8")
    gold.write_text(json.dumps(gold_of(Acme=gold_deal())), encoding="utf-8")
    out = tmp_path / "o" / "score.json"
    assert main(["score", str(sub), "--gold", str(gold), "--out", str(out)]) == 0
    assert "TOTAL" in capsys.readouterr().out
    first = out.read_bytes()
    assert json.loads(first)["totals"]["deals"] == 1
    run_score(sub, gold, out)
    assert out.read_bytes() == first


def test_cli_score_missing_inputs_and_no_scored_deals(tmp_path):
    sub = tmp_path / "sub.json"
    gold = tmp_path / "gold.json"
    sub.write_text(json.dumps(sub_of({})), encoding="utf-8")
    assert main(["score", str(sub), "--gold", str(gold)]) == 2
    gold.write_text(json.dumps(gold_of(Acme=gold_deal(status="todo"))), encoding="utf-8")
    assert main(["score", str(sub), "--gold", str(gold), "--out", str(tmp_path / "x.json")]) == 2
    assert main(["score", str(tmp_path / "nope.json"), "--gold", str(gold)]) == 2
