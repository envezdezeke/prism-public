"""Engine skeleton: config, money, interface surface, tracer slice."""
from __future__ import annotations

import importlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from prism.contract import Submission
from prism.engine.config import (
    ConfigError,
    Rules,
    load_assignments,
    load_fraud_config,
    load_rules,
    load_stages,
    load_teams,
)
from prism.engine.money import cents_to_number, to_cents
from prism.engine.run import run_sim
from prism.engine.writer import build_submission

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "participant"


# ------------------------------------------------------------ fraud.yaml


def test_fraud_yaml_values():
    c = load_fraud_config()
    assert c.percentiles == {"F2": 0.95, "F3": 0.95, "F4": 0.95, "F5": 0.95}
    assert c.f1_rarity_fraction == 0.02
    assert c.f1_min_linked_deals == 2
    assert c.hold_required == ("F1",)
    assert c.hold_additional_from == ("F2", "F3", "F4", "F5")
    assert c.hold_min_additional == 2
    assert c.non_voting == ("F6",)
    assert c.history_min_links == 2
    assert (c.train_end, c.test_start, c.test_end) == (
        date(2026, 6, 30), date(2026, 7, 1), date(2026, 10, 31),
    )


def _cfg(tmp_path, mutate):
    raw = yaml.safe_load((ROOT / "config" / "fraud.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "fraud.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return p


def test_bad_percentile(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["families"]["F2"].update(percentile=1.5)))


def test_f6_cannot_vote(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["families"]["F6"].update(votes=True)))


def test_unknown_hold_family(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["hold_rule"]["additional_from"].append("F9")))


def test_non_voting_family_cannot_count_toward_hold(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["hold_rule"]["additional_from"].append("F6")))


def test_min_additional_out_of_range(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["hold_rule"].update(min_additional=9)))


