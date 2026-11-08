"""features: profile for Move, deposit_months for Fraud, history index (03-02 Task 3)."""
from __future__ import annotations

import csv
import itertools
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_move_support import ASSIGNMENTS, PEOPLE, ROUTING, STAGES, at, fact

from prism.engine.run import run_sim
from prism.engine.state import DealState, Doc
from prism.fraud.rarity import parse_weekdays
from prism.move import messages
from prism.read import docmodel, features, history
from prism.read import issues as read_issues
from prism.read.docmodel import ParsedStatement, Txn
from prism.read.extract import extract_facts, extract_features
from tests.read_fixtures import BASE, F7004_ROWS, make_state, quote_rows

HIST_COLUMNS = ["deal_id", "application_date", "legal_name", "ein", "final_status", "fraud_label",
                "repayment_status", "relationship_since", "equipment_vendor", "vendor_remit_acct_last4"]
APP_EIN = "98-7654321"


def _history(tmp_path: Path, rows: list[dict]) -> SimpleNamespace:
    d = tmp_path / "pack" / "history"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "deals.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIST_COLUMNS, restval="")
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({"deal_id": f"H-{i:03d}", "ein": r.pop("ein", "00-0000000"), **r})
    history.clear_cache()
    return SimpleNamespace(history_dir=d)


def _row(**kw):
    base = {"final_status": "funded", "fraud_label": "none", "repayment_status": "current"}
    base.update(kw)
    return base


def _profile(tmp_path, parts=None, rules=None, **kw):
    state = make_state(tmp_path, parts, **kw)
    state.facts.update(extract_facts(state, None))
    return state, features.build_profile(state, rules)


# ---------------------------------------------------------------- profile keys

def test_profile_from_documents(tmp_path):
    _state, p = _profile(tmp_path)
    assert p["formation_date"] == "2015-04-02" and p["application_date"] == "2026-11-02"
    assert "dba" not in p  # the form shows a dash
    assert p["preparer"] == "Pat Example, CPA (Example & Co CPAs)"
    assert p["use_of_proceeds"] == "Buy seasonal inventory" and p["collateral"] == "Inventory and receivables"
    assert p["ownership"] == "Owner / 100%"
    assert p["tax_doc"] == "tax_return"
    assert (p["net_income_fy_cents"], p["net_income_ytd_cents"], p["ytd_months"]) == (9_000_000, 8_100_000, 10)
    assert (p["cash_cents"], p["receivables_cents"], p["liabilities_cents"]) == (5_000_000, 7_000_000, 12_000_000)
    assert p["disbursement"] == {"kind": "borrower", "bank": "Sample Savings Bank", "last4": "4321"}
    for absent in ("statement_months", "deposits_annualized_cents", "relationship", "quote", "vendor_verified",
                   "vendor_past_loans", "mismatch_evidence", "fraud_watch"):
        assert absent not in p, absent


def test_every_money_value_is_an_int(tmp_path):
    _state, p = _profile(tmp_path, {"quote": quote_rows()})
    for key, value in p.items():
        if key.endswith("_cents"):
            assert isinstance(value, int) and not isinstance(value, bool), key
    assert isinstance(p["quote"]["total_cents"], int)


def test_dba_shown_when_the_form_gives_one(tmp_path):
    _state, p = _profile(tmp_path, app_over={"dba": "Nimbus Orchard"})
    assert p["dba"] == "Nimbus Orchard"


def test_tax_doc_is_form_7004_without_a_return(tmp_path):
    _state, p = _profile(tmp_path, {"tax": None, "f7004": F7004_ROWS})
    assert p["tax_doc"] == "form_7004"
    _state, p = _profile(tmp_path, {"tax": None})
    assert "tax_doc" not in p


def test_unknown_values_are_omitted_never_guessed(tmp_path):
    parts = {"id": None, "irs": None, "cert": None, "tax": None, "fs": None, "letter": None}
    _state, p = _profile(tmp_path, parts)
    assert set(p) == {"formation_date", "application_date", "use_of_proceeds", "collateral", "ownership",
                      "disbursement"}
    assert features.build_profile(DealState(folder="Empty"), None) == {}


def test_preparer_without_a_return_comes_from_the_financials(tmp_path):
    _state, p = _profile(tmp_path, {"tax": None})
    assert p["preparer"] == "Pat Example, CPA (Example & Co CPAs)"


# ---------------------------------------------------------------- relationship from history

