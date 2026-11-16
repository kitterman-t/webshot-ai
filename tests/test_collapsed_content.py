"""Text the page keeps behind a closed control, named in a warning (docs/09 P102).

A legal case page's main content, a 1,020-word summary, sat in the served HTML
as a Bootstrap collapse behind "show full summary"; the PDF and content.md
held only the 80-word teaser, and nothing said so. A video page's description
stopped at "…more", its text not fetched until the button is pressed.

tests/fixtures/collapsed_content_page.html holds one of each, beside a closed
<details> of prose, and the closed controls that are not the page's text: a
folded navigation menu, a short <details>, a folded footer notice.
tests/fixtures/collapsed_read_more_page.html holds the two shapes P102 missed
or misdescribed: an article that goes on behind "Story Continues" in a wrapper
with no box, and closed <details> whose contents the page's stylesheet hides
(docs/09 P120-2, P120-3). Clean mode opens them all, faithful mode and
`--collapsed closed` do not. All of the text is invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
READ_MORE = Path(__file__).parent / "fixtures" / "collapsed_read_more_page.html"
MINIFIED = Path(__file__).parent / "fixtures" / "collapsed_minified_page.html"
LAZY_IMAGE = Path(__file__).parent / "fixtures" / "collapsed_lazy_image_page.html"
#: A 2x2 opaque PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73"
    "0000001249444154789c63d4885ac0c0c0c0c40006000d4a0126aff0078f000000"
    "0049454e44ae426082"
)

HIDDEN = CollapsedRegion(
    "hidden", "button#show", "show all", "div#all", 1234, ("The town sued the ferry",)
)
DETAILS = CollapsedRegion(
    "details", "summary", "Why?", "details", 300, ("The old ledger was left beside",)
)
FETCHED = CollapsedRegion("fetched", "button#expand", "more", "div#expander", None)

#: A bundle holding the <details>' text and not the hidden region's.
BUNDLE = "Questions people ask\nWhy? The old ledger was left beside a window.\n"


def test_each_kind_says_where_its_text_is() -> None:
    assert collapsed_content_warning(
        Collapsed((HIDDEN, DETAILS, FETCHED)), bundle_text=BUNDLE
    ) == (
        "The page keeps text behind 3 closed controls, so the PDF does not show it: "
        'button#show ("show all") opens div#all, 1,234 characters, in neither the '
        'PDF nor the bundle; details ("Why?"), 300 characters, in the bundle but '
        'not in the PDF; button#expand ("more") in div#expander, whose text the '
        "page fetches only when it is opened. WebShot printed the page as it was "
        "shown; --collapsed open opens them before printing."
    )


def test_a_details_the_bundle_lacks_is_said_to_be_in_neither() -> None:
    """Where the text is, is read from the bundle: a policy page's closed
    <details> held contents its own stylesheet hid, and the warning said
    "in the bundle" of text the bundle did not hold (docs/09 P120-3)."""
    message = collapsed_content_warning(Collapsed((DETAILS,)), bundle_text="Other.")
    assert message is not None
    assert 'details ("Why?"), 300 characters, in neither the PDF nor the bundle.' in (
        message
    )


def test_without_a_bundle_nothing_is_said_to_be_in_one() -> None:
    """With --no-ai-bundle there is no bundle to hold a closed <details>
    (review of #313)."""
    message = collapsed_content_warning(Collapsed((HIDDEN, DETAILS)), bundle_text=None)
    assert message is not None
    assert "bundle" not in message
    assert "opens div#all, 1,234 characters, not in the PDF;" in message
    assert 'details ("Why?"), 300 characters, not in the PDF.' in message


def test_what_stayed_closed_after_opening_is_offered_what_works() -> None:
    """Once the capture has tried, the remedy is the reader's own browser, and
    a stylesheet where one can show the region: never for text the page has
    not fetched (docs/09 P120-3)."""
    for kept in (HIDDEN, DETAILS):
        message = collapsed_content_warning(
            Collapsed((kept,)), bundle_text=BUNDLE, opening="done"
        )
        assert message is not None
        assert message.endswith(
            "WebShot opened what it could, and this stayed closed. To capture the "
            "text, open the control in your own browser and save the page there, "
            "or pass a --css stylesheet that shows the region."
        )
    message = collapsed_content_warning(
        Collapsed((FETCHED,)), bundle_text=BUNDLE, opening="done"
    )
    assert message is not None
    assert message.endswith(
        "open the control in your own browser and save the page there."
    )
    assert "--css" not in message and "--collapsed" not in message


@pytest.mark.browser
def test_a_details_a_stylesheet_shows_is_not_named(tmp_path: Path) -> None:
    """A --css rule that shows a closed <details>' contents prints them and
    leaves the element closed; the check named it all the same (docs/09
    P120-7)."""
    css = Path(__file__).parent / "fixtures" / "collapsed_details_shown.css"
    warnings, _, text = _capture(
        tmp_path, READ_MORE, "--collapsed", "closed", "--css", str(css)
    )
    assert "the summer fare printed on the ticket applies" in text
    [folded] = [w for w in warnings if "closed control" in w]
    assert "details.section" not in folded
    assert folded.startswith("The page keeps text behind 1 closed control,")


def test_an_opening_that_failed_is_not_said_to_have_opened_anything() -> None:
    """An opening that could not read the page has its own warning, and this
    one must not say "opened what it could" beside it (review of #367)."""
    message = collapsed_content_warning(
        Collapsed((HIDDEN,)), bundle_text=BUNDLE, opening="failed"
    )
    assert message is not None
    assert "opened what it could" not in message
    assert message.endswith(
        "WebShot could not open them, as the warning before this one says. To "
        "capture the text, open the control in your own browser and save the page "
        "there, or pass a --css stylesheet that shows the region."
    )


def test_a_region_is_in_the_bundle_only_when_all_its_samples_are() -> None:
    """A teaser that repeats the hidden text's opening put its first words in
    the bundle, and the hidden text was said to be there (review of #367)."""
    region = CollapsedRegion(
        "hidden",
        "button#show",
        "show all",
        "div#all",
        1234,
        ("The town sued the ferry", "The court held a first hearing"),
    )
    message = collapsed_content_warning(
        Collapsed((region,)), bundle_text="The town sued the ferry company."
    )
    assert message is not None
    assert "in neither the PDF nor the bundle" in message


def test_many_regions_name_five_and_count_the_rest() -> None:
    message = collapsed_content_warning(Collapsed((HIDDEN,) * 7), bundle_text=BUNDLE)
    assert message is not None
    assert message.startswith("The page keeps text behind 7 closed controls")
    assert message.count("opens div#all") == 5
    assert "; and 2 more." in message


def test_none_found_says_nothing_and_an_unread_page_says_so() -> None:
    assert collapsed_content_warning(Collapsed(), bundle_text=BUNDLE) is None
    assert collapsed_content_warning(
        Collapsed(unread="Execution context was destroyed"), bundle_text=BUNDLE
    ) == (
        "WebShot could not read the page for text it keeps collapsed (Execution "
        "context was destroyed), so whether some is missing from the PDF was not "
        "checked."
    )


def _capture(tmp_path: Path, fixture: Path, *flags: str) -> tuple[list[str], str, str]:
    """The warnings, content.md and PDF text of a capture, whitespace folded."""
    pdf = tmp_path / "capture.pdf"
    assert main([str(fixture), "--output", str(pdf), "--no-ocr", *flags]) == 0
    bundle = pdf.with_suffix(".ai")
    warnings = json.loads((bundle / "manifest.json").read_text("utf-8"))["warnings"]
    markdown = " ".join((bundle / "content.md").read_text("utf-8").split())
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    return warnings, markdown, text


@pytest.mark.browser
def test_the_closed_regions_holding_the_pages_text_are_named(tmp_path: Path) -> None:
    warnings, markdown, text = _capture(tmp_path, FIXTURE, "--collapsed", "closed")
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith(
        "The page keeps text behind 3 closed controls, so the PDF does not show it: "
        'button#showSummary ("show full summary") opens span#summaryFull.collapse, '
    )
    assert 'details ("Why were two entries lost?"),' in folded
    assert "characters, in the bundle but not in the PDF;" in folded
    # The teaser repeats the summary's opening sentence, and is in the bundle;
    # the summary is not (review of #367).
    assert re.search(
        r"opens span#summaryFull\.collapse, [\d,]+ characters, in neither the PDF "
        r"nor the bundle;",
        folded,
    ), folded
    assert (
        'button#expand ("…more") in ytd-text-inline-expander#description-inline-expander, '
        "whose text the page fetches only when it is opened."
    ) in folded
    assert folded.endswith("--collapsed open opens them before printing.")
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
def test_clean_mode_opens_them_and_names_what_stayed_closed(tmp_path: Path) -> None:
    """Clean mode opens the regions the check would name, so their text is in
    the PDF and the bundle; the expander, whose button the stand-in does not
    answer, stays closed and is named (docs/09 P120-3)."""
    warnings, markdown, text = _capture(tmp_path, FIXTURE)
    summary = "asked both sides for the logbooks of the three winters before"
    answer = "only the two February entries could not be read again"
    for opened in (summary, answer):
        assert opened in markdown and opened in text, opened
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith("The page keeps text behind 1 closed control,")
    assert "ytd-text-inline-expander#description-inline-expander" in folded
    assert "this stayed closed" in folded
    # Page chrome stays folded: opening reaches only what the check names.
    assert "The harbor office and its opening hours" not in text
    assert "where a folded notice is page chrome" not in text


@pytest.mark.browser
def test_faithful_mode_leaves_them_as_the_page_showed_them(tmp_path: Path) -> None:
    warnings, _markdown, text = _capture(tmp_path, FIXTURE, "--mode", "faithful")
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith("The page keeps text behind 3 closed controls")
    assert "asked both sides for the logbooks" not in text


@pytest.mark.browser
def test_a_read_more_region_and_a_hidden_details_are_named_as_held(
    tmp_path: Path,
) -> None:
    """A "Story Continues" button whose article goes on in a wrapper with no
    box, and closed <details> whose contents the page hides, are named, and
    said to be in neither deliverable, which is what the capture holds
    (docs/09 P120-2, P120-3)."""
    warnings, markdown, text = _capture(tmp_path, READ_MORE, "--collapsed", "closed")
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith("The page keeps text behind 2 closed controls")
    assert (
        'button.readmore-button ("Story Continues") opens div.read-more-wrapper, '
    ) in folded
    assert 'details.section ("1. Who pays the winter fare"),' in folded
    assert folded.count("in neither the PDF nor the bundle") == 2
    for closed in (
        "The council meets again in March",
        "the summer fare printed on the ticket applies",
    ):
        assert closed not in markdown and closed not in text, closed


@pytest.mark.browser
def test_a_read_more_region_and_a_hidden_details_are_opened(tmp_path: Path) -> None:
    warnings, markdown, text = _capture(tmp_path, READ_MORE)
    assert not [w for w in warnings if "closed control" in w]
    for opened in (
        "The council meets again in March",
        "the summer fare printed on the ticket applies",
    ):
        assert opened in markdown and opened in text, opened


@pytest.mark.browser
def test_text_the_capture_hid_itself_is_not_reported_as_closed(tmp_path: Path) -> None:
    """An --exclude hides the region with display: none, so it has no box, but
    the page did not close it (review of #313); nor is it opened (P120-3)."""
    warnings, _, _ = _capture(
        tmp_path, FIXTURE, "--collapsed", "closed", "--exclude", "#summaryFull"
    )
    [folded] = [w for w in warnings if "closed control" in w]
    assert "summaryFull" not in folded
    assert folded.startswith("The page keeps text behind 2 closed controls")
    warnings, _markdown, text = _capture(tmp_path, FIXTURE, "--exclude", "#summaryFull")
    [folded] = [w for w in warnings if "closed control" in w]
    assert "summaryFull" not in folded
    assert "asked both sides for the logbooks" not in text


def test_the_mode_decides_unless_collapsed_is_given(tmp_path: Path) -> None:
    """Clean mode opens, faithful mode does not, and `--collapsed` overrides
    either; the report names it only when it was given (docs/09 P120-3)."""
    from webshot.config import CaptureOptions
    from webshot.report import resolved_options

    def options(**given: object) -> CaptureOptions:
        return CaptureOptions(
            source="https://site.test/a", output=tmp_path / "a.pdf", **given
        )

    assert options().opens_collapsed
    assert not options(mode="faithful").opens_collapsed
    assert not options(collapsed="closed").opens_collapsed
    assert options(mode="faithful", collapsed="open").opens_collapsed
    assert "collapsed" not in resolved_options(options()).model_dump()
    assert resolved_options(options(collapsed="closed")).model_dump()["collapsed"] == (
        "closed"
    )


@pytest.mark.browser
def test_minified_text_is_found_and_a_script_is_not_a_region(tmp_path: Path) -> None:
    """Text nodes are read a word apart, so minified markup's words are found
    in the bundle; and an inline script after "Read more" is not a closed
    region (review of #367)."""
    warnings, markdown, _ = _capture(tmp_path, MINIFIED, "--collapsed", "closed")
    [folded] = [w for w in warnings if "closed control" in w]
    assert folded.startswith("The page keeps text behind 1 closed control,")
    assert 'details.fares ("The winter fares"),' in folded
    assert "in the bundle but not in the PDF" in folded
    assert "script" not in folded and "more-button" not in folded
    assert "keeps the summer fare printed on it" in markdown


@pytest.mark.browser
def test_an_opening_that_fails_is_said_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webshot import pipeline
    from webshot.capture.collapsed import Opened

    async def fails(page: object) -> Opened:
        return Opened(unread="Execution context was destroyed")

    monkeypatch.setattr(pipeline, "open_collapsed", fails)
    warnings, _, _ = _capture(tmp_path, FIXTURE)
    assert any(
        w.startswith("WebShot could not open the page's closed controls")
        for w in warnings
    )
    [folded] = [w for w in warnings if "closed control" in w and "keeps text" in w]
    assert "opened what it could" not in folded
    assert "WebShot could not open them" in folded


@pytest.mark.browser
def test_a_lazy_picture_in_an_opened_region_is_loaded_before_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image wait ran before opening and skips a picture with no box, so a
    lazy one inside a region with no box printed blank (review of #367)."""
    import http.server
    import time

    from conftest import serve
    from webshot import pipeline

    page_bytes = LAZY_IMAGE.read_bytes()

    class _Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/chart.png":
                time.sleep(0.5)
                body, kind = PNG, "image/png"
            else:
                body, kind = page_bytes, "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    seen: list[object] = []
    original = pipeline.render_pdf

    async def render(page: Any, *args: Any, **kwargs: Any) -> None:
        seen.append(
            await page.evaluate(
                "() => { const img = document.getElementById('chart');"
                " return img.complete && img.naturalWidth > 0; }"
            )
        )
        await original(page, *args, **kwargs)

    monkeypatch.setattr(pipeline, "render_pdf", render)
    with serve(_Pages) as origin:
        pdf = tmp_path / "capture.pdf"
        assert (
            main([f"{origin}/page.html", "--output", str(pdf), "--no-ai-bundle"]) == 0
        )
    assert seen == [True]
