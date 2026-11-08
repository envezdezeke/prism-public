"""Label engine: same value from next-line, colon and column layouts."""
from __future__ import annotations

from prism.read.labels import extract_labeled, extract_labeled_all, split_sections

LABELS = {"legal": ["Legal business name", "Legal name"], "amount": ["Amount requested"],
          "name": ["Name"], "bizname": ["Name of business"], "use": ["Use of proceeds"],
          "term": ["Requested term"]}


def test_three_layouts_give_the_same_value():
    nxt = "Legal business name\nAcme Widgets LLC\nAmount requested\n$5,000.00\n"
    colon = "Legal business name: Acme Widgets LLC\nAmount requested: $5,000.00\n"
    cols = "Legal business name                 Acme Widgets LLC\nAmount requested                     $5,000.00\n"
    want = {"legal": "Acme Widgets LLC", "amount": "$5,000.00"}
    for t in (nxt, colon, cols):
        assert extract_labeled(t, LABELS) == want


def test_wrapped_value_over_two_lines_stops_at_next_label():
    t = "Use of proceeds\nBuy a truck and\ntwo mowers\nRequested term\n60 months\n"
    got = extract_labeled(t, LABELS)
    assert got["use"] == "Buy a truck and two mowers" and got["term"] == "60 months"


def test_prefix_label_never_matches_longer_line():
    t = "Name of business\nACME\nName\nJane Doe\n"
    assert extract_labeled(t, LABELS) == {"bizname": "ACME", "name": "Jane Doe"}
    assert extract_labeled("Name of business   ACME\n", {"name": ["Name"]}) == {}


def test_stops_and_empty_value():
    t = "Requested term\nSection B - more\nAmount requested\n"
    got = extract_labeled(t, LABELS, stops=("Section*",))
    assert got == {"term": "", "amount": ""}


def test_all_values_in_order_and_regex_chars_in_labels_are_literal():
    t = "Name\nAda\nTitle\nOwner\nName\nBea\n"
    assert extract_labeled_all(t, {"name": ["Name"], "t": ["Title"]}, "name") == ["Ada", "Bea"]
    odd = {"x": ["Cost (est.)"]}
    assert extract_labeled("Cost (est.)\n$3\n", odd) == {"x": "$3"}
    assert extract_labeled("Cost est\n$3\n", odd) == {}


def test_split_sections():
    t = "intro\nSection A\na1\nSection B\nb1\nb2\n"
    got = split_sections(t, {"a": [r"^section a"], "b": [r"^section b"]})
    assert got["a"].strip() == "a1" and got["b"].split() == ["b1", "b2"] and got[""].strip() == "intro"