def test_calibration_order(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(
            _cfg(tmp_path, lambda r: r["calibration"].update(test_start="2026-06-01"))
        )


def test_min_linked_deals_floor(tmp_path):
    with pytest.raises(ConfigError):
        load_fraud_config(_cfg(tmp_path, lambda r: r["families"]["F1"].update(min_linked_deals=1)))


def test_yaml_is_safe_loaded(tmp_path):
    p = tmp_path / "evil.yaml"
    p.write_text("families: !!python/object/apply:os.getcwd []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fraud_config(p)


def test_missing_fraud_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_fraud_config(tmp_path / "nope.yaml")


# ------------------------------------------------------------ data-pack loaders


def test_loaders_on_minimal_json(tmp_path):
    (tmp_path / "s.json").write_text(
        json.dumps({
            "pipeline": ["A", "B"],
            "stages": {"A": {"sla_hours": 8, "human_gate": None},
                       "B": {"sla_hours": 24, "human_gate": {"type": "x"}}},
        }),
        encoding="utf-8",
    )
    (tmp_path / "t.json").write_text(
        json.dumps({"people": [{"id": "p.one", "backup": "p.two", "escalates_to": "p.boss"}],
                    "routing": {"A": "text"}}),
        encoding="utf-8",
    )
    (tmp_path / "a.json").write_text(
        json.dumps({"F": {"relationship_manager": "p.one", "credit_officer": "p.two"}}),
        encoding="utf-8",
    )
    s = load_stages(tmp_path / "s.json")
    assert s.pipeline == ("A", "B") and s.sla_hours("A") == 8.0 and s.sla_hours("Z") is None
    assert s.gate("A") is None and s.gate("B") == {"type": "x"}
    t = load_teams(tmp_path / "t.json")
    assert t.backup_of("p.one") == "p.two" and t.escalates_to("p.one") == "p.boss"
    assert t.person("nobody") is None
    a = load_assignments(tmp_path / "a.json")
    assert a.rm("F") == "p.one" and a.credit_officer("F") == "p.two" and a.rm("Q") is None


def test_bad_json_is_config_error(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_stages(p)
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_stages(p)


def test_load_rules_warns_on_missing(tmp_path):
    r = load_rules(tmp_path)
    assert isinstance(r, Rules) and r.fraud is not None
    assert len(r.warnings) == 3 and r.history_dir is None
    assert load_rules(None).warnings == ()


@pytest.mark.skipif(not DATA.is_dir(), reason="sample data not present")
def test_loaders_on_participant_data():
    r = load_rules(DATA)
    assert r.warnings == ()
    assert r.stages.pipeline and r.stages.sla_hours(r.stages.pipeline[0])
    assert r.teams.people and r.assignments.by_folder
    assert r.history_dir is not None


# ------------------------------------------------------------ money


def test_money():
    assert to_cents("$1,234.50") == 123450
    assert to_cents(246000) == 24600000
    assert to_cents(0.125) == 13  # half-up
    assert to_cents(Decimal("2.005")) == 201
    assert to_cents("1,000") == 100000
    for bad in (True, float("nan"), float("inf"), "abc", None, "12-34"):
        with pytest.raises(ValueError):
            to_cents(bad)
    assert cents_to_number(24600000) == 246000 and isinstance(cents_to_number(24600000), int)
    assert cents_to_number(123450) == 1234.5


# ------------------------------------------------------------ interface surface

SURFACE = {
    "prism.engine.state": ["PIPELINE", "Doc", "EntityRef", "DealState"],
    "prism.read.classify": ["DOC_CLASSES", "classify"],
    "prism.read.extract": ["FACT_FIELDS", "extract_facts", "extract_entities"],
    "prism.read.issues": ["detect_issues"],
    "prism.move.stage": ["step_deal"],
    "prism.move.routing": ["route_gate"],
    "prism.move.sla": ["sla_pct", "sla_alerts"],
    "prism.fraud.baseline": ["Baseline", "build_baseline"],
    "prism.fraud.graph": ["EntityGraph", "build_graph"],
    "prism.fraud.signals": ["FamilySignal", "compute_signals"],
    "prism.fraud.decide": ["FraudOutcome", "decide", "evaluate"],
    "prism.engine.config": [
        "ConfigError", "Rules", "Stages", "Teams", "Assignments", "FraudConfig",
        "load_stages", "load_teams", "load_assignments", "load_fraud_config", "load_rules",
    ],
    "prism.engine.run": ["run_sim", "RunResult"],
    "prism.engine.writer": ["build_submission", "dumps", "write_submission"],
    "prism.engine.visibility": ["ArrivalLog", "Arrival", "SIM_TZ", "parse_time"],
}

SIGNATURES = {
    ("prism.read.classify", "classify"): ["doc"],
    ("prism.read.extract", "extract_facts"): ["state", "rules"],
    ("prism.read.extract", "extract_entities"): ["state", "rules"],
    ("prism.read.issues", "detect_issues"): ["state", "rules", "now"],
    ("prism.move.stage", "step_deal"): ["state", "rules", "now"],
    ("prism.move.routing", "route_gate"): ["gate_type", "state", "rules", "now"],
    ("prism.move.sla", "sla_pct"): ["state", "rules", "now"],
    ("prism.move.sla", "sla_alerts"): ["state", "rules", "now"],
    ("prism.fraud.baseline", "build_baseline"): ["history_dir", "cfg"],
    ("prism.fraud.graph", "build_graph"): ["states", "baseline"],
    ("prism.fraud.signals", "compute_signals"): ["graph", "states", "baseline", "cfg", "now"],
    ("prism.fraud.decide", "decide"): ["signals", "graph", "states", "baseline", "cfg", "now"],
    ("prism.fraud.decide", "evaluate"): ["states", "baseline", "cfg", "now"],
}


def test_interface_surface_is_pinned():
    import inspect

    for mod, names in SURFACE.items():
        m = importlib.import_module(mod)
        for n in names:
            assert hasattr(m, n), f"{mod}.{n} missing"
    for (mod, fn), params in SIGNATURES.items():
        got = list(inspect.signature(getattr(importlib.import_module(mod), fn)).parameters)
        assert got == params, f"{mod}.{fn} signature changed: {got}"


def test_deal_state_fields_pinned():
    from dataclasses import fields

    from prism.engine.state import DealState

    assert [f.name for f in fields(DealState)] == [
        "folder", "docs", "facts", "entities", "features", "issues", "resolved_issues",
        "stage", "stage_entered_at", "stage_log", "human_requests", "alerts", "fraud",
        "last_arrival_at", "error",
    ]


def test_doc_classes_and_fact_fields():
    from prism.contract import Extracted
    from prism.read.classify import DOC_CLASSES
    from prism.read.extract import FACT_FIELDS

    assert "other" in DOC_CLASSES and "bank_statement" in DOC_CLASSES
    assert FACT_FIELDS == tuple(Extracted.model_fields)


# ------------------------------------------------------------ tracer


def test_tracer_tiny_set_to_valid_submission(tiny_set):
    folder, arrivals = tiny_set
    result = run_sim(folder, arrivals)
    assert sorted(result.states) == ["Alpha Trading", "Beta & Sons"]
    # 4 arrivals + end-grace tick (option 1) + one quiet-expiry tick at 10:15 (D-16: 09:45 + 30 min is the
    # first arrival of a quiet gap in Alpha's folder; the other expiries coincide with existing ticks)
    assert result.n_files == 4 and result.n_ticks == 6
    assert result.unattached == ["notice.txt"]
    payload = build_submission(
        result.states, result.history_flags, result.errors, team="T", set_name="sample"
    )
    assert sorted(payload["deals"]) == ["Alpha Trading", "Beta & Sons"]
    for deal in payload["deals"].values():
        assert set(deal) == {"extracted", "issues", "stage_log", "human_requests", "alerts", "fraud"}
        assert all(v is None for v in deal["extracted"].values())
        # D-12 (user-approved): zero-byte files cannot be classified, so every requirement is unmet. With no
        # readable application date the statement window anchors on the last arrival's month (2026-01), so
        # the six months before it are missing. Issues were [] while detect_issues was a stub.
        assert deal["issues"] == sorted(
            ["MISSING_DOC:application", "MISSING_DOC:guarantor_id", "MISSING_DOC:certificate_of_formation",
             "MISSING_DOC:ein_letter", "MISSING_DOC:financial_statements", "MISSING_DOC:tax_return"]
            + [f"MISSING_DOC:bank_statement_2025-{m:02d}" for m in range(7, 13)]
        ) and deal["stage_log"] == []
        assert deal["fraud"]["flagged"] is False and deal["fraud"]["held"] is False
    assert payload["history_flags"] == []
    Submission.model_validate(payload)


def test_docs_appear_only_after_arrival(tiny_set):
    from prism.engine.visibility import parse_time

    folder, arrivals = tiny_set
    result = run_sim(folder, arrivals, end=parse_time("2026-01-05T09:45"))
    names = {k: [d.name for d in v.docs] for k, v in result.states.items()}
    assert names == {"Alpha Trading": ["01_app.pdf", "02_id.pdf"], "Beta & Sons": ["02_id.pdf"]}


def test_deal_dir_without_arrivals_still_gets_an_entry(tiny_set):
    folder, arrivals = tiny_set
    (folder / "Gamma Foods").mkdir()
    result = run_sim(folder, arrivals)
    assert "Gamma Foods" in result.states and result.states["Gamma Foods"].docs == []


def test_missing_arrivals_file_is_a_usage_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_sim(tmp_path, tmp_path / "none.jsonl")


# ------------------------------------------------------------ determinism, crash isolation (task 2)

import ast
import re

from prism.cli import main

ENGINE_PKGS = ("engine", "read", "move", "fraud")


def _run_cli(folder, arrivals, out):
    code = main(["run", "--folder", str(folder), "--arrivals", str(arrivals), "--out", str(out)])
    assert code == 0
    return Path(out).read_bytes()


def test_run_is_deterministic(tiny_set, tmp_path):
    folder, arrivals = tiny_set
    a = _run_cli(folder, arrivals, tmp_path / "a" / "submission.json")
    b = _run_cli(folder, arrivals, tmp_path / "b" / "submission.json")
    assert a == b
    data = json.loads(a)
    assert sorted(data["deals"]) == ["Alpha Trading", "Beta & Sons"]
    assert b"\r\n" not in a and a.endswith(b"\n")
    for m in re.findall(r'"(\d{4}-\d\d-\d\dT[^"]+)"', a.decode("utf-8")):
        assert m.endswith("-06:00")


def test_crash_isolation(tiny_set, tmp_path, monkeypatch):
    import prism.read.extract as extract
    from prism.engine.validate import validate_submission_file

    folder, arrivals = tiny_set

    def boom(state, rules):
        if state.folder == "Alpha Trading":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(extract, "extract_facts", boom)
    out = tmp_path / "out" / "submission.json"
    assert main(["run", "--folder", str(folder), "--arrivals", str(arrivals), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "boom" in data["deals"]["Alpha Trading"]["_error"]
    assert "Traceback" not in data["deals"]["Alpha Trading"]["_error"]
    assert "_error" not in data["deals"]["Beta & Sons"]
    res = validate_submission_file(out, folders=["Alpha Trading", "Beta & Sons"])
    assert res.ok and any("engine error" in w for w in res.warnings)


def test_errored_deal_is_frozen_on_later_ticks(tiny_set, monkeypatch):
    import prism.read.extract as extract

    folder, arrivals = tiny_set
    calls = []

    def boom(state, rules):
        calls.append(state.folder)
        raise RuntimeError("boom")

    monkeypatch.setattr(extract, "extract_facts", boom)
    result = run_sim(folder, arrivals)
    assert calls.count("Alpha Trading") == 1  # later arrivals skip the errored deal
    assert result.states["Alpha Trading"].error.startswith("extract_facts: RuntimeError: boom")


def test_fraud_crash_is_recorded_not_fatal(tiny_set, monkeypatch):
    import prism.fraud.decide as decide

    def boom(*a, **k):
        raise ValueError("graph exploded")

    monkeypatch.setattr(decide, "evaluate", boom)
    folder, arrivals = tiny_set
    result = run_sim(folder, arrivals)
    assert any(e.startswith("fraud: ValueError") for e in result.errors)
    assert result.states["Alpha Trading"].error is None


def test_writer_falls_back_on_unserializable_state():
    from prism.contract import Fact
    from prism.engine.state import DealState

    bad = DealState(folder="Bad Co")
    bad.facts["legal_name"] = Fact.model_construct(value=["not", "a", "string"], source_doc="x", confidence=1)
    good = DealState(folder="Good Co")
    payload = build_submission(
        {"Bad Co": bad, "Good Co": good}, [], [], team="T", set_name="sample"
    )
    assert payload["deals"]["Bad Co"]["_error"].startswith("serialize:")
    assert all(v is None for v in payload["deals"]["Bad Co"]["extracted"].values())
    assert "_error" not in payload["deals"]["Good Co"]
    from prism.engine.validate import validate_submission_data

    assert validate_submission_data(payload).ok


_CLOCK_ATTRS = {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "time_ns", "process_time"}
_BANNED_IMPORTS = {"random", "uuid", "secrets"}


def _engine_sources():
    for pkg in ENGINE_PKGS:
        for p in sorted((ROOT / "prism" / pkg).rglob("*.py")):
            yield p


def test_engine_has_no_wall_clock_or_randomness():
    offenders = []
    for p in _engine_sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [f"{p.name}: import {a.name}" for a in node.names
                              if a.name.split(".")[0] in _BANNED_IMPORTS]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in _BANNED_IMPORTS:
                    offenders.append(f"{p.name}: from {node.module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _CLOCK_ATTRS:
                    offenders.append(f"{p.name}: .{node.func.attr}()")
    assert offenders == []


def test_no_sample_literals_in_code():
    hist = re.compile(r"H-\d{4}")
    for p in _engine_sources():
        assert not hist.search(p.read_text(encoding="utf-8")), f"history id literal in {p}"
    shared = DATA / "shared_folder"
    if not shared.is_dir():
        pytest.skip("sample data not present")
    names = [d.name.lower() for d in shared.iterdir() if d.is_dir()]
    texts = {p: p.read_text(encoding="utf-8").lower() for p in (ROOT / "prism").rglob("*.py")}
    texts[ROOT / "config" / "fraud.yaml"] = (ROOT / "config" / "fraud.yaml").read_text(encoding="utf-8").lower()
    for p, t in texts.items():
        for n in names:
            assert n not in t, f"sample folder name {n!r} in {p.name}"


@pytest.mark.skipif(not DATA.is_dir(), reason="sample data not present")
def test_cli_smoke_on_participant_data(tmp_path):
    out = tmp_path / "submission.json"
    code = main([
        "run", "--folder", str(DATA / "shared_folder"),
        "--arrivals", str(DATA / "sim" / "arrivals.jsonl"), "--out", str(out),
    ])
    assert code == 0
    assert main(["validate", str(out), "--folder", str(DATA / "shared_folder")]) == 0


def test_default_end_is_last_arrival_plus_grace(tmp_path):
    """Option 1 (2026-11-07): with no --end, the clock runs 30 sim-minutes past the last arrival."""
    import json as _json
    from prism.engine.run import run_sim

    (tmp_path / "shared" / "Deal A").mkdir(parents=True)
    (tmp_path / "shared" / "Deal A" / "a.txt").write_text("x", encoding="utf-8")
    arr = tmp_path / "arrivals.jsonl"
    arr.write_text(_json.dumps({"path": "Deal A/a.txt", "arrive_at": "2026-11-02T09:00-06:00"}) + "\n",
                   encoding="utf-8")
    with_grace = run_sim(tmp_path / "shared", arr, data_root=tmp_path)
    no_grace = run_sim(tmp_path / "shared", arr, data_root=tmp_path, end_grace_minutes=0)
    assert with_grace.n_ticks == no_grace.n_ticks + 1


def test_arrival_for_folder_not_on_disk_is_skipped_and_reported(tiny_set):
    folder, arrivals = tiny_set
    with open(arrivals, "a", encoding="utf-8") as f:
        f.write(json.dumps({"path": "Nowhere Inc/x.pdf", "arrive_at": "2026-01-05T09:50-06:00"}) + "\n")
    result = run_sim(folder, arrivals)
    assert "Nowhere Inc" not in result.states
    assert sorted(result.states) == ["Alpha Trading", "Beta & Sons"]
    assert any("not on disk skipped: Nowhere Inc" in e for e in result.errors)
