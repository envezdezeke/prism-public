"""Optional live smoke for the Nemotron VL null-fill (03-04 Task 3). Never prints the key or any value.

    PYTHONUTF8=1 python scripts/vl_smoke.py

Takes up to two TUNING applications, masks one field at a time IN MEMORY (the label line and its value line are
removed from the text the model sees; nothing is written to disk) and checks that the VL fill gives back the
value the parser read (a null answer is correct behaviour when the masked text no longer states the value).
Prints correct / wrong / left-null counts by field NAME only. At most 18 live calls. With no usable key or
mode off it prints "skipped: no key" and exits 0.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism.engine.config import load_rules  # noqa: E402
from prism.engine.state import DealState, Doc  # noqa: E402
from prism.read import docmodel, vl  # noqa: E402
from prism.read.classify import classify  # noqa: E402
from prism.read.extract import extract_facts  # noqa: E402
from prism.read.settings import get_config  # noqa: E402
from prism.read.text import DocText, load_text  # noqa: E402

TZ = timezone(timedelta(hours=-6))
MAX_APPS, MAX_CALLS = 2, 18


def _tuning_folders(data_root: Path) -> list[Path]:
    hold = set(json.loads((ROOT / "eval" / "holdout.json").read_text(encoding="utf-8"))["holdout"])
    return sorted(p for p in (data_root / "shared_folder").iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name not in hold)


def _label_words(field: str) -> set[str]:
    labels = get_config().labels.get("application", {})
    return {str(x).casefold() for x in labels.get(field, ())}


def _masked_text(dt: DocText, field: str) -> DocText:
    """Drop the field's label line (and, for a label on its own line, the value line after it)."""
    words = _label_words(field)
    lines = (dt.plain or dt.layout).splitlines()
    out: list[str] = []
    skip = False
    for ln in lines:
        if skip:
            skip = False
            continue
        low = " ".join(ln.casefold().split())
        if low in words:
            skip = True
            continue
        if any(low.startswith(w + ":") for w in words):
            continue
        out.append(ln)
    text = "\n".join(out)
    return DocText(kind=dt.kind, plain=text, layout=text, pages=(text,), n_pages=dt.n_pages)


def main() -> int:
    if vl.mode() != "live" or not vl._models():
        print("skipped: no key")
        return 0
    data_root = Path(__import__("os").environ.get("PRISM_SAMPLE_DATA") or ROOT / "data" / "participant")
    if not (data_root / "shared_folder").is_dir():
        print("skipped: no sample data")
        return 0
    rules = load_rules(data_root)
    fields = [str(f) for f in vl._cfg().get("fillable") or []]
    ok: dict[str, int] = {f: 0 for f in fields}
    wrong: dict[str, int] = {f: 0 for f in fields}
    asked: dict[str, int] = {f: 0 for f in fields}
    used = 0
    done_apps = 0
    vl.reset()
    for folder in _tuning_folders(data_root):
        if done_apps >= MAX_APPS or used >= MAX_CALLS:
            break
        docs = []
        for i, p in enumerate(sorted(folder.iterdir())):
            if p.is_file():
                d = Doc(path=f"{folder.name}/{p.name}", name=p.name, arrive_at=datetime(2026, 11, 2, tzinfo=TZ) + timedelta(minutes=i),
                        abs_path=p)
                classify(d)
                docs.append(d)
        state = DealState(folder=folder.name, docs=docs)
        apps = docmodel.parsed_all(state, "application")
        if not apps:
            continue
        done_apps += 1
        app_doc, app = apps[-1]
        base = extract_facts_no_vl(state, rules)
        real_text = load_text(app_doc)
        for f in fields:
            if f not in base or used >= MAX_CALLS:
                continue
            masked_facts = {k: v for k, v in base.items() if k != f}
            masked_app = replace(app, missing=(f,), declared_none=())
            orig_all, orig_load = docmodel.parsed_all, vl.load_text
            docmodel.parsed_all = lambda st, cls, _o=orig_all: [(app_doc, masked_app)] if cls == "application" else _o(st, cls)
            vl.load_text = lambda doc, _t=_masked_text(real_text, f), _o=orig_load: _t if doc is app_doc else _o(doc)
            try:
                before = vl.calls_used()
                got = vl.fill_nulls(state, rules, masked_facts)
                used += vl.calls_used() - before
            finally:
                docmodel.parsed_all, vl.load_text = orig_all, orig_load
            asked[f] += 1
            if f in got and got[f].value == base[f].value:
                ok[f] += 1
            elif f in got:
                wrong[f] += 1
    print(f"vl smoke: {done_apps} tuning application(s), {used} live call(s)")
    for f in fields:
        if asked[f]:
            print(f"  {f}: {ok[f]}/{asked[f]} correct, {wrong[f]} wrong, {asked[f] - ok[f] - wrong[f]} left null")
    return 0


def extract_facts_no_vl(state: DealState, rules) -> dict:
    orig = vl.fill_nulls
    vl.fill_nulls = lambda st, ru, facts: dict(facts)
    try:
        return extract_facts(state, rules)
    finally:
        vl.fill_nulls = orig


if __name__ == "__main__":
    raise SystemExit(main())
