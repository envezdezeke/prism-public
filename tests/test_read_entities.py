"""Graph entities: canonical values, hygiene, no personal identifiers (03-02 Task 2, D-10)."""
from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest

from prism.engine.state import DealState, Doc
from prism.fraud.baseline import HISTORY_COLUMN_KINDS
from prism.fraud.canon import canon_value
from prism.read import docmodel
from prism.read.classify import classify
from prism.read.entities import build_entities, clean_value, make_ref
from prism.read.extract import extract_entities
from tests.pdfgen import application_lines
from tests.read_fixtures import make_state, quote_rows
from tests.real_data import requires_sample, sample_root, tuning_folders


def _by_kind(refs):
    out: dict[str, set[str]] = {}
    for r in refs:
        out.setdefault(r.kind, set()).add(r.value)
    return out


def _full(tmp_path, **kw):
    return make_state(tmp_path, {"quote": quote_rows()}, **kw)


def test_every_kind_the_documents_support(tmp_path):
    kinds = _by_kind(extract_entities(_full(tmp_path), None))
    assert kinds["address"] == {canon_value("address", "10 Example Way, Testville, TX 75001")}
    assert kinds["guarantor"] == {"ada quillfeather"}
    assert kinds["registered_agent"] == {"example agents inc"}
    assert kinds["preparer"] == {"pat example cpa"}
    assert kinds["phone"] == {"2145550100"}
    assert kinds["vendor"] == {"sample equipment supply llc"}
    assert kinds["remit_account"] == {"9902"}
    assert kinds["pdf_producer"] == {"producer one", "creator one"}
    assert kinds["bank"] == {"sample savings bank", "example bank"}  # the application bank and the quote's remit bank


def test_quote_gives_both_a_remit_account_and_a_bank_entity(tmp_path):
    refs = extract_entities(_full(tmp_path), None)
    quote_refs = [r for r in refs if r.source_doc.endswith("h_quote.pdf")]
    assert {r.kind for r in quote_refs} == {"vendor", "remit_account", "bank"}


def test_values_are_canonical_and_idempotent(tmp_path):
    for ref in extract_entities(_full(tmp_path), None):
        assert ref.value == canon_value(ref.kind, ref.value)
        assert canon_value(ref.kind, canon_value(ref.kind, ref.value)) == ref.value


def test_source_doc_is_the_doc_path_and_output_is_sorted_and_unique(tmp_path):
    state = _full(tmp_path)
    refs = extract_entities(state, None)
    paths = {d.path for d in state.docs}
    assert all(r.source_doc in paths for r in refs)
    keys = [(r.kind, r.value, r.source_doc) for r in refs]
    assert keys == sorted(keys)
    assert len({(r.kind, r.value) for r in refs}) == len(refs)


def test_no_personal_identifier_is_an_entity(tmp_path):
    blob = " ".join(f"{r.kind}={r.value}" for r in extract_entities(_full(tmp_path), None))
    for secret in ("01234567", "19800102", "1980", "xxx", "0000", "p00000000", "7654321"):
        assert secret not in blob.casefold(), secret
    assert not {r.kind for r in extract_entities(_full(tmp_path), None)} & {"dob", "dl", "ssn", "dl_number"}


def test_preparer_prefers_tax_return_and_uses_financials_only_without_one(tmp_path):
    from tests.test_read_forms import _fs_rows, _tax_rows

    both = {"tax": _tax_rows("Tax Person, EA (Tax Firm)"), "fs": _fs_rows("Fin Person, CPA, Fin Firm · 214-555-0100")}
    assert _by_kind(extract_entities(make_state(tmp_path, both), None))["preparer"] == {"tax person ea"}
    only_fs = {"tax": None, "fs": _fs_rows("Fin Person, CPA, Fin Firm · 214-555-0100")}
    assert _by_kind(extract_entities(make_state(tmp_path, only_fs), None))["preparer"] == {"fin person cpa"}
    no_cred = {"tax": _tax_rows("K. Person (Bookkeeping Firm)")}
    assert _by_kind(extract_entities(make_state(tmp_path, no_cred), None))["preparer"] == {"k person"}


def test_self_prepared_is_kept_and_owner_placeholder_dropped(tmp_path):
    from tests.test_read_forms import _fs_rows

    state = make_state(tmp_path, {"tax": None, "fs": _fs_rows("Self-prepared, (owner)")})
    kinds = _by_kind(extract_entities(state, None))
    assert kinds["preparer"] == {"self prepared"}
    assert "phone" not in kinds  # no preparer phone on a self-prepared statement
    assert all("owner" not in v for vals in kinds.values() for v in vals)


@pytest.mark.parametrize("agent", ["(owner)", "N/A", "-", "—"])
def test_placeholder_registered_agent_is_not_an_entity(tmp_path, agent):
    from tests.read_fixtures import CERT_ROWS

    rows = [("Registered agent", agent) if isinstance(r, tuple) and r[0] == "Registered agent" else r for r in CERT_ROWS]
    kinds = _by_kind(extract_entities(make_state(tmp_path, {"cert": rows}), None))
    assert "registered_agent" not in kinds


