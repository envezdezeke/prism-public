"""Time-split report for F1, F3 and F5 on history alone (owner: Phase 5; M5, M6, M7).

``python -m prism.fraud.eval_baseline [--history DIR] [--fraud-config PATH] [--out-dir DIR]
[--gold PATH]`` replays the history test period (config calibration.test_start..test_end) in
(date, id) order. Each test deal is scored against a baseline of strictly earlier deals only.
The F3 and F5 thresholds and the F5 reference sample come from the train period alone and stay
fixed for the whole replay. It writes ``baseline_eval.json`` and ``baseline_eval.md``.

Nothing is tuned on these numbers. There is no wall clock and no randomness, so two runs on
the same input are byte-identical.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from prism.engine.config import ConfigError, FraudConfig, load_fraud_config
from prism.fraud.baseline import Baseline, Calibration, build_baseline
from prism.fraud.calibrate import compute_thresholds
from prism.fraud.rarity import FraudParams
from prism.fraud.signals import score_f1, score_f3, score_f5

FAMILIES = ("F1", "F3", "F5")
DEFAULT_HISTORY = "data/participant/history"
JSON_NAME = "baseline_eval.json"
MD_NAME = "baseline_eval.md"


def _fired(bits: float, tau: Optional[float]) -> bool:
    return bits > 0 and tau is not None and bits >= tau


def replay(full: Baseline, params: FraudParams, cal: Calibration) -> list[dict[str, Any]]:
    """Score every test-period deal against only the deals that precede it in (date, id) order."""
    order = full.ordered_ids()
    rows: list[dict[str, Any]] = []
    for pos, deal_id in enumerate(order):
        deal = full.deals[deal_id]
        if not params.test_start <= deal.applied <= params.test_end:
            continue
        prior = full.restricted_to(order[:pos])
        profile = deal.profile
        f1 = score_f1(profile, prior, params)
        f3 = score_f3(profile, prior, params)
        f5 = score_f5(profile, prior, params, cal.reference)
        bits = {
            "F1": f1.bits if f1 else 0.0,
            "F3": f3.bits if f3 else 0.0,
            "F5": f5.bits if f5 else 0.0,
        }
        fired = ["F1"] if f1 is not None else []
        fired += [f for f in ("F3", "F5") if _fired(bits[f], cal.tau.get(f))]
        rows.append({
            "deal_id": deal_id,
            "applied": deal.applied.isoformat(),
            "fraud_label": deal.fraud_label,
            "final_status": deal.final_status,
            "good": deal.repaid,
            "bits": bits,
            "fired": sorted(fired),
            "f1_partners": list(f1.partners) if f1 else [],
        })
    return rows


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts over the replay. A good test deal is funded, unlabelled and performing or paid off."""
    good = [r for r in rows if r["good"]]
    fired_good = {f: sum(1 for r in good if f in r["fired"]) for f in FAMILIES}
    flagged = [r["deal_id"] for r in good if all(f in r["fired"] for f in FAMILIES)]
    n_good = len(good)
    return {
        "n_test_deals": len(rows),
        "n_good_test_deals": n_good,
        "fired_among_good": fired_good,
        "fire_rate_among_good": {f: (fired_good[f] / n_good if n_good else 0.0) for f in FAMILIES},
        "false_flags_f1_f3_f5": len(flagged),
        "false_flag_ids": flagged,
        "watch_any_family_among_good": sum(1 for r in good if r["fired"]),
    }


def hold_rule_fires(fired: Sequence[str], cfg: FraudConfig) -> bool:
    """The configured hold rule over a list of fired families (non-voting families never count)."""
    fams = set(fired) - set(cfg.non_voting)
    if not all(f in fams for f in cfg.hold_required):
        return False
    return sum(1 for f in fams if f in cfg.hold_additional_from) >= cfg.hold_min_additional


