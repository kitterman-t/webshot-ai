"""A list item's block of code keeps its lines in content.md (docs/09 P50).

docling's HTML backend read a `<pre>` in a list item into the item's line,
with every run of whitespace joined: content.md wrote the code as inline code
on one line, and the code of an item that opened with it as the item's own
text, no longer code. Given a block of code under an item, docling-core
indented only its first line, so at depth 1 the fence ended the list.

`item_code_blocks.html` holds each shape: down to "Code outside a list" it is
the page WebShot made of a draft of `code_items_book.epub` (docs/09 P44), which
also had code in a quotation, then shapes a web page has. The tests parse content.md with markdown-it-py (installed with
`rich`), a CommonMark reader, and compare its lists, code included, with the
fixture's, and each block of code with the same `<pre>` outside a list.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.errors import BundleBuildError
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import ExtractionResult, extract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "item_code_blocks.html"

#: The sections written as before, each for its own reason (docs/09 P50-3).
#: Code with emphasis in it was a third until the bridge read a `<pre>`'s text
#: as the page lays it out, one code item however its runs are formatted
#: (docs/09 P55).
AS_BEFORE = ("In a table cell", "Code in a term")

#: A list as ("list", start or None, items); an item as [its own text, its
#: children], a child a list or ("code", the `<pre>`'s HTML).
Node = Any


class _Sections(HTMLParser):
    """Each `<h2>` section's lists and top-level `<pre>`s, as `Node`s.

    An item's own text is what docling reads as one: its text but for its
    lists and its code, whitespace joined. A `<dl>` is the list docling makes
    of one: each term an item, its text bold, holding a list of its
    descriptions.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.sections: dict[str, list[Node]] = {}
        self._section = ""
        self._heading: list[str] | None = None
        self._open: list[Any] = []
        self._pre: tuple[int, int] | None = None
        self._lines = [0, *(m.end() for m in re.finditer("\n", source))]

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._lines[line - 1] + column

    def _children(self) -> list[Node]:
        if not self._open:
            return self.sections.setdefault(self._section, [])
        top = self._open[-1]
        return top[1] if isinstance(top, list) else top[2]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._pre is not None:
            return
        if tag == "h2":
            self._heading = []
        elif tag in {"ul", "ol"}:
            start = dict(attrs).get("start") or "1" if tag == "ol" else None
            node = ("list", start, [])
            self._children().append(node)
            self._open.append(node)
        elif tag == "dl":
            node = ("list", None, [])
            self._children().append(node)
            self._open.append(node)
        elif tag in {"li", "dd"}:
            if tag == "dd":
                term = self._open[-1][2][-1]
                if not term[1] or term[1][-1][0] != "list":
                    term[1].append(("list", None, []))
                self._open.append(term[1][-1])
            item: list[Any] = ["", []]
            self._open[-1][2].append(item)
            self._open.append(item)
        elif tag == "dt":
            item = ["", []]
            self._open[-1][2].append(item)
            self._open.append(item)
        elif tag == "pre":
            self._pre = (self._offset(), len(self._open))

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._pre is not None:
            start, _ = self._pre
            end = self.source.index("</pre>", self._offset()) + len("</pre>")
            self._pre = None
            self._children().append(("code", self.source[start:end]))
            return
        if self._pre is not None:
            return
        if tag == "h2" and self._heading is not None:
            self._section = "".join(self._heading).strip()
            self._heading = None
        elif tag in {"ul", "ol", "dl", "li", "dt"}:
            closed = self._open.pop()
            if isinstance(closed, list):
                closed[0] = " ".join(closed[0].split())
                if tag == "dt":
                    closed[0] = f"**{closed[0]}**"
        elif tag == "dd":
            item = self._open.pop()
            item[0] = " ".join(item[0].split())
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        elif self._pre is None and self._open and isinstance(self._open[-1], list):
            self._open[-1][0] += f" {data}"


