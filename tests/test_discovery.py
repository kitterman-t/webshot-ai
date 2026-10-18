"""Main-content discovery and metadata enrichment (docs/05 task 3.4).

Two things are worth pinning. The metadata must not *invent* provenance —
Trafilatura's date comes from htmldate, which guesses one from the text, the
URL or a heading when the page declares none, and a capture tool that records
a guessed date is lying about the record (docs/09 P3-3, P82). Its author can
come from a byline's text and its site name from the URL's host, and its
title from a heading or one side of the `<title>`, so those are read from
the page's declarations too (P88, P93). And the strategy
that chose the content region has to be the one the manifest names, in all
four of its outcomes, because that is the only way a reader can tell an
isolated region from a whole page.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from pathlib import Path

import pytest
import trafilatura
import trafilatura.metadata
from playwright.async_api import async_playwright

from webshot.acquire.adapters import materialize_source
from webshot.capture import discover
from webshot.capture.discover import (
    DiscoveredMetadata,
    declared_author,
    declared_date,
    declared_sitename,
    declared_title,
    main_content_text,
    read_metadata,
    read_page,
)
from webshot.capture.prepare import (
    ContentSelection,
    locate_main_content,
    select_main_content,
)
from webshot.capture.snapshot import LOCATOR_JS
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTICLE = REPO_ROOT / "tests" / "fixtures" / "professional_page.html"
DATES = REPO_ROOT / "tests" / "fixtures" / "declared_dates"
AUTHORS = REPO_ROOT / "tests" / "fixtures" / "declared_authors"
SITENAMES = REPO_ROOT / "tests" / "fixtures" / "declared_sitenames"
TITLES = REPO_ROOT / "tests" / "fixtures" / "declared_titles"
NOTES = REPO_ROOT / "tests" / "fixtures" / "sample_notes.txt"

DECLARED = """<!doctype html><html><head>
<meta property="article:published_time" content="2024-03-05T10:00:00Z">
<meta name="author" content="R. Chen">
<title>Field Notes</title></head>
<body><article><h1>Field Notes</h1><p>%s</p></article></body></html>
""" % ("A paragraph with enough substance to look like a document. " * 12)

UNDECLARED = DECLARED.replace(
    '<meta property="article:published_time" content="2024-03-05T10:00:00Z">', ""
)


# --------------------------------------------------------------------------- #
# Metadata: what the page declares, and nothing else
# --------------------------------------------------------------------------- #


def test_declared_metadata_is_read() -> None:
    """The author as the page writes it (Trafilatura made it "R Chen"), and
    no site name: the page declares none, and the URL's host is not one
    (docs/09 P88)."""
    metadata = read_metadata(DECLARED)
    assert metadata.title == "Field Notes"
    assert metadata.date == "2024-03-05"
    assert metadata.author == "R. Chen"
    assert metadata.sitename == ""


def test_an_undeclared_date_is_not_invented() -> None:
    """Trafilatura's default `extensive` pass guesses one (P3-3)."""
    assert read_metadata(UNDECLARED).date == ""


def test_the_address_is_never_read() -> None:
    """htmldate read a date from the URL's path ahead of the page's own
    `<meta>`, answering `meta_date_and_body_date.html` at `/2015/04/03/notice`
    with 2015-04-03 (P82), and Trafilatura gave a page that names no site the
    URL's host (P88). Nothing the readers record can come from an address
    they are never given."""
    for reader in (read_page, read_metadata):
        assert list(inspect.signature(reader).parameters) == ["html"]


def _dated(name: str) -> str:
    html = (DATES / name).read_text(encoding="utf-8")
    return read_metadata(html).date


def test_a_bare_date_in_the_text_is_not_a_declared_date() -> None:
    """htmldate 1.11 returns the first `YYYY-MM-DD` in the text in fast mode
    when nothing else matches (adbar/htmldate#193): this page got 2019-03-01
    there, and None from 1.10 (docs/09 P82)."""
    assert _dated("bare_date_in_paragraph.html") == ""


def test_the_text_notes_golden_input_has_no_declared_date() -> None:
    """The case that failed webshot-ai#30: the page WebShot builds from
    `sample_notes.txt`, whose text says "Service restored: 2026-07-16 14:32
    UTC", recorded that date under htmldate 1.11."""
    materialized = materialize_source(NOTES.as_uri())
    try:
        page = Path(file_url_path(materialized.browser_url)).read_text("utf-8")
    finally:
        materialized.cleanup()
    assert "2026-07-16" in page, "the page must still hold the date it tests"
    assert read_metadata(page).date == ""


