"""Contract tests: fixtures validate, validators accept/reject, every endpoint behaves."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from prism.api.app import create_app
from prism.contract import (
    ISSUE_RE,
    AlertFeedItem,
    Clock,
    ClockAdvance,
    DealDetail,
    DealSummary,
    Fraud,
    FraudGraph,
    InboxItem,
    InvestigatorTrace,
    RedTeamReport,
    Submission,
    SubmissionDeal,
    parse_sim_time,
)
from prism.derive import derive

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"

FIXTURE_MODELS: dict[str, object] = {
    "clock.json": Clock,
    "deals.json": list[DealSummary],
    "deal_detail.json": dict[str, DealDetail],
    "alerts.json": list[AlertFeedItem],
    "submission.json": Submission,
    "inbox.json": list[InboxItem],
    "graph.json": FraudGraph,
    "investigator.json": InvestigatorTrace,
    "redteam.json": RedTeamReport,
}


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    return TestClient(create_app())


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_fixture_validates(name):
    TypeAdapter(FIXTURE_MODELS[name]).validate_python(load(name))


# ------------------------------------------------------------ sim time

@pytest.mark.parametrize("s", ["2026-11-02T09:30-06:00", "2026-11-02T09:30:00-06:00"])
def test_sim_time_accepts(s):
    parse_sim_time(s)
    Clock(now=s, start=s, end=s)


@pytest.mark.parametrize(
    "s",
    [
        "2026-11-02T09:30Z",
        "2026-11-02T09:30-05:00",
        "2026-11-02T09:30",
        "2026-11-02 09:30-06:00",
        "not-a-time-06:00",
    ],
)
def test_sim_time_rejects(s):
    with pytest.raises(ValueError):
        parse_sim_time(s)
    with pytest.raises(ValidationError):
        Clock(now=s, start=s, end=s)


# ------------------------------------------------------------ issue grammar

DOCS = [
    "application", "guarantor_id", "certificate_of_formation", "ein_letter",
    "bank_statement_2026-09", "financial_statements", "tax_return", "equipment_quote",
]
MISMATCH = [
    "legal_name", "ein", "amount_requested", "guarantor_name", "guarantor_dob",
    "guarantor_address", "annual_revenue", "bank_balance",
]
TRICKS = ["STMT_TAMPER", "REV_INFLATION", "ID_MISMATCH", "DUP_EIN", "SHELL_ADDRESS"]
ACCEPT = (
    [f"MISSING_DOC:{d}" for d in DOCS]
    + ["MISSING_PAGE:bank_statement_2026-08"]
    + [f"MISMATCH:{m}" for m in MISMATCH]
    + [f"OLD_TRICK:{t}" for t in TRICKS]
)
REJECT = [
    "MISSING_DOC:passport",
    "missing_doc:tax_return",
    "MISMATCH:ein ",
    "MISSING_DOC:tax_return\n",
    "MISSING_DOC:bank_statement_2026-5",
    "OLD_TRICK:UNKNOWN",
    "MISMATCH:ein|x",
]


@pytest.mark.parametrize("s", ACCEPT)
def test_issue_accepts(s):
    assert ISSUE_RE.fullmatch(s)


@pytest.mark.parametrize("s", REJECT)
def test_issue_rejects(s):
    assert not ISSUE_RE.fullmatch(s)


def test_clock_advance_exactly_one():
    with pytest.raises(ValidationError):
        ClockAdvance()
    with pytest.raises(ValidationError):
        ClockAdvance(hours=1, to="2026-11-03T08:00-06:00")
    ClockAdvance(hours=1)
    ClockAdvance(to="2026-11-03T08:00-06:00")


# ------------------------------------------------------------ clock endpoints

def test_get_clock(client):
    r = client.get("/api/clock")
    assert r.status_code == 200
    assert r.json() == load("clock.json")


def test_advance_clock(client):
    r = client.post("/api/clock/advance", json={"hours": 2})
    assert r.json()["now"] == "2026-11-04T20:00-06:00"
    r = client.post("/api/clock/advance", json={"hours": 100000})
    assert r.json()["now"] == load("clock.json")["end"]
    assert client.post("/api/clock/advance", json={}).status_code == 422
    # Q15: a 'to' earlier than now is rejected (F-02's clamp-to-start no longer applies)
    r = client.post("/api/clock/advance", json={"to": "2020-01-01T00:00-06:00"})
    assert r.status_code == 422


def test_advance_clock_overflow_clamps(client):
    end = load("clock.json")["end"]
    for raw in ('{"hours": 1e300}', '{"hours": Infinity}'):
        r = client.post(
            "/api/clock/advance", content=raw, headers={"content-type": "application/json"}
        )
        assert r.status_code == 200 and r.json()["now"] == end, raw


def test_replay_clock(client):
    r = client.post("/api/clock/replay")
    assert r.status_code == 200
    assert r.json()["now"] == load("clock.json")["start"]
    assert client.post("/api/clock/replay", json={"speed": 4}).status_code == 200


def test_cors(client):
    ok = client.options(
        "/api/clock",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(
        "/api/clock",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_mock_off_is_503(monkeypatch):
    monkeypatch.setenv("MOCK", "0")
    c = TestClient(create_app())
    r = c.get("/api/clock")
    assert r.status_code == 503
    assert r.json()["detail"] == "engine not wired yet"


def test_mock_unset_serves_fixtures(monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    assert TestClient(create_app()).get("/api/clock").status_code == 200


# ------------------------------------------------------------ deals slice

def details() -> dict[str, DealDetail]:
    return TypeAdapter(dict[str, DealDetail]).validate_python(load("deal_detail.json"))


def test_derived_fixtures_are_fresh():
    for name, content in derive(details()).items():
        assert load(name) == content, f"{name} is stale; run python -m prism.derive"


def test_detail_keys_and_facts_agree():
    for folder, d in details().items():
        assert d.folder == folder
        facts = d.facts
        assert d.legal_name == (facts.legal_name.value if facts.legal_name else None)
        assert d.amount_requested == (facts.amount_requested.value if facts.amount_requested else None)
        assert d.needed_by == (facts.needed_by.value if facts.needed_by else None)


def test_issue_strings_valid():
    for d in details().values():
        for i in d.issues + d.resolved_issues:
            assert ISSUE_RE.fullmatch(i), i


def test_hold_and_watch_flags():
    for d in details().values():
        if d.held:
            assert d.fraud.held and d.fraud.flagged
        if d.watch:
            assert not d.fraud.flagged and not d.held


def test_required_scenarios():
    ds = list(details().values())
    assert any(d.held for d in ds)
    assert any(d.watch for d in ds)
    assert any(any(i.startswith("MISSING_DOC:") for i in d.issues) for d in ds)
    assert any(d.needed_by for d in ds)
    assert any({a.level for a in d.alerts} >= {"alert", "escalation"} for d in ds)


def test_deals_endpoint(client):
    r = client.get("/api/deals")
    assert r.status_code == 200
    assert len(r.json()) == 5


def test_deal_detail_endpoint_encodings(client):
    for folder in details():
        assert client.get(f"/api/deals/{folder}").status_code == 200
        r = client.get("/api/deals/" + quote(folder, safe=""))
        assert r.status_code == 200
        assert r.json()["folder"] == folder
    assert client.get("/api/deals/nope").status_code == 404


def test_alerts_newest_first(client):
    r = client.get("/api/alerts")
    assert r.status_code == 200
    ats = [parse_sim_time(a["at"]) for a in r.json()]
    assert ats == sorted(ats, reverse=True)


def test_submission_endpoint(client):
    r = client.get("/api/submission")
    assert r.status_code == 200
    Submission.model_validate(r.json())


@pytest.mark.parametrize("path", ["/api/deals", "/api/deals/x", "/api/alerts", "/api/submission"])
def test_deals_routes_503_when_mock_off(monkeypatch, path):
    monkeypatch.setenv("MOCK", "0")
    assert TestClient(create_app()).get(path).status_code == 503


def test_no_sample_folder_names_in_fixtures():
    sample = ROOT / "data" / "participant" / "shared_folder"
    if not sample.is_dir():
        pytest.skip("sample data not present")
    names = [p.name.lower() for p in sample.iterdir() if p.is_dir()]
    for f in FIX.glob("*.json"):
        text = f.read_text(encoding="utf-8").lower()
        for n in names:
            assert n not in text, f"{f.name} contains sample folder name"


# ------------------------------------------------------------ inbox, graph, agents

ALL_PATHS = [
    "/api/deals", "/api/deals/{folder}", "/api/clock", "/api/clock/advance", "/api/clock/replay",
    "/api/inbox", "/api/inbox/{item_id}/reply", "/api/alerts", "/api/graph",
    "/api/investigator/{cluster_id}", "/api/redteam", "/api/submission",
]


def test_fixture_files_all_mapped():
    assert {p.name for p in FIX.glob("*.json")} == set(FIXTURE_MODELS)


def test_inbox_matches_human_requests():
    inbox = TypeAdapter(list[InboxItem]).validate_python(load("inbox.json"))
    from_inbox = sorted((i.deal, i.type, i.to, i.at, i.answer, i.answered_at) for i in inbox)
    expected = sorted(
        (folder, r.type, r.to, r.at, r.answer, r.answered_at)
        for folder, d in details().items()
        for r in d.human_requests
        if r.to != "borrower"
    )
    assert from_inbox == expected


def test_graph_referential_and_rules():
    g = FraudGraph.model_validate(load("graph.json"))
    ds = details()
    assert {n.id for n in g.nodes if n.type == "deal"} == set(ds)
    ids = {n.id for n in g.nodes}
    history = {n.id for n in g.nodes if n.type == "history_deal"}
    for folder, d in ds.items():
        for link in d.fraud.linked_deals:
            assert link in ds or link in history
        if d.held:
            assert any(folder in c.deals and c.tier == "hold" for c in g.clusters)
    for c in g.clusters:
        n = sum(f.fired for f in c.families.values())
        if c.tier == "hold":
            assert c.families["F1"].fired and n >= 3
        elif c.tier == "watch":
            assert n in (1, 2)
        else:
            assert n == 0
    assert any(c.tier == "hold" and len(c.deals) == 3 for c in g.clusters)
    rarities = [e.rarity for e in g.edges]
    assert min(rarities) < 0.2 and max(rarities) > 0.9
    assert len(g.nodes) == 12 and len(g.edges) == 12
    trace = InvestigatorTrace.model_validate(load("investigator.json"))
    assert trace.cluster_id in {c.id for c in g.clusters}


def test_graph_rejects_bad_structure():
    g = load("graph.json")
    entity = next(n["id"] for n in g["nodes"] if n["type"] == "entity")
    flipped = dict(g["edges"][0], source=g["edges"][0]["target"], target=g["edges"][0]["source"])
    with pytest.raises(ValidationError):
        FraudGraph.model_validate({**g, "edges": [flipped] + g["edges"][1:]})
    bad_cluster = dict(g["clusters"][0], deals=g["clusters"][0]["deals"] + [entity])
    with pytest.raises(ValidationError):
        FraudGraph.model_validate({**g, "clusters": [bad_cluster] + g["clusters"][1:]})


def test_redteam_rates():
    rep = RedTeamReport.model_validate(load("redteam.json"))
    for t in rep.tricks:
        assert abs(t.catch_rate - t.caught / t.n_deals) <= 0.01


def test_inbox_endpoints(client):
    assert client.get("/api/inbox").status_code == 200
    r = client.get("/api/inbox?status=pending")
    assert r.status_code == 200 and r.json() and all(i["status"] == "pending" for i in r.json())
    assert client.get("/api/inbox?status=bogus").status_code == 422


def test_inbox_reply(client):
    r = client.post("/api/inbox/inbox-001/reply", json={"option": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "answered" and r.json()["answer"] == "approve"
    assert client.post("/api/inbox/inbox-001/reply", json={"option": "maybe"}).status_code == 422
    assert client.post("/api/inbox/nope/reply", json={"option": "approve"}).status_code == 404
    assert client.post("/api/inbox/inbox-005/reply", json={"option": "approve"}).status_code == 409


def test_graph_endpoint(client):
    full = client.get("/api/graph")
    assert full.status_code == 200
    at = "2026-11-03T08:30-06:00"
    part = client.get("/api/graph", params={"at": at})
    assert part.status_code == 200
    body = part.json()
    assert len(body["edges"]) < len(full.json()["edges"])
    cutoff = parse_sim_time(at)
    assert all(parse_sim_time(e["first_visible_at"]) <= cutoff for e in body["edges"])
    assert client.get("/api/graph", params={"at": "garbage"}).status_code == 422


def test_agent_endpoints(client):
    assert client.get("/api/investigator/C-001").status_code == 200
    assert client.get("/api/investigator/nope").status_code == 404
    assert client.get("/api/redteam").status_code == 200


def test_openapi_and_docs(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ALL_PATHS:
        assert p in paths, p
    assert client.get("/docs").status_code == 200


def test_everything_503_when_mock_off(monkeypatch):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("PRISM_GRAPH_PATH", str(ROOT / "out" / "no_such_graph.json"))
    c = TestClient(create_app())
    calls = [
        ("get", "/api/deals", None), ("get", "/api/deals/x", None), ("get", "/api/clock", None),
        ("post", "/api/clock/advance", {"hours": 1}), ("post", "/api/clock/replay", None),
        ("get", "/api/inbox", None), ("post", "/api/inbox/inbox-001/reply", {"option": "approve"}),
        ("get", "/api/alerts", None), ("get", "/api/graph", None),
        ("get", "/api/investigator/C-001", None), ("get", "/api/redteam", None),
        ("get", "/api/submission", None),
    ]
    assert len(calls) == len(ALL_PATHS)
    for method, path, body in calls:
        r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 503, path
    assert c.get("/docs").status_code == 200


# ------------------------------------------------------------ Q14: fraud consistency (UI models only)


def held_detail() -> dict:
    return copy.deepcopy(load("deal_detail.json")["Bayou Bistro & Grill"])


def watch_detail() -> dict:
    return copy.deepcopy(load("deal_detail.json")["Cedar Ridge Welding"])


def test_q14_fixtures_still_valid():
    DealDetail.model_validate(held_detail())
    DealDetail.model_validate(watch_detail())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["fraud"].update(flagged=False),  # held without flagged
        lambda d: d["fraud"].update(flagged_at=None),
        lambda d: d["fraud"].update(stage_at_flag=None),
        lambda d: d.update(held=True) or d["fraud"].update(flagged=False, held=False,
                                                           flagged_at=None, stage_at_flag=None),
    ],
)
def test_q14_detail_rejects_inconsistent_fraud(mutate):
    d = held_detail()
    mutate(d)
    with pytest.raises(ValidationError):
        DealDetail.model_validate(d)


def test_q14_detail_rejects_held_flag_without_fraud_flag():
    d = load("deal_detail.json")["Lone Star Dental"]
    d = copy.deepcopy(d)
    d["held"] = True
    with pytest.raises(ValidationError):
        DealDetail.model_validate(d)


def test_q14_detail_rejects_watch_with_flagged():
    d = held_detail()
    d["held"] = False
    d["watch"] = True
    with pytest.raises(ValidationError):
        DealDetail.model_validate(d)


def test_q14_summary_rejects_held_and_watch():
    s = DealDetail.model_validate(held_detail()).summary().model_dump()
    s["watch"] = True
    with pytest.raises(ValidationError):
        DealSummary.model_validate(s)


def test_q14_submission_models_stay_lenient():
    f = Fraud(flagged=False, held=True)
    g = Fraud(flagged=True, held=True)
    assert f.held and not f.flagged and g.flagged_at is None
    base = load("submission.json")["deals"]
    deal = copy.deepcopy(next(iter(base.values())))
    deal["fraud"] = {"flagged": True, "held": False}
    assert SubmissionDeal.model_validate(deal).fraud.flagged
    sub = {"team": "T", "set": "s", "deals": {"X": deal}, "history_flags": []}
    Submission.model_validate(sub)


# ------------------------------------------------------------ Q15: advance never rewinds


def test_q15_earlier_to_is_422_and_clock_unchanged(client):
    before = client.get("/api/clock").json()
    r = client.post("/api/clock/advance", json={"to": "2026-11-03T08:00-06:00"})
    assert r.status_code == 422
    assert "/api/clock/replay" in r.json()["detail"]
    assert client.get("/api/clock").json() == before


def test_q15_to_equal_now_is_200(client):
    now = load("clock.json")["now"]
    r = client.post("/api/clock/advance", json={"to": now})
    assert r.status_code == 200 and r.json()["now"] == now


def test_q15_to_after_end_clamps(client):
    r = client.post("/api/clock/advance", json={"to": "2030-01-01T00:00-06:00"})
    assert r.status_code == 200 and r.json()["now"] == load("clock.json")["end"]


def test_q15_hours_unchanged(client):
    r = client.post("/api/clock/advance", json={"hours": 1})
    assert r.json()["now"] == "2026-11-04T19:00-06:00"


# ------------------------------------------------------------ field names are pinned (UI depends on them)

SUMMARY = {"folder", "legal_name", "stage", "stage_entered_at", "sla_hours", "sla_pct", "needed_by",
           "needed_by_badge", "amount_requested", "held", "watch", "rm"}


def test_ui_contract_field_names_unchanged():
    from prism.contract import ClockReplay

    assert set(DealSummary.model_fields) == SUMMARY
    assert set(DealDetail.model_fields) == SUMMARY | {
        "facts", "issues", "resolved_issues", "stage_log", "human_requests", "alerts", "fraud"}
    assert set(SubmissionDeal.model_fields) == {
        "extracted", "issues", "stage_log", "human_requests", "alerts", "fraud"}
    assert set(Fraud.model_fields) == {
        "flagged", "held", "flagged_at", "stage_at_flag", "reason", "linked_deals"}
    assert set(Submission.model_fields) == {"team", "set", "deals", "history_flags"}
    assert set(Clock.model_fields) == {"now", "start", "end"}
    assert set(ClockAdvance.model_fields) == {"hours", "to"}
    assert set(ClockReplay.model_fields) == {"speed"}
