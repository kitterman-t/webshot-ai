"""A highlighter's inline line numbers stay out of the code (docs/09 P68).

Pygments and Chroma can write each line's number into a `<pre>` as an element
at the start of the line, set apart with padding and marked
`user-select: none`. Read as text, after P55 kept a `<pre>`'s text as the page
lays it out, each number was glued to its line: `1package main`, `2`,
`3import "fmt"`. Prompts are marked unselectable the same way and are part of
what the block says, so the fixture holds them beside the numbers, along with
numbers that are code, numbers a reader can select, and line-number columns of
their own. The markup is Pygments 2.21's, Chroma 2.20's and the highlight.js
line-numbers plugin's own output. content.md is read as P55's tests read it,
with markdown-it-py.
"""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt
from playwright.async_api import async_playwright

from webshot.capture.snapshot import extract_semantic_content, line_number_warnings
from webshot.extract import docling_bridge

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "line_numbers.html"

AREA = "def area(r):\n    return 3 * r * r\n\nprint(area(2))\n"
MAIN = "package main\n\nfunc main() {\n\tprintln(1)\n}\n"
CONSOLE = ">>> area(2)\n12\n>>> for r in (1, 2):\n...     print(area(r))\n...\n3\n12\n"

#: Each section's `<pre>` text in content.html, in order.
EXPECTED = {
    "Pygments inline": [AREA],
    "Pygments inline from line 9": [AREA],
    "Pygments inline with a highlighted line": [AREA],
    "Pygments inline with linked numbers": [AREA],
    # The numbers go and the prompts stay, though the page marks both
    # unselectable; the output `12` after the number 2 is not a number at the
    # start of a line.
    "Pygments console with numbers": [CONSOLE],
    "Chroma inline": [MAIN],
    # Hugo's default writes Chroma's rules into style attributes.
    "Chroma inline with inline styles": [MAIN],
    "Chroma inline with linked numbers": [MAIN],
    "Pygments console": [CONSOLE],
    "Shiki shell prompt": ["$ npm install\n$ npm test"],
    "Numbers in the code": ["10 20\n30 40\n"],
    # Pygments' own stylesheet does not mark its numbers unselectable, and
    # nothing else here says they are not the page's text.
    "Selectable line numbers": [
        "1def area(r):\n2    return 3 * r * r\n3\n4print(area(2))\n"
    ],
    "An unselectable number inside a line": ["retries = 3\ndelay = 1"],
    # Every token of the block reads unselectable, so that mark does not set
    # the numbers apart from the code.
    "An unselectable block": ["10 20\n30 40"],
    # Nothing but numbers: there is no code for them to stand beside.
    "Unselectable numbers alone": ["1\n2"],
    # The gutter of a line-number table stays for the bridge, which reads
    # the code beside it (P55).
    "Pygments table": ["1\n2\n3\n4", AREA],
    "Chroma table": ["1\n2\n3\n4\n5\n", MAIN],
    "Prism line numbers": ["def area(r):\n    return 3 * r * r\n"],
    # Its rows hold no line endings; the bridge sets each on a line (P55).
    "highlight.js line numbers": ["def area(r):    return 3 * r * r print(area(2))"],
}

#: Where content.md's code is not the section's `<pre>` text: a line-number
#: table is its code, and each of the plugin's rows is a line.
EXPECTED_MD = {
    **EXPECTED,
    "Pygments table": [AREA],
    "Chroma table": [MAIN],
    "highlight.js line numbers": [
        "def area(r):\n    return 3 * r * r\n \nprint(area(2))"
    ],
}

NUMBERED = [
    ("body > div:nth-of-type(1) > pre", "1", "4"),
    ("body > div:nth-of-type(2) > pre", "9", "12"),
    ("body > div:nth-of-type(3) > pre", "1", "4"),
    ("body > div:nth-of-type(4) > pre", "1", "4"),
    ("body > div:nth-of-type(5) > pre", "1", "7"),
    ("body > pre:nth-of-type(1)", "1", "5"),
    ("body > pre:nth-of-type(2)", "1", "5"),
    ("body > pre:nth-of-type(3)", "1", "5"),
]


@pytest.fixture(scope="module")
def extracted() -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                return await extract_semantic_content(page)
            finally:
                await browser.close()

    return asyncio.run(run())


def _pre_texts(document: str) -> dict[str, list[str]]:
    """Each `<h2>` section's `<pre>` elements, as text."""
    sections: dict[str, list[str]] = {}
    for part in re.split(r"<h2[^>]*>", document)[1:]:
        heading, _, rest = part.partition("</h2>")
        sections[html.unescape(heading)] = [
            html.unescape(re.sub(r"<[^>]+>", "", pre))
            for pre in re.findall(r"<pre\b.*?</pre>", rest, re.DOTALL)
        ]
    return sections


@pytest.mark.browser
@pytest.mark.parametrize("section", list(EXPECTED))
def test_each_block_holds_its_code_and_only_its_code(
    extracted: dict[str, Any], section: str
) -> None:
    assert _pre_texts(extracted["html"])[section] == EXPECTED[section]


@pytest.mark.browser
def test_every_section_is_checked(extracted: dict[str, Any]) -> None:
    assert list(_pre_texts(extracted["html"])) == list(EXPECTED)


@pytest.mark.browser
def test_the_manifest_names_each_numbered_block_and_its_numbers(
    extracted: dict[str, Any],
) -> None:
    assert [
        (block["locator"], block["first"], block["last"])
        for block in extracted["lineNumbered"]
    ] == NUMBERED
    (warning,) = line_number_warnings(extracted)
    assert warning.startswith("8 code block(s) number their lines")
    assert "`body > div:nth-of-type(2) > pre` (numbered 9 to 12)" in warning
    assert "content.md, content.txt, content.json and" in warning


def test_no_numbered_block_means_no_warning() -> None:
    assert line_number_warnings({"lineNumbered": []}) == []


@pytest.mark.browser
def test_a_linked_number_is_still_a_link_the_page_shows(
    extracted: dict[str, Any],
) -> None:
    """links.json is the page's links, read off the live page: the number
    leaves the code's text, not the record of what the page links to."""
    urls = {link["url"].rpartition("#")[2] for link in extracted["links"]}
    assert {"area-1", "hl-0-5"} <= urls


def _code(text: str) -> str:
    """A block's code as content.md holds it: its lines but the blank ones at
    its edges (P55)."""
    lines = re.split(r"\r\n|\r|\n", text)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _blocks(markdown: str) -> dict[str, list[str]]:
    """content.md's blocks of code by section, as CommonMark reads them."""
    sections: dict[str, list[str]] = {}
    section = ""
    tokens = MarkdownIt("commonmark").parse(markdown)
    for at, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2":
            section = tokens[at + 1].content
            sections[section] = []
        elif token.type in {"fence", "code_block"}:
            sections.setdefault(section, []).append(token.content.removesuffix("\n"))
    return sections


@pytest.fixture(scope="module")
def markdown(extracted: dict[str, Any]) -> str:
    return docling_bridge.extract(extracted["html"], []).markdown


@pytest.mark.browser
@pytest.mark.parametrize("section", list(EXPECTED_MD))
def test_content_md_holds_each_block_as_its_code(markdown: str, section: str) -> None:
    """The text surfaces are built from content.html, so content.md's code
    has no numbers and keeps its prompts."""
    assert _blocks(markdown)[section] == [_code(text) for text in EXPECTED_MD[section]]