def test_a_declared_date_beats_a_different_date_in_the_text() -> None:
    assert _dated("meta_date_and_body_date.html") == "2024-03-05"


def test_json_ld_declares_the_outermost_objects_date() -> None:
    """The article's own `datePublished`: not the comment's nested inside it,
    which comes first in the text, and not its `dateModified`."""
    assert _dated("json_ld_date.html") == "2021-06-07"


def test_a_time_element_declares_its_datetime() -> None:
    assert _dated("time_datetime.html") == "2022-11-30"


def test_a_heading_date_does_not_hide_a_declared_time() -> None:
    """htmldate reads the heading first and answers 2018-12-31, on 1.10 and
    1.11 alike; the page declares 2022-11-30."""
    assert _dated("time_behind_heading_date.html") == "2022-11-30"


def test_dates_htmldate_reads_from_undeclared_cues_are_not_recorded() -> None:
    """A heading, an element classed `post-date` and a copyright year:
    htmldate's fast search reads a date from each, and answers this page with
    the year made 2017-01-01, or at `/2015/04/03/audit` with the URL's
    2015-04-03 (P82)."""
    assert _dated("undeclared_cues.html") == ""


def test_a_publication_beats_a_modification_that_comes_first() -> None:
    assert _dated("published_over_modified.html") == "2023-01-15"


def test_a_declared_date_does_not_depend_on_the_calendar() -> None:
    """htmldate refuses a date later than today, so its answer for this page
    would change on 2091-01-02. The page declares the date now."""
    assert _dated("future_declared_date.html") == "2091-01-02"


def test_a_value_that_needs_a_guess_is_not_read() -> None:
    """A year alone, `05/03/2024`, `0000-00-00`, and a date in words."""
    assert _dated("unreadable_declarations.html") == ""


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<meta name="citation_publication_date" content="2004/05/31">', "2004-05-31"),
        ('<meta name="DC.date.issued" content="20190302">', "2019-03-02"),
        ('<meta property="dcterms:issued" content="2011-11-11">', "2011-11-11"),
        # The date the page wrote, in its own offset: no zone conversion.
        (
            '<meta property="article:published_time"'
            ' content="2024-03-05T23:30:00-05:00">',
            "2024-03-05",
        ),
        ("<p><time>2018-08-08</time></p>", "2018-08-08"),
        (
            '<time datetime="2017-07-07">a</time>'
            '<time itemprop="datePublished" datetime="2016-06-06">b</time>',
            "2016-06-06",
        ),
        (
            '<time datetime="2017-07-07">a</time>'
            '<time pubdate datetime="2016-06-06">b</time>',
            "2016-06-06",
        ),
        ('<span itemprop="dateCreated" content="2015-05-15">May</span>', "2015-05-15"),
        # Not a key the table names, so not read, however date-like.
        ('<meta name="expires" content="2030-01-01">', ""),
        ('<meta name="pdate" content="20190301">', ""),
        # A nested comment's publication does not outrank the article's own
        # modification: only the outermost objects that state a date count.
        (
            '<script type="application/ld+json">{"@type": "Article",'
            ' "comment": [{"datePublished": "2024-06-09"}],'
            ' "dateModified": "2024-06-01"}</script>',
            "2024-06-01",
        ),
        (
            '<script type="application/ld+json">[{"@type": "WebSite"},'
            ' {"@graph": [{"@type": "Person"},'
            ' {"@type": "WebPage", "datePublished": "2020-02-02"}]}]</script>',
            "2020-02-02",
        ),
        # Not JSON: its structure would have to be guessed at.
        (
            '<script type="application/ld+json">'
            '{"datePublished": "2019-05-05",}</script>',
            "",
        ),
        # A month is not a day, and February has no 30th.
        ('<time datetime="2019-03">March</time>', ""),
        ('<meta name="date" content="2024-02-30">', ""),
        ("", ""),
    ],
)
def test_what_counts_as_a_declared_date(markup: str, expected: str) -> None:
    assert declared_date(markup) == expected


def _authored(name: str) -> str:
    html = (AUTHORS / name).read_text(encoding="utf-8")
    return read_metadata(html).author


