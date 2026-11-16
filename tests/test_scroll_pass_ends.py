"""Two ways the lazy-load pass ended badly (docs/09 P120-4, P120-5).

A news site appends its next article as the window nears the end of one and
puts that article's address and title on the page. The pass scrolled on
through ten articles, and the capture printed all of them under the tenth
one's title and address, with no warning. The pass now notes the address and
title at its first step, stops when the address changes (a change of fragment
alone is a page marking where the reader is), keeps the first address and
title, and hides the articles the page appended while it was scrolled.

A policy page's window stopped short of the height the pass measured, and the
pass ran to its limit, 100 steps and then 300, on a page that was complete.
It now stops once the window has stayed put for `UNMOVED_STEPS` steps with
the page not growing, and says where the window got to.

The pages are in tests/fixtures/scroll_pass/ and are served from loopback.
"""

from __future__ import annotations

import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from conftest import serve
from webshot.capture.prepare import UNMOVED_STEPS, LazyLoadPass, lazy_load_warning
from webshot.cli import main

PAGES = Path(__file__).resolve().parent / "fixtures" / "scroll_pass"


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class _Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            name = self.path.split("?", 1)[0].lstrip("/")
            path = PAGES / name
            if not name or not path.is_file():
                self.send_error(404)
                return
            payload = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Pages) as origin:
        yield origin


def _capture(tmp_path: Path, url: str, *flags: str) -> tuple[dict[str, Any], str, str]:
    """The report, content.md and PDF text of a capture, whitespace folded."""
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main(
        [url, "--output", str(pdf), "--no-ocr", "--report", str(report), *flags]
    )
    record = json.loads(report.read_text("utf-8"))
    assert code == 0, record.get("error")
    markdown = " ".join((pdf.with_suffix(".ai") / "content.md").read_text().split())
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    return record, markdown, text


@pytest.mark.browser
def test_a_page_that_appends_its_next_article_keeps_the_first(
    tmp_path: Path, site: str
) -> None:
    url = f"{site}/appending_articles.html"
    report, markdown, text = _capture(tmp_path, url)
    assert report["title"] == "The harbor office opens a winter timetable"
    assert report["final_url"] == url
    [moved] = [w for w in report["warnings"] if "changed its address" in w]
    assert moved.startswith(
        f"The page changed its address while it was scrolled, from {url} to "
        f'{site}/the-ferry-company-counts.html ("The ferry company counts its '
        'summer passengers"), as a site that appends its next article does, so '
        "the lazy-load pass stopped at step "
    )
    assert moved.endswith(
        "The capture keeps the first address and title, and leaves out the 1 "
        "article the page appended while it was scrolled."
    )
    first = "The first story ends with the harbor master reading the new times aloud."
    second = "The second story ends with the ledger closed for the season."
    assert first in markdown and first in text
    assert second not in markdown and second not in text
    # The running header prints the first article's title too.
    assert "The ferry company counts its summer passengers" not in text
    # And every record the bundle reads from the page names the first article,
    # not only the report (review of #367).
    bundle = tmp_path / "capture.ai"
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    assert manifest["title"] == "The harbor office opens a winter timetable"
    assert manifest["page_metadata"]["title"] == (
        "The harbor office opens a winter timetable"
    )
    assert manifest["final_url"] == url
    assert "<title>The harbor office opens a winter timetable</title>" in (
        (bundle / "content.html").read_text("utf-8")
    )


@pytest.mark.browser
def test_an_appended_article_a_stylesheet_shows_is_named_for_what_it_is(
    tmp_path: Path, site: str
) -> None:
    """A stylesheet that outranks WebShot's hiding rule prints the article,
    so P128 takes the mark off and names the rule that set it: the pass's
    mark says which rule it was, as every other one does."""
    css = tmp_path / "shows.css"
    css.write_text("main#feed > article#second { display: block !important; }\n")
    report, markdown, _ = _capture(
        tmp_path, f"{site}/appending_articles.html", "--css", str(css)
    )
    [named] = [w for w in report["warnings"] if "shows anyway" in w]
    assert (
        "article#second (an article the page appended after the one captured)" in named
    ), named
    assert "The second story ends with the ledger closed for the season." in markdown


@pytest.mark.browser
def test_a_page_that_marks_its_fragment_is_scrolled_to_the_end(
    tmp_path: Path, site: str
) -> None:
    url = f"{site}/appending_articles.html#fragment"
    report, markdown, _ = _capture(tmp_path, url)
    assert not [w for w in report["warnings"] if "changed its address" in w]
    assert not [w for w in report["warnings"] if "lazy-load" in w.lower()]
    assert "The first story ends with the harbor master" in markdown


@pytest.mark.browser
def test_a_window_that_does_not_scroll_ends_the_pass_and_says_where(
    tmp_path: Path, site: str
) -> None:
    report, markdown, _ = _capture(tmp_path, f"{site}/window_does_not_scroll.html")
    [stopped] = [w for w in report["warnings"] if "lazy-load pass" in w]
    assert stopped.startswith(
        f"The lazy-load pass stopped at step {UNMOVED_STEPS + 1}: the window could "
        "scroll no further than "
    )
    assert f"and the page did not grow for {UNMOVED_STEPS} steps." in stopped
    assert not [w for w in report["warnings"] if "safety limit" in w]
    assert "Paragraph 80 of a page that scrolls inside its own box" in markdown


def test_the_unmoved_warning_says_how_far_the_window_got() -> None:
    result = LazyLoadPass(
        steps=9,
        ended="unmoved",
        height=10_675,
        first_height=10_675,
        window_bottom=9_480,
    )
    assert lazy_load_warning(result, 100) == (
        "The lazy-load pass stopped at step 9: the window could scroll no further "
        "than 9,480 pixels down a page measured at 10,675, and the page did not "
        f"grow for {UNMOVED_STEPS} steps. Content below that the page loads only "
        "as it is scrolled, or that a box of the page's own scrolls into view, may "
        "not have loaded."
    )


def test_a_navigation_with_nothing_to_hide_says_what_stays() -> None:
    result = LazyLoadPass(
        steps=6,
        ended="navigated",
        height=9_000,
        loaded_url="https://news.test/a",
        loaded_title="A",
        navigated_url="https://news.test/b",
        navigated_title="",
    )
    assert lazy_load_warning(result, 100) == (
        "The page changed its address while it was scrolled, from "
        "https://news.test/a to https://news.test/b, as a site that appends its "
        "next article does, so the lazy-load pass stopped at step 6. The capture "
        "keeps the first address and title. What the page appended before the "
        "pass stopped is in it; --no-scroll captures the page as it loaded."
    )