@pytest.mark.parametrize("hist_row,status,since", [
    (_row(relationship_since="2019"), "returning_clean", 2019),
    (_row(repayment_status="paid_off"), "returning_clean", None),
    (_row(repayment_status="30_days_past_due", relationship_since="2020"), "returning_past_due", 2020),
    (_row(repayment_status="charged_off"), "returning_past_due", None),
    (_row(fraud_label="REV_INFLATION"), "new", None),
    (_row(final_status="declined", repayment_status=""), "new", None),
])
def test_relationship_from_history(tmp_path, hist_row, status, since):
    rules = _history(tmp_path, [{**hist_row, "ein": APP_EIN}])
    _state, p = _profile(tmp_path, rules=rules)
    rel = p["relationship"]
    assert rel["status"] == status and rel["prior_loans"] == 1
    assert rel.get("since_year") == since


def test_relationship_no_match_is_new_and_no_history_is_absent(tmp_path):
    rules = _history(tmp_path, [_row(ein="11-1111111")])
    _state, p = _profile(tmp_path, rules=rules)
    assert p["relationship"] == {"status": "new", "prior_loans": 0}
    _state, p = _profile(tmp_path, rules=SimpleNamespace(history_dir=None))
    assert "relationship" not in p


def test_relationship_worst_status_wins_and_counts_every_row(tmp_path):
    rules = _history(tmp_path, [_row(ein=APP_EIN), _row(ein=APP_EIN, repayment_status="charged_off")])
    _state, p = _profile(tmp_path, rules=rules)
    assert p["relationship"]["status"] == "returning_past_due" and p["relationship"]["prior_loans"] == 2


def test_relationship_matches_the_irs_letter_ein_when_the_application_differs(tmp_path):
    rules = _history(tmp_path, [_row(ein=APP_EIN, relationship_since="2018")])
    _state, p = _profile(tmp_path, rules=rules, app_over={"ein": "98-7654322"})
    assert p["relationship"]["status"] == "returning_clean"


# ---------------------------------------------------------------- vendor verification (D-15)

VENDOR = "Sample Equipment Supply LLC"


@pytest.mark.parametrize("rows,verified,past", [
    ([_row(equipment_vendor="Sample Equipment Supply, L.L.C.", vendor_remit_acct_last4="9902")] * 2, True, 2),
    ([_row(equipment_vendor="Sample Equipment Supply, Inc.", vendor_remit_acct_last4="9902")], False, 0),
    ([_row(equipment_vendor=VENDOR, vendor_remit_acct_last4="1111")], False, 0),
    ([_row(equipment_vendor="Sampel Equipment Supply Co", vendor_remit_acct_last4="9902")], False, 0),
    ([_row(equipment_vendor="Entirely Different Trucking Corp", vendor_remit_acct_last4="9902")], None, 0),
])
def test_vendor_verification_from_history(tmp_path, rows, verified, past):
    rules = _history(tmp_path, [dict(r) for r in rows])
    _state, p = _profile(tmp_path, {"quote": quote_rows()}, rules=rules)
    if verified is None:
        assert "vendor_verified" not in p
    else:
        assert p["vendor_verified"] is verified
    assert p["vendor_past_loans"] == past
    d = p["disbursement"]
    assert d["kind"] == "vendor" and d["vendor"] == VENDOR and d["last4"] == "9902" and d["bank"] == "Example Bank"
    assert d["past_loans"] == past
    assert p["quote"] == {"number": "Q-26-0001", "vendor": VENDOR, "total_cents": 4_290_000, "remit_last4": "9902"}


def test_vendor_with_no_history_loaded_is_unknown(tmp_path):
    _state, p = _profile(tmp_path, {"quote": quote_rows()}, rules=SimpleNamespace(history_dir=None))
    assert "vendor_verified" not in p and "vendor_past_loans" not in p
    assert p["disbursement"]["kind"] == "vendor" and "past_loans" not in p["disbursement"]


def test_move_reads_the_profile_shapes(tmp_path):
    rules = _history(tmp_path, [_row(equipment_vendor=VENDOR, vendor_remit_acct_last4="9902")])
    state, p = _profile(tmp_path, {"quote": quote_rows()}, rules=rules)
    state.features["profile"] = p
    assert messages.pay_to_text(state) == f"verified vendor {VENDOR}, Example Bank ...9902 (1 past loans paid to this account)"
    assert messages.cents_text(p["cash_cents"]) != messages.NOT_PROVIDED


# ---------------------------------------------------------------- history index

