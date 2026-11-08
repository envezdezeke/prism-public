"""Pure rarity math in bits of surprise (owner: Phase 5; M1, M7).

M1: every signal is ``bits = -log2 P(observation | clean history)`` with +1 smoothing, so
nothing is ever zero: ``p_hat(v) = (df + 1) / (N + 1)``.
M7: an entity is strong iff ``df / N < rho`` (2% in config) and it links at least two deals;
the pair score of two deals sums ``w_type * omega_v * I(v)`` over their shared strong
entities, with ``omega_v = 1 - beta * (repaid + 1) / (df + 2)`` discounting values whose
history deals repaid.

No I/O and no Baseline import: ``FraudParams`` is built once from ``FraudConfig.raw``.
"""
from __future__ import annotations

import difflib
import functools
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from prism.engine.config import ConfigError, FraudConfig

DEFAULT_TYPE_WEIGHT = 1.0


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class FraudParams:
    rho: float = 0.02
    min_linked: int = 2
    type_weights: dict[str, float] = field(default_factory=dict)
    default_type_weight: float = DEFAULT_TYPE_WEIGHT
    outcome_beta: float = 0.0
    percentiles: dict[str, float] = field(default_factory=dict)
    train_end: date = date.max
    test_start: date = date.min
    test_end: date = date.max
    min_similarity: float = 0.8  # F3 token-set similarity floor
    max_edit_distance: int = 2  # F3 typo tolerance
    min_vendor_deals: int = 3  # F3: a "frequent" history vendor has at least this many deals
    dirichlet_alpha: float = 0.5  # F5 weekday smoothing
    # history_flags.link_classes: (entity kind, link class id) pairs that merge kinds into one
    # independent link class (M18). Empty means every kind is its own class.
    link_classes: tuple[tuple[str, str], ...] = ()
    burst_window_days: int = 7  # F2: partners opened within this many days count
    burst_min_partners: int = 1  # F2 fires at this many burst partners
    f4_third_party_classes: tuple[str, ...] = ("bank_statement",)
    f4_borrower_classes: tuple[str, ...] = (
        "application", "request_letter", "equipment_quote", "financial_statements",
    )
    f4_min_docs: int = 3  # F4: at least this many matching statements
    f4_min_share: float = 0.8  # ... and at least this share of the usable statements
    f6_tail_percentile: float = 0.95  # F6: ratio at or above this percentile of clean history

    def weight(self, kind: str) -> float:
        return self.type_weights.get(kind, self.default_type_weight)

    @classmethod
    def from_config(cls, cfg: Optional[FraudConfig]) -> "FraudParams":
        """Read typed fields plus the additive keys in ``cfg.raw`` (defaults when absent)."""
        if cfg is None:
            return cls()
        fams = (cfg.raw or {}).get("families") or {}
        f1 = fams.get("F1") or {}

        weights_raw = f1.get("type_weights") or {}
        if not isinstance(weights_raw, Mapping):
            raise ConfigError("fraud.yaml: F1.type_weights must be a mapping")
        weights: dict[str, float] = {}
        for kind, w in weights_raw.items():
            if not _num(w) or w < 0:
                raise ConfigError(f"fraud.yaml: F1.type_weights.{kind} must be a number >= 0")
            weights[str(kind)] = float(w)
        beta = f1.get("outcome_beta", 0.0)
        if not _num(beta) or not 0 <= beta <= 1:
            raise ConfigError("fraud.yaml: F1.outcome_beta must be a number in [0, 1]")
        f3 = fams.get("F3") or {}
        min_sim = f3.get("min_similarity", 0.8)
        if not _num(min_sim) or not 0 < min_sim <= 1:
            raise ConfigError("fraud.yaml: F3.min_similarity must be a number in (0, 1]")
        max_edit = f3.get("max_edit_distance", 2)
        if isinstance(max_edit, bool) or not isinstance(max_edit, int) or max_edit < 0:
            raise ConfigError("fraud.yaml: F3.max_edit_distance must be an int >= 0")
        min_vendor = f3.get("min_vendor_deals", 3)
        if isinstance(min_vendor, bool) or not isinstance(min_vendor, int) or min_vendor < 1:
            raise ConfigError("fraud.yaml: F3.min_vendor_deals must be an int >= 1")
        alpha = (fams.get("F5") or {}).get("dirichlet_alpha", 0.5)
        if not _num(alpha) or alpha <= 0:
            raise ConfigError("fraud.yaml: F5.dirichlet_alpha must be a number > 0")
        f2 = fams.get("F2") or {}
        window = f2.get("burst_window_days", 7)
        if isinstance(window, bool) or not isinstance(window, int) or window < 0:
            raise ConfigError("fraud.yaml: F2.burst_window_days must be an int >= 0")
        min_partners = f2.get("burst_min_partners", 1)
        if isinstance(min_partners, bool) or not isinstance(min_partners, int) or min_partners < 1:
            raise ConfigError("fraud.yaml: F2.burst_min_partners must be an int >= 1")
        f4 = fams.get("F4") or {}
        third = _str_tuple(f4.get("third_party_classes", ["bank_statement"]), "F4.third_party_classes")
        borrower = _str_tuple(
            f4.get("borrower_classes", ["application", "request_letter", "equipment_quote", "financial_statements"]),
            "F4.borrower_classes",
        )
        min_docs = f4.get("min_docs", 3)
        if isinstance(min_docs, bool) or not isinstance(min_docs, int) or min_docs < 1:
            raise ConfigError("fraud.yaml: F4.min_docs must be an int >= 1")
        min_share = f4.get("min_share", 0.8)
        if not _num(min_share) or not 0 < min_share <= 1:
            raise ConfigError("fraud.yaml: F4.min_share must be a number in (0, 1]")
        tail = (fams.get("F6") or {}).get("tail_percentile", 0.95)
        if not _num(tail) or not 0 < tail < 1:
            raise ConfigError("fraud.yaml: F6.tail_percentile must be a number in (0, 1)")
        link_classes = _parse_link_classes(((cfg.raw or {}).get("history_flags") or {}).get("link_classes"))
        return cls(
            rho=cfg.f1_rarity_fraction,
            min_linked=cfg.f1_min_linked_deals,
            type_weights=weights,
            outcome_beta=float(beta),
            percentiles=dict(cfg.percentiles),
            train_end=cfg.train_end,
            test_start=cfg.test_start,
            test_end=cfg.test_end,
            min_similarity=float(min_sim),
            max_edit_distance=max_edit,
            min_vendor_deals=min_vendor,
            dirichlet_alpha=float(alpha),
            link_classes=link_classes,
            burst_window_days=window,
            burst_min_partners=min_partners,
            f4_third_party_classes=third,
            f4_borrower_classes=borrower,
            f4_min_docs=min_docs,
            f4_min_share=float(min_share),
            f6_tail_percentile=float(tail),
        )