def _md_tree(markdown: str) -> dict[str, list[Node]]:
    """content.md's lists and code, by section, as CommonMark reads them."""
    sections: dict[str, list[Node]] = {}
    section = ""
    stack: list[list[Node]] = [sections.setdefault(section, [])]
    items: list[list[Any]] = []
    heading = False
    for token in MarkdownIt("commonmark").parse(markdown):
        if token.type == "heading_open":
            heading = token.tag == "h2"
        elif token.type == "inline" and heading:
            section = token.content
            stack = [sections.setdefault(section, [])]
            heading = False
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            start = None
            if token.type == "ordered_list_open":
                start = str(token.attrGet("start") or 1)
            node = ("list", start, [])
            stack[-1].append(node)
            stack.append(node[2])
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            stack.pop()
        elif token.type == "list_item_open":
            item: list[Any] = ["", []]
            stack[-1].append(item)
            stack.append(item[1])
            items.append(item)
        elif token.type == "list_item_close":
            stack.pop()
            items.pop()
        elif token.type == "inline" and items and stack[-1] is items[-1][1]:
            items[-1][0] = " ".join([items[-1][0], token.content]).strip()
        elif token.type in {"fence", "code_block"}:
            stack[-1].append(("code", token.content.removesuffix("\n")))
        elif token.type == "inline":
            stack[-1].append(("text", token.content))
    return sections


def _page(body: str) -> str:
    return (
        f"<!doctype html><html><head><title>t</title></head><body>{body}</body></html>"
    )


@cache
def _outside_a_list(pre: str) -> str:
    """The code of `pre` as WebShot records it on its own, outside a list:
    content.json's text, line endings read as CommonMark reads them."""
    document = json.loads(extract(_page(pre), []).document_json)
    (code,) = [item["text"] for item in document["texts"] if item["label"] == "code"]
    return re.sub(r"\r\n|\r", "\n", code)


def _expected(nodes: list[Node]) -> list[Node]:
    def resolve(node: Node) -> Node:
        if isinstance(node, list):
            return [node[0], [resolve(child) for child in node[1]]]
        if node[0] == "list":
            return ("list", node[1], [resolve(child) for child in node[2]])
        return ("code", _outside_a_list(node[1]))

    return [resolve(node) for node in nodes]


@pytest.fixture(scope="module")
def result() -> ExtractionResult:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.fixture(scope="module")
def page() -> dict[str, list[Node]]:
    parser = _Sections(FIXTURE.read_text("utf-8"))
    parser.feed(parser.source)
    return parser.sections


def _block_sections() -> list[str]:
    parser = _Sections(FIXTURE.read_text("utf-8"))
    parser.feed(parser.source)
    return [name for name in parser.sections if name and name not in AS_BEFORE]


@pytest.mark.parametrize("section", _block_sections())
def test_each_items_code_is_a_block_in_its_item(
    section: str, result: ExtractionResult, page: dict[str, list[Node]]
) -> None:
    """Every `<pre>` an item holds is a block of code in that item in
    content.md, at its depth, after the item's text and in the order the item
    holds it among its lists, and reads as the same `<pre>` outside a list."""
    assert _md_tree(result.markdown)[section] == _expected(page[section])


def test_an_item_that_opens_with_code_is_an_item_holding_it(
    result: ExtractionResult,
) -> None:
    """content.json holds it as the page does: an item with no text of its
    own, the code its child. Before, the code was the item's text."""
    document = json.loads(result.document_json)
    texts = {item["self_ref"]: item for item in document["texts"]}
    owner = next(
        texts[item["parent"]["$ref"]]
        for item in document["texts"]
        if item["label"] == "code" and item["text"].startswith("opal = 1")
    )
    assert owner["label"] == "list_item"
    assert owner["text"] == ""
    assert "-   ```\n    opal = 1\n    opine = 2\n    ```\n- Jessop" in result.markdown


