"""NEM-03 investigator (06-02): tool loop, read-only tools, degrade, routes. Synthetic data only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from prism.agents import client, investigator, tools
from prism.agents.tools import InvestigationContext, case_targets, run_tool
from prism.contract import InvestigatorTrace
from prism.engine.state import EntityRef
from prism.fraud.baseline import Baseline
from prism.fraud.graph import build_graph
from test_fraud_synth import deal_row, filler_deals
from test_move_support import make_state

SENTINEL = "nvapi-SENTINEL-investigator"


def _ctx():
    a = make_state("Deal One")
    b = make_state("Deal Two")
    c = make_state("Clean Three")
    for s in (a, b):
        s.entities = [EntityRef("preparer", "Shared Prep Co", s.folder + "/app.pdf"),
                      EntityRef("phone", "210-555-9999", s.folder + "/app.pdf")]
        s.fraud.flagged = True
        s.fraud.held = True
        s.fraud.reason = "Held by the cross-deal fraud screen."
    a.fraud.linked_deals = ["Deal Two", "H-1"]
    b.fraud.linked_deals = ["Deal One"]
    a.docs[0].meta = {"producer": "Tool X", "creator": "Maker"}
    a.docs[0].doc_class = "application"
    c.entities = [EntityRef("preparer", "Other Prep", "Clean Three/app.pdf")]
    rows = filler_deals(6) + [deal_row("H-1", preparer_name="Shared Prep Co", fraud_label="fraud",
                                       final_status="declined", repayment_status="")]
    baseline = Baseline.from_rows(rows)
    states = {s.folder: s for s in (a, b, c)}
    graph = build_graph(states, baseline)
    return InvestigationContext(states, baseline, graph, case_targets(states, []))


class Script:
    """Fake transport: returns scripted assistant messages in order; records whether tools were sent."""

    def __init__(self, *replies, default=None):
        self.replies = list(replies)
        self.default = default
        self.calls = []

    def __call__(self, model, messages, **kw):
        self.calls.append({"tools": kw.get("tools"), "messages": messages})
        if self.replies:
            r = self.replies.pop(0)
        else:
            r = self.default(kw) if callable(self.default) else self.default
        if isinstance(r, BaseException):
            raise r
        return r


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "<think>x</think>Check " + name,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def live(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    monkeypatch.setenv("NVIDIA_API_KEY", SENTINEL)
    monkeypatch.setenv("NEMOTRON_AGENT_MODEL", "fake/agent")


def chat(transport, **kw):
    return client.ChatClient(cache_subdir="investigator", transport=transport, sleep=lambda s: None, **kw)


# ---------------------------------------------------------------- tracer


def test_fallback_targets():
    ctx = _ctx()
    assert len(ctx.targets) == 1
    t = ctx.targets[0]
    assert t.id == "C-001" and t.deals == ("Deal One", "Deal Two") and t.history == ("H-1",)
    assert set(t.families) == {"F1", "F2", "F3", "F4", "F5", "F6"} and not any(v["fired"] for v in t.families.values())


def test_tracer_loop_file_and_routes(monkeypatch):
    live(monkeypatch)
    ctx = _ctx()
    fake = Script(_call("cluster_summary", {"cluster_id": "C-001"}),
                  {"role": "assistant", "content": "Both deals share a rare preparer."})
    traces = investigator.investigate_all(ctx, chat(fake))
    (tr,) = traces.values()
    assert tr.mode == "live" and tr.case_summary == "Both deals share a rare preparer."
    assert [s.tool for s in tr.steps] == ["cluster_summary"] and tr.steps[0].thought == "Check cluster_summary"
    assert tr.steps[0].result_summary.startswith("C-001: hold, 2 current deals, 1 history deals")
    assert fake.calls[1]["messages"][-1]["role"] == "tool"

    path = client.agents_dir() / "investigator.json"
    investigator.write_traces(traces, path, generated_by="test", mode="live")
    monkeypatch.setenv("MOCK", "0")
    from prism.api.app import create_app

    tc = TestClient(create_app())
    r = tc.get("/api/investigator/C-001")
    assert r.status_code == 200 and InvestigatorTrace.model_validate(r.json()).cluster_id == "C-001"
    assert tc.get("/api/investigator/C-999").status_code == 404
    path.unlink()
    assert tc.get("/api/investigator/C-001").status_code == 503

    assert tc.get("/api/redteam").status_code == 503
    (client.agents_dir() / "redteam.json").write_text(json.dumps({"tricks": [], "generated_by": "t"}), encoding="utf-8")
    assert tc.get("/api/redteam").status_code == 200

    monkeypatch.setenv("MOCK", "1")
    assert TestClient(create_app()).get("/api/investigator/C-001").status_code == 200


# ---------------------------------------------------------------- tools


def test_deal_entities_and_entity_deals():
    ctx = _ctx()
    res, summary = run_tool(ctx, "deal_entities", {"deal": "Deal One"})
    assert [e["kind"] for e in res["entities"]] and res["entities"][0]["links"] >= res["entities"][-1]["links"]
    assert len(summary) <= 240
    assert run_tool(ctx, "deal_entities", {"deal": "Nope"})[0] == {"error": "unknown deal"}
    res, _ = run_tool(ctx, "entity_deals", {"kind": "preparer", "value": "shared prep co"})
    assert res["current_deals"] == ["Deal One", "Deal Two"] and res["history_deals"] == ["H-1"]
    assert res["rarity"] == "used by 1 of " + str(ctx.baseline.n_effective()) + " history deals"
    assert "error" in run_tool(ctx, "entity_deals", {"kind": "ssn", "value": "x"})[0]


def test_history_and_documents_and_bad_input():
    ctx = _ctx()
    res, _ = run_tool(ctx, "history_deal", {"deal_id": "H-1"})
    assert res["fraud_label"] == "fraud" and res["final_status"] == "declined" and len(res["applied"]) == 10
    assert "error" in run_tool(ctx, "history_deal", {"deal_id": "H-404"})[0]
    res, _ = run_tool(ctx, "deal_documents", {"deal": "Deal One"})
    assert res["documents"][0]["producer"] == "Tool X" and res["documents"][0]["arrived"].endswith("-06:00")
    assert res["fraud_reason"]
    assert run_tool(ctx, "read_file", {"path": "/etc/passwd"})[0] == {"error": "unknown tool"}
    assert run_tool(ctx, "deal_documents", "notadict")[0] == {"error": "bad arguments"}


# ---------------------------------------------------------------- loop


def test_step_cap_then_final_call(monkeypatch, tmp_path):
    live(monkeypatch)
    ctx = _ctx()
    n = {"i": 0}

    def reply(kw):
        if kw.get("tools") is None:
            return {"role": "assistant", "content": "Final case text."}
        n["i"] += 1
        return _call("deal_entities", {"deal": "Deal One"}, cid="c%d" % n["i"])

    fake = Script(default=reply)
    tr = investigator.investigate(ctx, ctx.targets[0], chat(fake))
    assert len(tr.steps) == investigator.MAX_STEPS and tr.case_summary == "Final case text."
    assert fake.calls[-1]["tools"] is None
    assert fake.calls[-1]["messages"][-1]["content"] == investigator.STEP_LIMIT_NOTE

    def reply_fail(kw):
        if kw.get("tools") is None:
            return client.NemotronError("http 400")
        n["i"] += 1
        return _call("deal_documents", {"deal": "Deal Two"}, cid="d%d" % n["i"])

    monkeypatch.setenv("PRISM_AGENT_CACHE_DIR", str(tmp_path / "fresh"))  # no replay of the first run
    tr = investigator.investigate(ctx, ctx.targets[0], chat(Script(default=reply_fail)))
    assert len(tr.steps) == 8 and tr.case_summary.endswith("this case adds no new evidence.")


def test_json_fallback(monkeypatch):
    live(monkeypatch)
    ctx = _ctx()
    fake = Script({"role": "assistant", "content": 'Sure. {"tool": "history_deal", "args": {"deal_id": "H-1"}, "thought": "Check H-1"}'},
                  {"role": "assistant", "content": '{"final": "H-1 was fraud and shares the preparer."}'})
    tr = investigator.investigate(ctx, ctx.targets[0], chat(fake))
    assert [s.tool for s in tr.steps] == ["history_deal"] and tr.steps[0].thought == "Check H-1"
    assert tr.case_summary == "H-1 was fraud and shares the preparer."
    assert fake.calls[1]["messages"][-1]["content"].startswith("Result of history_deal: ")


def test_degrade_off_empty_and_raising(monkeypatch):
    ctx = _ctx()
    traces = investigator.investigate_all(ctx, chat(Script(RuntimeError("never called"))))
    (tr,) = traces.values()
    assert tr.mode == "off" and [s.tool for s in tr.steps] == ["cluster_summary"]
    assert investigator.investigate_all(InvestigationContext(ctx.states, ctx.baseline, ctx.graph, [])) == {}
    live(monkeypatch)
    (tr,) = investigator.investigate_all(ctx, chat(Script(default=RuntimeError("boom")))).values()
    assert tr.case_summary.endswith("this case adds no new evidence.") and len(tr.steps) == 1


@pytest.mark.parametrize("on", [True, False])
def test_off_the_scored_path(monkeypatch, on):
    ctx = _ctx()
    before = {k: s.to_submission_deal().model_dump() for k, s in ctx.states.items()}
    if on:
        live(monkeypatch)
    fake = Script(_call("cluster_summary", {"cluster_id": "C-001"}), {"role": "assistant", "content": "done"})
    investigator.investigate_all(ctx, chat(fake))
    assert {k: s.to_submission_deal().model_dump() for k, s in ctx.states.items()} == before


def test_replay_reproduces_live(monkeypatch):
    live(monkeypatch)
    ctx = _ctx()
    fake = Script(_call("cluster_summary", {"cluster_id": "C-001"}),
                  _call("entity_deals", {"kind": "preparer", "value": "Shared Prep Co"}, cid="c2"),
                  {"role": "assistant", "content": "Case."})
    a = investigator.investigate_all(ctx, chat(fake))["C-001"]
    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    none = Script(default=RuntimeError("no network"))
    c = chat(none)
    b = investigator.investigate_all(ctx, c)["C-001"]
    assert none.calls == [] and c.calls == 0 and b.mode == "replay"
    assert a.steps == b.steps and a.case_summary == b.case_summary == "Case."


def test_tool_specs_whitelist():
    assert tools.TOOL_NAMES == {"cluster_summary", "deal_entities", "entity_deals", "history_deal", "deal_documents"}
