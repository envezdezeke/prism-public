"""Tests for eval/drop_labelling/check_labels.py. Fake values only."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent / "eval" / "drop_labelling"
if not (_DIR / "check_labels.py").is_file():
    pytest.skip("eval/ is not in the public copy", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("check_labels", _DIR / "check_labels.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


@pytest.fixture
def tmpl():
    return json.loads((_DIR / "gold_drop.TEMPLATE.json").read_text(encoding="utf-8"))


@pytest.fixture
def filled(tmpl):
    g = copy.deepcopy(tmpl)
    g["deals"] = {"X2 Fake Bakery": g["deals"]["X-Example Deal"]}
    return g


def test_template_passes_with_allow_example(tmpl):
    assert cl.check(tmpl, allow_example=True) == []


def test_template_fails_until_example_replaced(tmpl):
    assert any("placeholder" in m for m in cl.check(tmpl))


def test_filled_passes(filled):
    assert cl.check(filled) == []


def test_ring_example_passes(filled):
    d2 = copy.deepcopy(filled["deals"]["X2 Fake Bakery"])
    for d, other in ((filled["deals"]["X2 Fake Bakery"], "X3 Fake Gym"), (d2, "X2 Fake Bakery")):
        d["fraud"] = {"flagged": True, "held": True, "latest_ok_stage": "Credit",
                      "linked_deals": [other, "H-9901-001"]}
    filled["deals"]["X3 Fake Gym"] = d2
    filled["history_flags"] = ["H-9901-001"]
    filled["history_must_not_flag"] = ["H-9901-002"]
    assert cl.check(filled) == []


@pytest.mark.parametrize("path,val", [
    (("extracted", "purpose_category"), "vacation"),
    (("extracted", "product"), "Mortgage"),
    (("final_stage",), "Done"),
    (("status",), "finished"),
    (("extracted", "ein"), "123456789"),
    (("extracted", "amount_requested"), "$120,000"),
])
def test_bad_enum_or_format_fails(filled, path, val):
    d = filled["deals"]["X2 Fake Bakery"]
    t = d
    for p in path[:-1]:
        t = t[p]
    t[path[-1]] = val
    assert cl.check(filled)


@pytest.mark.parametrize("code", [
    "MISSING:bank_statement_2026-06", "MISSING_DOC:bank_statement", "MISMATCH:address",
    "OLD_TRICK:FAKE_ID", "missing_doc:tax_return", "MISMATCH:ein ",
])
def test_bad_issue_code_fails(filled, code):
    filled["deals"]["X2 Fake Bakery"]["issues"] = [code]
    assert any("bad issue code" in m for m in cl.check(filled))


@pytest.mark.parametrize("t", ["2026-11-06 15:00-06:00", "2026-11-06T15:00", "2026-11-06T15:00-05:00",
                               "No specific date", "Nov 6"])
def test_bad_time_fails(filled, t):
    filled["deals"]["X2 Fake Bakery"]["extracted"]["needed_by"] = t
    assert any("needed_by" in m for m in cl.check(filled))


def test_good_time_passes(filled):
    filled["deals"]["X2 Fake Bakery"]["extracted"]["needed_by"] = "2026-11-06T15:00-06:00"
    assert cl.check(filled) == []


def test_old_trick_requires_hold(filled):
    filled["deals"]["X2 Fake Bakery"]["issues"] = ["OLD_TRICK:DUP_EIN"]
    assert any("J17" in m for m in cl.check(filled))


def test_flagged_needs_latest_ok_stage(filled):
    f = filled["deals"]["X2 Fake Bakery"]["fraud"]
    f.update(flagged=True, held=True)
    assert cl.check(filled)


def test_history_overlap_fails(filled):
    filled["history_flags"] = ["H-9901-001"]
    filled["history_must_not_flag"] = ["H-9901-001"]
    assert cl.check(filled)


def test_cli_exit_codes(tmp_path, filled):
    p = tmp_path / "g.json"
    p.write_text(json.dumps(filled))
    assert cl.main([str(p)]) == 0
    filled["deals"]["X2 Fake Bakery"]["final_stage"] = "Nope"
    p.write_text(json.dumps(filled))
    assert cl.main([str(p)]) == 1
    assert cl.main([str(tmp_path / "missing.json")]) == 2