def _txt_block(lines: list[str], text: list[str]) -> int | None:
    """Where `lines` sit in `text` as one block at one indentation, or None.

    The first line may follow its item's marker, for an item that opens with
    its code. A blank line in the code is an empty line.
    """
    for at in range(len(text) - len(lines) + 1):
        window = text[at : at + len(lines)]
        if not window[0].endswith(lines[0]):
            continue
        prefix = window[0][: len(window[0]) - len(lines[0])]
        if not prefix or not re.fullmatch(r" *(?:(?:[-*+]|\d+[.)]) +)?", prefix):
            continue
        pad = " " * len(prefix)
        if window[1:] == [pad + line if line else "" for line in lines[1:]]:
            return at
    return None


def test_content_txt_holds_each_items_code_line_for_line(
    result: ExtractionResult, page: dict[str, list[Node]]
) -> None:
    """content.txt writes each block's lines, with no fence, indented into
    its item, as content.md does."""
    text = result.text.split("\n")

    def codes(nodes: list[Node], depth: int) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for node in nodes:
            if isinstance(node, list):
                found += codes(node[1], depth)
            elif node[0] == "list":
                found += codes(node[2], depth + 1)
            elif depth:
                found.append((depth, _outside_a_list(node[1])))
        return found

    at = 0
    for section in _block_sections():
        for depth, code in codes(page[section], 0):
            lines = code.split("\n")
            found = _txt_block(lines, text[at:])
            assert found is not None, (section, code)
            # The block starts at its item's content column or past it.
            first = text[at + found]
            assert len(first) - len(lines[0]) >= 4 * depth, (section, first)
            at += found + len(lines)


def test_chunks_hold_each_block_as_content_md_writes_it(
    result: ExtractionResult,
) -> None:
    """The chunker writes a list with the same serializer for its text, so a
    chunk holds each block in its item as content.md does."""
    chunks = "\n".join(seed.text for seed in result.chunk_seeds)
    lines = result.markdown.split("\n")
    blocks = [
        "\n".join(lines[token.map[0] : token.map[1]])
        for token in MarkdownIt("commonmark").parse(result.markdown)
        if token.type == "fence" and token.map
    ]
    assert len(blocks) >= 15
    for block in blocks:
        assert block in chunks, block