def test_two_guarantors_emit_both(tmp_path):
    lines = application_lines()
    i = lines.index("Affiliated businesses")
    lines[i:i] = ["Name", "Bo Frostwhistle", "Title / ownership", "Owner / 40%", "Date of birth", "1975-05-05",
                  "SSN", "XXX-XX-1111", "Home address", "3 Lane, Testville, TX 75003", "Driver license", "TX 7654321"]
    state = make_state(tmp_path, {"application": [lines]})
    assert _by_kind(extract_entities(state, None))["guarantor"] == {"ada quillfeather", "bo frostwhistle"}


def test_producer_equal_to_creator_is_one_entity(tmp_path):
    state = make_state(tmp_path, producer="Same Tool", creator="Same Tool")
    assert _by_kind(extract_entities(state, None))["pdf_producer"] == {"same tool"}
    state = make_state(tmp_path, producer="Only Producer", creator=None)
    assert _by_kind(extract_entities(state, None))["pdf_producer"] == {"only producer"}


def test_no_documents_gives_nothing():
    assert build_entities(DealState(folder="Empty"), None) == []


# ---------------------------------------------------------------- hygiene

def test_clean_value_strips_controls_collapses_space_and_caps():
    assert clean_value("  Acme\x00\x07 \x1b Co‮  {x} %s\n\tLtd ") == "Acme Co {x} %s Ltd"
    assert clean_value("a" * 500) == "a" * 200
    assert clean_value("a" * 500, limit=10) == "a" * 10
    assert clean_value(None) == "" and clean_value("\x00\x01") == ""


def test_make_ref_canonicalizes_after_cleaning_and_drops_placeholders():
    ref = make_ref("vendor", "Acme\x00 Equipment, L.L.C.", "F/q.pdf")
    assert ref.value == canon_value("vendor", "Acme Equipment, L.L.C.") == "acme equipment llc"
    assert make_ref("phone", "(214) 555-0100", "F/p.pdf").value == "2145550100"
    assert make_ref("preparer", "(owner)", "F/p.pdf") is None
    assert make_ref("preparer", "N/A", "F/p.pdf") is None
    assert make_ref("preparer", "\x00\x01", "F/p.pdf") is None
    assert make_ref("preparer", "---", "F/p.pdf") is None
    assert make_ref("preparer", "Self-prepared", "F/p.pdf").value == "self prepared"


def test_braces_and_format_specifiers_are_plain_data():
    ref = make_ref("guarantor", "{0} %(name)s ${x} <script>", "F/a.pdf")
    assert ref is not None and "{" not in ref.value and "%" not in ref.value


# ---------------------------------------------------------------- real data: canonical agreement with history

@requires_sample
def test_emitted_values_agree_with_history_canonical_forms():
    root = sample_root()
    hist_path = root / "history" / "deals.csv"
    if not hist_path.exists():
        pytest.skip("history not present")
    with open(hist_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by_ein: dict[str, list[dict]] = {}
    for r in rows:
        by_ein.setdefault((r.get("ein") or "").strip(), []).append(r)
    producer_vocab = {canon_value("pdf_producer", (r.get("pdf_producer") or "").strip()) for r in rows} - {""}
    compared = 0
    differing: list[str] = []
    for folder in tuning_folders(root):
        docs = []
        for p in sorted((root / "shared_folder" / folder).iterdir()):
            d = Doc(path=f"{folder}/{p.name}", name=p.name, arrive_at=datetime(2026, 11, 2, tzinfo=timezone.utc), abs_path=p)
            classify(d)
            docs.append(d)
        state = DealState(folder=folder, docs=docs)
        app = next((docmodel.parsed(d) for d in docs if d.doc_class == "application"), None)
        matches = by_ein.get(app.ein, []) if app and app.ein else []
        if not matches:
            continue
        kinds = _by_kind(extract_entities(state, None))
        # A returning borrower's earlier packet can come from another PDF tool, so the producer is checked
        # against the whole history vocabulary (same canonical form), not against that borrower's old row.
        produced = kinds.get("pdf_producer", set())
        assert produced and produced <= producer_vocab, "emitted producer not in the history vocabulary"
        for row in matches:
            for col, kind in HISTORY_COLUMN_KINDS.items():
                if kind == "pdf_producer":
                    continue
                raw = (row.get(col) or "").strip()
                if not raw or make_ref(kind, raw, "x") is None:
                    continue  # empty or a placeholder such as "(owner)": no entity is expected
                compared += 1
                if canon_value(kind, raw) not in kinds.get(kind, set()):
                    differing.append(kind)
    print("history agreement: compared", compared, "differing kinds", sorted(differing))
    assert compared > 0
    assert len(differing) <= 1, sorted(differing)
