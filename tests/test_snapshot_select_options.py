"""A select the page shows reaches the snapshot with what it drew (docs/09 P69).

`VISIBLE_JS` kept an element only if its box had width and height. A dropdown
draws its chosen option in its own box and lays out none of its options, so
every option in one was dropped from content.html: the `<select>` arrived
empty, and the choice the page showed was on no text surface. P8-75 blamed
the sanitizer; the sanitizer keeps `<option>` (`test_sanitizer.py`).

docling's HTML reader writes every `<option>` it is given as a paragraph of
its own and drops `selected`, so content.html holds a dropdown's choice and
nothing else from its list. Holding every option would have put words the
page never drew into content.md, content.txt and the chunks as page text.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from webshot.capture.snapshot import extract_semantic_content
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import extract

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "select_options.html"

#: What the fixture's selects draw, in page order: each dropdown's choice,
#: then every option a list box lays out.
DRAWN = ["beta", "epsilon", "zeta", "iota", "kappa", "mu", "digamma"]
DRAWN += ["pi", "rho", "tau", "upsilon", "phi", "chi"]

#: Options in the fixture that no select draws.
UNDRAWN = ["alpha", "gamma", "delta", "eta", "theta", "lambda", "never drawn"]
UNDRAWN += ["xi", "koppa", "omicron", "sigma", "psi", "omega", "vau", "sampi"]


@pytest.fixture(scope="module")
def snapshot() -> str:
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


def body_of(html: str) -> str:
    return html[html.index("<body>") : html.index("</body>")]


@pytest.mark.browser
def test_a_dropdown_keeps_the_option_it_draws_and_no_other(snapshot: str) -> None:
    body = body_of(snapshot)
    for held in (
        '<select name="plain"><option selected="">beta</option></select>',
        # The live choice, which the script changed. Paired by index, the one
        # option left (0 in the copy, 1 on the page) lost its `selected`.
        '<select id="scripted" name="scripted">'
        '<option value="e" selected="">epsilon</option></select>',
        '<select name="first"><option selected="">zeta</option></select>',
        # An <optgroup> is never drawn in a dropdown; it stays around the choice.
        '<select name="grouped"><optgroup><option selected="">iota</option>'
        "</optgroup></select>",
        # Hidden from the list, and drawn as the choice all the same.
        '<select name="placeholder"><option value="" selected="">kappa</option></select>',
        # The dropdown draws the option's label, not its text.
        '<select name="labelled"><option selected="">mu</option></select>',
        # An empty label is no label. `.label` reads it back empty, and taking
        # that for the drawn words emptied the option.
        '<select name="empty-label"><option selected="">digamma</option></select>',
        # No choice: the dropdown draws nothing.
        '<select id="unchosen" name="unchosen"></select>',
    ):
        assert held in body, (held, body)


@pytest.mark.browser
def test_a_list_box_keeps_every_option_it_draws_with_the_live_choice(
    snapshot: str,
) -> None:
    body = body_of(snapshot)
    # The hidden first option is not drawn. Paired by index, its removal took
    # `selected` off rho, and tau kept its own only because index 2 was chosen.
    assert (
        '<select name="listbox" multiple=""><option>pi</option>'
        '<option selected="">rho</option><option selected="">tau</option></select>'
    ) in body, body
    # `size` is what makes it a list box, so content.html keeps it.
    assert (
        '<select name="sized" size="3"><option>upsilon</option>'
        '<option selected="">phi</option><option>chi</option></select>'
    ) in body, body


@pytest.mark.browser
def test_an_option_no_select_draws_stays_out(snapshot: str) -> None:
    body = body_of(snapshot)
    for word in UNDRAWN:
        assert f">{word}<" not in body, (word, body)
    # A select the page does not show is not brought back by its choice.
    for name in ("undrawn-menu", "unseen-menu", "undrawn-list"):
        assert name not in body, (name, body)
    assert "<datalist" not in body


@pytest.mark.browser
def test_the_text_surfaces_hold_what_the_selects_drew_and_nothing_else(
    snapshot: str,
) -> None:
    # Line for line, so an option no select drew cannot be anywhere in them.
    result = extract(snapshot, [])
    for heading, surface in (("# ", result.markdown), ("", result.text)):
        lines = [line for line in surface.splitlines() if line]
        assert lines == [f"{heading}Select options", *DRAWN], surface
    chunks = [record["text"] for record in build_chunk_records(result.chunk_seeds)]
    assert chunks == [f"Select options\n{word}" for word in DRAWN], chunks
