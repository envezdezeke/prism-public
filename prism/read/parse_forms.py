"""Label-anchored parsers for the eight non-application, non-statement document classes.

guarantor_id, ein_letter, certificate_of_formation, tax_return, form_7004, request_letter,
equipment_quote and financial_statements. Every parser is ``fn(dt: DocText, doc) -> Parsed* | None``,
registered with ``docmodel.register``. Generic by design (D-01): fields are found by label text from
config/read.yaml in plain or layout text, never by position or by a deal's template.

Privacy and safety: the preparer PTIN, the SSN and the driver-license class are never stored. Document
text is never used as a pattern; the only compiled regexes come from config or are fixed here. Parsers
return None (or a dataclass with None fields) on garbage and never raise.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from prism.read import docmodel
from prism.read.docmodel import (
    Parsed7004, ParsedCert, ParsedFinancials, ParsedId, ParsedIrs, ParsedLetter, ParsedQuote, ParsedTax,
)
from prism.read.labels import extract_labeled
from prism.read.normalize import money_to_cents, norm_ein, parse_date, strip_none
from prism.read.settings import get_config
from prism.read.text import DocText

_EIN_RE = re.compile(r"(?<!\d)\d{2}-\d{7}(?!\d)")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?!\d)")
_STATE_CODE_RE = re.compile(r"^[A-Za-z]{2}\s+(\S.*)$")
_MONEY_TOKEN_RE = re.compile(r"\(?-?\s?\$?\s?-?\d[\d,]*(?:\.\d+)?\)?")
_NAME_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z .,'\-]*$")
_WS = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"\d{6,}")


# ---------------------------------------------------------------- shared helpers

def _text(dt: DocText) -> str:
    return dt.plain or dt.layout or ""


def _stops() -> tuple[str, ...]:
    return tuple(get_config().raw.get("stops") or ())


def _fields(dt: DocText, doc_class: str, wrap: Iterable[str] = ()) -> dict[str, str]:
    """First non-empty value per label field. Fields in ``wrap`` may continue on a second line; every
    other field is exactly one line. Plain text first, layout text fills what plain missed."""
    labels = get_config().labels.get(doc_class, {})
    stops = _stops()
    wrap = set(wrap)
    out: dict[str, str] = {}
    for n, text in enumerate((dt.plain, dt.layout)):
        if not text or (n == 1 and dt.layout == dt.plain):
            continue
        one = extract_labeled(text, labels, stops, max_lines=1)
        two = extract_labeled(text, labels, stops, max_lines=2)
        for field in labels:
            v = two.get(field) if field in wrap else one.get(field)
            if v and not out.get(field):
                out[field] = _WS.sub(" ", v).strip()
    return out


def _regex_list(name: str) -> list[re.Pattern[str]]:
    raw = (get_config().raw.get("patterns") or {}).get(name) or []
    raw = [raw] if isinstance(raw, str) else list(raw)
    out = []
    for p in raw:
        try:
            out.append(re.compile(str(p), re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return out


def _first_match(name: str, text: str) -> Optional[re.Match[str]]:
    for rx in _regex_list(name):
        m = rx.search(text[:200_000])
        if m:
            return m
    return None


def _tax_year(value: Optional[str], text: str) -> Optional[int]:
    """Tax year from a labeled value, else from the config patterns over the text."""
    if value:
        m = re.search(r"(?<!\d)(\d{4})(?!\d)", value)
        if m:
            return int(m.group(1))
    m = _first_match("tax_year", _WS.sub(" ", text))
    return int(m.group(1)) if m else None


def _credentials() -> set[str]:
    return {str(c).casefold().replace(".", "") for c in (get_config().raw.get("credentials") or [])}


def _is_credential(token: str) -> bool:
    return token.strip().casefold().replace(".", "") in _credentials()


def _is_parenthesized(s: str) -> bool:
    t = s.strip()
    return t.startswith("(") and t.endswith(")")


def _split_person_firm(text: str, paren_firm: bool) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """``(name, credential, firm)`` for "Name, CPA (Firm)", "Name (Firm)", "Name, CPA, Firm", "Name, Firm".

    With ``paren_firm`` a trailing ``(...)`` is the firm (tax return). Otherwise the parts are comma
    separated and a wholly parenthesised firm such as ``(owner)`` is a placeholder and becomes None."""
    s = _WS.sub(" ", text).strip()
    firm: Optional[str] = None
    head = s
    if paren_firm:
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", s)
        if m:
            head, firm = m.group(1), (m.group(2).strip() or None)
    parts = [p.strip() for p in head.split(",") if p.strip()]
    if not parts:
        return None, None, None
    name, rest, cred = parts[0], parts[1:], None
    if rest and _is_credential(rest[0]):
        cred, rest = rest[0], rest[1:]
    if firm is None and rest:
        firm = ", ".join(rest)
    if firm is not None and (_is_parenthesized(firm) or strip_none(firm) is None):
        firm = None
    return strip_none(name), cred, firm


def _split_separators(text: str) -> list[str]:
    seps = [str(s) for s in (get_config().raw.get("separators") or [])]
    if not seps:
        return [text.strip()]
    rx = re.compile("|".join(re.escape(s) for s in seps))
    return [p.strip() for p in rx.split(text) if p.strip()]


# ---------------------------------------------------------------- guarantor_id

def _nospace(s: str) -> str:
    return _WS.sub("", s).casefold()


def _id_from_ocr(dt: DocText) -> ParsedId:
    """OCR-style text: uppercase, spaces often missing, ``DL``/``DOB``/``EXP`` glued to their values."""
    cfg = (get_config().raw.get("id_ocr") or {})
    dob_p = tuple(cfg.get("dob_prefixes") or ())
    exp_p = tuple(cfg.get("exp_prefixes") or ())
    dl_p = tuple(cfg.get("dl_prefixes") or ())
    lab_p = tuple(cfg.get("label_prefixes") or ())
    noise = tuple(cfg.get("noise") or ())
    lines = [_WS.sub(" ", ln).strip() for ln in _text(dt).splitlines() if ln.strip()]
    if not lines:
        return ParsedId(readable=False, via_ocr=True)

    def starts(line: str, prefixes: tuple[str, ...]) -> bool:
        ns = _nospace(line)
        return any(ns.startswith(p) for p in prefixes)

    dob = dob_i = exp = dl = None
    dl_i = None
    for i, ln in enumerate(lines):
        if dob is None and starts(ln, dob_p):
            dob, dob_i = parse_date(ln), i
        elif exp is None and starts(ln, exp_p):
            exp = parse_date(ln)
        elif dl is None and starts(ln, dl_p):
            m = _DIGITS_RE.search(_nospace(ln))
            if m:
                dl, dl_i = m.group(0), i

    def is_name(i: int, ln: str) -> bool:
        ns = _nospace(ln)
        return bool(_NAME_LINE_RE.match(ln) and sum(c.isalpha() for c in ln) >= 3
                    and not any(n in ns for n in noise)
                    and not starts(ln, dob_p + exp_p + dl_p + lab_p))

    end = dob_i if dob_i is not None else len(lines)
    start = dl_i + 1 if dl_i is not None else 0
    name_i = next((i for i in range(start, end) if is_name(i, lines[i])), None)
    if name_i is None:
        name_i = next((i for i in range(0, end) if is_name(i, lines[i])), None)
    name = lines[name_i] if name_i is not None else None
    addr_lines: list[str] = []
    if name_i is not None:
        for i in range(name_i + 1, end):
            ln = lines[i]
            ns = _nospace(ln)
            if any(n in ns for n in noise) or starts(ln, exp_p + dl_p + lab_p):
                continue
            addr_lines.append(ln)
    address = strip_none(" ".join(addr_lines))
    found = sum(1 for v in (name, dob, dl) if v)
    return ParsedId(name=name, dob=dob, address=address, dl_number=dl, expires=exp,
                    readable=found >= 2, via_ocr=True)


def parse_guarantor_id(dt: DocText, doc: Any = None) -> Optional[ParsedId]:
    via_ocr = dt.kind == "image" or bool(dt.ocr_used)
    if via_ocr:
        return _id_from_ocr(dt)
    f = _fields(dt, "guarantor_id", wrap=("name", "address"))
    dl_raw = strip_none(f.get("dl"))
    if dl_raw:
        m = _STATE_CODE_RE.match(dl_raw)
        dl_raw = (m.group(1) if m else dl_raw).strip()
    dl = dl_raw if dl_raw and sum(c.isalnum() for c in dl_raw) >= 5 else None
    name = strip_none(f.get("name"))
    dob = parse_date(f.get("dob"))
    found = sum(1 for v in (name, dob, dl) if v)
    if not found and not f:
        return None
    return ParsedId(name=name, dob=dob, address=strip_none(f.get("address")), dl_number=dl,
                    expires=parse_date(f.get("expires")), readable=found >= 2, via_ocr=False)


# ---------------------------------------------------------------- ein letter, certificate

def parse_ein_letter(dt: DocText, doc: Any = None) -> Optional[ParsedIrs]:
    f = _fields(dt, "ein_letter", wrap=("name",))
    ein = norm_ein(f.get("ein"))
    if ein is None:
        m = _EIN_RE.search(_text(dt))
        ein = norm_ein(m.group(0)) if m else None
    name = strip_none(f.get("name"))
    notice = parse_date(f.get("notice_date"))
    if name is None and ein is None and notice is None:
        return None
    return ParsedIrs(name=name, ein=ein, notice_date=notice)


def parse_certificate(dt: DocText, doc: Any = None) -> Optional[ParsedCert]:
    f = _fields(dt, "certificate_of_formation",
                wrap=("entity_name", "principal_office", "registered_agent", "registered_office"))
    out = ParsedCert(
        entity_name=strip_none(f.get("entity_name")), entity_type=strip_none(f.get("entity_type")),
        file_number=strip_none(f.get("file_number")), effective_date=parse_date(f.get("effective_date")),
        principal_office=strip_none(f.get("principal_office")),
        registered_agent=strip_none(f.get("registered_agent")),
        registered_office=strip_none(f.get("registered_office")),
    )
    return out if any(v is not None for v in vars(out).values()) else None


# ---------------------------------------------------------------- tax return, form 7004

def parse_tax_return(dt: DocText, doc: Any = None) -> Optional[ParsedTax]:
    f = _fields(dt, "tax_return", wrap=("name", "preparer"))
    text = _text(dt)
    ein = norm_ein(f.get("ein"))
    if ein is None:
        m = _EIN_RE.search(text)
        ein = norm_ein(m.group(0)) if m else None
    pname = pcred = pfirm = None
    if strip_none(f.get("preparer")):
        pname, pcred, pfirm = _split_person_firm(f["preparer"], paren_firm=True)
    out = ParsedTax(
        name=strip_none(f.get("name")), ein=ein, tax_year=_tax_year(f.get("tax_year"), text),
        gross_receipts_cents=money_to_cents(f.get("gross")),
        ordinary_income_cents=money_to_cents(f.get("income")),
        preparer_name=pname, preparer_firm=pfirm, preparer_credential=pcred,
        date_filed=parse_date(f.get("filed")),
    )
    return out if any(v is not None for v in vars(out).values()) else None


def parse_form_7004(dt: DocText, doc: Any = None) -> Optional[Parsed7004]:
    f = _fields(dt, "form_7004", wrap=("name",))
    text = _text(dt)
    ein = norm_ein(f.get("ein"))
    if ein is None:
        m = _EIN_RE.search(text)
        ein = norm_ein(m.group(0)) if m else None
    out = Parsed7004(
        name=strip_none(f.get("name")), ein=ein, tax_year=_tax_year(f.get("tax_year"), text),
        extended_due_date=parse_date(f.get("due")), status=strip_none(f.get("status")),
    )
    return out if any(v is not None for v in vars(out).values()) else None


# ---------------------------------------------------------------- request letter

def parse_request_letter(dt: DocText, doc: Any = None) -> Optional[ParsedLetter]:
    flat = _WS.sub(" ", _text(dt))
    for rx in _regex_list("request_amount"):
        m = rx.search(flat[:200_000])
        if m:
            token = m.group(1).replace(" ", "")
            cents = money_to_cents(token)
            if cents is not None and cents > 0:
                return ParsedLetter(amount_cents=cents, amount_text=m.group(1).strip())
    return None


# ---------------------------------------------------------------- equipment quote

def parse_equipment_quote(dt: DocText, doc: Any = None) -> Optional[ParsedQuote]:
    text = _text(dt)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    number = None
    rx_n = _regex_list("quote_number")
    if rx_n:
        m = rx_n[0].search(lines[0])
        number = strip_none(m.group(1)) if m else None
    vendor = None
    if len(lines) > 1:
        segs = _split_separators(lines[1])
        vendor = strip_none(segs[0]) if segs else None
    f = _fields(dt, "equipment_quote", wrap=("remit",))
    remit = f.get("remit") or ""
    bank = last_four = None
    if remit:
        segs = _split_separators(remit)
        bank = strip_none(segs[0]) if segs else None
        rx_a = _regex_list("quote_account")
        m = rx_a[0].search(remit) if rx_a else None
        last_four = m.group(1) if m else None
    out = ParsedQuote(
        number=number, vendor=vendor, remit_bank=bank, remit_last4=last_four,
        total_cents=money_to_cents(f.get("total")), quote_date=parse_date(f.get("quote_date")),
    )
    return out if any(v is not None for v in vars(out).values()) else None


# ---------------------------------------------------------------- financial statements

def _fs_cfg() -> dict[str, Any]:
    return get_config().raw.get("financial_statements") or {}


def _row_synonyms(field: str) -> list[str]:
    syns = get_config().labels.get("financial_statements", {}).get(field) or []
    return sorted((_WS.sub(" ", str(s)).strip().casefold() for s in syns), key=lambda s: -len(s))


def _money_tokens(rest: str) -> list[int]:
    out = []
    for tok in _MONEY_TOKEN_RE.findall(rest):
        c = money_to_cents(tok.replace(" ", ""))
        if c is not None:
            out.append(c)
    return out


def _row_values(text: str, field: str, n: int) -> list[int]:
    """Up to ``n`` money values of a table row: the same line (layout or "Label: v1 v2") or the next lines."""
    syns = _row_synonyms(field)
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        folded = _WS.sub(" ", raw).strip().casefold()
        for syn in syns:
            if not folded.startswith(syn):
                continue
            rest = raw.strip()[len(syn):]
            if rest and (rest[0].isalnum()):
                continue  # "Revenue growth" is not the "Revenue" row
            rest = rest.lstrip(" :\t")
            if rest:
                vals = _money_tokens(rest)
                if vals:
                    return vals[:n]
                continue
            vals = []
            for nxt in lines[i + 1:i + 1 + 6]:
                if not nxt.strip():
                    continue
                v = money_to_cents(nxt.strip())
                if v is None:
                    break
                vals.append(v)
                if len(vals) >= n:
                    break
            if vals:
                return vals
    return []


def _split_statements(text: str) -> tuple[str, str]:
    """(profit and loss text, balance sheet text) split at the first balance-sheet phrase line."""
    phrases = [str(p).casefold() for p in _fs_cfg().get("bs_phrases") or []]
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if any(p in ln.casefold() for p in phrases):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    return text, text


def _month_index(token: str) -> Optional[int]:
    months = [str(m).casefold() for m in _fs_cfg().get("months") or []]
    t = token.casefold()[:3]
    return months.index(t) + 1 if t in months else None


def split_prepared_by(prepared: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """``(name, credential, firm, phone)`` of a "Prepared by" value such as
    "Name, CPA, Firm · 214-555-0100", "Name, Firm · phone" or "Self-prepared, (owner)"."""
    if not prepared:
        return None, None, None, None
    phone = None
    body = prepared
    pm = _PHONE_RE.search(prepared)
    if pm:
        phone = pm.group(0).strip()
        body = prepared[:pm.start()]
    body = body.rstrip(" ,;:-" + "".join(str(x) for x in (get_config().raw.get("separators") or [])))
    segs = _split_separators(body)
    head = segs[0] if segs else body
    name, cred, firm = _split_person_firm(head, paren_firm=False)
    return name, cred, firm, phone


def parse_financials(dt: DocText, doc: Any = None) -> Optional[ParsedFinancials]:
    cfg = _fs_cfg()
    text = _text(dt)
    layout = dt.layout or text
    folded = text.casefold()
    has_pl = any(str(p).casefold() in folded for p in cfg.get("pl_phrases") or [])
    has_bs = any(str(p).casefold() in folded for p in cfg.get("bs_phrases") or [])
    f = _fields(dt, "financial_statements", wrap=("prepared_by",))
    prepared = strip_none(f.get("prepared_by"))
    pname, _pcred, pfirm, pphone = split_prepared_by(prepared)
    pl_text, bs_text = _split_statements(layout)
    pl_plain, bs_plain = _split_statements(dt.plain or layout)

    def row(field: str, n: int, pl: bool) -> list[int]:
        vals = _row_values(pl_text if pl else bs_text, field, n)
        return vals or _row_values(pl_plain if pl else bs_plain, field, n)

    # column order: which of the two value columns is the fiscal year and which is year to date
    flat = _WS.sub(" ", pl_text or text)
    fy_m = _first_match("fy", flat)
    ytd_m = _first_match("ytd", flat)
    fy_year = int(fy_m.group(1)) if fy_m else None
    ytd_months = None
    if ytd_m:
        a, b = _month_index(ytd_m.group(1)), _month_index(ytd_m.group(2))
        if a and b and b >= a:
            ytd_months = b - a + 1

    def split_cols(vals: list[int]) -> tuple[Optional[int], Optional[int]]:
        if not vals:
            return None, None
        fy_pos = fy_m.start() if fy_m else None
        ytd_pos = ytd_m.start() if ytd_m else None
        if len(vals) >= 2:
            if fy_pos is not None and ytd_pos is not None and ytd_pos < fy_pos:
                return vals[1], vals[0]
            return vals[0], vals[1]
        if ytd_pos is not None and fy_pos is None:
            return None, vals[0]
        return vals[0], None

    rev_fy, rev_ytd = split_cols(row("revenue", 2, True))
    ni_fy, ni_ytd = split_cols(row("net_income", 2, True))

    def single(field: str) -> Optional[int]:
        vals = row(field, 1, False)
        return vals[0] if vals else None

    balance_date = None
    phrases = [str(p).casefold() for p in cfg.get("bs_phrases") or []]
    for ln in text.splitlines():
        if any(p in ln.casefold() for p in phrases):
            balance_date = parse_date(ln)
            if balance_date:
                break
    out = ParsedFinancials(
        has_pl=has_pl, has_balance_sheet=has_bs, prepared_by=prepared, preparer_name=pname,
        preparer_firm=pfirm, preparer_phone=pphone, fy_year=fy_year, revenue_fy_cents=rev_fy,
        net_income_fy_cents=ni_fy, revenue_ytd_cents=rev_ytd, net_income_ytd_cents=ni_ytd,
        ytd_months=ytd_months, balance_date=balance_date, cash_cents=single("cash"),
        receivables_cents=single("receivables"), liabilities_cents=single("liabilities"),
    )
    informative = [v for k, v in vars(out).items() if k not in ("has_pl", "has_balance_sheet") and v is not None]
    return out if (informative or has_pl or has_bs) else None


def _guard(fn):
    """Wrap a parser so it can never raise (docmodel.parsed also guards, tests call parsers directly)."""
    def wrapped(dt: DocText, doc: Any = None):
        try:
            return fn(dt, doc)
        except Exception:  # noqa: BLE001
            return None
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


PARSERS = {
    "guarantor_id": _guard(parse_guarantor_id),
    "ein_letter": _guard(parse_ein_letter),
    "certificate_of_formation": _guard(parse_certificate),
    "tax_return": _guard(parse_tax_return),
    "form_7004": _guard(parse_form_7004),
    "request_letter": _guard(parse_request_letter),
    "equipment_quote": _guard(parse_equipment_quote),
    "financial_statements": _guard(parse_financials),
}
for _cls, _fn in PARSERS.items():
    docmodel.register(_cls, _fn)
