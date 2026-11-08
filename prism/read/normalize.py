"""Normalization helpers for Read: money, dates, EIN, names, addresses (J13).

Everything is deterministic and explicit: no fuzzy or edit-distance matching. Names and
addresses reuse ``prism.fraud.canon`` so Read and Fraud agree on canonical forms. Unknown or
unparseable input returns None, never a guess.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from prism.contract import format_sim_time
from prism.engine.money import to_cents
from prism.engine.visibility import SIM_TZ
from prism.fraud import canon
from prism.read.settings import get_config

_SUFFIX_MULT = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "million": 1_000_000}
_SUF = r"(k|thousand|million|mm|m)"
_NUM = r"(\d[\d,]*(?:\.\d+)?|\.\d+)"
_MONEY_RE = re.compile(rf"^\(?\s*(-)?\s*\$?\s*(-)?\s*{_NUM}\s*{_SUF}?\s*\)?$", re.IGNORECASE)
_RANGE_RE = re.compile(
    rf"(?<![\w.,-])\$?\s*{_NUM}\s*{_SUF}?\b\s*(?:-|–|—|to)\s*\$?\s*{_NUM}\s*{_SUF}?(?![\w-])",
    re.IGNORECASE,
)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_US_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)")
_MDY_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})\s*([AaPp]\.?[Mm]\.?)?")


def _decimal(num: str) -> Optional[Decimal]:
    try:
        return Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None


def _to_cents_decimal(d: Decimal, mult: int = 1) -> int:
    return to_cents(d * mult)


def money_to_cents(value: Any) -> Optional[int]:
    """"$1,234.50" -> 123450; "$1.2M", "1.2 million", "400k"; "(12.40)" and "-$12.40" are negative."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return to_cents(value)
        except ValueError:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    m = _MONEY_RE.match(text)
    if not m:
        return None
    d = _decimal(m.group(3))
    if d is None:
        return None
    mult = _SUFFIX_MULT.get((m.group(4) or "").casefold(), 1)
    cents = _to_cents_decimal(d, mult)
    negative = bool(m.group(1) or m.group(2)) or (text.startswith("(") and text.endswith(")"))
    return -cents if negative else cents


def money_range_to_cents(text: Any) -> Optional[tuple[int, int]]:
    """"approx 400-500k" -> (40000000, 50000000): a suffix on one end applies to both. Single figure: None."""
    if not isinstance(text, str):
        return None
    m = _RANGE_RE.search(text)
    if not m:
        return None
    lo, s1, hi, s2 = m.group(1), m.group(2), m.group(3), m.group(4)
    dlo, dhi = _decimal(lo), _decimal(hi)
    if dlo is None or dhi is None:
        return None
    mlo = _SUFFIX_MULT.get((s1 or s2 or "").casefold(), 1)
    mhi = _SUFFIX_MULT.get((s2 or s1 or "").casefold(), 1)
    a, b = _to_cents_decimal(dlo, mlo), _to_cents_decimal(dhi, mhi)
    return (a, b) if a <= b else None


def _valid_date(y: int, mo: int, d: int) -> Optional[str]:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_date(text: Any) -> Optional[str]:
    """ISO, M/D/YY (20YY), MM/DD/YYYY, "November 3, 2026", "3 Nov 2026" -> "YYYY-MM-DD", else None."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    m = _ISO_RE.search(s)
    if m:
        return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _US_RE.search(s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        return _valid_date(y, int(m.group(1)), int(m.group(2)))
    m = _MDY_RE.search(s)
    if m and m.group(1).casefold() in _MONTHS:
        return _valid_date(int(m.group(3)), _MONTHS[m.group(1).casefold()], int(m.group(2)))
    m = _DMY_RE.search(s)
    if m and m.group(2).casefold() in _MONTHS:
        return _valid_date(int(m.group(3)), _MONTHS[m.group(2).casefold()], int(m.group(1)))
    return None


def dates_in(text: Any) -> list[str]:
    """Every date written in ``text`` (ISO, M/D/YY, "May 1, 2026", "1 May 2026") as ISO, in reading order."""
    if not isinstance(text, str):
        return []
    spans: list[tuple[int, int, str]] = []
    for rx, ok in ((_ISO_RE, None), (_US_RE, None), (_MDY_RE, 1), (_DMY_RE, 2)):
        for m in rx.finditer(text):
            if ok is not None and m.group(ok).casefold() not in _MONTHS:
                continue
            spans.append((m.start(), m.end(), m.group(0)))
    out: list[str] = []
    last_end = -1
    for start, end, tok in sorted(spans):
        if start < last_end:  # overlaps a date already taken
            continue
        iso = parse_date(tok)
        if iso:
            out.append(iso)
            last_end = end
    return out


def _none_markers() -> set[str]:
    return {str(x).strip().casefold() for x in (get_config().raw.get("none_markers") or [])}


def strip_none(text: Any) -> Optional[str]:
    """Stripped text, or None for empty values and the configured "not provided" markers."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if s.casefold() in _none_markers() or set(s) <= set("-–—_. "):
        return None
    return s


