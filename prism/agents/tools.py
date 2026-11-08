"""Read-only investigator tools over the entity graph, the deals' documents and the history (NEM-03).

No tool takes a file path, touches the filesystem or the network, or changes any state. Summaries are
deterministic and built by concatenation (never format() on document-derived text).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from prism.contract import FAMILY_LABELS, format_sim_time
from prism.fraud.canon import canon_value
from prism.fraud.graph import ENTITY_KINDS

_SUMMARY_MAX = 240
_CONTENT_MAX = 2000


@dataclass(frozen=True)
class CaseTarget:
    id: str
    deals: tuple[str, ...]
    history: tuple[str, ...]
    tier: str
    families: dict = field(default_factory=dict)  # family -> {"fired": bool, "evidence": str}
    reasons: dict = field(default_factory=dict)  # deal -> fraud reason


@dataclass
class InvestigationContext:
    states: Mapping[str, Any]
    baseline: Any
    graph: Any
    targets: list[CaseTarget] = field(default_factory=list)


def _reasons(states: Mapping[str, Any], deals: Sequence[str]) -> dict:
    out = {}
    for d in deals:
        r = getattr(getattr(states[d], "fraud", None), "reason", None)
        if r:
            out[d] = str(r)
    return out


def case_targets(states: Mapping[str, Any], outcome_clusters: Sequence[Any]) -> list[CaseTarget]:
    """Hold clusters from the engine, or a deterministic fallback over flagged, linked current deals."""
    targets: list[CaseTarget] = []
    if outcome_clusters:
        for c in outcome_clusters:
            if getattr(c, "tier", None) != "hold":
                continue
            members = list(c.deals)
            cur = tuple(sorted(d for d in members if d in states))
            hist = tuple(sorted(d for d in members if d not in states))
            fams = {k: {"fired": bool(v.fired), "evidence": str(v.evidence)} for k, v in sorted(c.families.items())}
            targets.append(CaseTarget(str(c.id), cur, hist, "hold", fams, _reasons(states, cur)))
        return targets
    flagged = sorted(d for d, s in states.items() if getattr(getattr(s, "fraud", None), "flagged", False))
    flagged_set = set(flagged)
    seen: set[str] = set()
    comps: list[tuple[list[str], list[str]]] = []
    for start in flagged:
        if start in seen:
            continue
        comp, hist, stack = set(), set(), [start]
        while stack:
            d = stack.pop()
            if d in comp:
                continue
            comp.add(d)
            for link in states[d].fraud.linked_deals or []:
                if link in flagged_set:
                    stack.append(link)
                elif link not in states:
                    hist.add(link)
        seen |= comp
        comps.append((sorted(comp), sorted(hist)))
    comps.sort(key=lambda c: c[0][0])
    fams = {k: {"fired": False, "evidence": "not evaluated"} for k in sorted(FAMILY_LABELS)}
    for i, (cur, hist) in enumerate(comps, start=1):
        targets.append(CaseTarget("C-%03d" % i, tuple(cur), tuple(hist), "hold", dict(fams), _reasons(states, cur)))
    return targets


def _str_arg(args: Mapping[str, Any], name: str) -> Optional[str]:
    v = args.get(name) if isinstance(args, Mapping) else None
    return v if isinstance(v, str) and v.strip() else None


def _fn(name: str, description: str, props: dict) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": sorted(props)}}}


_S = {"type": "string"}
TOOL_SPECS: list[dict] = [
    _fn("cluster_summary", "The held cluster: its deals, history deals, fired fraud families and hold reasons.",
        {"cluster_id": {**_S, "description": "cluster id, e.g. C-001"}}),
    _fn("deal_entities", "Canonical entities of one current deal with how many deals share each and whether it is rare.",
        {"deal": {**_S, "description": "current deal name"}}),
    _fn("entity_deals", "Every current and history deal carrying one entity, and how rare it is in history.",
        {"kind": {**_S, "description": "entity kind: " + ", ".join(sorted(ENTITY_KINDS))},
         "value": {**_S, "description": "entity value"}}),
    _fn("history_deal", "Outcome of one history deal: applied date, final status, fraud label, repayment status.",
        {"deal_id": {**_S, "description": "history deal id"}}),
    _fn("deal_documents", "Stage, issues, hold reason and documents (class, arrival, PDF producer and creator) of one deal.",
        {"deal": {**_S, "description": "current deal name"}}),
]
TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOL_SPECS)


def _cut(s: str, n: int = _SUMMARY_MAX) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _cluster_summary(ctx: InvestigationContext, args: Mapping[str, Any]) -> tuple[dict, str]:
    cid = _str_arg(args, "cluster_id")
    t = next((x for x in ctx.targets if x.id == cid), None)
    if t is None:
        return {"error": "unknown cluster"}, "Unknown cluster."
    fams = {k: {"label": FAMILY_LABELS.get(k, k), "fired": v["fired"], "evidence": v["evidence"]}
            for k, v in sorted(t.families.items())}
    res = {"id": t.id, "tier": t.tier, "deals": list(t.deals), "history": list(t.history),
           "families": fams, "reasons": dict(sorted(t.reasons.items()))}
    fired = [FAMILY_LABELS.get(k, k) for k, v in sorted(t.families.items()) if v["fired"]]
    summary = (t.id + ": " + t.tier + ", " + str(len(t.deals)) + " current deals, " + str(len(t.history))
               + " history deals, fired: " + (", ".join(fired) if fired else "none") + ".")
    return res, _cut(summary)


def _history_of(ctx: InvestigationContext, ids: Sequence[str]) -> list[str]:
    return sorted(d for d in ids if d not in ctx.states)


def _deal_entities(ctx: InvestigationContext, args: Mapping[str, Any]) -> tuple[dict, str]:
    deal = _str_arg(args, "deal")
    if deal is None or deal not in ctx.states:
        return {"error": "unknown deal"}, "Unknown deal."
    rows = []
    for ref in ctx.graph.deal_entities.get(deal, []):
        key = (ref.kind, ref.value)
        w = ctx.graph.entity_weights.get(key)
        rows.append({"kind": ref.kind, "value": ref.value, "links": int(getattr(w, "links", 0) or 0),
                     "history_deals": len(_history_of(ctx, ctx.graph.entity_deals.get(key, []))),
                     "strong": bool(getattr(w, "strong", False))})
    rows.sort(key=lambda r: (-r["links"], r["kind"], r["value"]))
    n_strong = sum(1 for r in rows if r["strong"])
    rows = rows[:25]
    shared = [r for r in rows if r["links"] > 1]
    summary = deal + ": " + str(len(rows)) + " entities, " + str(len(shared)) + " shared, " + str(n_strong) + " rare."
    if shared:
        summary += " Top: " + shared[0]["kind"] + " " + shared[0]["value"] + " (" + str(shared[0]["links"]) + " links)."
    return {"deal": deal, "entities": rows}, _cut(summary)


def _entity_deals(ctx: InvestigationContext, args: Mapping[str, Any]) -> tuple[dict, str]:
    kind, raw = _str_arg(args, "kind"), _str_arg(args, "value")
    if kind is None or kind not in ENTITY_KINDS:
        return {"error": "unknown entity kind"}, "Unknown entity kind."
    if raw is None:
        return {"error": "missing value"}, "Missing entity value."
    value = canon_value(kind, raw)
    if not value:
        return {"error": "empty value"}, "Empty entity value."
    carriers = ctx.graph.entity_deals.get((kind, value), [])
    current = sorted(d for d in carriers if d in ctx.states)
    hist_ids = set(_history_of(ctx, carriers))
    try:
        hist_ids |= set(ctx.baseline.deals_with(kind, value))
    except Exception:  # noqa: BLE001
        pass
    history = sorted(hist_ids)
    try:
        m = int(ctx.baseline.n_effective())
    except Exception:  # noqa: BLE001
        m = len(getattr(ctx.baseline, "deals", {}) or {})
    rarity = "used by " + str(len(history)) + " of " + str(m) + " history deals"
    res = {"kind": kind, "value": value, "current_deals": current, "history_deals": history, "rarity": rarity}
    summary = kind + " " + value + ": " + str(len(current)) + " current deals, " + rarity + "."
    return res, _cut(summary)


def _history_deal(ctx: InvestigationContext, args: Mapping[str, Any]) -> tuple[dict, str]:
    did = _str_arg(args, "deal_id")
    deals = getattr(ctx.baseline, "deals", {}) or {}
    if did is None or did not in deals:
        return {"error": "unknown history deal"}, "Unknown history deal."
    h = deals[did]
    applied = h.applied.isoformat() if hasattr(h.applied, "isoformat") else str(h.applied)
    res = {"deal_id": did, "applied": applied, "final_status": h.final_status, "fraud_label": h.fraud_label,
           "repayment_status": h.repayment_status}
    summary = (did + ": applied " + applied + ", " + str(h.final_status) + ", fraud label " + str(h.fraud_label)
               + ", repayment " + str(h.repayment_status) + ".")
    return res, _cut(summary)


def _deal_documents(ctx: InvestigationContext, args: Mapping[str, Any]) -> tuple[dict, str]:
    deal = _str_arg(args, "deal")
    if deal is None or deal not in ctx.states:
        return {"error": "unknown deal"}, "Unknown deal."
    s = ctx.states[deal]
    docs = []
    for d in list(s.docs)[:30]:
        meta = d.meta or {}
        docs.append({"name": d.name, "class": d.doc_class, "arrived": format_sim_time(d.arrive_at),
                     "producer": meta.get("producer"), "creator": meta.get("creator")})
    reason = getattr(s.fraud, "reason", None)
    res = {"deal": deal, "stage": str(getattr(s, "stage", "")), "issues": list(s.issues or []),
           "fraud_reason": reason, "documents": docs}
    producers = sorted({str(x["producer"]) for x in docs if x["producer"]})
    summary = (deal + ": stage " + str(getattr(s, "stage", "")) + ", " + str(len(docs)) + " documents, "
               + str(len(s.issues or [])) + " issues, producers: " + (", ".join(producers) if producers else "none") + ".")
    return res, _cut(summary)


_DISPATCH = {
    "cluster_summary": _cluster_summary,
    "deal_entities": _deal_entities,
    "entity_deals": _entity_deals,
    "history_deal": _history_deal,
    "deal_documents": _deal_documents,
}


def run_tool(ctx: InvestigationContext, name: Any, args: Any) -> tuple[dict, str]:
    """``(result, result_summary)``. Unknown tools, bad arguments and failures return fixed error dicts."""
    fn = _DISPATCH.get(name) if isinstance(name, str) else None
    if fn is None:
        return {"error": "unknown tool"}, "Unknown tool."
    if not isinstance(args, Mapping):
        return {"error": "bad arguments"}, "Bad arguments."
    try:
        return fn(ctx, args)
    except Exception:  # noqa: BLE001
        return {"error": "tool failed"}, "Tool failed."


def tool_content(result: Any) -> str:
    return json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)[:_CONTENT_MAX]
