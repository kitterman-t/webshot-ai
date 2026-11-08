"""Clean mode hides a fixed box drawn on top across the window (docs/09 P113-4).

A news site's welcome ad printed over the first pages of its article in clean
mode. Its dialog was 640 px wide, under the 45% of a 1440 px window the size
rule asks for, and its backdrop, a child covering the whole window, had
`z-index: -1` inside the dialog's stacking context, so it read as behind the
page while printing over it. tests/fixtures/interstitial_page.html holds that
shape beside a masthead bar and a decorative fixed background, which stay;
interstitial_shell_page.html holds a page kept whole in a fixed scrolling
shell, which is the page and stays too; interstitial_short_page.html a short
page over a fixed background at `z-index: -1`, which is alone at most points
below the body's end and stays as well. All of their text is invented.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import (
    HIDE_OBSTRUCTIVE_OVERLAYS_JS,
    INTERSTITIAL_POINTS,
    INTERSTITIAL_TEXT_SHARE,
    interstitials_message,
)
from webshot.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
PROMO = ("SPONSORED MESSAGE", "Close this message", "Moonbeam connectors")
ARTICLE = (
    "Gulls learn the ferry timetable",
    "which the keeper wrote down twice.",
    "The last paragraph says the timetable is posted beside the ticket window.",
)


def capture(
    tmp_path: Path, fixture: str, mode: str
) -> tuple[str, list[str], dict[str, Any]]:
    """content.md, each page's text layer, and the manifest."""
    pdf = tmp_path / f"{mode}.pdf"
    code = main(
        [str(FIXTURES / fixture), "--output", str(pdf), "--mode", mode, "--no-ocr"]
    )
    assert code == 0
    bundle = pdf.with_suffix(".ai")
    pages = [page.extract_text() or "" for page in PdfReader(str(pdf)).pages]
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    return (bundle / "content.md").read_text(encoding="utf-8"), pages, manifest


@pytest.mark.browser
def test_clean_mode_hides_an_interstitial_and_its_backdrop(tmp_path: Path) -> None:
    markdown, pages, manifest = capture(tmp_path, "interstitial_page.html", "clean")
    flat = " ".join("\n".join(pages).split())
    for surface in (markdown, flat):
        for phrase in PROMO:
            assert phrase not in surface, (phrase, surface[:400])
    for phrase in ARTICLE:
        assert phrase in " ".join(markdown.split()), phrase
        assert phrase in flat, phrase
    # The masthead is a bar, on top at three points of the nine at most.
    assert "The Lantern Gazette" in flat
    hid = [w for w in manifest["warnings"] if w.startswith("Clean mode hid")]
    assert hid == [interstitials_message(["div#promo-overlay"])]
    # Nothing is left over the text for the covering check to report.
    assert not [w for w in manifest["warnings"] if "prints over" in w]


@pytest.mark.browser
def test_faithful_mode_keeps_it_and_the_covering_check_names_it(
    tmp_path: Path,
) -> None:
    _, pages, manifest = capture(tmp_path, "interstitial_page.html", "faithful")
    assert "SPONSORED MESSAGE" in " ".join("\n".join(pages).split())
    assert not [w for w in manifest["warnings"] if w.startswith("Clean mode hid")]
    covering = [w for w in manifest["warnings"] if "prints over" in w]
    assert len(covering) == 1, covering
    assert covering[0].startswith("A fixed box, div#promo-overlay, prints over ")


@pytest.mark.browser
def test_a_page_kept_in_a_fixed_shell_is_not_taken_for_one(tmp_path: Path) -> None:
    markdown, pages, manifest = capture(
        tmp_path, "interstitial_shell_page.html", "clean"
    )
    flat = " ".join("\n".join(pages).split())
    for phrase in (
        "Tide tables for the north pier",
        "so clean mode must keep it whole.",
    ):
        assert phrase in flat, phrase
        assert phrase in " ".join(markdown.split()), phrase
    assert not [w for w in manifest["warnings"] if w.startswith("Clean mode hid")]


def test_the_warning_names_every_box_and_counts_past_three() -> None:
    one = interstitials_message(["div#a"])
    assert one.startswith("Clean mode hid the fixed box drawn on top")
    assert ": div#a. " in one
    many = interstitials_message(["div#a", "div#b", "div#c", "div.d", "aside"])
    assert "the fixed boxes drawn on top" in many
    assert ": div#a, div#b, div#c and 2 more. " in many


async def _hide_in(fixture: str) -> tuple[dict[str, Any], list[str]]:
    """The overlay pass's answer on a fixture at WebShot's window, and the
    elements it marked."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            await page.goto((FIXTURES / fixture).as_uri())
            answer = await page.evaluate(
                HIDE_OBSTRUCTIVE_OVERLAYS_JS,
                [INTERSTITIAL_POINTS, INTERSTITIAL_TEXT_SHARE],
            )
            marked = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-hidden]')]
                    .map(e => e.className || e.id)"""
            )
        finally:
            await browser.close()
    return answer, marked


@pytest.mark.browser
def test_a_background_beneath_a_short_page_is_not_taken_for_one(
    tmp_path: Path,
) -> None:
    """Review of P113-4: below the end of a short page's body, a fixed
    background at z-index -1 is the only thing at a point, so it was "on
    top" at most of the grid and hid as an interstitial. A root painted
    beneath the page is not on top of it."""
    answer, marked = asyncio.run(_hide_in("interstitial_short_page.html"))
    assert answer == {"count": 0, "covering": []}
    assert marked == []
    _, pages, manifest = capture(tmp_path, "interstitial_short_page.html", "clean")
    assert "The north pier is closed for repairs" in " ".join(pages)
    assert not [w for w in manifest["warnings"] if w.startswith("Clean mode hid")]


@pytest.mark.browser
def test_the_measured_backdrop_at_minus_one_inside_its_dialog_still_counts() -> None:
    """The fix keys on the fixed root's stacking, not the hit's: the ad's
    backdrop is at -1 inside a dialog at 2147483644, and that stays hidden."""
    answer, marked = asyncio.run(_hide_in("interstitial_page.html"))
    assert answer["covering"] == ["div#promo-overlay"]
    assert "promo-overlay" in marked
    assert "backdrop-art" not in marked