def _sited(name: str) -> str:
    html = (SITENAMES / name).read_text(encoding="utf-8")
    return read_metadata(html).sitename


def test_a_byline_is_not_a_declared_author() -> None:
    """Trafilatura reads "Jane Doe" from the element classed `byline`
    (docs/09 P88)."""
    assert _authored("byline_only.html") == ""


def test_a_declared_author_beats_a_different_byline() -> None:
    """As written: Trafilatura replaced the full stop with a space."""
    assert _authored("meta_author_beside_byline.html") == "R. Chen"


def test_json_ld_declares_every_author_of_the_outermost_object() -> None:
    """A Person, a name and an Organization, in order and as written but for
    the doubled space. Not the comment's author one level down, and not the
    `citation_author`, a less preferred kind naming the first author again.
    Trafilatura dropped the Organization."""
    assert _authored("json_ld_authors.html") == (
        "Priya Raman; Tomas Okafor; Harbor Lane Survey Group"
    )


def test_a_microdata_author_item_declares_its_name() -> None:
    """Trafilatura took the text of the whole item, "Facilities Morgan Lee",
    job title and all."""
    assert _authored("microdata_author.html") == "Morgan Lee"


def test_authors_of_listed_posts_are_not_the_pages() -> None:
    """`rel="author"` links, an account in `twitter:creator`, and a profile
    URL in `article:author`: Trafilatura answered with the first listed
    post's author."""
    assert _authored("listed_posts_authors.html") == ""


def test_a_page_without_a_site_name_does_not_get_the_host() -> None:
    """Trafilatura answered "harbor-lane.example", the URL's host, at
    `https://harbor-lane.example/visiting`."""
    assert _sited("no_site_name.html") == ""


def test_open_graph_names_the_site() -> None:
    """Over `application-name` and a JSON-LD WebSite, which Trafilatura
    preferred for being longer."""
    assert _sited("og_site_name.html") == "Harbor Lane Archive"


def test_a_json_ld_website_names_the_site_over_its_publisher() -> None:
    """Trafilatura took the organization's longer name."""
    assert _sited("json_ld_graph.html") == "Harbor Lane Archive"


def test_the_outermost_json_ld_publisher_names_the_site() -> None:
    assert _sited("json_ld_publisher.html") == "Harbor Lane Gazette"


