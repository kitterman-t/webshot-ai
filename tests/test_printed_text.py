"""A PDF that holds much less of the page's text than its bundle, named (docs/09 P97).

A news site's registration wall left the headline, one paragraph and the wall
on each of ten printed pages, while `content.md` held the whole article. The
capture exited 0, and its warnings named three fixed boxes and suggested
`--exclude`, which would have taken down the wall and brought back nothing.
The PDF carries the bundle as its record, so the two disagreeing that far
must be said, and a wall a vendor's script drew must be named as one.

tests/fixtures/printed_text_wall_page.html holds an article of that shape: all
of it in the document, everything after its first paragraph drawn fully
transparent, under a fixed box with the class Mg2's ConneXt gives its own.
All of its text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.cli import main
from webshot.render.text_layer import (
    LOW_SHARE,
    MIN_RUNS,
    RUN_CHARACTERS,
    PrintedShare,
    printed_share,
    printed_text_warning,
)

FIXTURE = Path(__file__).parent / "fixtures" / "printed_text_wall_page.html"

#: One paragraph of about 200 letters, as a line of the bundle's text.
LINE = (
    "The harbor town keeps its tide tables in a ledger older than the "
    "lighthouse, and every spring the harbor master copies the readings into "
    "a fresh volume before the first fishing boats leave the bay."
)


def test_text_the_pdf_holds_whatever_its_layout_is_fully_shared() -> None:
    """Case, punctuation, line breaks and inferred spaces are layout, not text."""
    words = LINE.split()
    wrapped = "\r\n".join(" ".join(words[i : i + 5]) for i in range(0, len(words), 5))
    measured = printed_share(LINE, [wrapped.upper().replace(",", " ,")])
    assert measured.runs > 0
    assert measured.printed == measured.runs and measured.share == 1.0


def test_a_paragraph_the_pdf_lacks_is_missing_from_the_share() -> None:
    other = (
        "A bookbinder from the next valley spent three afternoons easing the "
        "swollen pages apart with a thin bone folder and a pot of warm water."
    )
    measured = printed_share(f"{LINE}\n\n{other}", [LINE])
    # Every run of the printed paragraph, and none of the other.
    assert measured.printed == printed_share(LINE, [LINE]).runs
    assert 0.5 < measured.share < 0.7


def test_lines_printed_in_another_order_are_still_shared() -> None:
    """Cards and grids print their items side by side, not in the bundle's
    order: runs are taken within a line, never across a line break."""
    first = "A first card holds a sentence of its own about the ferry."
    second = "A second card holds another about the lighthouse keeper."
    measured = printed_share(f"{first}\n{second}", [f"{second} {first}"])
    assert measured.share == 1.0


def test_page_breaks_do_not_split_a_paragraph() -> None:
    half = len(LINE) // 2
    measured = printed_share(LINE, [LINE[:half], LINE[half:]])
    assert measured.share == 1.0


def test_recognized_text_is_not_the_bundle_text_the_pdf_must_print() -> None:
    ocr = (
        "[OCR text from asset-001, machine-recognized by Tesseract, mean "
        "confidence 47%:]\nwords read off a picture that the PDF never prints "
        "as text at all\n[End of OCR text from asset-001]"
    )
    assert printed_share(f"{LINE}\n{ocr}", [LINE]).share == 1.0


def test_a_bundle_with_no_text_has_nothing_to_miss() -> None:
    assert printed_share("", ["anything"]) == PrintedShare(runs=0, printed=0)
    assert PrintedShare(runs=0, printed=0).share == 1.0


def test_a_low_share_is_named_with_its_counts() -> None:
    assert printed_text_warning(PrintedShare(runs=1000, printed=999)) is None
    at = int(LOW_SHARE * 1000)
    assert printed_text_warning(PrintedShare(runs=1000, printed=at)) is None
    assert printed_text_warning(PrintedShare(runs=1000, printed=56)) == (
        "The PDF's text layer holds 56 of the 1,000 distinct "
        f"{RUN_CHARACTERS}-character runs of the bundle's text (6%), so the PDF "
        "shows much less of the page than content.md and content.txt, which are "
        "embedded in it, say it has. Something kept the rest from printing: an "
        "overlay over the text, a wall that hides the article, or content the page "
        "keeps collapsed, scrolled out of its box or hidden from print. Look at "
        "the PDF before relying on it."
    )


def test_too_little_text_for_a_share_to_mean_anything_says_nothing() -> None:
    assert printed_text_warning(PrintedShare(runs=MIN_RUNS - 1, printed=0)) is None
    assert printed_text_warning(PrintedShare(runs=MIN_RUNS, printed=0)) is not None


def test_a_wall_is_named_and_excluding_it_is_not_offered() -> None:
    message = printed_text_warning(PrintedShare(runs=1000, printed=56), ["Piano"])
    assert message is not None
    assert message.startswith(
        "The page showed Piano's access wall, which asks the reader to sign in, "
        "register or subscribe. The PDF's text layer holds 56 of the 1,000"
    )
    assert "--exclude would not bring the article back" in message
    assert message.endswith(
        "Open the page in your own browser, signed in if you have an account, "
        "and save it from there."
    )


def _capture(tmp_path: Path, *args: str) -> tuple[int, list[str], str, str]:
    pdf = tmp_path / "capture.pdf"
    code = main([str(FIXTURE), "--output", str(pdf), "--no-ocr", *args])
    bundle = pdf.with_suffix(".ai")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    markdown = " ".join((bundle / "content.md").read_text(encoding="utf-8").split())
    return code, list(manifest["warnings"]), text, markdown


#: In the document, so in the bundle; drawn transparent, so not in the PDF.
HIDDEN = "only two entries were lost entirely"


@pytest.mark.browser
def test_a_registration_wall_over_a_hidden_article_is_named(tmp_path: Path) -> None:
    code, warnings, text, markdown = _capture(tmp_path)
    assert code == 0
    assert HIDDEN in markdown and HIDDEN not in text
    walled = [w for w in warnings if w.startswith("The page showed Mg2 ConneXt's")]
    assert len(walled) == 1, warnings
    assert "--exclude would not bring the article back" in walled[0]
    # No warning offers the remedy that cannot work.
    assert not [w for w in warnings if "Pass --exclude" in w], warnings


@pytest.mark.browser
def test_excluding_the_wall_does_not_bring_the_article_back(tmp_path: Path) -> None:
    """Without the wall on screen, nothing is named, and the loss still is."""
    code, warnings, text, markdown = _capture(tmp_path, "--exclude", ".Mg2-connext")
    assert code == 0
    assert HIDDEN in markdown and HIDDEN not in text
    assert "Create a free account" not in text
    lost = [w for w in warnings if w.startswith("The PDF's text layer holds")]
    assert len(lost) == 1, warnings
    assert "Something kept the rest from printing" in lost[0]
