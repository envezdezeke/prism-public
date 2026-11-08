"""Label-anchored parser for the loan application (class ``application``).

Generic by design (D-01): every field is found by its label text from config/read.yaml, in
plain or layout text, with no coordinates. The SSN label is a terminator only; its value is never
read into any field or into ``evidence``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from prism.read import docmodel
from prism.read.docmodel import Guarantor, ParsedApplication
from prism.read.labels import scan_labeled, split_sections
from prism.read.normalize import (
    last4, money_range_to_cents, money_to_cents, needed_by_iso, norm_ein, parse_date, strip_none,
)
from prism.read.settings import get_config
from prism.read.text import DocText

_BANK_RE = re.compile(r"^(.*?)\s*\.{2,}\s*(\d{4})\s*$")
_TERM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([A-Za-z]*)")
_STATE_DL_RE = re.compile(r"^[A-Za-z]{2}\s+(\S.*)$")
_SIG_DATE_RE = re.compile(r"\bdate\s*:?\s*(.+)$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[^\s/,;]+@[^\s/,;]+")
_GUAR_FIELDS = {"guarantor_name": "name", "guarantor_title": "title", "guarantor_dob": "dob",
                "guarantor_address": "address", "guarantor_dl": "dl_number"}


def term_to_months(text: Optional[str]) -> Optional[int]:
    """"36 months" -> 36, "3 years" -> 36, a bare integer is months, anything else None."""
    if not text:
        return None
    units = get_config().raw.get("term_units") or {}
    months = {u.casefold() for u in units.get("months", [])}
    years = {u.casefold() for u in units.get("years", [])}
    m = _TERM_RE.search(text)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).casefold()
    if unit in years:
        total = n * 12
    elif unit in months or unit == "":
        total = n
    else:
        return None
    return int(total) if total == int(total) and total > 0 else None


def product_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    folded = " ".join(text.casefold().split())
    for phrase, product in (get_config().raw.get("product_synonyms") or {}).items():
        if re.search(rf"\b{re.escape(str(phrase).casefold())}\b", folded):
            return str(product)
    return None


def _merged_hits(dt: DocText, labels, stops) -> list[tuple[str, str]]:
    """Plain-text hits, plus layout-text hits for fields plain did not find."""
    plain = scan_labeled(dt.plain, labels, stops)
    seen = {f for f, v in plain if v}
    extra = [(f, v) for f, v in scan_labeled(dt.layout, labels, stops) if v and f not in seen]
    return plain + extra


def _first(hits: list[tuple[str, str]]) -> tuple[dict[str, str], set[str]]:
    """(first non-empty value per field, fields whose label was present at all)."""
    values: dict[str, str] = {}
    present: set[str] = set()
    for f, v in hits:
        present.add(f)
        if v and f not in values:
            values[f] = v
    return values, present


def _guarantors(text: str, labels, stops) -> tuple[Guarantor, ...]:
    sections = split_sections(text, get_config().raw.get("sections") or {})
    scope = sections.get("guarantor") or text
    out: list[dict[str, Optional[str]]] = []
    for f, v in scan_labeled(scope, labels, stops):
        if f == "guarantor_name":
            out.append({"name": strip_none(v)})
        elif f in _GUAR_FIELDS and out and _GUAR_FIELDS[f] not in out[-1]:
            key = _GUAR_FIELDS[f]
            if key == "dob":
                out[-1][key] = parse_date(v)
            elif key == "dl_number":
                m = _STATE_DL_RE.match(v.strip())
                out[-1][key] = (m.group(1) if m else v).strip() or None
            else:
                out[-1][key] = strip_none(v)
    return tuple(Guarantor(**{k: g.get(k) for k in ("name", "title", "dob", "address", "dl_number")})
                 for g in out if g.get("name"))


def _signature(dt: DocText, cfg) -> tuple[Optional[bool], Optional[str]]:
    sig = cfg.raw.get("signature") or {}
    markers = [str(m).casefold() for m in sig.get("markers") or ["/s/"]]
    lines = [ln.strip() for ln in (dt.plain or dt.layout).splitlines()]
    for ln in lines:
        folded = ln.casefold()
        for mk in markers:
            at = folded.find(mk)
            if at >= 0:
                rest = ln[at + len(mk):].strip()
                dm = _SIG_DATE_RE.search(rest)
                name = rest[:dm.start()].strip() if dm else rest
                if name:
                    return True, (parse_date(dm.group(1)) if dm else None)
    labels = {"signature": list(sig.get("labels") or [])}
    for f, v in scan_labeled(dt.plain or dt.layout, labels, ("Section*",)):
        if f == "signature":
            return (True if strip_none(v) else False), None
    return None, None


def parse_application(dt: DocText, doc: Any = None) -> ParsedApplication:
    cfg = get_config()
    labels = cfg.labels.get("application", {})
    stops = tuple(cfg.raw.get("stops") or ())
    hits = [(f, v) for f, v in _merged_hits(dt, labels, stops)]
    raw, present = _first([(f, v) for f, v in hits if f not in _GUAR_FIELDS or f == "guarantor_name"])
    evidence: dict[str, str] = {}
    missing: list[str] = []
    declared_none: list[str] = []

    def classify(field: str, label: str, parsed: object) -> None:
        """Record missing (absent, empty or unparseable) versus declared none for a tracked field."""
        value = raw.get(label)
        if label not in present or value is None:
            missing.append(field)
        elif strip_none(value) is None:
            declared_none.append(field)
        elif parsed is None:
            missing.append(field)

    legal = strip_none(raw.get("legal_name"))
    ein = norm_ein(raw.get("ein"))
    amount = money_to_cents(raw.get("amount_requested"))
    revenue_text = strip_none(raw.get("revenue"))
    revenue_cents = money_to_cents(revenue_text) if revenue_text else None
    revenue_range = money_range_to_cents(revenue_text) if revenue_text and revenue_cents is None else None
    bank_raw = strip_none(raw.get("bank"))
    bank_name = bank_last4 = None
    if bank_raw:
        m = _BANK_RE.match(bank_raw)
        bank_name, bank_last4 = (m.group(1).strip() or None, m.group(2)) if m else (bank_raw, last4(bank_raw))
    product = product_from_text(strip_none(raw.get("product")))
    purpose = strip_none(raw.get("purpose"))
    term = term_to_months(strip_none(raw.get("term")))
    needed_text = raw.get("needed_by")
    needed_by = needed_by_iso(needed_text) if needed_text else None
    guarantors = max((_guarantors(t, labels, stops) for t in (dt.plain, dt.layout)),
                     key=lambda gs: (len(gs), sum(v is not None for g in gs for v in vars(g).values())))
    phone_email = strip_none(raw.get("business_phone_email")) or ""
    email_m = _EMAIL_RE.search(phone_email)
    phone = next((p.strip() for p in re.split(r"\s*/\s*|\s{2,}", phone_email)
                  if sum(c.isdigit() for c in p) >= 7 and "@" not in p), None)
    signed, sig_date = _signature(dt, cfg)

    classify("legal_name", "legal_name", legal)
    classify("ein", "ein", ein)
    classify("amount_requested", "amount_requested", amount)
    classify("purpose_category", "purpose", purpose)
    classify("product", "product", product)
    classify("term_months", "term", term)
    classify("guarantor_name", "guarantor_name", guarantors[0].name if guarantors else None)
    classify("annual_revenue_stated", "revenue", revenue_cents if revenue_cents is not None else revenue_range)
    classify("needed_by", "needed_by", needed_by)
    classify("dba", "dba", strip_none(raw.get("dba")))
    for fld, label in (("revenue", "revenue"), ("needed_by", "needed_by"), ("amount_requested", "amount_requested"),
                       ("purpose", "purpose"), ("term", "term"), ("product", "product")):
        if raw.get(label):
            evidence[fld] = raw[label]
    missing = [m for m in missing if m != "dba"]  # a missing DBA label is not a gap worth filling
    return ParsedApplication(
        legal_name=legal, dba=strip_none(raw.get("dba")), ein=ein,
        entity_type=strip_none(raw.get("entity_type")), formation_date=parse_date(raw.get("formation_date")),
        business_address=strip_none(raw.get("business_address")),
        business_phone=phone, business_email=email_m.group(0) if email_m else None,
        industry=strip_none(raw.get("industry")), employees=strip_none(raw.get("employees")),
        revenue_text=revenue_text, revenue_cents=revenue_cents, revenue_range_cents=revenue_range,
        bank_name=bank_name, bank_last4=bank_last4, amount_cents=amount, product=product,
        purpose_text=purpose, term_months=term, collateral=strip_none(raw.get("collateral")),
        needed_by_text=strip_none(needed_text), needed_by=needed_by, guarantors=guarantors,
        signed=signed, application_date=parse_date(raw.get("application_date")), signature_date=sig_date,
        missing=tuple(dict.fromkeys(missing)), declared_none=tuple(dict.fromkeys(declared_none)),
        evidence=evidence,
    )


docmodel.register("application", parse_application)