def _json_ld(data: str) -> str:
    return f'<script type="application/ld+json">{data}</script>'


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<meta name="author" content="Jane Doe">', "Jane Doe"),
        # A single-word author is still declared; Trafilatura dropped it.
        ('<meta name="author" content="normesta">', "normesta"),
        # As written: not split at a comma, and white space made one space.
        (
            '<meta name="author" content=" Jane  Doe,\n John Roe ">',
            "Jane Doe, John Roe",
        ),
        (
            '<meta name="author" content="Jane Doe">'
            '<meta name="author" content="John Roe">'
            '<meta name="author" content="Jane Doe">',
            "Jane Doe; John Roe",
        ),
        (
            '<meta name="citation_author" content="Doe, Jane">'
            '<meta name="citation_author" content="Roe, John">',
            "Doe, Jane; Roe, John",
        ),
        ('<meta property="article:author" content="Jane Doe">', "Jane Doe"),
        ('<meta name="article:author" content="Jane Doe">', "Jane Doe"),
        ('<meta name="DC.creator" content="Doe, Jane">', "Doe, Jane"),
        ('<meta name="dc:creator" content="Doe, Jane">', "Doe, Jane"),
        ('<meta name="DCTERMS.creator" content="Doe, Jane">', "Doe, Jane"),
        # The first kind present is the author, and the others are not added.
        (
            '<meta name="DC.creator" content="Doe, J.">'
            '<meta name="author" content="Jane Doe">',
            "Jane Doe",
        ),
        (
            '<meta name="author" content="J. Doe">'
            + _json_ld('{"@type": "Article", "author": "Jane Doe"}'),
            "Jane Doe",
        ),
        # An empty value does not make its kind present.
        (
            '<meta name="author" content="">'
            '<meta name="citation_author" content="Roe, John">',
            "Roe, John",
        ),
        # An address, an account, a link and a class are not names.
        ('<meta property="article:author" content="https://example.org/jane">', ""),
        ('<meta name="twitter:creator" content="@janedoe">', ""),
        ('<a rel="author" href="/people/jane">Jane Doe</a>', ""),
        ('<link rel="author" href="humans.txt">', ""),
        ('<span class="author">Jane Doe</span>', ""),
        # JSON-LD: a name, a Person, a list, in a graph.
        (_json_ld('{"@type": "Article", "author": "Jane Doe"}'), "Jane Doe"),
        (
            _json_ld('{"author": {"@type": "Person", "name": "Jane Doe"}}'),
            "Jane Doe",
        ),
        (
            _json_ld('{"author": [{"name": "Jane Doe"}, {"@id": "#roe"}, "John Roe"]}'),
            "Jane Doe; John Roe",
        ),
        (
            _json_ld(
                '{"@graph": [{"@type": "WebSite"},'
                ' {"@type": "Article", "author": {"name": "Jane Doe"}}]}'
            ),
            "Jane Doe",
        ),
        (_json_ld('{"author": "https://example.org/jane"}'), ""),
        # A reference states an author it does not name: the comment's
        # author below it is not the page's, and the next kind is read.
        (
            _json_ld(
                '{"@type": "Article", "author": {"@id": "#jane"},'
                ' "comment": [{"author": "Sam Ellery"}]}'
            ),
            "",
        ),
        (
            _json_ld('{"author": {"@id": "#jane"}}')
            + '<meta name="author" content="Jane Doe">',
            "Jane Doe",
        ),
        # Not JSON: its structure would have to be guessed at.
        (_json_ld('{"author": "Jane Doe",}'), ""),
        # Microdata: text, content, an item's name.
        ('<span itemprop="author">Jane Doe</span>', "Jane Doe"),
        ('<meta itemprop="author" content="Jane Doe">', "Jane Doe"),
        (
            '<div itemprop="author" itemscope>'
            '<meta itemprop="name" content="Jane Doe"></div>',
            "Jane Doe",
        ),
        # The name of an item inside the author is that item's.
        (
            '<div itemprop="author" itemscope>'
            '<div itemprop="worksFor" itemscope><span itemprop="name">Harbor Lane'
            '</span></div><span itemprop="name">Jane Doe</span></div>',
            "Jane Doe",
        ),
        (
            '<span itemprop="author">Jane Doe</span> and '
            '<span itemprop="author">John Roe</span>',
            "Jane Doe; John Roe",
        ),
        # A link's microdata value is its URL, and an author's box with a
        # biography in it is not a name.
        ('<a itemprop="author" href="/people/jane">Jane Doe</a>', ""),
        (
            '<div itemprop="author"><img src="jane.png" alt="">'
            "<p>Jane Doe writes about archives.</p></div>",
            "",
        ),
        # A paragraph left open runs into the next one: not a name.
        ('<p itemprop="author">Jane Doe<p>The next paragraph.', ""),
        # An item's author, not its comment's, whichever comes first.
        (
            '<article itemscope><div itemprop="comment" itemscope>'
            '<span itemprop="author">Sam Ellery</span></div>'
            '<span itemprop="author">Jane Doe</span></article>',
            "Jane Doe",
        ),
        ("", ""),
    ],
)
def test_what_counts_as_a_declared_author(markup: str, expected: str) -> None:
    assert declared_author(markup) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<meta property="og:site_name" content="Harbor Lane">', "Harbor Lane"),
        ('<meta name="og:site_name" content="Harbor Lane">', "Harbor Lane"),
        (
            '<meta property="og:site_name" content="Harbor Lane">'
            '<meta property="og:site_name" content="Quay Weekly">',
            "Harbor Lane",
        ),
        ('<meta name="application-name" content="Harbor Reader">', "Harbor Reader"),
        (
            '<meta property="og:site_name" content=" ">'
            '<meta name="application-name" content="Harbor Reader">',
            "Harbor Reader",
        ),
        # A label, an account and a part of the title are not declared names.
        ('<meta name="apple-mobile-web-app-title" content="Harbor">', ""),
        ('<meta name="twitter:site" content="@harborlane">', ""),
        ("<title>Visiting Hours | harbor-lane.example</title>", ""),
        (_json_ld('{"@type": ["WebSite"], "name": "Harbor Lane"}'), "Harbor Lane"),
        (_json_ld('{"@type": "schema:WebSite", "name": "Harbor Lane"}'), "Harbor Lane"),
        (
            _json_ld(
                '{"@type": "WebPage", "isPartOf":'
                ' {"@type": "WebSite", "name": "Harbor Lane"}}'
            ),
            "Harbor Lane",
        ),
        (_json_ld('{"@type": "Article", "publisher": "Harbor Lane"}'), "Harbor Lane"),
        (_json_ld('{"publisher": "https://harbor-lane.example/"}'), ""),
        (
            _json_ld('{"@type": "Article", "publisher": {"name": "Harbor Lane Trust"}}')
            + _json_ld('{"@type": "WebSite", "name": "Harbor Lane"}'),
            "Harbor Lane",
        ),
        ("", ""),
    ],
)
def test_what_counts_as_a_declared_sitename(markup: str, expected: str) -> None:
    assert declared_sitename(markup) == expected