def _str_tuple(v: Any, name: str) -> tuple[str, ...]:
    if not isinstance(v, (list, tuple)) or not v or not all(isinstance(x, str) and x for x in v):
        raise ConfigError(f"fraud.yaml: {name} must be a non-empty list of strings")
    return tuple(v)


def _parse_link_classes(raw: Any) -> tuple[tuple[str, str], ...]:
    """``history_flags.link_classes``: mapping of class id to a non-empty list of entity kinds."""
    if raw is None:
        return ()
    from typing import get_args

    from prism.contract import EntityKind

    kinds = set(get_args(EntityKind))
    if not isinstance(raw, Mapping):
        raise ConfigError("fraud.yaml: history_flags.link_classes must be a mapping")
    out: dict[str, str] = {}
    for cls_id, members in raw.items():
        if not isinstance(cls_id, str) or not cls_id:
            raise ConfigError("fraud.yaml: history_flags.link_classes keys must be non-empty strings")
        if not isinstance(members, (list, tuple)) or not members:
            raise ConfigError(f"fraud.yaml: history_flags.link_classes.{cls_id} must be a non-empty list")
        for k in members:
            if k not in kinds:
                raise ConfigError(f"fraud.yaml: history_flags.link_classes.{cls_id}: unknown entity kind {k!r}")
            if k in out:
                raise ConfigError(f"fraud.yaml: history_flags.link_classes: kind {k} listed twice")
            out[k] = cls_id
    return tuple(sorted(out.items()))


def p_hat(df: int, n: int) -> float:
    """Smoothed history frequency of a value used by ``df`` of ``n`` deals."""
    return (df + 1) / (n + 1)


def surprisal(df: int, n: int) -> float:
    """``I(v) = -log2 p_hat(v)`` in bits (0.0 when there is no history to judge against)."""
    if n <= 0:
        return 0.0
    bits = -math.log2(min(1.0, p_hat(df, n)))
    return bits if bits > 0 else 0.0


def is_strong(df: int, n: int, links: int, rho: float, min_linked: int) -> bool:
    """Strong entity: rare in history (``df / n < rho``) and links at least ``min_linked`` deals."""
    if n <= 0:
        return False
    return df / n < rho and links >= min_linked


def outcome_discount(repaid: int, df: int, beta: float) -> float:
    """``omega_v = 1 - beta * (repaid + 1) / (df + 2)``: shared values that repaid count less."""
    return 1.0 - beta * (repaid + 1) / (df + 2)


