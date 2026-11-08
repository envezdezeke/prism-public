"""Helpers for the Move tests (a module, not a test file: named to fit the test glob).

Everything is synthetic: invented people, invented folders, rules written to tmp_path and
loaded through the real loaders, so no sample data and no sample names are needed.
"""
from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from prism.contract import Fact
from prism.engine.config import Rules, load_assignments, load_stages, load_teams
from prism.engine.state import DealState, Doc
from prism.engine.visibility import parse_time
from prism.move import stage as move_stage
from prism.move.settings import get_config, use_config

# Monday 2026-03-02. The credit officer below is out Tue 03-03 08:00 through Fri 03-06 18:00.
MON = "2026-03-02"


def at(s: str) -> datetime:
    return parse_time(s)


def _person(pid, name, backup=None, esc=None, notes=""):
    return {"id": pid, "name": name, "team": "x", "role": "x", "backup": backup, "escalates_to": esc, "notes": notes}


PEOPLE = [
    _person("ivy.one", "Ivy One", "ivy.two", "ivy.boss"),
    _person("ivy.two", "Ivy Two", "ivy.one", "ivy.boss"),
    _person("ivy.boss", "Ivy Boss", None, "top.lead"),
    _person("cora.credit", "Cora Credit", "cal.credit", "chief.credit",
            "OUT OF OFFICE 2026-03-03 08:00 through 2026-03-06 18:00. Backup: Cal Credit."),
    _person("cal.credit", "Cal Credit", "cass.credit", "chief.credit"),
    _person("cass.credit", "Cass Credit", "cal.credit", "chief.credit"),
    _person("chief.credit", "Chief Credit", None, "top.lead"),
    _person("dele.gate", "Dele Gate", "chief.credit", "chief.credit"),
    _person("chair.gate", "Chair Gate", "chief.credit", "chief.credit"),
    _person("prod.one", "Prod One", "top.lead", "top.lead"),
    _person("rm.alpha", "Rita Alpha", "rm.beta", "top.lead"),
    _person("rm.beta", "Remy Beta", "rm.alpha", "top.lead"),
    _person("close.er", "Cleo Closer", "prod.one", "top.lead"),
    _person("fraud.an", "Fran Analyst", "fraud.head", "fraud.head"),
    _person("fraud.head", "Fern Head", None, "top.lead"),
    _person("top.lead", "Tess Lead", None, None),
]

STAGES = {
    "pipeline": ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"],
    "stages": {
        "Intake": {"sla_hours": 8, "owner": "Intake",
                   "required_to_exit": ["loan application (signed)", "photo ID", "certificate of formation", "EIN letter"],
                   "human_gate": None},
        "Credit": {"sla_hours": 48, "owner": "Credit",
                   "required_to_exit": ["bank statements", "financial statements", "tax return"],
                   "human_gate": {"type": "credit_decision", "who": "assigned credit officer (backup if out of office)",
                                  "options": ["approve", "decline", "request_info"]}},
        "Committee": {"sla_hours": 48, "sla_hours_delegated": 24, "owner": "Committee",
                      "routing": {"delegated_max_amount": 249999.99, "delegated_approver": "dele.gate",
                                  "full_committee_chair": "chair.gate"},
                      "required_to_exit": ["credit memo (system-written summary of what was seen, decided and why)"],
                      "human_gate": {"type": "committee_approval", "who": "delegated approver or committee chair (by amount)"}},
        "Product": {"sla_hours": 16, "owner": "Product",
                    "required_to_exit": ["product and pricing chosen",
                                         "equipment loans: vendor quote present; vendor identity and remit-to account verified"],
                    "human_gate": None},
        "Committed": {"sla_hours": 48, "owner": "Relationship Management",
                      "required_to_exit": ["commitment letter signed"],
                      "human_gate": {"type": "commitment_signoff", "who": "deal's relationship manager"}},
        "Closed": {"sla_hours": 24, "owner": "Closing",
                   "required_to_exit": ["final fraud screen clear", "disbursement account verified"],
                   "human_gate": {"type": "funding_release", "who": "close.er"}},
    },
    "fraud_hold": {"who_decides": "Fraud Operations (fraud.an, backup fraud.head)"},
    "stuck_deals": {"alert_at_pct_of_sla": 100, "escalate_at_pct_of_sla": 150,
                    "second_escalation_at_pct_of_sla": 200, "second_escalation_to": "top.lead"},
}

