"""Shared types and the entry-point table: the hand-off contract for Phases 3, 4 and 5.

Per-tick order (prism/engine/run.py):  ingest -> fraud -> move
  1. ingest  (Phase 3, prism.read), for each deal with newly visible docs:
       read.classify.classify(doc) -> Doc           content only, never the filename, never raises
       read.extract.extract_facts(state, rules)      replaces state.facts
       read.extract.extract_entities(state, rules)   replaces state.entities
       read.issues.detect_issues(state, rules, now)  replaces state.issues (sorted open set)
  2. fraud   (Phase 5, prism.fraud), once per tick across all deals:
       fraud.decide.evaluate(states, baseline, cfg, now) -> FraudOutcome
         = build_graph -> compute_signals -> decide; holds are sticky
  3. move    (Phase 4, prism.move), per live deal:
       move.stage.step_deal(state, rules, now)       mutates stage, stage_log, human_requests, alerts
       move.routing.route_gate(gate, state, rules, now) -> teams.json id or None
       move.sla.sla_pct / sla_alerts(state, rules, now)

Workstreams replace stub BODIES, never these signatures or the dataclasses below.
A step that raises NotImplementedError is skipped (not built yet); any other exception
is recorded as DealState.error and freezes only that deal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, get_args

from prism.contract import (
    Alert,
    Extracted,
    Fact,
    Fraud,
    HumanRequest,
    Stage,
    StageLogEntry,
    SubmissionDeal,
    parse_sim_time,
)

PIPELINE: tuple[str, ...] = get_args(Stage)


@dataclass
class Doc:
    path: str  # arrivals path, POSIX, "<folder>/<file>"
    name: str  # last component, display only, NEVER used to classify
    arrive_at: datetime  # aware, -06:00
    abs_path: Path  # file on disk (may not exist)
    doc_class: Optional[str] = None  # set by read.classify
    period: Optional[str] = None  # "YYYY-MM" for bank statements
    meta: dict[str, Any] = field(default_factory=dict)  # producer, creator, page markers, ...


@dataclass(frozen=True)
class EntityRef:
    kind: str  # one of prism.contract.EntityKind
    value: str  # canonicalized
    source_doc: str  # Doc.path


@dataclass
class DealState:
    folder: str
    docs: list[Doc] = field(default_factory=list)  # visible docs, arrival order
    facts: dict[str, Fact] = field(default_factory=dict)  # keys = Extracted field names
    entities: list[EntityRef] = field(default_factory=list)  # graph attributes, filled by read
    features: dict[str, Any] = field(default_factory=dict)  # deposit/terms features for F4-F6
    issues: list[str] = field(default_factory=list)  # open KIND:target strings, sorted
    resolved_issues: list[str] = field(default_factory=list)
    stage: str = PIPELINE[0]
    stage_entered_at: Optional[datetime] = None
    stage_log: list[StageLogEntry] = field(default_factory=list)
    human_requests: list[HumanRequest] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    fraud: Fraud = field(default_factory=Fraud)
    last_arrival_at: Optional[datetime] = None
    error: Optional[str] = None  # first crash in this deal's pipeline; freezes the deal

    def to_submission_deal(self) -> SubmissionDeal:
        extracted = {}
        for name in Extracted.model_fields:
            fact = self.facts.get(name)
            extracted[name] = None if fact is None else fact.value

        def by_time(items, attr):
            return sorted(items, key=lambda x: parse_sim_time(getattr(x, attr)))

        fraud = self.fraud.model_copy(update={"linked_deals": sorted(set(self.fraud.linked_deals))})
        return SubmissionDeal(
            extracted=Extracted(**extracted),
            issues=sorted(set(self.issues)),
            stage_log=by_time(self.stage_log, "entered_at"),
            human_requests=by_time(self.human_requests, "at"),
            alerts=by_time(self.alerts, "at"),
            fraud=fraud,
        )
