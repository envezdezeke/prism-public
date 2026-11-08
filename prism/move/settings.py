"""Move configuration (config/move.yaml): frozen dataclasses plus a validating loader.

All rule values live in the YAML file. ``yaml.safe_load`` only. Env ``PRISM_MOVE_CONFIG``
selects another file and ``PRISM_MOVE_PERSONA`` overrides the default persona; both are
read at load time only.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

from prism.engine.config import REPO_ROOT, ConfigError

DEFAULT_MOVE_PATH = REPO_ROOT / "config" / "move.yaml"


@dataclass(frozen=True)
class Persona:
    wall_hours: float
    business_minutes: int


@dataclass(frozen=True)
class GateCfg:
    options: tuple[str, ...]
    advance: tuple[str, ...] = ()
    stop: tuple[str, ...] = ()
    info: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponderCfg:
    default: str
    personas: dict[str, Optional[Persona]]
    by_person: dict[str, str] = field(default_factory=dict)
    by_type: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    superseded_label: str = "superseded"


@dataclass(frozen=True)
class IssueRules:
    requestable: tuple[re.Pattern, ...]
    immediate: tuple[re.Pattern, ...]
    blocking: dict[str, tuple[re.Pattern, ...]]


@dataclass(frozen=True)
class MemoCfg:
    assumed_rate: Decimal
    coverage_bands: tuple[tuple[Decimal, int], ...]
    coverage_floor_points: int
    revenue_bands: tuple[tuple[Decimal, int], ...]
    tib_bands: tuple[tuple[Decimal, int], ...]
    tib_floor_points: int
    relationship_points: dict[str, int]
    grade_bands: tuple[tuple[int, int, str], ...]
    top_grade: tuple[int, str]
    watch_min_grade: int


@dataclass(frozen=True)
class MoveConfig:
    quiet_minutes: int
    business_days: tuple[int, ...]
    open_time: dtime
    close_time: dtime
    auto_stage_minutes: dict[str, int]
    required_facts: dict[str, tuple[str, ...]]
    issues: IssueRules
    vendor_check_products: tuple[str, ...]
    gate_roles: dict[str, str]
    gates: dict[str, GateCfg]
    responder: ResponderCfg
    fraud_first_hours: float
    fraud_second_hours: float
    deadline_statistic: str
    memo: MemoCfg


def _bad(msg: str) -> ConfigError:
    return ConfigError(f"move.yaml: {msg}")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise _bad(msg)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _key(raw: dict, name: str, kind: type | tuple, where: str = "") -> Any:
    _need(name in raw, f"missing key {where}{name}")
    v = raw[name]
    _need(isinstance(v, kind), f"{where}{name} has the wrong type")
    return v


def _time(v: Any, name: str) -> dtime:
    try:
        hh, mm = str(v).split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, TypeError) as e:
        raise _bad(f"{name} is not an HH:MM time: {v!r}") from e


def _dec(v: Any, name: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except InvalidOperation as e:
        raise _bad(f"{name} is not a decimal: {v!r}") from e
    _need(d.is_finite(), f"{name} must be finite")
    return d


def _patterns(items: Any, name: str) -> tuple[re.Pattern, ...]:
    _need(isinstance(items, list), f"{name} must be a list of patterns")
    out = []
    for s in items:
        _need(isinstance(s, str), f"{name} entries must be strings")
        try:
            out.append(re.compile(s))
        except re.error as e:
            raise _bad(f"{name} has a bad regex {s!r}: {e}") from e
    return tuple(out)


def _bands(items: Any, key: str, name: str) -> tuple[tuple[Decimal, int], ...]:
    _need(isinstance(items, list) and bool(items), f"{name} must be a non-empty list")
    out = []
    for b in items:
        _need(isinstance(b, dict) and key in b and _is_int(b.get("points")), f"{name} entries need {key} and points")
        out.append((_dec(b[key], f"{name}.{key}"), b["points"]))
    return tuple(out)


def _load_memo(raw: dict) -> MemoCfg:
    rp = _key(raw, "relationship_points", dict, "credit_memo.")
    for k in ("returning_clean", "new", "returning_past_due"):
        _need(_is_int(rp.get(k)), f"credit_memo.relationship_points.{k} must be an int")
    gb = _key(raw, "grade_bands", list, "credit_memo.")
    bands = []
    for b in gb:
        _need(
            isinstance(b, dict) and _is_int(b.get("max_points")) and _is_int(b.get("grade"))
            and isinstance(b.get("name"), str),
            "credit_memo.grade_bands entries need max_points, grade and name",
        )
        bands.append((b["max_points"], b["grade"], b["name"]))
    top = _key(raw, "top_grade", dict, "credit_memo.")
    _need(_is_int(top.get("grade")) and isinstance(top.get("name"), str), "credit_memo.top_grade needs grade and name")
    return MemoCfg(
        assumed_rate=_dec(raw.get("assumed_rate"), "credit_memo.assumed_rate"),
        coverage_bands=_bands(raw.get("coverage_bands"), "min", "credit_memo.coverage_bands"),
        coverage_floor_points=_key(raw, "coverage_floor_points", int, "credit_memo."),
        revenue_bands=_bands(raw.get("revenue_bands"), "max", "credit_memo.revenue_bands"),
        tib_bands=_bands(raw.get("time_in_business_bands"), "min", "credit_memo.time_in_business_bands"),
        tib_floor_points=_key(raw, "time_in_business_floor_points", int, "credit_memo."),
        relationship_points={k: rp[k] for k in ("returning_clean", "new", "returning_past_due")},
        grade_bands=tuple(bands),
        top_grade=(top["grade"], top["name"]),
        watch_min_grade=_key(raw, "watch_min_grade", int, "credit_memo."),
    )


def load_move_config(path: Path | str | None = None) -> MoveConfig:
    env_path = os.environ.get("PRISM_MOVE_CONFIG")
    p = Path(path) if path is not None else Path(env_path) if env_path else DEFAULT_MOVE_PATH
    with open(p, encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f)  # safe_load only: no object construction
        except yaml.YAMLError as e:
            raise _bad(f"invalid YAML ({e})") from e
    _need(isinstance(raw, dict), "top level must be a mapping")

    qm = _key(raw, "quiet_minutes", int)
    _need(qm >= 0 and not isinstance(qm, bool), "quiet_minutes must be >= 0")
    bh = _key(raw, "business_hours", dict)
    days = _key(bh, "days", list, "business_hours.")
    _need(all(_is_int(d) and 0 <= d <= 6 for d in days) and bool(days), "business_hours.days must be weekday ints 0-6")
    open_t, close_t = _time(bh.get("open"), "business_hours.open"), _time(bh.get("close"), "business_hours.close")
    _need(open_t < close_t, "business_hours.open must be before close")

    asm = _key(raw, "auto_stage_minutes", dict)
    _need(all(isinstance(k, str) and _is_int(v) and v >= 0 for k, v in asm.items()), "auto_stage_minutes: stage -> minutes")
    rf = _key(raw, "required_facts", dict)
    _need(all(isinstance(v, list) for v in rf.values()), "required_facts: stage -> list of fact names")

    ir = _key(raw, "issue_rules", dict)
    blocking_raw = _key(ir, "blocking", dict, "issue_rules.")
    issues = IssueRules(
        requestable=_patterns(ir.get("requestable"), "issue_rules.requestable"),
        immediate=_patterns(ir.get("immediate"), "issue_rules.immediate"),
        blocking={k: _patterns(v, f"issue_rules.blocking.{k}") for k, v in blocking_raw.items()},
    )

    gates_raw = _key(raw, "gates", dict)
    gates: dict[str, GateCfg] = {}
    for g, spec in gates_raw.items():
        _need(isinstance(spec, dict) and isinstance(spec.get("options"), list) and bool(spec["options"]), f"gates.{g}.options is required")
        opts = tuple(spec["options"])
        sub = {}
        for k in ("advance", "stop", "info"):
            vals = tuple(spec.get(k) or ())
            _need(all(v in opts for v in vals), f"gates.{g}.{k} names an option not in options")
            sub[k] = vals
        gates[g] = GateCfg(options=opts, **sub)

    rr = _key(raw, "responder", dict)
    personas_raw = _key(rr, "personas", dict, "responder.")
    personas: dict[str, Optional[Persona]] = {}
    for name, spec in personas_raw.items():
        if spec is None:
            personas[name] = None
            continue
        _need(isinstance(spec, dict) and _is_num(spec.get("wall_hours")) and _is_int(spec.get("business_minutes")), f"responder.personas.{name} needs wall_hours and business_minutes")
        _need(spec["wall_hours"] >= 0 and spec["business_minutes"] >= 0, f"responder.personas.{name} must not be negative")
        personas[name] = Persona(float(spec["wall_hours"]), spec["business_minutes"])
    default = os.environ.get("PRISM_MOVE_PERSONA") or _key(rr, "default", str, "responder.")
    by_person = rr.get("by_person") or {}
    by_type = rr.get("by_type") or {}
    _need(isinstance(by_person, dict) and isinstance(by_type, dict), "responder.by_person/by_type must be mappings")
    for name in [default, *by_person.values(), *by_type.values()]:
        _need(name in personas, f"unknown persona {name!r}")
    answers = _key(rr, "answers", dict, "responder.")
    for g, opt in answers.items():
        _need(g in gates and opt in gates[g].options, f"responder.answers.{g}: {opt!r} is not an option of that gate")
    responder = ResponderCfg(
        default=default, personas=personas, by_person=dict(by_person), by_type=dict(by_type),
        answers=dict(answers), superseded_label=_key(rr, "superseded_label", str, "responder."),
    )

    fc = _key(raw, "fraud_chase", dict)
    _need(_is_num(fc.get("first_hours")) and _is_num(fc.get("second_hours")), "fraud_chase needs first_hours and second_hours")
    dl = _key(raw, "deadline", dict)
    _need(dl.get("statistic") in ("median",), "deadline.statistic must be 'median'")
    roles = _key(raw, "gate_roles", dict)
    _need(all(v in ("credit_officer", "relationship_manager") for v in roles.values()), "gate_roles values must be credit_officer or relationship_manager")
    vcp = _key(raw, "vendor_check_products", list)

    return MoveConfig(
        quiet_minutes=qm,
        business_days=tuple(sorted(set(days))),
        open_time=open_t,
        close_time=close_t,
        auto_stage_minutes=dict(asm),
        required_facts={k: tuple(v) for k, v in rf.items()},
        issues=issues,
        vendor_check_products=tuple(vcp),
        gate_roles=dict(roles),
        gates=gates,
        responder=responder,
        fraud_first_hours=float(fc["first_hours"]),
        fraud_second_hours=float(fc["second_hours"]),
        deadline_statistic=dl["statistic"],
        memo=_load_memo(_key(raw, "credit_memo", dict)),
    )


_cached: Optional[MoveConfig] = None


def get_config() -> MoveConfig:
    global _cached
    if _cached is None:
        _cached = load_move_config()
    return _cached


@contextmanager
def use_config(cfg: MoveConfig) -> Iterator[MoveConfig]:
    """Swap the active config for the duration of the block (tests, persona switches)."""
    global _cached
    prev = _cached
    _cached = cfg
    try:
        yield cfg
    finally:
        _cached = prev
