"""Main-content discovery and metadata enrichment (docs/05 task 3.4).

Two things are worth pinning. Trafilatura must not *invent* provenance — its
metadata pass will guess a publication date for a page that declares none, and
a capture tool that records a guessed date is lying about the record (docs/09
P3-3). And the strategy that chose the content region has to be the one the
manifest names, in all four of its outcomes, because that is the only way a
reader can tell an isolated region from a whole page.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from webshot.capture.discover import (
    DiscoveredMetadata,
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

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTICLE = REPO_ROOT / "tests" / "fixtures" / "professional_page.html"

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
    """Trafilatura's default `extensive` pass guesses one; WebShot turns it off."""
    assert read_metadata(UNDECLARED, url="https://example.com/notes").date == ""


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