ROUTING = {
    "Intake": "assigned intake specialist (round-robin ivy.one / ivy.two)",
    "Credit": "credit officer assigned in sim/assignments.json",
    "Committee": "by amount",
    "Product": "prod.one",
    "Committed": "the deal's relationship manager",
    "Closed": "close.er releases funds",
    "FraudHold": "fraud.an places or releases fraud holds (backup fraud.head)",
}

ASSIGNMENTS = {
    "Acme Co": {"relationship_manager": "rm.alpha", "credit_officer": "cal.credit"},
    "Bolt Inc": {"relationship_manager": "rm.beta", "credit_officer": "cora.credit"},
    "Cedar Ltd": {"relationship_manager": "rm.alpha", "credit_officer": "cass.credit"},
}


def make_rules(tmp_path: Path, *, stages: Optional[dict] = None, people: Optional[list] = None,
               routing: Optional[dict] = None, assignments: Optional[dict] = None,
               history: Optional[str] = None) -> Rules:
    """Write synthetic stages/teams/assignments and load them with the real loaders."""
    root = Path(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "stages.json").write_text(json.dumps(stages or STAGES), encoding="utf-8")
    (root / "teams.json").write_text(
        json.dumps({"people": people or PEOPLE, "routing": routing or ROUTING}), encoding="utf-8")
    (root / "assignments.json").write_text(json.dumps(assignments or ASSIGNMENTS), encoding="utf-8")
    hist_dir = None
    if history is not None:
        hist_dir = root / "history"
        hist_dir.mkdir(exist_ok=True)
        (hist_dir / "stage_events.csv").write_text(history, encoding="utf-8")
    return Rules(
        stages=load_stages(root / "stages.json"),
        teams=load_teams(root / "teams.json"),
        assignments=load_assignments(root / "assignments.json"),
        history_dir=hist_dir,
    )


def fact(value: Any, src: str = "deal/application.pdf") -> Fact:
    return Fact(value=value, source_doc=src, confidence=0.9)


def make_state(folder: str = "Acme Co", arrive: str = f"{MON}T09:00", *, amount: Any = 100000,
               product: str = "Term Loan", term: int = 24, purpose: str = "working_capital",
               legal_name: Optional[str] = "Acme Company LLC", needed_by: Optional[str] = None,
               issues: Optional[list[str]] = None, profile: Optional[dict] = None,
               last_arrival: Optional[str] = None, facts: bool = True) -> DealState:
    t = at(arrive)
    st = DealState(folder=folder)
    st.docs.append(Doc(path=f"{folder}/app.pdf", name="app.pdf", arrive_at=t, abs_path=Path("app.pdf")))
    st.last_arrival_at = at(last_arrival) if last_arrival else t
    if facts:
        if legal_name:
            st.facts["legal_name"] = fact(legal_name)
        st.facts["amount_requested"] = fact(amount)
        st.facts["product"] = fact(product)
        if term is not None:
            st.facts["term_months"] = fact(term)
        st.facts["purpose_category"] = fact(purpose)
        if needed_by:
            st.facts["needed_by"] = fact(needed_by)
    st.issues = sorted(issues or [])
    if profile is not None:
        st.features["profile"] = profile
    return st


def add_doc(state: DealState, arrive: str, name: str = "late.pdf") -> None:
    t = at(arrive)
    state.docs.append(Doc(path=f"{state.folder}/{name}", name=name, arrive_at=t, abs_path=Path(name)))
    state.last_arrival_at = t


def tick(state: DealState, rules: Rules, *times: str) -> None:
    for s in times:
        move_stage.step_deal(state, rules, at(s))


def kinds(state: DealState) -> list[tuple[str, str]]:
    return [(r.type, r.to) for r in state.human_requests]


@contextmanager
def persona(name: str) -> Iterator[None]:
    cfg = get_config()
    r = dataclasses.replace(cfg.responder, default=name, by_person={}, by_type={})
    with use_config(dataclasses.replace(cfg, responder=r)):
        yield


GOOD_PROFILE = {
    "dba": "Acme Bakery",
    "ownership": "Owned 100% by the founder",
    "formation_date": "2017-03-01",
    "application_date": "2026-03-02",
    "preparer": "A. Bookkeeper",
    "use_of_proceeds": "Seasonal inventory",
    "collateral": "Business assets (blanket lien)",
    "statement_months": 6,
    "tax_doc": "tax_return",
    "net_income_ytd_cents": 62_000_00,
    "ytd_months": 10,
    "deposits_annualized_cents": 680_000_00,
    "cash_cents": 70_000_00,
    "receivables_cents": 40_000_00,
    "liabilities_cents": 90_000_00,
    "relationship": {"status": "new"},
}
