"""A `<pre>`'s code reaches every surface as the page lays it out (docs/09 P55).

docling's HTML backend strips each text node of a `<pre>`, drops one of only
whitespace unless it is only line endings, joins what is left with a space,
and strips the result. Highlighted code, every token an element, lost its
indentation and most of its lines (`def f ( x ): return x`), and the first
line of any code its indentation; code with a link or emphasis in it was an
inline group written inline, its lines joined. docling-core fenced every block
with three backticks, so a line of three in the code closed it. A
line-number table (Pygments, Rouge, Chroma) was a table row, the code on one
line, and the snapshot dropped Chroma's blank lines, each an element holding
only a line break.

`pre_code_text.html` holds each shape, made by the highlighters themselves.
`pre_code_text.json` is each section's `<pre>` text as Chromium lays it out:
what selecting the block gives a reader. The browser tests hold it to
Chromium, and the capture end to end to it; the rest extract the fixture.
content.md is read with markdown-it-py (installed with `rich`), a CommonMark
reader.
"""

from __future__ import annotations

import asyncio
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt
from playwright.async_api import async_playwright

from webshot.capture.snapshot import extract_semantic_content
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import ExtractionResult, extract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "pre_code_text.html"
LAID_OUT = FIXTURES / "pre_code_text.json"

#: Each section's `<pre>`s and the footer's, as Chromium lays them out: the
#: text a selection of the block holds, and whether a table holds it.
_LAYOUT_JS = """() => {
    const out = {sections: {}, footer: []};
    let section = null;
    for (const element of document.querySelectorAll('h2, pre')) {
        if (element.tagName === 'H2') {
            section = out.sections[element.textContent] = [];
            continue;
        }
        if (element.parentElement.closest('pre')) continue;
        const selection = getSelection();
        selection.selectAllChildren(element);
        const record = {text: selection.toString(), table: !!element.closest('table')};
        (element.closest('footer') ? out.footer : section).push(record);
    }
    return out;
}"""

#: The table of two cells whose numbers do not count the code's lines.
NOT_A_GUTTER = "A table of two cells that is not a line-number table"


def _laid_out() -> dict[str, Any]:
    return dict(json.loads(LAID_OUT.read_text("utf-8")))


def _code(text: str) -> str:
    """The code of a `<pre>` whose text is `text`: its lines but the blank
    ones before the first line with text and after the last."""
    lines = re.split(r"\r\n|\r|\n", text)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _expected() -> dict[str, list[str]]:
    """Each section's blocks of code: each `<pre>`'s, but for a line-number
    table, which is its code."""
    expected: dict[str, list[str]] = {}
    for section, pres in _laid_out()["sections"].items():
        if section == NOT_A_GUTTER:
            expected[section] = []
        elif any(pre["table"] for pre in pres):
            numbers, code = pres
            assert re.fullmatch(r"[0-9\n]+", numbers["text"].strip())
            expected[section] = [_code(code["text"])]
        else:
            expected[section] = [_code(pre["text"]) for pre in pres]
    return expected


def _blocks(markdown: str) -> dict[str, list[str]]:
    """content.md's blocks of code by section, as CommonMark reads them: a
    block in a list item without the item's indentation."""
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
def result() -> ExtractionResult:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.mark.parametrize("section", list(_expected()))
def test_each_block_of_code_is_the_code_the_page_lays_out(
    section: str, result: ExtractionResult
) -> None:
    """Every `<pre>` is one block of code in content.md holding the lines a
    reader selecting it gets, indentation, blank lines and fence lines and
    all, but the blank lines at its edges."""
    assert _blocks(result.markdown).get(section, []) == _expected()[section]


def test_content_json_holds_one_code_item_a_block_as_the_page_has_it(
    result: ExtractionResult,
) -> None:
    """One `code` item for each block, its `orig` the code as the page has
    it, and no inline group of code runs."""
    document = json.loads(result.document_json)
    codes = [item for item in document["texts"] if item["label"] == "code"]
    expected = [code for blocks in _expected().values() for code in blocks]
    # The footer's code, last in the document.
    expected.append(_code(_laid_out()["footer"][0]["text"]))
    assert [code["orig"] for code in codes] == expected
    assert [code["text"] for code in codes] == expected
    code_refs = {code["self_ref"] for code in codes}
    assert not [
        group
        for group in document["groups"]
        if group["label"] == "inline"
        and any(child["$ref"] in code_refs for child in group["children"])
    ]


