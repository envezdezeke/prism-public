"""Nemotron VL null-fill (03-04 Tasks 2 and 3). No network and no key: every call goes through a fake transport.

All names, numbers and the "key" below are invented. conftest.py keeps VL off, the .env absent and the cache in
a tmp dir for every test; tests that exercise VL turn it on explicitly.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest

from prism.engine.config import Rules
from prism.read import docmodel, vl
from prism.read.extract import extract_facts
from prism.read.settings import get_config
from tests.pdfgen import application_lines, fake_png_bytes, write_pdf
from tests.read_fixtures import BASE, make_state
from tests.test_read_forms import ID_ROWS

SENTINEL = "nvapi-SENTINEL-do-not-leak-0123456789"
RULES = Rules()
PRIMARY, FALLBACK = "vl-primary-test", "vl-fallback-test"


# ---------------------------------------------------------------- helpers

class Fake:
    """A transport double: returns the next scripted reply (a str, or an exception to raise)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, list]] = []

    def __call__(self, model, messages, *, timeout, max_tokens):
        self.calls.append((model, messages))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        return r


def live(monkeypatch, fake, *, key=SENTINEL, fallback=True, mode="live"):
    monkeypatch.setenv("NEMOTRON_MODE", mode)
    if key is not None:
        monkeypatch.setenv("NVIDIA_API_KEY", key)
    monkeypatch.setenv("NEMOTRON_VL_MODEL", PRIMARY)
    if fallback:
        monkeypatch.setenv("NEMOTRON_VL_FALLBACK_MODEL", FALLBACK)
    else:
        monkeypatch.delenv("NEMOTRON_VL_FALLBACK_MODEL", raising=False)
    monkeypatch.setattr(vl, "_transport", fake)
    monkeypatch.setattr(vl, "_sleep", lambda s: None)
    vl.reset()
    return fake


def app_pages(drop=(), **over):
    """The generated application with some label/value pairs removed (the field becomes unreadable)."""
    lines = application_lines(**over)
    for label in drop:
        i = lines.index(label)
        del lines[i:i + 2]
    return [lines]


def state_for(tmp_path, drop=(), parts=None, **over):
    p = {"application": app_pages(drop, **over)}
    p.update(parts or {})
    st = make_state(tmp_path, parts=p, folder="Deal")
    with pytest.MonkeyPatch.context() as mp:  # deterministic facts only; tests call vl.fill_nulls themselves
        mp.setattr(vl, "fill_nulls", lambda state, rules, facts: dict(facts))
        st.facts = extract_facts(st, RULES)
    return st


def clean_id():
    return [("Address", "20 Sample Lane, Testville, TX 75002") if isinstance(r, tuple) and r[0] == "Address" else r
            for r in ID_ROWS]


# ---------------------------------------------------------------- mode

