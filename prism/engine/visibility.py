"""Arrival gate (D-01): a file does not exist for the engine until its arrive_at.

Everything the engine knows about a deal comes through ``ArrivalLog.visible_files``.
All times are aware datetimes in the fixed UTC-06:00 sim zone, never system local time.
Arrivals paths are untrusted input: absolute paths and ``..`` components are rejected.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

SIM_TZ = timezone(timedelta(hours=-6))  # fixed offset, never a named zone

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def parse_time(s: str) -> datetime:
    """Parse an ISO time into SIM_TZ. Naive values are read as sim time; aware ones converted."""
    if not isinstance(s, str):
        raise ValueError(f"time must be a string: {s!r}")
    text = s.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SIM_TZ)
    return dt.astimezone(SIM_TZ)


def _as_sim(dt: datetime) -> datetime:
    return dt.replace(tzinfo=SIM_TZ) if dt.tzinfo is None else dt.astimezone(SIM_TZ)


def _clean_path(raw: object) -> str:
    """Normalise to a POSIX relative path or raise ValueError."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("missing path")
    p = raw.replace("\\", "/")
    if p.startswith("/") or _DRIVE_RE.match(p):
        raise ValueError("absolute path rejected")
    parts = [c for c in p.split("/") if c not in ("", ".")]
    if ".." in parts:
        raise ValueError("path with .. rejected")
    if not parts:
        raise ValueError("empty path")
    return "/".join(parts)


@dataclass(frozen=True)
class Arrival:
    path: str  # POSIX, "<folder>/<file>"
    arrive_at: datetime

    @property
    def folder(self) -> str:
        return self.path.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


class ArrivalLog:
    def __init__(self, arrivals: Iterable[Arrival], errors: Optional[list[str]] = None):
        self.errors: list[str] = list(errors or [])
        seen: dict[str, Arrival] = {}
        for a in arrivals:
            a = Arrival(a.path, _as_sim(a.arrive_at))
            prev = seen.get(a.path)
            if prev is None or a.arrive_at < prev.arrive_at:
                if prev is not None:
                    self.errors.append(f"duplicate path kept earliest: {a.path}")
                seen[a.path] = a
            else:
                self.errors.append(f"duplicate path kept earliest: {a.path}")
        self._arrivals = sorted(seen.values(), key=lambda a: (a.arrive_at, a.path))

    @classmethod
    def from_jsonl(cls, path: Path | str) -> "ArrivalLog":
        arrivals: list[Arrival] = []
        errors: list[str] = []
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError("not an object")
                    rel = _clean_path(obj.get("path"))
                    if obj.get("arrive_at") is None:
                        raise ValueError("missing arrive_at")
                    arrivals.append(Arrival(rel, parse_time(obj["arrive_at"])))
                except (ValueError, TypeError) as e:
                    errors.append(f"line {n}: {e}")
        return cls(arrivals, errors)

    @property
    def arrivals(self) -> list[Arrival]:
        return list(self._arrivals)

    def visible_files(self, now: datetime) -> list[Arrival]:
        now = _as_sim(now)
        return [a for a in self._arrivals if a.arrive_at <= now]

    def times(self) -> list[datetime]:
        return sorted({a.arrive_at for a in self._arrivals})

    def folders(self) -> list[str]:
        return sorted({a.folder for a in self._arrivals if "/" in a.path})

    def unattached(self) -> list[str]:
        return sorted(a.path for a in self._arrivals if "/" not in a.path)
