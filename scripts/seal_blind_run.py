"""Guarded sealing of the blind weekend-drop first run (D-25, D-46, MODEL_DESIGN M20).

Two commands, standard library only:

  seal   Run the frozen engine twice on the drop, validate, and write a commit-safe record
         (commit, tag, SHA-256 of the first output, aggregate counts, verdicts). Refuses a dirty
         engine tree, a tag that is not HEAD, an existing record and an existing gold_drop file.
  score  Verify the first-run output still hashes to the sealed record, score it against the
         blind labels, and write an aggregate-only score (counts, never names).

Nothing here prints or writes a deal folder name, a history id or an extracted value.
All engine work goes through the repository's own CLI (``python -m prism``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = Path("out/blind_drop")
DEFAULT_RECORD = Path("eval/blind_drop_record.json")
DEFAULT_GOLD = Path("eval/gold_drop.json")
DEFAULT_SCORE_OUT = Path("eval/blind_drop_score.json")
REFUSED = 2

_RUN_LINE = re.compile(r"(\d+)\s+errors\b")


# --------------------------------------------------------------------------- pure functions


def aggregate_submission(sub: Mapping[str, Any]) -> dict[str, int]:
    """Counts only: deals, held, flagged, history_flags."""
    deals = sub.get("deals") or {}
    held = sum(1 for d in deals.values() if (d.get("fraud") or {}).get("held") is True)
    flagged = sum(1 for d in deals.values() if (d.get("fraud") or {}).get("flagged") is True)
    return {
        "deals": len(deals),
        "held": held,
        "flagged": flagged,
        "history_flags": len(sub.get("history_flags") or []),
    }


def refusal_reason(
    record_exists: bool,
    gold_exists: bool,
    tree_dirty: bool,
    tag: Optional[str],
    tag_commit: Optional[str],
    head: str,
) -> Optional[str]:
    """Why sealing must not run, or None when it may."""
    if record_exists:
        return "a sealed record already exists; the first run is never overwritten"
    if gold_exists:
        return "the gold_drop labels already exist; labels must come after the seal"
    if tree_dirty:
        return "prism/ or config/ has uncommitted changes; the engine must be the frozen commit"
    if tag is not None and tag_commit != head:
        return f"tag {tag!r} is not the current commit; seal only on the tagged frozen engine"
    return None


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha(path: Path | str, expected: str) -> bool:
    return sha256_file(path) == expected


def aggregate_score(score: Mapping[str, Any], sub: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate counts from a score result and the scored submission. No names, no ids."""
    deals = score.get("deals") or {}
    gold_flagged = held_of_gold = false_flags = 0
    for d in deals.values():
        fr = d.get("fraud") or {}
        want = bool((fr.get("flagged") or {}).get("want"))
        got = bool((fr.get("flagged") or {}).get("got"))
        if want:
            gold_flagged += 1
            if (fr.get("held") or {}).get("got"):
                held_of_gold += 1
        elif got:
            false_flags += 1

    stage_at_flag: dict[str, int] = {}
    held_before_closed = 0
    for d in (sub.get("deals") or {}).values():
        fr = d.get("fraud") or {}
        if fr.get("held") is not True:
            continue
        stage = fr.get("stage_at_flag")
        if stage:
            stage_at_flag[stage] = stage_at_flag.get(stage, 0) + 1
        if not any((e or {}).get("stage") == "Closed" for e in d.get("stage_log") or []):
            held_before_closed += 1

    hist = score.get("history") or {}
    return {
        "scored_deals": len(deals),
        "gold_flagged": gold_flagged,
        "held_of_gold_flagged": held_of_gold,
        "false_flags": false_flags,
        "history_missed": len(hist.get("missed") or []),
        "history_extra": len(hist.get("extra") or []),
        "history_must_not_flagged": len(hist.get("must_not_flagged") or []),
        "stage_at_flag": dict(sorted(stage_at_flag.items())),
        "held_before_closed": held_before_closed,
    }


def parse_run_errors(line: str) -> Optional[int]:
    m = _RUN_LINE.search(line)
    return int(m.group(1)) if m else None


def inside_data_dir(path: Path) -> bool:
    """True if any component of the resolved path is a folder named data."""
    return "data" in Path(os.path.abspath(path)).parts or "data" in path.resolve().parts


# --------------------------------------------------------------------------- git and CLI helpers


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _prism(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, "-m", "prism", *args], cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", env=env,
    )


def _under_repo(p: Path) -> Path:
    return p if p.is_absolute() else REPO / p


def _safe_command(argv: list[str]) -> str:
    """The command line with any argument that points inside data/ redacted."""
    out = []
    for a in argv:
        looks_like_path = "/" in a or "\\" in a or a.endswith((".jsonl", ".json"))
        if looks_like_path and "data" in Path(a).parts:
            out.append("<data-path>")
        else:
            out.append(a)
    return " ".join(out)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- commands


