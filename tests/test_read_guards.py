"""Guards: no sample literals in Read code, config or tests; quiet windows agree."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _gold_literals() -> list[str]:
    gold = json.loads((ROOT / "eval" / "gold.json").read_text(encoding="utf-8"))
    out: set[str] = set()
    for folder, deal in (gold.get("deals") or {}).items():
        out.add(folder)
        ext = deal.get("extracted") or {}
        for key in ("legal_name", "guarantor_name", "ein"):
            if isinstance(ext.get(key), str):
                out.add(ext[key])
    return sorted(x.strip().casefold() for x in out if len(x.strip()) >= 4)


def _guarded_files() -> list[Path]:
    files = sorted((ROOT / "prism" / "read").glob("*.py"))
    files += sorted((ROOT / "config").glob("read*.yaml"))
    files += sorted((ROOT / "tests").glob("test_read*.py"))
    files.append(ROOT / "tests" / "pdfgen.py")
    return files


def test_no_sample_literals_in_read_code_config_or_tests():
    if not (ROOT / "eval" / "gold.json").is_file():
        pytest.skip("gold not present")
    literals = _gold_literals()
    hits: list[str] = []
    for p in _guarded_files():
        body = p.read_text(encoding="utf-8").casefold()
        n = sum(1 for lit in literals if lit in body)
        if n:
            hits.append(f"{p.name}: {n} sample literal(s)")  # never print the matched value
    assert hits == [], "; ".join(hits)


def test_read_quiet_minutes_equals_move_quiet_minutes():
    read = yaml.safe_load((ROOT / "config" / "read.yaml").read_text(encoding="utf-8"))
    move = yaml.safe_load((ROOT / "config" / "move.yaml").read_text(encoding="utf-8"))
    assert read["quiet_minutes"] == move["quiet_minutes"]


def test_guard_scans_the_expected_files():
    names = {p.name for p in _guarded_files()}
    assert {"classify.py", "read.yaml", "pdfgen.py", "test_read_guards.py"} <= names
