"""Lazy RapidOCR wrapper (D-05). Any failure returns None: the file is then present-but-unverified.

Recognized text is never logged or printed (T-03-03).
"""
from __future__ import annotations

from typing import Any, Optional, Union

_ENGINE: Any = None
_BROKEN = False


def available() -> bool:
    """True when the OCR package can be imported."""
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _engine() -> Any:
    global _ENGINE, _BROKEN
    if _ENGINE is None and not _BROKEN:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _ENGINE = RapidOCR()
        except Exception:  # noqa: BLE001
            _BROKEN = True
    return _ENGINE


def read_image_text(source: Union[str, bytes]) -> Optional[str]:
    """Recognized lines joined with newlines, or None on any failure or an empty result.

    ``source`` is a file path or the raw bytes of an image.
    """
    try:
        eng = _engine()
        if eng is None:
            return None
        result, _elapsed = eng(source if isinstance(source, (bytes, bytearray)) else str(source))
        if not result:
            return None
        lines = [str(item[1]).strip() for item in result if len(item) >= 2 and str(item[1]).strip()]
        return "\n".join(lines) if lines else None
    except Exception:  # noqa: BLE001
        return None
