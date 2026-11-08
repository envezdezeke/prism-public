"""Fact and entity extraction (owner: Phase 3, READ-02, READ-03).

Contract: both functions are full recomputes over ``state.docs``; the caller replaces
``state.facts`` / ``state.entities`` with the return value. Facts carry source_doc and
confidence; unknown values are simply absent (the writer emits null). ``extract_features`` is a
full recompute too; its dict is merged into ``state.features`` by the caller (never replaced).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from prism.contract import Extracted, Fact
from prism.engine.config import Rules
from prism.engine.state import DealState, EntityRef
from prism.engine.money import cents_to_number
from prism.read import docmodel
from prism.read import vl as read_vl
from prism.read.settings import get_config

FACT_FIELDS: tuple[str, ...] = tuple(Extracted.model_fields)


def _purpose_category(text: Optional[str], product: Optional[str]) -> tuple[Optional[str], float]:
    """Ordered keyword table (config); product fallback for equipment loans; else None (never guess)."""
    conf = get_config().confidence
    folded = " ".join((text or "").casefold().split())
    for entry in get_config().raw.get("purpose_keywords") or []:
        for word in entry.get("words") or []:
            w = re.escape(str(word).casefold())
            pat = rf"\b{w}(?:s|es)?\b" if len(str(word)) <= 4 else rf"\b{w}"
            if folded and re.search(pat, folded):
                return str(entry["category"]), conf["keyword"]
    if product == "Equipment Loan":
        return "equipment", conf["inferred"]
    return None, 0.0


def _application_facts(doc, app, conf) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}

    def put(name: str, value: Any, confidence: float) -> None:
        if value is not None:
            facts[name] = Fact(value=value, source_doc=doc.path, confidence=confidence)

    put("legal_name", app.legal_name, conf["label"])
    put("ein", app.ein, conf["label"])
    put("amount_requested", None if app.amount_cents is None else cents_to_number(app.amount_cents),
        conf["label"])
    category, cat_conf = _purpose_category(app.purpose_text, app.product)
    put("purpose_category", category, cat_conf)
    put("product", app.product, conf["label"])
    put("term_months", app.term_months, conf["label"])
    put("guarantor_name", app.guarantors[0].name if app.guarantors else None, conf["label"])
    if app.revenue_cents is not None:
        put("annual_revenue_stated", cents_to_number(app.revenue_cents), conf["label"])
    elif app.revenue_range_cents is not None:
        lo, hi = app.revenue_range_cents
        put("annual_revenue_stated", cents_to_number((lo + hi) // 2), conf["range"])
    put("needed_by", app.needed_by, conf["label"])
    return facts


def _newest_with(state: DealState, doc_class: str, attr: str):
    """Newest-arrived ``(doc, parsed)`` of a class whose ``attr`` is set, else None."""
    found = None
    for doc, p in docmodel.parsed_all(state, doc_class):
        if getattr(p, attr, None):
            found = (doc, p)
    return found


def _fallback_facts(state: DealState, app, facts: dict[str, Fact], conf) -> None:
    """D-15: the IRS-letter EIN is the fact; other documents fill only fields the application left missing."""

    def put(name: str, value: Any, doc, confidence: float) -> None:
        facts[name] = Fact(value=value, source_doc=doc.path, confidence=confidence)

    letter = _newest_with(state, "ein_letter", "ein")
    if letter is not None:
        put("ein", letter[1].ein, letter[0], conf["label"])  # the application value stays the answer's alternative
    elif "ein" not in facts:
        for cls in ("tax_return", "form_7004"):
            hit = _newest_with(state, cls, "ein")
            if hit is not None:
                put("ein", hit[1].ein, hit[0], conf["inferred"])
                break

    def open_field(name: str) -> bool:
        return name not in facts and name in app.missing and name not in app.declared_none

    if open_field("legal_name"):
        for cls, attr in (("certificate_of_formation", "entity_name"), ("ein_letter", "name")):
            hit = _newest_with(state, cls, attr)
            if hit is not None:
                put("legal_name", getattr(hit[1], attr), hit[0], conf["inferred"])
                break
    if open_field("guarantor_name"):
        for doc, p in reversed(docmodel.parsed_all(state, "guarantor_id")):
            if p.name and p.readable and not p.via_ocr:  # an OCR name is never promoted to a fact
                put("guarantor_name", p.name, doc, conf["inferred"])
                break


def extract_facts(state: DealState, rules: Rules) -> dict[str, Fact]:
    """Return the facts known from the visible docs, keyed by Extracted field. Never raises."""
    try:
        apps = docmodel.parsed_all(state, "application")
        if not apps:
            return {}
        doc, app = apps[-1]  # latest arrival wins; parsed_all sorts by (arrive_at, path)
        conf = get_config().confidence
        facts = _application_facts(doc, app, conf)
        try:
            _fallback_facts(state, app, facts, conf)
        except Exception:  # noqa: BLE001
            pass
        try:
            facts = read_vl.fill_nulls(state, rules, facts)  # null-fill only; off/failed means unchanged
        except Exception:  # noqa: BLE001
            pass
        return facts
    except Exception:  # noqa: BLE001
        return {}


def extract_entities(state: DealState, rules: Rules) -> list[EntityRef]:
    """Return graph attributes (preparer, phone, agent, ...) from the visible docs (D-10). Never raises."""
    try:
        from prism.read.entities import build_entities

        return build_entities(state, rules)
    except Exception:  # noqa: BLE001
        return []


def extract_features(state: DealState, rules: Rules) -> dict[str, Any]:
    """Return features (profile, deposit_months) to merge into state.features (D-09, D-10, D-14).

    Only what can be determined: an empty result is ``{}`` so the merge never clobbers a profile written
    elsewhere. Never sets ``fraud_watch`` (D-15) and never raises."""
    out: dict[str, Any] = {}
    try:
        from prism.read import features

        profile = features.build_profile(state, rules)
        if profile:
            out["profile"] = profile
        months = features.build_deposit_months(state)
        if months:
            out["deposit_months"] = months
        fills = read_vl.fills_for(state)
        if fills:
            out["vl_fills"] = fills
    except Exception:  # noqa: BLE001
        return {}
    return out
