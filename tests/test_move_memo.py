"""Credit memo: exact numbers, boundaries, grades, text rules and committee wiring."""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from test_move_support import GOOD_PROFILE, at, fact, kinds, make_rules, make_state, persona, tick

from prism.move import credit_memo as cm
from prism.move.runtime import get_runtime
from prism.move.settings import get_config
from prism.move.sla import stage_limit_hours

RATE = Decimal("0.09")
NOW = at("2026-03-02T10:00")


@pytest.fixture
def rules(tmp_path):
    return make_rules(tmp_path)


def deal(amount=50000, product="Working Capital Loan", term=24, stated=700000, name="Cedar Lane Bakery LLC",
         needed_by=None, **profile):
    prof = dict(GOOD_PROFILE)
    prof.update(profile)
    st = make_state(amount=amount, product=product, term=term, legal_name=name, needed_by=needed_by, profile=prof)
    st.facts["annual_revenue_stated"] = fact(stated)
    return st


# ------------------------------------------------------------ the template's own numbers


def test_yearly_payment_numbers():
    assert cm.monthly_payment_cents(5_000_000, 24, RATE) == 228_424
    assert cm.yearly_payment_cents(5_000_000, 24, "Working Capital Loan", RATE) == 2_741_088  # about 27,411
    assert cm.yearly_payment_cents(6_000_000, None, "Revolving Line of Credit", RATE) == 540_000
    assert cm.yearly_payment_cents(120_000_000, None, "Revolving Line of Credit", RATE) == 10_800_000
    assert abs(cm.yearly_payment_cents(3_500_000, 12, "Term Loan", RATE) - 3_673_000) <= 100
    assert abs(cm.yearly_payment_cents(32_000_000, 84, "Term Loan", RATE) - 6_178_200) <= 100
    assert cm.monthly_payment_cents(1_200, 12, Decimal(0)) == 100  # zero rate


def test_worked_example_reproduces_the_template(rules):
    memo = cm.build_credit_memo(deal(), rules, NOW)
    assert (memo.grade, memo.grade_name, memo.points) == (2, "Satisfactory", 1)
    assert memo.numbers["coverage"] == "2.71" and memo.numbers["revenue_support"] == "1.03"
    assert memo.numbers["years_in_business"] == 9
    assert memo.short_text.split("\n") == [
        "Cedar Lane Bakery LLC requests $50,000 Working Capital Loan over 24 months for working capital. "
        "Risk grade 2 (Satisfactory): recommend approval, subject to conditions.",
        "Coverage 2.71x at an assumed 9.0%; revenue supported 1.03x by bank deposits; 9 years in business; new borrower.",
        "All documents and checks clear.",
        "Full memo on the deal page. Please approve or decline.",
    ]
    assert "(covers 31 months of payments)" in memo.full_text
    assert "Yearly payment on this loan: $27,411 (24-month amortizing)." in memo.full_text
    assert "about $74,400 a year. Coverage 2.71x." in memo.full_text
    assert memo.inputs_missing == []


CASES = [
    # product, amount $, term, yearly $, coverage, relationship, expected grade
    ("Revolving Line of Credit", 60_000, None, 5_400, "25.6", "returning_clean", 1),
    ("Revolving Line of Credit", 1_200_000, None, 108_000, "14.5", "returning_clean", 1),
    ("Term Loan", 35_000, 12, 36_730, "1.48", "returning_clean", 2),
    ("Term Loan", 320_000, 84, 61_782, "5.5", "new", 2),
]


@pytest.mark.parametrize("product,amount,term,yearly,cov,rel,grade", CASES)
def test_doc_sample_cases(rules, product, amount, term, yearly, cov, rel, grade):
    ni = int(Fraction(cov) * yearly * 100)
    st = deal(amount=amount, product=product, term=term or 12, stated=500_000,
              deposits_annualized_cents=50_000_000, net_income_fy_cents=ni, net_income_ytd_cents=None,
              formation_date="2010-01-01", relationship={"status": rel})
    memo = cm.build_credit_memo(st, rules, NOW)
    assert abs(memo.numbers["yearly_payment_cents"] - yearly * 100) <= 100  # within a dollar
    assert memo.grade == grade
    assert memo.numbers["coverage"] == f"{Decimal(cov):.2f}"


