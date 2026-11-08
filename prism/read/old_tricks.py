"""The five OLD_TRICK checks, exactly as rules/known_fraud_checks.md defines them (READ-08).

Each check returns a boolean; the ``*_reasons`` helpers return short reason codes for logging and tests
(month labels and rule names only, never document text). A check that cannot be run on trusted input
(incomplete, unverified or unparsed documents) raises nothing: a false alarm costs more than a miss here.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

from prism.read import docmodel, requirements, statements
from prism.read.docmodel import ParsedApplication, ParsedStatement, ParsedTax
from prism.read.normalize import name_base, norm_ein

log = logging.getLogger("prism")


def _threshold(name: str) -> Any:
    return (requirements.get_issues_config().raw.get("thresholds") or {}).get(name)


def _month_index(period: str) -> Optional[int]:
    try:
        y, m = period.split("-")
        return int(y) * 12 + int(m) - 1
    except (ValueError, AttributeError):
        return None


def latest_complete_statements(state: Any) -> dict[str, ParsedStatement]:
    """Per month, the latest-arrived COMPLETE copy (parsed). Incomplete copies and unknown periods are left out."""
    out: dict[str, ParsedStatement] = {}
    for doc, ps in docmodel.parsed_all(state, "bank_statement"):  # sorted by arrival, so later copies win
        if not isinstance(ps, ParsedStatement):
            continue
        period = doc.period or ps.period
        if period and _month_index(period) is not None and statements.is_complete(ps):
            out[period] = ps
    return out


# ---------------------------------------------------------------- STMT_TAMPER

def stmt_tamper_reasons(state: Any) -> list[str]:
    """Why the statements look edited: footing failures per month, opening versus prior closing per pair."""
    months = latest_complete_statements(state)
    reasons: list[str] = []
    for period in sorted(months):
        for why in statements.footing_errors(months[period]):
            reasons.append(f"{period}:{why}")
    for period in sorted(months):
        idx = _month_index(period)
        prev = months.get(_label_of(idx - 1)) if idx is not None else None
        cur = months[period]
        if prev is None or not (statements.trusted(prev) and statements.trusted(cur)):
            continue
        opening, closing = statements.implied_opening(cur), statements.closing(prev)
        if opening is not None and closing is not None and opening != closing:
            reasons.append(f"{period}:chain")
    return reasons


def _label_of(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def stmt_tamper(state: Any) -> bool:
    return bool(stmt_tamper_reasons(state))


# ---------------------------------------------------------------- REV_INFLATION

def stated_revenue_cents(app: Optional[ParsedApplication]) -> Optional[int]:
    """The application's plain figure, or the midpoint of a range."""
    if app is None:
        return None
    if app.revenue_cents is not None:
        return app.revenue_cents
    if app.revenue_range_cents is not None:
        lo, hi = app.revenue_range_cents
        return (lo + hi) // 2
    return None


def annualized_deposits_cents(state: Any) -> Optional[int]:
    """The credits of the window months scaled to a year (two times the six); None unless all six are present, complete and verified."""
    ref = requirements.reference_date(state)
    if ref is None:
        return None
    months = latest_complete_statements(state)
    total = 0
    window = requirements.statement_window(ref)
    for period in window:
        ps = months.get(period)
        if ps is None or not statements.trusted(ps):
            return None
        credits = ps.total_credits_cents
        if credits is None:
            credits = sum(t.amount_cents for t in ps.txns if t.amount_cents is not None and t.amount_cents > 0)
        total += credits
    return total * 12 // len(window)  # six months doubled


def rev_inflation_numbers(state: Any) -> Optional[tuple[int, int]]:
    """``(highest stated revenue, annualized deposits)`` in cents when the check can run, else None."""
    annual = annualized_deposits_cents(state)
    if annual is None:
        return None
    app = requirements.latest_application(state)
    stated = [v for v in (stated_revenue_cents(app), _tax_receipts(state)) if v is not None]
    return (max(stated), annual) if stated else None


