"""MathML reaches the snapshot as text (docs/09 P14-36).

The sanitizer's HTML allowlist drops a foreign element with everything inside
it, so before this a formula Chromium drew in the PDF was absent from
content.html and from every text surface built on it -- a statistics reading's
standard-deviation formula read "The sample standard deviation is:" followed by
nothing, and no warning said so.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from webshot.capture.snapshot import extract_semantic_content

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "math_formulas.html"


def snapshot_html() -> str:
    async def run() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                extracted = await extract_semantic_content(page)
                return str(extracted["html"])
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
def test_every_visible_formula_survives_as_text_marked_with_its_source() -> None:
    html = snapshot_html()
    held = re.findall(r'<(div|span) data-webshot-math="([a-z]+)">([^<]*)</\1>', html)
    assert held == [
        # The TeX annotation is the author's own source: it wins, and a
        # display formula stays a block of its own.
        ("div", "tex", r"s = \sqrt{\frac{\Sigma (x - \overline{x})^2}{n-1}}"),
        # No annotation: the alttext the page wrote for it.
        ("span", "alttext", "(8 + 10) ÷ 2 = 9"),
        # Neither: the formula's own linear text, rather than nothing.
        ("span", "text", "n\u22121"),  # MathML's minus sign
        # Linear text is what was drawn: the phantom and the hidden term are
        # laid out or present in the DOM, and neither is on the page.
        ("span", "text", "a+b"),
    ]
    # Inline math stays inside its sentence.
    assert re.search(r"The median is <span[^>]*>[^<]*</span> for this list", html)


@pytest.mark.browser
def test_a_formula_no_reader_could_see_stays_out_and_no_mathml_is_left() -> None:
    html = snapshot_html()
    assert "secret" not in html
    assert "<math" not in html and "<annotation" not in html


@pytest.mark.browser
def test_a_formulas_linear_text_leaves_out_what_it_does_not_draw() -> None:
    """`textContent` reads every descendant, drawn or not. With no TeX and no
    alttext to prefer, an <mphantom> (spacing, never painted) and a
    `display: none` term were read into content.html as part of the formula
    (docs/09 P18-4)."""
    html = snapshot_html()
    assert "phantomterm" not in html
    assert "undrawnterm" not in html