# ------------------------------------------------------------ boundaries


def test_coverage_boundaries_take_the_better_band():
    c = get_config().memo
    f = Fraction
    assert [cm.points_coverage(f(x), c) for x in ("2.0", "1.9999", "1.5", "1.4999", "1.25", "1.2499", "1.0", "0.9999", "-1")] == [
        0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_revenue_and_time_boundaries():
    c = get_config().memo
    f = Fraction
    assert [cm.points_revenue(f(x), c) for x in ("1.1", "1.1001", "1.5", "1.5001")] == [0, 1, 1, None]
    assert [cm.points_tib(f(x), c) for x in ("5", "4.99", "2", "1.99")] == [0, 1, 1, 2]


def test_points_to_grade_bands():
    c = get_config().memo
    got = [cm.grade_for(p, c) for p in (0, 1, 2, 3, 4, 5, 6, 7, 12)]
    assert [g for g, _ in got] == [1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert got[0][1] == "Strong" and got[-1][1] == "Weak"


def test_watch_tier_is_at_least_grade_three(rules):
    st = deal(relationship={"status": "returning_clean"})
    assert cm.build_credit_memo(st, rules, NOW).grade == 1
    st.features["fraud_watch"] = "shared bookkeeper with two recent loans"
    memo = cm.build_credit_memo(st, rules, NOW)
    assert (memo.grade, memo.grade_name) == (3, "Acceptable")
    assert "Fraud Ops review before funding." in memo.conditions
    assert "Cross-deal fraud screen: watch: shared bookkeeper with two recent loans." in memo.full_text


def test_grade_four_names_the_driver_and_the_fix(rules):
    st = deal(amount=100_000, term=36, stated=600_000, deposits_annualized_cents=50_000_000,
              net_income_ytd_cents=None, net_income_fy_cents=4_197_600, formation_date="2023-03-01")
    memo = cm.build_credit_memo(st, rules, NOW)
    assert memo.grade == 4 and memo.recommendation == "Not recommended as submitted."
    assert "Coverage 1.10x: a longer term or a smaller amount would bring it above 1.25x" in memo.full_text
    assert "Committee to consider a longer term, a smaller amount or extra collateral." in memo.conditions
    assert "Borrower to explain the gap between stated revenue and deposits." in memo.conditions
    assert "not recommended as submitted" in memo.short_text


def test_revenue_above_the_top_band_is_grade_five_with_the_reason(rules):
    st = deal(stated=900_000, deposits_annualized_cents=50_000_000)
    memo = cm.build_credit_memo(st, rules, NOW)
    assert (memo.grade, memo.grade_name) == (5, "Weak") and memo.recommendation == "Not recommended."
    assert "REV_INFLATION" in memo.full_text


# ------------------------------------------------------------ text rules


def test_missing_inputs_read_not_provided_and_lower_confidence(rules):
    st = make_state(profile=None, amount=80000)
    memo = cm.build_credit_memo(st, rules, NOW)
    text = memo.full_text
    assert "not provided" in text and "CONFIDENCE" in text and "Lower confidence" in text
    assert memo.inputs_missing and memo.grade == 5  # missing tests score at their maximum
    assert len(memo.short_text.split("\n")) == 4


def test_no_braces_and_no_none_in_any_memo_text(rules):
    for st in (deal(), make_state(profile=None), deal(needed_by="2026-03-06T15:00-06:00", product="Equipment Loan")):
        memo = cm.build_credit_memo(st, rules, NOW)
        for text in (memo.full_text, memo.short_text, *memo.conditions):
            assert "{" not in text and "}" not in text and "None" not in text


def test_short_version_is_exactly_four_lines_with_deadline_clauses(rules):
    st = deal(needed_by="2026-03-06T15:00-06:00")
    memo = cm.build_credit_memo(st, rules, NOW, answer_by=at("2026-03-03T15:00"))
    lines = memo.short_text.split("\n")
    assert len(lines) == 4
    assert lines[2] == "All documents and checks clear; needed by Fri 03/06 3:00 PM CT."
    assert lines[3] == "Full memo on the deal page. Please approve or decline by Tue 03/03 3:00 PM CT."
    assert "Fund before the deadline: funds required by Fri 03/06 3:00 PM CT." in memo.conditions


def test_triggered_conditions(rules):
    memo = cm.build_credit_memo(deal(product="Revolving Line of Credit", term=None), rules, NOW)
    assert len(memo.conditions) == 5
    assert any(c.startswith("Annual review and renewal") for c in memo.conditions)
    eq = cm.build_credit_memo(deal(product="Equipment Loan", quote={"number": "Q1", "vendor": "Vend Co", "total_cents": 4_500_000,
                                                                     "remit_last4": "9876"}), rules, NOW)
    assert any("First lien on the financed equipment" in c for c in eq.conditions)
    assert any("phone callback" in c for c in eq.conditions)
    assert "NOT in history: callback required." in eq.full_text
    ok = cm.build_credit_memo(deal(product="Equipment Loan", vendor_verified=True), rules, NOW)
    assert not any("phone callback" in c for c in ok.conditions)


def test_braces_in_a_legal_name_pass_through(rules):
    st = deal(name="Brace {0} {x} Co")
    memo = cm.build_credit_memo(st, rules, NOW)
    assert "Brace {0} {x} Co" in memo.full_text and "Brace {0} {x} Co" in memo.short_text


def test_memo_to_dict_is_plain_data(rules):
    memo = cm.build_credit_memo(deal(), rules, NOW)
    d = cm.credit_memo_to_dict(memo)
    assert d["grade"] == 2 and d["numbers"]["coverage"] == "2.71" and isinstance(d["conditions"], list)
    assert cm.credit_memo_to_dict(None) is None


# ------------------------------------------------------------ committee wiring


def test_memo_is_written_on_entering_committee_and_is_the_request(rules):
    st = deal()
    assert cm.get_credit_memo(st) is None
    with persona("never"):
        tick(st, rules, "2026-03-02T09:40")
        assert st.stage == "Credit" and cm.get_credit_memo(st) is None  # no memo before Committee
        (idx, _), = [(i, r) for i, r in enumerate(st.human_requests) if r.type == "credit_decision"]
    with persona("fast"):
        st2 = deal()
        tick(st2, rules, "2026-03-02T09:50")
    memo = cm.get_credit_memo(st2)
    assert st2.stage == "Committee" and memo is not None
    req = [r for r in st2.human_requests if r.type == "committee_approval"][0]
    assert req.to == "dele.gate"
    assert req.message == memo.short_text + "\nUnder the $250,000 delegated limit."
    assert len(req.message.split("\n")) == 5


def test_chair_route_has_no_delegated_sentence_and_longer_limit(rules):
    st = deal(amount=300_000)
    with persona("fast"):
        tick(st, rules, "2026-03-02T09:50")
    req = [r for r in st.human_requests if r.type == "committee_approval"][0]
    assert req.to == "chair.gate" and "delegated limit" not in req.message
    assert len(req.message.split("\n")) == 4
    assert stage_limit_hours(st, rules, "Committee") == 48 and get_runtime(st).limit_hours == 48
    d = deal(amount=249_999.99)
    assert stage_limit_hours(d, rules, "Committee") == 24
    assert stage_limit_hours(d, rules, "Credit") == 48 and stage_limit_hours(d, rules, "Nope") is None


def test_amount_boundary_in_committee_message(rules):
    for amount, who in ((249_999.99, "dele.gate"), (250_000.00, "chair.gate")):
        st = deal(amount=amount)
        with persona("fast"):
            tick(st, rules, "2026-03-02T09:50")
        assert [r.to for r in st.human_requests if r.type == "committee_approval"] == [who]
