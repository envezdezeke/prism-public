"""Out-of-sample check of the old-trick rules against the bank's own labels in history/deals.csv.

The engine's OLD_TRICK checks were written from rules/known_fraud_checks.md, not fitted to data. This
script replays the same rule code over every history deal (257 rows the checks were never tuned on)
and compares with ``fraud_label``:

* SHELL_ADDRESS: the engine's own ``shell_address_patterns`` on ``business_address``.
* DUP_EIN: the engine's ``norm_ein``/``name_base``; an EIN seen EARLIER in history under a different
  base name (leave-one-out in time, so a row never matches itself or a later row).
* REV_INFLATION: stated revenue vs the engine's ratio (config ``rev_inflation_ratio``, default 1.5)
  times annualized deposits (``monthly_deposits.csv``: mean month x 12, last 6 months on file).

STMT_TAMPER and ID_MISMATCH need the documents themselves (statement lines, the photo ID); history
has neither, so they are covered by unit and synthetic tests instead (reported, not scored here).

Prints counts only (no names, EINs or addresses). Exit 0 always; this is a report.
Usage: PYTHONUTF8=1 python scripts/validate_old_tricks.py [--history data/participant/history]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from prism.read.normalize import name_base, norm_ein
from prism.read.old_tricks import _shell_patterns, _threshold

CODES = ("SHELL_ADDRESS", "DUP_EIN", "REV_INFLATION")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, default=Path("data/participant/history"))
    ap.add_argument("--json", type=Path, default=Path("out/old_tricks_validation.json"))
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.history / "deals.csv", encoding="utf-8-sig", newline="")))
    rows.sort(key=lambda r: (r.get("application_date") or "", r.get("deal_id") or ""))
    deps: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in csv.DictReader(open(a.history / "monthly_deposits.csv", encoding="utf-8-sig", newline="")):
        try:
            deps[r["deal_id"]].append((r["month"], float(r["total_deposits"])))
        except (KeyError, ValueError):
            continue

    ratio = Fraction(str(_threshold("rev_inflation_ratio") or 1.5))
    pats = _shell_patterns()
    seen: dict[str, set[str]] = {}
    pred: dict[str, dict[str, bool]] = {}
    for r in rows:
        did = r["deal_id"]
        p: dict[str, bool] = {}
        p["SHELL_ADDRESS"] = any(rx.search((r.get("business_address") or "")[:400]) for rx in pats)
        ein, base = norm_ein(r.get("ein") or ""), name_base(r.get("legal_name"))
        p["DUP_EIN"] = bool(ein and base and any(b != base for b in seen.get(ein, set())))
        if ein and base:
            seen.setdefault(ein, set()).add(base)
        months = sorted(deps.get(did, []))[-6:]
        try:
            stated = float(r.get("annual_revenue_stated") or "nan")
        except ValueError:
            stated = float("nan")
        if len(months) >= 3 and stated == stated:
            annual = sum(v for _, v in months) / len(months) * 12
            p["REV_INFLATION"] = annual > 0 and Fraction(str(stated)) > ratio * Fraction(str(round(annual, 2)))
        else:
            p["REV_INFLATION"] = False
        pred[did] = p

    report: dict[str, dict[str, int]] = {}
    for code in CODES:
        tp = fp = fn = tn = 0
        for r in rows:
            truth = (r.get("fraud_label") or "none") == code
            hit = pred[r["deal_id"]][code]
            tp += truth and hit
            fp += (not truth) and hit
            fn += truth and not hit
            tn += (not truth) and not hit
        report[code] = {"labelled": tp + fn, "caught": tp, "missed": fn, "false_flags": fp, "clean_ok": tn}
    clean = [r for r in rows if (r.get("fraud_label") or "none") == "none"]
    any_fp = sum(1 for r in clean if any(pred[r["deal_id"]].values()))
    report["_clean_deals_flagged_by_any_check"] = {"clean": len(clean), "flagged": any_fp}

    print(f"history deals: {len(rows)}  (rev_inflation_ratio {float(ratio)})")
    print(f"{'check':15} {'labelled':>8} {'caught':>7} {'missed':>7} {'false flags':>12}")
    for code in CODES:
        m = report[code]
        print(f"{code:15} {m['labelled']:>8} {m['caught']:>7} {m['missed']:>7} {m['false_flags']:>12}")
    print(f"clean deals flagged by any check: {any_fp} of {len(clean)}")
    print("STMT_TAMPER, ID_MISMATCH: need documents (not in history); see unit + synthetic tests")
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
