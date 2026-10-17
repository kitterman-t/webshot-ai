"""The one module that imports Trafilatura (CONTRIBUTING rule 1).

Two questions get asked of the captured, post-JS HTML, and Trafilatura is
better at both than the heuristics v2 had:

**Where is the main content?**  Trafilatura reports the *text* of the article,
not a CSS selector, so `capture/prepare.py` locates that text back in the live
DOM and isolates the element that holds it.  The v2 static selector list stays
as the fallback, and the manifest records which one won — a capture that
silently changed how it chose its content region would be unauditable.

**What does the page say about itself?**  Title, author and sitename as
Trafilatura reads them, and the date the page declares.  This runs on every
capture, and it is strictly additive: nothing WebShot already recorded
changes shape because of it.

The date is not Trafilatura's (docs/09 P82).  Trafilatura gets it from
htmldate, and htmldate guesses.  With `extensive=True`, the default, it gave
"2026-01-01" to a document with no date anywhere in it (P3-3).  With
`extensive=False` it still reads a date from the URL's path ahead of the
page's `<meta>`, from a heading ahead of a `<time>`, and from a copyright
year, and since htmldate 1.11 from any `YYYY-MM-DD` in the text.  It drops a
declared date later than today, so its answer can change with the calendar,
and the order of its cues changes with its releases.  A capture tool may not
invent provenance, so `declared_date` reads the date from what the page
declares about itself, and Trafilatura's is never recorded.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from trafilatura import extract, extract_metadata

LOGGER = logging.getLogger("webshot")

# How strongly a declaration names the page's own date.  The first readable
# declaration of the strongest kind present is the date, so a page's stated
# publication beats its stated modification, and both beat a <time> that only
# marks a date somewhere in it.
_ORIGINAL, _MODIFIED, _MARKED = range(3)

#: The `<meta>` keys read as a date, by the `property`, `name`, `itemprop` or
#: `http-equiv` that carries them, lowercased, with `dc:` and `dcterms:`
#: spelled `dc.` and `dcterms.`.  A key not in this table is not read: the
#: table is the contract, not a sample of it.
_META_DATE_KEYS = {
    # Open Graph (ogp.me's article type, and the og:updated_time that
    # Facebook's crawler reads).
    "article:published_time": _ORIGINAL,
    "article:modified_time": _MODIFIED,
    "og:updated_time": _MODIFIED,
    # schema.org as microdata on a <meta>.
    "datepublished": _ORIGINAL,
    "datecreated": _ORIGINAL,
    "datemodified": _MODIFIED,
    # Dublin Core in HTML, DCMI's DC.* and DCTERMS.* names.
    "dc.date": _ORIGINAL,
    "dc.date.issued": _ORIGINAL,
    "dc.date.created": _ORIGINAL,
    "dc.date.modified": _MODIFIED,
    "dcterms.date": _ORIGINAL,
    "dcterms.issued": _ORIGINAL,
    "dcterms.created": _ORIGINAL,
    "dcterms.modified": _MODIFIED,
    # Highwire Press tags, which Google Scholar reads.
    "citation_publication_date": _ORIGINAL,
    "citation_date": _ORIGINAL,
    "citation_online_date": _ORIGINAL,
    # The plain names, and the HTTP header a page can restate.
    "date": _ORIGINAL,
    "pubdate": _ORIGINAL,
    "last-modified": _MODIFIED,
}

#: schema.org's three dates, as a JSON-LD key or a microdata `itemprop`.
_SCHEMA_ORG_DATES = {
    "datePublished": _ORIGINAL,
    "dateCreated": _ORIGINAL,
    "dateModified": _MODIFIED,
}
_ITEMPROP_DATES = {key.lower(): tier for key, tier in _SCHEMA_ORG_DATES.items()}

#: A calendar date no reader can take two ways: four-digit year, month, day,
#: as ISO 8601 writes it (`2024-03-05`, `20240305`) or with the slashes
#: Highwire's tags use (`2024/03/05`), at the start of the value and not run
#: into more digits.  `05/03/2024` is May or March depending on who reads it,
#: and `March 5, 2024` needs a parser that guesses at languages; neither is
#: read.  What follows the date (a time, a zone) is ignored, so the date is
#: the one the page wrote, in its own offset.
_DECLARED_DATE = re.compile(r"(\d{4})([-/]?)(\d{2})\2(\d{2})(?!\d)")

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
    """What the page says about itself: title, author and sitename as
    Trafilatura reads them, and the date the page declares (`declared_date`).

    Empty strings rather than nulls, matching the `page_metadata` record the
    DOM side already publishes: a consumer reads one shape for both.
    """

    title: str = ""
    author: str = ""
    #: `YYYY-MM-DD`, or empty when the page declares no date that
    #: `declared_date` reads. Never one found in the text, the URL or a
    #: heading (docs/09 P82).
    date: str = ""
    sitename: str = ""


def read_page(html: str, *, url: str | None = None) -> PageReading:
    """Both readings of one page, in one blocking call."""
    return PageReading(
        metadata=read_metadata(html, url=url), article=main_content_text(html)
    )


def read_metadata(html: str, *, url: str | None = None) -> DiscoveredMetadata:
    """Read the page's metadata, with only a declared date."""
    stated = declared_date(html)
    try:
        # The date Trafilatura returns is discarded (docs/09 P82), so the
        # fast search is the cheaper one to throw away.
        document = extract_metadata(html, default_url=url, extensive=False)
    except Exception as exc:  # a capture must never fail over its metadata
        LOGGER.debug("Metadata extraction failed: %s", exc)
        return DiscoveredMetadata(date=stated)
    if document is None:
        return DiscoveredMetadata(date=stated)
    return DiscoveredMetadata(
        title=document.title or "",
        author=document.author or "",
        date=stated,
        sitename=document.sitename or "",
    )


