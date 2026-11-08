"""Canonical keys for entities (owner: Phase 5, locked canonicalization).

Casefold; periods and apostrophes are dropped (so "L.L.C." is "llc"); "&" becomes the word
"and"; every other non-alphanumeric run is one space. Names split into a base and ONE
trailing legal suffix, so F3 can tell "same base, different suffix" from "same name".
Every function is idempotent: ``canon_value(k, canon_value(k, x)) == canon_value(k, x)``.
The tables below are generic English and US address words, never sample data.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

_NON_ALNUM = re.compile(r"[\W_]+")

# Trailing legal-suffix tokens and their normal form.
LEGAL_SUFFIXES: dict[str, str] = {
    "llc": "llc", "inc": "inc", "incorporated": "inc", "corp": "corp", "corporation": "corp",
    "co": "co", "company": "co", "ltd": "ltd", "limited": "ltd", "lp": "lp", "llp": "llp",
    "pllc": "pllc", "pc": "pc",
}

ADDRESS_ABBREVIATIONS: dict[str, str] = {
    "st": "street", "ave": "avenue", "rd": "road", "dr": "drive", "blvd": "boulevard",
    "ln": "lane", "ste": "suite", "hwy": "highway",
    "pkwy": "parkway", "ct": "court", "pl": "place", "cir": "circle", "trl": "trail", "fwy": "freeway",
    # Directionals: "W Spring Valley Rd" and "West Spring Valley Road" are one address.
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}

_ZIP4 = re.compile(r"\b(\d{5})-\d{4}\b")


def _fold_accents(t: str) -> str:
    """"José Núñez" -> "jose nunez": strip combining marks after NFKD decomposition."""
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


@dataclass(frozen=True)
class NameKey:
    base: str
    suffix: str
    full: str


def canon_text(s: Optional[str]) -> str:
    """Casefolded, punctuation-insensitive, whitespace-collapsed text ("" for None)."""
    if s is None:
        return ""
    t = _fold_accents(str(s)).casefold()
    for ch in (".", "'", "’", "`"):
        t = t.replace(ch, "")
    t = t.replace("&", " and ")
    return _NON_ALNUM.sub(" ", t).strip()


def split_legal_suffix(s: Optional[str]) -> NameKey:
    """Split a name into base and one trailing legal suffix (never leaving an empty base)."""
    tokens = canon_text(s).split()
    suffix = ""
    if len(tokens) >= 2 and tokens[-1] in LEGAL_SUFFIXES:
        suffix = LEGAL_SUFFIXES[tokens[-1]]
        tokens = tokens[:-1]
    base = " ".join(tokens)
    return NameKey(base=base, suffix=suffix, full=f"{base} {suffix}" if suffix else base)


def canon_phone(s: Optional[str]) -> str:
    digits = "".join(c for c in str(s or "") if c.isdigit())
    return digits[1:] if len(digits) == 11 and digits[0] == "1" else digits


def canon_address(s: Optional[str]) -> str:
    text = _ZIP4.sub(r"\1", str(s)) if s is not None else None
    tokens = {ADDRESS_ABBREVIATIONS.get(t, t) for t in canon_text(text).split()}
    return " ".join(sorted(tokens))


def canon_account(s: Optional[str]) -> str:
    return "".join(c for c in str(s or "").casefold() if c.isalnum())


def canon_value(kind: str, raw: Optional[str]) -> str:
    """Canonical key for an entity of ``kind`` ("" when empty). Vendor returns the full key."""
    if raw is None or not str(raw).strip():
        return ""
    if kind == "phone":
        return canon_phone(raw)
    if kind == "address":
        return canon_address(raw)
    if kind == "remit_account":
        return canon_account(raw)
    if kind == "vendor":
        return split_legal_suffix(raw).full
    return canon_text(raw)
