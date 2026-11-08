"""Synthetic history builders for the fraud tests (helpers only, no test functions).

Import from other test modules as ``from test_fraud_synth import ...`` (tests/ has no
``__init__.py``). Everything is fake: made-up names, 555-01xx phones, ``X-`` ids that cannot
be mistaken for dataset ids, no ``random`` (arithmetic patterns only). Nothing from data/.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Optional

DEAL_COLUMNS = [
    "deal_id", "application_date", "legal_name", "ein", "industry", "naics", "formation_date",
    "business_address", "registered_agent", "registered_agent_address", "guarantor_name",
    "guarantor_dob", "guarantor_ssn_last4", "guarantor_dl_number", "preparer_name",
    "preparer_firm", "preparer_phone", "bank_name", "amount_requested", "product", "purpose",
    "term_months", "annual_revenue_stated", "equipment_vendor", "vendor_remit_acct_last4",
    "pdf_producer", "final_status", "fraud_label", "repayment_status", "relationship_since",
    "prior_note",
]
DEPOSIT_COLUMNS = [
    "deal_id", "month", "total_deposits", "deposit_count", "pct_deposits_multiple_of_100",
    "deposit_weekdays", "largest_deposit", "month_end_balance",
]

BASE_ORDINAL = date(2026, 1, 1).toordinal()


def iso_day(offset: int) -> str:
    """ISO date ``offset`` days after the synthetic epoch."""
    return date.fromordinal(BASE_ORDINAL + offset).isoformat()


def deal_row(deal_id: str, applied: Optional[str] = None, **fields: str) -> dict[str, str]:
    """A deals.csv row: funded, clean, current by default; every other column blank."""
    row = {c: "" for c in DEAL_COLUMNS}
    row.update(
        deal_id=deal_id,
        application_date=applied if applied is not None else iso_day(0),
        final_status="funded",
        fraud_label="none",
        repayment_status="current",
    )
    row.update({k: str(v) for k, v in fields.items()})
    return row


def deposit_rows(deal_id: str, months: Iterable[tuple[int, float, str]]) -> list[dict[str, str]]:
    """monthly_deposits.csv rows from (count, round_share, weekday_label) per month."""
    rows = []
    for i, (count, share, label) in enumerate(months):
        r = {c: "" for c in DEPOSIT_COLUMNS}
        r.update(
            deal_id=deal_id, month=f"2026-{i + 1:02d}", deposit_count=str(count),
            pct_deposits_multiple_of_100=str(share), deposit_weekdays=label,
        )
        rows.append(r)
    return rows


def write_history(
    tmp_path: Path,
    deals: Iterable[Mapping[str, str]],
    deposits: Optional[Iterable[Mapping[str, str]]] = None,
) -> Path:
    """Write ``<tmp_path>/history/deals.csv`` (and monthly_deposits.csv when given)."""
    hist = Path(tmp_path) / "history"
    hist.mkdir(parents=True, exist_ok=True)

    def dump(name: str, cols: list[str], rows: Iterable[Mapping[str, str]]) -> None:
        with open(hist / name, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    dump("deals.csv", DEAL_COLUMNS, deals)
    if deposits is not None:
        dump("monthly_deposits.csv", DEPOSIT_COLUMNS, deposits)
    return hist


def filler_deals(n: int, *, prefix: str = "X", start: int = 0, **shared: str) -> list[dict[str, str]]:
    """n deals with unique names, phones, agents, addresses and preparers (nothing shared).

    ``shared`` overrides apply to every deal (e.g. a common preparer).
    """
    out = []
    for i in range(start, start + n):
        fields = dict(
            legal_name=f"Filler Business {i}",
            business_address=f"{100 + i} Synthetic Way",
            registered_agent=f"Agent Number {i}",
            guarantor_name=f"Guarantor Person {i}",
            preparer_name=f"Preparer Unique {i}",
            preparer_phone=f"210-555-{100 + i:04d}",
            bank_name=f"Bank Unique {i}",
            pdf_producer=f"Producer Unique {i}",
        )
        fields.update(shared)
        out.append(deal_row(f"{prefix}-{i:03d}", iso_day(i), **fields))
    return out
