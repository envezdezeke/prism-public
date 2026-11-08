"""Validator and CLI exit codes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from prism.cli import main
from prism.engine.validate import validate_submission_data, validate_submission_file

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "submission.json"


@pytest.fixture
def sub():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def first_deal(sub):
    return next(iter(sub["deals"]))


def write(tmp_path, data, name="s.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_fixture_validates_clean():
    r = validate_submission_file(FIXTURE)
    assert r.ok and r.errors == []


def test_unexpected_and_missing_deal(sub):
    names = set(sub["deals"])
    r = validate_submission_data(sub, folders=names | {"Gamma Foods"})
    assert "missing deal: Gamma Foods" in r.errors
    sub["deals"]["Rogue Co"] = copy.deepcopy(sub["deals"][first_deal(sub)])
    r = validate_submission_data(sub, folders=names)
    assert "unexpected deal: Rogue Co" in r.errors


def test_empty_or_pathlike_key(sub):
    sub["deals"]["a/b"] = copy.deepcopy(sub["deals"][first_deal(sub)])
    sub["deals"][""] = copy.deepcopy(sub["deals"][first_deal(sub)])
    r = validate_submission_data(sub)
    assert len([e for e in r.errors if "folder name" in e]) == 2


def test_bad_stage_enum(sub):
    sub["deals"][first_deal(sub)]["stage_log"][0]["stage"] = "Review"
    r = validate_submission_data(sub)
    assert any("stage_log" in e for e in r.errors)


def test_bad_issue_grammar(sub):
    sub["deals"][first_deal(sub)]["issues"] = ["MISSING_DOC:passport"]
    r = validate_submission_data(sub)
    assert any("issues" in e for e in r.errors)


def test_misspelled_field(sub):
    deal = sub["deals"][first_deal(sub)]
    deal["stagelog"] = deal.pop("stage_log")
    r = validate_submission_data(sub)
    assert not r.ok and any("stagelog" in e for e in r.errors)


def test_underscore_keys_stripped_and_warned(sub):
    sub["deals"][first_deal(sub)]["_error"] = "extract_facts: RuntimeError: boom"
    sub["_errors"] = ["fraud: ValueError: x"]
    r = validate_submission_data(sub)
    assert r.ok
    assert sum("engine error" in w for w in r.warnings) == 2


def test_q14_are_warnings_only(sub):
    d = sub["deals"][first_deal(sub)]
    d["fraud"] = {"flagged": False, "held": True, "flagged_at": None, "stage_at_flag": None,
                  "reason": None, "linked_deals": []}
    r = validate_submission_data(sub)
    assert r.ok and any("held" in w for w in r.warnings)
    d["fraud"] = {"flagged": True, "held": True, "flagged_at": None, "stage_at_flag": None,
                  "reason": "x", "linked_deals": []}
    r = validate_submission_data(sub)
    assert r.ok and any("flagged_at" in w for w in r.warnings)


def test_top_level_not_object(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert not validate_submission_file(p).ok
    assert main(["validate", str(p)]) == 1


def test_unreadable_json_exit_1(tmp_path, capsys):
    p = tmp_path / "s.json"
    p.write_text("{oops", encoding="utf-8")
    assert main(["validate", str(p)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_missing_file_exit_2(tmp_path):
    assert main(["validate", str(tmp_path / "nope.json")]) == 2


def test_no_subcommand_exit_2(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_validate_exit_codes(tmp_path, capsys, sub):
    assert main(["validate", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "valid" in out and "skipped" in out
    d = sub["deals"][first_deal(sub)]
    d["fraud"]["held"] = True
    d["fraud"]["flagged"] = False
    p = write(tmp_path, sub)
    assert main(["validate", str(p)]) == 0
    assert "WARN:" in capsys.readouterr().out
    sub["deals"][first_deal(sub)]["issues"] = ["MISSING_DOC:passport"]
    assert main(["validate", str(write(tmp_path, sub, "bad.json"))]) == 1


def test_cli_validate_against_folder(tmp_path, tiny_set, capsys):
    folder, arrivals = tiny_set
    out = tmp_path / "out" / "submission.json"
    assert main(["run", "--folder", str(folder), "--arrivals", str(arrivals), "--out", str(out)]) == 0
    assert main(["validate", str(out), "--folder", str(folder)]) == 0
    assert main(["validate", str(out), "--arrivals", str(arrivals)]) == 0
    (folder / "Gamma Foods").mkdir()
    assert main(["validate", str(out), "--folder", str(folder)]) == 1
    assert "missing deal: Gamma Foods" in capsys.readouterr().out
