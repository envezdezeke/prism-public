"""Independent link classes, clusters, linked deals and history flags (owner: Phase 5; M17, M18, FRD-06).

Contract: pure and deterministic (sorted iteration, no module state). Two deals, or a deal and
a history deal, are linked only when they share at least ``cfg.history_min_links`` independent
link classes:

- each strong shared entity kind is one class (``history_flags.link_classes`` in
  config/fraud.yaml may merge kinds into one class; by default every kind is its own class
  and ``pdf_producer`` is the class "producer");
- the same lookalike-vendor group on both deals (F3) is the class "f3";
- both deals showing the deposit anomaly on the same strongest statistic (F5) is "f5".

A shared value counts only when ``graph.entity_weights`` marks it strong (rare in history and
carried by at least F1.min_linked_deals deals). History deals are scored leave-one-out.
History flags and links exist only for clusters that contain a held deal; a deal that is not
held always has empty links. Link checks run only for held members and their F1 partners.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence

from prism.contract import FAMILY_LABELS, Cluster, FamilyResult, Fraud
from prism.engine.config import FraudConfig
from prism.engine.state import DealState
from prism.fraud.baseline import Baseline, DealProfile, deposit_features
from prism.fraud.graph import ENTITY_KINDS, EntityGraph
from prism.fraud.rarity import FraudParams
from prism.fraud.signals import (
    COMPUTED_FAMILIES,
    LINK_MARK,
    FamilySignal,
    fires,
    score_f3,
    score_f5,
)

CLASS_PRODUCER = "producer"
CLASS_F3 = "f3"
CLASS_F5 = "f5"
NOT_FIRED = "not fired"
NOT_EVALUATED = "not evaluated"


def link_classes_for(cfg_or_params: FraudConfig | FraudParams | None) -> dict[str, str]:
    """Entity kind -> link class id (config merges first, defaults for every other kind)."""
    if isinstance(cfg_or_params, FraudParams):
        params = cfg_or_params
    else:
        params = FraudParams.from_config(cfg_or_params)
    mapping = {k: k for k in sorted(ENTITY_KINDS)}
    mapping["pdf_producer"] = CLASS_PRODUCER
    for kind, cls_id in params.link_classes:
        mapping[kind] = cls_id
    return mapping


@dataclass
class LinkContext:
    """Cached per-call scoring of deals for the link rule (one per decide() call)."""

    graph: EntityGraph
    states: Mapping[str, DealState]
    baseline: Baseline
    cfg: FraudConfig
    classes: dict[str, str] = field(default_factory=dict)
    _profiles: dict[str, DealProfile] = field(default_factory=dict)
    _f3: dict[str, Optional[str]] = field(default_factory=dict)
    _f5: dict[str, Optional[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.classes:
            self.classes = link_classes_for(self.baseline.params)

    def is_history(self, deal_id: str) -> bool:
        return deal_id not in self.graph.profiles and deal_id in self.baseline.deals

    def _exclude(self, deal_id: str) -> Optional[str]:
        return deal_id if self.is_history(deal_id) else None

    def profile(self, deal_id: str) -> DealProfile:
        if deal_id in self._profiles:
            return self._profiles[deal_id]
        if deal_id in self.graph.profiles:
            prof = self.graph.profiles[deal_id]
            state = self.states.get(deal_id)
            dep = deposit_features(state.features.get("deposit_months")) if state is not None else None
            if dep is not None:
                prof = replace(prof, deposits=dep)
        elif deal_id in self.baseline.deals:
            prof = self.baseline.deals[deal_id].profile
        else:
            prof = DealProfile(deal_id=deal_id)
        self._profiles[deal_id] = prof
        return prof

    def f3_group(self, deal_id: str) -> Optional[str]:
        """Vendor group of the deal's lookalike-vendor result, or None."""
        if deal_id not in self._f3:
            res = score_f3(self.profile(deal_id), self.baseline, self.baseline.params,
                           exclude=self._exclude(deal_id))
            self._f3[deal_id] = res.group if res is not None else None
        return self._f3[deal_id]

    def f5_stat(self, deal_id: str) -> Optional[str]:
        """Strongest statistic of a fired deposit anomaly, or None (no deposit data: None)."""
        if deal_id not in self._f5:
            cal = self.baseline.calibration
            res = score_f5(self.profile(deal_id), self.baseline, self.baseline.params,
                           cal.reference, exclude=self._exclude(deal_id))
            ok = res is not None and fires(res.bits, cal.tau.get("F5"))
            self._f5[deal_id] = res.stat if ok else None
        return self._f5[deal_id]

    def shared(self, a: str, b: str) -> set[str]:
        """The link classes two deals share."""
        pa, pb = self.profile(a), self.profile(b)
        out: set[str] = set()
        for kind in sorted(set(pa.entities) & set(pb.entities)):
            for value in sorted(set(pa.entities[kind]) & set(pb.entities[kind])):
                w = self.graph.entity_weights.get((kind, value))
                if w is not None and w.strong:
                    out.add(self.classes.get(kind, kind))
                    break
        g3 = self.f3_group(a)
        if g3 is not None and g3 == self.f3_group(b):
            out.add(CLASS_F3)
        s5 = self.f5_stat(a)
        if s5 is not None and s5 == self.f5_stat(b):
            out.add(CLASS_F5)
        return out

    def qualified(self, a: str, b: str) -> bool:
        return a != b and len(self.shared(a, b)) >= self.cfg.history_min_links


