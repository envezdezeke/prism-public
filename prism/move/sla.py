"""SLA timers and alerts (owner: Phase 4).

Contract: ``sla_pct`` is elapsed sim hours over the stage SLA (may exceed 1; frozen while
held). ``sla_alerts`` returns only alerts newly due at ``now``, each exactly once, in time
order. Times are wall-clock sim hours: staff hours never pause the clock (J16).

The ladder percentages and the second-escalation person come from ``stages.json``
(``stuck_deals``); the limits come from each stage's ``sla_hours`` (or the delegated limit).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Optional

from prism.contract import Alert
from prism.engine.config import Rules
from prism.engine.state import DealState
from prism.move import messages
from prism.move.routing import committee_delegated, rm_of
from prism.move.runtime import MoveRuntime, get_runtime
from prism.move.timeutil import iso, to_sim

# (stuck_deals key, alert level) in ladder order. Index 0 is the stage alert, 1 and 2 escalate.
_LADDER = (
    ("alert_at_pct_of_sla", "alert"),
    ("escalate_at_pct_of_sla", "escalation"),
    ("second_escalation_at_pct_of_sla", "escalation"),
)


def stage_limit_hours(state: DealState, rules: Rules, stage: str) -> Optional[float]:
    """The stage's SLA hours; the committee-style stage uses its delegated limit on that route."""
    cfg = rules.stages.stages.get(stage, {})
    base = rules.stages.sla_hours(stage)
    delegated = cfg.get("sla_hours_delegated")
    if delegated is not None and committee_delegated(state, cfg) is True:
        return float(delegated)
    return base


def thresholds(rules: Rules) -> list[tuple[int, float, str]]:
    """[(ladder index, percent, level)] for the percentages the data pack defines."""
    sd = rules.stages.stuck_deals
    out = []
    for i, (key, level) in enumerate(_LADDER):
        v = sd.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out.append((i, float(v), level))
    return out


def _round_half_up(x: Fraction) -> int:
    return (2 * x.numerator + x.denominator) // (2 * x.denominator)


def crossing_time(entered: datetime, limit_hours: float, pct: float, shift: timedelta = timedelta(0)) -> datetime:
    """Stage entry plus limit times percent, rounded half-up to whole minutes (plus any frozen time)."""
    minutes = Fraction(str(limit_hours)) * 60 * Fraction(str(pct)) / 100
    return to_sim(entered) + shift + timedelta(minutes=_round_half_up(minutes))


def _limit(state: DealState, rules: Rules, rt: MoveRuntime) -> Optional[float]:
    return rt.limit_hours if rt.limit_hours is not None else stage_limit_hours(state, rules, state.stage)


def ladder_key(stage: str, pct: float) -> str:
    return f"{stage}:{pct:g}"


def next_threshold(state: DealState, rules: Rules, rt: MoveRuntime) -> Optional[tuple[int, float, str, datetime, str]]:
    """The next ladder step not yet emitted for the current stage: (index, pct, level, time, key)."""
    if state.stage_entered_at is None:
        return None
    limit = _limit(state, rules, rt)
    if not limit:
        return None
    for idx, pct, level in thresholds(rules):
        key = ladder_key(state.stage, pct)
        if key not in rt.emitted:
            return idx, pct, level, crossing_time(state.stage_entered_at, limit, pct, rt.frozen_total), key
    return None


def _manager(rules: Rules, rt: MoveRuntime, state: DealState) -> Optional[str]:
    for pid in (rt.owner, rm_of(state, rules)):
        mgr = rules.teams.escalates_to(pid) if pid else None
        if mgr:
            return mgr
    return rules.stages.stuck_deals.get("second_escalation_to")


def ladder_alerts(
    state: DealState, rules: Rules, rt: MoveRuntime, idx: int, pct: float, level: str, when: datetime,
    waiting: tuple[str, str],
) -> list[Alert]:
    """The alert entries for one crossed threshold (J3: owner and RM are separate entries; J4)."""
    stage = state.stage
    limit = _limit(state, rules, rt) or 0.0
    entered = to_sim(state.stage_entered_at)
    sd = rules.stages.stuck_deals
    a_time = rt.alert_times.get(ladder_key(stage, thresholds(rules)[0][1])) if thresholds(rules) else None
    e_pct = [p for i, p, _ in thresholds(rules) if i == 1]
    e_time = rt.alert_times.get(ladder_key(stage, e_pct[0])) if e_pct else None
    manager = _manager(rules, rt, state)
    if idx == 0:
        recipients = []
        for pid in (rt.owner, rm_of(state, rules)):
            if pid and pid not in recipients:
                recipients.append(pid)
    elif idx == 1:
        recipients = [manager] if manager else []
    else:
        second = sd.get("second_escalation_to")
        recipients = [second] if isinstance(second, str) and second else []
    text = messages.ladder_message(state, rules, rt, idx, pct, stage, entered, when, limit, waiting, a_time, e_time, manager)
    return [Alert(at=iso(when), stage=stage, level=level, to=pid, message=text) for pid in recipients]


def sla_pct(state: DealState, rules: Rules, now: datetime) -> float:
    """Fraction of the current stage SLA used at ``now`` (frozen while held, stopped when the deal ends)."""
    rt = get_runtime(state)
    if state.stage_entered_at is None or not rules.stages.pipeline:
        return 0.0
    limit = _limit(state, rules, rt)
    if not limit:
        return 0.0
    end = to_sim(now)
    for stop in (rt.frozen_at, rt.terminal_at):
        if stop is not None and stop < end:
            end = stop
    elapsed = (end - to_sim(state.stage_entered_at)) - rt.frozen_total
    return max(elapsed.total_seconds() / 3600 / limit, 0.0)


def _waiting_for(state: DealState, rules: Rules, rt: MoveRuntime, now: datetime) -> tuple[str, str]:
    from prism.move import stage as move_stage  # late import: stage imports this module

    return move_stage.waiting_for(state, rules, rt, tuple(sorted(set(state.issues))), now)


def sla_alerts(state: DealState, rules: Rules, now: datetime) -> list[Alert]:
    """Ladder alerts crossed by ``now`` that have not been emitted yet (each exactly once).

    ``step_deal`` already appends and records every alert it emits, so after a ``step_deal``
    call this returns []. Called alone, it returns the crossed alerts once.
    """
    rt = get_runtime(state)
    if not rules.stages.pipeline or not rt.started or rt.frozen_at is not None or rt.terminal:
        return []
    now = to_sim(now)
    out: list[Alert] = []
    while True:
        nt = next_threshold(state, rules, rt)
        if nt is None or nt[3] > now:
            return out
        idx, pct, level, when, key = nt
        out.extend(ladder_alerts(state, rules, rt, idx, pct, level, when, _waiting_for(state, rules, rt, when)))
        rt.emitted.add(key)
        rt.alert_times[key] = when