def test_history_loader_never_raises_and_caches(tmp_path):
    history.clear_cache()
    assert history.load_history(SimpleNamespace(history_dir=None)) is history.EMPTY
    assert history.load_history(SimpleNamespace(history_dir=tmp_path / "nope")) is history.EMPTY
    d = tmp_path / "h"
    d.mkdir()
    assert history.load_history(SimpleNamespace(history_dir=d)) is history.EMPTY  # no deals.csv
    (d / "deals.csv").write_bytes(b"\x00\xff\xfe garbage \x00\n\x81\x82")
    assert isinstance(history.load_history(SimpleNamespace(history_dir=d)), history.HistoryIndex)
    (d / "deals.csv").write_text("deal_id,ein\nH-1,98-7654321\nH-2\n", encoding="utf-8")  # partial rows and columns
    idx = history.load_history(SimpleNamespace(history_dir=d))
    assert idx.n_rows == 2 and idx.by_ein[APP_EIN][0]["final_status"] == ""
    assert history.load_history(SimpleNamespace(history_dir=d)) is idx  # cached by path and mtime
    assert history.load_history(None) is history.EMPTY


# ---------------------------------------------------------------- deposit_months

def _stmt(period, credits, pages=(1, 2), total=2, closing=None, debit=True):
    txns = tuple(Txn(date=d, description="x", amount_cents=a, balance_cents=None) for d, a in credits)
    if debit:
        txns += (Txn(date=f"{period}-02", description="d", amount_cents=-5_000, balance_cents=None),)
    return ParsedStatement(source="pdf", period=period, closing_cents=closing, txns=txns,
                           pages_found=tuple(pages), pages_total=total)


def _state_with_statements(tmp_path, monkeypatch, copies):
    """``copies`` is a list of (period, ParsedStatement); each becomes one arrived bank_statement doc."""
    docmodel.clear_cache()
    by_name = {}
    docs = []
    d = tmp_path / "Deal"
    d.mkdir(parents=True, exist_ok=True)
    for i, (period, parsed) in enumerate(copies):
        name = f"s{i:02d}.pdf"
        (d / name).write_bytes(b"%PDF-1.4 x")
        by_name[name] = parsed
        docs.append(Doc(path=f"Deal/{name}", name=name, arrive_at=BASE + timedelta(minutes=i), abs_path=d / name,
                        doc_class="bank_statement", period=period))
    monkeypatch.setitem(docmodel._PARSERS, "bank_statement", lambda dt, doc: by_name[doc.name])
    return DealState(folder="Deal", docs=docs)


# Mon 2026-05-04, Wed 2026-05-06, Fri 2026-05-08
MAY = [("2026-05-04", 100_000), ("2026-05-06", 50_000), ("2026-05-08", 25_050), ("2026-05-08", 30_000)]


def test_month_row_in_history_units(tmp_path, monkeypatch):
    state = _state_with_statements(tmp_path, monkeypatch, [("2026-05", _stmt("2026-05", MAY, closing=1_234_567))])
    rows = features.build_deposit_months(state)
    assert rows == [{
        "month": "2026-05", "total_deposits": 2050.5, "deposit_count": 4,
        "pct_deposits_multiple_of_100": 0.75, "largest_deposit": 1000.0,
        "deposit_weekdays": "Mon,Wed,Fri", "month_end_balance": 12345.67,
    }]


def test_weekday_labels_round_trip_through_parse_weekdays():
    for n in range(1, 8):
        for combo in itertools.combinations(range(7), n):
            label = features.weekday_label(set(combo))
            assert parse_weekdays(label) == combo, (combo, label)
    assert features.weekday_label(set(range(7))) == "all days"
    assert features.weekday_label(set(range(5))) == "Mon-Fri"
    assert features.weekday_label(set(range(6))) == "Mon-Sat"
    assert features.weekday_label(set()) is None


def test_months_with_a_missing_page_get_no_row_and_the_latest_complete_copy_wins(tmp_path, monkeypatch):
    partial = _stmt("2026-06", MAY, pages=(1,), total=2)
    old = _stmt("2026-07", [("2026-07-06", 10_000)])
    new = _stmt("2026-07", [("2026-07-06", 20_000), ("2026-07-07", 20_000)])
    later_partial = _stmt("2026-07", [("2026-07-06", 99_000)], pages=(2,), total=2)
    state = _state_with_statements(tmp_path, monkeypatch, [
        ("2026-06", partial), ("2026-07", old), ("2026-07", new), ("2026-07", later_partial)])
    rows = features.build_deposit_months(state)
    assert [r["month"] for r in rows] == ["2026-07"]
    assert rows[0]["deposit_count"] == 2 and rows[0]["total_deposits"] == 400.0  # the later complete copy


