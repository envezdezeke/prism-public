"""Signal families F1-F6 in bits of surprise (owner: Phase 5, M1, M7, M8).

Contract: one FamilySignal per fired-or-evaluated family per deal group; F6 is evidence
only and never votes. Thresholds come from ``FraudConfig`` percentiles, not literals.

All six families are built. F1 has no percentile: it fires iff a strong shared entity
exists. F3 and F5 fire iff ``bits > 0`` and ``bits >= tau`` where tau is the startup
calibration percentile on ``Baseline.calibration`` (never fires when tau is missing).
F2 (burst) and F4 (document forensics) are structural rules with thresholds from
config/fraud.yaml (their percentile entries are kept for the config contract but unused).
F6 (terms shape) is evidence only: it is non-voting in config and never counts toward a hold.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Mapping, Optional

from prism.engine.config import FraudConfig
from prism.engine.state import DealState
from prism.fraud.baseline import Baseline, DealProfile, deposit_features
from prism.fraud.canon import split_legal_suffix
from prism.fraud.graph import EntityGraph
from prism.fraud.rarity import (
    FraudParams,
    percentile,
    base_similar,
    entropy_bits,
    fit_beta_mom,
    is_strong,
    kl_bits,
    outcome_discount,
    shrunk_share,
    surprisal,
    upper_tail_bits,
)


# Families compute_signals evaluates; the others show "not evaluated" in clusters.
COMPUTED_FAMILIES: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6")


@dataclass(frozen=True)
class FamilySignal:
    family: str
    deals: tuple[str, ...]
    bits: float
    fired: bool
    evidence: str


def _plain(kind: str) -> str:
    return kind.replace("_", " ")


# ------------------------------------------------------------------------------- F1

@dataclass(frozen=True)
class F1Result:
    bits: float  # max pair score over partners
    partners: tuple[str, ...]  # partners with a positive pair score, sorted by id
    shared: tuple[tuple[str, str, int], ...]  # strong shared (kind, value, history df)
    n: int  # history deals the rarity was judged against


def score_f1(
    profile: DealProfile,
    baseline: Baseline,
    params: FraudParams,
    *,
    exclude: Optional[str] = None,
    others: Optional[Mapping[str, DealProfile]] = None,
) -> Optional[F1Result]:
    """F1: max pair score ``sum w * omega * I`` over partners sharing strong entities.

    ``others`` maps other current deal ids to their profile; ``exclude`` is a history id to
    leave out (leave-one-out). None when n is 0 or no strong shared entity exists.
    """
    n = baseline.n_effective(exclude)
    if n <= 0:
        return None
    others = others or {}
    pair: dict[str, float] = {}
    shared: list[tuple[str, str, int]] = []
    for kind in sorted(profile.entities):
        for value in profile.entities[kind]:
            df, repaid, _first, _last = baseline.entity_stats(kind, value, exclude)
            current = sorted(
                oid for oid, op in others.items()
                if oid != profile.deal_id and value in op.entities.get(kind, ())
            )
            links = 1 + df + len(current)
            if not is_strong(df, n, links, params.rho, params.min_linked):
                continue
            partners = baseline.deals_with(kind, value, exclude) + current
            if not partners:
                continue
            contrib = (
                params.weight(kind)
                * outcome_discount(repaid, df, params.outcome_beta)
                * surprisal(df, n)
            )
            for p in partners:
                pair[p] = pair.get(p, 0.0) + contrib
            shared.append((kind, value, df))
    positive = sorted(p for p, s in pair.items() if s > 0)
    if not shared or not positive:
        return None
    return F1Result(
        bits=max(pair[p] for p in positive),
        partners=tuple(positive),
        shared=tuple(shared),
        n=n,
    )


# Text placed between the F1 entity part and its partner list; decide() cuts the reason here.
LINK_MARK = "; linked to "


def _f1_evidence(res: F1Result) -> str:
    parts = [f"{_plain(k)} used by {df} of {res.n} history deals" for k, _v, df in res.shared]
    return "shared rare " + "; ".join(parts) + LINK_MARK + ", ".join(res.partners)


# ------------------------------------------------------------------------------- F3

@dataclass(frozen=True)
class F3Result:
    bits: float
    group: str  # base key of the frequent history vendor it imitates
    what_differs: str  # name, suffix and/or remit account
    n_pat: int  # history observations showing a variant of this vendor
    n_vend: int  # history vendor observations judged against
    evidence: str


def _modal_form(members: list[tuple[str, str, str, str]]) -> tuple[str, str]:
    """Most common (full key, account) form; ties broken by lexicographic order."""
    counts = Counter((o[2], o[3]) for o in members)
    return min(counts, key=lambda form: (-counts[form], form))


def _differs(full: str, account: str, modal: tuple[str, str]) -> bool:
    return full != modal[0] or bool(account and modal[1] and account != modal[1])


def score_f3(
    profile: DealProfile,
    baseline: Baseline,
    params: FraudParams,
    *,
    exclude: Optional[str] = None,
) -> Optional[F3Result]:
    """F3: a vendor with a frequent history vendor's base name but another suffix, name
    spelling or remit account. ``bits = -log2((n_pat + 1) / (n_vend + 1))`` (M1)."""
    vendors = profile.entities.get("vendor", ())
    groups = baseline.vendor_groups()
    if not vendors or not groups:
        return None
    accounts = profile.entities.get("remit_account") or ("",)
    n_vend = baseline.n_vendor_obs(exclude)
    best: Optional[F3Result] = None
    for vendor in sorted(vendors):
        key = split_legal_suffix(vendor)
        for account in sorted(accounts):
            for gbase in sorted(groups):
                group = groups[gbase]
                members = [o for o in group.members if o[0] != exclude]
                if len(members) < params.min_vendor_deals:
                    continue
                if not base_similar(key.base, gbase, params.min_similarity, params.max_edit_distance):
                    continue
                modal = _modal_form(members)
                if not _differs(key.full, account, modal):
                    continue
                diffs = []
                if key.base != gbase:
                    diffs.append("name")
                if key.suffix != split_legal_suffix(modal[0]).suffix:
                    diffs.append("suffix")
                if account and modal[1] and account != modal[1]:
                    diffs.append("remit account")
                n_pat = sum(
                    1 for o in group.similar if o[0] != exclude and _differs(o[2], o[3], modal)
                )
                bits = -math.log2((n_pat + 1) / (n_vend + 1))
                bits = bits if bits > 0 else 0.0
                if best is not None and (bits < best.bits or (bits == best.bits and gbase >= best.group)):
                    continue
                what = ", ".join(diffs)
                best = F3Result(
                    bits=bits, group=gbase, what_differs=what, n_pat=n_pat, n_vend=n_vend,
                    evidence=(
                        f"vendor resembles the usual history vendor '{gbase}' but its {what} differs; "
                        f"{n_pat} of {n_vend} history vendor deals show such a variant"
                    ),
                )
    return best


# ------------------------------------------------------------------------------- F5

F5_STATS = ("round", "conc", "kl")
_F5_LABELS = {
    "round": "round-number deposit share",
    "conc": "weekday concentration",
    "kl": "weekday pattern unlike history",
}


@dataclass(frozen=True)
class F5Result:
    bits: float  # maximum over the statistics
    stat: str  # statistic that gave the maximum (ties in the order round, conc, kl)
    stat_bits: dict[str, float]
    z: dict[str, float]
    evidence: str


def _smoothed(mass: tuple[float, ...], alpha: float) -> list[float]:
    total = sum(mass) + len(mass) * alpha
    return [(m + alpha) / total for m in mass]


def f5_statistics(
    profile: DealProfile,
    baseline: Baseline,
    params: FraudParams,
    *,
    exclude: Optional[str] = None,
) -> Optional[dict[str, float]]:
    """F5 statistics oriented so higher is more suspicious; None without deposit data.

    ``round``: beta-binomial posterior round share (prior fitted on history shares by method
    of moments, so small samples shrink toward it). ``conc``: minus the Shannon entropy of the
    Dirichlet-smoothed weekday distribution. ``kl``: KL of that distribution against the
    pooled, equally smoothed history weekday distribution.
    """
    dep = profile.deposits
    if dep is None or dep.n <= 0:
        return None
    a, b = fit_beta_mom(baseline.round_shares(exclude))
    q = _smoothed(dep.day_mass, params.dirichlet_alpha)
    p0 = _smoothed(baseline.pooled_day_mass(exclude), params.dirichlet_alpha)
    return {
        "round": shrunk_share(dep.round_mass, dep.n, a, b),
        "conc": -entropy_bits(q),
        "kl": kl_bits(q, p0),
    }


def score_f5(
    profile: DealProfile,
    baseline: Baseline,
    params: FraudParams,
    reference: Mapping[str, list[float]],
    *,
    exclude: Optional[str] = None,
) -> Optional[F5Result]:
    """F5: each statistic to bits through the smoothed upper tail on the clean reference."""
    stats = f5_statistics(profile, baseline, params, exclude=exclude)
    if stats is None:
        return None
    stat_bits = {
        s: upper_tail_bits(stats[s], reference[s]) for s in F5_STATS if reference.get(s)
    }
    if not stat_bits:
        return None
    top = max(F5_STATS, key=lambda s: (stat_bits.get(s, -1.0), -F5_STATS.index(s)))
    return F5Result(
        bits=stat_bits[top],
        stat=top,
        stat_bits=stat_bits,
        z={s: stats[s] for s in stat_bits},
        evidence=(
            f"deposit pattern unusual versus clean history: strongest on {_F5_LABELS[top]} "
            f"({stat_bits[top]:.2f} bits)"
        ),
    )


# ------------------------------------------------------------------------------- F2

@dataclass(frozen=True)
class F2Result:
    k: int  # distinct burst partners
    partners: tuple[str, ...]
    bits: float  # log2(1 + k)
    fired: bool
    evidence: str


def opening_date(deal_id: str, states: Mapping[str, DealState], baseline: Baseline) -> Optional[date]:
    """A current deal opens with its earliest visible document; a history deal on its application."""
    st = states.get(deal_id)
    if st is not None:
        times = [d.arrive_at for d in st.docs if d.arrive_at is not None]
        return min(times).date() if times else None
    hd = baseline.deals.get(deal_id)
    return hd.applied if hd is not None else None


def score_f2(
    folder: str,
    graph: EntityGraph,
    states: Mapping[str, DealState],
    baseline: Baseline,
    params: FraudParams,
) -> Optional[F2Result]:
    """F2: other deals sharing a strong entity with this one, opened within the burst window.

    A ring opens together; honest repeat borrowers come back months apart. None when no
    partner falls inside the window (nothing to report).
    """
    prof = graph.profiles.get(folder)
    start = opening_date(folder, states, baseline)
    if prof is None or start is None:
        return None
    partners: set[str] = set()
    for kind in sorted(prof.entities):
        for value in prof.entities[kind]:
            w = graph.entity_weights.get((kind, value))
            if w is None or not w.strong:
                continue
            for other in graph.entity_deals.get((kind, value), ()):
                if other == folder or other in partners:
                    continue
                when = opening_date(other, states, baseline)
                if when is not None and abs((when - start).days) <= params.burst_window_days:
                    partners.add(other)
    if not partners:
        return None
    k = len(partners)
    return F2Result(
        k=k,
        partners=tuple(sorted(partners)),
        bits=math.log2(1 + k),
        fired=k >= params.burst_min_partners,
        evidence=(
            f"{k} other deal{'s' if k != 1 else ''} that share a rare entity with this one "
            f"opened within {params.burst_window_days} days (a ring opens together; honest "
            "repeat borrowers come back months apart)"
        ),
    )


# ------------------------------------------------------------------------------- F4

@dataclass(frozen=True)
class F4Result:
    matched: int  # third-party documents made by a borrower-paperwork tool
    usable: int  # third-party documents with producer text and no OCR
    bits: float  # matched, informational (F4 has no percentile)
    fired: bool
    evidence: str


def _tool(doc) -> Optional[tuple[str, str]]:
    meta = doc.meta or {}
    producer = str(meta.get("producer") or "").strip()
    if not producer or meta.get("ocr_used") is True:
        return None
    return producer, str(meta.get("creator") or "").strip()


def score_f4(state: DealState, params: FraudParams) -> Optional[F4Result]:
    """F4: the same creation tool made the borrower's own paperwork and its bank statements.

    Only third-party documents with producer text and no OCR are usable, so scans and images
    never fire. None when there is no usable third-party document or no borrower tool.
    """
    third = set(params.f4_third_party_classes)
    borrower = set(params.f4_borrower_classes)
    tools = {t for d in state.docs if d.doc_class in borrower for t in [_tool(d)] if t is not None}
    usable = [t for d in state.docs if d.doc_class in third for t in [_tool(d)] if t is not None]
    if not usable or not tools:
        return None
    matched = sum(1 for t in usable if t in tools)
    fired = matched >= params.f4_min_docs and matched / len(usable) >= params.f4_min_share
    return F4Result(
        matched=matched,
        usable=len(usable),
        bits=float(matched),
        fired=fired,
        evidence=(
            f"the same creation tool produced the borrower's own paperwork and {matched} of "
            f"{len(usable)} bank statements (statements normally come from the bank's systems)"
        ),
    )


# ------------------------------------------------------------------------------- F6

@dataclass(frozen=True)
class F6Result:
    ratio: float
    bits: float
    fired: bool
    evidence: str


def _fact_number(state: DealState, key: str) -> Optional[float]:
    f = state.facts.get(key)
    if f is None or isinstance(f.value, bool):
        return None
    try:
        x = float(f.value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def score_f6(state: DealState, baseline: Baseline, params: FraudParams) -> Optional[F6Result]:
    """F6 (evidence only): amount requested over stated revenue against clean history ratios."""
    amount = _fact_number(state, "amount_requested")
    revenue = _fact_number(state, "annual_revenue_stated")
    ref = baseline.terms_ratios()
    if amount is None or revenue is None or amount <= 0 or revenue <= 0 or not ref:
        return None
    ratio = amount / revenue
    bits = upper_tail_bits(ratio, ref)
    cut = percentile(ref, params.f6_tail_percentile)
    fired = bits > 0 and cut is not None and ratio >= cut
    return F6Result(
        ratio=ratio,
        bits=bits,
        fired=fired,
        evidence=(
            f"amount requested is {ratio:.2f} times stated annual revenue; "
            f"{len(ref)} clean history deals reference this ratio (evidence only, never votes)"
        ),
    )


# ------------------------------------------------------------------------------ driver

def _fires(bits: float, tau: Optional[float]) -> bool:
    return bits > 0 and tau is not None and bits >= tau


def fires(bits: float, tau: Optional[float]) -> bool:
    """Public tau test: ``bits > 0`` and ``bits >= tau`` (never fires without a tau)."""
    return _fires(bits, tau)


def compute_signals(
    graph: EntityGraph,
    states: Mapping[str, DealState],
    baseline: Baseline,
    cfg: FraudConfig,
    now: datetime,
) -> list[FamilySignal]:
    """Return the family signals at ``now`` (``now`` is unused so far), sorted by (folder, family)."""
    params = baseline.params
    cal = baseline.calibration
    out: list[FamilySignal] = []
    for folder in sorted(graph.profiles):
        profile = graph.profiles[folder]
        f1 = score_f1(profile, baseline, params, others=graph.profiles)
        if f1 is not None:
            out.append(
                FamilySignal("F1", (folder,) + f1.partners, f1.bits, True, _f1_evidence(f1))
            )
        f3 = score_f3(profile, baseline, params)
        if f3 is not None:
            out.append(
                FamilySignal("F3", (folder,), f3.bits, _fires(f3.bits, cal.tau.get("F3")), f3.evidence)
            )
        state = states.get(folder)
        dep = deposit_features(state.features.get("deposit_months")) if state is not None else None
        if dep is not None:
            f5 = score_f5(replace(profile, deposits=dep), baseline, params, cal.reference)
            if f5 is not None:
                out.append(
                    FamilySignal("F5", (folder,), f5.bits, _fires(f5.bits, cal.tau.get("F5")), f5.evidence)
                )
        f2 = score_f2(folder, graph, states, baseline, params)
        if f2 is not None:
            out.append(FamilySignal("F2", (folder,) + f2.partners, f2.bits, f2.fired, f2.evidence))
        if state is not None:
            f4 = score_f4(state, params)
            if f4 is not None:
                out.append(FamilySignal("F4", (folder,), f4.bits, f4.fired, f4.evidence))
            f6 = score_f6(state, baseline, params)
            if f6 is not None:
                out.append(FamilySignal("F6", (folder,), f6.bits, f6.fired, f6.evidence))
    out.sort(key=lambda sg: (sg.deals[0], sg.family))
    return out