def declared_date(html: str) -> str:
    """The date the page declares for itself, as `YYYY-MM-DD`, or "".

    Read from four kinds of declaration and nothing else:

    - a `<meta>` whose key `_META_DATE_KEYS` names;
    - schema.org's `datePublished`, `dateCreated` or `dateModified`, in a
      `<script type="application/ld+json">` block (from its outermost objects
      that state one) or as a microdata `itemprop` with a `content` or
      `datetime` value;
    - a `<time>` marked `pubdate`, read as a publication;
    - any other `<time>`, from its `datetime`, or its text when it has none.

    A publication or creation beats a modification, and both beat a plain
    `<time>`; among equals the first in the document wins.  A value that is not
    a date `_DECLARED_DATE` reads is passed over, not guessed at.  A year or
    a month alone (`2019`, `2019-03`) is not a date: recording it as the 1st
    would invent a day, which is what htmldate does with a copyright year.
    """
    reader = _DateDeclarations()
    try:
        reader.feed(html)
        reader.close()
    except Exception as exc:  # a capture must never fail over its metadata
        LOGGER.debug("Date declarations could not be read: %s", exc)
        return ""
    best: tuple[int, str] | None = None
    for tier, value in reader.declarations:
        stated = _calendar_date(value)
        if stated and (best is None or tier < best[0]):
            best = (tier, stated)
    return best[1] if best else ""


def _calendar_date(value: str) -> str:
    match = _DECLARED_DATE.match(value.strip())
    if match is None:
        return ""
    year, _, month, day = match.groups()
    try:
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError:  # 2024-02-30, or the 0000-00-00 some CMSs write for none
        return ""


class _DateDeclarations(HTMLParser):
    """Every date declaration in a page, in document order, with its tier.

    The standard library's parser rather than Trafilatura's tree, so that what
    counts as a declaration is this module's rule and no upstream's: the point
    is that an htmldate release cannot change it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.declarations: list[tuple[int, str]] = []
        #: The open `<time>` whose text is its value, and that text so far.
        self._time: tuple[int, list[str]] | None = None
        #: The open JSON-LD block's text so far.
        self._json_ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        itemprop = _ITEMPROP_DATES.get(attributes.get("itemprop", "").lower())
        if tag == "meta":
            keys = (
                _meta_key(attributes.get(name))
                for name in ("property", "name", "itemprop", "http-equiv")
            )
            tiers = [_META_DATE_KEYS[key] for key in keys if key in _META_DATE_KEYS]
            if tiers and "content" in attributes:
                self.declarations.append((min(tiers), attributes["content"]))
        elif tag == "time":
            tier = _MARKED
            if itemprop is not None:
                tier = itemprop
            elif "pubdate" in attributes:
                tier = _ORIGINAL
            if "datetime" in attributes:
                self.declarations.append((tier, attributes["datetime"]))
            elif self._time is None:
                self._time = (tier, [])
        elif itemprop is not None:
            value = attributes.get("content", attributes.get("datetime"))
            if value is not None:
                self.declarations.append((itemprop, value))
        elif tag == "script" and _is_json_ld(attributes.get("type", "")):
            self._json_ld = []

    def handle_data(self, data: str) -> None:
        if self._time is not None:
            self._time[1].append(data)
        if self._json_ld is not None:
            self._json_ld.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "time":
            self._end_time()
        elif tag == "script" and self._json_ld is not None:
            self.declarations.extend(_json_ld_dates("".join(self._json_ld)))
            self._json_ld = None

    def close(self) -> None:
        super().close()
        self._end_time()  # a <time> the page never closed still said a date

    def _end_time(self) -> None:
        if self._time is not None:
            tier, parts = self._time
            self.declarations.append((tier, "".join(parts)))
            self._time = None


def _meta_key(key: str | None) -> str:
    key = (key or "").strip().lower()
    for prefix in ("dc:", "dcterms:"):
        if key.startswith(prefix):
            return prefix[:-1] + "." + key[len(prefix) :]
    return key


def _is_json_ld(script_type: str) -> bool:
    return script_type.split(";")[0].strip().lower() == "application/ld+json"


def _json_ld_dates(text: str) -> list[tuple[int, str]]:
    """The schema.org dates of the outermost objects in one JSON-LD block that
    state any.

    Read level by level, and no deeper than the first level with a date, so
    the dates of a comment or a review nested in an article never count when
    the article states one of its own, whichever kind each is and wherever
    the page put the keys.  The items of an array, `@graph`'s among them, sit
    at the level of the array.  A block that is not JSON is not read: guessing
    at its structure is the kind of reading this module exists to refuse.
    """
    try:
        data = json.loads(text, strict=False)
    except (ValueError, RecursionError):
        return []
    level = _objects([data])
    while level:
        found: list[tuple[int, str]] = []
        deeper: list[object] = []
        for node in level:
            for key, value in node.items():
                tier = _SCHEMA_ORG_DATES.get(key)
                if tier is not None and isinstance(value, str):
                    found.append((tier, value))
                elif isinstance(value, (dict, list)):
                    deeper.append(value)
        if found:
            return found
        level = _objects(deeper)
    return []


def _objects(values: list[object]) -> list[dict[str, object]]:
    """The objects among `values`, with every array opened in place."""
    objects: list[dict[str, object]] = []
    pending = list(reversed(values))
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            objects.append(value)
        elif isinstance(value, list):
            pending.extend(reversed(value))
    return objects


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
