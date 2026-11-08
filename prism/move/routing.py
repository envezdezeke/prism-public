"""Gate routing (owner: Phase 4), all from data.

No person id appears in code. People, backups, thresholds and out-of-office windows come
from stages.json, teams.json and assignments.json. Amounts are compared in integer cents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from prism.engine.config import Rules, Teams
from prism.engine.money import to_cents
from prism.engine.state import DealState
from prism.engine.visibility import SIM_TZ
from prism.move.settings import get_config

_OOO_RE = re.compile(
    r"OUT OF OFFICE\s+(\d{4}-\d{2}-\d{2})(?:[ T]+(\d{1,2}:\d{2}))?\s+through\s+(\d{4}-\d{2}-\d{2})(?:[ T]+(\d{1,2}:\d{2}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Route:
    to: str
    primary: str
    covering: bool = False  # True when ``to`` is standing in for ``primary``
    until: Optional[datetime] = None  # end of the primary's out-of-office window


def ids_in(text: Any, teams: Teams) -> list[str]:
    """Known person ids in ``text``, in order of position, each once."""
    if not isinstance(text, str):
        return []
    found: list[tuple[int, str]] = []
    for pid in sorted(teams.people):
        for m in re.finditer(r"(?<!\w)" + re.escape(pid) + r"(?![A-Za-z0-9])", text):
            found.append((m.start(), pid))
    seen: set[str] = set()
    out = []
    for _, pid in sorted(found):
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def person_name(teams: Teams, pid: Optional[str]) -> str:
    if not pid:
        return "nobody"
    return str((teams.people.get(pid) or {}).get("name") or pid)


def _stamp(date_s: str, time_s: Optional[str], default: str) -> Optional[datetime]:
    try:
        hh, mm = (time_s or default).split(":")
        y, mo, d = (int(x) for x in date_s.split("-"))
        return datetime(y, mo, d, int(hh), int(mm), tzinfo=SIM_TZ)
    except ValueError:
        return None


def ooo_windows(pid: str, teams: Teams) -> list[tuple[datetime, datetime]]:
    notes = (teams.people.get(pid) or {}).get("notes")
    if not isinstance(notes, str):
        return []
    out = []
    for m in _OOO_RE.finditer(notes):
        start, end = _stamp(m.group(1), m.group(2), "00:00"), _stamp(m.group(3), m.group(4), "23:59")
        if start is not None and end is not None:
            out.append((start, end))
    return out


def ooo_until(pid: str, teams: Teams, now: datetime) -> Optional[datetime]:
    for start, end in ooo_windows(pid, teams):
        if start <= now <= end:  # both edges inclusive
            return end
    return None


def is_out(pid: str, teams: Teams, now: datetime) -> bool:
    return ooo_until(pid, teams, now) is not None


def available(pid: str, teams: Teams, now: datetime) -> str:
    """pid, else its backup, else the backup's backup... First person not out; pid if all are."""
    cur: Optional[str] = pid
    seen: set[str] = set()
    while cur and cur not in seen:
        if not is_out(cur, teams, now):
            return cur
        seen.add(cur)
        cur = teams.backup_of(cur)
    return pid


def stage_for_gate(gate_type: str, rules: Rules) -> Optional[str]:
    for s in rules.stages.pipeline:
        g = rules.stages.gate(s)
        if g and g.get("type") == gate_type:
            return s
    return None


def committee_delegated(state: DealState, stage_cfg: dict[str, Any]) -> Optional[bool]:
    """True when the amount is within the delegated limit (integer cents), None if unknown."""
    r = stage_cfg.get("routing")
    if not isinstance(r, dict):
        return None
    fact = state.facts.get("amount_requested")
    if fact is None:
        return None
    try:
        return to_cents(fact.value) <= to_cents(r["delegated_max_amount"])
    except (ValueError, KeyError, TypeError):
        return None


def _primary(gate_type: str, state: DealState, rules: Rules) -> Optional[str]:
    teams = rules.teams
    if gate_type == "fraud_hold":
        ids = ids_in(rules.stages.fraud_hold.get("who_decides"), teams)
        return ids[0] if ids else None
    role = get_config().gate_roles.get(gate_type)
    if role == "credit_officer":
        pid = rules.assignments.credit_officer(state.folder)
        if pid:
            return pid
    elif role == "relationship_manager":
        pid = rules.assignments.rm(state.folder)
        if pid:
            return pid
    stage = stage_for_gate(gate_type, rules)
    if stage is None:
        return None
    cfg = rules.stages.stages.get(stage, {})
    r = cfg.get("routing")
    if isinstance(r, dict):
        delegated = committee_delegated(state, cfg)
        if delegated is None:
            return None
        key = "delegated_approver" if delegated else "full_committee_chair"
        pid = r.get(key)
        return pid if isinstance(pid, str) and pid in teams.people else None
    gate = rules.stages.gate(stage) or {}
    for text in (gate.get("who"), teams.routing.get(stage)):
        ids = ids_in(text, teams)
        if ids:
            return ids[0]
    return None


def route_detail(gate_type: str, state: DealState, rules: Rules, now: datetime) -> Optional[Route]:
    primary = _primary(gate_type, state, rules)
    if primary is None:
        return None
    to = available(primary, rules.teams, now)
    if to == primary:
        return Route(to=to, primary=primary)
    return Route(to=to, primary=primary, covering=True, until=ooo_until(primary, rules.teams, now))


def route_gate(gate_type: str, state: DealState, rules: Rules, now: datetime) -> Optional[str]:
    """Return a teams.json id or None (unknown gate, or an input the route needs is missing)."""
    r = route_detail(gate_type, state, rules, now)
    return r.to if r else None


def _pool(stage: str, rules: Rules) -> list[str]:
    return ids_in(rules.teams.routing.get(stage), rules.teams)


def stage_owner(stage: str, state: DealState, rules: Rules, now: datetime) -> Optional[str]:
    """Who owns the stage for ladder purposes: its gate's route, else the routing table."""
    gate = rules.stages.gate(stage)
    if gate and isinstance(gate.get("type"), str):
        pid = route_gate(gate["type"], state, rules, now)
        if pid:
            return pid
    pool = _pool(stage, rules)
    if not pool:
        return None
    if len(pool) == 1:
        return available(pool[0], rules.teams, now)
    # J2: round-robin by first-seen order of deals; the list is per Rules object (per run).
    orders = getattr(rules, "_move_intake_order", None)
    if orders is None:
        orders = {}
        object.__setattr__(rules, "_move_intake_order", orders)
    seen = orders.setdefault(stage, [])
    if state.folder not in seen:
        seen.append(state.folder)
    return available(pool[seen.index(state.folder) % len(pool)], rules.teams, now)


def rm_of(state: DealState, rules: Rules) -> Optional[str]:
    return rules.assignments.rm(state.folder)

