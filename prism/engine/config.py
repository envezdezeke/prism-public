"""Rules as data (D-02): loaders for stages.json, teams.json, assignments.json, config/fraud.yaml.

No threshold, folder name or person id lives in code. Strict loaders raise
FileNotFoundError or ConfigError with a plain message; ``load_rules`` is lenient
about the optional data-pack files and records a warning instead.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRAUD_PATH = REPO_ROOT / "config" / "fraud.yaml"


class ConfigError(ValueError):
    pass


def _read_json(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{p.name}: invalid JSON ({e})") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p.name}: top level must be an object")
    return data


@dataclass(frozen=True)
class Stages:
    pipeline: tuple[str, ...] = ()
    stages: dict[str, Any] = field(default_factory=dict)
    fraud_hold: dict[str, Any] = field(default_factory=dict)
    stuck_deals: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def sla_hours(self, stage: str) -> Optional[float]:
        v = self.stages.get(stage, {}).get("sla_hours")
        return float(v) if v is not None else None

    def gate(self, stage: str) -> Optional[dict[str, Any]]:
        g = self.stages.get(stage, {}).get("human_gate")
        return g if isinstance(g, dict) else None


@dataclass(frozen=True)
class Teams:
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def person(self, pid: str) -> Optional[dict[str, Any]]:
        return self.people.get(pid)

    def backup_of(self, pid: str) -> Optional[str]:
        return (self.people.get(pid) or {}).get("backup") or None

    def escalates_to(self, pid: str) -> Optional[str]:
        return (self.people.get(pid) or {}).get("escalates_to") or None


@dataclass(frozen=True)
class Assignments:
    by_folder: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def rm(self, folder: str) -> Optional[str]:
        return (self.by_folder.get(folder) or {}).get("relationship_manager")

    def credit_officer(self, folder: str) -> Optional[str]:
        return (self.by_folder.get(folder) or {}).get("credit_officer")


@dataclass(frozen=True)
class FraudConfig:
    percentiles: dict[str, float]
    f1_rarity_fraction: float
    f1_min_linked_deals: int
    hold_required: tuple[str, ...]
    hold_additional_from: tuple[str, ...]
    hold_min_additional: int
    non_voting: tuple[str, ...]
    history_min_links: int
    train_end: date
    test_start: date
    test_end: date
    raw: dict[str, Any] = field(default_factory=dict)
    # Strong-link hold path (None = off): F1 plus this many additional families is enough
    # when the deal shares at least history_min_links independent link classes with a partner.
    hold_strong_link_min_additional: Optional[int] = None


@dataclass(frozen=True)
class Rules:
    stages: Stages = field(default_factory=Stages)
    teams: Teams = field(default_factory=Teams)
    assignments: Assignments = field(default_factory=Assignments)
    fraud: Optional[FraudConfig] = None
    data_root: Optional[Path] = None
    history_dir: Optional[Path] = None
    warnings: tuple[str, ...] = ()


def load_stages(path: Path | str) -> Stages:
    raw = _read_json(path)
    pipeline = raw.get("pipeline", [])
    stages = raw.get("stages", {})
    if not isinstance(pipeline, list) or not all(isinstance(s, str) for s in pipeline):
        raise ConfigError("stages.json: pipeline must be a list of stage names")
    if not isinstance(stages, dict):
        raise ConfigError("stages.json: stages must be an object")
    return Stages(
        pipeline=tuple(pipeline),
        stages=stages,
        fraud_hold=raw.get("fraud_hold") or {},
        stuck_deals=raw.get("stuck_deals") or {},
        raw=raw,
    )


def load_teams(path: Path | str) -> Teams:
    raw = _read_json(path)
    people = raw.get("people", [])
    if not isinstance(people, list):
        raise ConfigError("teams.json: people must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for p in people:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str):
            raise ConfigError("teams.json: every person needs a string id")
        by_id[p["id"]] = p
    routing = raw.get("routing") or {}
    if not isinstance(routing, dict):
        raise ConfigError("teams.json: routing must be an object")
    return Teams(people=by_id, routing=routing, raw=raw)


def load_assignments(path: Path | str) -> Assignments:
    raw = _read_json(path)
    for k, v in raw.items():
        if not isinstance(v, dict):
            raise ConfigError(f"assignments.json: entry for {k!r} must be an object")
    return Assignments(by_folder=raw, raw=raw)


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(f"fraud.yaml: {msg}")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _date(v: Any, name: str) -> date:
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    except ValueError as e:
        raise ConfigError(f"fraud.yaml: calibration.{name} is not an ISO date") from e


def load_fraud_config(path: Path | str | None = None) -> FraudConfig:
    p = Path(path) if path is not None else DEFAULT_FRAUD_PATH
    with open(p, encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f)  # safe_load only: no object construction
        except yaml.YAMLError as e:
            raise ConfigError(f"fraud.yaml: invalid YAML ({e})") from e
    _need(isinstance(raw, dict), "top level must be a mapping")
    fams = raw.get("families")
    _need(isinstance(fams, dict) and bool(fams), "families mapping is required")

    percentiles: dict[str, float] = {}
    non_voting: list[str] = []
    for fid, spec in fams.items():
        _need(isinstance(spec, dict), f"family {fid} must be a mapping")
        if spec.get("votes") is False:
            non_voting.append(fid)
        if "percentile" in spec:
            v = spec["percentile"]
            _need(_num(v) and 0 < v < 1, f"{fid}.percentile must be strictly between 0 and 1")
            percentiles[fid] = float(v)
    _need("F6" in fams, "F6 must be declared")
    _need(fams["F6"].get("votes") is False, "F6 must not vote")

    f1 = fams.get("F1")
    _need(isinstance(f1, dict), "F1 must be declared")
    rf = f1.get("rarity_fraction")
    _need(_num(rf) and 0 < rf <= 1, "F1.rarity_fraction must be in (0, 1]")
    ml = f1.get("min_linked_deals")
    _need(_int(ml) and ml >= 2, "F1.min_linked_deals must be an int >= 2")

    hr = raw.get("hold_rule")
    _need(isinstance(hr, dict), "hold_rule is required")
    req, add = hr.get("required"), hr.get("additional_from")
    _need(
        isinstance(req, list) and isinstance(add, list) and bool(req) and bool(add),
        "hold_rule.required and additional_from must be non-empty lists",
    )
    for fid in list(req) + list(add):
        _need(fid in fams, f"hold_rule names unknown family {fid}")
    for fid in add:
        _need(fid not in non_voting, f"hold_rule.additional_from contains non-voting family {fid}")
    ma = hr.get("min_additional")
    _need(
        _int(ma) and 1 <= ma <= len(add),
        "hold_rule.min_additional must be between 1 and len(additional_from)",
    )

    slm = hr.get("strong_link_min_additional")
    _need(slm is None or (_int(slm) and 1 <= slm <= ma),
          "hold_rule.strong_link_min_additional must be an int between 1 and min_additional")

    hf = raw.get("history_flags")
    _need(isinstance(hf, dict), "history_flags is required")
    ml2 = hf.get("min_links")
    _need(_int(ml2) and ml2 >= 1, "history_flags.min_links must be an int >= 1")

    cal = raw.get("calibration")
    _need(isinstance(cal, dict), "calibration is required")
    train_end = _date(cal.get("train_end"), "train_end")
    test_start = _date(cal.get("test_start"), "test_start")
    test_end = _date(cal.get("test_end"), "test_end")
    _need(train_end < test_start <= test_end, "calibration needs train_end < test_start <= test_end")

    return FraudConfig(
        percentiles=percentiles,
        f1_rarity_fraction=float(rf),
        f1_min_linked_deals=ml,
        hold_required=tuple(req),
        hold_additional_from=tuple(add),
        hold_min_additional=ma,
        non_voting=tuple(sorted(non_voting)),
        history_min_links=ml2,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        raw=raw,
        hold_strong_link_min_additional=slm,
    )


def load_rules(data_root: Path | str | None, *, fraud_path: Path | str | None = None) -> Rules:
    fraud = load_fraud_config(fraud_path)
    if data_root is None:
        return Rules(fraud=fraud)
    root = Path(data_root)
    warnings: list[str] = []

    def opt(rel: str, loader, empty):
        p = root / rel
        if not p.is_file():
            warnings.append(f"missing {rel}: using empty rules")
            return empty
        return loader(p)

    hist = root / "history"
    return Rules(
        stages=opt("rules/stages.json", load_stages, Stages()),
        teams=opt("contacts/teams.json", load_teams, Teams()),
        assignments=opt("sim/assignments.json", load_assignments, Assignments()),
        fraud=fraud,
        data_root=root,
        history_dir=hist if hist.is_dir() else None,
        warnings=tuple(warnings),
    )
