"""Normalization helpers: money, dates, EIN, names, addresses (J13)."""
from __future__ import annotations

import pytest

from prism.read.normalize import (
    address_key, addresses_match, last4, legal_names_match, money_range_to_cents, money_to_cents,
    name_base, needed_by_iso, norm_ein, parse_date, person_names_match, strip_none,
)


@pytest.mark.parametrize("text,want", [
    ("$1,234.50", 123450), ("1234", 123400), ("$1.2M", 120000000), ("1.2 million", 120000000),
    ("400k", 40000000), ("(12.40)", -1240), ("-$12.40", -1240), ("$-12.40", -1240),
    ("N/A", None), ("", None), ("abc", None), (True, None), (None, None), ("$", None),
])
def test_money_to_cents(text, want):
    assert money_to_cents(text) == want


def test_money_to_cents_numbers():
    assert money_to_cents(250000) == 25000000
    assert money_to_cents(12.5) == 1250


def test_money_range():
    assert money_range_to_cents("approx 400-500k (annualized, note)") == (40000000, 50000000)
    assert money_range_to_cents("$400,000 to $500,000") == (40000000, 50000000)
    assert money_range_to_cents("$450,000") is None
    assert money_range_to_cents("on 2026-11-06 we") is None


@pytest.mark.parametrize("text,want", [
    ("2026-11-03", "2026-11-03"), ("5/1/26", "2026-05-01"), ("05/01/2026", "2026-05-01"),
    ("November 3, 2026", "2026-11-03"), ("3 Nov 2026", "2026-11-03"), ("garbage", None),
    ("2026-13-45", None), ("", None), (None, None),
])
def test_parse_date(text, want):
    assert parse_date(text) == want


@pytest.mark.parametrize("text,want", [
    ("Friday 2026-11-06 by 3:00 PM (payroll)", "2026-11-06T15:00-06:00"),
    ("2026-11-06 by 12:15 AM", "2026-11-06T00:15-06:00"),
    ("2026-11-06 by 12:15 PM", "2026-11-06T12:15-06:00"),
    ("2026-11-06 by 9:05 am", "2026-11-06T09:05-06:00"),
    ("No specific date", None), ("TBD", None), ("", None), (None, None),
    ("2026-11-06", None), ("Friday 2026-11-06 (payroll)", None),
])
def test_needed_by_iso(text, want):
    assert needed_by_iso(text) == want


@pytest.mark.parametrize("text,want", [
    ("12-3456789", "12-3456789"), ("123456789", "12-3456789"), (" 12 3456789 ", "12-3456789"),
    ("12-345678", None), ("1234567890", None), ("", None), (None, None),
])
def test_norm_ein(text, want):
    assert norm_ein(text) == want


def test_last4():
    assert last4("...1234") == "1234"
    assert last4("acct 98761234") == "1234"
    assert last4("12") is None


def test_strip_none():
    assert strip_none("No specific date") is None
    assert strip_none("  -  ") is None
    assert strip_none("N/A") is None
    assert strip_none("Real Value ") == "Real Value"
    assert strip_none(None) is None


def test_names():
    assert name_base("Acme, L.L.C.") == "acme"
    assert legal_names_match("Acme, L.L.C.", "ACME LLC")
    assert legal_names_match("Acme LLC", "Acme")
    assert legal_names_match("Smith & Sons Inc.", "SMITH AND SONS, INC")
    assert not legal_names_match("Acme LLC", "Apex LLC")
    assert not legal_names_match("", "Acme")


def test_person_names():
    assert person_names_match("Bob Marlowe", "Robert Allen Marlowe")
    assert person_names_match("Bill", "William")
    assert person_names_match("Robert Allen Marlowe", "Bob Marlowe")
    assert not person_names_match("Bob Marlowe", "Rob Marlow")
    assert not person_names_match("Ada Smith", "Ada Jones")
    assert not person_names_match("Ada Smith", "Bea Smith")
    assert not person_names_match("", "Ada")


def test_addresses():
    assert addresses_match("12 Main St Ste 4, Dallas, TX 75001-1234", "12 Main Street #4, Dallas TX 75001")
    assert not addresses_match("12 Main St, Dallas, TX 75001", "14 Main St, Dallas, TX 75001")
    assert address_key("") == ""
