"""API and submission contract: Pydantic v2 models for every UI endpoint.

Submission-facing models (Submission, SubmissionDeal and their parts) mirror
the organizers' submission format exactly. UI-facing models extend that data.
Change a model here, regenerate fixtures (python -m prism.derive), tell the
other person.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- enums

Stage = Literal["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]
PurposeCategory = Literal[
    "working_capital", "equipment", "inventory", "payroll", "expansion", "refinance"
]
Product = Literal[
    "Term Loan", "Equipment Loan", "Revolving Line of Credit", "Working Capital Loan"
]
RequestType = Literal[
    "credit_decision",
    "committee_approval",
    "commitment_signoff",
    "funding_release",
    "fraud_hold",
    "fraud_release",
    "info_request",
]
AlertLevel = Literal["alert", "escalation"]

# ---------------------------------------------------------------- issues

ISSUE_RE = re.compile(
    r"^(MISSING_DOC|MISSING_PAGE):(application|guarantor_id|certificate_of_formation"
    r"|ein_letter|bank_statement_\d{4}-\d{2}|financial_statements|tax_return|equipment_quote)$"
    r"|^MISMATCH:(legal_name|ein|amount_requested|guarantor_name|guarantor_dob"
    r"|guarantor_address|annual_revenue|bank_balance)$"
    r"|^OLD_TRICK:(STMT_TAMPER|REV_INFLATION|ID_MISMATCH|DUP_EIN|SHELL_ADDRESS)$",
    re.ASCII,
)


def _check_issue(v: str) -> str:
    if not ISSUE_RE.fullmatch(v):
        raise ValueError(f"invalid issue string: {v!r}")
    return v


Issue = Annotated[str, AfterValidator(_check_issue)]

# ---------------------------------------------------------------- sim time


def parse_sim_time(s: str) -> datetime:
    if "T" not in s:
        raise ValueError(f"sim time needs a 'T' separator: {s!r}")
    if not s.endswith("-06:00"):
        raise ValueError(f"sim time must end with -06:00: {s!r}")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"sim time must be timezone-aware: {s!r}")
    return dt


def format_sim_time(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _check_sim_time(v: str) -> str:
    parse_sim_time(v)
    return v


SimTime = Annotated[str, AfterValidator(_check_sim_time)]

# ---------------------------------------------------------------- clock


class Clock(ContractModel):
    now: SimTime
    start: SimTime
    end: SimTime


class ClockAdvance(ContractModel):
    hours: Optional[float] = Field(default=None, gt=0)
    to: Optional[SimTime] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ClockAdvance":
        if (self.hours is None) == (self.to is None):
            raise ValueError("provide exactly one of 'hours' or 'to'")
        return self


class ClockReplay(ContractModel):
    speed: float = Field(default=1.0, gt=0)


# ---------------------------------------------------------------- submission-facing

Number = Union[int, float]


class Extracted(ContractModel):
    legal_name: Optional[str] = None
    ein: Optional[str] = None
    amount_requested: Optional[Number] = None
    purpose_category: Optional[PurposeCategory] = None
    product: Optional[Product] = None
    term_months: Optional[int] = None
    guarantor_name: Optional[str] = None
    annual_revenue_stated: Optional[Number] = None
    needed_by: Optional[SimTime] = None


class StageLogEntry(ContractModel):
    stage: Stage
    entered_at: SimTime
    exited_at: Optional[SimTime] = None
    decision: Optional[str] = None
    why: str


class HumanRequest(ContractModel):
    type: RequestType
    to: str
    at: SimTime
    message: str
    answer: Optional[str] = None
    answered_at: Optional[SimTime] = None


class Alert(ContractModel):
    at: SimTime
    stage: Stage
    level: AlertLevel
    to: str
    message: str


class Fraud(ContractModel):
    flagged: bool = False
    held: bool = False
    flagged_at: Optional[SimTime] = None
    stage_at_flag: Optional[Stage] = None
    reason: Optional[str] = None
    linked_deals: list[str] = Field(default_factory=list)


class SubmissionDeal(ContractModel):
    extracted: Extracted
    issues: list[Issue]
    stage_log: list[StageLogEntry]
    human_requests: list[HumanRequest]
    alerts: list[Alert]
    fraud: Fraud = Field(default_factory=Fraud)


class Submission(ContractModel):
    team: str
    set: str
    deals: dict[str, SubmissionDeal]
    history_flags: list[str]

    @model_validator(mode="after")
    def _history_prefix(self) -> "Submission":
        for h in self.history_flags:
            if not h.startswith("H-"):
                raise ValueError(f"history flag must start with 'H-': {h!r}")
        return self


# ---------------------------------------------------------------- UI-facing: deals


class Fact(ContractModel):
    value: Union[str, int, float]
    source_doc: str
    confidence: float = Field(ge=0, le=1)


class ExtractedFacts(ContractModel):
    legal_name: Optional[Fact] = None
    ein: Optional[Fact] = None
    amount_requested: Optional[Fact] = None
    purpose_category: Optional[Fact] = None
    product: Optional[Fact] = None
    term_months: Optional[Fact] = None
    guarantor_name: Optional[Fact] = None
    annual_revenue_stated: Optional[Fact] = None
    needed_by: Optional[Fact] = None


class DealSummary(ContractModel):
    folder: str
    legal_name: Optional[str] = None
    stage: Stage
    stage_entered_at: SimTime
    sla_hours: float = Field(gt=0)
    sla_pct: float = Field(ge=0)  # may exceed 1 for a stuck deal; freezes while held
    needed_by: Optional[SimTime] = None
    needed_by_badge: Literal["none", "ok", "at_risk", "overdue"]  # server-computed
    amount_requested: Optional[Number] = None
    held: bool
    watch: bool  # UI-only, never in the submission
    rm: Optional[str] = None

    @model_validator(mode="after")
    def _held_not_watch(self) -> "DealSummary":
        if self.held and self.watch:
            raise ValueError("a held deal cannot also be on watch")
        return self


_SUMMARY_FIELDS = tuple(DealSummary.model_fields)


class DealDetail(DealSummary):
    facts: ExtractedFacts
    issues: list[Issue]
    resolved_issues: list[Issue]
    stage_log: list[StageLogEntry]
    human_requests: list[HumanRequest]
    alerts: list[Alert]
    fraud: Fraud

    @model_validator(mode="after")
    def _fraud_consistent(self) -> "DealDetail":
        f = self.fraud
        if f.held and not f.flagged:
            raise ValueError("fraud.held requires fraud.flagged")
        if f.flagged and (f.flagged_at is None or f.stage_at_flag is None):
            raise ValueError("fraud.flagged requires flagged_at and stage_at_flag")
        if self.held and not f.flagged:
            raise ValueError("held requires fraud.flagged")
        if self.watch and f.flagged:
            raise ValueError("a watch deal cannot have fraud.flagged")
        return self

    def summary(self) -> DealSummary:
        return DealSummary(**{k: getattr(self, k) for k in _SUMMARY_FIELDS})

    def to_submission_deal(self) -> SubmissionDeal:
        extracted = {}
        for name in Extracted.model_fields:
            fact = getattr(self.facts, name)
            extracted[name] = None if fact is None else fact.value
        return SubmissionDeal(
            extracted=Extracted(**extracted),
            issues=list(self.issues),
            stage_log=list(self.stage_log),
            human_requests=list(self.human_requests),
            alerts=list(self.alerts),
            fraud=self.fraud,
        )


class AlertFeedItem(Alert):
    id: str
    deal: str


# ---------------------------------------------------------------- UI-facing: inbox


class InboxItem(ContractModel):
    id: str
    deal: str
    type: RequestType
    to: str
    at: SimTime
    message: str
    options: list[str] = Field(min_length=1)
    status: Literal["pending", "answered"]
    answer: Optional[str] = None
    answered_at: Optional[SimTime] = None

    @model_validator(mode="after")
    def _status_consistent(self) -> "InboxItem":
        if self.status == "answered":
            if self.answer is None or self.answer not in self.options:
                raise ValueError("answered item needs an answer from its options")
            if self.answered_at is None:
                raise ValueError("answered item needs answered_at")
        elif self.answer is not None or self.answered_at is not None:
            raise ValueError("pending item must not have answer or answered_at")
        return self


class InboxReply(ContractModel):
    option: str
    note: Optional[str] = None


# ---------------------------------------------------------------- UI-facing: graph

EntityKind = Literal[
    "preparer",
    "phone",
    "registered_agent",
    "address",
    "vendor",
    "remit_account",
    "pdf_producer",
    "bank",
    "guarantor",
]
FamilyId = Literal["F1", "F2", "F3", "F4", "F5", "F6"]
FAMILY_LABELS: dict[str, str] = {
    "F1": "shared rare entity",
    "F2": "novelty or burst",
    "F3": "lookalike vendor or account",
    "F4": "document forensics",
    "F5": "deposit pattern",
    "F6": "terms shape",
}


class GraphNode(ContractModel):
    id: str
    type: Literal["deal", "history_deal", "entity"]
    label: str
    kind: Optional[EntityKind] = None

    @model_validator(mode="after")
    def _kind_for_entity(self) -> "GraphNode":
        if (self.type == "entity") != (self.kind is not None):
            raise ValueError("kind is required exactly when type is 'entity'")
        return self


class GraphEdge(ContractModel):
    source: str
    target: str
    entity_kind: EntityKind
    rarity: float = Field(ge=0, le=1)  # 1 is rarest
    weight: float = Field(ge=0)  # drives line thickness
    first_visible_at: SimTime  # drives the replay
    evidence: str


class FamilyResult(ContractModel):
    fired: bool
    evidence: str


class Cluster(ContractModel):
    id: str
    deals: list[str]
    families: dict[FamilyId, FamilyResult]
    tier: Literal["hold", "watch", "none"]

    @model_validator(mode="after")
    def _all_families(self) -> "Cluster":
        if set(self.families) != set(FAMILY_LABELS):
            raise ValueError("cluster must list all six families F1..F6")
        return self


class FraudGraph(ContractModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    clusters: list[Cluster]

    @model_validator(mode="after")
    def _referential(self) -> "FraudGraph":
        by_id = {n.id: n for n in self.nodes}
        for e in self.edges:
            if e.source not in by_id or e.target not in by_id:
                raise ValueError(f"edge references unknown node: {e.source} -> {e.target}")
            if by_id[e.source].type == "entity" or by_id[e.target].type != "entity":
                raise ValueError(f"edge must go from a deal to an entity: {e.source} -> {e.target}")
            if by_id[e.target].kind != e.entity_kind:
                raise ValueError(f"edge entity_kind differs from target kind: {e.target}")
        for c in self.clusters:
            for d in c.deals:
                if d not in by_id:
                    raise ValueError(f"cluster {c.id} references unknown node {d}")
                if by_id[d].type == "entity":
                    raise ValueError(f"cluster {c.id} lists entity node {d} as a deal")
        return self


# ---------------------------------------------------------------- UI-facing: agents


class TraceStep(ContractModel):
    step: int = Field(ge=1)
    thought: str
    tool: str
    args: dict[str, Any]
    result_summary: str


class InvestigatorTrace(ContractModel):
    cluster_id: str
    steps: list[TraceStep]
    case_summary: str
    mode: Literal["off", "replay", "live"]


class TrickResult(ContractModel):
    name: str
    description: str
    n_deals: int = Field(ge=0)
    caught: int = Field(ge=0)
    catch_rate: float = Field(ge=0, le=1)
    median_time_to_hold_hours: Optional[float] = None
    false_flags: int = Field(ge=0)

    @model_validator(mode="after")
    def _caught_le_n(self) -> "TrickResult":
        if self.caught > self.n_deals:
            raise ValueError("caught cannot exceed n_deals")
        return self


class RedTeamReport(ContractModel):
    tricks: list[TrickResult]
    generated_by: str
