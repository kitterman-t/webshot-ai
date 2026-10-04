"""RENDER: print the prepared page and refuse to publish an unreadable PDF.

Chromium builds the structure tree and the heading outline itself, so tagging
and bookmarks are parameters rather than code (docs/04-spec.md §1.4, verified
in docs/09 S1).  Validation is the last gate before publication: a file that
pypdf cannot parse never reaches the output path.

Two kinds of caller print through this module.  The capture pipeline prints the
live page it just prepared.  The appendix renderers print an authored document
that has no page of its own — the protected path's transcript appendix, and the
web path's walkthroughs of the videos embedded in the page.  The appendix side
is synchronous and deliberately isolated — its own headless browser, no
authentication, no network — because the protected path may be running headed
and Chromium refuses to print outside headless mode.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as AsyncPage
from playwright.async_api import PdfMargins
from playwright.sync_api import Page, sync_playwright
from pypdf import PdfReader

from ..config import CaptureOptions
from ..errors import PdfValidationError
from .ligatures import (
    LIGATURES_OFF,
    LIGATURES_OFF_DECLARATION,
    SUPPRESS_LIGATURES_SCRIPT,
)

LOGGER = logging.getLogger("webshot")


def _header_footer_templates(
    title: str, source: str, *, total_is_final: bool = True
) -> tuple[str, str]:
    """The running header and footer Chromium prints on every page.

    `total_is_final=False` drops the "of N" half. Chromium resolves
    `totalPages` while printing, and a walkthrough appendix is merged onto the
    file *after* that — so a one-page capture with a five-page appendix printed
    "Page 1 of 1" on its first page and nothing at all on the other five. The
    number was not stale, it was measuring a different document (docs/09
    P8-68).

    Dropped rather than corrected, because there is nothing to correct it to:
    the placeholder is resolved inside the browser and the appendix's length is
    not known until it has been rendered, which happens later and in a second
    browser. "Page 3" is true on every page of the composed file; "Page 3 of 4"
    is false on four of them.
    """
    # The templates are separate documents that no page stylesheet reaches, and
    # the title in the header is in the text layer like any other text.
    common = (
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "font-size:8px;color:#64748b;width:100%;box-sizing:border-box;"
        f"{LIGATURES_OFF_DECLARATION}"
    )
    safe_title = html.escape(title)
    safe_source = html.escape(source)
    header = (
        f'<div style="{common}padding:0 0.6in;display:flex;justify-content:space-between;gap:16px;">'
        f'<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{safe_title}</span>'
        f'<span style="white-space:nowrap;">{safe_source}</span></div>'
    )
    total = ' of <span class="totalPages"></span>' if total_is_final else ""
    footer = (
        f'<div style="{common}padding:0 0.6in;display:flex;justify-content:space-between;">'
        f'<span>WebShot</span><span>Page <span class="pageNumber"></span>{total}'
        "</span></div>"
    )
    return header, footer


def print_geometry(options: CaptureOptions) -> dict[str, Any]:
    """The page box a capture is printed in: paper, orientation, scale, margins.

    Defined once for both prints of a capture — the render, and the probe
    `render/clipping.py` prints to learn the width the render was laid out at.
    A probe given a different page box would certify a different page.
    """
    margins: PdfMargins = {
        "top": options.margin,
        "right": options.margin,
        "bottom": options.margin,
        "left": options.margin,
    }
    return {
        "format": options.paper_format,
        "landscape": options.landscape,
        "prefer_css_page_size": options.prefer_css_page_size,
        "scale": options.scale,
        "margin": margins,
    }


async def render_pdf(
    page: AsyncPage,
    options: CaptureOptions,
    destination: Path,
    *,
    title: str,
    final_url: str,
    appendix_expected: bool = False,
) -> None:
    """Print the page, with the running header and footer unless disabled.

    `appendix_expected` is known by the time this runs — enrichment has already
    fetched the walkthroughs — and it decides whether the footer may claim a
    page total (see `_header_footer_templates`).
    """
    LOGGER.info("Rendering PDF...")
    extra: dict[str, Any] = {}
    if options.header_footer:
        source_label = (
            urlparse(final_url).netloc or Path(unquote(urlparse(final_url).path)).name
        )
        header, footer = _header_footer_templates(
            title, source_label, total_is_final=not appendix_expected
        )
        extra = {
            "display_header_footer": True,
            "header_template": header,
            "footer_template": footer,
        }
    # Translated here rather than left to `classify`. A `PlaywrightError` out
    # of `page.pdf()` — a browser that crashed mid-print, a PDF protocol
    # failure — reached the generic branch and was reported as exit 5,
    # capture-integrity; the published taxonomy assigns PDF render failures to
    # exit 7, and the distinction is what tells a caller whether the *page* was
    # the problem (docs/09 P8-59). Only this call is wrapped: an error raised
    # by anything else in this function is not a render failure.
    try:
        await page.pdf(
            path=destination,
            **print_geometry(options),
            print_background=True,
            tagged=options.tagged,
            outline=options.outline,
            **extra,
        )
    except PlaywrightError as exc:
        raise PdfValidationError(f"Chromium could not print the page: {exc}") from exc


def _print_appendix(
    load: Callable[[Page], Any],
    destination: Path,
    *,
    paper_format: str,
    margin: str,
    landscape: bool = False,
    prefer_css_page_size: bool = False,
    outline: bool = True,
) -> None:
    """The print settings every authored appendix is rendered with.

    Its own headless browser, no authentication, no network: an appendix is a
    self-contained document, and the protected path may be running headed —
    Chromium refuses to print outside headless mode.

    `landscape` and `prefer_css_page_size` are passed through because the
    appendix is bound into the *capture's* file and claims to use the capture's
    page box. It received only `format`, so a `--landscape` capture published a
    portrait appendix and one page orientation changed mid-document
    (docs/09 P8-29).
    """
    margins: PdfMargins = {
        "top": margin,
        "right": margin,
        "bottom": margin,
        "left": margin,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            load(page)
            # The same pass a capture gets: an appendix is read and searched
            # as much as the pages it follows (docs/09 P16-2).
            page.evaluate(SUPPRESS_LIGATURES_SCRIPT, LIGATURES_OFF)
            page.pdf(
                path=str(destination),
                format=paper_format,
                landscape=landscape,
                prefer_css_page_size=prefer_css_page_size,
                print_background=True,
                tagged=True,
                outline=outline,
                margin=margins,
            )
        finally:
            browser.close()


def render_html_to_pdf(
    html_document: str,
    destination: Path,
    *,
    paper_format: str = "Letter",
    margin: str = "0.6in",
) -> None:
    """Print a standalone HTML document with the same tagging as a capture.

    Synchronous by design: the callers assemble an appendix in a worker thread,
    and a second async Playwright instance inside a running capture would have
    to be threaded through the pipeline for no benefit.  `set_content` keeps
    the render offline — the document carries its own styles and references
    nothing.
    """
    _print_appendix(
        lambda page: page.set_content(html_document, wait_until="load"),
        destination,
        paper_format=paper_format,
        margin=margin,
    )


def render_document_to_pdf(
    document: Path,
    destination: Path,
    *,
    paper_format: str = "Letter",
    margin: str = "0.6in",
    landscape: bool = False,
    prefer_css_page_size: bool = False,
    outline: bool = True,
) -> None:
    """Print an authored HTML *file*, so it can reference local images.

    `set_content` leaves the page on `about:blank`, from which Chromium
    refuses to load `file:` subresources — measured: an image referenced that
    way arrives with `naturalWidth === 0`.  The video appendix illustrates
    every step with a screenshot downloaded into the staging bundle, so it is
    written to disk and navigated to instead.  Still offline: a `file:` page
    loading `file:` images reaches no network.
    """
    _print_appendix(
        lambda page: page.goto(document.resolve().as_uri(), wait_until="load"),
        destination,
        paper_format=paper_format,
        margin=margin,
        landscape=landscape,
        prefer_css_page_size=prefer_css_page_size,
        outline=outline,
    )


def is_tagged(path: Path) -> bool:
    """Whether the file really carries a structure tree, rather than was asked to.

    docs/05 task 1.1 is a *verification* task: `tagged=True` is a request, and
    what the manifest publishes should be what Chromium actually produced.  The
    golden harness asserts the same predicate independently, so a change to
    either side has to survive the other.
    """
    root = PdfReader(str(path)).root_object
    if "/StructTreeRoot" not in root:
        return False
    mark_info = root.get("/MarkInfo")
    return bool(mark_info and mark_info.get_object().get("/Marked"))


def validate_pdf(path: Path) -> tuple[int, int]:
    """Check the file signature and parse every page before publishing output."""
    size = path.stat().st_size
    if size < 1_000:
        raise PdfValidationError(f"Generated PDF is unexpectedly small ({size} bytes).")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise PdfValidationError("Generated output is not a valid PDF file.")
    try:
        pages = len(PdfReader(str(path)).pages)
    except Exception as exc:  # pypdf reports several parser-specific exception types
        raise PdfValidationError(
            f"Generated PDF failed structural validation: {exc}"
        ) from exc
    if pages < 1:
        raise PdfValidationError("Generated PDF contains no pages.")
    return pages, size
