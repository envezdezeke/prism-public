"""Classifier refinements for scanned-looking text: uppercase, doubled spaces, glued OCR words (03-02)."""
from __future__ import annotations

import re

import pytest

from prism.read.classify import DOC_CLASSES, score_classes
from prism.read.text import DocText
from tests.test_read_classify import TEXTS

_ID_OCR_GLUED = "\n".join([
    "TEXAS", "DRIVERLICENSE", "DL01234567", "ADAQUILLFEATHER", "10SAMPLELNAPT3", "PHOTO",
    "TESTVILLE,TX75002", "DOB1980-01-02", "EXP2029-04-11CLASSC", "SPECIMEN-invented",
])


def _dt(text: str, kind: str = "pdf") -> DocText:
    return DocText(kind=kind, plain=text, layout=text, pages=(text,), n_pages=1)


def _top(text: str, kind: str = "pdf"):
    ranked = score_classes(_dt(text, kind))
    return ranked[0] if ranked else ("other", 0.0)


def _min_score() -> float:
    from prism.read.settings import get_config

    return float(get_config().classify["min_score"])


CLASSES = sorted(c for c in DOC_CLASSES if c != "other")


@pytest.mark.parametrize("cls", CLASSES)
def test_uppercase_text_resolves(cls):
    top = _top("\n".join(TEXTS[cls]).upper())
    assert top[0] == cls and top[1] >= _min_score(), (cls, top)


@pytest.mark.parametrize("cls", CLASSES)
def test_double_spaced_text_resolves(cls):
    noisy = "\n".join(re.sub(r" ", "  ", ln) for ln in TEXTS[cls])
    top = _top(noisy)
    assert top[0] == cls and top[1] >= _min_score(), (cls, top)


@pytest.mark.parametrize("cls", CLASSES)
def test_blank_lines_and_trailing_spaces_resolve(cls):
    noisy = "\n\n".join(ln + "   " for ln in TEXTS[cls])
    top = _top(noisy)
    assert top[0] == cls and top[1] >= _min_score(), (cls, top)


def test_glued_ocr_id_resolves_by_content():
    top = _top(_ID_OCR_GLUED, kind="image")
    assert top[0] == "guarantor_id" and top[1] >= _min_score(), top


def test_other_stays_other_when_noisy():
    top = _top("\n".join(TEXTS["other"]).upper())
    assert top[1] < _min_score()


def test_application_with_id_words_is_still_an_application():
    # an application carries a date of birth and a driver license; the title wins
    top = _top("\n".join(TEXTS["application"]))
    assert top[0] == "application"
