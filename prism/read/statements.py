"""Bank statement parser (PDF and CSV), page gaps and footing helpers (READ-06, READ-08).

Registered with the docmodel registry for ``bank_statement``. Summary values are found by label
synonyms (config/read_issues.yaml); transaction rows are lines that start with a date and end with
money tokens (amount, then running balance). The row sign is the sign of the change in running
balance (the first row compares with the opening balance) and is cross-checked against the one
other amount printed on the row. ``signs_verified`` is True only when every row parsed and the sign of
each is known. A row whose printed amount disagrees with its balance change is NOT ambiguity: it is a
footing failure (``row_amount``, STMT_TAMPER). A statement that is not verified is never used to raise
STMT_TAMPER. Money is integer cents,
dates ISO. The parser never raises. Document text is untrusted: every pattern is a bounded fixed regex or a
config pattern, and text is never used as a pattern or a format string.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from prism.read import docmodel
from prism.read.classify import statement_period
from prism.read.docmodel import ParsedStatement, Txn
from prism.read.labels import extract_labeled
from prism.read.normalize import dates_in, last4, money_to_cents, parse_date
from prism.read.requirements import get_issues_config
from prism.read.text import DocText, page_markers, pdf_tables

log = logging.getLogger("prism")

_MONEY_TOKEN = re.compile(r"^\(?-?\$?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?-?$|^\(?-?\$?\d+(?:\.\d{1,2})?\)?-?$")
_MONEY_IN_TEXT = re.compile(r"\(?-?\$?\d[\d,]{0,15}\.\d{2}\)?")
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_US_DATE = re.compile(r"(?<!\d)\d{1,2}/\d{1,2}/\d{2,4}(?!\d)")
_BANNER_HEAD = r"^\s*(.{1,80}?)\s+-\s+(?:"
_BANNER_TAIL = r")\W{0,4}(\d{2,})\s+-\s+(.{1,80})$"
_SCAN_LINES = 20000


def _cfg() -> dict[str, Any]:
    return dict(get_issues_config().raw.get("statement") or {})


def _labels(cfg: dict[str, Any]) -> dict[str, list[str]]:
    return {k: [str(s) for s in v] for k, v in (cfg.get("labels") or {}).items() if isinstance(v, list)}


def _date_res(cfg: dict[str, Any]) -> list[re.Pattern[str]]:
    out = []
    for pat in cfg.get("row_date_patterns") or []:
        try:
            out.append(re.compile(str(pat)))
        except re.error:
            continue
    return out


def _first_money(text: str) -> Optional[int]:
    m = _MONEY_IN_TEXT.search(text or "")
    return money_to_cents(m.group(0)) if m else None


def _dates_in(text: str) -> list[str]:
    return dates_in(text or "")


# ---------------------------------------------------------------- page markers

def _markers(dt: DocText, doc: Any) -> list[tuple[int, int]]:
    meta = getattr(doc, "meta", None) or {}
    raw = meta.get("page_markers")
    if raw is None:
        return page_markers(dt.plain)
    return [(int(a), int(b)) for a, b in raw]


def _page_numbers(markers: Sequence[tuple[int, int]]) -> tuple[tuple[int, ...], Optional[int]]:
    good = [(x, n) for x, n in markers if 1 <= x <= n <= 999]
    if not good:
        return (), None
    total = max(n for _x, n in good)
    return tuple(sorted({x for x, n in good if n == total})), total


def missing_pages(ps: Any) -> tuple[int, ...]:
    """Page numbers in 1..N not found, from the ``Page X of N`` markers only. No markers: nothing missing."""
    total = getattr(ps, "pages_total", None)
    if not total:
        return ()
    found = set(getattr(ps, "pages_found", ()) or ())
    return tuple(p for p in range(1, int(total) + 1) if p not in found)


def is_complete(ps: Any) -> bool:
    return not missing_pages(ps)


def marker_gap(copies: Sequence[Sequence[tuple[int, int]]]) -> tuple[int, ...]:
    """Missing page numbers for one document group (all copies of a month, or of a class).

    Each copy is its list of ``(x, n)`` markers. A copy with no markers is no evidence of a gap, and a month
    with any complete copy raises nothing; otherwise the gap is 1..N minus the numbers found across copies.
    """
    if not copies:
        return ()
    found: set[int] = set()
    total = 0
    for markers in copies:
        nums, n = _page_numbers(list(markers))
        if n is None:
            return ()
        if all(p in nums for p in range(1, n + 1)):
            return ()
        found.update(nums)
        total = max(total, n)
    return tuple(p for p in range(1, total + 1) if p not in found)


# ---------------------------------------------------------------- parse: PDF

def _find_rows(lines: Sequence[str], date_res: list[re.Pattern[str]], max_rows: int, max_chars: int,
               opening: Optional[int]) -> tuple[list[Txn], bool]:
    """Transaction rows from text lines. Returns (rows, all_rows_ok)."""
    rows: list[Txn] = []
    ok = True
    prev = opening
    for ln in lines:
        if len(rows) >= max_rows:
            ok = False
            break
        if len(ln) > max_chars:
            continue
        toks = ln.split()
        if len(toks) < 2 or not any(rx.match(toks[0]) for rx in date_res):
            continue
        date = parse_date(toks[0])
        money: list[int] = []
        j = len(toks)
        while j > 1 and _MONEY_TOKEN.match(toks[j - 1]) and "." in toks[j - 1] and len(money) < 3:
            j -= 1
            money.append(j)
        if not money:
            continue  # not a transaction line
        if len(money) < 2 or date is None:
            ok = False
            continue
        bal_tok, amt_tok = toks[money[0]], toks[money[1]]
        amount, balance = money_to_cents(amt_tok), money_to_cents(bal_tok)
        if amount is None or balance is None:
            ok = False
            continue
        amount = abs(amount)
        desc = " ".join(toks[1:money[-1]])[:120]
        if prev is None:
            ok = False
            signed = amount
        else:
            delta = balance - prev
            signed = amount if delta >= 0 else -amount
            # abs(delta) != amount is not ambiguity: the row parsed fully, so footing_errors reports it
        prev = balance
        rows.append(Txn(date=date, description=desc or None, amount_cents=signed, balance_cents=balance))
    return rows, ok


def _table_rows(path: Any, cfg: dict[str, Any], opening: Optional[int]) -> tuple[list[Txn], bool]:
    """Fallback: rows from pdfplumber tables with a recognisable header."""
    words = {k: [str(w).casefold() for w in v] for k, v in (cfg.get("table_words") or {}).items()}

    def col(header: Sequence[str], key: str) -> Optional[int]:
        for i, cell in enumerate(header):
            c = (cell or "").casefold()
            if any(w in c for w in words.get(key, ())):
                return i
        return None

    rows: list[Txn] = []
    ok = True
    prev = opening
    max_rows = int(cfg.get("max_rows") or 2000)
    for table in pdf_tables(path) or ():
        header = next((r for r in table[:3] if col(r, "date") is not None and col(r, "balance") is not None), None)
        if header is None:
            continue
        ci = {k: col(header, k) for k in ("date", "balance", "credit", "debit", "amount", "description")}
        for r in table:
            if r is header or len(rows) >= max_rows:
                continue
            date = parse_date(r[ci["date"]]) if ci["date"] is not None and ci["date"] < len(r) else None
            if date is None:
                continue
            cell = lambda k: r[ci[k]] if ci[k] is not None and ci[k] < len(r) else ""  # noqa: E731
            balance = money_to_cents(cell("balance").replace(" ", ""))
            cr, db = money_to_cents(cell("credit").replace(" ", "")), money_to_cents(cell("debit").replace(" ", ""))
            amt = money_to_cents(cell("amount").replace(" ", ""))
            amount = abs(cr) if cr else (abs(db) if db else (abs(amt) if amt else None))
            if balance is None or amount is None:
                ok = False
                continue
            if prev is None:
                ok, signed = False, amount
            else:
                delta = balance - prev
                signed = amount if delta >= 0 else -amount
            prev = balance
            rows.append(Txn(date=date, description=(cell("description") or "").strip()[:120] or None,
                            amount_cents=signed, balance_cents=balance))
    return rows, ok


def _parse_pdf(dt: DocText, doc: Any, cfg: dict[str, Any]) -> Optional[ParsedStatement]:
    layout = "\n".join(dt.pages) if dt.pages else (dt.layout or dt.plain)
    labels = _labels(cfg)
    got: dict[str, str] = {}
    for text in (layout, dt.plain):
        for k, v in extract_labeled(text or "", labels, max_lines=1).items():
            if v and not got.get(k):
                got[k] = v
    opening = _first_money(got.get("opening", ""))
    credits = _first_money(got.get("credits", ""))
    debits = _first_money(got.get("debits", ""))
    closing = _first_money(got.get("closing", ""))
    lines = (layout or "").splitlines()[:_SCAN_LINES]
    rows, ok = _find_rows(lines, _date_res(cfg), int(cfg.get("max_rows") or 2000),
                          int(cfg.get("max_line_chars") or 300), opening)
    if not rows:
        rows, ok = _table_rows(getattr(doc, "abs_path", None), cfg, opening)
    summary_seen = any(v is not None for v in (opening, credits, debits, closing))
    if not rows and not summary_seen:
        return None
    dates = _dates_in(got.get("period", ""))
    start = dates[0] if dates else None
    end = dates[-1] if dates else None
    period = getattr(doc, "period", None) or (end[:7] if end else None)
    found, total = _page_numbers(_markers(dt, doc))
    if closing is None and rows:
        closing = rows[-1].balance_cents
    return ParsedStatement(
        source="pdf", period=period, period_start=start, period_end=end,
        holder=(got.get("holder") or "").strip()[:120] or None, account_last4=last4(got.get("account", "")),
        opening_cents=opening, closing_cents=closing, total_credits_cents=credits, total_debits_cents=debits,
        txns=tuple(rows), pages_found=found, pages_total=total, signs_verified=bool(ok and rows),
    )


# ---------------------------------------------------------------- parse: CSV

def _csv_columns(row: Sequence[str], cfg: dict[str, Any]) -> Optional[dict[str, int]]:
    words = {k: [str(w).casefold() for w in v] for k, v in (cfg.get("csv_words") or {}).items()}
    cells = [(c or "").casefold() for c in row]
    out: dict[str, int] = {}
    for key in ("balance", "amount", "date", "description"):
        for i, c in enumerate(cells):
            if i in out.values():
                continue
            if any(w in c for w in words.get(key, ())):
                out[key] = i
                break
    return out if {"date", "amount", "balance"} <= set(out) else None


def _parse_csv(dt: DocText, doc: Any, cfg: dict[str, Any]) -> Optional[ParsedStatement]:
    cols: Optional[dict[str, int]] = None
    start = 0
    for i, row in enumerate(dt.rows[:8]):
        cols = _csv_columns(row, cfg)
        if cols:
            start = i + 1
            break
    if not cols:
        return None
    bank = holder = acct = None
    acct_words = "|".join(re.escape(str(w)) for w in (cfg.get("csv_account_words") or ["acct"]))
    banner = re.compile(_BANNER_HEAD + acct_words + _BANNER_TAIL, re.IGNORECASE)
    for row in dt.rows[: start - 1]:
        if len(row) == 1 and (m := banner.match(row[0])):
            bank, acct, holder = m.group(1).strip(), m.group(2)[-4:], m.group(3).strip()
            break
    max_rows = int(cfg.get("max_rows") or 2000)
    rows: list[Txn] = []
    ok = True
    for row in dt.rows[start:]:
        if not any(c.strip() for c in row):
            continue
        if len(rows) >= max_rows:
            ok = False
            break

        def cell(k: str) -> str:
            i = cols[k]
            return row[i] if i < len(row) else ""

        date, amount, balance = parse_date(cell("date")), money_to_cents(cell("amount")), money_to_cents(cell("balance"))
        if date is None or amount is None or balance is None:
            ok = False
            continue
        desc = cell("description").strip()[:120] if "description" in cols else ""
        rows.append(Txn(date=date, description=desc or None, amount_cents=amount, balance_cents=balance))
    if not rows:
        return None
    if len(rows) > 1 and rows[0].date > rows[-1].date:  # newest first: read chronologically
        rows.reverse()
    opening = rows[0].balance_cents - rows[0].amount_cents
    period = getattr(doc, "period", None) or statement_period(dt)
    return ParsedStatement(
        source="csv", period=period, period_start=rows[0].date, period_end=rows[-1].date, bank=bank or None,
        holder=holder or None, account_last4=acct, opening_cents=opening, closing_cents=rows[-1].balance_cents,
        txns=tuple(rows), pages_found=(), pages_total=None, signs_verified=ok,
    )


def parse_statement(dt: DocText, doc: Any) -> Optional[ParsedStatement]:
    """Parse a classified bank statement; None for anything unreadable. Never raises."""
    try:
        cfg = _cfg()
        if dt.kind == "csv":
            return _parse_csv(dt, doc, cfg)
        if dt.kind in ("pdf", "text", "image"):
            return _parse_pdf(dt, doc, cfg)
        return None
    except Exception:  # noqa: BLE001 - never include document text in the log
        log.error("statement parse failed", exc_info=True)
        return None


docmodel.register("bank_statement", parse_statement)


# ---------------------------------------------------------------- helpers

def implied_opening(ps: Any) -> Optional[int]:
    """Opening balance: printed, else implied by the first row (running balance minus its amount)."""
    if getattr(ps, "opening_cents", None) is not None:
        return ps.opening_cents
    txns = getattr(ps, "txns", ()) or ()
    if txns and txns[0].balance_cents is not None and txns[0].amount_cents is not None:
        return txns[0].balance_cents - txns[0].amount_cents
    return None


def closing(ps: Any) -> Optional[int]:
    """Closing balance: printed, else the last running balance."""
    if getattr(ps, "closing_cents", None) is not None:
        return ps.closing_cents
    txns = getattr(ps, "txns", ()) or ()
    return txns[-1].balance_cents if txns else None


def trusted(ps: Any) -> bool:
    """Complete (no missing page) and every row verified: safe to use for tamper checks."""
    return ps is not None and getattr(ps, "signs_verified", None) is True and is_complete(ps)


def footing_errors(ps: Any) -> list[str]:
    """Short reasons the statement does not foot, to the cent. Empty when fine or not trusted."""
    if not trusted(ps):
        return []
    errors: list[str] = []
    txns = ps.txns or ()
    amounts = [t.amount_cents for t in txns if t.amount_cents is not None]
    credits = sum(a for a in amounts if a > 0)
    debits = -sum(a for a in amounts if a < 0)
    if ps.source == "csv" or (ps.total_credits_cents is None and ps.total_debits_cents is None
                              and ps.closing_cents is None):
        prev = None
        for t in txns:
            if prev is not None and t.balance_cents is not None and t.amount_cents is not None:
                if t.balance_cents != prev + t.amount_cents:
                    errors.append("running_balance")
                    break
            prev = t.balance_cents
        return errors
    prev = ps.opening_cents
    for t in txns:  # a row's printed amount must equal its running-balance change
        if prev is not None and t.balance_cents is not None and t.amount_cents is not None                 and abs(t.balance_cents - prev) != abs(t.amount_cents):
            errors.append("row_amount")
            break
        prev = t.balance_cents
    if ps.total_credits_cents is not None and ps.total_credits_cents != credits:
        errors.append("credits_sum")
    if ps.total_debits_cents is not None and ps.total_debits_cents != debits:
        errors.append("debits_sum")
    if ps.opening_cents is not None and ps.closing_cents is not None:
        c = ps.total_credits_cents if ps.total_credits_cents is not None else credits
        d = ps.total_debits_cents if ps.total_debits_cents is not None else debits
        if ps.opening_cents + c - d != ps.closing_cents:
            errors.append("summary_closing")
    return errors


def _doc_markers(doc: Any) -> list[tuple[int, int]]:
    try:
        return [(int(a), int(b)) for a, b in ((getattr(doc, "meta", None) or {}).get("page_markers") or [])]
    except (TypeError, ValueError):
        return []


def missing_page_labels(state: Any) -> list[str]:
    """``bank_statement_<period>`` and class-name targets that have a gap in their ``Page X of N`` markers."""
    out: list[str] = []
    by_period: dict[str, list[list[tuple[int, int]]]] = {}
    for doc in state.docs:
        if doc.doc_class == "bank_statement" and doc.period:
            by_period.setdefault(doc.period, []).append(_doc_markers(doc))
    for period in sorted(by_period):
        if marker_gap(by_period[period]):
            out.append(f"bank_statement_{period}")
    for cls in get_issues_config().raw.get("page_gap_classes") or []:
        copies = [_doc_markers(d) for d in state.docs if d.doc_class == cls]
        if copies and marker_gap(copies):
            out.append(str(cls))
    return out
