"""The one module that imports Trafilatura (CONTRIBUTING rule 1).

Two questions get asked of the captured, post-JS HTML, and Trafilatura is
better at both than the heuristics v2 had:

**Where is the main content?**  Trafilatura reports the *text* of the article,
not a CSS selector, so `capture/prepare.py` locates that text back in the live
DOM and isolates the element that holds it.  The v2 static selector list stays
as the fallback, and the manifest records which one won — a capture that
silently changed how it chose its content region would be unauditable.

**What does the page say about itself?**  Title, author, date, sitename, read
from the page's own declarations.  This runs on every capture, and it is
strictly additive: nothing WebShot already recorded changes shape because of
it.

One correction to the library's defaults is load-bearing (docs/09 P3-3):
`extract_metadata` defaults to `extensive=True`, which lets htmldate *infer* a
publication date for a page that declares none — the fixture corpus got
"2026-01-01" for a document with no date anywhere in it.  A capture tool may
not invent provenance, and an inferred value would also change with the
calendar, so the extensive pass is off.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from trafilatura import extract, extract_metadata

LOGGER = logging.getLogger("webshot")

#: Below this, "the main content" is not a claim worth making: a page with a
#: paragraph on it has no main region to isolate, and matching a handful of
#: words back into the DOM would land on something arbitrary.
MINIMUM_MAIN_CONTENT_CHARACTERS = 200


@dataclass(frozen=True, slots=True)
class PageReading:
    """Everything Trafilatura has to say about one captured page.

    One object, and one call, because both answers come from parsing the same
    HTML: reading them separately would parse it twice and — worse — describe
    two different moments of a live DOM.  It also means the caller decides
    once that this work belongs off the event loop.
    """

    metadata: DiscoveredMetadata
    #: The article's text, or None when the page has no main region worth
    #: isolating.  A probe for the DOM search, never published.
    article: str | None


@dataclass(frozen=True, slots=True)
class DiscoveredMetadata:
    """What the page declares about itself, as Trafilatura reads it.

    Empty strings rather than nulls, matching the `page_metadata` record the
    DOM side already publishes: a consumer reads one shape for both.
    """

    title: str = ""
    author: str = ""
    date: str = ""
    sitename: str = ""


def read_page(html: str, *, url: str | None = None) -> PageReading:
    """Both readings of one page, in one blocking call."""
    return PageReading(
        metadata=read_metadata(html, url=url), article=main_content_text(html)
    )


def read_metadata(html: str, *, url: str | None = None) -> DiscoveredMetadata:
    """Read the page's declared metadata, inventing nothing."""
    try:
        document = extract_metadata(html, default_url=url, extensive=False)
    except Exception as exc:  # a capture must never fail over its metadata
        LOGGER.debug("Metadata extraction failed: %s", exc)
        return DiscoveredMetadata()
    if document is None:
        return DiscoveredMetadata()
    return DiscoveredMetadata(
        title=document.title or "",
        author=document.author or "",
        date=document.date or "",
        sitename=document.sitename or "",
    )


def main_content_text(html: str) -> str | None:
    """The article text Trafilatura finds, or None if it finds no article.

    `favor_recall` is deliberate: this text is a *probe* used to find an
    element in the DOM, so missing a paragraph costs more than including a
    marginal one.  Tables are included for the same reason — a report whose
    substance is tabular must not look like an empty region.
    """
    try:
        text = extract(
            html,
            favor_recall=True,
            include_tables=True,
            include_comments=False,
            include_formatting=False,
        )
    except Exception as exc:  # the static selector fallback still applies
        LOGGER.debug("Main-content extraction failed: %s", exc)
        return None
    if not text or len(text) < MINIMUM_MAIN_CONTENT_CHARACTERS:
        return None
    return str(text)
