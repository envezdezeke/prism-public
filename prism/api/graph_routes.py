"""GET /api/graph: the fixture under MOCK=1, the engine's graph (out/graph.json) under MOCK=0.

out/graph.json and out/graph_timeline.json are written by ``python -m prism.fraud.graph_out``
(env PRISM_GRAPH_PATH overrides the graph path). ``?at=`` replays the graph at a sim time.
Do not re-add this route to the mock-only router in app.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query

from prism.contract import FraudGraph, parse_sim_time
from prism.fraud.graph_out import filter_graph, timeline_path

REPO_ROOT = Path(__file__).resolve().parents[2]


def graph_path() -> Path:
    override = os.environ.get("PRISM_GRAPH_PATH", "").strip()
    return Path(override) if override else REPO_ROOT / "out" / "graph.json"


def _read_json(p: Path, missing: str) -> Any:
    if not p.is_file():
        raise HTTPException(status_code=503, detail=missing)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="graph output unreadable") from None


def _mock_filter(graph: dict, cutoff: Any) -> dict:
    edges = [e for e in graph["edges"] if parse_sim_time(e["first_visible_at"]) <= cutoff]
    used = {e["source"] for e in edges} | {e["target"] for e in edges}
    nodes = [n for n in graph["nodes"] if n["type"] != "entity" or n["id"] in used]
    return {**graph, "nodes": nodes, "edges": edges}


def build_graph_router(load_fixture: Callable[[str], Any], mock_enabled: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/graph", response_model=FraudGraph)
    def get_graph(at: Optional[str] = Query(default=None)) -> Any:
        cutoff = None
        if at is not None:
            try:
                cutoff = parse_sim_time(at)
            except ValueError:
                raise HTTPException(status_code=422, detail="at must be a sim time ending -06:00") from None
        if mock_enabled():
            graph = load_fixture("graph.json")
            return graph if cutoff is None else _mock_filter(graph, cutoff)
        p = graph_path()
        graph = _read_json(p, "graph output not generated")
        if cutoff is not None:
            timeline = _read_json(timeline_path(p), "graph timeline not generated")
            try:
                graph = filter_graph(graph, timeline, cutoff)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=503, detail="graph output unreadable") from None
        try:
            return FraudGraph.model_validate(graph)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="graph output unreadable") from None

    return router
