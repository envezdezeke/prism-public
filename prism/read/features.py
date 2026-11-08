"""state.features for Move (profile, D-09) and Fraud (deposit_months, D-10).

Only what can be determined is returned: unknown keys are omitted, never guessed, money is integer cents in
the profile and dollars (floats, like history/monthly_deposits.csv) in ``deposit_months``. Never sets
``fraud_watch`` (D-15: Phase 5 owns it). SSN, date of birth and driver-license numbers never appear here.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from prism.read import docmodel, history
from prism.read import issues as read_issues
from prism.read.parse_forms import split_prepared_by
from prism.read.settings import get_config

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------- bank statements

def _is_complete(p: Any) -> bool:
    total = getattr(p, "pages_total", None)
    if not total:
        return True
    return set(range(1, int(total) + 1)) <= set(getattr(p, "pages_found", ()) or ())


def complete_months(state: Any) -> list[tuple[str, Any]]:
    """``[(YYYY-MM, ParsedStatement)]`` sorted by month: the latest-arrived complete copy of each month.

    A month whose every copy misses a page has partial credits and is left out."""
    picked: dict[str, Any] = {}
    for doc, p in docmodel.parsed_all(state, "bank_statement"):
        period = getattr(p, "period", None) or getattr(doc, "period", None)
        if period and _is_complete(p):
            picked[period] = p  # parsed_all is in arrival order, so a later copy replaces an earlier one
    return sorted(picked.items())


def _credits(p: Any) -> list[Any]:
    return [t for t in (getattr(p, "txns", ()) or ()) if isinstance(t.amount_cents, int) and t.amount_cents > 0]


def weekday_label(days: set[int]) -> Optional[str]:
    """A label ``prism.fraud.rarity.parse_weekdays`` reads back to the same set (Mon=0)."""
    if not days:
        return None
    if days == set(range(7)):
        return "all days"
    if days == set(range(5)):
        return "Mon-Fri"
    if days == set(range(6)):
        return "Mon-Sat"
    return ",".join(_WEEKDAYS[d] for d in sorted(days))


def _month_row(period: str, p: Any) -> dict[str, Any]:
    credits = _credits(p)
    total = sum(t.amount_cents for t in credits)
    row: dict[str, Any] = {
        "month": period,
        "total_deposits": round(total / 100, 2),
        "deposit_count": len(credits),
        "pct_deposits_multiple_of_100": round(
            sum(1 for t in credits if t.amount_cents % 10_000 == 0) / len(credits), 6) if credits else 0.0,
        "largest_deposit": round(max((t.amount_cents for t in credits), default=0) / 100, 2),
    }
    days: set[int] = set()
    for t in credits:
        try:
            days.add(date.fromisoformat(str(t.date)[:10]).weekday())
        except (TypeError, ValueError):
            continue
    label = weekday_label(days)
    if label:
        row["deposit_weekdays"] = label
    end = getattr(p, "closing_cents", None)
    if end is None:
        end = next((t.balance_cents for t in reversed(getattr(p, "txns", ()) or ()) if t.balance_cents is not None), None)
    if isinstance(end, int):
        row["month_end_balance"] = round(end / 100, 2)
    return row


def build_deposit_months(state: Any) -> list[dict[str, Any]]:
    """One row per complete statement month, in the history column names. No statements gives ``[]``."""
    return [_month_row(period, p) for period, p in complete_months(state)]


# ---------------------------------------------------------------- profile

def _newest(state: Any, doc_class: str, attr: Optional[str] = None) -> tuple[Optional[Any], Optional[Any]]:
    found: tuple[Optional[Any], Optional[Any]] = (None, None)
    for doc, p in docmodel.parsed_all(state, doc_class):
        if attr is None or getattr(p, attr, None) is not None:
            found = (doc, p)
    return found


def _put(d: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "" and value != () and value != {}:
        d[key] = value


def _preparer_text(state: Any) -> Optional[str]:
    """"Name, CPA (Firm)" from the tax return, else the financial statements; "Self-prepared" stays as is."""
    _d, tax = _newest(state, "tax_return", "preparer_name")
    name = cred = firm = None
    if tax is not None:
        name, cred, firm = tax.preparer_name, tax.preparer_credential, tax.preparer_firm
    else:
        _d, fin = _newest(state, "financial_statements", "prepared_by")
        if fin is not None:
            name, cred, firm, _phone = split_prepared_by(fin.prepared_by)
    if not name:
        return None
    text = f"{name}, {cred}" if cred else name
    return f"{text} ({firm})" if firm else text


def _eins(state: Any, app: Any) -> list[str]:
    out: list[str] = []
    fact = state.facts.get("ein")
    for v in (getattr(fact, "value", None), getattr(app, "ein", None)):
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def _deposits_annualized(state: Any) -> Optional[int]:
    need = int((get_config().raw.get("features") or {}).get("min_deposit_months", 6))
    months = complete_months(state)
    if len(months) < need or need <= 0:
        return None
    recent = months[-need:]
    total = 0
    for _period, p in recent:
        credits = _credits(p)
        if credits:
            total += sum(t.amount_cents for t in credits)
        elif isinstance(getattr(p, "total_credits_cents", None), int):
            total += p.total_credits_cents
    return total * 2


def _mismatch_evidence(state: Any, rules: Any) -> dict[str, Any]:
    fn = getattr(read_issues, "mismatch_evidence", None)
    if not callable(fn):
        return {}
    try:
        ev = fn(state, rules)
        return ev if isinstance(ev, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def build_profile(state: Any, rules: Any) -> dict[str, Any]:
    """The ``features["profile"]`` dict Move reads; only keys with a known value. ``{}`` when nothing is known."""
    p: dict[str, Any] = {}
    app_doc, app = _newest(state, "application")
    if app is not None:
        _put(p, "dba", app.dba)
        _put(p, "formation_date", app.formation_date)
        _put(p, "application_date", app.application_date or app.signature_date)
        _put(p, "use_of_proceeds", app.purpose_text)
        _put(p, "collateral", app.collateral)
        titles = [g.title for g in app.guarantors if g.title]
        _put(p, "ownership", "; ".join(titles))
    _put(p, "preparer", _preparer_text(state))

    months = complete_months(state)
    if months:
        p["statement_months"] = len(months)
    if docmodel.parsed_all(state, "tax_return"):
        p["tax_doc"] = "tax_return"
    elif docmodel.parsed_all(state, "form_7004"):
        p["tax_doc"] = "form_7004"

    _d, fin = _newest(state, "financial_statements")
    if fin is not None:
        _put(p, "net_income_fy_cents", fin.net_income_fy_cents)
        if fin.net_income_ytd_cents is not None and fin.ytd_months:
            p["net_income_ytd_cents"] = fin.net_income_ytd_cents
            p["ytd_months"] = fin.ytd_months
        _put(p, "cash_cents", fin.cash_cents)
        _put(p, "receivables_cents", fin.receivables_cents)
        _put(p, "liabilities_cents", fin.liabilities_cents)
    _put(p, "deposits_annualized_cents", _deposits_annualized(state))

    hist = history.load_history(rules)
    rel = history.relationship(hist, _eins(state, app))
    _put(p, "relationship", rel)

    quote_doc, quote = _newest(state, "equipment_quote")
    if quote is not None:
        q: dict[str, Any] = {}
        _put(q, "number", quote.number)
        _put(q, "vendor", quote.vendor)
        _put(q, "total_cents", quote.total_cents)
        _put(q, "remit_last4", quote.remit_last4)
        _put(p, "quote", q)
        verified, past = history.vendor_check(hist, quote.vendor, quote.remit_last4)
        if verified is not None:
            p["vendor_verified"] = verified
        if hist.loaded and quote.vendor:
            p["vendor_past_loans"] = past
        d: dict[str, Any] = {"kind": "vendor"}
        _put(d, "bank", quote.remit_bank)
        _put(d, "last4", quote.remit_last4)
        _put(d, "vendor", quote.vendor)
        if hist.loaded and quote.vendor:
            d["past_loans"] = past
        p["disbursement"] = d
    elif app is not None and (app.bank_name or app.bank_last4):
        d = {"kind": "borrower"}
        _put(d, "bank", app.bank_name)
        _put(d, "last4", app.bank_last4)
        p["disbursement"] = d

    _put(p, "mismatch_evidence", _mismatch_evidence(state, rules))
    return p
