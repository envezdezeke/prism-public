"""Graph entities for Fraud (D-10, READ-03).

Every ``EntityRef.value`` is ``prism.fraud.canon.canon_value(kind, cleaned_raw)``: hygiene first
(control characters stripped, whitespace collapsed, length capped), then the canonical form Fraud links
on (idempotent, so ``graph.py`` re-canonicalizing changes nothing). Built from parsed documents only.

Never an entity: date of birth, driver-license number, SSN, the business phone, the routing number.
A guarantor entity is the name only. Placeholders such as ``(owner)`` are dropped; ``Self-prepared`` is kept.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable, Optional

from prism.engine.state import DealState, EntityRef
from prism.fraud.canon import canon_text, canon_value
from prism.read import docmodel
from prism.read.parse_forms import split_prepared_by
from prism.read.settings import get_config

_DROP_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}


def _cfg() -> dict[str, Any]:
    return get_config().raw.get("entities") or {}


def clean_value(text: Any, limit: Optional[int] = None) -> str:
    """Strip control characters, collapse whitespace, cap the length. Never raises."""
    if text is None:
        return ""
    cap = int(limit if limit is not None else _cfg().get("max_length", 200))
    chars = []
    for ch in str(text):
        if ch in "\t\n\r\x0b\x0c  ":
            chars.append(" ")
        elif unicodedata.category(ch) in _DROP_CATEGORIES:
            continue
        else:
            chars.append(ch)
    return " ".join("".join(chars).split())[:max(cap, 0)].strip()


def _is_placeholder(cleaned: str) -> bool:
    folded = cleaned.casefold().strip()
    if not folded or not canon_text(folded):
        return True
    return folded in {str(p).casefold() for p in _cfg().get("placeholders") or []}


def make_ref(kind: str, raw: Any, source_doc: str) -> Optional[EntityRef]:
    """One canonical ``EntityRef`` or None when the value is empty or a placeholder."""
    cleaned = clean_value(raw)
    if not cleaned or _is_placeholder(cleaned):
        return None
    value = canon_value(kind, cleaned)
    return EntityRef(kind=kind, value=value, source_doc=source_doc) if value else None


def _newest(state: DealState, doc_class: str, attr: Optional[str] = None) -> tuple[Optional[Any], Optional[Any]]:
    """Newest-arrived ``(doc, parsed)`` of a class (with ``attr`` set when given), else ``(None, None)``."""
    found = (None, None)
    for doc, p in docmodel.parsed_all(state, doc_class):
        if attr is None or getattr(p, attr, None):
            found = (doc, p)
    return found


def _preparer(state: DealState) -> Optional[tuple[str, str]]:
    """``("<Name>, <Credential>", source_doc)``: the tax return first, the financial statements otherwise."""
    keep = bool(_cfg().get("keep_credential", True))
    doc, tax = _newest(state, "tax_return", "preparer_name")
    if tax is not None:
        name, cred = tax.preparer_name, tax.preparer_credential
        return (f"{name}, {cred}" if cred and keep else name), doc.path
    doc, fin = _newest(state, "financial_statements", "prepared_by")
    if fin is not None:
        name, cred, _firm, _phone = split_prepared_by(fin.prepared_by)
        if name:
            return (f"{name}, {cred}" if cred and keep else name), doc.path
    return None


def build_entities(state: DealState, rules: Any = None) -> list[EntityRef]:
    """Graph entities from the visible, classified documents. Deduplicated and sorted. Never raises."""
    raw: list[tuple[str, Any, str]] = []

    app_doc, app = _newest(state, "application")
    if app is not None:
        raw.append(("address", app.business_address, app_doc.path))
        raw.append(("bank", app.bank_name, app_doc.path))
        for g in app.guarantors:
            raw.append(("guarantor", g.name, app_doc.path))
        producer, creator = app_doc.meta.get("producer"), app_doc.meta.get("creator")
        raw.append(("pdf_producer", producer, app_doc.path))
        if creator and clean_value(creator).casefold() != clean_value(producer).casefold():
            raw.append(("pdf_producer", creator, app_doc.path))

    prep = _preparer(state)
    if prep is not None:
        raw.append(("preparer", prep[0], prep[1]))
    fin_doc, fin = _newest(state, "financial_statements")
    if fin is not None:
        _n, _c, _f, phone = split_prepared_by(fin.prepared_by)
        raw.append(("phone", phone or fin.preparer_phone, fin_doc.path))

    cert_doc, cert = _newest(state, "certificate_of_formation")
    if cert is not None:
        raw.append(("registered_agent", cert.registered_agent, cert_doc.path))

    quote_doc, quote = _newest(state, "equipment_quote")
    if quote is not None:
        raw.append(("vendor", quote.vendor, quote_doc.path))
        raw.append(("remit_account", quote.remit_last4, quote_doc.path))
        raw.append(("bank", quote.remit_bank, quote_doc.path))

    seen: dict[tuple[str, str], EntityRef] = {}
    for kind, value, source in raw:
        try:
            ref = make_ref(kind, value, source)
        except Exception:  # noqa: BLE001
            ref = None
        if ref is not None:
            seen.setdefault((ref.kind, ref.value), ref)
    return sorted(seen.values(), key=lambda r: (r.kind, r.value, r.source_doc))


def entity_kinds(refs: Iterable[EntityRef]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in refs:
        out[r.kind] = out.get(r.kind, 0) + 1
    return dict(sorted(out.items()))
