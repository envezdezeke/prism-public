"""Submission validator (D-05): strict contract check plus Q14 consistency warnings.

Underscore-prefixed diagnostic keys (``_error``, ``_errors``) are stripped before model
validation and surface as warnings: the organizers ignore extra keys, so the file stays valid.
Q14 (held without flagged, flagged without flagged_at/stage_at_flag) is a warning only.
Plain ``json`` only, never eval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import ValidationError

from prism.contract import Submission


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    io_error: bool = False  # file missing or unreadable (CLI exit 2)

    @property
    def ok(self) -> bool:
        return not self.errors


def _loc(loc: Iterable[Any]) -> str:
    return ".".join(str(p) for p in loc)


def validate_submission_data(
    data: Any, folders: Optional[Iterable[str]] = None
) -> ValidationResult:
    res = ValidationResult()
    if not isinstance(data, dict):
        res.errors.append("top level must be a JSON object")
        return res

    clean: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(k, str) and k.startswith("_"):
            if k == "_errors":
                items = v if isinstance(v, list) else [v]
                for item in items:
                    res.warnings.append(f"run: engine error recorded: {item}")
            continue
        clean[k] = v

    deals = clean.get("deals")
    raw_fraud: dict[str, Any] = {}
    if isinstance(deals, dict):
        stripped: dict[str, Any] = {}
        for name, deal in deals.items():
            if isinstance(deal, dict):
                for k in sorted(k for k in deal if isinstance(k, str) and k.startswith("_")):
                    res.warnings.append(f"deal {name}: engine error recorded: {deal[k]}")
                raw_fraud[name] = deal.get("fraud")
                deal = {k: v for k, v in deal.items() if not (isinstance(k, str) and k.startswith("_"))}
            stripped[name] = deal
        clean["deals"] = stripped

    try:
        Submission.model_validate(clean)
    except ValidationError as e:
        res.errors.extend(sorted(f"{_loc(err['loc'])}: {err['msg']}" for err in e.errors()))

    if isinstance(deals, dict):
        for name in sorted(deals):
            if not name or "/" in name or "\\" in name:
                res.errors.append(f"deals.{name!r}: deal key must be a folder name")
        if folders is not None:
            want = set(folders)
            for name in sorted(want - set(deals)):
                res.errors.append(f"missing deal: {name}")
            for name in sorted(set(deals) - want):
                res.errors.append(f"unexpected deal: {name}")
        for name in sorted(raw_fraud):
            fr = raw_fraud[name]
            if not isinstance(fr, dict):
                continue
            if fr.get("held") and not fr.get("flagged"):
                res.warnings.append(f"deal {name}: held but not flagged")
            if fr.get("flagged") and (not fr.get("flagged_at") or not fr.get("stage_at_flag")):
                res.warnings.append(f"deal {name}: flagged without flagged_at/stage_at_flag")
    return res


def validate_submission_file(
    path: Path | str, folders: Optional[Iterable[str]] = None
) -> ValidationResult:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        r = ValidationResult(io_error=True)
        r.errors.append(f"cannot read file: {type(e).__name__}")
        return r
    except UnicodeDecodeError:
        r = ValidationResult()
        r.errors.append("file is not valid UTF-8")
        return r
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        r = ValidationResult()
        r.errors.append(f"invalid JSON: {e.msg} (line {e.lineno})")
        return r
    return validate_submission_data(data, folders)