def _titled(name: str) -> str:
    html = (TITLES / name).read_text(encoding="utf-8")
    return read_metadata(html).title


def test_a_heading_is_not_a_declared_title() -> None:
    """No `<title>` and no title declaration: Trafilatura gave the text of
    the page's one `<h1>`, "Reading Room Rules" (docs/09 P93)."""
    assert _titled("heading_only.html") == ""


def test_a_title_element_is_kept_whole() -> None:
    """Trafilatura gave "Visiting Hours", one side of the separator. The
    site's name stays, though the page declares it: which side of which
    separator is the title would be a guess."""
    assert _titled("title_with_site_name.html") == (
        "Visiting Hours | Harbor Lane Archive"
    )


def test_open_graph_declares_the_title() -> None:
    """Over the card's title and the `<title>`, and read though the page
    writes it with `name=`: Trafilatura reads `og:title` only as a property,
    and gave the card's "Tide Tables for the Harbor, 1890 to 1940"."""
    assert _titled("og_title.html") == "Tide Tables"


def test_json_ld_declares_the_title_at_the_top_of_a_block() -> None:
    """The NewsArticle at the top of a graph, and not the headline of a post
    nested in the blog's block before it. Not the `<h1>` either."""
    assert _titled("json_ld_title.html") == "Tide Tables Return to the Reading Room"


