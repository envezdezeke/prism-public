"""History baseline (owner: Phase 5, M13).

Contract: built once per run from whatever history ships with the dataset; thresholds are
recomputed from percentiles in config/fraud.yaml, never stored as bit values.

``build_baseline`` reads ``deals.csv`` (and ``monthly_deposits.csv`` when present) with the
stdlib csv module and NEVER raises for history content: a missing dir or file, a bad date, a
duplicate id or an absent column becomes a warning string on ``Baseline.warnings`` and a
skipped row. Every value is canonicalized (prism.fraud.canon) so spellings of one entity
share one key. Queries take an optional ``exclude`` deal id (leave-one-out) that subtracts
exactly that deal in O(1) via id sets.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from prism.engine.config import FraudConfig
from prism.fraud.canon import canon_value, split_legal_suffix
from prism.fraud.rarity import FraudParams, base_similar, parse_weekdays

# History CSV column -> entity kind. preparer_firm, registered_agent_address, ein, ssn and
# licence columns are intentionally not indexed (see the plan SUMMARY).
HISTORY_COLUMN_KINDS: dict[str, str] = {
    "preparer_name": "preparer",
    "preparer_phone": "phone",
    "registered_agent": "registered_agent",
    "business_address": "address",
    "equipment_vendor": "vendor",
    "vendor_remit_acct_last4": "remit_account",
    "pdf_producer": "pdf_producer",
    "bank_name": "bank",
    "guarantor_name": "guarantor",
}

# The history only carries the application-packet producer.
HISTORY_PRODUCER_CLASS = "application"

CLEAN_REPAYMENTS = ("current", "paid_off")


# ------------------------------------------------------------------ deposit features

@dataclass(frozen=True)
class DepositFeatures:
    n: float  # total deposits across the usable months
    round_mass: float  # sum of (round share * count)
    day_mass: tuple[float, ...]  # 7 weekday masses, Mon..Sun
    months_used: int


def _finite(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _positive(v: Any) -> Optional[float]:
    """A finite positive number from a CSV cell (commas and a leading $ allowed), else None."""
    if isinstance(v, str):
        v = v.replace(",", "").replace("$", "").strip()
    x = _finite(v)
    return x if x is not None and x > 0 else None


def deposit_features(rows: Any) -> Optional[DepositFeatures]:
    """Pool per-month deposit rows into features; None when no row is usable.

    Rows are mappings with ``deposit_count``, ``pct_deposits_multiple_of_100`` and
    ``deposit_weekdays`` (the history column names). A row with an unparseable count or share
    is skipped; an unparseable weekday label still counts toward ``n`` and round mass but adds
    no day mass. A month spreads its count evenly over the days its label names.
    """
    if not isinstance(rows, (list, tuple)):
        return None
    n = 0.0
    round_mass = 0.0
    day_mass = [0.0] * 7
    used = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        count = _finite(row.get("deposit_count"))
        share = _finite(row.get("pct_deposits_multiple_of_100"))
        if count is None or share is None or count < 0:
            continue
        share = min(1.0, max(0.0, share))
        n += count
        round_mass += share * count
        used += 1
        days = parse_weekdays(row.get("deposit_weekdays"))
        if days:
            for d in days:
                day_mass[d] += count / len(days)
    if used == 0 or n <= 0:
        return None
    return DepositFeatures(n=n, round_mass=round_mass, day_mass=tuple(day_mass), months_used=used)


# ------------------------------------------------------------------ profiles and deals

@dataclass(frozen=True)
class DealProfile:
    """Canonical entities and deposit features of one deal, history or current."""

    deal_id: str
    entities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deposits: Optional[DepositFeatures] = None


@dataclass(frozen=True)
class HistoryDeal:
    deal_id: str
    applied: date
    final_status: str
    fraud_label: str
    repayment_status: str
    profile: DealProfile
    amount: Optional[float] = None  # amount_requested (F6)
    revenue: Optional[float] = None  # annual_revenue_stated, None unless positive (F6)

    @property
    def repaid(self) -> bool:
        """Funded, unlabelled and still performing or paid off."""
        return (
            self.final_status == "funded"
            and self.fraud_label == "none"
            and self.repayment_status in CLEAN_REPAYMENTS
        )

    @property
    def clean(self) -> bool:
        """Alias of ``repaid``: the calibration clean set minus the date test."""
        return self.repaid


@dataclass
class Calibration:
    """Startup calibration record (M13): thresholds are computed, then kept here for audit."""

    n_clean: int = 0
    tau: dict[str, Optional[float]] = field(default_factory=dict)
    reference: dict[str, list[float]] = field(default_factory=dict)
    fire_rate_clean: dict[str, float] = field(default_factory=dict)
    percentiles: dict[str, float] = field(default_factory=dict)


# A vendor observation: (deal id, base key, full key, remit account or "").
VendorObs = tuple[str, str, str, str]


@dataclass(frozen=True)
class VendorGroup:
    """A frequent history vendor: its exact-base observations and every similar-base one."""

    base: str
    members: tuple[VendorObs, ...]
    similar: tuple[VendorObs, ...]


@dataclass
class Baseline:
    n_history_deals: int = 0
    deals: dict[str, HistoryDeal] = field(default_factory=dict)
    params: FraudParams = field(default_factory=FraudParams)
    calibration: Calibration = field(default_factory=Calibration)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._build_indexes()

    # ------------------------------------------------------------------ indexes
    def _build_indexes(self) -> None:
        self.n_history_deals = len(self.deals)
        ids: dict[tuple[str, str], set[str]] = {}
        for d in self.deals.values():
            for kind, values in d.profile.entities.items():
                for v in values:
                    ids.setdefault((kind, v), set()).add(d.deal_id)
        self._ids = ids
        self._repaid_ids = {i for i, d in self.deals.items() if d.repaid}
        self._ordered = sorted(self.deals, key=lambda i: (self.deals[i].applied, i))
        self._build_deposit_aggregates()
        self._build_vendor_index()

    def _build_deposit_aggregates(self) -> None:
        self._round_share: dict[str, float] = {}
        self._day_mass: dict[str, tuple[float, ...]] = {}
        pooled = [0.0] * 7
        for i in sorted(self.deals):
            dep = self.deals[i].profile.deposits
            if dep is None or dep.n <= 0:
                continue
            self._round_share[i] = dep.round_mass / dep.n
            self._day_mass[i] = dep.day_mass
            for k in range(7):
                pooled[k] += dep.day_mass[k]
        self._pooled_day_mass = pooled

    def _build_vendor_index(self) -> None:
        obs: list[VendorObs] = []
        for i in sorted(self.deals):
            ents = self.deals[i].profile.entities
            account = (ents.get("remit_account") or ("",))[0]
            for v in ents.get("vendor", ()):
                key = split_legal_suffix(v)
                obs.append((i, key.base, key.full, account))
        self._vendor_obs = obs
        self._vendor_ids = {o[0] for o in obs}
        by_base: dict[str, list[VendorObs]] = defaultdict(list)
        for o in obs:
            by_base[o[1]].append(o)
        p = self.params
        groups: dict[str, VendorGroup] = {}
        for base in sorted(by_base):
            if len(by_base[base]) < p.min_vendor_deals:
                continue
            similar: list[VendorObs] = []
            for other in sorted(by_base):
                if base_similar(other, base, p.min_similarity, p.max_edit_distance):
                    similar.extend(by_base[other])
            groups[base] = VendorGroup(base, tuple(by_base[base]), tuple(similar))
        self._vendor_groups = groups

    # ------------------------------------------------------------------ queries
    def n_effective(self, exclude: Optional[str] = None) -> int:
        return len(self.deals) - (1 if exclude is not None and exclude in self.deals else 0)

    def _members(self, kind: str, value: str, exclude: Optional[str]) -> list[str]:
        members = self._ids.get((kind, value), ())
        return sorted(i for i in members if i != exclude)

    def df(self, kind: str, value: str, exclude: Optional[str] = None) -> int:
        members = self._ids.get((kind, value), ())
        return len(members) - (1 if exclude is not None and exclude in members else 0)

    def deals_with(self, kind: str, value: str, exclude: Optional[str] = None) -> list[str]:
        return self._members(kind, value, exclude)

    def entity_stats(
        self, kind: str, value: str, exclude: Optional[str] = None
    ) -> tuple[int, int, Optional[date], Optional[date]]:
        """(df, repaid count, first seen, last seen) over history deals, minus ``exclude``."""
        members = self._members(kind, value, exclude)
        if not members:
            return 0, 0, None, None
        dates = [self.deals[i].applied for i in members]
        repaid = sum(1 for i in members if i in self._repaid_ids)
        return len(members), repaid, min(dates), max(dates)

    def value_counts(self, kind: str) -> dict[str, int]:
        """Document frequency of every canonical value of ``kind`` in history."""
        return {v: len(s) for (k, v), s in sorted(self._ids.items()) if k == kind}

    def producer_counts(self, doc_class: str) -> dict[str, int]:
        """Producer distribution for a document class (history has only the packet class)."""
        return self.value_counts("pdf_producer") if doc_class == HISTORY_PRODUCER_CLASS else {}

    def ordered_ids(self) -> list[str]:
        """History ids sorted by (application date, id)."""
        return list(self._ordered)

    def vendor_groups(self) -> Mapping[str, VendorGroup]:
        """Frequent history vendors by base key (frequent as built, before any leave-one-out)."""
        return self._vendor_groups

    def n_vendor_obs(self, exclude: Optional[str] = None) -> int:
        """History vendor observations, minus ``exclude``'s own."""
        return len(self._vendor_obs) - (
            sum(1 for o in self._vendor_obs if o[0] == exclude) if exclude in self._vendor_ids else 0
        )

    def terms_ratios(self, exclude: Optional[str] = None) -> list[float]:
        """Sorted amount-to-revenue ratios of the clean (repaid) history deals (F6 reference)."""
        return sorted(
            d.amount / d.revenue for i, d in self.deals.items()
            if i != exclude and d.repaid and d.amount is not None and d.revenue is not None
        )

    def round_shares(self, exclude: Optional[str] = None) -> list[float]:
        """Per-deal round-deposit shares (id order) for deals with deposit data."""
        return [s for i, s in sorted(self._round_share.items()) if i != exclude]

    def pooled_day_mass(self, exclude: Optional[str] = None) -> tuple[float, ...]:
        """Weekday deposit mass pooled over history deals, minus ``exclude``'s own."""
        pooled = list(self._pooled_day_mass)
        own = self._day_mass.get(exclude) if exclude is not None else None
        if own is not None:
            pooled = [p - o for p, o in zip(pooled, own)]
        return tuple(max(0.0, p) for p in pooled)

    # ------------------------------------------------------------------ builders
    @classmethod
    def from_rows(
        cls,
        deal_rows: Iterable[Mapping[str, Any]],
        deposit_rows: Iterable[Mapping[str, Any]] = (),
        params: Optional[FraudParams] = None,
    ) -> "Baseline":
        """Build from parsed CSV rows. Never raises; skips bad rows with a warning each."""
        warnings: list[str] = []
        deals: dict[str, HistoryDeal] = {}
        for n, row in enumerate(deal_rows, start=2):  # row 1 is the header
            deal_id = str(row.get("deal_id") or "").strip()
            if not deal_id:
                warnings.append(f"deals.csv row {n}: missing deal_id, skipped")
                continue
            if deal_id in deals:
                warnings.append(f"deals.csv row {n}: duplicate deal id {deal_id}, first row kept")
                continue
            try:
                applied = date.fromisoformat(str(row.get("application_date") or "").strip())
            except ValueError:
                warnings.append(f"deals.csv row {n}: {deal_id} has an invalid application_date, skipped")
                continue
            entities: dict[str, tuple[str, ...]] = {}
            for col, kind in HISTORY_COLUMN_KINDS.items():
                v = canon_value(kind, row.get(col))
                if v:
                    entities[kind] = (v,)
            deals[deal_id] = HistoryDeal(
                deal_id=deal_id,
                applied=applied,
                final_status=str(row.get("final_status") or "").strip(),
                fraud_label=str(row.get("fraud_label") or "").strip(),
                repayment_status=str(row.get("repayment_status") or "").strip(),
                profile=DealProfile(deal_id=deal_id, entities=entities),
                amount=_positive(row.get("amount_requested")),
                revenue=_positive(row.get("annual_revenue_stated")),
            )
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        unknown: set[str] = set()
        for row in deposit_rows:
            deal_id = str(row.get("deal_id") or "").strip()
            if deal_id in deals:
                grouped[deal_id].append(row)
            else:
                unknown.add(deal_id)
        for deal_id in sorted(unknown):
            warnings.append(f"monthly_deposits.csv: deposit rows for unknown deal id {deal_id or '(blank)'} ignored")
        for deal_id, rows in grouped.items():
            d = deals[deal_id]
            deals[deal_id] = replace(d, profile=DealProfile(d.deal_id, d.profile.entities, deposit_features(rows)))
        return cls(deals=deals, params=params or FraudParams(), warnings=warnings)

    def restricted_to(self, ids: Iterable[str]) -> "Baseline":
        """A fresh Baseline over only ``ids`` (unknown ids ignored) with its own indexes."""
        keep = {i: self.deals[i] for i in sorted(set(ids)) if i in self.deals}
        return Baseline(deals=keep, params=self.params)


def _read_csv(path: Path, warnings: list[str], *, required: bool = True) -> Optional[list[dict[str, str]]]:
    if not path.is_file():
        if required:
            warnings.append(f"{path.name}: not found")
        else:
            warnings.append(f"{path.name}: not found, deposit features unavailable")
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        warnings.append(f"{path.name}: unreadable ({type(e).__name__})")
        return None


def build_baseline(history_dir: Optional[Path], cfg: FraudConfig) -> Baseline:
    """Return the history baseline (``history_dir`` may be None). Never raises on history content."""
    params = FraudParams.from_config(cfg)
    if history_dir is None:
        return Baseline(params=params, warnings=["no history directory: empty baseline"])
    hist = Path(history_dir)
    warnings: list[str] = []
    deal_rows = _read_csv(hist / "deals.csv", warnings)
    deposit_rows = _read_csv(hist / "monthly_deposits.csv", warnings, required=False)
    base = Baseline.from_rows(deal_rows or (), deposit_rows or (), params=params)
    base.warnings[:0] = warnings
    # Lazy import: calibrate imports signals imports baseline, so a top-level import is circular.
    from prism.fraud.calibrate import compute_thresholds

    base.calibration = compute_thresholds(base, params)
    return base