# ------------------------------------------------------------------ percentiles and tails

def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile (rank ``q * (n - 1)``); None for an empty sample."""
    xs = sorted(values)
    if not xs:
        return None
    rank = q * (len(xs) - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def upper_tail_bits(z: float, sample: Sequence[float]) -> float:
    """M7 smoothed empirical upper tail: ``-log2((1 + #{s >= z}) / (1 + len(sample)))``."""
    if not sample:
        return 0.0
    above = sum(1 for s in sample if s >= z)
    bits = -math.log2((1 + above) / (1 + len(sample)))
    return bits if bits > 0 else 0.0


# ------------------------------------------------------------------ F5 deposit statistics

# Prior strength (a + b) used when the method-of-moments fit is degenerate. Documented
# constant, not a bit threshold: it only says how many deposits the fallback prior is worth.
PRIOR_STRENGTH = 10.0
_SHARE_EPS = 0.01


def fit_beta_mom(shares: Sequence[float]) -> tuple[float, float]:
    """Beta(a, b) prior on the round share by method of moments.

    ``a + b = m (1 - m) / var - 1``. Fewer than two shares, zero variance, or a variance at
    or above ``m (1 - m)`` fall back to ``PRIOR_STRENGTH`` around the observed mean (0.5 when
    there are no shares). a and b are always strictly positive.
    """
    n = len(shares)
    mean = sum(shares) / n if n else 0.5
    mean = min(1.0 - _SHARE_EPS, max(_SHARE_EPS, mean))
    if n >= 2:
        raw_mean = sum(shares) / n
        var = sum((s - raw_mean) ** 2 for s in shares) / (n - 1)
        limit = raw_mean * (1.0 - raw_mean)
        if var > 0 and var < limit:
            total = limit / var - 1.0
            if total > 0:
                return raw_mean * total, (1.0 - raw_mean) * total
    return mean * PRIOR_STRENGTH, (1.0 - mean) * PRIOR_STRENGTH


def shrunk_share(r: float, n: float, a: float, b: float) -> float:
    """Beta-binomial posterior mean ``(r + a) / (n + a + b)`` of the round share."""
    return (r + a) / (n + a + b)


def entropy_bits(q: Sequence[float]) -> float:
    """Shannon entropy in bits."""
    h = -sum(p * math.log2(p) for p in q if p > 0)
    return h if h > 0 else 0.0


def kl_bits(q: Sequence[float], p0: Sequence[float]) -> float:
    """KL divergence of ``q`` against ``p0`` in bits."""
    d = sum(p * math.log2(p / max(r, 1e-12)) for p, r in zip(q, p0) if p > 0)
    return d if d > 0 else 0.0


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_ALIASES = {
    **{d: i for i, d in enumerate(_DAYS)},
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5,
    "sunday": 6, "tues": 1, "thur": 3, "thurs": 3,
}


def parse_weekdays(label: Any) -> Optional[tuple[int, ...]]:
    """Days (Mon=0 .. Sun=6) named by a label such as ``Mon-Fri``, ``Mon,Thu`` or ``all days``."""
    if not isinstance(label, str):
        return None
    text = label.strip().casefold().replace("–", "-").replace("—", "-")
    if not text:
        return None
    if text in ("all days", "all", "daily", "every day", "everyday", "7 days"):
        return tuple(range(7))
    days: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo, _, hi = part.partition("-")
            a, b = _DAY_ALIASES.get(lo.strip()), _DAY_ALIASES.get(hi.strip())
            if a is None or b is None:
                return None
            i = a
            days.add(i)
            while i != b:
                i = (i + 1) % 7
                days.add(i)
        else:
            d = _DAY_ALIASES.get(part)
            if d is None:
                return None
            days.add(d)
    return tuple(sorted(days))


# ------------------------------------------------------------------ F3 similarity

def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def token_set_similarity(a: str, b: str) -> float:
    """difflib ratio of the sorted-unique-token strings (word order ignored; a subset is not 1.0)."""
    ka = " ".join(sorted(set(a.split())))
    kb = " ".join(sorted(set(b.split())))
    return difflib.SequenceMatcher(None, ka, kb).ratio()


@functools.lru_cache(maxsize=65536)
def base_similar(a: str, b: str, min_sim: float, max_edit: int) -> bool:
    """Same or lookalike base name: token-set similarity >= ``min_sim`` or edit distance <= ``max_edit``."""
    if a == b:
        return True
    return token_set_similarity(a, b) >= min_sim or edit_distance(a, b) <= max_edit
