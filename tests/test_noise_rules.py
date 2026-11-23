"""Clean mode's page-chrome rules keep the page they are wrong about (docs/09 P132).

Two captures printed an empty or nearly empty page in clean mode and said
nothing that named the cause:

- A theme classed the wrapper around its article and the article's sidebar
  `sidebar`, and the `.sidebar` rule hid the wrapper, the article with it,
  whether `--selector` named the article or no region was chosen (P132-1).
  tests/fixtures/noise_sidebar_wrapper_page.html is that layout.
- A CMS wrote a post's tags as classes on the section holding the post, one
  of them `tag-…-subscriber-only`, and `[class*='subscribe' i]` hid the
  section, its `<h1>` and the article (P132-2).
  tests/fixtures/noise_tag_classes_page.html is that page, with the sign-up
  boxes inside it that the rule is for.

The rules now match a class name's words, pass over a CMS's tag and category
classes, and keep what holds the content region, or a visible `<h1>` and most
of the page's text. An empty capture names what clean mode hid that had text.
A page that declares an article in JSON-LD and whose capture holds neither its
headline nor most of its words says so (P132-4). All text is invented.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from webshot.capture.headings import missing_headline, missing_headline_warning
from webshot.capture.prepare import (
    LAZY_LOAD_STATE_JS,
    NOISE_CLASS_WORDS,
    NOISE_SELECTORS,
    NoiseBox,
    NoiseRemoval,
    hide_noise,
    isolate_content,
    kept_noise_message,
    noise_removal_note,
)
from webshot.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
WRAPPER = FIXTURES / "noise_sidebar_wrapper_page.html"
TAGGED = FIXTURES / "noise_tag_classes_page.html"

PARAGRAPHS = "".join(
    f"<p>Paragraph {n} of the harbor ledger, long enough to read aloud.</p>"
    for n in range(12)
)


def _page(body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>A page</title></head><body>{body}</body></html>"
    )


async def _hide(
    body: str, selector: str | None = None
) -> tuple[NoiseRemoval, list[str]]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(_page(body))
            if selector:
                await isolate_content(page, selector)
            removal = await hide_noise(page, NOISE_SELECTORS, NOISE_CLASS_WORDS)
            hidden: list[str] = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-hidden-by="noise"]')]
                    .map(element => element.id || element.getAttribute('class') || element.localName)"""
            )
            return removal, hidden
        finally:
            await browser.close()


def hide(body: str, selector: str | None = None) -> tuple[NoiseRemoval, list[str]]:
    return asyncio.run(_hide(body, selector))


@pytest.mark.browser
def test_a_class_word_is_matched_whole_and_a_tag_class_never() -> None:
    """`[class*='subscribe' i]` matched `tag-…-subscriber-only`; a word of a
    class name is now matched whole, in kebab, snake, BEM and camel case."""
    _, hidden = hide(
        '<div id="kebab" class="newsletter-signup">a</div>'
        '<div id="bem" class="c-newsletter__form">b</div>'
        '<div id="pascal" class="NewsletterSignup">c</div>'
        '<div id="camel" class="siteSubscribeBox">d</div>'
        '<div id="whole" class="subscribe">e</div>'
        '<div id="subscriber" class="subscriber-note">f</div>'
        '<div id="pascal-subscriber" class="SubscriberOnly">g</div>'
        '<div id="tag" class="post tag-members-subscriber-only tag-newsletter">h</div>'
        '<div id="category" class="category-newsletter">i</div>'
        '<div id="term" class="term-subscribe">j</div>'
        '<div id="inside" class="newsletters-archive">k</div>'
    )
    assert sorted(hidden) == ["bem", "camel", "kebab", "pascal", "whole"]


@pytest.mark.browser
def test_a_wrapper_holding_the_h1_and_most_of_the_text_is_kept() -> None:
    """The `.sidebar` wrapper around the article (P132-1); the site header
    holding the site's name as an `<h1>` is a small part of the page, and is
    still removed."""
    removal, hidden = hide(
        "<header><h1>THE LANTERN REVIEW</h1><nav>Essays Letters</nav></header>"
        '<main><div class="container sidebar">'
        '<div class="sidebar__sidebar"><p>More from the review.</p></div>'
        f'<div class="sidebar__content"><article><h1>Harbor ledgers</h1>{PARAGRAPHS}'
        "</article></div></div></main><footer>Printed by the press.</footer>"
    )
    assert "container sidebar" not in hidden
    assert {"header", "nav", "footer"} <= set(hidden)
    [kept] = removal.kept
    assert (kept.name, kept.rule, kept.why) == ("div.container", ".sidebar", "heading")
    assert kept.characters and kept.total and kept.characters > kept.total / 2
    message = kept_noise_message(removal)
    assert message is not None and "div.container (matched by .sidebar:" in message


@pytest.mark.browser
def test_what_holds_the_selected_region_is_kept() -> None:
    removal, hidden = hide(
        '<div class="sidebar"><p>Short.</p><article id="post"><p>The article.</p>'
        "</article></div>",
        selector="#post",
    )
    assert hidden == []
    [kept] = removal.kept
    assert (kept.name, kept.why) == ("div.sidebar", "root")