def _sits_at(text: list[str], start: int, lines: list[str]) -> bool:
    """Whether `lines` are `text`'s from `start`, at one indentation."""
    window = text[start : start + len(lines)]
    pad = " " * (len(window[0]) - len(lines[0])) if window else ""
    return len(window) == len(lines) and window == [
        pad + line if line else "" for line in lines
    ]


def test_content_txt_holds_each_block_line_for_line(result: ExtractionResult) -> None:
    """content.txt writes each block's lines in order, at one indentation:
    the margin, or its list item's."""
    text = result.text.split("\n")
    at = 0
    for blocks in _expected().values():
        for code in blocks:
            lines = code.split("\n")
            found = next(
                (
                    start
                    for start in range(at, len(text))
                    if _sits_at(text, start, lines)
                ),
                None,
            )
            assert found is not None, code
            at = found + len(lines)


def test_chunks_hold_each_block_as_content_md_writes_it(
    result: ExtractionResult,
) -> None:
    chunks = "\n".join(seed.text for seed in result.chunk_seeds)
    lines = result.markdown.split("\n")
    fences = [
        "\n".join(lines[token.map[0] : token.map[1]])
        for token in MarkdownIt("commonmark").parse(result.markdown)
        if token.type == "fence" and token.map
    ]
    assert len(fences) == sum(map(len, _expected().values()))
    for fence in fences:
        assert fence in chunks, fence


def test_each_fence_is_longer_than_any_run_of_backticks_in_its_code(
    result: ExtractionResult,
) -> None:
    """Three backticks, or one more than the code's longest run of them, so
    no line of the code closes the block."""
    fences = [
        token
        for token in MarkdownIt("commonmark").parse(result.markdown)
        if token.type == "fence"
    ]
    for token in fences:
        longest = max(map(len, re.findall("`+", token.content)), default=0)
        assert len(token.markup) == max(3, longest + 1), token.content
    assert {len(token.markup) for token in fences} == {3, 4, 5}


def test_a_link_all_of_the_code_has_is_its_code_items(result: ExtractionResult) -> None:
    """The backend gave a run's link and formatting to its item. A link all
    of the code has is the code item's; links and emphasis on parts of it
    have no field, and links.json, read from the page, keeps every link."""
    document = json.loads(result.document_json)
    codes = {
        item["orig"].split("\n")[0]: item
        for item in document["texts"]
        if item["label"] == "code"
    }
    assert codes["linked = 1"]["hyperlink"] == "#orvash"
    assert "hyperlink" not in codes["func Gorvane(wend []Plinth, sep Plinth) Plinth"]
    emphasized = codes["gorvane [OPTION...] PLINTH [FILE...]"]
    assert "formatting" not in emphasized
    # A block is never written as a link or with emphasis around its fence.
    assert "[```" not in result.markdown and "**```" not in result.markdown


def test_a_line_number_table_is_its_code_and_any_other_table_a_table(
    result: ExtractionResult,
) -> None:
    """A table of one row, a `<pre>` of numbers counting up by one beside a
    `<pre>` of code with no more lines, is the code. Numbers that do not
    count the code's lines keep their table."""
    assert result.tables == [[["1\n2\nthree", "wend\nvarnel\njessop"]]]
    assert "| 1 2 three | wend varnel jessop |" in result.markdown.split("\n")


def test_a_footers_highlighted_code_stays_in_the_footer(
    result: ExtractionResult,
) -> None:
    """The bridge files furniture whose text no chrome region holds in the
    body. A token of code split across elements is one word in its code
    item now, and in the footer's region too, so the footer's code stays
    off the body."""
    footer = _code(_laid_out()["footer"][0]["text"])
    assert footer.split("\n")[0] not in result.markdown
    document = json.loads(result.document_json)
    (item,) = [item for item in document["texts"] if item["orig"] == footer]
    assert item["content_layer"] == "furniture"