def cmd_seal(a: argparse.Namespace) -> int:
    out_dir = _under_repo(a.out_dir)
    record = _under_repo(a.record)
    gold = _under_repo(a.gold)
    for p in (out_dir, record):
        if inside_data_dir(p):
            print(f"refused: output path is inside a data folder: {p.name}", file=sys.stderr)
            return REFUSED

    head = _git("rev-parse", "HEAD")
    tree_dirty = bool(_git("status", "--porcelain", "--", "prism", "config"))
    tag_commit: Optional[str] = None
    if a.tag:
        try:
            tag_commit = _git("rev-parse", f"{a.tag}^{{commit}}")
        except subprocess.CalledProcessError:
            tag_commit = None
    reason = refusal_reason(record.exists(), gold.exists(), tree_dirty, a.tag, tag_commit, head)
    if reason:
        print(f"refused: {reason}")
        return REFUSED

    folder = a.folder.resolve()
    arrivals = a.arrivals.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run1, run2 = out_dir / "run1.json", out_dir / "run2.json"
    common = ["--folder", str(folder), "--arrivals", str(arrivals)]
    if a.data_root:
        common += ["--data-root", str(a.data_root.resolve())]

    run_line = ""
    run_rc = 0
    for target in (run1, run2):
        r = _prism("run", *common, "--out", str(target))
        if r.returncode == 2 or not target.is_file():
            print(f"run failed (exit {r.returncode}); nothing sealed")
            return 1
        if target is run1:
            run_rc = r.returncode
            run_line = next((ln for ln in r.stdout.splitlines() if ln.startswith("run:")), "")

    val = _prism("validate", str(run1), "--folder", str(folder))
    validator_ok = val.returncode == 0
    deterministic = run1.read_bytes() == run2.read_bytes()
    sub = _read_json(run1)
    agg = aggregate_submission(sub)

    rec: dict[str, Any] = {
        "commit": head,
        "tag": a.tag,
        "sealed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deals": agg["deals"],
        "errors": parse_run_errors(run_line),
        "held": agg["held"],
        "flagged": agg["flagged"],
        "history_flags": agg["history_flags"],
        "validator_ok": validator_ok,
        "deterministic": deterministic,
        "run_exit_code": run_rc,
        "run1_sha256": sha256_file(run1),
        "command": _safe_command(["prism", "run", *common]),
    }
    _write_json(record, rec)
    for k in ("commit", "tag", "deals", "errors", "held", "flagged", "history_flags",
              "validator_ok", "deterministic", "run1_sha256"):
        print(f"{k}: {rec[k]}")
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    out_dir = _under_repo(a.out_dir)
    record_path = _under_repo(a.record)
    gold = _under_repo(a.gold)
    score_out = _under_repo(a.score_out)
    if inside_data_dir(score_out):
        print("refused: score output is inside a data folder", file=sys.stderr)
        return REFUSED
    for label, p in (("record", record_path), ("labels", gold), ("first-run output", out_dir / "run1.json")):
        if not p.is_file():
            print(f"error: {label} not found: {p.name}", file=sys.stderr)
            return REFUSED
    rec = _read_json(record_path)
    run1 = out_dir / "run1.json"
    if not verify_sha(run1, rec["run1_sha256"]):
        print("output changed since sealing")
        return REFUSED

    score_json = out_dir / "score.json"
    r = _prism("score", str(run1), "--gold", str(gold), "--out", str(score_json))
    if r.returncode != 0:
        print(f"score failed (exit {r.returncode})")
        return 1
    agg = aggregate_score(_read_json(score_json), _read_json(run1))
    result = {"record_commit": rec["commit"], "run1_sha256": rec["run1_sha256"], **agg}
    _write_json(score_out, result)
    for k, v in result.items():
        print(f"{k}: {v}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seal_blind_run", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("seal", help="guarded frozen first run and aggregate record")
    s.add_argument("--folder", required=True, type=Path)
    s.add_argument("--arrivals", required=True, type=Path)
    s.add_argument("--data-root", type=Path, default=None)
    s.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    s.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    s.add_argument("--gold", type=Path, default=DEFAULT_GOLD, help="labels file whose existence blocks sealing")
    s.add_argument("--tag", default=None, help="frozen-baseline tag; must resolve to HEAD")
    c = sub.add_parser("score", help="verify the sealed output, then score it against the blind labels")
    c.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    c.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    c.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    c.add_argument("--score-out", type=Path, default=DEFAULT_SCORE_OUT)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return REFUSED
    return {"seal": cmd_seal, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