def needed_by_iso(text: Any) -> Optional[str]:
    """"Friday 2026-11-06 by 3:00 PM" -> "2026-11-06T15:00-06:00". A date with no time, or no date: None (D-15)."""
    if not isinstance(text, str) or strip_none(text) is None:
        return None
    day = parse_date(text)
    if day is None:
        return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    mer = (m.group(3) or "").replace(".", "").casefold()
    if mer:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if mer == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    y, mo, d = (int(x) for x in day.split("-"))
    return format_sim_time(datetime(y, mo, d, hour, minute, tzinfo=SIM_TZ))


def norm_ein(text: Any) -> Optional[str]:
    """Nine digits -> "NN-NNNNNNN"; anything else None."""
    if not isinstance(text, str):
        return None
    digits = "".join(c for c in text if c.isdigit())
    return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else None


def last4(text: Any) -> Optional[str]:
    """Last four digits of an account-like string, or None."""
    if not isinstance(text, str):
        return None
    digits = "".join(c for c in text if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


# ---------------------------------------------------------------- names

def name_base(name: Any) -> str:
    """Canonical name without one trailing legal suffix ("Acme, L.L.C." -> "acme")."""
    return canon.split_legal_suffix(name if isinstance(name, str) else None).base


def legal_names_match(a: Any, b: Any) -> bool:
    ba, bb = name_base(a), name_base(b)
    return bool(ba) and ba == bb


_NICK_CACHE: dict[int, dict[str, frozenset[int]]] = {}


def _nick_index() -> dict[str, frozenset[int]]:
    groups = get_config().raw.get("nicknames") or []
    key = id(groups)
    if key not in _NICK_CACHE:
        idx: dict[str, set[int]] = {}
        for i, group in enumerate(groups):
            for tok in group or []:
                idx.setdefault(canon.canon_text(str(tok)), set()).add(i)
        _NICK_CACHE.clear()
        _NICK_CACHE[key] = {k: frozenset(v) for k, v in idx.items()}
    return _NICK_CACHE[key]


def _tok_match(a: str, b: str, idx: dict[str, frozenset[int]]) -> bool:
    if a == b:
        return True
    return bool(idx.get(a, frozenset()) & idx.get(b, frozenset()))


def person_names_match(a: Any, b: Any) -> bool:
    """Every token of the shorter name matches a distinct token of the longer, directly or through a
    nickname group; two or more tokens must match when both names have two or more tokens."""
    ta = canon.canon_text(a if isinstance(a, str) else None).split()
    tb = canon.canon_text(b if isinstance(b, str) else None).split()
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    idx = _nick_index()
    used: set[int] = set()
    matched = 0
    for tok in short:
        for j, other in enumerate(long_):
            if j not in used and _tok_match(tok, other, idx):
                used.add(j)
                matched += 1
                break
        else:
            return False
    if len(short) >= 2 and matched < 2:
        return False
    return True


# ---------------------------------------------------------------- addresses

_SUITE_WORDS = {"ste", "suite", "unit", "apt", "apartment"}
_ZIP4_RE = re.compile(r"\b(\d{5})-\d{4}\b")


def address_key(addr: Any) -> str:
    """Order-insensitive canonical address: ZIP+4 dropped, #/ste/suite/unit/apt are one marker."""
    if not isinstance(addr, str) or not addr.strip():
        return ""
    s = _ZIP4_RE.sub(r"\1", addr).replace("#", " suite ")
    tokens = []
    for t in canon.canon_text(s).split():
        t = "suite" if t in _SUITE_WORDS else canon.ADDRESS_ABBREVIATIONS.get(t, t)
        tokens.append(t)
    return " ".join(sorted(set(tokens)))


def addresses_match(a: Any, b: Any) -> bool:
    ka = address_key(a)
    return bool(ka) and ka == address_key(b)
