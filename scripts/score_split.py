"""Score a submission on the tuning deals only (D-02 holdout split).

Default output: the tuning per-deal table and ONE tuning summary line (the number compared before
and after a change, D-18). ``--holdout-summary`` additionally prints ONE aggregate holdout line.
There is deliberately no option that prints holdout rows or holdout deal names.

    python scripts/score_split.py out/submission.json [--fields legal_name,ein] [--holdout-summary]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism.engine.score import format_table, score_submission  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split_gold(gold: Mapping[str, Any], holdout: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(tuning gold, holdout gold): same top-level keys, deals partitioned by the holdout names."""
    hold = set(holdout)
    deals = gold.get("deals") or {}
    tune = {**gold, "deals": {k: v for k, v in deals.items() if k not in hold}}
    held = {**gold, "deals": {k: v for k, v in deals.items() if k in hold}}
    return tune, held


def restrict_fields(result: dict[str, Any], fields: Optional[Sequence[str]]) -> dict[str, Any]:
    """Count only ``fields`` among the extracted facts (totals recomputed)."""
    if not fields:
        return result
    keep = set(fields)
    for d in result["deals"].values():
        d["extracted"] = {k: v for k, v in d["extracted"].items() if k in keep}
    cells = [c["ok"] for d in result["deals"].values() for c in d["extracted"].values()]
    result["totals"]["extracted"] = {"ok": sum(cells), "total": len(cells)}
    return result


def score_tuning(sub: Mapping[str, Any], gold: Mapping[str, Any], holdout: Sequence[str],
                 fields: Optional[Sequence[str]] = None) -> dict[str, Any]:
    tune, _ = split_gold(gold, holdout)
    return restrict_fields(score_submission(sub, tune), fields)


def _issue_exact(result: Mapping[str, Any]) -> tuple[int, int]:
    ds = list(result["deals"].values())
    return sum(1 for d in ds if d["issues"]["ok"]), len(ds)


def tuning_summary(result: Mapping[str, Any]) -> str:
    t = result["totals"]
    ok, n = _issue_exact(result)
    return (
        f"TUNING facts {t['extracted']['ok']}/{t['extracted']['total']} "
        f"issues_exact {ok}/{n} tp{t['issues']['tp']} fn{t['issues']['fn']} fp{t['issues']['fp']} "
        f"route {t['routing']['ok']}/{t['routing']['total']} "
        f"stage {t['final_stage']['ok']}/{t['final_stage']['total']} "
        f"flag {t['fraud_flagged']['ok']}/{t['fraud_flagged']['total']} "
        f"held {t['fraud_held']['ok']}/{t['fraud_held']['total']} "
        f"links {t['fraud_linked_deals']['ok']}/{t['fraud_linked_deals']['total']}"
    )


def holdout_summary(sub: Mapping[str, Any], gold: Mapping[str, Any], holdout: Sequence[str],
                    fields: Optional[Sequence[str]] = None) -> str:
    _, held = split_gold(gold, holdout)
    r = restrict_fields(score_submission(sub, held), fields)
    t = r["totals"]
    ok, n = _issue_exact(r)
    return (f"HOLDOUT facts {t['extracted']['ok']}/{t['extracted']['total']} "
            f"issues_exact {ok}/{n} fp{t['issues']['fp']} fn{t['issues']['fn']}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("submission", type=Path)
    ap.add_argument("--gold", type=Path, default=ROOT / "eval" / "gold.json")
    ap.add_argument("--holdout", type=Path, default=ROOT / "eval" / "holdout.json")
    ap.add_argument("--fields", default="", help="comma list of extracted fields to count")
    ap.add_argument("--holdout-summary", action="store_true",
                    help="also print ONE aggregate holdout line (03-04 Task 1 and the final gate only)")
    a = ap.parse_args(argv)
    fields = [f.strip() for f in a.fields.split(",") if f.strip()] or None
    gold, sub = _load(a.gold), _load(a.submission)
    hold = list(_load(a.holdout)["holdout"])
    res = score_tuning(sub, gold, hold, fields)
    print(format_table(res))
    print(tuning_summary(res))
    if a.holdout_summary:
        print(holdout_summary(sub, gold, hold, fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