def test_mode_matrix(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MODE", "off")
    monkeypatch.setenv("NVIDIA_API_KEY", SENTINEL)
    assert vl.mode() == "off"
    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    assert vl.mode() == "replay"
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    assert vl.mode() == "live"
    monkeypatch.setenv("NEMOTRON_MODE", "")
    assert vl.mode() == "live"  # unset: live when a key exists (N9)
    monkeypatch.setenv("NEMOTRON_MODE", "nonsense")
    assert vl.mode() == "off"
    monkeypatch.delenv("NVIDIA_API_KEY")
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    assert vl.mode() == "off"  # live without a key behaves as off
    monkeypatch.setenv("NEMOTRON_MODE", "")
    assert vl.mode() == "off"
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-REPLACE_ME")
    assert vl.mode() == "off"
    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    assert vl.mode() == "replay"  # replay never needs a key


def test_env_is_read_at_call_time_and_dotenv_is_a_fallback(monkeypatch, tmp_path):
    envfile = tmp_path / "x.env"
    envfile.write_text("NEMOTRON_MODE=replay\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setenv("PRISM_ENV_FILE", str(envfile))
    monkeypatch.delenv("NEMOTRON_MODE")
    assert vl.mode() == "replay"
    monkeypatch.setenv("NEMOTRON_MODE", "off")
    assert vl.mode() == "off"  # process env wins


# ---------------------------------------------------------------- targets

def test_off_makes_no_call_and_returns_facts_unchanged(monkeypatch, tmp_path):
    fake = Fake('{"product": "Term Loan"}')
    st = state_for(tmp_path, drop=("Product",))
    live(monkeypatch, fake, mode="off")
    before = dict(st.facts)
    assert vl.fill_nulls(st, RULES, st.facts) == before
    assert fake.calls == []
    assert vl.fills_for(st) == {}


def test_only_missing_fields_are_sent_and_a_declared_none_field_never(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake('{"product": "Term Loan", "term_months": 24}'))
    st = state_for(tmp_path, drop=("Product", "Requested term"))
    assert "product" not in st.facts and "term_months" not in st.facts
    assert "needed_by" in docmodel.parsed_all(st, "application")[-1][1].declared_none
    out = vl.fill_nulls(st, RULES, st.facts)
    assert len(fake.calls) == 1
    sent = json.dumps(fake.calls[0][1])
    assert "product:" in sent and "term_months:" in sent
    for untouched in ("needed_by", "legal_name", "ein", "amount_requested", "guarantor_name"):
        assert f"{untouched}:" not in sent
    assert out["product"].value == "Term Loan" and out["term_months"].value == 24


def test_a_parser_filled_field_is_never_overwritten(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake(json.dumps({"product": "Term Loan", "legal_name": "Someone Else LLC"})))
    st = state_for(tmp_path, drop=("Product",))
    original = st.facts["legal_name"].value
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out["legal_name"].value == original
    assert out["legal_name"].source_doc == st.facts["legal_name"].source_doc
    # a reply that carries a key we did not request is rejected as a whole
    assert "product" not in out
    assert fake.calls and "legal_name:" not in json.dumps(fake.calls[0][1])


def test_nothing_missing_means_no_call(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake("{}"))
    st = state_for(tmp_path)
    vl.fill_nulls(st, RULES, st.facts)
    assert fake.calls == []


def test_no_application_means_no_call(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake('{"product": "Term Loan"}'))
    st = make_state(tmp_path, parts={"application": None}, folder="Deal")
    assert vl.fill_nulls(st, RULES, {}) == {}
    assert fake.calls == []


# ---------------------------------------------------------------- fills, tags, confidence

def test_a_fill_is_tagged_and_carries_the_configured_confidence(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"product": "Equipment Loan"}'))
    st = state_for(tmp_path, drop=("Product",))
    out = vl.fill_nulls(st, RULES, st.facts)
    f = out["product"]
    assert f.value == "Equipment Loan" and f.confidence == get_config().confidence["vl"]
    assert f.source_doc.endswith("a_app.pdf")  # still the real document
    assert vl.fills_for(st) == {"product": {"source": "nemotron_vl", "model": PRIMARY, "doc": f.source_doc}}


def test_a_null_or_invalid_answer_fills_nothing(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"product": null, "term_months": 9999}'))
    st = state_for(tmp_path, drop=("Product", "Requested term"))
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out == st.facts and vl.fills_for(st) == {}


# ---------------------------------------------------------------- reply parsing

@pytest.mark.parametrize("reply, expected", [
    ('{"product": "Term Loan"}', {"product": "Term Loan"}),
    ('```json\n{"product": "Term Loan"}\n```', {"product": "Term Loan"}),
    ('Sure! Here it is: {"product": "Term Loan"} Hope that helps {not json}', {"product": "Term Loan"}),
    ('{"product": "Term Loan", "notes": "x"}', None),
    ('{"other": 1}', None),
    ('[1, 2]', None),
    ('not json at all', None),
    ('', None),
    ('{"product": null, "term_months": {"product": "Term Loan"}}', None),
    ('{"product": "Term Loan", "term_months": [24]}', None),
    ('Example: {"product": "Line of Credit"} Answer: {"product": "Term Loan"}', None),
    ('{"term_months": 12, "note": {"product": "Term Loan"}}', None),
])
def test_parse_answer(reply, expected):
    assert vl.parse_answer(reply, ["product", "term_months"]) == expected


# ---------------------------------------------------------------- validators

@pytest.mark.parametrize("field, value, ok", [
    ("legal_name", "Zephyr Quarry LLC", True), ("legal_name", "A", False), ("legal_name", "A\nB", False),
    ("legal_name", "x" * 121, False), ("legal_name", 12, False),
    ("legal_name", "N/A", False), ("legal_name", "Not provided", False), ("legal_name", "unknown", False),
    ("legal_name", "Not stated in document", False), ("guarantor_name", "None", False), ("guarantor_name", "--", False),
    ("guarantor_name", "AB", False), ("guarantor_name", "Ada Quillfeather", True),
    ("ein", "12-3456789", True), ("ein", "123456789", True), ("ein", "12-345", False),
    ("amount_requested", 125000, True), ("amount_requested", "$125,000.50", True), ("amount_requested", 0, False),
    ("amount_requested", -5, False), ("amount_requested", 2_000_000_000, False), ("amount_requested", True, False),
    ("purpose_category", "equipment", True), ("purpose_category", "vacation", False),
    ("product", "Term Loan", True), ("product", "term loan", False),
    ("term_months", 36, True), ("term_months", 0, False), ("term_months", 601, False), ("term_months", "24", True),
    ("term_months", 2.5, False), ("term_months", True, False),
    ("annual_revenue_stated", 0, True), ("annual_revenue_stated", 900000.5, True), ("annual_revenue_stated", -1, False),
    ("needed_by", "2026-11-06T15:00-06:00", True), ("needed_by", "2026-11-06T15:00:00-06:00", True),
    ("needed_by", "2026-11-06", False), ("needed_by", "2026-11-06T15:00-05:00", False),
    ("needed_by", "2026-13-06T15:00-06:00", False),
])
def test_validators(field, value, ok):
    assert (vl.validate_value(field, value) is not None) is ok


@pytest.mark.parametrize("field, value, text, ok", [
    ("legal_name", "Zephyr  Quarry LLC", "Legal name: zephyr quarry llc EIN 12-3456789", True),
    ("legal_name", "Invented Holdings LLC", "Legal name: Zephyr Quarry LLC", False),
    ("ein", "12-3456789", "EIN 123456789", True), ("ein", "12-3456789", "EIN 98-7654321", False),
    ("guarantor_name", "Ada Quillfeather", "Guarantor: Ada Quillfeather, Owner", True),
    ("guarantor_name", "Ada Quillfeather", "Guarantor: someone else", False),
    ("product", "Term Loan", "anything", True),
])
def test_a_text_value_must_appear_in_the_document_text(field, value, text, ok):
    assert vl.grounded(field, value, text) is ok


def test_normalizations():
    assert vl.validate_value("ein", "123456789") == "12-3456789"
    assert vl.validate_value("amount_requested", "$125,000.00") == 125000
    assert vl.validate_value("amount_requested", 125000.5) == 125000.5


# ---------------------------------------------------------------- cross-document agreement

def test_agreement_with_sibling_documents(tmp_path):
    st = make_state(tmp_path, parts={"id": clean_id()}, folder="Deal")
    assert vl.agrees(st, "legal_name", "Nimbus Orchard Supply, L.L.C.")
    assert not vl.agrees(st, "legal_name", "Different Holdings LLC")
    assert vl.agrees(st, "ein", "98-7654321") and not vl.agrees(st, "ein", "11-1111111")
    assert vl.agrees(st, "amount_requested", 125000) and not vl.agrees(st, "amount_requested", 205000)
    assert vl.agrees(st, "guarantor_name", "Ada Quillfeather") and not vl.agrees(st, "guarantor_name", "Bo Nobody")
    assert vl.agrees(st, "product", "Term Loan")  # no sibling for the other fields


def test_without_siblings_format_alone_decides(tmp_path):
    parts = {k: None for k in ("id", "irs", "cert", "tax", "fs", "letter")}
    st = make_state(tmp_path, parts=parts, folder="Deal")
    for field, value in (("legal_name", "Anything Co"), ("ein", "12-3456789"), ("amount_requested", 7),
                         ("guarantor_name", "Some Person")):
        assert vl.agrees(st, field, value)


def test_a_disagreeing_answer_is_rejected_per_field(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"amount_requested": 999999}'))
    st = state_for(tmp_path, drop=("Amount requested",), parts={"id": clean_id()})
    assert "amount_requested" not in st.facts
    out = vl.fill_nulls(st, RULES, st.facts)
    assert "amount_requested" not in out  # the request letter says another figure

    monkeypatch.setenv("PRISM_NEMOTRON_CACHE_DIR", str(tmp_path / "second_cache"))
    live(monkeypatch, Fake('{"amount_requested": 125000}'))
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out["amount_requested"].value == 125000  # agrees with the letter


# ---------------------------------------------------------------- input

def test_text_is_capped_and_images_are_the_fallback(monkeypatch, tmp_path):
    from prism.engine.state import Doc

    big = tmp_path / "big.txt"
    big.write_text("word " * 20000, encoding="utf-8")
    d = Doc(path="Deal/big.txt", name="big.txt", arrive_at=BASE, abs_path=big)
    messages, digest = vl._build_input(d, ["product"])
    assert len(messages[0]["content"]) < int(vl._cfg()["max_text_chars"]) + 2000 and digest

    png = tmp_path / "scan.png"
    png.write_bytes(fake_png_bytes())
    monkeypatch.setattr("prism.read.text._ocr.read_image_text", lambda p: "")
    d2 = Doc(path="Deal/scan.png", name="scan.png", arrive_at=BASE, abs_path=png)
    messages, _digest = vl._build_input(d2, ["product"])
    parts = messages[0]["content"]
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"")
    d3 = Doc(path="Deal/junk.bin", name="junk.bin", arrive_at=BASE, abs_path=junk)
    assert vl._build_input(d3, ["product"]) is None


def test_an_oversize_image_is_not_sent(monkeypatch, tmp_path):
    from prism.engine.state import Doc

    monkeypatch.setattr(vl, "_cfg", lambda: {**vl.get_config().raw["vl"], "max_image_bytes": 50})
    monkeypatch.setattr("prism.read.text._ocr.read_image_text", lambda p: "")
    png = tmp_path / "scan.png"
    png.write_bytes(fake_png_bytes())  # 72 bytes
    d = Doc(path="Deal/scan.png", name="scan.png", arrive_at=BASE, abs_path=png)
    assert vl._build_input(d, ["product"]) is None


# ---------------------------------------------------------------- budget

def test_the_31st_live_call_is_skipped_without_error_and_reset_restores_it(monkeypatch):
    fake = live(monkeypatch, Fake("ok"))
    for i in range(30):
        assert vl._call_model(PRIMARY, [{"role": "user", "content": f"q{i}"}]) == "ok"
    assert vl.calls_used() == 30
    assert vl._call_model(PRIMARY, [{"role": "user", "content": "q31"}]) is None
    assert len(fake.calls) == 30
    vl.reset()
    assert vl._call_model(PRIMARY, [{"role": "user", "content": "again"}]) == "ok"
    assert vl.calls_used() == 1


def test_a_fallback_attempt_and_a_retry_each_count_as_a_call(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake(vl._VLError("http 429", retryable=True), vl._VLError("http 400"), '{"product": "Term Loan"}'))
    st = state_for(tmp_path, drop=("Product",))
    out = vl.fill_nulls(st, RULES, st.facts)
    assert [m for m, _ in fake.calls] == [PRIMARY, PRIMARY, FALLBACK]
    assert vl.calls_used() == 3
    assert out["product"].value == "Term Loan"
    assert vl.fills_for(st)["product"]["model"] == FALLBACK


def test_the_budget_is_enforced_inside_fill_nulls(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake(vl._VLError("http 500", retryable=True)))
    monkeypatch.setattr(vl, "_cfg", lambda: {**vl.get_config().raw["vl"], "max_calls": 2})
    st = state_for(tmp_path, drop=("Product",))
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts
    assert len(fake.calls) == 2
    # a failed key is remembered for the run: ticks that repeat the same question cost nothing more
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts
    assert len(fake.calls) == 2


# ---------------------------------------------------------------- cache

def test_live_records_and_replay_reads_without_a_call(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake('{"product": "Term Loan"}'))
    st = state_for(tmp_path, drop=("Product",))
    first = vl.fill_nulls(st, RULES, st.facts)
    assert len(fake.calls) == 1 and list(Path(vl._cache_dir()).glob("*.json"))

    fake2 = live(monkeypatch, Fake("should not be called"), mode="replay", key=None)
    second = vl.fill_nulls(st, RULES, st.facts)
    assert fake2.calls == [] and second == first

    fake3 = live(monkeypatch, Fake("should not be called"))
    third = vl.fill_nulls(st, RULES, st.facts)
    assert fake3.calls == [] and third == first and vl.calls_used() == 0  # a cache hit costs nothing


def test_replay_with_an_empty_cache_fills_nothing(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake('{"product": "Term Loan"}'), mode="replay")
    st = state_for(tmp_path, drop=("Product",))
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts and fake.calls == []


def test_a_poisoned_cache_entry_is_revalidated_and_ignored(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"product": "Term Loan"}'))
    st = state_for(tmp_path, drop=("Product",))
    vl.fill_nulls(st, RULES, st.facts)
    (entry,) = list(Path(vl._cache_dir()).glob("*.json"))
    rec = json.loads(entry.read_text(encoding="utf-8"))
    rec["answer"] = {"product": "Not A Product"}
    entry.write_text(json.dumps(rec), encoding="utf-8")
    live(monkeypatch, Fake("unused"), mode="replay", key=None)
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts
    entry.write_text("{ not json", encoding="utf-8")
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts


# ---------------------------------------------------------------- failure never raises

@pytest.mark.parametrize("failure", [
    vl._VLError("timeout", retryable=True), vl._VLError("http 401"), RuntimeError("boom"), KeyError("x"),
    "", "plain words", "{\"product\": ",
])
def test_any_failure_means_no_fill_and_no_exception(monkeypatch, tmp_path, failure):
    live(monkeypatch, Fake(failure))
    st = state_for(tmp_path, drop=("Product",))
    assert vl.fill_nulls(st, RULES, st.facts) == st.facts


def test_retries_sleep_on_a_fixed_schedule(monkeypatch, tmp_path):
    fake = live(monkeypatch, Fake(vl._VLError("http 429", retryable=True), '{"product": "Term Loan"}'))
    slept: list[float] = []
    monkeypatch.setattr(vl, "_sleep", slept.append)
    st = state_for(tmp_path, drop=("Product",))
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out["product"].value == "Term Loan" and slept == [2.0] and len(fake.calls) == 2


# ---------------------------------------------------------------- key hygiene

def test_the_key_never_appears_anywhere(monkeypatch, tmp_path, caplog, capsys):
    """Real transport with a faked urlopen whose errors and replies are full of the key."""
    seen: list = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def boom(req, timeout=None):
        seen.append(req)
        raise urllib.error.HTTPError(req.full_url, 500, f"server said {SENTINEL}", {"X-Key": SENTINEL}, io.BytesIO(SENTINEL.encode()))

    monkeypatch.setenv("NEMOTRON_MODE", "live")
    monkeypatch.setenv("NVIDIA_API_KEY", SENTINEL)
    monkeypatch.setenv("NEMOTRON_VL_MODEL", PRIMARY)
    monkeypatch.setenv("NEMOTRON_VL_FALLBACK_MODEL", FALLBACK)
    monkeypatch.setattr(vl, "_sleep", lambda s: None)
    monkeypatch.setattr(vl.urllib.request, "urlopen", boom)
    vl.reset()
    caplog.set_level(logging.DEBUG)
    st = state_for(tmp_path, drop=("Product",))
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out == st.facts and seen
    assert seen[0].get_header("Authorization") == f"Bearer {SENTINEL}"  # the one place it belongs
    with pytest.raises(vl._VLError) as exc:
        vl._transport(PRIMARY, [], timeout=1, max_tokens=1)
    assert SENTINEL not in str(exc.value) and SENTINEL not in repr(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__

    def ok(req, timeout=None):
        return Resp(json.dumps({"choices": [{"message": {"content": '{"product": "Term Loan"}'}}]}).encode())

    monkeypatch.setattr(vl.urllib.request, "urlopen", ok)
    vl.reset()
    out = vl.fill_nulls(st, RULES, st.facts)
    assert out["product"].value == "Term Loan"

    blob = json.dumps({k: v.model_dump() for k, v in out.items()}) + json.dumps(vl.fills_for(st))
    blob += caplog.text + "".join(capsys.readouterr())
    for p in Path(vl._cache_dir()).glob("*"):
        blob += p.read_text(encoding="utf-8")
    assert SENTINEL not in blob


def test_http_replies_are_classified_without_the_body(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", SENTINEL)

    def raising(exc):
        def fn(req, timeout=None):
            raise exc
        return fn

    cases = [(urllib.error.HTTPError("u", 429, "m", {}, io.BytesIO(b"")), True),
             (urllib.error.HTTPError("u", 401, "m", {}, io.BytesIO(b"")), False),
             (TimeoutError("t"), True), (urllib.error.URLError("down"), True), (ValueError("x"), False)]
    for exc, retryable in cases:
        monkeypatch.setattr(vl.urllib.request, "urlopen", raising(exc))
        with pytest.raises(vl._VLError) as e:
            vl._transport(PRIMARY, [], timeout=1, max_tokens=1)
        assert e.value.retryable is retryable


# ---------------------------------------------------------------- determinism and non-interference

def test_two_identical_runs_give_identical_output(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"product": "Term Loan", "term_months": 18}'))
    st = state_for(tmp_path, drop=("Product", "Requested term"))
    a = {k: v.model_dump() for k, v in vl.fill_nulls(st, RULES, st.facts).items()}
    vl.reset()
    b = {k: v.model_dump() for k, v in vl.fill_nulls(st, RULES, st.facts).items()}
    assert a == b


def test_vl_never_adds_an_issue_entity_or_hold(monkeypatch, tmp_path):
    live(monkeypatch, Fake('{"product": "Term Loan"}'))
    st = state_for(tmp_path, drop=("Product",))
    issues, entities, fraud = list(st.issues), list(st.entities), st.fraud.model_copy()
    vl.fill_nulls(st, RULES, st.facts)
    assert (st.issues, st.entities, st.fraud) == (issues, entities, fraud)


def test_module_text_has_no_clock_calls():
    src = (Path(__file__).resolve().parents[1] / "prism" / "read" / "vl.py").read_text(encoding="utf-8")
    assert "time.time" not in src and "datetime.now" not in src and "import random" not in src


# ---------------------------------------------------------------- wiring through run_sim (Task 3)

def _deal_rows(tmp_path, drop=("Product",)):
    st = make_state(tmp_path, parts={"application": app_pages(drop), "id": clean_id()}, folder="Deal")
    rows = [{"path": d.path, "arrive_at": d.arrive_at.isoformat()} for d in st.docs]
    arr = tmp_path / "arrivals.jsonl"
    arr.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return arr


def _run(tmp_path, arr):
    from prism.engine.run import run_sim
    from prism.engine.writer import build_submission, dumps

    result = run_sim(tmp_path, arr, data_root=tmp_path)
    payload = build_submission(result.states, result.history_flags, result.errors, team="t", set_name="s")
    return result, dumps(payload), payload["deals"]["Deal"]["extracted"]


def test_off_replay_empty_and_live_agree_except_for_the_one_filled_field(monkeypatch, tmp_path):
    arr = _deal_rows(tmp_path)
    fake = live(monkeypatch, Fake('{"product": "Term Loan"}'), mode="off")
    _r, off_text, off_ext = _run(tmp_path, arr)
    assert fake.calls == [] and off_ext["product"] is None

    live(monkeypatch, fake, mode="replay")
    _r, replay_empty_text, _e = _run(tmp_path, arr)
    assert replay_empty_text == off_text and fake.calls == []

    live(monkeypatch, fake)
    result, live_text, live_ext = _run(tmp_path, arr)
    assert fake.calls and live_ext["product"] == "Term Loan"
    assert {k: v for k, v in live_ext.items() if k != "product"} == {k: v for k, v in off_ext.items() if k != "product"}
    assert result.states["Deal"].features["vl_fills"]["product"]["source"] == "nemotron_vl"
    n = len(fake.calls)
    assert n <= 30 and result.states["Deal"].error is None

    live(monkeypatch, fake, mode="replay")  # recorded cache: same answer, no transport call
    fake.calls.clear()
    _r, replay_text, _e = _run(tmp_path, arr)
    assert replay_text == live_text and fake.calls == []

    live(monkeypatch, fake)  # rerun live with the cache present: identical, still no call
    _r, again_text, _e = _run(tmp_path, arr)
    assert again_text == live_text and fake.calls == []


def test_a_failing_api_leaves_the_scored_output_unchanged(monkeypatch, tmp_path):
    arr = _deal_rows(tmp_path)
    live(monkeypatch, Fake("x"), mode="off")
    _r, off_text, _e = _run(tmp_path, arr)
    fake = live(monkeypatch, Fake(vl._VLError("http 429", retryable=True)))
    _r, down_text, _e = _run(tmp_path, arr)
    assert fake.calls and down_text == off_text
    assert vl.calls_used() <= 30


def test_run_sim_resets_the_call_budget(monkeypatch, tmp_path):
    arr = _deal_rows(tmp_path)
    live(monkeypatch, Fake('{"product": "Term Loan"}'))
    vl._RUN.calls = 30  # exhausted by an earlier run
    result, _t, ext = _run(tmp_path, arr)
    assert ext["product"] == "Term Loan"  # the reset at the start of run_sim restored the budget


def test_extract_facts_survives_a_crashing_vl_module(monkeypatch, tmp_path):
    st = state_for(tmp_path, drop=("Product",))
    plain = dict(st.facts)

    def crash(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("prism.read.extract.read_vl.fill_nulls", crash)
    assert extract_facts(st, RULES) == plain


def test_extract_features_reports_vl_fills_only_when_there_are_some(monkeypatch, tmp_path):
    from prism.read.extract import extract_features

    st = state_for(tmp_path, drop=("Product",))
    assert "vl_fills" not in extract_features(st, RULES)
    live(monkeypatch, Fake('{"product": "Term Loan"}'))
    st.facts = extract_facts(st, RULES)
    assert extract_features(st, RULES)["vl_fills"]["product"]["model"] == PRIMARY


# ---------------------------------------------------------------- grounding and placeholders (HI-06)

@pytest.mark.parametrize("answer", ['{"legal_name": "Invented Holdings Incorporated"}', '{"legal_name": "N/A"}',
                                    '{"legal_name": "Not provided"}'])
def test_a_placeholder_or_ungrounded_name_is_never_filled(monkeypatch, tmp_path, answer):
    live(monkeypatch, Fake(answer))
    st = state_for(tmp_path, drop=("Legal business name",), parts={k: None for k in ("id", "irs", "cert", "tax", "fs", "letter")})
    assert "legal_name" not in st.facts
    out = vl.fill_nulls(st, RULES, st.facts)
    assert "legal_name" not in out and vl.fills_for(st) == {}