def _as_docling_reads_it(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> ExtractionResult:
    """`source` extracted with docling's own backend and text serializers."""
    from docling.backend.html_backend import HTMLDocumentBackend
    from docling_core.transforms.serializer.markdown import MarkdownTextSerializer
    from docling_core.transforms.serializer.plain_text import PlainTextTextSerializer

    with monkeypatch.context() as patch:
        patch.setattr(
            docling_bridge, "_code_block_backend", lambda: HTMLDocumentBackend
        )
        patch.setattr(
            docling_bridge,
            "_code_block_serializers",
            lambda: (MarkdownTextSerializer, PlainTextTextSerializer),
        )
        return extract(source, [])


def test_docling_alone_reads_none_of_it_as_the_page_lays_it_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture holds the defect: docling's own backend and serializers
    write no section's code as the page lays it out. Should a release start
    to, this says which, and the bridge's reading of it can go."""
    written = _blocks(_as_docling_reads_it(monkeypatch, FIXTURE.read_text()).markdown)
    expected = _expected()
    assert [
        section
        for section in expected
        if section != NOT_A_GUTTER and written.get(section, []) == expected[section]
    ] == []


def test_one_line_of_code_with_a_link_of_its_own_stays_a_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paragraph that is only linked code, and one line of code in a link,
    are inline code in the link, as docling writes them; only code of more
    lines is a block, where a link cannot be."""
    source = (
        "<!doctype html><html><body><main><h1>Links</h1>"
        '<p><a href="#wend"><code>wend_only</code></a></p>'
        '<a href="#varnel"><pre>varnel(1)</pre></a>'
        '<ul><li>Jessop<a href="#orvash"><pre>orvash(2)</pre></a></li></ul>'
        "</main></body></html>"
    )
    written = extract(source, []).markdown.split("\n")
    assert "[`wend_only`](#wend)" in written
    assert "[`varnel(1)`](#varnel)" in written
    assert "    [`orvash(2)`](#orvash)" in written
    before = _as_docling_reads_it(monkeypatch, source).markdown.split("\n")
    assert "[`wend_only`](#wend)" in before
    assert "[`varnel(1)`](#varnel)" in before


def test_without_the_backends_module_hooks_code_is_read_as_docling_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence must not read as success: with a module-level name gone, as a
    docling that renamed `_BR_SENTINEL` would have it, the capture completes
    on docling's own backend and a warning says why."""
    monkeypatch.setattr(
        docling_bridge,
        "_BACKEND_MODULE_HOOKS",
        (*docling_bridge._BACKEND_MODULE_HOOKS, "_a_name_docling_renamed"),
    )
    written = extract(FIXTURE.read_text("utf-8"), [])
    assert (
        _blocks(written.markdown)["An indented first line and fence lines"]
        != (_expected()["An indented first line and fence lines"])
    )
    assert any("docs/09 P50, P55" in warning for warning in written.warnings)


@cache
def _in_chromium() -> tuple[dict[str, Any], str]:
    """The fixture as Chromium lays it out, and the snapshot WebShot takes."""

    async def run() -> tuple[dict[str, Any], str]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                layout = await page.evaluate(_LAYOUT_JS)
                extracted = await extract_semantic_content(page)
                return dict(layout), str(extracted["html"])
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
def test_the_expectations_are_the_page_as_chromium_lays_it_out() -> None:
    layout, _ = _in_chromium()
    assert layout == _laid_out()


@pytest.mark.browser
def test_captured_end_to_end_each_block_is_the_code_the_page_lays_out() -> None:
    """The snapshot, then the bridge. A line of code that is an element
    holding only its line break has a box with no width, and the snapshot
    dropped it as unseen: every blank line in Chroma's code."""
    _, snapshot = _in_chromium()
    written = _blocks(extract(snapshot, []).markdown)
    assert {section: written.get(section, []) for section in _expected()} == (
        _expected()
    )
    chroma = written["Chroma with a line-number table"][0]
    assert 'return f"orvash {plinth}"\n\n    return' in chroma


@pytest.mark.browser
def test_the_snapshot_keeps_only_the_line_breaks_a_reader_sees() -> None:
    """An element holding only a line break is kept in preformatted text it
    lays out, as P48 keeps a `<br>`. In a hidden block, or `visibility:
    hidden`, it lays out nothing a reader sees, and stays out."""

    async def run() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    "<!doctype html><html><body><main>"
                    "<pre><span>shown</span><span>\n</span><br>after</pre>"
                    '<div style="display:none"><pre>gone<span>\n</span><br>'
                    "too</pre></div>"
                    '<pre>seen<span style="visibility:hidden">\n</span>'
                    "<br>here</pre>"
                    "</main></body></html>"
                )
                return str((await extract_semantic_content(page))["html"])
            finally:
                await browser.close()

    snapshot = asyncio.run(run())
    assert "<pre><span>shown</span><span>\n</span><br>after</pre>" in snapshot
    assert "gone" not in snapshot
    assert "<pre>seen<br>here</pre>" in snapshot
