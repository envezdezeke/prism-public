"""Tests for the synthetic fraud-deal generator and runner (eval/synth.py, eval/run_synth.py).

Everything runs on a tiny FAKE history written to tmp_path: no data/ needed, no sample names.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

if not (Path(__file__).resolve().parent.parent / "eval" / "synth.py").is_file():
    pytest.skip("eval/ is not in the public copy", allow_module_level=True)

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import run_synth  # noqa: E402
import synth  # noqa: E402
from test_fraud_synth import deposit_rows, filler_deals, write_history  # noqa: E402

from prism.contract import Fraud  # noqa: E402
from prism.fraud.decide import FraudOutcome  # noqa: E402

PHONE = re.compile(r"555-01\d\d$")
CORE_MENU = yaml.safe_load((ROOT / "eval" / "trick_menu.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hist_dir(tmp_path_factory):
    """40 clean funded deals, 6 deposit months each, with a few repeated vendors."""
    tmp = tmp_path_factory.mktemp("fakehist")
    deals = filler_deals(40)
    deps = []
    for i, d in enumerate(deals):
        if i % 4 == 0:
            d["equipment_vendor"] = "Fixture Dealer LLC"
            d["vendor_remit_acct_last4"] = f"{8000 + i}"
            d["product"] = "Equipment Loan"
        d["pdf_producer"] = "Fixture Print 1.0" if i % 3 == 0 else d["pdf_producer"]
        d["amount_requested"] = str(40000 + 1000 * i)
        d["industry"] = "Fixtureware"
        deps += deposit_rows(d["deal_id"], [(8 + (i + m) % 3, 0.1 + 0.02 * (m % 3), "Mon-Fri") for m in range(6)])
        for r in deps[-6:]:
            r["total_deposits"], r["largest_deposit"] = str(10000 + 100 * i), "2500"
    return write_history(tmp, deals, deps)


@pytest.fixture(scope="module")
def hist(hist_dir):
    h = synth.load_history(hist_dir)
    assert h is not None
    return h


def _snapshot(cases):
    return [(c.deal_rows, c.deposit_rows, c.planted_rows, c.trick_ids, c.background_ids) for c in cases]


def test_history_absent_returns_none(tmp_path):
    assert synth.load_history(tmp_path / "nope") is None


def test_generation_is_deterministic(hist):
    for cell in synth.cells_for(("core", "redteam")):
        a = synth.generate_cases(cell, hist, 2)
        b = synth.generate_cases(cell, hist, 2)
        assert _snapshot(a) == _snapshot(b), cell.id


def test_seed_changes_output(hist):
    cell = synth.cells_for(("core",))[0]
    a = synth.build_case(cell, 0, hist)
    b = synth.build_case(cell, 1, hist)
    assert a.deal_rows != b.deal_rows


def test_every_menu_trick_and_twin_is_generated(hist):
    cells = synth.cells_for(("core",))
    fam_ids = {f["id"] for f in CORE_MENU["families"]}
    twin_ids = {t["id"] for f in CORE_MENU["families"] for t in f.get("benign_twins", [])}
    assert {c.family for c in cells if c.kind == "ring"} == fam_ids
    assert {c.id for c in cells if c.kind == "twin"} == twin_ids and twin_ids
    for f in CORE_MENU["families"]:
        assert set(f["strengths"]) == {"low", "med", "high"}
    for cell in cells:
        case = synth.generate_cases(cell, hist, 1)[0]
        assert case.trick_ids, cell.id
        assert case.background_ids and len(case.background_ids) == synth.BACKGROUND_DEALS
        n = int(cell.params.get("ring_size", cell.params.get("twin_size", 1)))
        assert len(case.trick_ids) == n, cell.id
        assert len(case.deposit_rows) == 6 * len(case.deal_rows)


def test_redteam_menu_cells_generate(hist):
    cells = synth.cells_for(("redteam",))
    assert {c.family for c in cells} == {"R1", "R2", "R3", "R4"}
    for cell in cells:
        assert synth.generate_cases(cell, hist, 1)[0].trick_ids


def test_ring_shares_what_the_trick_says(hist):
    cells = {c.id: c for c in synth.cells_for(("core",))}
    t1 = synth.build_case(cells["T1-high"], 0, hist)
    rows = [r for r in t1.deal_rows if r["deal_id"] in t1.trick_ids]
    for col in ("preparer_name", "preparer_phone", "vendor_remit_acct_last4", "pdf_producer"):
        assert len({r[col] for r in rows}) == 1, col
    assert len({r["legal_name"] for r in rows}) == len(rows)
    assert len({r["ein"] for r in rows}) == len(rows)
    assert t1.planted_rows  # common-producer filler plus a planted ring member
    t3 = synth.build_case(cells["T3-high"], 0, hist)
    rows = [r for r in t3.deal_rows if r["deal_id"] in t3.trick_ids]
    assert len({r["preparer_phone"] for r in rows}) == len(rows)  # nothing shared


def test_mule_and_format_variants(hist):
    cells = {c.id: c for c in synth.cells_for(("redteam",))}
    r2 = synth.build_case(cells["R2-med"], 0, hist)
    rows = {r["deal_id"]: r for r in r2.deal_rows}
    mule = rows[r2.trick_ids[-1]]
    assert mule["preparer_phone"] == rows[r2.trick_ids[0]]["preparer_phone"]
    assert not mule["equipment_vendor"] and any("mule" in n for n in r2.notes)
    r3 = synth.build_case(cells["R3-med"], 0, hist)
    phones = {r["preparer_phone"] for r in r3.deal_rows if r["deal_id"] in r3.trick_ids}
    assert len(phones) > 1  # typed differently ...
    from prism.fraud.canon import canon_value
    assert len({canon_value("phone", p) for p in phones}) == 1  # ... but one canonical entity


def test_only_fake_values(hist):
    cells = synth.cells_for(("core", "redteam"))
    for cell in cells:
        for case in synth.generate_cases(cell, hist, 1):
            for r in case.deal_rows + case.planted_rows:
                assert r["deal_id"].startswith(("S-", "X-")), r["deal_id"]
                assert PHONE.search(r["preparer_phone"]), r["preparer_phone"]
                assert r["ein"].startswith("00-7"), r["ein"]
                assert "Test National Bank" in r["bank_name"]
                if r["vendor_remit_acct_last4"].strip() and r["equipment_vendor"].find("Fixture") < 0:
                    assert r["vendor_remit_acct_last4"].strip().startswith("9")
                assert "Fixture" not in r["legal_name"] and "Filler" not in r["legal_name"]
                for col, kind in run_synth.HISTORY_COLUMN_KINDS.items():
                    if col == "equipment_vendor":
                        continue
                    v = run_synth.canon_value(kind, r.get(col))
                    assert not v or v not in hist.reserved or col == "pdf_producer", (col, v)


def test_no_data_pack_values_in_output():
    """When the real data pack is on disk, no generated identity value may equal a real one."""
    real = synth.load_history(Path(synth.DEFAULT_HISTORY))
    if real is None:
        pytest.skip("data pack not present")
    for cell in synth.cells_for(("core", "redteam")):
        for case in synth.generate_cases(cell, real, 1):
            for r in case.deal_rows + case.planted_rows:
                for col, kind in run_synth.HISTORY_COLUMN_KINDS.items():
                    v = run_synth.canon_value(kind, r.get(col))
                    # the shared common PDF producer and vendor groups are invented, never real
                    assert not v or v not in real.reserved, (cell.id, col, v)
                for col in ("legal_name", "ein"):
                    v = run_synth.canon_value("preparer", r.get(col))
                    assert not v or v not in real.reserved, (cell.id, col, v)


# ------------------------------------------------------------------ runner

def _report(hist, modes=run_synth.MODES, menus=("core", "redteam")):
    return run_synth.build_report(list(menus), hist, 1, modes)


def test_report_schema_and_determinism(hist):
    a = run_synth._round(_report(hist))
    b = run_synth._round(_report(hist))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["schema_version"] == run_synth.SCHEMA_VERSION
    assert set(a["stamp"]) >= {"menus", "menu_version", "seed", "engine_commit", "computed_thresholds", "injection"}
    assert set(a["modes"]) == {"engine", "proxy"}
    for m in a["modes"].values():
        assert set(m) >= {"mode", "rule", "per_trick", "cells", "bars"}
        assert {t["trick"] for t in m["per_trick"]} == {f"T{i}" for i in range(1, 9)} | {"R1", "R2", "R3", "R4"}
        for t in m["per_trick"]:
            assert set(t) >= {"ring_held_rate", "ring_watch_rate", "twin_false_hold_rate",
                              "earliest_hold_stage", "ring_held_by_strength"}
            assert 0 <= t["ring_held_rate"] <= 1 and 0 <= t["twin_false_hold_rate"] <= 1
        assert set(m["bars"]) >= {"catch_bar", "twin_bar", "blind_spot_cells", "not_measurable"}
        for c in m["cells"]:
            assert c["n_cases"] == 1 and sum(c["deals"].values()) == c["n_deals"]
    text = run_synth.render_markdown(a)
    assert "### Per trick" in text and "Earliest hold stage" in text


def test_engine_mode_with_real_decide_never_holds_a_twin(hist):
    """decide() is real since Plan 05-01: the honest twins (good analogues) are never held."""
    eng = _report(hist, ("engine",), ("core",))["modes"]["engine"]
    assert all(c["hold_rate"] == 0 for c in eng["cells"] if c["kind"] == "twin")
    assert eng["bars"]["twin_bar"]["pass"]


def test_engine_mode_reads_decide_output(hist, monkeypatch):
    """The harness must pick up holds, watch clusters and stage_at_flag from decide.evaluate."""
    calls = []

    def fake_evaluate(states, baseline, cfg, now):
        calls.append((len(states), now))
        out = FraudOutcome()
        for name, st in states.items():
            if name.startswith("S-T1-high") and "-b" not in name:
                out.per_deal[name] = Fraud(flagged=True, held=True, stage_at_flag=st.stage)
        return out

    monkeypatch.setattr(run_synth.fraud_decide, "evaluate", fake_evaluate)
    eng = _report(hist, ("engine",), ("core",))["modes"]["engine"]
    cells = {c["cell"]: c for c in eng["cells"]}
    assert cells["T1-high"]["hold_rate"] == 1.0
    assert cells["T1-high"]["earliest_hold_stage"] == "Intake"
    assert cells["T2-high"]["hold_rate"] == 0
    assert calls and all(n >= 1 for n, _ in calls)
    assert [now for _, now in calls[:11]] == sorted(now for _, now in calls[:11])  # time moves forward
    row = next(t for t in eng["per_trick"] if t["trick"] == "T1")
    assert row["earliest_hold_stage"] == "Intake"


def test_state_injection_layer(hist):
    case = synth.build_case(synth.cells_for(("core",))[0], 0, hist)
    row = case.deal_rows[0]
    deps = [d for d in case.deposit_rows if d["deal_id"] == row["deal_id"]]
    st = run_synth.state_of(row, deps)
    assert st.folder == row["deal_id"]
    assert {e.kind for e in st.entities} >= {"preparer", "phone", "guarantor"}
    assert len(st.features["deposit_months"]) == 6


def test_cli_writes_reports(hist_dir, tmp_path, capsys):
    rc = run_synth.main(["--history-dir", str(hist_dir), "--out-dir", str(tmp_path / "o"),
                         "--max-cases", "1", "--menus", "core", "--mode", "proxy"])
    assert rc == 0
    rep = json.loads((tmp_path / "o" / "synth_report.json").read_text(encoding="utf-8"))
    assert set(rep["modes"]) == {"proxy"}
    assert (tmp_path / "o" / "synth_report.md").read_text(encoding="utf-8").startswith("# Synthetic")
    assert run_synth.main(["--history-dir", str(tmp_path / "missing"), "--out-dir", str(tmp_path / "o2")]) == 0
