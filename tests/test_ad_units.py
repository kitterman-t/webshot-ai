"""Clean mode hides the ad units an ad network writes into an article (docs/09 P23-9).

An ad network places its units inside the article, so selecting the content
region keeps them. On a statistics page, Mediavine's video player ("My Latest
Videos") sat between a calculator's result and its verdict, and the text
recognized in a screenshot of its ad frame went into content.md and the PDF's
text layer at that point. tests/fixtures/ad_player_page.html has that player's
markup, an AdSense unit and a Publisher Tag slot, beside article content that
looks like them by name: an "Advertisement" picture, a `download-header`, an
`mv-notes` section. All of its text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "ad_player_page.html"
ADS = (
    "My Latest Videos",
    "Report this ad",
    "Video ad caption offer",
    "Sponsored offer text",
    "Ad slot text",
)
ARTICLE = (
    "Difference: 0.25",
    "The verdict: the boxes differ.",
    "An advertisement for boxes from 1952, part of the article.",
    "It subtracts the lighter box from the heavier one.",
)


def capture(tmp_path: Path, mode: str) -> tuple[str, dict[str, object]]:
    """content.md and the PDF's text layer, one after the other, and the report."""
    pdf = tmp_path / f"{mode}.pdf"
    report = tmp_path / f"{mode}.json"
    code = main(
        [
            str(FIXTURE),
            "--output",
            str(pdf),
            "--mode",
            mode,
            "--no-ocr",
            "--report",
            str(report),
        ]
    )
    assert code == 0
    bundle = pdf.with_suffix(".ai")
    text = (bundle / "content.md").read_text(encoding="utf-8")
    layer = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    return text + "\n" + layer, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.browser
def test_clean_mode_hides_the_ad_units_and_keeps_what_only_looks_like_one(
    tmp_path: Path,
) -> None:
    text, _ = capture(tmp_path, "clean")
    for phrase in ADS:
        assert phrase not in text, phrase
    for phrase in ARTICLE:
        assert phrase in " ".join(text.split()), phrase
    # The result and the verdict are no longer split by the player.
    flat = " ".join(text.split())
    assert flat.index("Difference: 0.25") < flat.index("The verdict: the boxes differ.")
    assert "How the calculator works" in text


@pytest.mark.browser
def test_faithful_mode_keeps_the_ad_units(tmp_path: Path) -> None:
    """The rule is clean mode's: a faithful capture records the page as it was."""
    text, _ = capture(tmp_path, "faithful")
    assert "My Latest Videos" in text and "Sponsored offer text" in text
