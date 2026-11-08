"""Fraud graph producer (owner: Phase 5, FRD-02, FRD-03): the UI contract I4 from the real engine.

Contract: ``build_fraud_graph`` turns end-of-run DealStates plus the history baseline into a
``prism.contract.FraudGraph`` in the shape of fixtures/graph.json. Pure and deterministic
(sorted iteration, no wall clock). Only strong shared entities become entity nodes; a
history deal appears only when it carries one of them or sits in a cluster. Every edge has
a rarity in [0, 1] (1 = no history deal uses the entity), a weight (kind weight times bits
of surprise), the sim time it first becomes visible and a plain-words evidence string.

``at`` filtering is one function (``filter_graph``) over the full graph plus a small
timeline (deal first-seen times, cluster hold times), so the CLI and the API agree.
At most ``MAX_NODES`` nodes are emitted (highest-weight entities first, ties by id).

    python -m prism.fraud.graph_out --folder data/participant/shared_folder \
        --arrivals data/participant/sim/arrivals.jsonl --out out/graph.json

writes out/graph.json (contract shape) and out/graph_timeline.json next to it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from prism.contract import Cluster, FraudGraph, format_sim_time, parse_sim_time
from prism.engine.state import DealState
from prism.fraud.baseline import Baseline
from prism.fraud.canon import canon_value
from prism.fraud.graph import build_graph
from prism.fraud.rarity import surprisal

MAX_NODES = 600
_LABEL_MAX = 80
_BAD = str.maketrans({c: " " for c in "{}<>\n\r\t"})
_TYPE_ORDER = {"deal": 0, "history_deal": 1, "entity": 2}


def _clean(s: str) -> str:
    return " ".join(str(s).translate(_BAD).split())


def _kind_plain(kind: str) -> str:
    return kind.replace("_", " ")


def _earliest(state: DealState) -> Optional[datetime]:
    times = [d.arrive_at for d in state.docs]
    return min(times) if times else None


def build_full(
    states: Mapping[str, DealState],
    baseline: Baseline,
    cfg: Any,
    *,
    clusters: Optional[Sequence[Cluster]] = None,
) -> tuple[dict, dict]:
    """Return (graph dict in contract shape, timeline dict). ``clusters`` defaults to evaluate()."""
    live = {f: s for f, s in states.items() if not s.error}
    firsts = {f: _earliest(s) for f, s in live.items()}
    seen_times = [t for t in firsts.values() if t is not None]
    if not seen_times:
        return {"nodes": [], "edges": [], "clusters": []}, {"deal_first_seen": {}, "cluster_hold_at": {}}
    run_start, run_end = min(seen_times), max(seen_times)

    per_deal_flag: dict[str, Optional[str]] = {f: s.fraud.flagged_at for f, s in live.items()}
    if clusters is None:
        from prism.fraud.decide import evaluate

        outcome = evaluate(states, baseline, cfg, run_end)
        clusters = list(outcome.clusters)
        for f, fr in outcome.per_deal.items():
            if per_deal_flag.get(f) is None and fr.flagged_at:
                per_deal_flag[f] = fr.flagged_at

    graph = build_graph(states, baseline)
    params = baseline.params
    n = baseline.n_effective()
    max_bits = surprisal(0, n)

    strong = sorted(k for k, w in graph.entity_weights.items() if w.strong)
    # Cap: keep the highest-weight entities first (ties by key) until the node budget is used.
    def _w(key: tuple[str, str]) -> float:
        return params.weight(key[0]) * graph.entity_weights[key].bits

    budget = MAX_NODES - len(live)
    kept: list[tuple[str, str]] = []
    hist_used: set[str] = set()
    for key in sorted(strong, key=lambda k: (-_w(k), k)):
        new_hist = {d for d in graph.entity_deals.get(key, []) if d not in live} - hist_used
        if len(kept) + len(hist_used) + len(new_hist) + 1 > budget:
            continue
        kept.append(key)
        hist_used |= new_hist
    kept.sort()

    # Entity node ids: ent-<kind>-<NN>, running index per kind over sorted (kind, value).
    ent_id: dict[tuple[str, str], str] = {}
    per_kind: dict[str, int] = {}
    nodes: list[dict] = []
    for kind, value in kept:
        per_kind[kind] = per_kind.get(kind, 0) + 1
        ent_id[(kind, value)] = "ent-" + kind + "-" + format(per_kind[kind], "02d")
        nodes.append({"id": ent_id[(kind, value)], "type": "entity",
                      "label": _clean(value)[:_LABEL_MAX], "kind": kind})

    # First visible time of (deal, kind, value): earliest doc carrying the canonical value.
    doc_time: dict[str, dict[str, datetime]] = {
        f: {d.path: d.arrive_at for d in s.docs} for f, s in live.items()
    }
    doc_name: dict[str, dict[str, str]] = {f: {d.path: d.name for d in s.docs} for f, s in live.items()}
    carry: dict[tuple[str, str, str], tuple[datetime, str]] = {}
    for f in sorted(live):
        for ref in live[f].entities:
            val = canon_value(ref.kind, ref.value)
            if (ref.kind, val) not in ent_id:
                continue
            t = doc_time[f].get(ref.source_doc, firsts[f])
            name = doc_name[f].get(ref.source_doc, ref.source_doc.rsplit("/", 1)[-1])
            k = (f, ref.kind, val)
            if k not in carry or (t, name) < carry[k]:
                carry[k] = (t, name)

    edges: list[dict] = []
    hist_nodes: set[str] = set()
    for key in kept:
        kind, value = key
        w = graph.entity_weights[key]
        if max_bits > 0:
            rarity = min(1.0, w.bits / max_bits)
        else:
            rarity = 1.0 if w.df == 0 else 0.0
        weight = max(0.0, params.weight(kind) * w.bits)
        tail = "; used by " + str(w.df) + " of " + str(n) + " history deals."
        val = _clean(value)
        for carrier in graph.entity_deals.get(key, []):
            if carrier in live:
                t, name = carry.get((carrier, kind, value), (firsts[carrier], "the deal folder"))
                ev = (_clean(carrier) + ": " + _kind_plain(kind) + " '" + val + "' appears in "
                      + _clean(name) + tail)
            else:
                t = run_start
                hist_nodes.add(carrier)
                ev = ("Prior deal " + _clean(carrier) + " used '" + val + "' as " + _kind_plain(kind) + tail)
            edges.append({
                "source": carrier, "target": ent_id[key], "entity_kind": kind,
                "rarity": round(rarity, 4), "weight": round(weight, 4),
                "first_visible_at": format_sim_time(t), "evidence": ev,
            })

    out_clusters: list[dict] = []
    hold_at: dict[str, Optional[str]] = {}
    for c in clusters:
        d = c.model_dump(mode="json")
        out_clusters.append(d)
        for m in c.deals:
            if m not in live:
                hist_nodes.add(m)
        flags = sorted(parse_sim_time(per_deal_flag[m]) for m in c.deals
                       if m in live and per_deal_flag.get(m))
        hold_at[c.id] = format_sim_time(flags[0]) if flags else None

    nodes += [{"id": f, "type": "deal", "label": f, "kind": None} for f in live]
    nodes += [{"id": h, "type": "history_deal", "label": h, "kind": None} for h in hist_nodes]
    nodes.sort(key=lambda x: (_TYPE_ORDER[x["type"]], x["id"]))
    edges.sort(key=lambda e: (e["source"], e["target"]))
    timeline = {
        "deal_first_seen": {f: format_sim_time(t) for f, t in sorted(firsts.items()) if t is not None},
        "cluster_hold_at": dict(sorted(hold_at.items())),
    }
    return {"nodes": nodes, "edges": edges, "clusters": out_clusters}, timeline


def filter_graph(graph: Mapping[str, Any], timeline: Mapping[str, Any], at: Union[str, datetime]) -> dict:
    """The graph as it looked at sim time ``at`` (no edge, node or hold tier before its time)."""
    cut = at if isinstance(at, datetime) else parse_sim_time(at)
    first = {f: parse_sim_time(t) for f, t in (timeline.get("deal_first_seen") or {}).items()}
    hold_at = timeline.get("cluster_hold_at") or {}
    deal_ids = {n["id"] for n in graph["nodes"] if n["type"] == "deal"}
    visible_deals = {d for d in deal_ids if d in first and first[d] <= cut}

    edges = [e for e in graph["edges"]
             if parse_sim_time(e["first_visible_at"]) <= cut
             and (e["source"] not in deal_ids or e["source"] in visible_deals)]
    clusters = []
    keep_hist: set[str] = set()
    for c in graph["clusters"]:
        members = [m for m in c["deals"] if m not in deal_ids or m in visible_deals]
        if not any(m in visible_deals for m in members):
            continue
        tier = c["tier"]
        h = hold_at.get(c["id"])
        if tier == "hold" and h is not None and parse_sim_time(h) > cut:
            tier = "watch"
        keep_hist |= {m for m in members if m not in deal_ids}
        clusters.append({**c, "deals": members, "tier": tier})
    used = {e["source"] for e in edges} | {e["target"] for e in edges} | keep_hist
    nodes = [n for n in graph["nodes"]
             if (n["type"] == "deal" and n["id"] in visible_deals) or (n["type"] != "deal" and n["id"] in used)]
    return {"nodes": nodes, "edges": edges, "clusters": clusters}


def build_fraud_graph(
    states: Mapping[str, DealState],
    baseline: Baseline,
    cfg: Any,
    *,
    at: Optional[Union[str, datetime]] = None,
    clusters: Optional[Sequence[Cluster]] = None,
) -> FraudGraph:
    full, timeline = build_full(states, baseline, cfg, clusters=clusters)
    if at is not None:
        full = filter_graph(full, timeline, at)
    return FraudGraph.model_validate(full)


def full_from_run(result: Any, data_root: Union[str, Path], *, fraud_path: Optional[Union[str, Path]] = None
                  ) -> tuple[dict, dict]:
    """Graph and timeline from a RunResult; clusters are the run's last-tick clusters when kept."""
    from prism.engine.config import load_rules
    from prism.fraud.baseline import build_baseline

    rules = load_rules(Path(data_root), fraud_path=Path(fraud_path) if fraud_path else None)
    baseline = build_baseline(rules.history_dir, rules.fraud)
    kept = getattr(result, "clusters", None)
    return build_full(result.states, baseline, rules.fraud, clusters=list(kept) if kept else None)


