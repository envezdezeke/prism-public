"""Needed-by dates (MOVE-06, J18).

Typical stage durations come from ``history/stage_events.csv`` (median hours per stage);
when that file or a stage is missing the stage's SLA hours stand in. Stages with a human
gate are counted in business hours (people answer Mon-Fri), automatic stages in wall hours.
"""
from __future__ import annotations

import csv
import statistics
from datetime import datetime, timedelta
from typing import Optional

from prism.engine.config import Rules
from prism.engine.state import DealState
from prism.move import routing
from prism.move.runtime import MoveRuntime, get_runtime
from prism.move.timeutil import business_add, business_sub, parse_iso, to_sim

_HIST_CACHE: dict[tuple[str, int, int], dict[str, float]] = {}


def needed_by(state: DealState) -> Optional[datetime]:
    f = state.facts.get("needed_by")
    return parse_iso(f.value) if f is not None else None


def _history(rules: Rules) -> dict[str, float]:
    if rules.history_dir is None:
        return {}
    path = rules.history_dir / "stage_events.csv"
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _HIST_CACHE:
        return _HIST_CACHE[key]
    by_stage: dict[str, list[float]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    stage, hours = (row.get("stage") or "").strip(), float(row.get("hours_in_stage") or "")
                except (TypeError, ValueError):
                    continue  # bad rows are skipped
                if stage and hours >= 0 and hours == hours and hours != float("inf"):
                    by_stage.setdefault(stage, []).append(hours)
    except (OSError, csv.Error, UnicodeDecodeError):
        by_stage = {}
    out = {s: float(statistics.median(v)) for s, v in sorted(by_stage.items()) if v}
    _HIST_CACHE[key] = out
    return out


def typical_hours(rules: Rules, stage: str) -> float:
    """Median hours in ``stage`` from history, else the stage's SLA hours, else 0."""
    h = _history(rules).get(stage)
    if h is not None:
        return h
    return rules.stages.sla_hours(stage) or 0.0


def _advance(rules: Rules, stage: str, cur: datetime, hours: float) -> datetime:
    if rules.stages.gate(stage):
        return business_add(cur, hours * 60)
    return cur + timedelta(hours=hours)


def _back(rules: Rules, stage: str, cur: datetime, hours: float) -> datetime:
    if rules.stages.gate(stage):
        return business_sub(cur, hours * 60)
    return cur - timedelta(hours=hours)


def remaining_stages(state: DealState, rules: Rules) -> list[str]:
    pipe = list(rules.stages.pipeline)
    return pipe[pipe.index(state.stage):] if state.stage in pipe else pipe


def project_finish(state: DealState, rules: Rules, now: datetime) -> datetime:
    """When the deal would finish at typical speed: current stage remainder, then each later stage."""
    now = to_sim(now)
    stages = remaining_stages(state, rules)
    if not stages:
        return now
    entered = state.stage_entered_at
    elapsed = max((now - to_sim(entered)).total_seconds() / 3600, 0.0) if entered is not None else 0.0
    cur = _advance(rules, stages[0], now, max(typical_hours(rules, stages[0]) - elapsed, 0.0))
    for s in stages[1:]:
        cur = _advance(rules, s, cur, typical_hours(rules, s))
    return cur


def at_risk(state: DealState, rules: Rules, now: datetime) -> bool:
    nb = needed_by(state)
    return nb is not None and project_finish(state, rules, now) > nb


def latest_exit(state: DealState, rules: Rules, stage: str) -> Optional[datetime]:
    """Latest time ``stage`` can be finished and still meet needed_by (later stages at typical speed)."""
    nb = needed_by(state)
    if nb is None or stage not in rules.stages.pipeline:
        return None
    pipe = list(rules.stages.pipeline)
    cur = nb
    for s in reversed(pipe[pipe.index(stage) + 1:]):
        cur = _back(rules, s, cur, typical_hours(rules, s))
    return cur


def answer_by(state: DealState, rules: Rules) -> Optional[datetime]:
    """Latest time the committee approval can arrive and still fund by needed_by (None without one)."""
    stage = routing.stage_for_gate("committee_approval", rules)
    return latest_exit(state, rules, stage) if stage else None


def needed_by_badge(state: DealState, rules: Rules, now: datetime) -> str:
    """none | ok | at_risk | overdue."""
    nb = needed_by(state)
    if nb is None:
        return "none"
    rt = get_runtime(state)
    if rt.terminal:
        return "overdue" if rt.terminal == "closed" and rt.terminal_at and rt.terminal_at > nb else "ok"
    now = to_sim(now)
    if now > nb:
        return "overdue"
    return "at_risk" if rules.stages.pipeline and project_finish(state, rules, now) > nb else "ok"


def priority_key(state: DealState) -> tuple[int, str, str]:
    """Deadline deals first, earliest date first, then folder name (inbox and board order)."""
    nb = needed_by(state)
    return (0, to_sim(nb).isoformat(), state.folder) if nb is not None else (1, "", state.folder)


def next_signoff(state: DealState, rules: Rules, rt: MoveRuntime, now: datetime) -> tuple[Optional[str], Optional[str]]:
    """(person, gate type) who must act next: the current stage's gate, else the stage owner."""
    gate = rules.stages.gate(state.stage)
    if gate and isinstance(gate.get("type"), str):
        pid = routing.route_gate(gate["type"], state, rules, now)
        if pid:
            return pid, gate["type"]
    return rt.owner, None