def test_citation_title_declares_the_title() -> None:
    """Over `DC.title` and the `<title>`, which adds the series."""
    assert _titled("citation_title.html") == "Notes on the Harbor Lane Tide Gauges"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<meta property="og:title" content="Field Notes">', "Field Notes"),
        ('<meta name="og:title" content="Field Notes">', "Field Notes"),
        ('<meta name="twitter:title" content="Field Notes">', "Field Notes"),
        ('<meta property="twitter:title" content="Field Notes">', "Field Notes"),
        ('<meta name="citation_title" content="Field Notes">', "Field Notes"),
        ('<meta name="DC.title" content="Field Notes">', "Field Notes"),
        ('<meta name="dc:title" content="Field Notes">', "Field Notes"),
        ('<meta name="DCTERMS.title" content="Field Notes">', "Field Notes"),
        # The first kind present is the title, wherever it sits.
        (
            '<meta name="twitter:title" content="Field Notes, Volume 2">'
            '<meta property="og:title" content="Field Notes">',
            "Field Notes",
        ),
        (
            _json_ld('{"@type": "Article", "headline": "Field Notes, Volume 2"}')
            + '<meta name="twitter:title" content="Field Notes">',
            "Field Notes",
        ),
        (
            '<meta name="citation_title" content="Field Notes, Volume 2">'
            + _json_ld('{"@type": "Article", "headline": "Field Notes"}'),
            "Field Notes",
        ),
        (
            '<meta name="DC.title" content="Field notes">'
            '<meta name="citation_title" content="Field Notes">',
            "Field Notes",
        ),
        (
            "<title>Field Notes | Harbor Lane</title>"
            '<meta name="DC.title" content="Field Notes">',
            "Field Notes",
        ),
        # An empty value does not make its kind present.
        (
            '<meta property="og:title" content=" "><title>Field Notes</title>',
            "Field Notes",
        ),
        # JSON-LD: an Article's or a WebPage's name, else its headline.
        (
            _json_ld(
                '{"@type": "Article", "name": "Field Notes",'
                ' "headline": "Notes kept on the harbor walls"}'
            ),
            "Field Notes",
        ),
        (
            _json_ld('{"@type": "NewsArticle", "headline": "Field Notes"}'),
            "Field Notes",
        ),
        (
            _json_ld(
                '{"@type": "BlogPosting", "name": " ", "headline": "Field Notes"}'
            ),
            "Field Notes",
        ),
        (_json_ld('{"@type": ["WebPage"], "name": "Field Notes"}'), "Field Notes"),
        (_json_ld('{"@type": "schema:FAQPage", "name": "Field Notes"}'), "Field Notes"),
        (
            _json_ld(
                '[{"@type": "WebSite", "name": "Harbor Lane"},'
                ' {"@type": "Article", "headline": "Field Notes"}]'
            ),
            "Field Notes",
        ),
        (
            _json_ld('{"@graph": {"@type": "Article", "headline": "Field Notes"}}'),
            "Field Notes",
        ),
        # A site's, an organization's or a product's name is not the page's
        # title, and an article nested one level down is not the page's.
        (_json_ld('{"@type": "WebSite", "name": "Harbor Lane"}'), ""),
        (_json_ld('{"@type": "Product", "name": "Tide Clock"}'), ""),
        (
            _json_ld(
                '{"@type": "WebPage", "mainEntity":'
                ' {"@type": "Article", "headline": "Field Notes, Volume 2"}}'
            )
            + "<title>Field Notes</title>",
            "Field Notes",
        ),
        (
            _json_ld(
                '{"@type": "ItemList", "itemListElement":'
                ' [{"@type": "ListItem", "item":'
                ' {"@type": "Article", "headline": "Another Page"}}]}'
            ),
            "",
        ),
        # Not JSON: its structure would have to be guessed at.
        (_json_ld('{"@type": "Article", "headline": "Field Notes",}'), ""),
        # Not read: HTML has no `title` meta, the vendors' keys, microdata
        # and headings.
        ('<meta name="title" content="Field Notes">', ""),
        ('<meta name="parsely-title" content="Field Notes">', ""),
        ('<meta itemprop="headline" content="Field Notes">', ""),
        ("<h1>Field Notes</h1>", ""),
        ('<h1 class="entry-title">Field Notes</h1><h1>Index</h1>', ""),
        # The <title> as written, its white space made one space, not split.
        ("<title> Field\n  Notes | Harbor  Lane </title>", "Field Notes | Harbor Lane"),
        (
            "<title>Field &amp; Notes \u2014 Volume 2</title>",
            "Field & Notes \u2014 Volume 2",
        ),
        (
            "<title>https://harbor-lane.example/notes</title>",
            "https://harbor-lane.example/notes",
        ),
        # The first <title> is the page's, even an empty one; an SVG's or
        # MathML's is the drawing's or the formula's.
        ("<title></title><title>Field Notes</title>", ""),
        (
            "<head><title>Field Notes</title></head>"
            "<body><svg><title>Close</title></svg></body>",
            "Field Notes",
        ),
        ("<body><svg><g><title>Close</title></g></svg><p>x</p></body>", ""),
        ("<body><math><title>x squared</title></math></body>", ""),
        (
            "<body><svg><title>Close</title></svg></body><title>Field Notes</title>",
            "Field Notes",
        ),
        # A <title> the page never closed still said its title.
        ("<title>Field Notes", "Field Notes"),
        ("", ""),
    ],
)
def test_what_counts_as_a_declared_title(markup: str, expected: str) -> None:
    assert declared_title(markup) == expected


def test_trafilaturas_metadata_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The title, date, author and site name are all the page's declarations
    (docs/09 P82, P88, P93), so Trafilatura is not asked for its metadata at
    all: whatever it or the installed htmldate would infer cannot reach the
    record, and its failure cannot take a declaration with it."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Trafilatura's metadata was read")

    monkeypatch.setattr(trafilatura, "extract_metadata", _refuse)
    monkeypatch.setattr(trafilatura.metadata, "extract_metadata", _refuse)
    assert not hasattr(discover, "extract_metadata")
    assert read_metadata(DECLARED) == DiscoveredMetadata(
        title="Field Notes", author="R. Chen", date="2024-03-05"
    )
    assert read_metadata(UNDECLARED).date == ""


def test_a_page_that_declares_nothing_yields_empty_strings_not_nulls() -> None:
    metadata = read_metadata("<html><body><p>hi</p></body></html>")
    assert metadata == DiscoveredMetadata(title="", author="", date="", sitename="")


def test_unreadable_input_degrades_instead_of_raising() -> None:
    assert read_metadata("") == DiscoveredMetadata()
    assert main_content_text("") is None


