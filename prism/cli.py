"""Command line: ``python -m prism run | validate | score``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from prism.engine.config import ConfigError
from prism.engine.visibility import parse_time


def _time_arg(s: str):
    try:
        return parse_time(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"bad time {s!r}: {e}") from e


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prism", description="Prism engine")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="run the engine and write the submission JSON")
    r.add_argument("--folder", required=True, type=Path, help="shared folder (one subfolder per deal)")
    r.add_argument("--arrivals", required=True, type=Path, help="arrivals.jsonl")
    r.add_argument("--out", type=Path, default=Path("out/submission.json"))
    r.add_argument("--set", dest="set_name", default="sample")
    r.add_argument("--team", default="Prism")
    r.add_argument("--data-root", type=Path, default=None,
                   help="dir holding rules/, contacts/, sim/, history/ (default: parent of --folder)")
    r.add_argument("--fraud-config", type=Path, default=None)
    r.add_argument("--end", type=_time_arg, default=None,
                   help="stop the sim clock at this time (default: last arrival + --end-grace-minutes)")
    r.add_argument("--end-grace-minutes", type=int, default=30,
                   help="when --end is not given, keep the clock running this long after the last "
                        "arrival so quiet-window actions fire (default 30, the J11 quiet window; 0 = stop at last arrival)")

    v = sub.add_parser("validate", help="validate a submission JSON file")
    v.add_argument("file", type=Path)
    v.add_argument("--folder", type=Path, default=None, help="deal keys must equal its subfolders")
    v.add_argument("--arrivals", type=Path, default=None, help="deal keys must equal its folders")

    s = sub.add_parser("score", help="self-score a submission against eval/gold.json")
    s.add_argument("submission", type=Path)
    s.add_argument("--gold", type=Path, default=Path("eval/gold.json"))
    s.add_argument("--out", type=Path, default=Path("out/score_latest.json"))
    return p


def _cmd_run(a: argparse.Namespace) -> int:
    from prism.engine.run import run_sim
    from prism.engine.validate import validate_submission_data
    from prism.engine.writer import build_submission, write_submission

    if not a.arrivals.is_file():
        print(f"error: arrivals file not found: {a.arrivals}", file=sys.stderr)
        return 2
    if not a.folder.is_dir():
        print(f"warning: folder not found, deals come from arrivals only: {a.folder}", file=sys.stderr)
    data_root = a.data_root if a.data_root is not None else a.folder.resolve().parent
    try:
        result = run_sim(a.folder, a.arrivals, data_root=data_root, fraud_path=a.fraud_config, end=a.end,
                         end_grace_minutes=a.end_grace_minutes)
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    payload = build_submission(
        result.states, result.history_flags, result.errors, team=a.team, set_name=a.set_name
    )
    write_submission(payload, a.out)
    v = validate_submission_data(payload, folders=set(result.states))
    n_err = len(result.errors) + sum(1 for s in result.states.values() if s.error)
    print(
        f"run: {len(result.states)} deals, {result.n_files} files, {result.n_ticks} ticks, "
        f"{n_err} errors -> {a.out}"
    )
    for e in v.errors:
        print(f"ERROR: {e}")
    for w in v.warnings:
        print(f"WARN: {w}")
    return 0 if v.ok else 1


def _cmd_validate(a: argparse.Namespace) -> int:
    from prism.engine.run import deal_dirs
    from prism.engine.validate import validate_submission_file
    from prism.engine.visibility import ArrivalLog

    if not a.file.is_file():
        print(f"error: file not found: {a.file}", file=sys.stderr)
        return 2
    folders: Optional[set[str]] = None
    if a.folder is not None:
        folders = set(deal_dirs(a.folder))
    if a.arrivals is not None:
        if not a.arrivals.is_file():
            print(f"error: arrivals file not found: {a.arrivals}", file=sys.stderr)
            return 2
        names = set(ArrivalLog.from_jsonl(a.arrivals).folders())
        folders = names if folders is None else folders | names
    res = validate_submission_file(a.file, folders)
    if res.io_error:
        print(f"error: {res.errors[0]}", file=sys.stderr)
        return 2
    if folders is None:
        print("note: deal key check skipped (no --folder or --arrivals)")
    for e in res.errors:
        print(f"ERROR: {e}")
    for w in res.warnings:
        print(f"WARN: {w}")
    if res.ok:
        print("valid")
        return 0
    print(f"INVALID ({len(res.errors)} errors)")
    return 1


def _cmd_score(a: argparse.Namespace) -> int:
    from prism.engine.score import format_table, run_score

    for label, p in (("submission", a.submission), ("gold", a.gold)):
        if not p.is_file():
            print(f"error: {label} file not found: {p}", file=sys.stderr)
            return 2
    try:
        result = run_score(a.submission, a.gold, a.out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not result["deals"]:
        print("error: no scored deals (gold has none with status draft or done)", file=sys.stderr)
        return 2
    print(format_table(result))
    print(f"wrote {a.out}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2
    return {"run": _cmd_run, "validate": _cmd_validate, "score": _cmd_score}[args.cmd](args)