def _as_docling_writes_it(
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


def _surfaces(result: ExtractionResult) -> dict[str, Any]:
    return {
        "content.json": result.document_json,
        "content.md": result.markdown,
        "content.txt": result.text,
        "content.doctags": result.doctags,
        "tables": result.tables,
        "chunks": result.chunk_seeds,
        "warnings": result.warnings,
    }


def _items_hold_code(source: str) -> bool:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(source, "html.parser")
    return any(pre.find_parent(["li", "dd"]) is not None for pre in soup("pre"))


#: `pre_code_text.html` and `line_numbers.html` hold code docling reads
#: differently from the page, outside a list too (docs/09 P55, P68).
HTML_FIXTURES = sorted(
    path
    for path in FIXTURES.rglob("*.html")
    if path
    not in {FIXTURE, FIXTURES / "pre_code_text.html", FIXTURES / "line_numbers.html"}
)


@pytest.mark.parametrize("path", HTML_FIXTURES, ids=lambda path: path.name)
def test_a_page_with_no_code_in_an_item_is_written_as_docling_writes_it(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a `<pre>` in an item nothing changes: every surface, the
    chunks and the warnings are docling's own, byte for byte. No other
    fixture holds a `<pre>` whose text docling reads differently from the
    page, which the bridge reads as the page has it (docs/09 P55)."""
    source = path.read_text("utf-8", errors="replace")
    if _items_hold_code(source):
        pytest.skip("the page holds code in a list item")
    assert _outcome(lambda: extract(source, [])) == _outcome(
        lambda: _as_docling_writes_it(monkeypatch, source)
    )


def _outcome(run: Callable[[], ExtractionResult]) -> dict[str, Any]:
    """The surfaces, or the error. Some fixtures are raw pages docling cannot
    parse until the capture's snapshot has cleaned them (list_numbering.html,
    docs/09 P30); there "nothing changes" means the same refusal."""
    try:
        return _surfaces(run())
    except BundleBuildError as exc:
        return {"error": str(exc)}


def _section(source: str, title: str) -> str:
    start = source.index(f"<h2>{title}</h2>")
    end = source.find("<h2>", start + 1)
    return source[start : end if end != -1 else source.index("</main>")]


def test_a_terms_code_is_written_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """A term's code is the term's text, as the backend walks nothing a term
    holds, and is written as docling writes it."""
    source = _page(_section(FIXTURE.read_text("utf-8"), "Code in a term"))
    assert _surfaces(extract(source, [])) == _surfaces(
        _as_docling_writes_it(monkeypatch, source)
    )


def test_code_in_a_table_cell_is_inline_code_on_its_items_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docling writes a table cell on one line. An item's code there is inline
    code on the item's line, whitespace joined, as before; the code of an
    item that opens with it is now inline code rather than the item's text."""
    source = _page(_section(FIXTURE.read_text("utf-8"), "In a table cell"))
    row = "| - Cobble `cell = 1 row` - `only code` |"
    assert row in extract(source, []).markdown.split("\n")
    before = _as_docling_writes_it(monkeypatch, source).markdown
    assert "| - Cobble `cell = 1 row` - only code |" in before.split("\n")
    document = json.loads(extract(source, []).document_json)
    assert [item["text"] for item in document["texts"] if item["label"] == "code"] == [
        "cell = 1\n  row",
        "only\n  code",
    ]


def test_a_footers_code_stays_in_the_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An item with no text but its code has no text to tell chrome by; it
    stays on its code's layer, so a footer's code is not written into the
    body of content.md, as it was not before."""
    source = _page(
        "<main><p>Body</p></main><footer><ul><li><pre>f1\nf2</pre></li>"
        "<li>Fin<pre>g1\ng2</pre></li></ul></footer>"
    )
    written = extract(source, [])
    assert "f1" not in written.markdown
    assert "g1" not in written.markdown
    assert written.markdown == _as_docling_writes_it(monkeypatch, source).markdown
    document = json.loads(written.document_json)
    layers = {item["text"]: item["content_layer"] for item in document["texts"]}
    assert layers["f1\nf2"] == layers["g1\ng2"] == "furniture"


def test_code_outside_a_list_is_written_as_before(result: ExtractionResult) -> None:
    assert "## Code outside a list\n\n```\ntop level\n  unchanged\n```\n" in (
        result.markdown
    )


def test_the_backend_still_has_what_the_bridge_extends() -> None:
    """The methods the bridge's backend overrides or calls are docling's
    private ones, and so are the module's names it reads (docs/09 P55). A
    docling that renames one fails here, not in a capture."""
    from docling.backend import html_backend

    missing = [
        hook
        for hook in docling_bridge._BACKEND_HOOKS
        if not hasattr(html_backend.HTMLDocumentBackend, hook)
    ] + [
        hook
        for hook in docling_bridge._BACKEND_MODULE_HOOKS
        if not hasattr(html_backend, hook)
    ]
    assert not missing
    assert docling_bridge._backend_hooked()


def test_an_image_in_an_items_code_is_still_a_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walking the item made a picture of an image inside its `<pre>`, which
    reading the `<pre>` as code would drop, and with it every picture's match
    to its asset. Such a `<pre>` is read as docling reads it."""
    source = _page(
        "<ul><li>Step<pre>run it\nnow<img src='a.png' alt='shot'></pre></li></ul>"
    )
    written = extract(source, [])
    assert written.picture_count == 1
    assert _surfaces(written) == _surfaces(_as_docling_writes_it(monkeypatch, source))


def test_without_them_items_are_read_as_docling_reads_them_and_the_record_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence must not read as success: with a method gone, as a docling that
    renamed one, the capture still completes on docling's own backend, the
    code on its item's line, and a warning says why."""
    monkeypatch.setattr(
        docling_bridge,
        "_BACKEND_HOOKS",
        (*docling_bridge._BACKEND_HOOKS, "_a_method_docling_renamed"),
    )
    source = _page(_section(FIXTURE.read_text("utf-8"), "A pre with no code element"))
    written = extract(source, [])
    assert "- Varnel wend = 4 wold" in written.markdown.split("\n")
    assert any("docs/09 P50" in warning for warning in written.warnings)
