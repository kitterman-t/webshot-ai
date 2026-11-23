"""A video's watch page, printed for reading in clean mode (docs/09 P120-6).

Two watch pages captured with the defaults printed the site's bar ("Skip
navigation") over a black player, a rail of related videos with blank
thumbnails, the description closed, and a date only as "2y ago": nothing on
the printed pages said when the video was published or captured. Clean mode
now hides the bar, the player and the rail, opens the description, and prints
the publication date and channel the page declares, with the capture's date,
in the footer. Faithful mode prints the page as it was shown. The bar clean
mode hides is page chrome to the headings check too (docs/09 P123-5).

The fixtures in tests/fixtures/watch_page/ are served by a route under the
site's own address, so the profile's address test sees a watch page and no
request leaves the machine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, BrowserContext, Playwright, Route
from pypdf import PdfReader

from webshot import pipeline
from webshot.capture.discover import DiscoveredMetadata
from webshot.capture.session import launch_context
from webshot.capture.watch_page import is_watch_page, watch_page_note
from webshot.cli import main
from webshot.config import CaptureOptions

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "watch_page"
FIXTURE = FIXTURES / "watch.html"
WATCH = "https://www.youtube.com/watch?v=abcdefghijk"
#: A watch page whose only `<h1>` is the site's name, in the bar.
MASTHEAD_TITLE = "https://www.youtube.com/watch?v=lmnopqrstuv"
PAGES = {WATCH: FIXTURE, MASTHEAD_TITLE: FIXTURES / "masthead_title.html"}


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer the site's address from inside the browser, and record each request."""
    asked: list[str] = []

    async def answer(route: Route) -> None:
        asked.append(route.request.url)
        if route.request.url in PAGES:
            await route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=PAGES[route.request.url].read_bytes(),
            )
        else:
            await route.fulfill(status=404, body="")

    async def launch(
        playwright: Playwright, options: CaptureOptions
    ) -> tuple[BrowserContext, Browser | None]:
        context, browser = await launch_context(playwright, options)
        await context.route("https://www.youtube.com/**", answer)
        return context, browser

    monkeypatch.setattr(pipeline, "launch_context", launch)
    return asked


def _capture(
    tmp_path: Path, *flags: str, url: str = WATCH
) -> tuple[dict[str, Any], str, list[str]]:
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main(
        [url, "--output", str(pdf), "--no-ocr", "--report", str(report), *flags]
    )
    record = json.loads(report.read_text("utf-8"))
    assert code == 0, record.get("error")
    pages = [page.extract_text() or "" for page in PdfReader(str(pdf)).pages]
    return record, " ".join(" ".join(pages).split()), pages


@pytest.mark.browser
def test_clean_mode_prints_the_video_and_its_dates(
    tmp_path: Path, site: list[str]
) -> None:
    report, text, pages = _capture(tmp_path)
    today = datetime.now(UTC).date().isoformat()
    note = f"Published 2024-11-12 by Harbor Town Council. Captured {today}."
    assert all(note in " ".join(page.split()) for page in pages), pages
    assert "The description ends with the timetable's effective date." in text
    for chrome in (
        "Skip navigation",
        "Sign in",
        "Another video about the lighthouse",
        # The chat replay's frame (docs/09 P135-7).
        "Chat replay of the harbor stream",
    ):
        assert chrome not in text, chrome
    assert not [w for w in report["warnings"] if "closed control" in w]
    assert site and all(url.startswith("https://www.youtube.com/") for url in site)


@pytest.mark.browser
def test_faithful_mode_prints_the_watch_page_as_shown(
    tmp_path: Path, site: list[str]
) -> None:
    report, text, _ = _capture(tmp_path, "--mode", "faithful")
    assert "Published 2024-11-12" not in text
    assert "Skip navigation" in text
    assert "Another video about the lighthouse keepers" in text
    assert "Chat replay of the harbor stream" in text
    [folded] = [w for w in report["warnings"] if "closed control" in w]
    assert "ytd-text-inline-expander#description-inline-expander" in folded


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_a_title_in_the_bar_clean_mode_hides_is_not_named(
    tmp_path: Path, site: list[str], mode: str
) -> None:
    """The headings check is told which selectors clean mode hid as page
    chrome, so a title removed with them is what was asked for, whatever
    region was chosen (docs/04 §5 item 24). On a watch page clean mode also
    hides the site's bar, and the site's name in an `<h1>` there was named as
    a title the region WebShot chose left out. Faithful mode hides no bar,
    so there the choice of region alone leaves it out, and it is named."""
    report, text, _ = _capture(
        tmp_path, "--auto-selector", "--mode", mode, url=MASTHEAD_TITLE
    )
    # The region chosen leaves out the bar and the rail beside the column.
    assert "Quayside Reels" not in text
    assert "Another video about the lighthouse keepers" not in text
    assert "Winter landings at the north quay" in text
    titles = [w for w in report["warnings"] if "main heading" in w]
    assert titles == (
        []
        if mode == "clean"
        else [
            "The page's main heading, \"Quayside Reels\", is not in the PDF's text: "
            "it lies outside the region WebShot chose as the page's main content, "
            "so the PDF has no title. --selector with a region that holds it "
            "keeps it."
        ]
    ), report["warnings"]


def test_only_a_watch_address_is_a_watch_page() -> None:
    assert is_watch_page(WATCH)
    assert is_watch_page("https://m.youtube.com/watch?v=abc&t=10s")
    for other in (
        "https://www.youtube.com/",
        "https://www.youtube.com/@channel",
        "https://www.youtube.com/watch",
        "https://youtube.example/watch?v=abc",
        "file:///tmp/watch?v=abc",
    ):
        assert not is_watch_page(other), other


def test_the_note_says_only_what_the_page_declares() -> None:
    captured = datetime(2026, 11, 15, tzinfo=UTC).date()
    assert watch_page_note(DiscoveredMetadata(date="2024-11-12"), captured) == (
        "Published 2024-11-12. Captured 2026-11-15."
    )
    assert watch_page_note(DiscoveredMetadata(author="A channel"), captured) == (
        "By A channel. Captured 2026-11-15."
    )
    assert watch_page_note(DiscoveredMetadata(), captured) == "Captured 2026-11-15."
