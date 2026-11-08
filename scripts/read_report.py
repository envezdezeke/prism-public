"""Tuning-only diagnostic of what Read produces per deal: classes, entity kinds, profile keys.

    PRISM_SAMPLE_DATA=<participant dir> python scripts/read_report.py

Excludes every deal named in eval/holdout.json and never prints deal names (deals are numbered), OCR text
or personal identifiers: only class names, entity kinds with counts and profile key names (D-02).
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism.engine.config import load_rules  # noqa: E402
from prism.engine.state import DealState, Doc  # noqa: E402
from prism.read import docmodel  # noqa: E402
from prism.read.classify import classify  # noqa: E402
from prism.read.entities import entity_kinds  # noqa: E402
from prism.read.extract import extract_entities, extract_facts, extract_features  # noqa: E402

PROFILE_KEYS = (
    "dba", "formation_date", "application_date", "preparer", "use_of_proceeds", "collateral", "ownership",
    "statement_months", "tax_doc", "net_income_fy_cents", "net_income_ytd_cents", "ytd_months",
    "deposits_annualized_cents", "cash_cents", "receivables_cents", "liabilities_cents", "relationship",
    "quote", "vendor_verified", "vendor_past_loans", "disbursement", "mismatch_evidence",
)
# Absent on a tuning deal for a good reason, or filled only after the statement parser (03-03) is merged.
EXPECTED_ABSENT_OK = {"dba", "quote", "vendor_verified", "vendor_past_loans", "mismatch_evidence", "collateral"}
STATEMENT_KEYS = {"statement_months", "deposits_annualized_cents"}
TZ = timezone(timedelta(hours=-6))


def tuning_deals(data_root: Path) -> list[Path]:
    import json

    hold = set(json.loads((ROOT / "eval" / "holdout.json").read_text(encoding="utf-8"))["holdout"])
    return sorted(p for p in (data_root / "shared_folder").iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name not in hold)


def main() -> int:
    env = os.environ.get("PRISM_SAMPLE_DATA")
    data_root = Path(env) if env else ROOT / "data" / "participant"
    if not (data_root / "shared_folder").is_dir():
        print("no sample data (set PRISM_SAMPLE_DATA)")
        return 1
    rules = load_rules(data_root)
    absent_total: Counter[str] = Counter()
    deals = tuning_deals(data_root)
    for n, folder in enumerate(deals, start=1):
        docs = []
        for i, p in enumerate(sorted(folder.iterdir())):
            if p.is_file():
                d = Doc(path=f"{folder.name}/{p.name}", name=p.name, arrive_at=datetime(2026, 11, 2, tzinfo=TZ)
                        + timedelta(minutes=i), abs_path=p)
                classify(d)
                docs.append(d)
        state = DealState(folder=folder.name, docs=docs)
        state.facts.update(extract_facts(state, rules))
        entities = extract_entities(state, rules)
        feats = extract_features(state, rules)
        profile = feats.get("profile") or {}
        classes = Counter(d.doc_class for d in docs)
        absent = [k for k in PROFILE_KEYS if k not in profile]
        absent_total.update(absent)
        print(f"deal {n:2d}: classes {dict(sorted(classes.items()))}")
        print(f"         entities {entity_kinds(entities)}")
        print(f"         profile {len(profile)}/{len(PROFILE_KEYS)} present; absent {absent}")
        print(f"         deposit_months {len(feats.get('deposit_months') or [])} rows; facts {len(state.facts)}/9")
    print()
    print(f"profile keys absent per deal count (of {len(deals)} tuning deals):")
    for k, c in sorted(absent_total.items()):
        note = " (statement parser, 03-03)" if k in STATEMENT_KEYS else (" (n/a for most deals)" if k in EXPECTED_ABSENT_OK else "")
        print(f"  {k}: {c}{note}")
    docmodel.clear_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
