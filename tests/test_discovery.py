"""Main-content discovery and metadata enrichment (docs/05 task 3.4).

Two things are worth pinning. The metadata must not *invent* provenance —
Trafilatura's date comes from htmldate, which guesses one from the text, the
URL or a heading when the page declares none, and a capture tool that records
a guessed date is lying about the record (docs/09 P3-3, P82). And the strategy
that chose the content region has to be the one the manifest names, in all
four of its outcomes, because that is the only way a reader can tell an
isolated region from a whole page.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from webshot.acquire.adapters import materialize_source
from webshot.capture import discover
from webshot.capture.discover import (
    DiscoveredMetadata,
    declared_date,
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
    metadata = read_metadata(DECLARED, url="https://example.com/notes")
    assert metadata.title == "Field Notes"
    assert metadata.date == "2024-03-05"
    assert "Chen" in metadata.author
    assert metadata.sitename == "example.com"


def test_an_undeclared_date_is_not_invented() -> None:
    """Trafilatura's default `extensive` pass guesses one (P3-3)."""
    assert read_metadata(UNDECLARED, url="https://example.com/notes").date == ""


def _dated(name: str, url: str = "https://example.com/notes") -> str:
    html = (DATES / name).read_text(encoding="utf-8")
    return read_metadata(html, url=url).date


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
    assert read_metadata(page, url=materialized.browser_url).date == ""


def test_a_declared_date_beats_a_different_date_in_the_text() -> None:
    assert _dated("meta_date_and_body_date.html") == "2024-03-05"


def test_a_declared_date_beats_a_date_in_the_url() -> None:
    """htmldate reads the URL's path before any `<meta>`, and answers this
    page with 2015-04-03 on 1.10 and 1.11 alike."""
    url = "https://example.com/2015/04/03/notice"
    assert _dated("meta_date_and_body_date.html", url) == "2024-03-05"


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


@pytest.mark.parametrize(
    "url", ["https://example.com/2015/04/03/audit", "https://example.com/audit"]
)
def test_dates_htmldate_reads_from_undeclared_cues_are_not_recorded(url: str) -> None:
    """A URL path, a heading, an element classed `post-date` and a copyright
    year: htmldate's fast search reads a date from each, and answers this page
    with the URL's 2015-04-03, or without it the year made 2017-01-01 (P82)."""
    assert _dated("undeclared_cues.html", url) == ""


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


def test_trafilaturas_date_is_never_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever date the installed htmldate finds, it is not the one written.

    The property behind every case above, held without depending on which
    htmldate is installed: a stand-in for Trafilatura that answers a date the
    page does not declare changes nothing.
    """

    class _Reading:
        title = "Field Notes"
        author = None
        date = "1999-09-09"
        sitename = None

    monkeypatch.setattr(discover, "extract_metadata", lambda *_a, **_k: _Reading())
    assert read_metadata(UNDECLARED).date == ""
    assert read_metadata(DECLARED).date == "2024-03-05"
    assert read_metadata(DECLARED).title == "Field Notes"


def test_a_declared_date_survives_trafilatura_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The date never came from Trafilatura, so its failure cannot take it."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(discover, "extract_metadata", _raise)
    assert read_metadata(DECLARED) == DiscoveredMetadata(date="2024-03-05")


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
    reading = read_page(DECLARED, url="https://example.com/notes")
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
