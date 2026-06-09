from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout.resume import load_resume


def test_load_txt(tmp_path: Path) -> None:
    p = tmp_path / "resume.txt"
    p.write_text("Taha El Ghadi\nBiology student.", encoding="utf-8")
    assert "Taha El Ghadi" in load_resume(p)


def test_load_txt_strips_bom(tmp_path: Path) -> None:
    p = tmp_path / "resume.txt"
    p.write_bytes(b"\xef\xbb\xbfTaha El Ghadi")  # UTF-8 BOM prefix
    text = load_resume(p)
    assert text.startswith("Taha")  # BOM stripped


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Resume not found"):
        load_resume(tmp_path / "nope.txt")


def test_pdf_missing_pypdf_or_parses(tmp_path: Path) -> None:
    """If pypdf is installed, a minimal text PDF should parse or raise a clear
    error; if pypdf is absent, a RuntimeError naming pypdf is raised."""
    pytest.importorskip("pypdf")
    import pypdf

    # Build a tiny one-page PDF with extractable text using pypdf's writer.
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = tmp_path / "resume.pdf"
    with out.open("wb") as fh:
        writer.write(fh)

    # A blank page has no extractable text → load_resume raises ValueError.
    with pytest.raises(ValueError, match="Could not extract text"):
        load_resume(out)

    assert pypdf is not None  # sanity
