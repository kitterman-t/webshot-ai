"""tools/coverage/printed_share.py: the capture's own text measure, on a finished PDF.

Reviews measured how much of a capture's text its PDF printed as the share of
content.md's word 5-grams found in pdftotext's output, which counts link
targets and recognized text that no PDF prints: a lesson page and an
interactive site that printed all their text scored 0.49 and 0.30 (docs/09
P113-6). The tool reads the measure the capture judges by
(`webshot.render.text_layer`, P97) instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfWriter
from tools.coverage import printed_share as tool

from webshot.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "coverage_links_page.html"


def _five_gram_share(markdown: str, printed: str) -> float:
    """The measure reviews used, as their scripts wrote it."""

    def words(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    source, held = words(markdown), " ".join(words(printed))
    grams = [" ".join(source[i : i + 5]) for i in range(len(source) - 4)]
    return sum(gram in held for gram in grams) / len(grams)


@pytest.mark.browser
def test_a_page_of_links_prints_all_its_text_by_the_capture_measure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = tmp_path / "links.pdf"
    assert main([str(FIXTURE), "--output", str(pdf), "--no-ocr"]) == 0
    markdown = (pdf.with_suffix(".ai") / "content.md").read_text(encoding="utf-8")
    assert "](https://archive.example.org/" in markdown
    measured = tool.measure(pdf)
    assert measured.source == "embedded"
    assert measured.verdict == "ok" and measured.share is not None
    assert measured.share >= 0.95, measured
    # The 5-gram measure over the same capture reads its link targets as
    # missing text.
    printed = " ".join(tool.page_texts(pdf))
    assert _five_gram_share(markdown, printed) < 0.6
    assert tool.main([str(pdf)]) == 0
    out = capsys.readouterr().out.strip()
    assert re.fullmatch(r"links\.pdf: [\d,]+ of [\d,]+ runs printed \(\d+%\)", out)
    assert tool.main(["--json", str(pdf)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["verdict"] == "ok" and record["source"] == "embedded"


@pytest.mark.browser
def test_without_an_embedded_bundle_it_reads_the_directory_beside(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "plain.pdf"
    assert (
        main([str(FIXTURE), "--output", str(pdf), "--no-ocr", "--no-embed-bundle"]) == 0
    )
    measured = tool.measure(pdf)
    assert measured.source == str(pdf.with_suffix(".ai"))
    assert measured.verdict == "ok"


def test_a_pdf_with_no_bundle_is_not_measured_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = tmp_path / "bare.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf.open("wb") as handle:
        writer.write(handle)
    measured = tool.measure(pdf)
    assert measured.verdict == "not measured" and measured.share is None
    assert (
        "no content.txt embedded in it, and no bare.ai/content.txt" in measured.reason
    )
    assert tool.main([str(pdf)]) == 2
    assert capsys.readouterr().out.startswith("bare.pdf: not measured: ")


def test_a_file_that_is_not_a_pdf_is_not_measured(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert tool.measure(junk).verdict == "not measured"
    assert tool.main([str(junk)]) == 2
