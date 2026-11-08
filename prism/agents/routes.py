"""Agent API routes: fixtures under MOCK=1, recorded agent output (out/agents) under MOCK=0.

The investigator and red-team routes live here; do not re-add them to the mock-only router in app.py.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from prism.agents import client
from prism.contract import InvestigatorTrace, RedTeamReport


def _read(name: str, missing: str, unreadable: str) -> Any:
    p = client.agents_dir() / name
    if not p.is_file():
        raise HTTPException(status_code=503, detail=missing)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail=unreadable) from None


def build_agents_router(load_fixture: Callable[[str], Any], mock_enabled: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/investigator/{cluster_id}", response_model=InvestigatorTrace)
    def get_investigator(cluster_id: str) -> Any:
        if mock_enabled():
            trace = load_fixture("investigator.json")
            if trace["cluster_id"] != cluster_id:
                raise HTTPException(status_code=404, detail="unknown cluster")
            return trace
        doc = _read("investigator.json", "investigator output not generated", "investigator output unreadable")
        traces = doc.get("traces") if isinstance(doc, dict) else None
        if not isinstance(traces, dict):
            raise HTTPException(status_code=503, detail="investigator output unreadable")
        entry = traces.get(cluster_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown cluster")
        try:
            return InvestigatorTrace.model_validate(entry)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="investigator output unreadable") from None

    @router.get("/redteam", response_model=RedTeamReport)
    def get_redteam() -> Any:
        if mock_enabled():
            return load_fixture("redteam.json")
        doc = _read("redteam.json", "red-team report not generated", "red-team report unreadable")
        try:
            return RedTeamReport.model_validate(doc)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="red-team report unreadable") from None

    return router