def test_a_page_too_short_to_have_a_main_region_reports_none() -> None:
    assert (
        main_content_text("<html><body><p>Three words here.</p></body></html>") is None
    )


def test_an_article_reports_its_text() -> None:
    text = main_content_text(ARTICLE.read_text(encoding="utf-8"))
    assert text and "Executive summary" in text


def test_one_reading_answers_both_questions() -> None:
    """Both come from parsing the same HTML, so they are read together."""
    reading = read_page(DECLARED)
    assert reading.metadata.title == "Field Notes"
    assert reading.article and "paragraph with enough substance" in reading.article


async def _none() -> None:
    """An awaitable None, for standing in as a locator that finds nothing."""
    return None


# --------------------------------------------------------------------------- #
# Selection: all four strategies, against a real DOM
# --------------------------------------------------------------------------- #


async def _select(
    html_or_path: str, explicit: str | None = None, *, auto: bool = True
) -> ContentSelection:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(html_or_path)
            article = read_page(await page.content()).article
            return await select_main_content(page, explicit, article, auto=auto)
        finally:
            await browser.close()


@pytest.mark.browser
def test_trafilatura_wins_on_an_article_page() -> None:
    selection = asyncio.run(_select(ARTICLE.as_uri()))
    assert selection.strategy == "trafilatura"
    assert selection.selector == "[data-webshot-auto-root='true']"


@pytest.mark.browser
def test_an_explicit_selector_is_never_second_guessed() -> None:
    selection = asyncio.run(_select(ARTICLE.as_uri(), explicit="main"))
    assert selection == ContentSelection("main", "explicit")


