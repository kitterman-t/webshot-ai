"""Text the page keeps behind a closed control, named in a warning (docs/09 P102).

A legal case page's main content, a 1,020-word summary, sat in the served HTML
as a Bootstrap collapse behind "show full summary"; the PDF and content.md
held only the 80-word teaser, and nothing said so. A video page's description
stopped at "…more", its text not fetched until the button is pressed.

tests/fixtures/collapsed_content_page.html holds one of each, beside a closed
<details> of prose, and the closed controls that are not the page's text: a
folded navigation menu, a short <details>, a folded footer notice. All of its
text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.capture.collapsed import (
    MIN_CHARACTERS,
    Collapsed,
    CollapsedRegion,
    collapsed_content_warning,
)
from webshot.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "collapsed_content_page.html"

HIDDEN = CollapsedRegion("hidden", "button#show", "show all", "div#all", 1234)
DETAILS = CollapsedRegion("details", "summary", "Why?", "details", 300)
FETCHED = CollapsedRegion("fetched", "button#expand", "more", "div#expander", None)


def test_each_kind_says_where_its_text_is() -> None:
    assert collapsed_content_warning(
        Collapsed((HIDDEN, DETAILS, FETCHED)), bundle=True
    ) == (
        "The page keeps text behind 3 closed controls, so the PDF does not show it: "
        'button#show ("show all") opens div#all, 1,234 characters, in neither the '
        'PDF nor the bundle; details ("Why?"), 300 characters, in the bundle but '
        'not in the PDF; button#expand ("more") in div#expander, whose text the '
        "page fetches only when it is opened. WebShot records the page as it was "
        "shown and does not open them. To capture the text, open the control in "
        "your own browser and save the page there, or pass a --css stylesheet that "
        "shows the region."
    )


def test_without_a_bundle_nothing_is_said_to_be_in_one() -> None:
    """With --no-ai-bundle there is no bundle to hold a closed <details>
    (review of #313)."""
    message = collapsed_content_warning(Collapsed((HIDDEN, DETAILS)), bundle=False)
    assert message is not None
    assert "bundle" not in message
    assert "opens div#all, 1,234 characters, not in the PDF;" in message
    assert 'details ("Why?"), 300 characters, not in the PDF.' in message


def test_text_the_page_has_not_fetched_is_not_offered_a_stylesheet() -> None:
    message = collapsed_content_warning(Collapsed((FETCHED,)), bundle=True)
    assert message is not None
    assert message.endswith(
        "open the control in your own browser and save the page there."
    )
    assert "--css" not in message


def test_many_regions_name_five_and_count_the_rest() -> None:
    message = collapsed_content_warning(Collapsed((HIDDEN,) * 7), bundle=True)
    assert message is not None
    assert message.startswith("The page keeps text behind 7 closed controls")
    assert message.count("opens div#all") == 5
    assert "; and 2 more." in message


def test_none_found_says_nothing_and_an_unread_page_says_so() -> None:
    assert collapsed_content_warning(Collapsed(), bundle=True) is None
    assert collapsed_content_warning(
        Collapsed(unread="Execution context was destroyed"), bundle=True
    ) == (
        "WebShot could not read the page for text it keeps collapsed (Execution "
        "context was destroyed), so whether some is missing from the PDF was not "
        "checked."
    )


@pytest.mark.browser
def test_the_closed_regions_holding_the_pages_text_are_named(tmp_path: Path) -> None:
    pdf = tmp_path / "capture.pdf"
    assert main([str(FIXTURE), "--output", str(pdf), "--no-ocr"]) == 0
    bundle = pdf.with_suffix(".ai")
    warnings = json.loads((bundle / "manifest.json").read_text("utf-8"))["warnings"]
    markdown = " ".join((bundle / "content.md").read_text("utf-8").split())
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith(
        "The page keeps text behind 3 closed controls, so the PDF does not show it: "
        'button#showSummary ("show full summary") opens span#summaryFull.collapse, '
    )
    assert 'details ("Why were two entries lost?"),' in folded
    assert "characters, in the bundle but not in the PDF;" in folded
    assert (
        'button#expand ("…more") in ytd-text-inline-expander#description-inline-expander, '
        "whose text the page fetches only when it is opened."
    ) in folded
    # What the warning says of each is what the capture holds.
    summary = "asked both sides for the logbooks of the three winters before"
    assert summary not in markdown and summary not in text
    answer = "only the two February entries could not be read again"
    assert answer in markdown and answer not in text
    # Page chrome, and a region under the floor, are not named.
    for quiet in ("Menu", "Legal", "Is the ledger on view?"):
        assert quiet not in folded, quiet
    assert len("On weekday mornings.") < MIN_CHARACTERS


@pytest.mark.browser
def test_text_the_capture_hid_itself_is_not_reported_as_closed(tmp_path: Path) -> None:
    """An --exclude hides the region with display: none, so it has no box, but
    the page did not close it (review of #313)."""
    pdf = tmp_path / "capture.pdf"
    assert (
        main(
            [
                str(FIXTURE),
                "--output",
                str(pdf),
                "--no-ocr",
                "--exclude",
                "#summaryFull",
            ]
        )
        == 0
    )
    warnings = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )["warnings"]
    [folded] = [w for w in warnings if "closed control" in w]
    assert "summaryFull" not in folded
    assert folded.startswith("The page keeps text behind 2 closed controls")
