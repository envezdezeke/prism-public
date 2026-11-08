"""Generated bank statements for the Read tests (owned by plan 03-03). Nothing is committed as a binary.

PDF layout mirrors a typical bank export: summary block (label, value), a Date/Description/Credit/Debit/
Balance table, a ``Page X of N`` marker at the bottom of every physical page. Everything is invented.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from tests.pdfgen import Line, write_csv, write_pdf

Txn = tuple[int, str, int]  # (day of month, description, signed cents: credit positive)

DEFAULT_TXNS: tuple[Txn, ...] = (
    (2, "ACH CREDIT EXAMPLE CUSTOMER", 450_000),
    (3, "ACH DEBIT EXAMPLE LANDLORD", -120_000),
    (5, "CARD SALES DEPOSIT", 310_050),
    (9, "PAYROLL EXAMPLE SERVICE", -205_075),
    (14, "ACH CREDIT EXAMPLE CLIENT", 275_025),
    (20, "UTILITY EXAMPLE POWER", -35_040),
    (27, "CARD SALES DEPOSIT", 198_500),
)


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def month_days(period: str, day: int) -> str:
    y, m = period.split("-")
    return f"{int(m):02d}/{day:02d}/{y}"


def totals(txns: Sequence[Txn]) -> tuple[int, int]:
    credits = sum(c for _d, _s, c in txns if c > 0)
    debits = -sum(c for _d, _s, c in txns if c < 0)
    return credits, debits


def closing_of(opening: int, txns: Sequence[Txn]) -> int:
    return opening + sum(c for _d, _s, c in txns)


def _row_cells(period: str, day: int, desc: str, cents: int, balance: int) -> list[tuple[int, str]]:
    credit = money(cents) if cents > 0 else ""
    debit = money(-cents) if cents < 0 else ""
    return [(72, month_days(period, day)), (150, desc), (380, credit), (450, debit), (520, money(balance))]


def statement_pages(
    period: str = "2026-05",
    txns: Sequence[Txn] = DEFAULT_TXNS,
    *,
    opening: int = 1_000_000,
    holder: str = "Example Holder LLC",
    bank: str = "Example Test Bank",
    account: str = "4321",
    n_pages: int = 1,
    markers: Optional[Sequence[Optional[tuple[int, int]]]] = None,
    printed_opening: Optional[int] = None,
    printed_credits: Optional[int] = None,
    printed_debits: Optional[int] = None,
    printed_closing: Optional[int] = None,
    summary: bool = True,
    printed_amounts: Optional[dict[int, int]] = None,
) -> list[list[Line]]:
    """Pages of lines for ``write_pdf``. ``markers`` is one (x, n) or None per physical page."""
    credits, debits = totals(txns)
    closing = closing_of(opening, txns)
    y, m = period.split("-")
    start, end = f"{y}-{m}-01", f"{y}-{m}-28"
    head: list[Line] = [bank, "Business Checking Statement", ""]
    if summary:
        head += [
            [(72, "Account holder"), (300, holder)],
            [(72, "Account"), (300, f"Business Checking ...{account}")],
            [(72, "Statement period"), (300, f"{start} to {end}")],
            [(72, "Opening balance"), (300, money(opening if printed_opening is None else printed_opening))],
            [(72, "Total deposits & credits"), (300, money(credits if printed_credits is None else printed_credits))],
            [(72, "Total withdrawals & debits"), (300, money(debits if printed_debits is None else printed_debits))],
            [(72, "Closing balance"), (300, money(closing if printed_closing is None else printed_closing))],
            "",
        ]
    else:
        head += [[(72, "Statement period"), (300, f"{start} to {end}")], ""]
    header: Line = [(72, "Date"), (150, "Description"), (380, "Credit"), (450, "Debit"), (520, "Balance")]
    rows: list[Line] = []
    bal = opening
    for i, (day, desc, cents) in enumerate(txns):
        bal += cents
        shown = (printed_amounts or {}).get(i, cents)  # an edited row amount; the balance column is untouched
        rows.append(_row_cells(period, day, desc, shown, bal))
    per = max(1, -(-len(rows) // max(1, n_pages)))
    pages: list[list[Line]] = []
    for i in range(max(1, n_pages)):
        chunk = rows[i * per:(i + 1) * per]
        page: list[Line] = (list(head) if i == 0 else []) + [header] + chunk
        mk = markers[i] if markers is not None and i < len(markers) else (i + 1, n_pages)
        if mk is not None:
            page += ["", f"Page {mk[0]} of {mk[1]}"]
        pages.append(page)
    return pages


def write_statement(folder: Path, name: str, period: str = "2026-05", txns: Sequence[Txn] = DEFAULT_TXNS,
                    **kw) -> int:
    """Write one statement PDF; returns the real closing balance (for chaining the next month)."""
    opening = kw.get("opening", 1_000_000)
    write_pdf(Path(folder) / name, statement_pages(period, txns, **kw))
    return closing_of(opening, txns)


def write_statement_csv(folder: Path, name: str, period: str = "2026-05", txns: Sequence[Txn] = DEFAULT_TXNS,
                        *, opening: int = 1_000_000, bad_balance_at: Optional[int] = None,
                        bank: str = "Example Test Bank", holder: str = "Example Holder LLC",
                        account: str = "7720") -> int:
    """CSV export: free-text first line, header row, signed amounts, M/D/YY dates, running balance."""
    y, m = period.split("-")
    rows: list = [f"{bank} - acct ...{account} - {holder}", ["Posted Date", "Details", "Amount", "Running Bal"]]
    bal = opening
    for i, (day, desc, cents) in enumerate(txns):
        bal += cents
        shown = bal + (777 if bad_balance_at == i else 0)
        rows.append([f"{int(m)}/{day}/{y[2:]}", desc, f"{cents / 100:.2f}", f"{shown / 100:.2f}"])
    write_csv(Path(folder) / name, rows)
    return bal