@pytest.mark.browser
def test_the_static_selector_list_is_still_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v2 path must survive Trafilatura declining to find an article."""
    from webshot.capture import prepare

    monkeypatch.setattr(prepare, "locate_main_content", lambda _page, _article: _none())
    selection = asyncio.run(_select(ARTICLE.as_uri()))
    assert selection.strategy == "static-selectors"
    assert selection.selector == "[data-webshot-auto-root='true']"


@pytest.mark.browser
def test_a_page_with_no_main_region_falls_through_to_the_full_page() -> None:
    selection = asyncio.run(
        _select("data:text/html,<body><p>Too little to isolate.</p></body>")
    )
    assert selection == ContentSelection(None, "full-page")


@pytest.mark.browser
def test_without_auto_selector_nothing_is_searched_for() -> None:
    """A run that did not ask for discovery still records what it did."""
    assert asyncio.run(_select(ARTICLE.as_uri(), auto=False)) == ContentSelection(
        None, "full-page"
    )
    assert asyncio.run(
        _select(ARTICLE.as_uri(), explicit="main", auto=False)
    ) == ContentSelection("main", "explicit")


# --------------------------------------------------------------------------- #
# Locators: a path that names an element must find that element
# --------------------------------------------------------------------------- #

_LOCATOR_FIXTURE = """<!doctype html><html><head>
<meta property="og:video" content="https://app.guidde.com/share/playbooks/x">
</head><body>
<div id="wrap"><p id="para">text</p><span>a</span><span>b</span><em>e</em></div>
<section><article><b>deep</b></article></section>
</body></html>"""


async def _locators(selectors: Sequence[str]) -> list[tuple[str, str, bool]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_LOCATOR_FIXTURE)
            results = []
            for selector in selectors:
                locator = await page.locator(selector).first.evaluate(LOCATOR_JS)
                same = await page.evaluate(
                    "([wanted, produced]) => {"
                    "  const a = document.querySelector(wanted);"
                    "  const b = document.querySelector(produced);"
                    "  return Boolean(b) && a === b;"
                    "}",
                    [selector, locator],
                )
                results.append((selector, locator, bool(same)))
            return results
        finally:
            await browser.close()


@pytest.mark.browser
def test_every_locator_resolves_to_the_element_it_names() -> None:
    """The whole content of the locator contract, checked as one property.

    Two spellings used to match nothing, both from an unconditional `body > `
    prefix (docs/09 P8-39): `body > html > head > meta` for an `og:video`
    embed, which is what the manifest's video locator pointed at; and
    `body > p#para` for an element shortened at an id it does not directly
    parent. Asserting resolution rather than the string means a third spelling
    cannot appear later and look plausible in a recording.
    """
    selectors = [
        "meta[property='og:video']",
        "#para",
        "#wrap",
        "span:nth-of-type(2)",
        "em",
        "b",
        "body",
    ]
    unresolved = [
        (selector, locator)
        for selector, locator, same in asyncio.run(_locators(selectors))
        if not same
    ]
    assert not unresolved, f"locators that do not name their own element: {unresolved}"


@pytest.mark.browser
def test_a_head_element_is_anchored_from_head_not_body() -> None:
    """The finding itself, spelled out so the reason survives a refactor."""
    ((_, locator, _),) = asyncio.run(_locators(["meta[property='og:video']"]))
    assert locator.startswith("head")
    assert "body" not in locator


#: A page whose vocabulary is small and whose article repeats it. The summary
#: box names every word once; the article says them all forty times over.
_VOCABULARY = (
    "revenue costs margin headcount regions quarters growth risk forecast "
    "pipeline retention churn expansion renewal discount backlog bookings "
    "attrition tenure coverage benchmark variance"
).split()


def _repetitive_page() -> tuple[str, str]:
    """The markup and the article text Trafilatura would report for it.

    Every paragraph is the same vocabulary in a different order — no extra
    words anywhere, so the summary's *distinct* coverage is exactly 1.0 while
    its share of the text is about 1/40th. Adding even "in period N" to the
    paragraphs would give the article 40 words the summary lacks and drop its
    distinct coverage to a third, which is how the first attempt at this
    fixture quietly stopped being able to fail.
    """
    # Twice, so the box clears the 200-character floor the scan applies before
    # it tokenizes anything.
    summary = " ".join(_VOCABULARY * 2) + "."
    paragraphs = [
        " ".join(
            _VOCABULARY[index % len(_VOCABULARY) :]
            + _VOCABULARY[: index % len(_VOCABULARY)]
        )
        + "."
        for index in range(40)
    ]
    article = "\n".join(paragraphs)
    markup = (
        "<!doctype html><html><body><main>"
        f'<div id="summary"><p>{summary}</p></div>'
        '<article id="article">'
        + "".join(f"<p>{text}</p>" for text in paragraphs)
        + "</article></main></body></html>"
    )
    return markup, article


async def _selected_id(markup: str, article: str) -> str | None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(markup)
            selector = await locate_main_content(page, article)
            if selector is None:
                return None
            return await page.locator(selector).first.evaluate(
                "element => element.closest('[id]')?.id || ''"
            )
        finally:
            await browser.close()


@pytest.mark.browser
def test_a_summary_repeating_the_vocabulary_does_not_win() -> None:
    """Distinct-word coverage measures vocabulary, not text.

    The summary box holds 100% of the article's distinct words in about 1/40th
    of its length — so it cleared a 90% *distinct* bar, and the shortest-wins
    rule then chose it over the article. `isolate_content` hid the article's
    own paragraphs and the capture silently lost the page (docs/09 P8-67).

    Deliberately constructed so it *can* fail: with the occurrence check
    removed this test picks `summary`, which is what makes it evidence rather
    than decoration (docs/09 P7-11).
    """
    markup, article = _repetitive_page()
    assert asyncio.run(_selected_id(markup, article)) == "article"


@pytest.mark.browser
def test_the_summary_box_really_does_clear_the_distinct_word_bar() -> None:
    """Otherwise the test above proves nothing: it would be passing because
    the old rule never selected the summary, not because the new one stops it."""
    markup, article = _repetitive_page()

    async def coverage() -> tuple[float, float]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(markup)
                return await page.evaluate(
                    """article => {
                        const words = v => (v || '').toLowerCase()
                            .match(/[\\p{L}\\p{N}]+/gu) || [];
                        const all = words(article);
                        const wanted = new Set(all);
                        const box = document.getElementById('summary');
                        const seen = new Set();
                        let hits = 0;
                        const left = new Map();
                        for (const w of all) left.set(w, (left.get(w) || 0) + 1);
                        for (const w of words(box.innerText)) {
                            if (!wanted.has(w)) continue;
                            seen.add(w);
                            const n = left.get(w) || 0;
                            if (n > 0) { left.set(w, n - 1); hits += 1; }
                        }
                        return [seen.size / wanted.size, hits / all.length];
                    }""",
                    article,
                )
            finally:
                await browser.close()

    distinct, occurrences = asyncio.run(coverage())
    assert distinct >= 0.9, "the summary must clear the old bar for this to bite"
    assert occurrences < 0.9, "and fail the new one"
