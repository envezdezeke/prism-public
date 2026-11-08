"""Routing: every gate resolves from rules data, with no person id in code."""
from __future__ import annotations

import copy
import json

import pytest
from test_move_support import (
    ASSIGNMENTS, PEOPLE, ROUTING, STAGES, at, make_rules, make_state,
)

from prism.engine.config import Rules
from prism.move import routing

NOW = at("2026-03-02T10:00")


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def test_ids_in_handles_trailing_period_and_order(rules):
    assert routing.ids_in("round-robin ivy.two / ivy.one.", rules.teams) == ["ivy.two", "ivy.one"]
    assert routing.ids_in("nobody here", rules.teams) == []
    assert routing.ids_in(None, rules.teams) == []


@pytest.mark.parametrize(
    "when,expected",
    [
        ("2026-03-02T17:59", "cora.credit"),  # day before the window: in
        ("2026-03-03T07:59", "cora.credit"),  # a minute before the start: in
        ("2026-03-03T08:00", "cal.credit"),  # start edge inclusive: out
        ("2026-03-05T12:00", "cal.credit"),
        ("2026-03-06T18:00", "cal.credit"),  # end edge inclusive: out
        ("2026-03-06T18:01", "cora.credit"),  # a minute after: in
    ],
)
def test_out_of_office_window_edges(rules, when, expected):
    st = make_state("Bolt Inc")
    assert routing.route_gate("credit_decision", st, rules, at(when)) == expected


def test_covering_route_names_primary_and_return_date(rules):
    st = make_state("Bolt Inc")
    r = routing.route_detail("credit_decision", st, rules, at("2026-03-04T09:00"))
    assert (r.to, r.primary, r.covering) == ("cal.credit", "cora.credit", True)
    assert r.until == at("2026-03-06T18:00")
    plain = routing.route_detail("credit_decision", make_state("Acme Co"), rules, at("2026-03-04T09:00"))
    assert plain.covering is False and plain.to == "cal.credit"


def test_backup_chain_skips_backups_who_are_also_out(tmp_path):
    people = copy.deepcopy(PEOPLE)
    for p in people:
        if p["id"] == "cal.credit":
            p["notes"] = "OUT OF OFFICE 2026-03-04 through 2026-03-04. Backup: Cass."
    r = make_rules(tmp_path, people=people)
    st = make_state("Bolt Inc")
    assert routing.route_gate("credit_decision", st, r, at("2026-03-04T09:00")) == "cass.credit"
    # a missing time means 00:00 start and 23:59 end
    assert routing.is_out("cal.credit", r.teams, at("2026-03-04T23:59"))
    assert not routing.is_out("cal.credit", r.teams, at("2026-03-05T00:00"))


def test_everyone_out_returns_the_primary(tmp_path):
    people = copy.deepcopy(PEOPLE)
    for p in people:
        if p["id"] in ("cora.credit", "cal.credit", "cass.credit"):
            p["notes"] = "OUT OF OFFICE 2026-03-03 08:00 through 2026-03-06 18:00."
    r = make_rules(tmp_path, people=people)
    assert routing.route_gate("credit_decision", make_state("Bolt Inc"), r, at("2026-03-04T09:00")) == "cora.credit"


@pytest.mark.parametrize(
    "amount,expected",
    [
        (249999.99, "dele.gate"),
        (250000.00, "chair.gate"),
        (250000, "chair.gate"),
        (249999, "dele.gate"),
        ("250,000.00", "chair.gate"),
        ("$249,999.99", "dele.gate"),
        (1_000_000, "chair.gate"),
        (10, "dele.gate"),
    ],
)
def test_committee_split_is_in_integer_cents(rules, amount, expected):
    st = make_state(amount=amount)
    assert routing.route_gate("committee_approval", st, rules, NOW) == expected


def test_committee_unknown_amount_routes_nowhere(rules):
    st = make_state()
    del st.facts["amount_requested"]
    assert routing.route_gate("committee_approval", st, rules, NOW) is None
    st2 = make_state(amount="not money")
    assert routing.route_gate("committee_approval", st2, rules, NOW) is None


def test_rm_funding_and_fraud_routes(rules):
    st = make_state("Bolt Inc")
    assert routing.route_gate("commitment_signoff", st, rules, NOW) == "rm.beta"
    assert routing.route_gate("funding_release", st, rules, NOW) == "close.er"
    assert routing.route_gate("fraud_hold", st, rules, NOW) == "fraud.an"
    assert rules.teams.backup_of("fraud.an") == "fraud.head"
    assert routing.rm_of(st, rules) == "rm.beta"


def test_unknown_gate_and_empty_rules(rules):
    st = make_state()
    assert routing.route_gate("no_such_gate", st, rules, NOW) is None
    empty = Rules()
    for g in ("credit_decision", "committee_approval", "fraud_hold", "funding_release"):
        assert routing.route_gate(g, st, empty, NOW) is None


def test_round_robin_alternates_by_first_seen_order(rules):
    a, b, c = make_state("Acme Co"), make_state("Bolt Inc"), make_state("Cedar Ltd")
    owners = [routing.stage_owner("Intake", s, rules, NOW) for s in (a, b, c)]
    assert owners == ["ivy.one", "ivy.two", "ivy.one"]
    assert routing.stage_owner("Intake", b, rules, NOW) == "ivy.two"  # stable on repeat
    assert routing.stage_owner("Intake", a, rules, NOW) == "ivy.one"


def test_stage_owner_follows_gate_and_routing_table(rules):
    st = make_state("Bolt Inc")
    assert routing.stage_owner("Credit", st, rules, at("2026-03-04T09:00")) == "cal.credit"
    assert routing.stage_owner("Product", st, rules, NOW) == "prod.one"
    assert routing.stage_owner("Committed", st, rules, NOW) == "rm.beta"
    assert routing.stage_owner("Closed", st, rules, NOW) == "close.er"


def test_renamed_people_produce_renamed_routes(tmp_path):
    """Proves no person id is in code: rename everyone and every route follows."""
    mapping = {p["id"]: f"zz{i}.person" for i, p in enumerate(PEOPLE)}

    def rename(obj):
        text = json.dumps(obj)
        for old, new in mapping.items():
            text = text.replace(old, new)
        return json.loads(text)

    r = make_rules(tmp_path, stages=rename(STAGES), people=rename(PEOPLE), routing=rename(ROUTING),
                   assignments=rename(ASSIGNMENTS))
    st = make_state("Bolt Inc", amount=300000)
    assert routing.route_gate("committee_approval", st, r, NOW) == mapping["chair.gate"]
    assert routing.route_gate("credit_decision", st, r, at("2026-03-04T09:00")) == mapping["cal.credit"]
    assert routing.route_gate("fraud_hold", st, r, NOW) == mapping["fraud.an"]
    assert routing.route_gate("funding_release", st, r, NOW) == mapping["close.er"]
    assert routing.stage_owner("Product", st, r, NOW) == mapping["prod.one"]
