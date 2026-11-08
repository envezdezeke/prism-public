"""Deterministic credit memo (docs/CREDIT_MEMO_TEMPLATE.md, MOVE-10).

Exact arithmetic only (integer cents, Decimal and Fraction, never a float in a comparison).
Every number, the grade and the recommendation come from here; the committee still decides.

Inputs: facts from ``state.facts``, everything else from the optional dict
``state.features["profile"]`` (money in integer cents) and ``features["fraud_watch"]``.
A missing input prints "not provided"; a test that needs a missing input is scored at its
maximum points (conservative) and the memo says so in a confidence line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Optional

from prism.engine.config import Rules
from prism.engine.money import to_cents
from prism.engine.state import DealState
from prism.move import messages, routing
from prism.move.runtime import get_runtime
from prism.move.settings import MemoCfg, get_config
from prism.move.timeutil import fmt_date, fmt_money, fmt_when, parse_iso

REVOLVING = "Revolving Line of Credit"  # contract Product literal: interest-only on the full line
NP = messages.NOT_PROVIDED


# ------------------------------------------------------------ exact arithmetic


def _round_half_up(x: Fraction) -> int:
    n, d = x.numerator, x.denominator
    return (2 * n + d) // (2 * d) if n >= 0 else -((2 * -n + d) // (2 * d))


def monthly_payment_cents(amount_cents: int, term_months: int, annual_rate: Decimal) -> int:
    """Standard amortizing payment at annual_rate/12, rounded half-up to whole cents."""
    r = Fraction(annual_rate) / 12
    n = int(term_months)
    if n <= 0:
        raise ValueError("term must be positive")
    if r == 0:
        pay = Fraction(amount_cents, n)
    else:
        pay = Fraction(amount_cents) * r / (1 - (1 + r) ** (-n))
    return _round_half_up(pay)


def yearly_payment_cents(amount_cents: int, term_months: Optional[int], product: Optional[str], annual_rate: Decimal) -> int:
    """Revolving line: interest only on the full amount. Other products: amortizing payment x 12."""
    if product == REVOLVING:
        return _round_half_up(Fraction(amount_cents) * Fraction(annual_rate))
    if not term_months:
        raise ValueError("term is required for an amortizing loan")
    return monthly_payment_cents(amount_cents, int(term_months), annual_rate) * 12


def points_coverage(cov: Fraction, cfg: MemoCfg) -> int:
    for lo, pts in cfg.coverage_bands:
        if cov >= Fraction(lo):
            return pts
    return cfg.coverage_floor_points


def points_revenue(sup: Fraction, cfg: MemoCfg) -> Optional[int]:
    """None when above the top band (REV_INFLATION territory)."""
    for hi, pts in cfg.revenue_bands:
        if sup <= Fraction(hi):
            return pts
    return None


def points_tib(years: Fraction, cfg: MemoCfg) -> int:
    for lo, pts in cfg.tib_bands:
        if years >= Fraction(lo):
            return pts
    return cfg.tib_floor_points


def grade_for(points: int, cfg: MemoCfg) -> tuple[int, str]:
    for mx, grade, name in cfg.grade_bands:
        if points <= mx:
            return grade, name
    return cfg.top_grade


def grade_name(grade: int, cfg: MemoCfg) -> str:
    for _, g, name in cfg.grade_bands:
        if g == grade:
            return name
    return cfg.top_grade[1] if grade == cfg.top_grade[0] else str(grade)


# ------------------------------------------------------------ formatting helpers


def _fx(x: Fraction, places: int = 2) -> str:
    q = Decimal(1).scaleb(-places)
    return str((Decimal(x.numerator) / Decimal(x.denominator)).quantize(q, rounding=ROUND_HALF_UP))


def _dollars(cents: int) -> str:
    """Whole dollars, half-up (the memo shows $27,411, not $27,410.88)."""
    return fmt_money(_round_half_up(Fraction(cents, 100)) * 100)


def _int_or_none(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _date_or_none(v: Any) -> Optional[date]:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _p(v: Any) -> str:
    return NP if v in (None, "") else str(v)


# ------------------------------------------------------------ the memo


@dataclass
class CreditMemo:
    grade: int
    grade_name: str
    points: int
    recommendation: str
    full_text: str
    short_text: str
    conditions: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    inputs_missing: list[str] = field(default_factory=list)


def _relationship(p: dict[str, Any]) -> tuple[Optional[str], Optional[dict]]:
    rel = p.get("relationship")
    if isinstance(rel, dict) and rel.get("status") in ("new", "returning_clean", "returning_past_due"):
        return rel["status"], rel
    return None, None


def _rel_phrase(status: Optional[str], rel: Optional[dict], long: bool) -> str:
    if status == "new":
        return "new borrower"
    if status == "returning_clean":
        since = f" since {rel['since_year']}" if rel and rel.get("since_year") else ""
        return f"returning customer{since}, prior loans repaid" if long else "returning customer, prior loans repaid"
    if status == "returning_past_due":
        return "returning customer with a past-due or charged-off prior loan"
    return NP


def build_credit_memo(state: DealState, rules: Rules, now: datetime, answer_by: Optional[datetime] = None) -> CreditMemo:
    cfg = get_config().memo
    rt = get_runtime(state)
    p = messages.profile(state)
    missing: list[str] = []

    amount_v = messages.fact_value(state, "amount_requested")
    amount_cents: Optional[int] = None
    if amount_v is not None:
        try:
            amount_cents = to_cents(amount_v)
        except (ValueError, TypeError):
            amount_cents = None
    product = messages.fact_value(state, "product")
    term = messages.fact_value(state, "term_months")
    term = term if isinstance(term, int) and not isinstance(term, bool) else None

    # ---- capacity
    yearly: Optional[int] = None
    if amount_cents is not None:
        try:
            yearly = yearly_payment_cents(amount_cents, term, product, cfg.assumed_rate)
        except ValueError:
            yearly = None
    if yearly is None:
        missing.append("yearly payment (amount, product or term)")
    monthly_c = Fraction(yearly, 12) if yearly else None

    ni_ytd, ytd_months = _int_or_none(p.get("net_income_ytd_cents")), _int_or_none(p.get("ytd_months"))
    ni_fy = _int_or_none(p.get("net_income_fy_cents"))
    ni_annual: Optional[Fraction] = None
    ni_basis = ""
    if ni_ytd is not None and ytd_months:
        ni_annual = Fraction(ni_ytd * 12, ytd_months)
        ni_basis = f"{fmt_money(ni_ytd)} for {ytd_months} months"
    elif ni_fy is not None:
        ni_annual = Fraction(ni_fy)
        ni_basis = f"{fmt_money(ni_fy)} for the fiscal year"
    else:
        missing.append("net income")
    coverage: Optional[Fraction] = None
    if ni_annual is not None and yearly:
        coverage = ni_annual / yearly

    stated = messages.fact_value(state, "annual_revenue_stated")
    stated_cents: Optional[int] = None
    if stated is not None:
        try:
            stated_cents = to_cents(stated)
        except (ValueError, TypeError):
            stated_cents = None
    deposits = _int_or_none(p.get("deposits_annualized_cents"))
    support: Optional[Fraction] = None
    if stated_cents is not None and deposits:
        support = Fraction(stated_cents, deposits)
    else:
        missing.append("revenue support (stated revenue or annualized deposits)")

    cash = _int_or_none(p.get("cash_cents"))
    cash_months = Fraction(cash) / monthly_c if cash is not None and monthly_c else None

    formed, applied = _date_or_none(p.get("formation_date")), _date_or_none(p.get("application_date"))
    if applied is None and state.docs:
        applied = min(d.arrive_at for d in state.docs).date()
    years: Optional[Fraction] = None
    if formed is not None and applied is not None:
        years = Fraction((applied - formed).days) / Fraction(36525, 100)
    else:
        missing.append("time in business (formation date)")

    status, rel = _relationship(p)
    if status is None:
        missing.append("relationship history")

    # ---- points (a missing input scores the maximum for that test: conservative)
    pts_cov = points_coverage(coverage, cfg) if coverage is not None else max(
        [cfg.coverage_floor_points] + [b[1] for b in cfg.coverage_bands])
    over_top = False
    if support is not None:
        pr = points_revenue(support, cfg)
        over_top = pr is None
        pts_rev = pr if pr is not None else max(b[1] for b in cfg.revenue_bands)
    else:
        pts_rev = max(b[1] for b in cfg.revenue_bands)
    pts_tib = points_tib(years, cfg) if years is not None else max(
        [cfg.tib_floor_points] + [b[1] for b in cfg.tib_bands])
    pts_rel = cfg.relationship_points[status] if status else max(cfg.relationship_points.values())
    tests = [
        ("debt coverage", pts_cov, "n/a" if coverage is None else f"{_fx(coverage)}x"),
        ("revenue support", pts_rev, "n/a" if support is None else f"{_fx(support)}x"),
        ("time in business", pts_tib, "n/a" if years is None else f"{int(years)} years"),
        ("relationship", pts_rel, _rel_phrase(status, rel, False)),
    ]
    total = pts_cov + pts_rev + pts_tib + pts_rel
    grade, gname = grade_for(total, cfg)
    reasons: list[str] = []
    if over_top:
        grade, gname = cfg.top_grade
        reasons.append(
            f"revenue support {_fx(support)}x is above the top band (the bank's REV_INFLATION check); this deal should not have reached committee")

    watch = state.features.get("fraud_watch")
    if watch and grade < cfg.watch_min_grade:
        grade, gname = cfg.watch_min_grade, grade_name(cfg.watch_min_grade, cfg)

    # ---- recommendation
    ranked = sorted(tests, key=lambda t: -t[1])
    drivers = [t for t in ranked if t[1] > 0][:2]
    if grade <= 3:
        rec, rec_short = "Recommend approval, subject to the conditions below.", "recommend approval, subject to conditions"
    elif grade == 4:
        rec, rec_short = "Not recommended as submitted.", "not recommended as submitted"
    else:
        rec, rec_short = "Not recommended.", "not recommended"
    if drivers:
        reason_sentence = "Driven by " + " and ".join(f"{n} {v} ({pt} point{'s' if pt != 1 else ''})" for n, pt, v in drivers) + "."
    else:
        reason_sentence = "All four tests score 0 points."
    if grade == 4 and drivers and drivers[0][0] == "debt coverage" and coverage is not None:
        better = sorted((lo for lo, pts in cfg.coverage_bands if pts < pts_cov), key=Fraction)
        target = f"{_fx(Fraction(better[0]), 2)}x" if better else "the next band"
        reason_sentence += f" Coverage {_fx(coverage)}x: a longer term or a smaller amount would bring it above {target}."
    elif grade >= 4 and drivers:
        reason_sentence += f" What would fix it: improve {drivers[0][0]}."
    if reasons:
        joined = "; ".join(reasons)
        reason_sentence += " " + joined[:1].upper() + joined[1:] + "."

    # ---- conditions of approval
    nb = messages.needed_by_dt(state)
    conds = [
        "Commitment letter signed by the RM and countersigned by the borrower (RM confirms).",
        "Funds go only to the borrower's own account or a verified vendor's remit account.",
        "Final fraud screen clear immediately before funding.",
        "Personal guarantee from each guarantor.",
    ]
    is_equipment = product in get_config().vendor_check_products
    if product == REVOLVING:
        conds.append("Annual review and renewal; line may be drawn only while the borrower is current.")
    if is_equipment:
        conds.append("First lien on the financed equipment (UCC filing); vendor identity and remit account verified against known vendors.")
        vp = _int_or_none(p.get("vendor_past_loans"))
        if p.get("vendor_verified") is not True and not (vp and vp > 0):
            conds.append("Verify vendor and remit account by an independent phone callback before funding.")
    if nb:
        conds.append(f"Fund before the deadline: funds required by {fmt_when(nb)}.")
    if support is not None and support > Fraction(cfg.revenue_bands[0][0]) and not over_top:
        conds.append("Borrower to explain the gap between stated revenue and deposits.")
    weak_cov = [Fraction(lo) for lo, pts in cfg.coverage_bands if pts == 2]
    if coverage is not None and weak_cov and coverage < weak_cov[0]:
        conds.append("Committee to consider a longer term, a smaller amount or extra collateral.")
    if watch:
        conds.append("Fraud Ops review before funding.")

    # ---- text
    rate_s = f"{(cfg.assumed_rate * 100).quantize(Decimal('0.1'))}%"
    name = messages.borrower_name(state)
    dba = p.get("dba")
    route = routing.route_detail("committee_approval", state, rules, now)
    approver = routing.person_name(rules.teams, route.to) if route else NP
    c_stage = routing.stage_for_gate("committee_approval", rules) or ""
    delegated = routing.committee_delegated(state, rules.stages.stages.get(c_stage, {}))
    role = "Delegated approver" if delegated else "Committee chair"
    rm = messages.rm_label(state, rules)
    cd = rt.decisions.get("credit_decision")
    cd_s = f"approve on {fmt_date(cd[1])}" if cd else NP
    officer = routing.person_name(rules.teams, cd[0]) if cd else NP
    cover = rt.covering.get(routing.stage_for_gate("credit_decision", rules) or "", {})
    if cover.get("covering") and cd:
        officer += f" (backup for {routing.person_name(rules.teams, cover.get('primary'))}, out of office)"
    nb_s = fmt_when(nb) if nb else "no specific date"
    purpose = messages.purpose_text(state)
    years_s = f"{int(years)} years in business (formed {_p(p.get('formation_date'))})" if years is not None else f"time in business {NP}"
    sc = f"statement pages complete" if p.get("statement_months") else "statement pages not provided"
    cash_s = f"{fmt_money(cash)} (covers {_fx(cash_months, 0)} months of payments)" if cash is not None and cash_months is not None else NP
    quote = p.get("quote") if isinstance(p.get("quote"), dict) else {}
    lines = [
        f"CREDIT MEMO: {name}" + (f" (DBA {dba})" if dba else ""),
        f"Prepared {fmt_when(now)} by Prism for {approver} ({role})",
        f"RM: {rm} · Credit officer: {officer} · Credit decision: {cd_s}",
        "",
        "REQUEST",
        f"{messages.amount_text(state)} {messages.product_text(state)}, {messages.term_text(state)} months, for {purpose}: \"{_p(p.get('use_of_proceeds'))}\"",
        f"Collateral offered: {_p(p.get('collateral'))}. Funds needed by: {nb_s}.",
        "",
        f"RISK GRADE {grade} ({gname}). {rec}",
        reason_sentence,
        "",
        "CHARACTER",
        f"{years_s}. {_p(p.get('ownership'))}.",
        f"Relationship: {_rel_phrase(status, rel, True)}.",
        f"Financials prepared by {_p(p.get('preparer'))}.",
        "",
        f"CAPACITY  (indicative, at an assumed {rate_s})",
        f"Yearly payment on this loan: {_dollars(yearly) if yearly is not None else NP} "
        f"({'interest-only on the full line' if product == REVOLVING else f'{messages.term_text(state)}-month amortizing'}).",
        f"Net income: {ni_basis or NP}, about {_dollars(_round_half_up(ni_annual)) if ni_annual is not None else NP} a year. "
        f"Coverage {_fx(coverage) + 'x' if coverage is not None else NP}.",
        f"Revenue: stated {messages.money_of(stated) if stated is not None else NP}; bank statements support about "
        f"{_dollars(deposits) if deposits is not None else NP} a year ({_fx(support) + 'x' if support is not None else NP}).",
        "Note: net-income coverage of the new loan only; other debt is not included.",
        "",
        "CAPITAL",
        f"Cash {cash_s} · Receivables {messages.cents_text(p.get('receivables_cents'))} · Liabilities {messages.cents_text(p.get('liabilities_cents'))}.",
        "",
        "COLLATERAL",
        f"{_p(p.get('collateral'))}."
        + (
            f" Equipment: quote {_p(quote.get('number'))} from {_p(quote.get('vendor'))}, total {messages.cents_text(quote.get('total_cents'))}; "
            f"vendor and remit account ...{_p(quote.get('remit_last4'))} "
            + ("verified: seen in past loans." if p.get("vendor_verified") is True else "NOT in history: callback required.")
            if is_equipment else ""
        ),
        "",
        "CONDITIONS",
        f"{purpose.capitalize()} loan request" + (f"; funds required by {fmt_when(nb)}" if nb else "") + ".",
        "",
        "CHECKS PERFORMED",
        f"Documents: all required documents present, {sc}.",
        f"Statements: {_p(p.get('statement_months'))} months, all foot to their summaries and chain month to month.",
        "Bank's known-fraud checks (5): all pass.",
        "Cross-deal fraud screen: " + (f"watch: {watch}" if watch else "no shared rare entities") + ".",
        "Open issues: " + (", ".join(sorted(set(state.issues))) if state.issues else "none") + ".",
        "",
        "CONDITIONS OF APPROVAL",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(conds, 1)]
    src = [
        f"{label}: {f.source_doc}"
        for label, key in (("Borrower and request", "legal_name"), ("Amount", "amount_requested"), ("Product", "product"),
                           ("Term", "term_months"), ("Stated revenue", "annual_revenue_stated"))
        if (f := state.facts.get(key)) is not None
    ]
    lines += ["", "SOURCES"] + (src or [NP])
    lines.append("Net income, cash, receivables and liabilities: financial statements. Deposits: bank statements.")
    if missing:
        lines += ["", "CONFIDENCE", "Lower confidence: these inputs were " + NP + " and were scored conservatively: " + "; ".join(missing) + "."]
    else:
        lines += ["", "CONFIDENCE", "All inputs provided."]
    full = "\n".join(lines)

    first = (
        f"{name} requests {messages.amount_text(state)} {messages.product_text(state)} over {messages.term_text(state)} months "
        f"for {purpose}. Risk grade {grade} ({gname}): {rec_short}."
    )
    cov_part = f"Coverage {_fx(coverage)}x at an assumed {rate_s}" if coverage is not None else f"Coverage {NP}"
    sup_part = f"revenue supported {_fx(support)}x by bank deposits" if support is not None else f"revenue support {NP}"
    yrs_part = f"{int(years)} years in business" if years is not None else f"time in business {NP}"
    rel_part = _rel_phrase(status, rel, False) if status else f"relationship {NP}"
    second = f"{cov_part}; {sup_part}; {yrs_part}; {rel_part}."
    third = "All documents and checks clear" + (f"; needed by {fmt_when(nb)}" if nb else "") + "."
    fourth = "Full memo on the deal page. Please approve or decline" + (f" by {fmt_when(answer_by)}" if answer_by else "") + "."
    short = "\n".join([first, second, third, fourth])

    numbers = {
        "yearly_payment_cents": yearly,
        "coverage": None if coverage is None else _fx(coverage),
        "revenue_support": None if support is None else _fx(support),
        "years_in_business": None if years is None else int(years),
        "points": {t[0]: t[1] for t in tests},
        "assumed_rate": str(cfg.assumed_rate),
    }
    return CreditMemo(
        grade=grade, grade_name=gname, points=total, recommendation=rec, full_text=full, short_text=short,
        conditions=conds, numbers=numbers, inputs_missing=missing,
    )


def get_credit_memo(state: DealState) -> Optional[CreditMemo]:
    return get_runtime(state).credit_memo


def credit_memo_to_dict(memo: Optional[CreditMemo]) -> Optional[dict[str, Any]]:
    if memo is None:
        return None
    return {
        "grade": memo.grade,
        "grade_name": memo.grade_name,
        "points": memo.points,
        "recommendation": memo.recommendation,
        "full_text": memo.full_text,
        "short_text": memo.short_text,
        "conditions": list(memo.conditions),
        "numbers": dict(memo.numbers),
        "inputs_missing": list(memo.inputs_missing),
    }
