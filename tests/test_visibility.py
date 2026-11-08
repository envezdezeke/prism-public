"""Arrival gate: inclusive cutoff, stable ties, fixed -06:00, untrusted paths."""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

import pytest

from prism.engine.visibility import SIM_TZ, Arrival, ArrivalLog, parse_time
from tests.conftest import TINY_ARRIVALS, write_arrivals


def log_from(tmp_path, rows):
    return ArrivalLog.from_jsonl(write_arrivals(tmp_path / "a.jsonl", rows))


def test_cutoff_is_inclusive(tmp_path):
    log = log_from(tmp_path, TINY_ARRIVALS)
    t = parse_time("2026-01-05T09:45-06:00")
    assert [a.path for a in log.visible_files(t)] == [
        "Alpha Trading/01_app.pdf",
        "Beta & Sons/02_id.pdf",
        "Alpha Trading/02_id.pdf",
    ]
    before = t - timedelta(minutes=1)
    assert [a.path for a in log.visible_files(before)] == [
        "Alpha Trading/01_app.pdf",
        "Beta & Sons/02_id.pdf",
    ]
    assert log.visible_files(parse_time("2026-01-05T09:29-06:00")) == []


def test_ties_sorted_by_path_regardless_of_file_order(tmp_path):
    rows = [{"path": f"D/{n}.pdf", "arrive_at": "2026-01-05T09:30-06:00"} for n in "cab"]
    rng = random.Random(7)
    seen = set()
    for _ in range(5):
        rng.shuffle(rows)
        log = log_from(tmp_path, rows)
        seen.add(tuple(a.path for a in log.visible_files(parse_time("2026-01-06T00:00-06:00"))))
    assert seen == {("D/a.pdf", "D/b.pdf", "D/c.pdf")}


@pytest.mark.parametrize(
    "raw",
    ["2026-01-05T09:30", "2026-01-05T15:30Z", "2026-01-05T09:30-06:00", "2026-01-05T10:30-05:00"],
)
def test_parse_time_lands_on_minus_six(raw):
    dt = parse_time(raw)
    assert dt.utcoffset() == timedelta(hours=-6)
    assert dt.isoformat(timespec="minutes") == "2026-01-05T09:30-06:00"


def test_naive_now_read_as_sim_time(tmp_path):
    log = log_from(tmp_path, TINY_ARRIVALS)
    naive = datetime(2026, 1, 5, 9, 30)
    assert len(log.visible_files(naive)) == 2


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="no time.tzset on this platform")
def test_machine_timezone_does_not_matter(tmp_path):
    old = os.environ.get("TZ")
    try:
        results = []
        for tz in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            os.environ["TZ"] = tz
            time.tzset()
            log = log_from(tmp_path, TINY_ARRIVALS)
            results.append(
                [a.path for a in log.visible_files(parse_time("2026-01-05T09:45"))]
            )
        assert results[0] == results[1] == results[2]
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_malformed_lines_collected_good_lines_survive(tmp_path):
    p = tmp_path / "a.jsonl"
    lines = [
        json.dumps({"path": "A/1.pdf", "arrive_at": "2026-01-05T09:30-06:00"}),
        "{not json",
        "",
        json.dumps({"arrive_at": "2026-01-05T09:30-06:00"}),
        json.dumps({"path": "A/2.pdf"}),
        json.dumps({"path": "A/3.pdf", "arrive_at": "garbage"}),
        json.dumps(["not", "an", "object"]),
        json.dumps({"path": "A/4.pdf", "arrive_at": "2026-01-05T09:31-06:00"}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log = ArrivalLog.from_jsonl(p)
    assert [a.path for a in log.arrivals] == ["A/1.pdf", "A/4.pdf"]
    assert sorted(e.split(":")[0] for e in log.errors) == [
        "line 2", "line 4", "line 5", "line 6", "line 7",
    ]


@pytest.mark.parametrize(
    "bad", ["/etc/passwd", "C:/Windows/x.pdf", "../x/1.pdf", "A/../../x.pdf", "A\\..\\x.pdf", ""]
)
def test_unsafe_paths_rejected(tmp_path, bad):
    log = log_from(tmp_path, [{"path": bad, "arrive_at": "2026-01-05T09:30-06:00"}])
    assert log.arrivals == []
    assert len(log.errors) == 1


def test_backslashes_normalised(tmp_path):
    log = log_from(tmp_path, [{"path": "A\\sub\\1.pdf", "arrive_at": "2026-01-05T09:30-06:00"}])
    assert log.arrivals[0].path == "A/sub/1.pdf"
    assert log.arrivals[0].folder == "A"
    assert log.arrivals[0].name == "1.pdf"


def test_folders_exclude_root_level_notice(tmp_path):
    log = log_from(tmp_path, TINY_ARRIVALS)
    assert log.folders() == ["Alpha Trading", "Beta & Sons"]
    assert log.unattached() == ["notice.txt"]
    assert log.times()[0] == parse_time("2026-01-05T09:30")
    assert len(log.times()) == 4


def test_arrival_dataclass_and_zone():
    a = Arrival("X/y.pdf", datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc))
    log = ArrivalLog([a])
    assert log.arrivals[0].arrive_at.utcoffset() == timedelta(hours=-6)
    assert SIM_TZ.utcoffset(None) == timedelta(hours=-6)
