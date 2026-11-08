"""Money is integer cents inside the engine; the submission shows plain numbers."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Union

_STR_RE = re.compile(r"^\s*\$?\s*(-?[\d,]*\.?\d+)\s*$")


def to_cents(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("bool is not money")
    if isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value))
    elif isinstance(value, Decimal):
        d = value
    elif isinstance(value, str):
        m = _STR_RE.match(value)
        if not m:
            raise ValueError(f"not a money string: {value!r}")
        try:
            d = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation as e:
            raise ValueError(f"not a money string: {value!r}") from e
    else:
        raise ValueError(f"not money: {value!r}")
    if not d.is_finite():
        raise ValueError("money must be finite")
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cents_to_number(cents: int) -> Union[int, float]:
    if cents % 100 == 0:
        return cents // 100
    return cents / 100
