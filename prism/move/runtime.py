"""Per-deal Move memory.

``DealState`` has no Move field (its fields are pinned by a test), so the runtime hangs off
the state as the instance attribute ``_move``. Contract models (HumanRequest, StageLogEntry,
Alert) stay in the state; this holds only the bookkeeping around them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from prism.engine.state import DealState


@dataclass
class ReqMeta:
    idx: int  # index into state.human_requests
    gate: str  # request type
    stage: str
    group: str  # first answer in a group wins; the rest are superseded
    to: str
    kind: str  # gate | reminder | copy | info | fraud | fraud_copy
    at: datetime
    due: Optional[datetime] = None  # when a scripted persona answers (None: never)
    recorded: bool = False  # an answer was written by record_reply
    processed: bool = False  # the stage machine applied that answer
    superseded: bool = False
    option: Optional[str] = None
    answered_at: Optional[datetime] = None


@dataclass(frozen=True)
class View:
    """What Read can change between ticks."""

    issues: tuple[str, ...] = ()
    last_arrival: Optional[datetime] = None
    first_doc: Optional[datetime] = None
    facts: frozenset[str] = frozenset()


@dataclass
class MoveRuntime:
    started: bool = False
    first_doc_at: Optional[datetime] = None
    owner: Optional[str] = None
    ready_at: Optional[datetime] = None
    eval_pending: bool = False
    gate_sent: dict[str, bool] = field(default_factory=dict)
    gate_satisfied: dict[str, bool] = field(default_factory=dict)
    gate_option: dict[str, str] = field(default_factory=dict)
    requested_issues: set[str] = field(default_factory=set)
    issue_requested_at: dict[str, datetime] = field(default_factory=dict)
    emitted: set[str] = field(default_factory=set)
    alert_times: dict[str, datetime] = field(default_factory=dict)
    reqs: list[ReqMeta] = field(default_factory=list)
    decisions: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    frozen_at: Optional[datetime] = None
    frozen_total: timedelta = timedelta(0)  # time spent frozen in the current stage (SLA clock excludes it)
    terminal: Optional[str] = None  # closed | declined | stopped
    terminal_at: Optional[datetime] = None
    watermark: Optional[datetime] = None
    view: Optional[View] = None
    credit_memo: Any = None
    resend_after: Optional[datetime] = None  # request_info answered: reopen after a later arrival
    covering: dict[str, dict[str, Any]] = field(default_factory=dict)
    limit_hours: Optional[float] = None
    delegated: Optional[bool] = None
    fraud_sent_at: Optional[datetime] = None
    fraud_chase1_at: Optional[datetime] = None
    fraud_chase2_at: Optional[datetime] = None
    reminded: set[str] = field(default_factory=set)  # groups already reminded


def get_runtime(state: DealState) -> MoveRuntime:
    rt = getattr(state, "_move", None)
    if rt is None:
        rt = MoveRuntime()
        state._move = rt  # type: ignore[attr-defined]
    return rt