def shared_link_classes(a: str, b: str, ctx: LinkContext) -> set[str]:
    """Module-level alias of ``LinkContext.shared``."""
    return ctx.shared(a, b)


@dataclass
class ClusterResult:
    links: dict[str, list[str]] = field(default_factory=dict)  # held deal -> linked ids
    history_flags: list[str] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)


def _components(nodes: Sequence[str], edge) -> list[list[str]]:
    """Connected components (each sorted) over ``nodes`` with the predicate ``edge(a, b)``."""
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ordered = sorted(nodes)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if find(a) != find(b) and edge(a, b):
                parent[find(b)] = find(a)
    groups: dict[str, list[str]] = {}
    for n in ordered:
        groups.setdefault(find(n), []).append(n)
    return [sorted(g) for g in groups.values()]


def _families(members: Sequence[str], fired: Mapping[str, Mapping[str, FamilySignal]]) -> dict[str, FamilyResult]:
    out: dict[str, FamilyResult] = {}
    for fam in sorted(FAMILY_LABELS):
        texts: list[str] = []
        for m in members:
            sig = fired.get(m, {}).get(fam)
            if sig is None:
                continue
            text = sig.evidence.partition(LINK_MARK)[0].strip()
            if text and text not in texts:
                texts.append(text)
        if texts or any(fam in fired.get(m, {}) for m in members):
            out[fam] = FamilyResult(fired=True, evidence="; ".join(texts) or FAMILY_LABELS[fam])
        else:
            out[fam] = FamilyResult(
                fired=False, evidence=NOT_FIRED if fam in COMPUTED_FAMILIES else NOT_EVALUATED
            )
    return out


def build_clusters(
    blocks: Mapping[str, Fraud],
    signals: Sequence[FamilySignal],
    graph: EntityGraph,
    states: Mapping[str, DealState],
    baseline: Baseline,
    cfg: FraudConfig,
    ctx: Optional[LinkContext] = None,
) -> ClusterResult:
    """Hold and watch clusters, held deals' linked ids and the history flags (see module doc).

    ``blocks`` maps every current folder to its Fraud block after the flag pass.
    """
    ctx = ctx or LinkContext(graph, states, baseline, cfg)
    fired: dict[str, dict[str, FamilySignal]] = {}
    for sig in signals:
        if sig.fired and sig.deals:
            fired.setdefault(sig.deals[0], {}).setdefault(sig.family, sig)
    current = set(states)
    partners: dict[str, list[str]] = {}
    for fam in cfg.hold_required:
        for folder in sorted(fired):
            sig = fired[folder].get(fam)
            if sig is not None:
                partners.setdefault(folder, [])
                partners[folder] = sorted(set(partners[folder]) | set(sig.deals[1:]) - {folder})

    held = sorted(f for f in states if (blocks.get(f) or states[f].fraud).flagged)
    held_set = set(held)
    members = set(held)
    for h in held:
        for p in partners.get(h, ()):
            if p in current and p not in members and ctx.qualified(h, p):
                members.add(p)

    res = ClusterResult()
    hist_flags: set[str] = set()
    raw: list[tuple[list[str], list[str], str]] = []  # (current members, history ids, tier)
    for comp in _components(sorted(members), ctx.qualified):
        cands = sorted({
            p for m in comp for p in partners.get(m, ()) if p not in current and p in baseline.deals
        })
        hist = [c for c in cands if any(ctx.qualified(m, c) for m in comp)]
        hist_flags.update(hist)
        for m in comp:
            if m in held_set:
                res.links[m] = sorted((set(comp) - {m}) | set(hist))
        raw.append((comp, hist, "hold"))

    watch_nodes = sorted(
        f for f in fired
        if f in current and f not in members and any(fam not in cfg.non_voting for fam in fired[f])
    )
    watch_set = set(watch_nodes)

    def f1_edge(a: str, b: str) -> bool:
        return b in partners.get(a, ()) or a in partners.get(b, ())

    for comp in _components(watch_nodes, f1_edge):
        hist = sorted({p for m in comp for p in partners.get(m, ()) if p not in current and p not in watch_set})
        raw.append((comp, hist, "watch"))

    raw.sort(key=lambda r: (r[0][0], r[2]))
    for i, (comp, hist, tier) in enumerate(raw, start=1):
        res.clusters.append(Cluster(
            id=f"C-{i:03d}", deals=list(comp) + list(hist), families=_families(comp, fired), tier=tier,
        ))
    res.history_flags = sorted(hist_flags)
    return res