def test_no_statements_gives_no_deposit_months_key(tmp_path):
    state = make_state(tmp_path)
    assert features.build_deposit_months(state) == []
    assert "deposit_months" not in extract_features(state, None)
    assert "statement_months" not in features.build_profile(state, None)


def _six_months(tmp_path, monkeypatch, n):
    periods = [f"2026-{m:02d}" for m in range(10 - n + 1, 11)]
    copies = [(p, _stmt(p, [(f"{p}-05", 100_000 + i * 1000), (f"{p}-12", 20_000)])) for i, p in enumerate(periods)]
    return _state_with_statements(tmp_path, monkeypatch, copies), copies


def test_deposits_annualized_uses_the_six_most_recent_complete_months(tmp_path, monkeypatch):
    state, copies = _six_months(tmp_path, monkeypatch, 7)
    p = features.build_profile(state, None)
    expected = 2 * sum(c.amount_cents for _per, parsed in copies[-6:] for c in parsed.txns if c.amount_cents > 0)
    assert p["statement_months"] == 7 and p["deposits_annualized_cents"] == expected
    assert isinstance(p["deposits_annualized_cents"], int)


def test_deposits_annualized_omitted_with_fewer_than_six_months(tmp_path, monkeypatch):
    state, _ = _six_months(tmp_path, monkeypatch, 5)
    p = features.build_profile(state, None)
    assert p["statement_months"] == 5 and "deposits_annualized_cents" not in p


def test_extract_features_has_both_keys_and_never_fraud_watch(tmp_path, monkeypatch):
    state, _ = _six_months(tmp_path, monkeypatch, 6)
    out = extract_features(state, None)
    assert set(out) == {"profile", "deposit_months"} and len(out["deposit_months"]) == 6
    assert "fraud_watch" not in out and "fraud_watch" not in out["profile"]


def test_extract_features_is_empty_when_nothing_is_known():
    assert extract_features(DealState(folder="Empty"), None) == {}


# ---------------------------------------------------------------- mismatch evidence hook

def test_mismatch_evidence_is_pulled_through_the_issues_module(tmp_path, monkeypatch):
    ev = {"ein": {"application": "98-7654322", "document": "IRS letter", "value": "98-7654321"}}
    monkeypatch.setattr(read_issues, "mismatch_evidence", lambda state, rules: ev)
    _s, p = _profile(tmp_path)
    assert p["mismatch_evidence"] == ev
    monkeypatch.setattr(read_issues, "mismatch_evidence", lambda state, rules: {})
    assert "mismatch_evidence" not in _profile(tmp_path)[1]

    def boom(state, rules):
        raise RuntimeError("x")

    monkeypatch.setattr(read_issues, "mismatch_evidence", boom)
    assert "mismatch_evidence" not in _profile(tmp_path)[1]
    monkeypatch.delattr(read_issues, "mismatch_evidence")
    assert "mismatch_evidence" not in _profile(tmp_path)[1]


# ---------------------------------------------------------------- engine merge (D-14)

ARRIVALS = [
    {"path": "Acme Co/01_app.pdf", "arrive_at": "2026-03-02T09:00-06:00"},
    {"path": "Acme Co/02_id.pdf", "arrive_at": "2026-03-02T09:10-06:00"},
]


def test_a_profile_written_elsewhere_survives_a_real_run_sim_merge(tmp_path, monkeypatch):
    root = tmp_path / "pack"
    for rel, data in (("rules/stages.json", STAGES), ("contacts/teams.json", {"people": PEOPLE, "routing": ROUTING}),
                      ("sim/assignments.json", ASSIGNMENTS)):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    folder = root / "shared_folder"
    for row in ARRIVALS:
        f = folder.joinpath(*row["path"].split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    arrivals = root / "sim" / "arrivals.jsonl"
    arrivals.write_text("\n".join(json.dumps(r) for r in ARRIVALS) + "\n", encoding="utf-8")

    import prism.read.extract as extract

    mine = {"formation_date": "2015-01-01", "relationship": {"status": "new"}, "net_income_fy_cents": 40_000_000}

    def fake_facts(state, rules):
        state.features["profile"] = dict(mine)
        return {"legal_name": fact("Acme Co LLC"), "amount_requested": fact(1000)}

    monkeypatch.setattr(extract, "extract_facts", fake_facts)  # extract_features stays the real one
    result = run_sim(folder, arrivals, data_root=root, end=at("2026-03-05T12:00"))
    state = result.states["Acme Co"]
    assert state.features["profile"] == mine
    assert "fraud_watch" not in state.features