@pytest.mark.browser
def test_a_comment_feed_holding_most_of_the_text_is_still_removed() -> None:
    """A feed can outweigh the article; it holds no `<h1>`, so the rule stands."""
    removal, hidden = hide(
        f"<article><h1>Short post</h1><p>Short.</p></article>"
        f'<div class="comments">{PARAGRAPHS}{PARAGRAPHS}</div>'
    )
    assert hidden == ["comments"]
    assert removal.kept == ()


@pytest.mark.browser
def test_an_h1_inside_a_rule_matched_box_inside_the_kept_one_is_kept() -> None:
    """Keeping the box that holds the page keeps the heading's whole chain."""
    _, hidden = hide(
        '<div class="container sidebar"><div class="subscribe-hero">'
        f"<h1>The ferry</h1></div>{PARAGRAPHS}</div>"
    )
    assert hidden == []


@pytest.mark.browser
@pytest.mark.parametrize("selector", [None, "article.post-page"])
def test_the_sidebar_wrapper_page_prints_its_article(
    tmp_path: Path, selector: str | None
) -> None:
    flags = ["--selector", selector] if selector else []
    pdf = tmp_path / "capture.pdf"
    assert main([str(WRAPPER), "--output", str(pdf), "--no-ocr", *flags]) == 0
    bundle = pdf.with_suffix(".ai")
    markdown = (bundle / "content.md").read_text("utf-8")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    for sentinel in ("WRAPPEDARTICLESENTINEL", "WRAPPEDCONCLUSIONSENTINEL"):
        assert sentinel in markdown and sentinel in text, sentinel
    warnings = json.loads((bundle / "manifest.json").read_text("utf-8"))["warnings"]
    assert not [w for w in warnings if "capture is empty" in w]


@pytest.mark.browser
def test_the_tagged_page_prints_its_article_without_its_sign_up_boxes(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "capture.pdf"
    assert main([str(TAGGED), "--output", str(pdf), "--no-ocr"]) == 0
    markdown = (pdf.with_suffix(".ai") / "content.md").read_text("utf-8")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    for kept in (
        "Why the ferry fare doubled",
        "TAGGEDARTICLESENTINEL",
        "SUBSCRIBERNOTESENTINEL",
    ):
        assert kept in markdown and kept in text, kept
    for gone in (
        "NEWSLETTERBOXSENTINEL",
        "SUBSCRIBEBOXSENTINEL",
        "NEWSLETTERFORMSENTINEL",
    ):
        assert gone not in markdown and gone not in text, gone


KEPT_WRAPPER_TITLE = FIXTURES / "noise_kept_wrapper_title_page.html"


@pytest.mark.browser
def test_a_title_beside_the_chosen_region_in_a_kept_wrapper_is_isolated(
    tmp_path: Path,
) -> None:
    """The rule-matched wrapper is kept, so the title in it was not removed as
    chrome: the region WebShot chose left it out, and the warning says so. The
    headings check took the wrapper's selector for chrome, called the title
    removed, and said nothing (review of #391, docs/09 P132-8)."""
    report = _report(tmp_path, KEPT_WRAPPER_TITLE, "--auto-selector")
    discovery = json.loads(
        (tmp_path / "capture.ai" / "manifest.json").read_text("utf-8")
    )["content_discovery"]
    assert discovery["strategy"] in ("trafilatura", "static-selectors"), discovery
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(str(tmp_path / "capture.pdf")).pages
    )
    assert "KEPTWRAPPERARTICLESENTINEL" in text
    [title] = [w for w in report["warnings"] if w.startswith("The page's main heading")]
    assert title.startswith(
        'The page\'s main heading, "Tide tables of the north harbor", is not in the '
        "PDF's text: it lies outside the region WebShot chose as the page's main "
        "content"
    )


def _report(tmp_path: Path, source: Path, *flags: str) -> dict[str, Any]:
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    assert (
        main(
            [
                str(source),
                "--output",
                str(pdf),
                "--no-ocr",
                "--report",
                str(report),
                *flags,
            ]
        )
        == 0
    )
    loaded: dict[str, Any] = json.loads(report.read_text("utf-8"))
    return loaded


@pytest.mark.browser
def test_an_empty_capture_names_what_clean_mode_hid(tmp_path: Path) -> None:
    """A page whose text is all in one box a rule matches, with no `<h1>` to
    say it is the page, prints empty; the warning says what was hidden."""
    source = tmp_path / "chrome_only.html"
    source.write_text(_page(f'<div class="sidebar">{PARAGRAPHS}</div>'), "utf-8")
    report = _report(tmp_path, source)
    [empty] = [w for w in report["warnings"] if "The capture is empty" in w]
    assert "Clean mode hid 1 element as page chrome that showed" in empty
    assert "the most in div.sidebar (" in empty and "matched by .sidebar)" in empty
    assert "--mode faithful keeps it" in empty
    faithful = _report(tmp_path, source, "--mode", "faithful")
    assert not [w for w in faithful["warnings"] if "The capture is empty" in w]


