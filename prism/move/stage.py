"""Stage machine (owner: Phase 4, MOVE-01..).

Contract: ``step_deal`` mutates the state in place (stage, stage_entered_at, stage_log,
human_requests, alerts). A fraud hold blocks any advance. Called once per tick for every
deal without an error.

``step_deal`` catches up everything between the previous call and ``now`` in two passes of
one event loop. Pass 1 replays events strictly before ``now`` with the PREVIOUS view of what
Read reported (so a stage is not judged on an issue list that did not exist yet); pass 2
replays events up to and including ``now`` with the CURRENT view. Every action is guarded by
a runtime flag or emitted key, so calling twice at the same ``now`` changes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from prism.contract import Alert, StageLogEntry
from prism.engine.config import Rules
from prism.engine.state import DealState
from prism.move import credit_memo, deadline, inbox, messages, routing, sla
from prism.move.runtime import MoveRuntime, View, get_runtime
from prism.move.settings import MoveConfig, get_config
from prism.move.timeutil import fmt_when, iso, parse_iso, to_sim

MAX_EVENTS = 10_000  # T-qx3-02: one call cannot fan out forever

P_START, P_FREEZE, P_LADDER, P_CHASE, P_ANSWER, P_EVAL = 0, 1, 2, 3, 4, 5


@dataclass
class Run:
    state: DealState
    rules: Rules
    rt: MoveRuntime
    cfg: MoveConfig
    now: datetime
    view: View
    count: int = 0


# ------------------------------------------------------------ helpers


def _view(state: DealState) -> View:
    docs = state.docs
    first = min((to_sim(d.arrive_at) for d in docs), default=None)
    last = state.last_arrival_at
    if last is None:
        last = max((d.arrive_at for d in docs), default=None)
    facts = frozenset(k for k, f in state.facts.items() if f is not None and f.value not in (None, ""))
    return View(
        issues=tuple(sorted(set(state.issues))),
        last_arrival=to_sim(last) if last is not None else None,
        first_doc=first,
        facts=facts,
    )


def _matches(patterns, issue: str) -> bool:
    return any(p.fullmatch(issue) for p in patterns)


def _pipeline(run: Run) -> tuple[str, ...]:
    return run.rules.stages.pipeline


def _next_stage(run: Run, stage: str) -> Optional[str]:
    pipe = _pipeline(run)
    i = pipe.index(stage)
    return pipe[i + 1] if i + 1 < len(pipe) else None


def _is_frozen(run: Run) -> bool:
    return run.rt.frozen_at is not None


def _open_entry(run: Run) -> Optional[StageLogEntry]:
    for e in reversed(run.state.stage_log):
        if e.stage == run.state.stage and e.exited_at is None:
            return e
    return None


def _log_enter(run: Run, stage: str, t: datetime, reason: str) -> None:
    run.state.stage_log.append(
        StageLogEntry(stage=stage, entered_at=iso(t), exited_at=None, decision=None, why=f"Entered {stage}: {reason}")
    )


def _enter(run: Run, stage: str, t: datetime, reason: str) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    st.stage, st.stage_entered_at = stage, t
    rt.owner = routing.stage_owner(stage, st, rules, t)
    rt.limit_hours = sla.stage_limit_hours(st, rules, stage)
    rt.frozen_total = timedelta(0)
    cfg = rules.stages.stages.get(stage, {})
    rt.delegated = routing.committee_delegated(st, cfg)
    ready = t
    if not rules.stages.gate(stage):
        ready = t + timedelta(minutes=run.cfg.auto_stage_minutes.get(stage, 0))
    rt.ready_at = ready
    rt.eval_pending = True
    rt.resend_after = None
    _log_enter(run, stage, t, reason)
    _on_enter(run, stage, t)


def _gate_type(run: Run, stage: str) -> Optional[str]:
    g = run.rules.stages.gate(stage)
    return g.get("type") if g else None


def _on_enter(run: Run, stage: str, t: datetime) -> None:
    """Stage-specific work on entry: the committee-style stage writes the credit memo once."""
    if _gate_type(run, stage) == "committee_approval" and run.rt.credit_memo is None:
        run.rt.credit_memo = credit_memo.build_credit_memo(run.state, run.rules, t, answer_by=_answer_by(run, t))


def _answer_by(run: Run, t: datetime) -> Optional[datetime]:
    """Deadline deals only: the latest time the committee can answer and still fund by needed_by."""
    return deadline.answer_by(run.state, run.rules) if deadline.needed_by(run.state) else None


# ------------------------------------------------------------ requests


def _send_info(run: Run, t: datetime, items: list[str]) -> None:
    st, rt = run.state, run.rt
    msg = messages.info_request_message(st, items, run.rules)
    inbox.send_request(st, rt, "info_request", inbox.BORROWER, t, msg, st.stage, f"info:{len(rt.reqs)}", "info")
    for i in items:
        rt.requested_issues.add(i)
        rt.issue_requested_at[i] = t


def _gate_message(run: Run, gate_type: str, route: routing.Route, t: datetime) -> str:
    st, rules = run.state, run.rules
    rt = run.rt
    if gate_type == "credit_decision":
        return messages.credit_decision_message(st, rules, route)
    if gate_type == "committee_approval":
        memo = rt.credit_memo
        if memo is None:
            raise RuntimeError("move: committee request without a credit memo")
        cfg = rules.stages.stages.get(st.stage, {})
        return messages.committee_approval_message(st, rules, route, memo.short_text, rt.delegated, cfg)
    if gate_type == "commitment_signoff":
        who, when = rt.decisions.get("committee_approval", (None, None))
        return messages.commitment_signoff_message(st, rules, route, who, when)
    if gate_type == "funding_release":
        committed = (rt.decisions.get("commitment_signoff") or (None, None))[1]
        return messages.funding_release_message(st, rules, route, t, committed)
    return messages.generic_gate_message(st, rules, gate_type, route)


def _send_gate(run: Run, gate_type: str, t: datetime) -> bool:
    st, rt, rules = run.state, run.rt, run.rules
    route = routing.route_detail(gate_type, st, rules, t)
    if route is None:
        return False
    msg = _gate_message(run, gate_type, route, t)
    inbox.send_request(st, rt, gate_type, route.to, t, msg, st.stage, f"{st.stage}:{gate_type}", "gate")
    rt.gate_sent[st.stage] = True
    rt.covering[st.stage] = {"primary": route.primary, "until": route.until, "covering": route.covering}
    return True


# ------------------------------------------------------------ evaluation


def _blocking_issue(run: Run, stage: str) -> Optional[str]:
    pats = run.cfg.issues.blocking.get(stage, ())
    for i in run.view.issues:
        if _matches(pats, i):
            return i
    return None


def _is_product(run: Run, stage: str) -> bool:
    return stage == "Product" or stage in _vendor_stages(run)  # "Product" is a contract Stage literal


def _vendor_unverified(run: Run, stage: str) -> bool:
    st = run.state
    if not _is_product(run, stage):
        return False
    if messages.fact_value(st, "product") not in run.cfg.vendor_check_products:
        return False
    return messages.profile(st).get("vendor_verified") is False


def _vendor_stages(run: Run) -> tuple[str, ...]:
    """The stage whose required_to_exit mentions a vendor check (found in data, not by name)."""
    out = []
    for s in _pipeline(run):
        items = run.rules.stages.stages.get(s, {}).get("required_to_exit") or []
        if any(isinstance(x, str) and "vendor" in x.lower() for x in items):
            out.append(s)
    return tuple(out)


def _exit_why(run: Run, stage: str, detail: str, nxt: Optional[str]) -> str:
    items = run.rules.stages.stages.get(stage, {}).get("required_to_exit") or []
    seen = "; ".join(str(x) for x in items) if items else "nothing further required"
    tail = f" Moving to {nxt}." if nxt else " The deal is complete."
    return f"Checked: {seen}. {detail}{tail}"


def _evaluate(run: Run, t: datetime) -> None:
    st, rt, rules, cfg, view = run.state, run.rt, run.rules, run.cfg, run.view
    stage = st.stage
    rt.eval_pending = False
    if rt.frozen_at is not None or rt.terminal:
        return
    # Ask the borrower first, for everything missing at once (J11, MOVE-07).
    ask = [i for i in view.issues if _matches(cfg.issues.requestable, i) and i not in rt.requested_issues]
    if ask:
        _send_info(run, t, ask)
    # (1) facts the stage needs
    if any(f not in view.facts for f in cfg.required_facts.get(stage, ())):
        return
    # (3) anything open that blocks this stage
    if _blocking_issue(run, stage) is not None or _vendor_unverified(run, stage):
        return
    # (4) the human gate
    gate = rules.stages.gate(stage)
    gate_type = gate.get("type") if gate else None
    if gate_type:
        if rt.resend_after is not None:
            if view.last_arrival is not None and view.last_arrival > rt.resend_after:
                rt.resend_after = None
                rt.gate_sent[stage] = False
                rt.gate_satisfied[stage] = False
            else:
                return
        if not rt.gate_sent.get(stage):
            _send_gate(run, gate_type, t)
            return
        if not rt.gate_satisfied.get(stage):
            return
    # (5) exit
    _exit_stage(run, t, gate_type)


def _exit_detail(run: Run, stage: str, gate_type: Optional[str]) -> tuple[str, str]:
    """(decision, plain-words detail) for the log entry on exit."""
    st, rt, rules = run.state, run.rt, run.rules
    if gate_type:
        who, when = rt.decisions.get(gate_type, (None, None))
        option = rt.gate_option.get(stage, "approve")
        name = routing.person_name(rules.teams, who)
        return option, f"{name} answered {option.replace('_', ' ')} on {fmt_when(when)}." if when else f"{option}."
    if _is_product(run, stage):
        decision = f"standard pricing for {messages.product_text(st)}"
        if messages.fact_value(st, "product") in run.cfg.vendor_check_products:
            return decision, "Vendor and remit account were not flagged; standard pricing for the requested product."
        return decision, "No vendor check is needed for this product; standard pricing for the requested product."
    return "advanced", "All required items are present and consistent with the application."


def _exit_stage(run: Run, t: datetime, gate_type: Optional[str]) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    stage = st.stage
    nxt = _next_stage(run, stage)
    if nxt is None or _next_stage(run, nxt) is None:
        # Entering (or finishing) the last stage means money goes out: refuse while held, flagged
        # or any issue is open.
        if st.fraud.held or st.fraud.flagged or run.view.issues:
            return
    if not _before_exit(run, stage, t):
        return
    decision, detail = _exit_detail(run, stage, gate_type)
    entry = _open_entry(run)
    if entry is not None:
        carry = entry.why[entry.why.find(" Frozen "):] if " Frozen " in entry.why else ""  # keep the hold history
        entry.exited_at, entry.decision, entry.why = iso(t), decision, _exit_why(run, stage, detail, nxt) + carry
    if nxt is None:
        rt.terminal, rt.terminal_at = "closed", t
        rt.eval_pending = False
        return
    _enter(run, nxt, t, f"{stage} complete, {decision}")


def _before_exit(run: Run, stage: str, t: datetime) -> bool:
    if _gate_type(run, stage) == "committee_approval" and run.rt.credit_memo is None:
        raise RuntimeError("move: committee stage cannot exit without its credit memo")
    return True


# ------------------------------------------------------------ event sources

Event = tuple  # (time, priority, seq, kind, payload)


def _clamp(run: Run, t: datetime) -> datetime:
    wm = run.rt.watermark
    return t if wm is None or t >= wm else wm


def _eval_time(run: Run) -> Optional[datetime]:
    rt, view = run.rt, run.view
    if not rt.eval_pending or rt.ready_at is None:
        return None
    if rt.gate_satisfied.get(run.state.stage):
        return rt.ready_at
    if view.last_arrival is None:
        return rt.ready_at
    return max(rt.ready_at, view.last_arrival + timedelta(minutes=run.cfg.quiet_minutes))


def _candidates(run: Run) -> list[Event]:
    rt, view = run.rt, run.view
    out: list[Event] = []
    if rt.terminal:
        return out
    if not rt.started:
        if view.first_doc is not None:
            out.append((view.first_doc, P_START, 0, "start", None))
        return out
    for src in _SOURCES:
        out.extend(src(run))
    return out


def _src_eval(run: Run) -> list[Event]:
    if run.rt.frozen_at is not None:
        return []
    out: list[Event] = []
    t = _eval_time(run)
    if t is not None:
        out.append((_clamp(run, t), P_EVAL, 0, "eval", None))
    pend = [i for i in run.view.issues if _matches(run.cfg.issues.immediate, i) and i not in run.rt.requested_issues]
    if pend:
        out.append((_clamp(run, run.now), P_EVAL, 1, "immediate", pend))
    return out


def _src_answer(run: Run) -> list[Event]:
    st, rt = run.state, run.rt
    frozen = rt.frozen_at is not None
    out: list[Event] = []
    for m in rt.reqs:
        if m.processed or m.superseded or m.to == inbox.BORROWER:
            continue
        if frozen and m.gate != "fraud_hold":
            continue  # non-fraud answers are not applied while the deal is frozen
        if m.recorded:
            t = m.answered_at
        elif st.human_requests[m.idx].answer is None and m.due is not None:
            t = m.due
        else:
            continue
        out.append((_clamp(run, t), P_ANSWER, m.idx, "answer", m.idx))
    return out


def _src_freeze(run: Run) -> list[Event]:
    st, rt = run.state, run.rt
    if rt.frozen_at is not None or not (st.fraud.held or st.fraud.flagged):
        return []
    t = parse_iso(st.fraud.flagged_at) or run.now
    t = min(_clamp(run, t), run.now)
    return [(t, P_FREEZE, 0, "freeze", None)]


def _src_ladder(run: Run) -> list[Event]:
    rt = run.rt
    if rt.frozen_at is not None:
        return []
    nt = sla.next_threshold(run.state, run.rules, rt)
    return [(_clamp(run, nt[3]), P_LADDER, 0, "ladder", nt)] if nt else []


def _src_chase(run: Run) -> list[Event]:
    rt = run.rt
    if rt.frozen_at is None or inbox.fraud_answered(rt):
        return []
    orig = inbox.fraud_original(rt)
    if orig is None:
        return []
    if rt.fraud_chase1_at is None:
        return [(_clamp(run, orig.at + timedelta(hours=run.cfg.fraud_first_hours)), P_CHASE, 0, "chase1", None)]
    if rt.fraud_chase2_at is None:
        return [(_clamp(run, rt.fraud_chase1_at + timedelta(hours=run.cfg.fraud_second_hours)), P_CHASE, 1, "chase2", None)]
    return []


_SOURCES: list[Callable[[Run], list[Event]]] = [_src_freeze, _src_ladder, _src_chase, _src_answer, _src_eval]


def _h_start(run: Run, t: datetime, payload: Any) -> None:
    run.rt.started, run.rt.first_doc_at = True, t
    _enter(run, _pipeline(run)[0], t, f"first document arrived {fmt_when(t)}")


def _h_eval(run: Run, t: datetime, payload: Any) -> None:
    _evaluate(run, t)


def _h_immediate(run: Run, t: datetime, payload: Any) -> None:
    items = [i for i in payload if i not in run.rt.requested_issues]
    if items and run.rt.frozen_at is None:
        _send_info(run, t, items)


def _note_on_open_entry(run: Run, text: str) -> None:
    entry = _open_entry(run)
    if entry is not None:
        entry.why = f"{entry.why} {text}"


def _stop_deal(run: Run, m, at: datetime) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    name = routing.person_name(rules.teams, m.to)
    label = messages.GATE_LABELS.get(m.gate, m.gate.replace("_", " "))
    kind = "declined" if "decline" in (m.option or "") else "stopped"
    entry = _open_entry(run)
    if entry is not None:
        entry.decision = m.option
        entry.why = (f"{name} answered {m.option.replace('_', ' ')} to the {label} request on {fmt_when(at)}, "
                     f"so the deal stops in {st.stage} ({kind}). No further requests are sent.")
    rt.terminal, rt.terminal_at, rt.eval_pending = kind, at, False


def _request_info(run: Run, m, at: datetime) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    officer = routing.person_name(rules.teams, m.to)
    msg = messages.info_followup_message(st, officer)
    inbox.send_request(st, rt, "info_request", inbox.BORROWER, at, msg, st.stage, f"info:{len(rt.reqs)}", "info")
    rt.resend_after, rt.eval_pending = at, False


def _apply_answer(run: Run, m, at: datetime) -> None:
    st, rt = run.state, run.rt
    if m.gate == "fraud_hold":
        rt.decisions["fraud_hold"] = (m.to, at)
        who = routing.person_name(run.rules.teams, m.to)
        said = (m.option or "").replace("_", " ")
        _note_on_open_entry(run, f"{who} answered {said} on {fmt_when(at)}; the deal stays frozen while the fraud flag is set.")
        return
    if m.stage != st.stage or rt.terminal or rt.frozen_at is not None:
        return
    g = run.cfg.gates.get(m.gate)
    opt = m.option
    if g is None or opt is None:
        return
    if opt in g.advance:
        rt.decisions[m.gate] = (m.to, at)
        rt.gate_satisfied[m.stage], rt.gate_option[m.stage] = True, opt
        rt.ready_at, rt.eval_pending = at, True
    elif opt in g.stop:
        _stop_deal(run, m, at)
    elif opt in g.info:
        _request_info(run, m, at)
    else:
        rt.eval_pending = False  # recorded; the deal waits


def _h_answer(run: Run, t: datetime, idx: int) -> None:
    m = inbox.meta_for(run.rt, idx)
    if m is None or m.processed or m.superseded:
        return
    if not m.recorded:
        option = inbox.answer_option(m.gate)
        if option is None or option not in inbox.gate_options(m.gate):
            m.processed = True
            return
        inbox.record_reply(run.state, run.rules, idx, option, t)
    m.processed = True
    _apply_answer(run, m, m.answered_at or t)


def _h_freeze(run: Run, t: datetime, payload: Any) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    rt.frozen_at = t
    entry = _open_entry(run)
    if entry is not None:
        entry.decision = "held"
        entry.why = (f"{entry.why} Frozen {fmt_when(t)}: a fraud hold was requested and the deal is awaiting Fraud Ops "
                     "to place or release it. No money goes out.")
    route = routing.route_detail("fraud_hold", st, rules, t)
    if route is not None:
        msg = messages.fraud_hold_message(st, rules, st.stage, t)
        inbox.send_request(st, rt, "fraud_hold", route.to, t, msg, st.stage, inbox.FRAUD_GROUP, "fraud")


def _h_ladder(run: Run, t: datetime, nt: tuple) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    idx, pct, level, when, key = nt
    waiting = waiting_for(st, rules, rt, run.view.issues, when)
    st.alerts.extend(sla.ladder_alerts(st, rules, rt, idx, pct, level, when, waiting))
    rt.emitted.add(key)
    rt.alert_times[key] = when
    if idx == 0:
        inbox.send_reminder_and_copy(st, rules, rt, when)


def _h_chase1(run: Run, t: datetime, payload: Any) -> None:
    run.rt.fraud_chase1_at = t
    inbox.send_fraud_copy(run.state, run.rules, run.rt, t)


def _h_chase2(run: Run, t: datetime, payload: Any) -> None:
    st, rt, rules = run.state, run.rt, run.rules
    rt.fraud_chase2_at = t
    orig = inbox.fraud_original(rt)
    if orig is None:
        return
    backup = inbox.fraud_backup(rules, orig.to)
    target = rules.teams.escalates_to(backup or orig.to)
    if not target:
        return
    copied = rt.fraud_chase1_at if backup else None
    msg = messages.fraud_chase_message(st, rules, st.stage, orig.to, orig.at, backup, copied, t)
    st.alerts.append(Alert(at=iso(t), stage=st.stage, level="escalation", to=target, message=msg))


_HANDLERS: dict[str, Callable[[Run, datetime, Any], None]] = {
    "start": _h_start,
    "eval": _h_eval,
    "immediate": _h_immediate,
    "answer": _h_answer,
    "freeze": _h_freeze,
    "ladder": _h_ladder,
    "chase1": _h_chase1,
    "chase2": _h_chase2,
}


def _drain(run: Run, strict: bool) -> None:
    rt = run.rt
    while True:
        best = None
        for ev in _candidates(run):
            t = ev[0]
            if (t < run.now) if strict else (t <= run.now):
                if best is None or ev[:3] < best[:3]:
                    best = ev
        if best is None:
            return
        run.count += 1
        if run.count > MAX_EVENTS:
            raise RuntimeError(f"move: more than {MAX_EVENTS} events in one step")
        t, _, _, kind, payload = best
        rt.watermark = t if rt.watermark is None or t > rt.watermark else rt.watermark
        _HANDLERS[kind](run, t, payload)
        _after_event(run, t)


def waiting_for(state: DealState, rules: Rules, rt: MoveRuntime, issues: tuple[str, ...], now: datetime) -> tuple[str, str]:
    """(waiting on, to unblock it) for an alert, from the current stage's blockers."""
    cfg = get_config()
    run = Run(state, rules, rt, cfg, to_sim(now), _view(state))
    stage = state.stage
    present = run.view.facts
    missing = [f for f in cfg.required_facts.get(stage, ()) if f not in present]
    return messages.waiting_on(
        state, rules, rt, issues, stage, cfg.issues.blocking.get(stage, ()), missing, _vendor_unverified(run, stage),
        to_sim(now),
    )


