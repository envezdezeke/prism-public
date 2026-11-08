"""Cached index of ``history/deals.csv`` for the relationship and vendor checks (D-09, D-15).

Read-only, team data but treated as untrusted: the csv module inside try/except, a missing directory,
file or column gives an empty or partial index, never an exception. The index is cached by
``(path, mtime_ns)`` so a run parses the file once. Keys use ``prism.fraud.canon`` so Read and Fraud
agree on what "the same vendor" means.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from prism.fraud.canon import canon_value, split_legal_suffix
from prism.fraud.rarity import base_similar
from prism.read.normalize import norm_ein
from prism.read.settings import get_config

COLUMNS = ("deal_id", "ein", "final_status", "fraud_label", "repayment_status", "relationship_since",
           "equipment_vendor", "vendor_remit_acct_last4", "application_date")
_MAX_ROWS = 200_000
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


@dataclass(frozen=True)
class HistoryIndex:
    n_rows: int = 0
    by_ein: dict[str, tuple[dict[str, str], ...]] = field(default_factory=dict)
    by_vendor: dict[str, tuple[dict[str, str], ...]] = field(default_factory=dict)  # canon vendor full key
    vendor_bases: tuple[str, ...] = ()  # distinct canonical vendor bases, sorted

    @property
    def loaded(self) -> bool:
        return self.n_rows > 0


EMPTY = HistoryIndex()
_CACHE: dict[tuple[str, int], HistoryIndex] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _build(path: Path) -> HistoryIndex:
    rows: list[dict[str, str]] = []
    with open(path, encoding="utf-8", newline="") as f:
        for i, raw in enumerate(csv.DictReader(f)):
            if i >= _MAX_ROWS:
                break
            try:
                rows.append({c: str(raw.get(c) or "").strip() for c in COLUMNS})
            except Exception:  # noqa: BLE001
                continue
    by_ein: dict[str, list[dict[str, str]]] = {}
    by_vendor: dict[str, list[dict[str, str]]] = {}
    bases: set[str] = set()
    for r in rows:
        ein = norm_ein(r["ein"]) or r["ein"]
        if ein:
            by_ein.setdefault(ein, []).append(r)
        if r["equipment_vendor"]:
            key = canon_value("vendor", r["equipment_vendor"])
            if key:
                by_vendor.setdefault(key, []).append(r)
                bases.add(split_legal_suffix(r["equipment_vendor"]).base)
    return HistoryIndex(
        n_rows=len(rows),
        by_ein={k: tuple(v) for k, v in by_ein.items()},
        by_vendor={k: tuple(v) for k, v in by_vendor.items()},
        vendor_bases=tuple(sorted(b for b in bases if b)),
    )


def load_history(rules: Any) -> HistoryIndex:
    """The history index for ``rules.history_dir``, or an empty index. Never raises."""
    try:
        hist_dir = getattr(rules, "history_dir", None)
        if not hist_dir:
            return EMPTY
        path = Path(hist_dir) / "deals.csv"
        key = (str(path), path.stat().st_mtime_ns)
        if key not in _CACHE:
            _CACHE.clear()
            _CACHE[key] = _build(path)
        return _CACHE[key]
    except Exception:  # noqa: BLE001
        return EMPTY


# ---------------------------------------------------------------- relationship and vendor checks

def _cfg() -> dict[str, Any]:
    return get_config().raw.get("features") or {}


def relationship(index: HistoryIndex, eins: list[str]) -> Optional[dict[str, Any]]:
    """``{status, prior_loans, since_year?}`` from the history rows of these EINs, or None when no history is loaded.

    No matching row is a ``new`` borrower. Past-due or charged-off wins over clean."""
    if not index.loaded:
        return None
    seen: dict[str, dict[str, str]] = {}
    for ein in eins:
        for r in index.by_ein.get(norm_ein(ein) or ein, ()):
            seen.setdefault(r["deal_id"] or repr(sorted(r.items())), r)
    rows = list(seen.values())
    cfg = _cfg()
    clean = set(cfg.get("clean_repayment") or ())
    bad = set(cfg.get("past_due_repayment") or ())
    status = "new"
    if any(r["repayment_status"] in bad for r in rows):
        status = "returning_past_due"
    elif any(r["final_status"] == "funded" and r["repayment_status"] in clean and r["fraud_label"].casefold() == "none"
             for r in rows):
        status = "returning_clean"
    out: dict[str, Any] = {"status": status, "prior_loans": len(rows)}
    years = [int(m.group(1)) for r in rows for m in [_YEAR_RE.search(r["relationship_since"])] if m]
    if years and status != "new":
        out["since_year"] = min(years)
    return out


def vendor_check(index: HistoryIndex, vendor: Optional[str], remit_last4: Optional[str]) -> tuple[Optional[bool], int]:
    """``(verified, past_loans)`` for a quote vendor (D-15).

    True: the vendor's canonical key is in history with this remit last four. False: a lookalike (same base,
    different suffix, or a similar base) or a known vendor with another remit. None: not in history (absent).
    ``past_loans`` counts history deals with the same canonical vendor and remit last four."""
    if not index.loaded or not vendor:
        return None, 0
    key = canon_value("vendor", vendor)
    if not key:
        return None, 0
    last4 = (remit_last4 or "").strip()
    exact = index.by_vendor.get(key, ())
    past = sum(1 for r in exact if last4 and r["vendor_remit_acct_last4"] == last4)
    if exact:
        return (True if past else False), past
    base = split_legal_suffix(vendor).base
    p = _cfg().get("vendor_lookalike") or {}
    min_sim, max_edit = float(p.get("min_similarity", 0.8)), int(p.get("max_edit_distance", 2))
    for hbase in index.vendor_bases:
        if hbase == base or base_similar(base, hbase, min_sim, max_edit):
            return False, 0
    return None, 0