def _tax_receipts(state: Any) -> Optional[int]:
    """Gross receipts of the latest tax return, only when the business existed the whole tax year."""
    got = docmodel.parsed_all(state, "tax_return")
    tax = got[-1][1] if got else None
    if not isinstance(tax, ParsedTax) or tax.gross_receipts_cents is None or tax.tax_year is None:
        return None
    formed = requirements.formation_date(state)
    try:
        formed_on = date.fromisoformat(formed) if formed else None
    except ValueError:
        formed_on = None
    if formed_on is None or formed_on >= date(int(tax.tax_year), 1, 1):
        return None
    return tax.gross_receipts_cents


def rev_inflation(state: Any) -> bool:
    nums = rev_inflation_numbers(state)
    if nums is None:
        return False
    stated, annual = nums
    ratio = Fraction(str(_threshold("rev_inflation_ratio") or 1.5))
    return Fraction(stated) > ratio * annual


# ---------------------------------------------------------------- ID_MISMATCH

def id_mismatch(state: Any) -> bool:
    """A readable photo ID disagrees with the application: name beyond nicknames, date of birth, or address.

    An unreadable ID raises nothing; an OCR-sourced ID needs several disagreeing fields (see compare)."""
    from prism.read import compare

    return bool(compare.raising_id_results(state))


# ---------------------------------------------------------------- DUP_EIN

_HISTORY_CACHE: dict[tuple[str, int], dict[str, frozenset[str]]] = {}
_HISTORY_MAX_ROWS = 500_000


def _history_eins(path: Any) -> dict[str, frozenset[str]]:
    """``{EIN: {base names}}`` from history/deals.csv. Never raises; a bad row is skipped, a missing file is empty."""
    try:
        p = Path(path) / "deals.csv"
        key = (str(p), p.stat().st_mtime_ns)
    except (OSError, TypeError, ValueError):
        return {}
    hit = _HISTORY_CACHE.get(key)
    if hit is not None:
        return hit
    index: dict[str, set[str]] = {}
    try:
        with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
            for n, row in enumerate(csv.DictReader(f)):
                if n >= _HISTORY_MAX_ROWS:
                    break
                try:
                    ein = norm_ein(str(row.get("ein") or ""))
                    base = name_base(row.get("legal_name"))
                except Exception:  # noqa: BLE001
                    continue
                if ein and base:
                    index.setdefault(ein, set()).add(base)
    except (OSError, csv.Error):
        index = {}
    result = {k: frozenset(v) for k, v in index.items()}
    _HISTORY_CACHE.clear()
    _HISTORY_CACHE[key] = result
    return result


def dup_ein(state: Any, rules: Any) -> bool:
    """The EIN (IRS letter, else application) is in history under a different canonical base name."""
    history_dir = getattr(rules, "history_dir", None)
    if history_dir is None:
        return False
    app = requirements.latest_application(state)
    irs = docmodel.parsed_all(state, "ein_letter")
    irs_ein = getattr(irs[-1][1], "ein", None) if irs else None
    ein = norm_ein(irs_ein) or (norm_ein(app.ein) if app is not None else None)
    mine = name_base(app.legal_name) if app is not None else ""
    if not ein or not mine:
        return False
    known = _history_eins(history_dir).get(ein)
    return bool(known) and any(base != mine for base in known)


# ---------------------------------------------------------------- SHELL_ADDRESS

_SHELL_CACHE: dict[int, list[re.Pattern[str]]] = {}


def _shell_patterns() -> list[re.Pattern[str]]:
    pats = requirements.get_issues_config().raw.get("shell_address_patterns") or []
    key = id(pats)
    if key not in _SHELL_CACHE:
        compiled = []
        for pat in pats:
            try:
                compiled.append(re.compile(str(pat), re.IGNORECASE))
            except re.error:
                continue
        _SHELL_CACHE.clear()
        _SHELL_CACHE[key] = compiled
    return _SHELL_CACHE[key]


def shell_address(state: Any) -> bool:
    """The business address (application, else the certificate's principal office) is a mailbox store or PMB."""
    app = requirements.latest_application(state)
    addr = app.business_address if app is not None else None
    if not (isinstance(addr, str) and addr.strip()):
        cert = docmodel.parsed_all(state, "certificate_of_formation")
        addr = getattr(cert[-1][1], "principal_office", None) if cert else None
    if not (isinstance(addr, str) and addr.strip()):
        return False
    text = addr[:400]
    return any(rx.search(text) for rx in _shell_patterns())
