"""Engine loop: per-tick issue refresh, features merge, quiet-expiry ticks (D-11, D-14, D-16)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from prism.engine.run import run_sim
from prism.engine.visibility import SIM_TZ, ArrivalLog, parse_time
from prism.read import extract as read_extract
from prism.read import issues as read_issues


def _setup(tmp_path, rows):
    folder = tmp_path / "shared"
    for r in rows:
        p = folder.joinpath(*r["path"].split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    arr = tmp_path / "arrivals.jsonl"
    arr.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return folder, arr


ROWS = [
    {"path": "A/1.pdf", "arrive_at": "2026-11-02T09:00-06:00"},
    {"path": "A/2.pdf", "arrive_at": "2026-11-02T09:10-06:00"},
    {"path": "A/3.pdf", "arrive_at": "2026-11-02T12:00-06:00"},
    {"path": "B/1.pdf", "arrive_at": "2026-11-02T09:35-06:00"},
]


def _tick_times(tmp_path, monkeypatch, **kw):
    seen: list[datetime] = []

    def spy(state, rules, now):
        if now not in seen:
            seen.append(now)
        return []

    monkeypatch.setattr(read_issues, "detect_issues", spy)
    folder, arr = _setup(tmp_path, ROWS)
    run_sim(folder, arr, data_root=tmp_path, **kw)
    return seen


def _hm(dts):
    return [d.strftime("%H:%M") for d in dts]


def test_quiet_expiry_ticks_present_and_absent(tmp_path, monkeypatch):
    got = _hm(_tick_times(tmp_path, monkeypatch))
    # A: 09:10 starts a gap (next arrival is 12:00) -> 09:40; 12:00 -> 12:30 (also the end tick).
    # A's 09:00 is followed by 09:10 inside the window -> no 09:30 tick. B: 09:35 -> 10:05.
    assert "09:40" in got and "12:30" in got and "10:05" in got
    assert "09:30" not in got
    assert got == sorted(got)


def test_no_tick_beyond_last_arrival_without_grace(tmp_path, monkeypatch):
    got = _hm(_tick_times(tmp_path, monkeypatch, end_grace_minutes=0))
    assert "12:30" not in got and got[-1] == "12:00"
    assert "09:40" in got


def test_detect_issues_called_every_tick_for_every_live_deal(tmp_path, monkeypatch):
    calls: list[tuple[str, datetime]] = []

    def spy(state, rules, now):
        calls.append((state.folder, now))
        return []

    monkeypatch.setattr(read_issues, "detect_issues", spy)
    rows = ROWS + [{"path": "C/1.pdf", "arrive_at": "2026-11-02T13:00-06:00"}]
    folder, arr = _setup(tmp_path, rows)
    (folder / "Empty").mkdir()
    result = run_sim(folder, arr, data_root=tmp_path)
    end = parse_time("2026-11-02T13:30")
    assert ("A", end) in calls and ("B", end) in calls and ("C", end) in calls  # the end tick included
    assert not any(name == "Empty" for name, _ in calls)  # no docs, skipped
    t_0940 = parse_time("2026-11-02T09:40")
    assert ("A", t_0940) in calls and ("B", t_0940) in calls  # B had no new file at 09:40
    assert not any(name == "C" and t < parse_time("2026-11-02T13:00") for name, t in calls)
    assert result.states["A"].error is None


def test_errored_deal_is_skipped(tmp_path, monkeypatch):
    calls = []

    def spy(state, rules, now):
        calls.append(state.folder)
        if state.folder == "A":
            raise ValueError("boom")
        return []

    monkeypatch.setattr(read_issues, "detect_issues", spy)
    folder, arr = _setup(tmp_path, ROWS)
    result = run_sim(folder, arr, data_root=tmp_path)
    assert result.states["A"].error and result.states["A"].error.startswith("detect_issues: ValueError")
    assert calls.count("A") == 1  # frozen after the crash
    assert calls.count("B") > 1


def test_resolved_issues_records_an_issue_that_disappears(tmp_path, monkeypatch):
    def fake(state, rules, now):
        return ["MISSING_DOC:ein_letter"] if now < parse_time("2026-11-02T10:00") else []

    monkeypatch.setattr(read_issues, "detect_issues", fake)
    folder, arr = _setup(tmp_path, ROWS)
    st = run_sim(folder, arr, data_root=tmp_path).states["A"]
    assert st.issues == [] and st.resolved_issues == ["MISSING_DOC:ein_letter"]


def test_features_merge_not_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(read_extract, "extract_features", lambda state, rules: {"deposit_months": [1, 2]})
    real_facts = read_extract.extract_facts

    def facts_with_profile(state, rules):
        state.features["profile"] = {"dba": "x"}
        return real_facts(state, rules)

    monkeypatch.setattr(read_extract, "extract_facts", facts_with_profile)
    folder, arr = _setup(tmp_path, ROWS)
    st = run_sim(folder, arr, data_root=tmp_path).states["A"]
    assert st.features["profile"] == {"dba": "x"} and st.features["deposit_months"] == [1, 2]


def test_non_dict_features_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(read_extract, "extract_features", lambda state, rules: None)
    folder, arr = _setup(tmp_path, ROWS)
    assert run_sim(folder, arr, data_root=tmp_path).states["A"].features == {}


def test_expiry_ticks_helper_matches_spec():
    from prism.engine.run import _quiet_expiry_ticks
    from prism.engine.visibility import Arrival

    t0 = datetime(2026, 11, 2, 9, 0, tzinfo=SIM_TZ)
    alog = ArrivalLog([Arrival("A/1", t0), Arrival("A/2", t0 + timedelta(minutes=30)),
                       Arrival("notice.txt", t0 + timedelta(minutes=5))])
    got = _quiet_expiry_ticks(alog, timedelta(minutes=30), None)
    # A/1: the next arrival at +30 is not strictly inside (t, t+30) -> expiry 09:30; A/2 expiry is past the last arrival
    assert got == [t0 + timedelta(minutes=30)]
