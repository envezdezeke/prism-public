"""Hold decision (owner: Phase 5, M8, M17, M18).

Contract: ``decide`` is sticky (a hold is never undone by weaker later evidence).
``evaluate`` = build_graph -> compute_signals -> decide and is the ONLY call run.py makes.
``FraudOutcome.per_deal`` holds a Fraud block for every deal it wants to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from prism.contract import FAMILY_LABELS, Cluster, Fraud, format_sim_time, parse_sim_time
from prism.engine.config import FraudConfig
from prism.engine.state import PIPELINE, DealState
from prism.fraud.baseline import Baseline
from prism.fraud.graph import EntityGraph, build_graph
from prism.fraud import links as fraud_links
from prism.fraud.signals import LINK_MARK, FamilySignal, compute_signals

_REASON_HEAD = "Held by the cross-deal fraud screen."
_OLD_TRICK = "OLD_TRICK:"


@dataclass
class FraudOutcome:
    per_deal: dict[str, Fraud] = field(default_factory=dict)
    history_flags: list[str] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)


def decide(
    signals: Sequence[FamilySignal],
    graph: EntityGraph,
    states: Mapping[str, DealState],
    baseline: Baseline,
    cfg: FraudConfig,
    now: datetime,
) -> FraudOutcome:
    """Apply the hold rule to the signals, tick by tick, with sticky holds (M17).

    A deal is flagged and held when every family in ``cfg.hold_required`` fired and at
    least ``cfg.hold_min_additional`` fired families come from ``cfg.hold_additional_from``
    (families in ``cfg.non_voting`` never count). A flag, once set, is carried forward from
    ``states[folder].fraud`` unchanged except for ``linked_deals``. ``states`` is not mutated.
    """
    fired: dict[str, dict[str, FamilySignal]] = {}
    for sig in signals:
        if not sig.fired or not sig.deals:
            continue
        by_family = fired.setdefault(sig.deals[0], {})
        if sig.family not in by_family:
            by_family[sig.family] = sig

    out = FraudOutcome()
    ctx = (fraud_links.LinkContext(graph, states, baseline, cfg)
           if graph is not None and baseline is not None else None)
    for folder in sorted(states):
        state = states[folder]
        fams = fired.get(folder, {})
        partners = _current_partners(folder, fams, states, cfg)
        voting = {f: s for f, s in fams.items() if f not in cfg.non_voting}
        cluster = _hold_met(set(voting), cfg) or _strong_link_met(folder, set(voting), partners, ctx, cfg)
        prev = state.fraud
        if prev.flagged:
            # Sticky (M17): only linked_deals may grow, and only from a tick that met the rule.
            links = sorted(set(prev.linked_deals) | (set(partners) if cluster else set()))
            out.per_deal[folder] = prev.model_copy(update={"linked_deals": links})
            continue
        codes = old_trick_codes(state)
        if not cluster and not codes:
            continue
        out.per_deal[folder] = Fraud(
            flagged=True,
            held=True,
            flagged_at=format_sim_time(now),
            stage_at_flag=stage_in_force(state, now),
            reason=build_reason(voting if cluster else {}, codes),
            linked_deals=partners if cluster else [],
        )
    # Cluster pass (M18): links and history flags follow the independent-link rule. A held
    # deal's linked_deals are replaced by its cluster peers and qualified history ids; the
    # sticky fields (flagged, held, flagged_at, stage_at_flag, reason) are never touched.
    # Without a graph and a baseline the link classes cannot be judged, so the flag pass's
    # current-partner links stand and no cluster is built (hand-built unit tests only).
    if graph is None or baseline is None:
        return out
    blocks = {f: out.per_deal.get(f, states[f].fraud) for f in states}
    res = fraud_links.build_clusters(blocks, signals, graph, states, baseline, cfg)
    for folder in sorted(out.per_deal):
        block = out.per_deal[folder]
        if block.flagged:
            out.per_deal[folder] = block.model_copy(update={"linked_deals": res.links.get(folder, [])})
    out.history_flags = list(res.history_flags)
    out.clusters = list(res.clusters)
    return out


def _strong_link_met(
    folder: str, families: set[str], partners: Sequence[str], ctx, cfg: FraudConfig
) -> bool:
    """Strong-link path: the required families fired, at least
    ``cfg.hold_strong_link_min_additional`` more agree, and the deal shares at least
    ``cfg.history_min_links`` independent link classes with one current partner.
    Off when the setting is None or no link context exists."""
    need = getattr(cfg, "hold_strong_link_min_additional", None)
    if need is None or ctx is None:
        return False
    if not all(f in families for f in cfg.hold_required):
        return False
    if sum(1 for f in families if f in cfg.hold_additional_from) < need:
        return False
    for p in partners:
        if p == folder:
            continue
        parties = {_PARTY_MERGE.get(c, c) for c in ctx.shared(folder, p)} - _NOT_A_PARTY
        if len(parties) >= cfg.history_min_links:
            return True
    return False


# Strong-link path: a shared deposit pattern is not a second link (honest businesses in one
# trade share it), weak kinds never count, and a phone follows its preparer (one fact, M18).
# Chosen on the synthetic bench (eval/run_synth.py): ring held 80.2%, twins held 0.1%.
_NOT_A_PARTY = frozenset({fraud_links.CLASS_F5, "address", "bank"})
_PARTY_MERGE = {"phone": "preparer"}


def _hold_met(families: set[str], cfg: FraudConfig) -> bool:
    if not all(f in families for f in cfg.hold_required):
        return False
    extra = sum(1 for f in families if f in cfg.hold_additional_from)
    return extra >= cfg.hold_min_additional


def _current_partners(
    folder: str, fams: Mapping[str, FamilySignal], states: Mapping[str, DealState], cfg: FraudConfig
) -> list[str]:
    """Partners of the deal's required-family signals that are current deals (not history)."""
    out: set[str] = set()
    for fam in cfg.hold_required:
        sig = fams.get(fam)
        if sig is None:
            continue
        out.update(p for p in sig.deals[1:] if p in states and p != folder)
    return sorted(out)


