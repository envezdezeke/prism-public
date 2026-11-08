"""Issue rules configuration (config/read_issues.yaml) and the MISSING_DOC requirement logic.

``yaml.safe_load`` only; ``PRISM_READ_ISSUES_CONFIG`` selects another file. The loaded config is cached by
(path, mtime_ns). The helpers here are pure and use only the standard library for date arithmetic.
Document text never reaches a pattern or a format string.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from prism.engine.config import REPO_ROOT, ConfigError
from prism.read import docmodel

log = logging.getLogger("prism")

DEFAULT_ISSUES_PATH = REPO_ROOT / "config" / "read_issues.yaml"
KINDS = ("single", "signed_single", "guarantors", "statements", "financials", "tax_or_extension", "product_quote")


@dataclass(frozen=True)
class Requirement:
    label: str
    kind: str
    doc_class: str
    stage: str = ""
    extension_class: Optional[str] = None
    when_product: Optional[str] = None


@dataclass(frozen=True)
class IssuesConfig:
    requirements: tuple[Requirement, ...]
    window_months: int
    young_business_years: int
    raw: Mapping[str, Any]


def _fail(msg: str) -> ConfigError:
    return ConfigError(f"read_issues.yaml: {msg}")


def _int(raw: Mapping[str, Any], key: str, low: int = 1) -> int:
    v = raw.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < low:
        raise _fail(f"{key} must be an integer of at least {low}")
    return v


def _build(raw: Any) -> IssuesConfig:
    if not isinstance(raw, dict):
        raise _fail("top level must be a mapping")
    reqs = raw.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        raise _fail("requirements must be a non-empty list")
    out: list[Requirement] = []
    for entry in reqs:
        if not isinstance(entry, dict):
            raise _fail("each requirement must be a mapping")
        label, kind, cls = entry.get("label"), entry.get("kind"), entry.get("doc_class")
        if not isinstance(label, str) or not isinstance(cls, str) or kind not in KINDS:
            raise _fail("each requirement needs a string label, a string doc_class and a known kind")
        for opt in ("stage", "extension_class", "when_product"):
            if entry.get(opt) is not None and not isinstance(entry[opt], str):
                raise _fail(f"requirement {label}: {opt} must be a string")
        out.append(Requirement(label=label, kind=kind, doc_class=cls, stage=entry.get("stage") or "",
                               extension_class=entry.get("extension_class"),
                               when_product=entry.get("when_product")))
    return IssuesConfig(requirements=tuple(out), window_months=_int(raw, "statement_window_months"),
                        young_business_years=_int(raw, "young_business_years"), raw=raw)


def load_issues_config(path: Optional[Path | str] = None) -> IssuesConfig:
    p = Path(path) if path is not None else Path(os.environ.get("PRISM_READ_ISSUES_CONFIG") or DEFAULT_ISSUES_PATH)
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise _fail(f"cannot read {p.name}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise _fail(f"invalid YAML in {p.name}") from e
    return _build(raw)


_CACHE: dict[tuple[str, int], IssuesConfig] = {}


def get_issues_config() -> IssuesConfig:
    """The cached config for the current path (re-read when the file's mtime changes)."""
    p = Path(os.environ.get("PRISM_READ_ISSUES_CONFIG") or DEFAULT_ISSUES_PATH)
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError as e:
        raise _fail(f"cannot read {p.name}: {e.strerror}") from e
    cfg = _CACHE.get(key)
    if cfg is None:
        cfg = load_issues_config(p)
        _CACHE.clear()
        _CACHE[key] = cfg
    return cfg


# ---------------------------------------------------------------- dates

def _iso(text: Any) -> Optional[date]:
    if not isinstance(text, str):
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def statement_window(ref: date, months: Optional[int] = None) -> list[str]:
    """The ``months`` full calendar months before the month of ``ref``, oldest first, as ``YYYY-MM``."""
    n = get_issues_config().window_months if months is None else months
    cur = _month_index(ref)
    return [_month_label(cur - k) for k in range(n, 0, -1)]


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def business_age_ok_for_7004(formation_iso: Any, ref: date, years: Optional[int] = None) -> bool:
    """True only when the formation date is known and the business is younger than ``years`` at ``ref``."""
    formed = _iso(formation_iso)
    if formed is None:
        return False
    n = get_issues_config().young_business_years if years is None else years
    return formed > _years_before(ref, n)


def _latest(state: Any, doc_class: str) -> Any:
    got = docmodel.parsed_all(state, doc_class)
    return got[-1][1] if got else None


def _statement_month(doc: Any) -> Optional[str]:
    """The month classify read from the content, else the month the statement parser found."""
    if getattr(doc, "period", None):
        return doc.period
    ps = docmodel.parsed(doc)
    period = getattr(ps, "period", None)
    return period if isinstance(period, str) and period else None


def latest_application(state: Any) -> Optional[docmodel.ParsedApplication]:
    app = _latest(state, "application")
    return app if isinstance(app, docmodel.ParsedApplication) else None


def reference_date(state: Any) -> Optional[date]:
    """Application date, else signature date, else the date of the deal's last arrival."""
    app = latest_application(state)
    if app is not None:
        for text in (app.application_date, app.signature_date):
            d = _iso(text)
            if d is not None:
                return d
    at = getattr(state, "last_arrival_at", None)
    if at is None:
        arrivals = [d.arrive_at for d in getattr(state, "docs", ()) if getattr(d, "arrive_at", None)]
        at = max(arrivals) if arrivals else None
    return at.date() if at is not None else None


def formation_date(state: Any) -> Optional[str]:
    """The application's formation date, else the certificate's effective date."""
    app = latest_application(state)
    if app is not None and _iso(app.formation_date) is not None:
        return app.formation_date
    cert = _latest(state, "certificate_of_formation")
    eff = getattr(cert, "effective_date", None)
    return eff if _iso(eff) is not None else None


# ---------------------------------------------------------------- requirement expansion

def _docs_of(state: Any, doc_class: str) -> list[Any]:
    return [d for d in state.docs if d.doc_class == doc_class]


def _guarantor_count(state: Any) -> int:
    app = latest_application(state)
    return max(1, len(app.guarantors)) if app is not None else 1


def _financials_ok(state: Any, doc_class: str) -> bool:
    docs = _docs_of(state, doc_class)
    if not docs:
        return False
    parsed = docmodel.parsed_all(state, doc_class)
    if len(parsed) < len(docs):
        return True  # a document with no parser result counts by class presence
    for attr in ("has_pl", "has_balance_sheet"):
        values = [getattr(p, attr, None) for _d, p in parsed]
        if all(v is False for v in values):
            return False
    return True


def required_labels(state: Any) -> list[str]:
    """Issue targets (``application``, ``bank_statement_2026-06``, ...) still missing, in config order."""
    cfg = get_issues_config()
    out: list[str] = []
    ref = reference_date(state)
    app = latest_application(state)
    for req in cfg.requirements:
        present = _docs_of(state, req.doc_class)
        if req.kind == "single":
            if not present:
                out.append(req.label)
        elif req.kind == "signed_single":
            if not present or (app is not None and app.signed is False):
                out.append(req.label)
        elif req.kind == "guarantors":
            if len(present) < _guarantor_count(state):
                out.append(req.label)
        elif req.kind == "statements":
            if ref is None:
                continue
            have = {p for p in (_statement_month(d) for d in present) if p}
            out.extend(f"{req.label}_{m}" for m in statement_window(ref, cfg.window_months) if m not in have)
        elif req.kind == "financials":
            if not _financials_ok(state, req.doc_class):
                out.append(req.label)
        elif req.kind == "tax_or_extension":
            if present:
                continue
            ext = _docs_of(state, req.extension_class) if req.extension_class else []
            young = ref is not None and business_age_ok_for_7004(formation_date(state), ref,
                                                                 cfg.young_business_years)
            if not (ext and young):
                out.append(req.label)
        elif req.kind == "product_quote":
            product = getattr(app, "product", None) if app is not None else None
            if (isinstance(product, str) and req.when_product
                    and product.strip().casefold() == req.when_product.casefold() and not present):
                out.append(req.label)
    return out
