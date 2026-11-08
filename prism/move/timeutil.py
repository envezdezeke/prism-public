"""Sim-time helpers: formatting, and business-hour arithmetic for scripted answerers.

Pure and deterministic. Nothing here reads the system clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from prism.contract import format_sim_time, parse_sim_time
from prism.engine.visibility import SIM_TZ
from prism.move.settings import MoveConfig, get_config

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)


def to_sim(dt: datetime) -> datetime:
    return dt.replace(tzinfo=SIM_TZ) if dt.tzinfo is None else dt.astimezone(SIM_TZ)


def iso(dt: datetime) -> str:
    return format_sim_time(to_sim(dt))


def parse_iso(s: object) -> Optional[datetime]:
    if not isinstance(s, str):
        return None
    try:
        return parse_sim_time(s)
    except ValueError:
        return None


def fmt_date(dt: datetime) -> str:
    d = to_sim(dt)
    return f"{_DAYS[d.weekday()]} {d.month:02d}/{d.day:02d}"


def fmt_when(dt: datetime) -> str:
    """'Tue 11/03 3:05 PM CT'."""
    d = to_sim(dt)
    hour12 = d.hour % 12 or 12
    suffix = "AM" if d.hour < 12 else "PM"
    return f"{fmt_date(d)} {hour12}:{d.minute:02d} {suffix} CT"


def fmt_duration(delta: timedelta) -> str:
    """'26h 10m' (whole minutes, never negative)."""
    minutes = max(int(round(delta.total_seconds() / 60)), 0)
    return f"{minutes // 60}h {minutes % 60}m"


def fmt_money(cents: int) -> str:
    """Integer cents in; cents are shown only when the amount is not whole dollars."""
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    dollars, rem = divmod(c, 100)
    return f"{sign}${dollars:,}" if rem == 0 else f"{sign}${dollars:,}.{rem:02d}"


def month_label(period: str) -> str:
    """'2025-11' -> 'November 2025'; anything else is returned unchanged."""
    try:
        y, m = period.split("-")
        return f"{_MONTHS[int(m) - 1]} {int(y)}"
    except (ValueError, IndexError, AttributeError):
        return str(period)


# ------------------------------------------------------------ business hours


def _window(d: datetime, cfg: MoveConfig) -> tuple[datetime, datetime]:
    base = d.replace(hour=0, minute=0, second=0, microsecond=0)
    o = base.replace(hour=cfg.open_time.hour, minute=cfg.open_time.minute)
    c = base.replace(hour=cfg.close_time.hour, minute=cfg.close_time.minute)
    return o, c


def next_open(dt: datetime, cfg: Optional[MoveConfig] = None) -> datetime:
    """The earliest instant at or after ``dt`` that falls inside business hours."""
    cfg = cfg or get_config()
    cur = to_sim(dt)
    for _ in range(400):
        if cur.weekday() in cfg.business_days:
            o, c = _window(cur, cfg)
            if cur < o:
                return o
            if cur < c:
                return cur
        cur = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("no business day in configured calendar")


def business_add(dt: datetime, minutes: float, cfg: Optional[MoveConfig] = None) -> datetime:
    """Add business minutes. A start outside hours first moves to the next open instant."""
    cfg = cfg or get_config()
    cur = next_open(dt, cfg)
    rem = timedelta(minutes=minutes)
    while True:
        _, c = _window(cur, cfg)
        avail = c - cur
        if rem <= avail:
            return cur + rem
        rem -= avail
        cur = next_open(c, cfg)


def _prev_close(dt: datetime, cfg: MoveConfig) -> datetime:
    """The latest instant at or before ``dt`` that falls inside business hours."""
    cur = to_sim(dt)
    for _ in range(400):
        if cur.weekday() in cfg.business_days:
            o, c = _window(cur, cfg)
            if cur > c:
                return c
            if cur > o:
                return cur
            if cur == o:
                return cur
        cur = (cur - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    raise ValueError("no business day in configured calendar")


def business_sub(dt: datetime, minutes: float, cfg: Optional[MoveConfig] = None) -> datetime:
    """Subtract business minutes. A start outside hours first moves to the previous close."""
    cfg = cfg or get_config()
    cur = _prev_close(dt, cfg)
    rem = timedelta(minutes=minutes)
    while True:
        o, _ = _window(cur, cfg)
        avail = cur - o
        if rem <= avail:
            return cur - rem
        rem -= avail
        cur = _prev_close(o - timedelta(seconds=1), cfg)
