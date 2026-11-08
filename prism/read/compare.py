"""MISMATCH rules (J13) and photo-ID disagreements (J6): the application against its supporting documents.

Tolerances: EIN exact; names case, punctuation, "&"/"and", one trailing legal suffix and nickname tolerant;
addresses abbreviation and suite tolerant; amounts exact to the cent; bank balance within a configured number
of cents; revenue within a configured percent, only for the same year, a plain figure (never a range) and a
business that existed the whole year. A comparison against a document that is not parsed, or where either side
is unknown, is skipped, never raised. With several documents of one class the latest-arrived is compared.
Evidence strings carry the borrower's own values; a date of birth, SSN or licence number never appears.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, Optional

from prism.contract import ISSUE_RE
from prism.read import docmodel, requirements, statements
from prism.read.docmodel import (ParsedApplication, ParsedCert, ParsedFinancials, ParsedId, ParsedIrs,
                                 ParsedLetter, ParsedTax, Parsed7004)
from prism.read.normalize import addresses_match, legal_names_match, norm_ein, parse_date, person_names_match
from prism.read.settings import get_config
from prism.fraud import canon

log = logging.getLogger("prism")

_ALNUM = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class Mismatch:
    application: str
    document: str
    value: str


@dataclass(frozen=True)
class IdResult:
    doc_path: str
    guarantor: int
    fields: tuple[str, ...]
    ocr: bool


def _raw() -> dict[str, Any]:
    return dict(requirements.get_issues_config().raw)


def _threshold(name: str, default: Any) -> Any:
    return (_raw().get("thresholds") or {}).get(name, default)


def _id_flag() -> bool:
    return _raw().get("id_disagreement_also_mismatch") is True


def _label(doc_class: str) -> str:
    return str((_raw().get("doc_labels") or {}).get(doc_class) or doc_class.replace("_", " "))


def money_text(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def _latest(state: Any, doc_class: str) -> Any:
    got = docmodel.parsed_all(state, doc_class)
    return got[-1][1] if got else None


def _text(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


# ---------------------------------------------------------------- the five document comparisons

def _legal_name(state: Any, app: ParsedApplication) -> Optional[Mismatch]:
    name = _text(app.legal_name)
    if name is None:
        return None
    for cls, attr, parsed_type in (("ein_letter", "name", ParsedIrs), ("certificate_of_formation", "entity_name", ParsedCert),
                                   ("tax_return", "name", ParsedTax)):
        doc = _latest(state, cls)
        other = _text(getattr(doc, attr, None)) if isinstance(doc, parsed_type) else None
        if other is not None and not legal_names_match(name, other):
            return Mismatch(name, _label(cls), other)
    return None


def _ein(state: Any, app: ParsedApplication) -> Optional[Mismatch]:
    mine = norm_ein(app.ein)
    if mine is None:
        return None
    for cls, parsed_type in (("ein_letter", ParsedIrs), ("tax_return", ParsedTax), ("form_7004", Parsed7004)):
        doc = _latest(state, cls)
        raw = _text(getattr(doc, "ein", None)) if isinstance(doc, parsed_type) else None
        theirs = norm_ein(raw)
        if theirs is not None and theirs != mine:
            return Mismatch(_text(app.ein) or mine, _label(cls), raw or theirs)
    return None


def _amount(state: Any, app: ParsedApplication) -> Optional[Mismatch]:
    letter = _latest(state, "request_letter")
    if app.amount_cents is None or not isinstance(letter, ParsedLetter) or letter.amount_cents is None:
        return None
    if letter.amount_cents != app.amount_cents:
        return Mismatch(money_text(app.amount_cents), _label("request_letter"), money_text(letter.amount_cents))
    return None


def _existed_all_year(state: Any, year: int) -> bool:
    formed = requirements.formation_date(state)
    try:
        return formed is not None and date.fromisoformat(formed) < date(year, 1, 1)
    except ValueError:
        return False


def _revenue(state: Any, app: ParsedApplication) -> Optional[Mismatch]:
    stated = app.revenue_cents
    ref = requirements.reference_date(state)
    if stated is None or stated <= 0 or ref is None:  # a range, or nothing, is never compared
        return None
    year = ref.year - int(_raw().get("revenue_year_offset") or 1)
    if not _existed_all_year(state, year):
        return None
    pct = Fraction(str(_threshold("revenue_tolerance_pct", 1.0)))
    tax, fin = _latest(state, "tax_return"), _latest(state, "financial_statements")
    pairs = []
    if isinstance(tax, ParsedTax) and tax.tax_year == year:
        pairs.append(("tax_return", tax.gross_receipts_cents))
    if isinstance(fin, ParsedFinancials) and fin.fy_year == year:
        pairs.append(("financial_statements", fin.revenue_fy_cents))
    for cls, value in pairs:
        if value is not None and abs(value - stated) * 100 > pct * stated:
            return Mismatch(money_text(stated), _label(cls), money_text(value))
    return None


def _bank_balance(state: Any, app: ParsedApplication) -> Optional[Mismatch]:
    fin = _latest(state, "financial_statements")
    if not isinstance(fin, ParsedFinancials) or fin.cash_cents is None or not fin.balance_date:
        return None
    day = parse_date(fin.balance_date)
    closing = None
    for _doc, ps in docmodel.parsed_all(state, "bank_statement"):  # arrival order: a later copy wins
        if day and getattr(ps, "period_end", None) == day and statements.is_complete(ps):
            closing = statements.closing(ps)
    if closing is None:
        return None
    if abs(fin.cash_cents - closing) > int(_threshold("bank_balance_tolerance_cents", 100)):
        return Mismatch(f"{money_text(fin.cash_cents)} on your balance sheet", _label("bank_statement"),
                        money_text(closing))
    return None


# ---------------------------------------------------------------- photo ID

def _glue(text: Any) -> str:
    return _ALNUM.sub("", str(text or "").casefold())


def _aliases(token: str) -> set[str]:
    out = {token}
    for group in get_config().raw.get("nicknames") or []:
        folded = {canon.canon_text(str(t)) for t in group or []}
        if token in folded:
            out |= folded
    return out


def _name_agrees(app_name: Optional[str], id_name: Optional[str], ocr: bool) -> bool:
    if not app_name or not id_name:
        return True  # nothing to compare
    if person_names_match(app_name, id_name):
        return True
    glued = _glue(id_name)
    tokens = canon.canon_text(app_name).split()
    if not glued or not tokens:
        return True
    hits = sum(1 for t in tokens if any(_glue(a) and _glue(a) in glued for a in _aliases(t)))
    need = 2 if len(tokens) >= 2 else 1
    return hits >= min(need, len(tokens))


def _address_agrees(app_addr: Optional[str], id_addr: Optional[str], ocr: bool) -> bool:
    if not app_addr or not id_addr:
        return True
    if addresses_match(app_addr, id_addr):
        return True
    a, b = _glue(app_addr), _glue(id_addr)
    if a and b and (a in b or b in a):
        return True
    # abbreviation-folded, space-insensitive (OCR drops spaces): compare the canonical street words glued together
    return _glue(canon.canon_address(app_addr)) == _glue(canon.canon_address(id_addr)) or (
        ocr and _glue(canon.canon_address(app_addr)) in _glue(canon.canon_address(id_addr)))


def _dob_agrees(app_dob: Optional[str], id_dob: Optional[str]) -> bool:
    a, b = parse_date(app_dob or ""), parse_date(id_dob or "")
    return a is None or b is None or a == b


def _disagreements(g: Any, pid: ParsedId, ocr: bool) -> tuple[str, ...]:
    out = []
    if not _name_agrees(_text(g.name), _text(pid.name), ocr):
        out.append("name")
    if not _dob_agrees(g.dob, pid.dob):
        out.append("dob")
    if not _address_agrees(_text(g.address), _text(pid.address), ocr):
        out.append("address")
    return tuple(out)


def id_disagreements(state: Any) -> list[IdResult]:
    """Per readable photo ID, the fields that disagree with the closest guarantor (empty tuple when it agrees).

    An unreadable ID (``readable`` False) is unverified and gives no entry."""
    app = requirements.latest_application(state)
    if app is None or not app.guarantors:
        return []
    out: list[IdResult] = []
    for doc, pid in docmodel.parsed_all(state, "guarantor_id"):
        if not isinstance(pid, ParsedId) or pid.readable is False:
            continue
        if not any((_text(pid.name), _text(pid.dob), _text(pid.address))):
            continue
        ocr = bool(pid.via_ocr)
        best: Optional[tuple[int, tuple[str, ...]]] = None
        for i, g in enumerate(app.guarantors):
            fields = _disagreements(g, pid, ocr)
            if best is None or len(fields) < len(best[1]):
                best = (i, fields)
        if best is not None:
            out.append(IdResult(doc.path, best[0], best[1], ocr))
    return out


def raising_id_results(state: Any) -> list[IdResult]:
    """The ID results that count as a real disagreement (one field; an OCR read needs several)."""
    need_ocr = int(_threshold("ocr_min_disagreeing_fields", 2))
    return [r for r in id_disagreements(state) if len(r.fields) >= (need_ocr if r.ocr else 1)]


_ID_FIELD = {"name": "guarantor_name", "dob": "guarantor_dob", "address": "guarantor_address"}


def _id_mismatches(state: Any) -> dict[str, Mismatch]:
    out: dict[str, Mismatch] = {}
    app = requirements.latest_application(state)
    if app is None or not _id_flag():
        return out
    for r in raising_id_results(state):
        g = app.guarantors[r.guarantor]
        pid = next((p for d, p in docmodel.parsed_all(state, "guarantor_id") if d.path == r.doc_path), None)
        for f in r.fields:
            if f == "dob":  # never put a date of birth in evidence
                ev = Mismatch("(date of birth as stated)", _label("guarantor_id"), "(a different date of birth)")
            else:
                mine, theirs = (g.name, pid.name) if f == "name" else (g.address, pid.address)
                ev = Mismatch(_text(mine) or "", _label("guarantor_id"), _text(theirs) or "")
            out.setdefault(_ID_FIELD[f], ev)
    return out


# ---------------------------------------------------------------- public

_RULES = (("legal_name", _legal_name), ("ein", _ein), ("amount_requested", _amount),
          ("annual_revenue", _revenue), ("bank_balance", _bank_balance))


def find_mismatches(state: Any, rules: Any = None) -> dict[str, Mismatch]:
    """``{field: Mismatch}`` for every real disagreement; each rule is isolated and never raises."""
    out: dict[str, Mismatch] = {}
    app = requirements.latest_application(state)
    if app is not None:
        for field, fn in _RULES:
            try:
                m = fn(state, app)
            except Exception:  # noqa: BLE001 - never include document text in the log
                log.error("mismatch rule %s failed", field, exc_info=True)
                continue
            if m is not None and ISSUE_RE.fullmatch(f"MISMATCH:{field}"):
                out[field] = m
    try:
        out.update(_id_mismatches(state))
    except Exception:  # noqa: BLE001
        log.error("id mismatch rule failed", exc_info=True)
    return out
