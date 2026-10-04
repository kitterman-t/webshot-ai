"""Markdown tables are written without cell padding (docs/09 P20-7).

docling pads every cell of a Markdown table to its column's widest, so one long
cell widens every row of its column. On a mirrored Wikipedia page 71% of
`content.md` was spaces. `padded_table.html` has 24 short rows and one long
note, the shape that does it: before the fix its `content.md` was 9,872 bytes, 84%
of them spaces.

The table must stay the same GitHub-flavored Markdown table, so the test parses
it with markdown-it-py's GFM table rule (installed with `rich`) and compares
every cell with the fixture's.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from markdown_it import MarkdownIt

from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import extract

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "padded_table.html"


class _Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            self.rows[-1].append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _expected() -> list[list[str]]:
    parser = _Cells()
    parser.feed(FIXTURE.read_text("utf-8"))
    return parser.rows


def _parsed(markdown: str) -> list[list[str]]:
    """The GFM table in `markdown`, as rows of cell text."""
    tokens = MarkdownIt("commonmark").enable("table").parse(markdown)
    rows: list[list[str]] = []
    inside = False
    for token in tokens:
        if token.type in {"table_open", "table_close"}:
            inside = token.type == "table_open"
        elif inside and token.type == "tr_open":
            rows.append([])
        elif inside and token.type == "inline":
            rows[-1].append(token.content)
    return rows


def _table_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("|")]


#: Two or more spaces between a cell's text and a pipe. An empty cell is `|  |`,
#: which is not padding.
PADDING = re.compile(r"[^\s|] {2,}\||\| {2,}[^\s|]")


def test_content_md_holds_the_same_table_without_padding() -> None:
    markdown = extract(FIXTURE.read_text("utf-8"), []).markdown
    rows = _parsed(markdown)
    # `&#124;` is how docling escapes a pipe in a cell; the parser reads it back.
    assert [[cell.replace("&#124;", "|") for cell in row] for row in rows] == (
        _expected()
    )
    lines = _table_lines(markdown)
    assert len(lines) == 26  # header, delimiter, 24 rows
    for line in lines:
        assert not PADDING.search(line), line


def test_content_txt_and_the_table_chunk_are_unpadded_too() -> None:
    result = extract(FIXTURE.read_text("utf-8"), [])
    tables = [
        record["text"]
        for record in build_chunk_records(result.chunk_seeds)
        if record["meta"]["kind"] == "table"
    ]
    assert tables, "the fixture's table produced no table chunk"
    for text in (result.text, *tables):
        lines = _table_lines(text)
        assert lines, text
        for line in lines:
            assert not PADDING.search(line), line
