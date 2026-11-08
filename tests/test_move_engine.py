"""Move inside run_sim: synthetic data root, Read monkeypatched to fake facts and issues."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_move_support import ASSIGNMENTS, PEOPLE, ROUTING, STAGES, at, fact, persona

from prism.engine.run import run_sim
from prism.engine.validate import validate_submission_data
from prism.engine.writer import build_submission, dumps
from prism.move.runtime import get_runtime

ARRIVALS = [
    {"path": "Acme Co/01_app.pdf", "arrive_at": "2026-03-02T09:00-06:00"},
    {"path": "Acme Co/02_id.pdf", "arrive_at": "2026-03-02T09:10-06:00"},
    {"path": "Bolt Inc/01_app.pdf", "arrive_at": "2026-03-02T10:00-06:00"},
    {"path": "Bolt Inc/02_late.pdf", "arrive_at": "2026-03-02T11:00-06:00"},
]
END = "2026-03-12T12:00"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "pack"
    for rel, data in (("rules/stages.json", STAGES), ("contacts/teams.json", {"people": PEOPLE, "routing": ROUTING}),
                      ("sim/assignments.json", ASSIGNMENTS)):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    folder = root / "shared_folder"
    for row in ARRIVALS:
        f = folder.joinpath(*row["path"].split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    arrivals = root / "sim" / "arrivals.jsonl"
    arrivals.write_text("\n".join(json.dumps(r) for r in ARRIVALS) + "\n", encoding="utf-8")

    import prism.read.extract as extract
    import prism.read.issues as issues

    def fake_facts(state, rules):
        out = {
            "legal_name": fact(f"{state.folder} LLC"),
            "amount_requested": fact(120000),
            "product": fact("Term Loan"),
            "term_months": fact(24),
            "purpose_category": fact("working_capital"),
            "annual_revenue_stated": fact(700000),
        }
        state.features["profile"] = {"formation_date": "2015-01-01", "relationship": {"status": "new"},
                                     "net_income_fy_cents": 40_000_000, "deposits_annualized_cents": 68_000_000}
        return out

    def fake_issues(state, rules, now):
        if state.folder == "Bolt Inc" and len(state.docs) < 2:
            return ["MISSING_DOC:ein_letter"]
        return []

    monkeypatch.setattr(extract, "extract_facts", fake_facts)
    monkeypatch.setattr(issues, "detect_issues", fake_issues)
    return root, folder, arrivals


def go(world, **kw):
    root, folder, arrivals = world
    return run_sim(folder, arrivals, data_root=root, end=at(END), **kw)


def payload(result):
    return build_submission(result.states, result.history_flags, result.errors, team="T", set_name="sample")


def test_run_sim_drives_the_pipeline_and_validates(world):
    with persona("fast"):
        result = go(world)
    assert result.errors == [] and all(s.error is None for s in result.states.values())
    for name in ("Acme Co", "Bolt Inc"):
        st = result.states[name]
        assert st.stage == "Closed" and get_runtime(st).terminal == "closed"
        assert [e.stage for e in st.stage_log] == ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]
        assert all(e.exited_at and e.why and e.decision for e in st.stage_log)
    bolt = result.states["Bolt Inc"]
    # the missing document was asked for first; the late arrival cleared it and Intake moved on after it
    assert bolt.human_requests[0].type == "info_request" and bolt.human_requests[0].to == "borrower"
    assert bolt.stage_log[0].exited_at == "2026-03-02T11:30-06:00"
    # Intake owners alternate by arrival order
    owners = {n: result.states[n].stage_log[0] for n in result.states}
    assert owners["Acme Co"].entered_at == "2026-03-02T09:00-06:00"
    data = payload(result)
    res = validate_submission_data(data)
    assert res.ok, res


def test_two_runs_are_byte_identical(world):
    with persona("fast"):
        a = dumps(payload(go(world)))
    with persona("fast"):
        b = dumps(payload(go(world)))
    assert a == b
    assert "\r" not in a


def test_persona_switch_changes_only_timing(world):
    with persona("fast"):
        fast = go(world)
    with persona("slow"):
        slow = go(world)
    for name in fast.states:
        f, s = fast.states[name], slow.states[name]
        assert [e.stage for e in f.stage_log] == [e.stage for e in s.stage_log]
        # a slow answerer also draws reminders and backup copies (J21), so compare the first request per gate
        first = lambda st: [(r.type, r.to) for i, r in enumerate(st.human_requests)  # noqa: E731
                            if (r.type, r.to) not in [(q.type, q.to) for q in st.human_requests[:i]]]
        assert [x for x in first(f)] == [x for x in first(s) if x in first(f)]
    assert fast.states["Acme Co"].stage_log[-1].exited_at < slow.states["Acme Co"].stage_log[-1].exited_at


def test_never_persona_leaves_the_deal_at_the_gate_with_alerts(world):
    with persona("never"):
        result = go(world)
    st = result.states["Acme Co"]
    assert st.stage == "Credit" and st.error is None
    assert any(a.level == "alert" for a in st.alerts)
    assert validate_submission_data(payload(result)).ok


def test_alerts_are_not_duplicated_by_the_engine_extend(world):
    with persona("never"):
        result = go(world)
    for st in result.states.values():
        keys = [(a.at, a.stage, a.level, a.to) for a in st.alerts]
        assert len(keys) == len(set(keys))


def test_empty_rules_run_is_untouched(tiny_set):
    folder, arrivals = tiny_set
    result = run_sim(folder, arrivals)
    for st in result.states.values():
        assert st.error is None and st.stage_log == [] and st.alerts == []
