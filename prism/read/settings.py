"""Read configuration (config/read.yaml). ``yaml.safe_load`` only.

Only the keys this module uses are validated; everything else is reachable through ``.raw`` so
later plans can add sections to the YAML without editing this file. ``PRISM_READ_CONFIG`` selects
another file. The loaded config is cached by (path, mtime_ns).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from prism.engine.config import REPO_ROOT, ConfigError

DEFAULT_READ_PATH = REPO_ROOT / "config" / "read.yaml"

_LIMIT_KEYS = ("max_bytes", "max_pages", "max_chars")
_CONF_KEYS = ("label", "table", "ocr", "range", "inferred", "vl")


@dataclass(frozen=True)
class ReadConfig:
    quiet_minutes: int
    limits: Mapping[str, int]
    confidence: Mapping[str, float]
    classify: Mapping[str, Any]
    labels: Mapping[str, Mapping[str, tuple[str, ...]]]
    raw: Mapping[str, Any]

    @property
    def class_signals(self) -> Mapping[str, Any]:
        return self.classify.get("signals") or {}


def _fail(msg: str) -> ConfigError:
    return ConfigError(f"read.yaml: {msg}")


def _num(d: Mapping[str, Any], key: str, where: str, *, unit: bool = False) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _fail(f"{where}.{key} must be a number")
    if unit and not 0 <= v <= 1:
        raise _fail(f"{where}.{key} must be between 0 and 1")
    return v


def _build(raw: Any) -> ReadConfig:
    if not isinstance(raw, dict):
        raise _fail("top level must be a mapping")
    q = raw.get("quiet_minutes")
    if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
        raise _fail("quiet_minutes must be a positive integer")
    lim = raw.get("limits")
    if not isinstance(lim, dict):
        raise _fail("limits must be a mapping")
    limits = {k: int(_num(lim, k, "limits")) for k in _LIMIT_KEYS}
    conf = raw.get("confidence")
    if not isinstance(conf, dict):
        raise _fail("confidence must be a mapping")
    confidence = {k: float(_num(conf, k, "confidence", unit=True)) for k in _CONF_KEYS}
    for k, v in conf.items():
        if k not in confidence:
            confidence[k] = float(_num(conf, k, "confidence", unit=True))
    cls = raw.get("classify")
    if not isinstance(cls, dict):
        raise _fail("classify must be a mapping")
    _num(cls, "title_lines", "classify")
    _num(cls, "min_score", "classify")
    if not isinstance(cls.get("image_default_class"), str):
        raise _fail("classify.image_default_class must be a string")
    if not isinstance(cls.get("signals"), dict):
        raise _fail("classify.signals must be a mapping")
    lab = raw.get("labels")
    if not isinstance(lab, dict):
        raise _fail("labels must be a mapping")
    labels: dict[str, dict[str, tuple[str, ...]]] = {}
    for doc_class, fields in lab.items():
        if not isinstance(fields, dict):
            raise _fail(f"labels.{doc_class} must be a mapping")
        labels[str(doc_class)] = {}
        for field, syns in fields.items():
            if not isinstance(syns, list) or not all(isinstance(s, str) for s in syns):
                raise _fail(f"labels.{doc_class}.{field} must be a list of strings")
            labels[str(doc_class)][str(field)] = tuple(syns)
    return ReadConfig(quiet_minutes=q, limits=limits, confidence=confidence, classify=cls,
                      labels=labels, raw=raw)


def load_read_config(path: Optional[Path | str] = None) -> ReadConfig:
    p = Path(path) if path is not None else Path(os.environ.get("PRISM_READ_CONFIG") or DEFAULT_READ_PATH)
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise _fail(f"cannot read {p.name}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise _fail(f"invalid YAML in {p.name}") from e
    return _build(raw)


_CACHE: dict[tuple[str, int], ReadConfig] = {}


def get_config() -> ReadConfig:
    """The cached config for the current path (re-read when the file's mtime changes)."""
    p = Path(os.environ.get("PRISM_READ_CONFIG") or DEFAULT_READ_PATH)
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError as e:
        raise _fail(f"cannot read {p.name}: {e.strerror}") from e
    cfg = _CACHE.get(key)
    if cfg is None:
        cfg = load_read_config(p)
        _CACHE.clear()
        _CACHE[key] = cfg
    return cfg