def stage_in_force(state: DealState, t: datetime) -> str:
    """Stage of the last stage_log entry entered at or before ``t``; the first stage if none.

    Reads the log, never ``state.stage``: Move may already have advanced the deal this tick.
    """
    stage = PIPELINE[0]
    best: datetime | None = None
    for entry in state.stage_log:
        entered = parse_sim_time(entry.entered_at)
        if entered <= t and (best is None or entered >= best):
            best = entered
            stage = entry.stage
    return stage


def old_trick_codes(state: DealState) -> list[str]:
    """Sorted failed known-fraud check codes from the deal's open OLD_TRICK issues."""
    return sorted({i[len(_OLD_TRICK):] for i in state.issues if i.startswith(_OLD_TRICK)} - {""})


def _join_words(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def build_reason(voting: Mapping[str, FamilySignal], codes: Sequence[str] = ()) -> str:
    """Plain-words reason: failed known-fraud checks first, then the cross-deal screen.

    Each failed code appears verbatim (Move's fraud message reads them back). F1-style
    evidence is cut at LINK_MARK so partner ids never appear in the reason. Built by
    concatenation (no format() on document-derived text).
    """
    parts: list[str] = []
    if codes:
        parts.append(
            "Known-fraud check " + _join_words(list(codes))
            + " failed on this deal's own documents, so it goes straight to Fraud Operations"
            + " under bank policy."
        )
    if not voting:
        return " ".join(parts)
    parts.append(_REASON_HEAD)
    for fam in sorted(voting):
        evidence = voting[fam].evidence.partition(LINK_MARK)[0].strip().rstrip(".")
        label = FAMILY_LABELS.get(fam, fam)
        label = label[:1].upper() + label[1:]
        parts.append(label + ": " + evidence + ".")
    return " ".join(parts)


def evaluate(
    states: Mapping[str, DealState], baseline: Baseline, cfg: FraudConfig, now: datetime
) -> FraudOutcome:
    """build_graph -> compute_signals -> decide."""
    graph = build_graph(states, baseline)
    signals = compute_signals(graph, states, baseline, cfg, now)
    return decide(signals, graph, states, baseline, cfg, now)