def _deadline_check(run: Run, t: datetime) -> None:
    """J18: one alert, to the RM and the stage owner, once the projection no longer fits before needed_by."""
    st, rt, rules = run.state, run.rt, run.rules
    if not rt.started or rt.terminal or rt.frozen_at is not None or "deadline_risk" in rt.emitted:
        return
    nb = deadline.needed_by(st)
    if nb is None:
        return
    projected = deadline.project_finish(st, rules, t)
    if projected <= nb:
        return
    rt.emitted.add("deadline_risk")
    rt.alert_times["deadline_risk"] = t
    recipients: list[str] = []
    for pid in (routing.rm_of(st, rules), rt.owner):
        if pid and pid not in recipients:
            recipients.append(pid)
    person, decision = deadline.next_signoff(st, rules, rt, t)
    msg = messages.deadline_risk_message(
        st, rules, st.stage, projected, deadline.remaining_stages(st, rules), person, decision,
        deadline.latest_exit(st, rules, st.stage),
    )
    for pid in recipients:
        st.alerts.append(Alert(at=iso(t), stage=st.stage, level="alert", to=pid, message=msg))


def _after_event(run: Run, t: datetime) -> None:
    _deadline_check(run, t)


def _maybe_unfreeze(run: Run) -> None:
    """Move never releases a hold; it resumes only once the fraud flag itself is cleared."""
    st, rt = run.state, run.rt
    if rt.frozen_at is None or rt.terminal or st.fraud.held or st.fraud.flagged:
        return
    t = max(run.now, rt.frozen_at)
    rt.frozen_total += t - rt.frozen_at
    rt.frozen_at = None
    rt.eval_pending, rt.ready_at = True, t
    rt.watermark = t if rt.watermark is None or t > rt.watermark else rt.watermark
    entry = _open_entry(run)
    if entry is not None:
        entry.decision = None
        entry.why = f"{entry.why} Fraud hold cleared {fmt_when(t)}; the deal resumes."


def step_deal(state: DealState, rules: Rules, now: datetime) -> None:
    """Advance the deal as far as the rules allow at ``now``."""
    if not rules.stages.pipeline:
        return None
    now = to_sim(now)
    rt = get_runtime(state)
    cur = _view(state)
    prev = rt.view
    cfg = get_config()
    if prev is not None:
        _drain(Run(state, rules, rt, cfg, now, prev), strict=True)
    if prev is None or cur != prev:
        rt.eval_pending = True
    run = Run(state, rules, rt, cfg, now, cur, count=0)
    _maybe_unfreeze(run)
    _drain(run, strict=False)
    _finish(run)
    rt.view = cur


def _finish(run: Run) -> None:
    wm = run.rt.watermark
    _deadline_check(run, run.now if wm is None or run.now >= wm else wm)
