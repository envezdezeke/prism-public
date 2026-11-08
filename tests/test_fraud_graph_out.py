"""Fraud graph producer (05-03): synthetic graph, at filter, contract and fixture parity, MOCK=0 route."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from test_fraud_links import CFG, PREP, PHONE, AGENT, PROD, _ring_world, hist_row, history, state
from test_move_support import add_doc

from prism.api.app import create_app
from prism.contract import FAMILY_LABELS, Cluster, FamilyResult, Fraud, FraudGraph
from prism.engine.state import EntityRef
from prism.fraud.graph_out import build_full, build_fraud_graph, filter_graph, timeline_path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FAM = {f: FamilyResult(fired=f in ("F1", "F3", "F5"), evidence="e") for f in FAMILY_LABELS}


def _world():
    base, a, b, honest = _ring_world()
    add_doc(b, "2026-03-04T09:00", name="tax.pdf")
    b.docs = b.docs[1:]  # Bolt Inc's first document arrives on day 3
    b.entities = [EntityRef(r.kind, r.value, "Bolt Inc/tax.pdf") for r in b.entities]
    a.fraud = Fraud(flagged=True, held=True, flagged_at="2026-03-03T09:00-06:00", stage_at_flag="Intake")
    states = {s.folder: s for s in (a, b, honest)}
    clusters = [
        Cluster(id="C-001", deals=["Acme Co", "Bolt Inc", "X-901", "X-902"], families=FAM, tier="hold"),
        Cluster(id="C-002", deals=["Hale Diner", "X-999"], families=FAM, tier="watch"),
    ]
    return base, states, clusters


def test_graph_nodes_edges_rarity_and_evidence():
    base, states, clusters = _world()
    g = build_fraud_graph(states, base, CFG, clusters=clusters)
    by_id = {n.id: n for n in g.nodes}
    ents = [n for n in g.nodes if n.type == "entity"]
    assert ents and all(n.id.startswith("ent-" + n.kind + "-") for n in ents)
    assert "X-999" in by_id  # cluster history members always get a node
    for e in g.edges:
        assert 0.0 <= e.rarity <= 1.0 and e.weight >= 0
        assert not any(ch in e.evidence for ch in "{}<>\n")
        assert "history deals." in e.evidence
        if by_id[e.source].type == "history_deal":
            assert e.evidence.startswith("Prior deal " + e.source)
            assert e.first_visible_at == "2026-03-02T09:00-06:00"
    acme = [e for e in g.edges if e.source == "Acme Co"]
    assert acme and all("app.pdf" in e.evidence for e in acme)
    bolt = [e for e in g.edges if e.source == "Bolt Inc"]
    assert bolt and all(e.first_visible_at == "2026-03-04T09:00-06:00" for e in bolt)
    order = [(n.type, n.id) for n in g.nodes]
    rank = {"deal": 0, "history_deal": 1, "entity": 2}
    assert order == sorted(order, key=lambda t: (rank[t[0]], t[1]))
    assert all(set(c.families) == set(FAMILY_LABELS) for c in g.clusters)


def test_common_entity_is_not_a_node():
    base = history([hist_row(i, bank_name="Big Common Bank") for i in range(1, 60)])
    a = state("Acme Co", [("bank", "Big Common Bank")])
    b = state("Bolt Inc", [("bank", "Big Common Bank")])
    g = build_fraud_graph({"Acme Co": a, "Bolt Inc": b}, base, CFG, clusters=[])
    assert not [n for n in g.nodes if n.type != "deal"] and g.edges == []


def test_at_filter_hides_late_deals_and_downgrades_early_holds():
    base, states, clusters = _world()
    full, tl = build_full(states, base, CFG, clusters=clusters)
    early = FraudGraph.model_validate(filter_graph(full, tl, "2026-03-02T12:00-06:00"))
    ids = {n.id for n in early.nodes}
    assert "Acme Co" in ids and "Bolt Inc" not in ids
    assert all(e.source != "Bolt Inc" for e in early.edges)
    c1 = next(c for c in early.clusters if c.id == "C-001")
    assert c1.tier == "watch" and "Bolt Inc" not in c1.deals
    late = FraudGraph.model_validate(filter_graph(full, tl, "2026-03-05T12:00-06:00"))
    assert next(c for c in late.clusters if c.id == "C-001").tier == "hold"
    assert len(late.edges) == len(full["edges"])


def test_deterministic_and_fixture_shape():
    base, states, clusters = _world()
    one = json.dumps(build_full(states, base, CFG, clusters=clusters), sort_keys=True)
    two = json.dumps(build_full(states, base, CFG, clusters=clusters), sort_keys=True)
    assert one == two
    fx = json.loads((FIXTURES / "graph.json").read_text(encoding="utf-8"))
    g = build_fraud_graph(states, base, CFG, clusters=clusters).model_dump(mode="json")
    assert set(g["nodes"][0]) == set(fx["nodes"][0])
    assert set(g["edges"][0]) == set(fx["edges"][0])
    assert set(g["clusters"][0]) == set(fx["clusters"][0])


def test_default_clusters_come_from_evaluate():
    base, states, _ = _world()
    g = build_fraud_graph(states, base, CFG)
    assert all(c.id.startswith("C-") for c in g.clusters)


def test_api_serves_engine_graph_under_mock_off(monkeypatch, tmp_path):
    base, states, clusters = _world()
    full, tl = build_full(states, base, CFG, clusters=clusters)
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(full), encoding="utf-8")
    timeline_path(p).write_text(json.dumps(tl), encoding="utf-8")
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("PRISM_GRAPH_PATH", str(p))
    c = TestClient(create_app())
    r = c.get("/api/graph")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["clusters"]] == ["C-001", "C-002"]
    early = c.get("/api/graph", params={"at": "2026-03-02T12:00-06:00"}).json()
    assert "Bolt Inc" not in {n["id"] for n in early["nodes"]}
    assert next(x for x in early["clusters"] if x["id"] == "C-001")["tier"] == "watch"
    assert c.get("/api/graph", params={"at": "nope"}).status_code == 422
    # MOCK=1 still serves the fixture.
    monkeypatch.setenv("MOCK", "1")
    fx = json.loads((FIXTURES / "graph.json").read_text(encoding="utf-8"))
    assert TestClient(create_app()).get("/api/graph").json() == fx
