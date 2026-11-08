"""Tracer: one application PDF to legal_name and amount_requested through run, validate, score."""
from __future__ import annotations

import json

from prism.engine.run import run_sim
from prism.engine.validate import validate_submission_data
from prism.engine.writer import build_submission
from prism.read.settings import get_config
from tests.pdfgen import application_pdf_pages, write_pdf
from tests.real_data import load_score_split, requires_sample, sample_root, tuning_folders


def _run(tmp_path, layout="next_line", **over):
    folder = tmp_path / "shared"
    write_pdf(folder / "Deal One" / "doc.pdf", application_pdf_pages(layout, **over), producer="Maker 1")
    arr = tmp_path / "arrivals.jsonl"
    arr.write_text(json.dumps({"path": "Deal One/doc.pdf", "arrive_at": "2026-11-02T09:00-06:00"}) + "\n",
                   encoding="utf-8")
    return folder, arr, run_sim(folder, arr, data_root=tmp_path)


def test_application_pdf_yields_facts_with_source_and_confidence(tmp_path):
    _folder, _arr, result = _run(tmp_path)
    st = result.states["Deal One"]
    assert st.error is None
    assert st.facts["legal_name"].value == "Nimbus Orchard Supply LLC"
    assert st.facts["amount_requested"].value == 125000
    for name in ("legal_name", "amount_requested"):
        assert st.facts[name].source_doc == "Deal One/doc.pdf"
        assert st.facts[name].confidence == get_config().confidence["label"]
    payload = build_submission(result.states, result.history_flags, result.errors, team="T", set_name="s")
    assert validate_submission_data(payload, folders={"Deal One"}).ok
    assert payload["deals"]["Deal One"]["extracted"]["legal_name"] == "Nimbus Orchard Supply LLC"


def test_layouts_agree(tmp_path):
    got = {}
    for layout in ("next_line", "colon", "columns"):
        d = tmp_path / layout
        d.mkdir()
        _f, _a, result = _run(d, layout)
        st = result.states["Deal One"]
        got[layout] = (st.facts["legal_name"].value, st.facts["amount_requested"].value)
    assert len(set(got.values())) == 1, got


def test_second_run_is_byte_identical(tmp_path):
    folder, arr, r1 = _run(tmp_path)
    r2 = run_sim(folder, arr, data_root=tmp_path)
    kw = dict(team="T", set_name="s")
    a = build_submission(r1.states, r1.history_flags, r1.errors, **kw)
    b = build_submission(r2.states, r2.history_flags, r2.errors, **kw)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@requires_sample
def test_tuning_deals_name_and_amount_correct(tmp_path):
    root = sample_root()
    ss = load_score_split()
    result = run_sim(root / "shared_folder", root / "sim" / "arrivals.jsonl", data_root=root)
    payload = build_submission(result.states, result.history_flags, result.errors, team="T", set_name="s")
    gold = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "eval" / "gold.json").read_text(encoding="utf-8"))
    from tests.real_data import holdout_names

    res = ss.score_tuning(payload, gold, holdout_names(), ["legal_name", "amount_requested"])
    t = res["totals"]["extracted"]
    assert len(tuning_folders(root)) == 10
    bad = sorted({f for d in res["deals"].values() for f, c in d["extracted"].items() if not c["ok"]})
    assert (t["ok"], t["total"]) == (20, 20), f"failing fields: {bad}"