def graph_from_run(result: Any, data_root: Union[str, Path], *, fraud_path: Optional[Union[str, Path]] = None,
                   at: Optional[Union[str, datetime]] = None) -> FraudGraph:
    full, timeline = full_from_run(result, data_root, fraud_path=fraud_path)
    if at is not None:
        full = filter_graph(full, timeline, at)
    return FraudGraph.model_validate(full)


def timeline_path(graph_path: Path) -> Path:
    return graph_path.with_name(graph_path.stem + "_timeline.json")


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    from prism.engine.config import ConfigError
    from prism.engine.run import run_sim
    from prism.engine.visibility import parse_time

    ap = argparse.ArgumentParser(prog="python -m prism.fraud.graph_out")
    ap.add_argument("--folder", type=Path, required=True)
    ap.add_argument("--arrivals", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, default=None)
    ap.add_argument("--fraud-config", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("out/graph.json"))
    ap.add_argument("--at", default=None)
    a = ap.parse_args(argv)
    if not a.arrivals.is_file():
        print("graph: arrivals file not found: " + str(a.arrivals), file=sys.stderr)
        return 2
    root = a.data_root if a.data_root is not None else a.folder.resolve().parent
    try:
        at = parse_time(a.at) if a.at else None
        result = run_sim(a.folder, a.arrivals, data_root=root, fraud_path=a.fraud_config)
        full, timeline = full_from_run(result, root, fraud_path=a.fraud_config)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print("graph: " + str(e), file=sys.stderr)
        return 2
    if at is not None:
        full = filter_graph(full, timeline, at)
    graph = FraudGraph.model_validate(full).model_dump(mode="json")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump(graph))
    with open(timeline_path(a.out), "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump(timeline))
    print("graph: " + str(len(graph["nodes"])) + " nodes, " + str(len(graph["edges"])) + " edges, "
          + str(len(graph["clusters"])) + " clusters -> " + str(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
