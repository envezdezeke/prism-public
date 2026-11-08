"""Startup calibration of the F3 and F5 thresholds (owner: Phase 5; M7, M13).

The clean calibration set C is funded, ``fraud_label`` none, repayment current or paid_off,
whatever its date (fallback: every label-none deal when fewer than MIN_CLEAN). Every deal in C is scored leave-one-out against the
rest of the baseline. ``tau_f`` is the linearly interpolated percentile (from config) of the
bits ``B_f`` over C. Config stores only the percentile; the computed tau lands on
``Baseline.calibration`` for audit and is never a stored bit value.

Pass 1 collects the F5 statistics of every clean deal with deposit data and builds the sorted
reference samples; pass 2 converts each deal's statistics to bits through the smoothed upper
tail on that reference and takes the maximum as ``B_5``. F1 has no percentile (it is
mandatory for a hold), so only its misfire rate on C is recorded.
"""
from __future__ import annotations

import math
from typing import Optional

from prism.fraud.baseline import Baseline, Calibration
from prism.fraud.rarity import FraudParams, percentile, upper_tail_bits
from prism.fraud.signals import F5_STATS, f5_statistics, score_f1, score_f3


# A clean set smaller than this is too thin to calibrate on, so the label-none deals of any
# repayment status stand in. A count of deals, not a bit threshold.
MIN_CLEAN = 20


def clean_ids(baseline: Baseline, params: FraudParams) -> list[str]:
    """Clean calibration deals sorted by (applied, id), whatever their dates.

    Thresholds are relative to the history that ships, never to dates in config: a history
    dated outside the config window must not switch F3 and F5 off (red-team trick R4).
    Measurement code that needs a time split builds its Baseline on the train deals only.
    ``params`` is kept for signature stability.
    """
    ids = baseline.ordered_ids()
    clean = [i for i in ids if baseline.deals[i].clean]
    if len(clean) >= MIN_CLEAN:
        return clean
    return [i for i in ids if baseline.deals[i].fraud_label == "none"]


def _tau(values: list[float], params: FraudParams, family: str) -> Optional[float]:
    q = params.percentiles.get(family)
    if q is None:
        return None
    empirical = percentile(values, q)
    if empirical is None:
        return None
    return max(empirical, floor_bits(q))


def floor_bits(q: float) -> float:
    """``-log2(1 - q)``: the surprise a q-tail event carries by definition.

    With a thin or mostly-zero clean sample (a small history without lookalikes) the empirical
    percentile collapses to 0 and any deal scoring a single bit would fire. The empirical
    percentile can only raise tau above this floor.
    """
    return -math.log2(1.0 - q)


def compute_thresholds(baseline: Baseline, params: FraudParams) -> Calibration:
    """Return the Calibration (tau, reference samples, clean-set fire rates) for ``baseline``."""
    ids = clean_ids(baseline, params)
    n_clean = len(ids)

    f1_fired = 0
    f3_bits: list[float] = []
    f5_stats: list[dict[str, float]] = []
    for i in ids:
        profile = baseline.deals[i].profile
        if score_f1(profile, baseline, params, exclude=i) is not None:
            f1_fired += 1
        f3 = score_f3(profile, baseline, params, exclude=i)
        f3_bits.append(f3.bits if f3 is not None else 0.0)
        stats = f5_statistics(profile, baseline, params, exclude=i)
        if stats is not None:
            f5_stats.append(stats)

    reference = {s: sorted(st[s] for st in f5_stats) for s in F5_STATS} if f5_stats else {}
    f5_bits = [max(upper_tail_bits(st[s], reference[s]) for s in F5_STATS) for st in f5_stats]

    tau: dict[str, Optional[float]] = {"F3": _tau(f3_bits, params, "F3"), "F5": _tau(f5_bits, params, "F5")}

    def rate(bits: list[float], t: Optional[float]) -> float:
        if not n_clean:
            return 0.0
        return sum(1 for b in bits if b > 0 and t is not None and b >= t) / n_clean

    return Calibration(
        n_clean=n_clean,
        tau=tau,
        reference=reference,
        fire_rate_clean={
            "F1": f1_fired / n_clean if n_clean else 0.0,
            "F3": rate(f3_bits, tau["F3"]),
            "F5": rate(f5_bits, tau["F5"]),
        },
        percentiles=dict(params.percentiles),
    )
