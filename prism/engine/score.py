"""Self-score against eval/gold.json (D-06). Semantics follow eval/README.md.

Only deals with status draft or done are scored. The result dict is plain JSON-able data
(no timestamps), so ``out/score_latest.json`` is reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from prism.engine.state import PIPELINE
from prism.engine.visibility import parse_time

SCORED_STATUSES = ("draft", "done")
EXTRACTED_FIELDS = (
    "legal_name", "ein", "amount_requested", "purpose_category", "product",
    "term_months", "guarantor_name", "annual_revenue_stated", "needed_by",
)
GATES = ("credit_decision", "committee_approval", "commitment_signoff", "funding_release")
_NAME_W = 28


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_match(got: Any, want: Any) -> bool:
    """Trim+casefold strings, exact numbers, null equals null only."""
    if got is None or want is None:
        return got is None and want is None
    if isinstance(got, str) and isinstance(want, str):
        return got.strip().casefold() == want.strip().casefold()
    if _is_num(got) and _is_num(want):
        return got == want
    return False


def _stage_idx(stage: Optional[str]) -> Optional[int]:
    return PIPELINE.index(stage) if stage in PIPELINE else None


def _first_request_to(requests: list[dict], gate: str) -> Optional[str]:
    cands = []
    for i, r in enumerate(requests):
        if not (isinstance(r, dict) and r.get("type") == gate and r.get("to") != "borrower"):
            continue
        try:
            cands.append((parse_time(r["at"]), i, r))
        except (KeyError, ValueError, TypeError):
            continue  # an unparseable time cannot be ordered; ignore that request
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][2].get("to")


def _furthest_stage(stage_log: list[dict]) -> Optional[str]:
    best: Optional[int] = None
    for e in stage_log:
        i = _stage_idx(e.get("stage")) if isinstance(e, dict) else None
        if i is not None and (best is None or i > best):
            best = i
    return None if best is None else PIPELINE[best]


def _score_deal(sub: Optional[Mapping[str, Any]], gold: Mapping[str, Any]) -> dict[str, Any]:
    sub = sub or {}
    s_ext = sub.get("extracted") or {}
    g_ext = gold.get("extracted") or {}
    accept = gold.get("extracted_accept") or {}
    extracted: dict[str, Any] = {}
    for f in EXTRACTED_FIELDS:
        want, got = g_ext.get(f), s_ext.get(f)
        ok = values_match(got, want) or any(values_match(got, alt) for alt in accept.get(f, []))
        extracted[f] = {"ok": ok, "got": got, "want": want}

    g_iss = set(gold.get("issues") or [])
    opt = set(gold.get("issues_optional") or [])
    s_iss = set(sub.get("issues") or [])
    missed = sorted(g_iss - s_iss - opt)
    false_alarms = sorted(s_iss - g_iss - opt)
    issues = {"ok": not missed and not false_alarms, "missed": missed, "false_alarms": false_alarms,
              "tp": len((g_iss - opt) & s_iss)}

    requests = sub.get("human_requests") or []
    routing: dict[str, Any] = {}
    for gate, want in (gold.get("routing") or {}).items():
        got = _first_request_to(requests, gate)
        routing[gate] = {"ok": got == want, "got": got, "want": want}

    got_stage = _furthest_stage(sub.get("stage_log") or [])
    want_stage = gold.get("final_stage")
    final_stage = {"ok": got_stage == want_stage, "got": got_stage, "want": want_stage}

    g_fr = gold.get("fraud") or {}
    s_fr = sub.get("fraud") or {}
    fraud: dict[str, Any] = {
        "flagged": {"ok": bool(s_fr.get("flagged")) == bool(g_fr.get("flagged")),
                    "got": bool(s_fr.get("flagged")), "want": bool(g_fr.get("flagged"))},
        "held": {"ok": bool(s_fr.get("held")) == bool(g_fr.get("held")),
                 "got": bool(s_fr.get("held")), "want": bool(g_fr.get("held"))},
    }
    if g_fr.get("flagged"):
        at, latest = s_fr.get("stage_at_flag"), g_fr.get("latest_ok_stage")
        ai, li = _stage_idx(at), _stage_idx(latest)
        ok = ai is not None and (li is None or ai <= li)
        fraud["latest_ok_stage"] = {"ok": ok, "got": at, "want": latest}
    g_links, s_links = set(g_fr.get("linked_deals") or []), set(s_fr.get("linked_deals") or [])
    fraud["linked_deals"] = {"ok": g_links == s_links, "missed": sorted(g_links - s_links),
                             "extra": sorted(s_links - g_links)}
    return {"extracted": extracted, "issues": issues, "routing": routing,
            "final_stage": final_stage, "fraud": fraud}


def _ratio(items: list[bool]) -> dict[str, int]:
    return {"ok": sum(items), "total": len(items)}


def score_submission(sub: Mapping[str, Any], gold: Mapping[str, Any]) -> dict[str, Any]:
    sub_deals = sub.get("deals") or {}
    deals: dict[str, Any] = {}
    unscored: list[str] = []
    for folder in sorted(gold.get("deals") or {}):
        g = gold["deals"][folder]
        if g.get("status") not in SCORED_STATUSES:
            unscored.append(folder)
            continue
        deals[folder] = _score_deal(sub_deals.get(folder), g)

    g_hist = set(gold.get("history_flags") or [])
    s_hist = set(sub.get("history_flags") or [])
    must_not = set(gold.get("history_must_not_flag") or [])
    history = {
        "ok": g_hist == s_hist,
        "missed": sorted(g_hist - s_hist),
        "extra": sorted(s_hist - g_hist),
        "must_not_flagged": sorted(s_hist & must_not),
    }

    ds = list(deals.values())
    totals: dict[str, Any] = {
        "deals": len(ds),
        "extracted": _ratio([c["ok"] for d in ds for c in d["extracted"].values()]),
        "issues": {"tp": sum(d["issues"]["tp"] for d in ds),
                   "fn": sum(len(d["issues"]["missed"]) for d in ds),
                   "fp": sum(len(d["issues"]["false_alarms"]) for d in ds)},
        "routing": _ratio([c["ok"] for d in ds for c in d["routing"].values()]),
        "final_stage": _ratio([d["final_stage"]["ok"] for d in ds]),
        "fraud_flagged": _ratio([d["fraud"]["flagged"]["ok"] for d in ds]),
        "fraud_held": _ratio([d["fraud"]["held"]["ok"] for d in ds]),
        "fraud_latest_ok_stage": _ratio(
            [d["fraud"]["latest_ok_stage"]["ok"] for d in ds if "latest_ok_stage" in d["fraud"]]
        ),
        "fraud_linked_deals": _ratio([d["fraud"]["linked_deals"]["ok"] for d in ds]),
    }
    return {"deals": deals, "history": history, "totals": totals, "unscored": unscored}


def _verdict(fraud: Mapping[str, Any]) -> str:
    fl, held = fraud["flagged"], fraud["held"]
    if fl["want"] and not fl["got"]:
        return "MISSED"
    if fl["got"] and not fl["want"]:
        return "FALSE FLAG"
    if "latest_ok_stage" in fraud and not fraud["latest_ok_stage"]["ok"]:
        return "LATE"
    if not held["ok"]:
        return "HELD-DIFF"
    return "ok"


def _short(name: str) -> str:
    return name if len(name) <= _NAME_W else name[: _NAME_W - 2] + ".."


def _cell(v: Any) -> str:
    return "-" if v is None else str(v)


def format_table(result: Mapping[str, Any]) -> str:
    head = f"{'deal':<{_NAME_W}} {'extr':>5} {'issues':>9} {'route':>5} {'stage':<18} {'fraud':<10} links"
    lines = [head, "-" * len(head)]
    for folder, d in result["deals"].items():
        ext = _ratio([c["ok"] for c in d["extracted"].values()])
        iss = d["issues"]
        issues = "ok" if iss["ok"] else f"+{len(iss['false_alarms'])} -{len(iss['missed'])}"
        rt = _ratio([c["ok"] for c in d["routing"].values()])
        fs = d["final_stage"]
        stage = "ok" if fs["ok"] else f"{_cell(fs['got'])}/{_cell(fs['want'])}"
        ld = d["fraud"]["linked_deals"]
        links = "ok" if ld["ok"] else f"-{len(ld['missed'])} +{len(ld['extra'])}"
        lines.append(
            f"{_short(folder):<{_NAME_W}} {ext['ok']}/{ext['total']:<3} {issues:>9} "
            f"{rt['ok']}/{rt['total']:<3} {stage:<18} {_verdict(d['fraud']):<10} {links}"
        )
    t = result["totals"]
    lines.append("-" * len(head))
    lines.append(
        f"{'TOTAL (' + str(t['deals']) + ' deals)':<{_NAME_W}} "
        f"{t['extracted']['ok']}/{t['extracted']['total']} "
        f"tp{t['issues']['tp']} fn{t['issues']['fn']} fp{t['issues']['fp']} "
        f"route {t['routing']['ok']}/{t['routing']['total']} "
        f"stage {t['final_stage']['ok']}/{t['final_stage']['total']} "
        f"flag {t['fraud_flagged']['ok']}/{t['fraud_flagged']['total']} "
        f"held {t['fraud_held']['ok']}/{t['fraud_held']['total']} "
        f"links {t['fraud_linked_deals']['ok']}/{t['fraud_linked_deals']['total']}"
    )
    h = result["history"]
    lines.append(
        f"history_flags: {'ok' if h['ok'] else 'MISMATCH'} "
        f"missed={len(h['missed'])} extra={len(h['extra'])} must_not_flagged={len(h['must_not_flagged'])}"
    )
    if result["unscored"]:
        lines.append(f"unscored (status not draft/done): {len(result['unscored'])}")
    return "\n".join(lines)


def _load(path: Path | str, what: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{what} is not valid JSON: {e.msg}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{what} must be a JSON object")
    return data


def run_score(submission_path: Path | str, gold_path: Path | str, out_path: Path | str) -> dict[str, Any]:
    sub = _load(submission_path, "submission")
    gold = _load(gold_path, "gold")
    result = score_submission(sub, gold)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    return result