def hold_rule_report(rows: Sequence[dict[str, Any]], cfg: FraudConfig) -> dict[str, Any]:
    """The configured hold rule on the replay: good deals it would hold (the false-hold metric)
    and, per fraud_label group (evaluation only, never training or thresholds), the 1-based
    position in (date, id) order of the first deal it holds and how many it catches."""
    good = [r for r in rows if r["good"]]
    held_good = [r["deal_id"] for r in good if hold_rule_fires(r["fired"], cfg)]
    labels = sorted({str(r.get("fraud_label") or "") for r in rows} - {"", "none"})
    by_label: dict[str, Any] = {}
    for lab in labels:
        group = [r for r in rows if r.get("fraud_label") == lab]
        hits = [i for i, r in enumerate(group, start=1) if hold_rule_fires(r["fired"], cfg)]
        by_label[lab] = {"n": len(group), "first_hold_position": hits[0] if hits else None, "caught": len(hits)}
    return {
        "rule": {
            "required": list(cfg.hold_required),
            "additional_from": list(cfg.hold_additional_from),
            "min_additional": cfg.hold_min_additional,
            "non_voting": list(cfg.non_voting),
        },
        "families_computed": list(FAMILIES),
        "n_good": len(good),
        "good_held": len(held_good),
        "good_held_rate": len(held_good) / len(good) if good else 0.0,
        "good_held_ids": held_good,
        "by_label": by_label,
    }


def scheme_links(rows: Sequence[dict[str, Any]], members: Sequence[str]) -> dict[str, Any]:
    """For each later member of a scheme: does its F1 partner list contain an earlier member?"""
    by_id = {r["deal_id"]: r for r in rows}
    present = sorted((by_id[m] for m in set(members) if m in by_id), key=lambda r: (r["applied"], r["deal_id"]))
    out_rows = []
    seen: list[str] = []
    for r in present:
        if seen:
            earlier = sorted(set(r["f1_partners"]) & set(seen))
            out_rows.append({"deal_id": r["deal_id"], "applied": r["applied"],
                             "linked_to_earlier_members": earlier, "has_link": bool(earlier)})
        seen.append(r["deal_id"])
    return {
        "members_in_test_period": [r["deal_id"] for r in present],
        "later_members": out_rows,
        "n_later": len(out_rows),
        "n_linked": sum(1 for r in out_rows if r["has_link"]),
    }