def test_the_note_names_the_largest_and_counts_the_rest() -> None:
    removal = NoiseRemoval(
        hidden=3,
        removed=(
            NoiseBox("div.sidebar", ".sidebar", 1200),
            NoiseBox("nav", "nav", 40),
        ),
    )
    assert noise_removal_note(removal) == (
        "Clean mode hid 2 elements as page chrome that showed 1,240 letters and "
        "digits of text, the most in div.sidebar (1,200, matched by .sidebar): if "
        "that is the page's content, --mode faithful keeps it."
    )
    assert noise_removal_note(NoiseRemoval()) is None


@pytest.mark.browser
def test_the_lazy_load_pass_measures_what_the_rules_match() -> None:
    """Kept means outside what the capture hides, matched alike (docs/09
    P23-8): a class word's box is removed, a rule-matched box holding a
    visible `<h1>` is counted as kept."""
    from playwright.async_api import async_playwright

    async def run() -> dict[str, Any]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(
                    _page(
                        '<div class="sidebar" id="wrap"><h1>Title</h1><p>Text.</p></div>'
                        '<div class="newsletterSignup" id="box"><p>Sign up.</p></div>'
                    )
                )
                state: dict[str, Any] = await page.evaluate(
                    LAZY_LOAD_STATE_JS,
                    {
                        "noise": list(NOISE_SELECTORS),
                        "words": list(NOISE_CLASS_WORDS),
                        "excluded": [],
                    },
                )
                return state
            finally:
                await browser.close()

    state = asyncio.run(run())
    assert set(state["regions"]) == {"clean div#box"}


ARTICLE_TITLES = (("The ferry and the fog: what the logbooks say", "Ferry and fog"),)


def test_the_declared_articles_are_read_from_the_top_of_each_block() -> None:
    """An Article type's `headline` and `name`; a WebPage's name is often the
    site's, and an article one level down is another page's."""
    from webshot.capture.discover import declared_article_titles

    graph = {
        "@graph": [
            {"@type": "BlogPosting", "headline": "The  ferry", "name": "Ferry"},
            {"@type": "WebPage", "name": "Harbor Notes"},
            {
                "@type": "Blog",
                "blogPost": [{"@type": "BlogPosting", "headline": "Other"}],
            },
        ]
    }
    html = (
        '<html><head><script type="application/ld+json">'
        f"{json.dumps(graph)}</script>"
        '<script type="application/ld+json">{"@type": "NewsArticle", "headline": '
        '"The fog"}</script></head><body></body></html>'
    )
    assert declared_article_titles(html) == (("The ferry", "Ferry"), ("The fog",))


def test_a_capture_without_the_declared_headline_is_named() -> None:
    printed = "Harbor letter. Monthly notes on tides, fares and lamps. Name. Email."
    assert missing_headline(ARTICLE_TITLES, printed, printed) == ARTICLE_TITLES[0][0]
    message = missing_headline_warning(ARTICLE_TITLES[0][0], mode="clean")
    assert message.startswith(
        'The page declares an article, "The ferry and the fog: what the logbooks '
        "say\", in its structured data (JSON-LD), and neither the PDF's text nor "
        "the bundle's holds its headline or most of its words"
    )
    assert "--mode faithful keeps it" in message
    assert "--mode faithful" not in missing_headline_warning("A", mode="faithful")


def test_a_printed_headline_that_differs_from_the_declared_one_is_enough() -> None:
    """A site often prints another headline than it declares; the article's
    text holds most of the declared one's longer words all the same."""
    printed = (
        "Fog stops the ferry again\nThe logbooks say the fog came in at dawn, "
        "and the ferry waited for it to lift."
    )
    assert missing_headline(ARTICLE_TITLES, printed, None) is None
    assert missing_headline(ARTICLE_TITLES, "", "the ferry and the fog what") is None
    # Either declared title will do, and a page that declares none is not asked.
    assert (
        missing_headline(
            (("Unrelated words entirely", "Ferry and fog"),), printed, None
        )
        is None
    )
    assert missing_headline((), "", None) is None


@pytest.mark.browser
def test_a_page_served_without_its_declared_article_says_so(tmp_path: Path) -> None:
    """As a think tank's post printed a newsletter popup and exited 0."""
    declared = json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "BlogPosting", "headline": ARTICLE_TITLES[0][0]},
                {"@type": "WebPage", "name": "Harbor Notes"},
            ],
        }
    )
    source = tmp_path / "served_without.html"
    source.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>The ferry and the fog | Harbor Notes</title>"
        f'<script type="application/ld+json">{declared}</script></head><body>'
        '<main id="main"></main><div class="popup-box"><p>Harbor letter</p>'
        "<p>Monthly notes on tides, fares and lamps. Name. Email. Send.</p></div>"
        "</body></html>",
        "utf-8",
    )
    report = _report(tmp_path, source)
    assert [w for w in report["warnings"] if "The page declares an article" in w]
    # Under the caller's own --selector the region is what was asked for.
    chosen = _report(tmp_path, source, "--selector", ".popup-box")
    assert not [w for w in chosen["warnings"] if "The page declares an article" in w]
