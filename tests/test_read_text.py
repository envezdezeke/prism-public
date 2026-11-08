"""Text layer: kinds by magic bytes, caching, never raising."""
from __future__ import annotations

import os

import pytest

from prism.read import ocr, text
from prism.read.text import load_text, page_markers
from tests.pdfgen import fake_jpeg_bytes, fake_png_bytes, make_pdf, write_csv, write_pdf


@pytest.fixture(autouse=True)
def _fresh_cache():
    text.clear_cache()
    yield
    text.clear_cache()


def test_pdf_pages_and_metadata(tmp_path):
    p = write_pdf(tmp_path / "a.bin", [["Alpha line", "Beta line"], ["Gamma line"]],
                  producer="Maker 1.0", creator="Tool X")
    dt = load_text(p)
    assert dt.kind == "pdf" and dt.n_pages == 2 and len(dt.pages) == 2
    assert "Alpha line" in dt.pages[0] and "Gamma line" in dt.pages[1]
    assert "Gamma line" in dt.plain
    assert dt.producer == "Maker 1.0" and dt.creator == "Tool X"


def test_kind_comes_from_bytes_not_name(tmp_path):
    p = tmp_path / "notes.csv"
    p.write_bytes(make_pdf([["Hello there"]]))
    assert load_text(p).kind == "pdf"


def test_zero_byte_random_and_truncated_never_raise(tmp_path):
    z = tmp_path / "z.pdf"
    z.write_bytes(b"")
    assert load_text(z).kind == "empty"
    r = tmp_path / "r.pdf"
    r.write_bytes(bytes(range(256)) * 20)
    assert load_text(r).kind in {"error", "empty"}
    t = tmp_path / "t.pdf"
    t.write_bytes(make_pdf([["Some text"]])[:150])
    assert load_text(t).kind in {"error", "empty", "pdf"}
    assert load_text(tmp_path / "missing.pdf").kind == "error"
    assert load_text(tmp_path).kind == "error"


def test_oversize_refused(tmp_path, monkeypatch):
    p = write_pdf(tmp_path / "big.pdf", [["x"]])
    from prism.read import settings

    small = {**settings.get_config().limits, "max_bytes": 10}
    monkeypatch.setattr(text, "get_config", lambda: type("C", (), {"limits": small})())
    dt = load_text(p)
    assert dt.kind == "error" and dt.error == "oversize"


def test_csv_rows_with_free_text_header(tmp_path):
    p = write_csv(tmp_path / "s.dat", ["Some Bank - acct ...1234 - Owner",
                                       ["Posted Date", "Details", "Amount", "Running Bal"],
                                       ["5/1/26", "DEPOSIT", "100.00", "1100.00"]])
    dt = load_text(p)
    assert dt.kind == "csv"
    assert dt.rows[1] == ("Posted Date", "Details", "Amount", "Running Bal")
    assert dt.rows[2][0] == "5/1/26"


def test_plain_text_is_text(tmp_path):
    p = tmp_path / "n.txt"
    p.write_text("Just a note, with one comma.\nSecond line\n", encoding="utf-8")
    assert load_text(p).kind == "text"


@pytest.mark.parametrize("data", [fake_jpeg_bytes(), fake_png_bytes()])
def test_image_calls_ocr_once_and_caches(tmp_path, monkeypatch, data):
    calls = []

    def fake(path):
        calls.append(path)
        return "TEXAS DRIVER LICENSE"

    monkeypatch.setattr(ocr, "read_image_text", fake)
    p = tmp_path / "pic.dat"
    p.write_bytes(data)
    dt = load_text(p)
    assert dt.kind == "image" and dt.ocr_used and "DRIVER" in dt.plain
    load_text(p)
    assert len(calls) == 1


def test_touching_file_busts_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr, "read_image_text", lambda path: calls.append(1) or "x")
    p = tmp_path / "pic.dat"
    p.write_bytes(fake_jpeg_bytes())
    load_text(p)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    load_text(p)
    assert len(calls) == 2
    p.write_bytes(fake_jpeg_bytes() + b"\x00")
    load_text(p)
    assert len(calls) == 3


def test_page_markers():
    assert page_markers("foo Page 2 of 3 bar page 1 of 3") == [(2, 3), (1, 3)]
    assert page_markers("no marker here") == []


# ---- Task 3: fallbacks -------------------------------------------------------------------------

def test_blank_page_with_embedded_images_is_ocrd_once_per_image_and_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(text, "_embedded_images", lambda page, cap: [b"img-one", b"img-two"])
    monkeypatch.setattr(ocr, "read_image_text", lambda src: calls.append(src) or "SCANNED LINE")
    p = write_pdf(tmp_path / "scan.pdf", [[]])
    dt = load_text(p)
    assert dt.kind == "pdf" and dt.ocr_used and "SCANNED LINE" in dt.plain
    assert len(calls) == 2
    load_text(p)
    assert len(calls) == 2


def test_image_ocr_is_capped(tmp_path, monkeypatch):
    seen = {}

    def fake_images(page, cap):
        seen["cap"] = cap
        return [b"x"] * cap

    monkeypatch.setattr(text, "_embedded_images", fake_images)
    monkeypatch.setattr(ocr, "read_image_text", lambda src: "t")
    load_text(write_pdf(tmp_path / "scan.pdf", [[], []]))
    assert seen["cap"] <= 6


def test_plumber_fallback_when_pypdf_page_text_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(text, "_plumber_pages", lambda data, wanted, max_pages: {0: "PLUMBER TEXT"})
    monkeypatch.setattr(text, "_embedded_images", lambda page, cap: [])
    dt = load_text(write_pdf(tmp_path / "blank.pdf", [[]]))
    assert "PLUMBER TEXT" in dt.plain


def test_pdf_tables_returns_empty_on_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert text.pdf_tables(bad) == ()
    assert text.pdf_tables(tmp_path / "missing.pdf") == ()


def test_pdf_tables_on_a_real_pdf_never_raises(tmp_path):
    p = write_pdf(tmp_path / "t.pdf", [["a", "b"]])
    assert isinstance(text.pdf_tables(p), tuple)