def _round(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, 6) + 0.0
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def build_report(
    full: Baseline,
    params: FraudParams,
    gold_members: Optional[Sequence[str]],
    n_warnings: int,
    cfg: Optional[FraudConfig] = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_fraud_config()
    order = full.ordered_ids()
    train_ids = [i for i in order if full.deals[i].applied <= params.train_end]
    train = full.restricted_to(train_ids)
    cal = compute_thresholds(train, params)
    rows = replay(full, params, cal)
    return {
        "meta": {
            "n_history_deals": full.n_history_deals,
            "n_train_deals": len(train_ids),
            "train_end": params.train_end.isoformat(),
            "test_start": params.test_start.isoformat(),
            "test_end": params.test_end.isoformat(),
            "n_load_warnings": n_warnings,
            "gold_given": gold_members is not None,
            "families_built": list(FAMILIES),
        },
        "thresholds": {
            "tau": cal.tau,
            "percentiles": cal.percentiles,
            "n_clean": cal.n_clean,
            "fire_rate_clean": cal.fire_rate_clean,
            "reference_sizes": {k: len(v) for k, v in sorted(cal.reference.items())},
        },
        "summary": {**summarize(rows), "hold_rule": hold_rule_report(rows, cfg)},
        "deals": rows,
        "scheme": None if gold_members is None else scheme_links(rows, gold_members),
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def render_markdown(rep: dict[str, Any]) -> str:
    m, t, s = rep["meta"], rep["thresholds"], rep["summary"]
    L = [
        "# Baseline time-split report (F1, F3, F5)",
        "",
        f"History deals: {m['n_history_deals']}. Train period ends {m['train_end']} "
        f"({m['n_train_deals']} deals). Test period {m['test_start']} to {m['test_end']}, "
        "replayed in (date, id) order; each deal is scored against earlier deals only.",
        "",
        "## Thresholds (train period only)",
        "",
        f"Clean calibration deals: {t['n_clean']}. Thresholds are interpolated percentiles of the "
        "bits on the clean set, scored leave-one-out; they stay fixed for the whole replay.",
        "",
        "| family | percentile | tau (bits) | fire rate on clean set |",
        "|---|---|---|---|",
    ]
    for f in FAMILIES:
        tau = t["tau"].get(f)
        L.append(
            f"| {f} | {t['percentiles'].get(f, 'none (mandatory family)')} | "
            f"{'n/a' if tau is None else f'{tau:.4f}'} | {_pct(t['fire_rate_clean'].get(f, 0.0))} |"
        )
    L += [
        "",
        "## Test period summary",
        "",
        f"Test deals: {s['n_test_deals']}. Good test deals (funded, unlabelled, current or paid off): "
        f"{s['n_good_test_deals']}.",
        "",
        "| family | fired among good deals | rate |",
        "|---|---|---|",
    ]
    for f in FAMILIES:
        L.append(f"| {f} | {s['fired_among_good'][f]} | {_pct(s['fire_rate_among_good'][f])} |")
    L += [
        "",
        f"False flags among good deals (F1 and F3 and F5 all fired): {s['false_flags_f1_f3_f5']}. "
        f"Good deals with any one family fired (watch-style): {s['watch_any_family_among_good']}.",
        "",
        "Good deals that would be flagged:",
        "",
    ]
    L += [f"- {i}" for i in s["false_flag_ids"]] or ["- none"]
    h = s.get("hold_rule")
    if h is not None:
        L += [
            "",
            "## Configured hold rule on the test period",
            "",
            f"Rule: all of {', '.join(h['rule']['required'])} plus at least {h['rule']['min_additional']} of "
            f"{', '.join(h['rule']['additional_from'])} (never {', '.join(h['rule']['non_voting']) or 'none'}); "
            f"families computed in this replay: {', '.join(h['families_computed'])}.",
            "",
            f"Good test deals the rule would hold: {h['good_held']} of {h['n_good']} ({_pct(h['good_held_rate'])}).",
            "",
            "Fraud-label groups (evaluation only, never used for training or thresholds):",
            "",
            "| label | deals | first held at position | caught |",
            "|---|---|---|---|",
        ]
        for lab, g in h["by_label"].items():
            pos = "never" if g["first_hold_position"] is None else str(g["first_hold_position"])
            L.append(f"| {lab} | {g['n']} | {pos} | {g['caught']} |")
        if not h["by_label"]:
            L.append("| none | 0 | never | 0 |")
    L += ["", "## Scheme links", ""]
    sch = rep["scheme"]
    if sch is None:
        L.append("No --gold file given.")
    else:
        L.append(
            f"Scheme members in the test period: {len(sch['members_in_test_period'])}. "
            f"Later members with an F1 link to an earlier member: {sch['n_linked']} of {sch['n_later']}."
        )
        L.append("")
        for r in sch["later_members"]:
            L.append(f"- {r['deal_id']} ({r['applied']}): "
                     + ("linked to " + ", ".join(r["linked_to_earlier_members"]) if r["has_link"] else "no link"))
    L += [
        "",
        "## Caveats",
        "",
        "- F2 and F4 are not built into this history replay (history rows carry no document "
        "metadata or arrival times), so the false-flag count above (all of F1, F3 and F5) is a lower "
        "bound: with F2 and F4 there are more ways to reach the two additional families a hold needs.",
        "- tau for F3 and F5 and the F5 reference sample come from the train period only.",
        "- F1 has no percentile (it is mandatory for a hold and fires on any strong shared entity), so "
        "its misfire rate is a measurement on good deals, not a tuned number.",
        "- Nothing here is tuned on the test period.",
        "",
    ]
    return "\n".join(L)


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m prism.fraud.eval_baseline", description=__doc__.split("\n")[0])
    ap.add_argument("--history", default=DEFAULT_HISTORY, help="history directory (deals.csv, monthly_deposits.csv)")
    ap.add_argument("--fraud-config", default=None, help="fraud.yaml path (default config/fraud.yaml)")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--gold", default=None, help="optional JSON with a history_flags list of scheme ids")
    args = ap.parse_args(argv)

    hist = Path(args.history)
    if not (hist / "deals.csv").is_file():
        print(f"error: {hist / 'deals.csv'} not found", file=sys.stderr)
        return 2
    try:
        cfg = load_fraud_config(args.fraud_config)
        params = FraudParams.from_config(cfg)
    except (OSError, ConfigError) as e:
        print(f"error: fraud config: {e}", file=sys.stderr)
        return 2

    members: Optional[list[str]] = None
    if args.gold is not None:
        try:
            gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"error: gold file: {e}", file=sys.stderr)
            return 2
        flags = gold.get("history_flags") if isinstance(gold, dict) else None
        members = [x for x in flags if isinstance(x, str)] if isinstance(flags, list) else []

    full = build_baseline(hist, cfg)
    report = build_report(full, params, members, len(full.warnings), cfg)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / JSON_NAME, json.dumps(_round(report), sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    _write(out / MD_NAME, render_markdown(_round(report)))
    print(f"wrote {out / JSON_NAME} and {out / MD_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
