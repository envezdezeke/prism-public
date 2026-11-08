"""The mock /api/graph is the shape the fraud-graph view reads (02-05) and Phase 5 must emit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prism.api.app import create_app
from prism.contract import FAMILY_LABELS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    return TestClient(create_app())


def test_graph_serves_the_fixture_unchanged(client):
    r = client.get("/api/graph")
    assert r.status_code == 200
    assert r.json() == json.loads((FIXTURES / "graph.json").read_text(encoding="utf-8"))


def test_graph_fields_the_ui_reads(client):
    g = client.get("/api/graph").json()
    nodes = {n["id"]: n for n in g["nodes"]}
    for e in g["edges"]:
        assert {"source", "target", "entity_kind", "rarity", "weight", "first_visible_at", "evidence"} <= set(e)
        assert nodes[e["target"]]["type"] == "entity"
    for c in g["clusters"]:
        assert set(c["families"]) == set(FAMILY_LABELS)
        assert all(d in nodes for d in c["deals"])
    # The view lights the first held cluster: its live deals must be held in the deal detail.
    detail = json.loads((FIXTURES / "deal_detail.json").read_text(encoding="utf-8"))
    ring = next(c for c in g["clusters"] if c["tier"] == "hold")
    live = [d for d in ring["deals"] if nodes[d]["type"] == "deal"]
    assert live and all(detail[d]["fraud"]["held"] for d in live)


def test_graph_at_keeps_deal_nodes_and_clusters(client):
    early = client.get("/api/graph", params={"at": "2026-11-02T08:00-06:00"}).json()
    full = client.get("/api/graph").json()
    deal_ids = {n["id"] for n in full["nodes"] if n["type"] != "entity"}
    assert deal_ids <= {n["id"] for n in early["nodes"]}
    assert early["clusters"] == full["clusters"]


def test_graph_503_when_mock_off_and_not_generated(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("PRISM_GRAPH_PATH", str(tmp_path / "missing.json"))
    assert TestClient(create_app()).get("/api/graph").status_code == 503
