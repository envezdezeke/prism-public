"""Parsed document types shared by every Read module, plus the parser registry.

All dataclasses are frozen and pure data. Scalar fields default to None (unknown); list fields are
tuples and default to ``()``. Money is integer cents, dates are ISO ``YYYY-MM-DD`` strings, EIN is
``NN-NNNNNNN``; names and addresses are the verbatim stripped document text.

A parser is ``fn(dt: DocText, doc: Doc) -> Parsed*`` registered with ``register(doc_class, fn)``.
``parsed(doc)`` dispatches on ``doc.doc_class``, caches by file version and class, and never raises.
Parser modules listed in ``PARSER_MODULES`` are imported lazily on the first ``parsed`` call; a
module that does not exist yet is skipped, any other import error is logged and skipped.
"""
from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from prism.read.text import DocText, load_text

log = logging.getLogger("prism")

PARSER_MODULES = ("prism.read.parse_application", "prism.read.parse_forms", "prism.read.statements")


@dataclass(frozen=True)
class Guarantor:
    name: Optional[str] = None
    title: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None
    dl_number: Optional[str] = None


@dataclass(frozen=True)
class ParsedApplication:
    legal_name: Optional[str] = None
    dba: Optional[str] = None
    ein: Optional[str] = None
    entity_type: Optional[str] = None
    formation_date: Optional[str] = None
    business_address: Optional[str] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    industry: Optional[str] = None
    employees: Optional[str] = None
    revenue_text: Optional[str] = None
    revenue_cents: Optional[int] = None
    revenue_range_cents: Optional[tuple[int, int]] = None
    bank_name: Optional[str] = None
    bank_last4: Optional[str] = None
    amount_cents: Optional[int] = None
    product: Optional[str] = None
    purpose_text: Optional[str] = None
    term_months: Optional[int] = None
    collateral: Optional[str] = None
    needed_by_text: Optional[str] = None
    needed_by: Optional[str] = None
    guarantors: tuple[Guarantor, ...] = ()
    signed: Optional[bool] = None
    application_date: Optional[str] = None
    signature_date: Optional[str] = None
    missing: tuple[str, ...] = ()
    declared_none: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedId:
    name: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None
    dl_number: Optional[str] = None
    expires: Optional[str] = None
    readable: Optional[bool] = None
    via_ocr: Optional[bool] = None


@dataclass(frozen=True)
class ParsedIrs:
    name: Optional[str] = None
    ein: Optional[str] = None
    notice_date: Optional[str] = None


@dataclass(frozen=True)
class ParsedCert:
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    file_number: Optional[str] = None
    effective_date: Optional[str] = None
    principal_office: Optional[str] = None
    registered_agent: Optional[str] = None
    registered_office: Optional[str] = None


@dataclass(frozen=True)
class ParsedTax:
    name: Optional[str] = None
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    gross_receipts_cents: Optional[int] = None
    ordinary_income_cents: Optional[int] = None
    preparer_name: Optional[str] = None
    preparer_firm: Optional[str] = None
    preparer_credential: Optional[str] = None
    date_filed: Optional[str] = None


@dataclass(frozen=True)
class Parsed7004:
    name: Optional[str] = None
    ein: Optional[str] = None
    tax_year: Optional[int] = None
    extended_due_date: Optional[str] = None
    status: Optional[str] = None


@dataclass(frozen=True)
class ParsedLetter:
    amount_cents: Optional[int] = None
    amount_text: Optional[str] = None


@dataclass(frozen=True)
class ParsedQuote:
    number: Optional[str] = None
    vendor: Optional[str] = None
    remit_bank: Optional[str] = None
    remit_last4: Optional[str] = None
    total_cents: Optional[int] = None
    quote_date: Optional[str] = None


@dataclass(frozen=True)
class ParsedFinancials:
    has_pl: Optional[bool] = None
    has_balance_sheet: Optional[bool] = None
    prepared_by: Optional[str] = None
    preparer_name: Optional[str] = None
    preparer_firm: Optional[str] = None
    preparer_phone: Optional[str] = None
    fy_year: Optional[int] = None
    revenue_fy_cents: Optional[int] = None
    net_income_fy_cents: Optional[int] = None
    revenue_ytd_cents: Optional[int] = None
    net_income_ytd_cents: Optional[int] = None
    ytd_months: Optional[int] = None
    balance_date: Optional[str] = None
    cash_cents: Optional[int] = None
    receivables_cents: Optional[int] = None
    liabilities_cents: Optional[int] = None


@dataclass(frozen=True)
class Txn:
    date: Optional[str] = None
    description: Optional[str] = None
    amount_cents: Optional[int] = None  # credit positive, debit negative
    balance_cents: Optional[int] = None


@dataclass(frozen=True)
class ParsedStatement:
    source: Optional[str] = None  # pdf or csv
    period: Optional[str] = None  # YYYY-MM
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    bank: Optional[str] = None
    holder: Optional[str] = None
    account_last4: Optional[str] = None
    opening_cents: Optional[int] = None
    closing_cents: Optional[int] = None
    total_credits_cents: Optional[int] = None
    total_debits_cents: Optional[int] = None
    txns: tuple[Txn, ...] = ()
    pages_found: tuple[int, ...] = ()
    pages_total: Optional[int] = None
    signs_verified: Optional[bool] = None


# ---------------------------------------------------------------- registry

_PARSERS: dict[str, Callable[[DocText, Any], Any]] = {}
_CACHE: dict[tuple[str, int, int, str], Any] = {}
_CACHE_MAX = 1024
_MODULES_LOADED = False


def register(doc_class: str, fn: Callable[[DocText, Any], Any]) -> None:
    """Register (or replace) the parser for a document class."""
    _PARSERS[doc_class] = fn


def clear_cache() -> None:
    _CACHE.clear()


def _load_modules() -> None:
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return
    _MODULES_LOADED = True
    for name in PARSER_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name != name:
                log.error("parser module %s failed to import", name, exc_info=True)
        except Exception:  # noqa: BLE001
            log.error("parser module %s failed to import", name, exc_info=True)


def parsed(doc: Any) -> Any:
    """The parsed object for a classified Doc, or None. Never raises."""
    try:
        _load_modules()
        cls = getattr(doc, "doc_class", None)
        fn = _PARSERS.get(cls or "")
        if fn is None:
            return None
        st = os.stat(doc.abs_path)
        key = (str(doc.abs_path.resolve()), st.st_mtime_ns, st.st_size, cls)
        if key in _CACHE:
            return _CACHE[key]
        try:
            result = fn(load_text(doc), doc)
        except Exception:  # noqa: BLE001
            log.error("parser for %s failed", cls, exc_info=True)
            result = None
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = result
        return result
    except Exception:  # noqa: BLE001
        return None


def parsed_all(state: Any, doc_class: str) -> list[tuple[Any, Any]]:
    """``[(doc, parsed)]`` for the visible docs of a class, sorted by (arrive_at, path)."""
    out = []
    for doc in sorted((d for d in state.docs if d.doc_class == doc_class),
                      key=lambda d: (d.arrive_at, d.path)):
        p = parsed(doc)
        if p is not None:
            out.append((doc, p))
    return out
