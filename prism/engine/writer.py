"""Submission writer (D-04): canonical, byte-reproducible JSON that is always written.

A deal whose state cannot be serialised falls back to an empty SubmissionDeal and carries
an ``_error`` key (the organizers ignore extra keys; our validator strips and warns on them).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from prism.contract import SubmissionDeal
from prism.engine.state import DealState


def build_submission(
    states: Mapping[str, DealState],
    history_flags: list[str],
    errors: list[str],
    *,
    team: str,
    set_name: str,
) -> dict[str, Any]:
    deals: dict[str, Any] = {}
    for folder in sorted(states):
        state = states[folder]
        error = state.error
        try:
            deal = state.to_submission_deal().model_dump(mode="json")
        except Exception as e:  # noqa: BLE001 - the file must always be written
            msg = " ".join(f"{type(e).__name__}: {e}".split())
            deal = SubmissionDeal(
                extracted={}, issues=[], stage_log=[], human_requests=[], alerts=[]
            ).model_dump(mode="json")
            error = f"serialize: {msg}"[:300]
        if error:
            deal["_error"] = error
        deals[folder] = deal
    payload: dict[str, Any] = {
        "team": team,
        "set": set_name,
        "deals": deals,
        "history_flags": sorted(set(history_flags)),
    }
    if errors:
        payload["_errors"] = list(errors)
    return payload


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_submission(payload: Mapping[str, Any], out: Path | str) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(payload))
    return p
