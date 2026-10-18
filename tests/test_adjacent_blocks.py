"""Two quotations, or two definition lists, side by side stay two (docs/09 P63).

markdownify writes a blank line between two `<blockquote>`s or two `<dl>`s,
and Python-Markdown makes a quotation that follows a quotation part of it and
adds a term that follows a definition list to it.  The bridge marks where each
begins and ends, as it marks lists (docs/09 P32), and puts the same HTML
comment where two meet, which nh3 drops from the page.  Each case below was
measured to merge, or measured not to, and must then be written exactly as
the bridge wrote it before.
"""

from __future__ import annotations

import difflib
import random
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from markdownify import MarkdownConverter

from webshot.acquire.adapters import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from webshot.acquire.markitdown_bridge import (
    LIST_SEPARATOR,
    _TextHtmlConverter,
    _TextMarkdownify,
    to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import (  # noqa: E402
    ADJACENT_BLOCKS,
    ADJACENT_BLOCKS_ACROSS,
    ADJACENT_BLOCKS_ARCHIVE_PAGE,
    ADJACENT_BLOCKS_PAGE,
    ADJACENT_BLOCKS_REPORT_PAGE,
)

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"
BOOK = FIXTURE_ROOT / "adjacent_blocks_book.epub"
REPORT = FIXTURE_ROOT / "adjacent_blocks_report.docx"
ARCHIVE = FIXTURE_ROOT / "adjacent_blocks_archive.zip"

Blocks = list[tuple[str, list[str]]]


def _render(source: str) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


class _Blocks(HTMLParser):
    """Each quotation and definition list, in the order its tag opens, with
    the words it holds outside the quotations and definition lists in it."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: Blocks = []
        self._open: list[int] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("blockquote", "dl"):
            self.blocks.append((tag, []))
            self._open.append(len(self.blocks) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("blockquote", "dl"):
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._open:
            self.blocks[self._open[-1]][1].extend(data.split())


def _blocks(page: str) -> Blocks:
    parser = _Blocks()
    parser.feed(page)
    parser.close()
    return parser.blocks


class _Skeleton(HTMLParser):
    """A page's quotations, definition lists, terms, definitions, lists and
    items, nested as they are: what two of them merging changes."""

    KEPT = ("blockquote", "dl", "dt", "dd", "ol", "ul", "li")

    def __init__(self) -> None:
        super().__init__()
        self.root: list[Any] = []
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.KEPT:
            node: list[Any] = [tag, []]
            self._stack[-1].append(node)
            self._stack.append(node[1])

    def handle_endtag(self, tag: str) -> None:
        if tag in self.KEPT and len(self._stack) > 1:
            self._stack.pop()


def _skeleton(html: str) -> list[Any]:
    parser = _Skeleton()
    parser.feed(html)
    parser.close()
    return parser.root


def _converted(body: str) -> str:
    """`body` as the bridge writes an HTML document's Markdown."""
    page = f"<html><body><p>Top</p>{body}<p>End</p></body></html>"
    converted: str = _TextHtmlConverter().convert_string(page).markdown
    return converted


def _unmarked(monkeypatch: pytest.MonkeyPatch, body: str) -> str:
    """`body` as the bridge wrote it before quotations and definition lists
    were marked: markdownify's own writers for both, and for terms and
    definitions, whose marks (docs/09 P64) only the bridge's `convert_dl`
    takes away."""
    upstream = vars(MarkdownConverter)
    with monkeypatch.context() as patch:
        for name in ("convert_blockquote", "convert_dl", "convert_dt", "convert_dd"):
            patch.setattr(_TextMarkdownify, name, upstream[name])
        return _converted(body)


def _bq(*words: str) -> str:
    return (
        "<blockquote>" + "".join(f"<p>{word}</p>" for word in words) + "</blockquote>"
    )


def _dl(*pairs: tuple[str, str]) -> str:
    return "<dl>" + "".join(f"<dt>{t}</dt><dd>{d}</dd>" for t, d in pairs) + "</dl>"


TWO_QUOTATIONS: Blocks = [("blockquote", ["Alpha"]), ("blockquote", ["Bravo"])]
TWO_LISTS: Blocks = [("dl", ["Term", "Alpha"]), ("dl", ["Word", "Bravo"])]
QUOTATIONS = _bq("Alpha") + _bq("Bravo")
DEFINITIONS = _dl(("Term", "Alpha")) + _dl(("Word", "Bravo"))

#: (case, the HTML, the blocks the page must hold).  Every one of these was
#: measured as one quotation, or one definition list, on the page before.
MERGED: list[tuple[str, str, Blocks]] = [
    ("quotations", QUOTATIONS, TWO_QUOTATIONS),
    (
        "bare-text",
        "<blockquote>Alpha</blockquote><blockquote>Bravo</blockquote>",
        TWO_QUOTATIONS,
    ),
    (
        "three-quotations",
        _bq("Alpha", "Bravo") + _bq("Charlie") + _bq("Delta"),
        [
            ("blockquote", ["Alpha", "Bravo"]),
            ("blockquote", ["Charlie"]),
            ("blockquote", ["Delta"]),
        ],
    ),
    (
        "quotations-nothing-between",
        _bq("Alpha") + "\n  <div></div><p></p><!-- c -->" + _bq("Bravo"),
        TWO_QUOTATIONS,
    ),
    (
        "quotations-empty-between",
        _bq("Alpha") + "<blockquote></blockquote>" + _bq("Bravo"),
        TWO_QUOTATIONS,
    ),
    (
        "quotations-each-in-a-div",
        f"<div>{_bq('Alpha')}</div><div>{_bq('Bravo')}</div>",
        TWO_QUOTATIONS,
    ),
    (
        "quotations-in-a-quotation",
        f"<blockquote>{QUOTATIONS}</blockquote>",
        [("blockquote", []), *TWO_QUOTATIONS],
    ),
    (
        "quotations-in-a-quotation-after-text",
        f"<blockquote><p>Lead</p>{QUOTATIONS}</blockquote>",
        [("blockquote", ["Lead"]), *TWO_QUOTATIONS],
    ),
    (
        "quotations-in-a-definition",
        f"<dl><dt>Term</dt><dd>{QUOTATIONS}</dd></dl>",
        [("dl", ["Term"]), *TWO_QUOTATIONS],
    ),
    (
        "quotations-in-a-definition-after-text",
        f"<dl><dt>Term</dt><dd><p>Lead</p>{QUOTATIONS}</dd></dl>",
        [("dl", ["Term", "Lead"]), *TWO_QUOTATIONS],
    ),
    (
        "quotations-of-quotations",
        f"<blockquote>{_bq('Alpha')}</blockquote><blockquote>{_bq('Bravo')}</blockquote>",
        [
            ("blockquote", []),
            ("blockquote", ["Alpha"]),
            ("blockquote", []),
            ("blockquote", ["Bravo"]),
        ],
    ),
    (
        "quotation-then-quotation-of-one",
        f"{_bq('Alpha')}<blockquote>{_bq('Bravo')}</blockquote>",
        [("blockquote", ["Alpha"]), ("blockquote", []), ("blockquote", ["Bravo"])],
    ),
    (
        "quotations-holding-lists",
        "<blockquote><ul><li>Alpha</li></ul></blockquote>"
        "<blockquote><ul><li>Bravo</li></ul></blockquote>",
        TWO_QUOTATIONS,
    ),
    ("definition-lists", DEFINITIONS, TWO_LISTS),
    (
        "three-definition-lists",
        DEFINITIONS + _dl(("Name", "Charlie")),
        [*TWO_LISTS, ("dl", ["Name", "Charlie"])],
    ),
    (
        "definition-lists-nothing-between",
        _dl(("Term", "Alpha"))
        + "\n  <div></div><p></p><!-- c -->"
        + _dl(("Word", "Bravo")),
        TWO_LISTS,
    ),
    (
        "definition-lists-empty-between",
        _dl(("Term", "Alpha")) + "<dl></dl>" + _dl(("Word", "Bravo")),
        TWO_LISTS,
    ),
    (
        "definition-lists-each-in-a-div",
        f"<div>{_dl(('Term', 'Alpha'))}</div><div>{_dl(('Word', 'Bravo'))}</div>",
        TWO_LISTS,
    ),
    (
        "two-entries-then-a-list",
        _dl(("Term", "Alpha"), ("Name", "Charlie")) + _dl(("Word", "Bravo")),
        [("dl", ["Term", "Alpha", "Name", "Charlie"]), ("dl", ["Word", "Bravo"])],
    ),
    (
        "two-definitions-then-a-list",
        "<dl><dt>Term</dt><dd>Alpha</dd><dd>Charlie</dd></dl>" + _dl(("Word", "Bravo")),
        [("dl", ["Term", "Alpha", "Charlie"]), ("dl", ["Word", "Bravo"])],
    ),
    (
        "a-definition-of-paragraphs-then-a-list",
        "<dl><dt>Term</dt><dd><p>Alpha</p><p>Charlie</p></dd></dl>"
        + _dl(("Word", "Bravo")),
        [("dl", ["Term", "Alpha", "Charlie"]), ("dl", ["Word", "Bravo"])],
    ),
    (
        "definition-lists-in-a-quotation",
        f"<blockquote>{DEFINITIONS}</blockquote>",
        [("blockquote", []), *TWO_LISTS],
    ),
    (
        "definition-lists-in-a-definition",
        f"<dl><dt>Outer</dt><dd>{DEFINITIONS}</dd></dl>",
        [("dl", ["Outer"]), *TWO_LISTS],
    ),
    (
        "definition-lists-in-a-definition-after-text",
        f"<dl><dt>Outer</dt><dd><p>Lead</p>{DEFINITIONS}</dd></dl>",
        [("dl", ["Outer", "Lead"]), *TWO_LISTS],
    ),
]


@pytest.mark.parametrize(
    ("body", "expected"),
    [(body, expected) for _, body, expected in MERGED],
    ids=[case for case, *_ in MERGED],
)
def test_blocks_that_met_stay_apart(body: str, expected: Blocks) -> None:
    page = _render(_converted(body))
    assert _blocks(page) == expected, page
    # An empty quotation or definition list is written as nothing.
    written = re.sub(r"<(blockquote|dl)></\1>", "", body)
    assert _skeleton(page) == _skeleton(written), page


@pytest.mark.parametrize(
    "body", [body for _, body, _ in MERGED], ids=[case for case, *_ in MERGED]
)
def test_the_separator_is_all_that_changed(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """Against the Markdown the bridge wrote before, the only lines added are
    separators and the blank lines round them, and none is taken away; the
    page holds no comment, and the same text."""
    before, after = _unmarked(monkeypatch, body), _converted(body)
    assert LIST_SEPARATOR in after
    _assert_only_separators_added(before, after)
    page, earlier = _render(after), _render(before)
    assert "<!--" not in page
    assert re.sub(r"<[^>]+>|\s", "", page) == re.sub(r"<[^>]+>|\s", "", earlier)


#: A `<br>` between two quotations or two definition lists.  markdownify's
#: line break wrote nothing a Markdown block could hold, so the two met; the
#: bridge's is a `<br>`, a block of its own between them, as a browser draws
#: it (docs/09 P77), and as it is between two lists (`test_adjacent_lists`).
BROKEN_BETWEEN: list[tuple[str, str, Blocks]] = [
    ("quotations-line-break", _bq("Alpha") + "<br/>" + _bq("Bravo"), TWO_QUOTATIONS),
    (
        "quotations-break-in-paragraph",
        _bq("Alpha") + "<p><br/></p>" + _bq("Bravo"),
        TWO_QUOTATIONS,
    ),
    (
        "definition-lists-line-break",
        _dl(("Term", "Alpha")) + "<br/>" + _dl(("Word", "Bravo")),
        TWO_LISTS,
    ),
    (
        "definition-lists-break-in-paragraph",
        _dl(("Term", "Alpha")) + "<p><br/></p>" + _dl(("Word", "Bravo")),
        TWO_LISTS,
    ),
]


@pytest.mark.parametrize(
    ("body", "expected"),
    [(body, expected) for _, body, expected in BROKEN_BETWEEN],
    ids=[case for case, *_ in BROKEN_BETWEEN],
)
def test_a_line_break_between_two_blocks_stands_between_them(
    body: str, expected: Blocks
) -> None:
    converted = _converted(body)
    assert LIST_SEPARATOR not in converted, converted
    page = _render(converted)
    assert _blocks(page) == expected, page
    assert re.search(r"</(blockquote|dl)>\s*<p><br>\s*</p>\s*<\1>", page), page


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<blockquote><pre>one\n  two</pre></blockquote>"
            "<blockquote><p>Bravo</p><pre>three</pre></blockquote>",
            [
                ("blockquote", ["one", "two"]),
                ("blockquote", ["Bravo", "three"]),
            ],
        ),
        (
            "<dl><dt>Term</dt><dd><pre>one\n  two</pre></dd></dl>"
            "<dl><dt>Word</dt><dd>Bravo<pre>three</pre></dd></dl>",
            [("dl", ["Term", "one", "two"]), ("dl", ["Word", "Bravo", "three"])],
        ),
    ],
    ids=["quotations", "definition-lists"],
)
def test_blocks_side_by_side_keep_their_code_blocks(
    body: str, expected: Blocks
) -> None:
    """Two quotations, or two definition lists, side by side stay two (docs/09
    P63), and each one's code is a code block in it (docs/09 P47), both at
    once.  The quotation converter that marked where a quotation begins and
    ends took the place of the one that took its code blocks' marks away."""
    from bs4 import BeautifulSoup

    converted = _converted(body)
    assert not re.search("[\ue000-\uf8ff]", converted), converted
    page = _render(converted)
    assert _blocks(page) == expected, page
    container = expected[0][0].replace("dl", "dd")
    held = [
        (pre.find_parent(container) is not None, pre.get_text())
        for pre in BeautifulSoup(page, "html.parser")("pre")
    ]
    assert held == [(True, "one\n  two\n"), (True, "three\n")], page


def _assert_only_separators_added(before: str, after: str) -> None:
    changes = [
        line
        for line in difflib.ndiff(before.split("\n"), after.split("\n"))
        if line[:2] in ("- ", "+ ")
    ]
    assert not [line for line in changes if line.startswith("- ")], changes
    assert all(re.fullmatch(r"\+ [ >]*(?:<!-- -->)?", line) for line in changes), (
        changes
    )


def _li(*items: str) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


#: Blocks that never met, and blocks in a place this change leaves alone:
#: written exactly as the bridge wrote them before.
UNCHANGED = [
    ("quotation-then-definition-list", _bq("Alpha") + _dl(("Word", "Bravo"))),
    ("definition-list-then-quotation", _dl(("Term", "Alpha")) + _bq("Bravo")),
    ("list-then-quotation", _li("Alpha") + _bq("Bravo")),
    ("quotation-then-list", _bq("Alpha") + _li("Bravo")),
    ("text-between", _bq("Alpha") + "loose text" + _bq("Bravo")),
    ("paragraph-between", _dl(("Term", "Alpha")) + "<p>a</p>" + _dl(("Word", "B"))),
    ("rule-between", _bq("Alpha") + "<hr/>" + _bq("Bravo")),
    ("heading-between", _bq("Alpha") + "<h2>Heading</h2>" + _bq("Bravo")),
    # Python-Markdown would read the separator as this list's term (P63-3).
    (
        "a-list-that-opens-with-a-definition",
        _dl(("Term", "Alpha")) + "<dl><dd>Charlie</dd></dl>",
    ),
    # An item is indented by its marker's width, which Python-Markdown does not
    # read as the item's (docs/09 P32-5, P63-4): the pair is left as it was.
    ("quotations-in-an-item", _li(f"Lead{QUOTATIONS}")),
    ("quotations-opening-an-item", _li(QUOTATIONS)),
    ("quotations-in-item-ten", f'<ol start="10"><li>Lead{QUOTATIONS}</li></ol>'),
    ("definition-lists-in-an-item", _li(f"Lead{DEFINITIONS}")),
    ("definition-lists-in-item-ten", f'<ol start="10"><li>Lead{DEFINITIONS}</li></ol>'),
    (
        "quotations-in-a-quotation-in-an-item",
        _li(f"<blockquote>{QUOTATIONS}</blockquote>"),
    ),
    (
        "in-a-table-cell",
        f"<table><tr><th>H</th></tr><tr><td>{QUOTATIONS}{DEFINITIONS}</td></tr></table>",
    ),
    ("in-a-pre", f"<pre>{QUOTATIONS}{DEFINITIONS}</pre>"),
    ("in-a-heading", f"<h2>{QUOTATIONS}</h2>"),
    # P32's own: a list in a quotation, then a list, keeps P32's comment.
    (
        "p32-list-in-a-quotation-then-a-list",
        f"<blockquote>{_li('A')}</blockquote>{_li('B')}",
    ),
    ("p32-two-lists", _li("Alpha") + _li("Bravo")),
]


@pytest.mark.parametrize(
    "body", [body for _, body in UNCHANGED], ids=[case for case, _ in UNCHANGED]
)
def test_blocks_that_never_met_are_written_as_before(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    assert _converted(body) == _unmarked(monkeypatch, body)


def test_a_list_that_opens_with_a_definition_gets_no_empty_term() -> None:
    """Python-Markdown takes the paragraph before a definition with no term
    for its term, and the separator is one: it would print an empty `<dt>`.
    The definition joins the list before, as it did (P63-3)."""
    page = _render(_converted(DEFINITIONS + "<dl><dd>Charlie</dd></dl>"))
    assert "<dt></dt>" not in re.sub(r"\s", "", page), page
    assert _blocks(page) == [
        ("dl", ["Term", "Alpha"]),
        ("dl", ["Word", "Bravo", "Charlie"]),
    ], page


def test_a_documents_own_private_use_characters_are_kept() -> None:
    own = "\ue000keep\ue001"
    converted = _converted(f"<p>{own}</p><pre>{own}</pre>{QUOTATIONS}")
    assert converted.count(own) == 2, converted
    assert _blocks(_render(converted)) == TWO_QUOTATIONS


# --------------------------------------------------------------------------- #
# Seeded random trees
# --------------------------------------------------------------------------- #


def _tree(rng: random.Random, words: list[str], depth: int = 0) -> str:
    """Up to three pieces: text, a paragraph, or a quotation, definition
    list or div of the same, three deep; a container is often written twice,
    so that two of a kind side by side are common."""
    pieces = []
    for _ in range(rng.randint(1, 3)):
        kind = rng.choice(["text", "p", "box", "box", "box"])
        if kind == "text" or (kind == "p" or depth >= 3):
            word = f"w{len(words)}"
            words.append(word)
            pieces.append(word if kind == "text" else f"<p>{word}</p>")
            continue
        tag = rng.choice(["blockquote", "dl", "div"])
        for _ in range(rng.choice([1, 2, 2])):
            if tag == "dl":
                term = f"w{len(words)}"
                words.append(term)
                pieces.append(
                    f"<dl><dt>{term}</dt><dd>{_tree(rng, words, depth + 1)}</dd></dl>"
                )
            else:
                pieces.append(f"<{tag}>{_tree(rng, words, depth + 1)}</{tag}>")
    return "".join(pieces)


#: Two thousand such trees were measured: 873 put their quotations and
#: definition lists on the page as the tree has them before, all 2000 now
#: (P63-2).  Forty are held here.
RANDOM_TREES = [_tree(random.Random(seed), []) for seed in range(40)]


@pytest.mark.parametrize("body", RANDOM_TREES, ids=[f"tree{n}" for n in range(40)])
def test_random_trees_keep_their_blocks(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    after = _converted(body)
    page = _render(after)
    assert _skeleton(page) == _skeleton(body), (after, page)
    assert "<!--" not in page and "\ue000" not in after
    _assert_only_separators_added(_unmarked(monkeypatch, body), after)


# --------------------------------------------------------------------------- #
# The fixtures
# --------------------------------------------------------------------------- #


def test_the_book_holds_every_pair() -> None:
    """Each pair in the generator's table, the blocks around two of them, and
    the two across chapters."""
    pairs = sum(len(blocks) for _, blocks in ADJACENT_BLOCKS.values())
    around = sum(
        layout.startswith(("<blockquote>", "<dl>"))
        for layout, _ in ADJACENT_BLOCKS.values()
    )
    assert pairs + around + len(ADJACENT_BLOCKS_ACROSS) == len(ADJACENT_BLOCKS_PAGE)


def test_blocks_meeting_across_two_chapters_stay_apart() -> None:
    """MarkItDown converts each chapter on its own and joins them with a blank
    line; the first chapter ends with a quotation and the second begins with
    one, which meet only once the book is whole."""
    page = _render(to_markdown(BOOK, suffix=".epub").markdown)
    assert _blocks(page) == ADJACENT_BLOCKS_PAGE, page


def test_a_word_files_quotations_stay_apart() -> None:
    """mammoth writes them where the file's style map says to."""
    pytest.importorskip("mammoth")
    page = _render(to_markdown(REPORT, suffix=".docx").markdown)
    assert _blocks(page) == ADJACENT_BLOCKS_REPORT_PAGE, page


def test_a_zips_members_stay_apart() -> None:
    """An HTML member, and the Word file as a member."""
    pytest.importorskip("mammoth")
    converted = to_markdown(ARCHIVE, suffix=".zip").markdown
    assert "\ue000" not in converted
    assert _blocks(_render(converted)) == ADJACENT_BLOCKS_ARCHIVE_PAGE


def test_a_book_inside_a_zip_is_resolved_too(tmp_path: Path) -> None:
    archive = tmp_path / "books.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.write(BOOK, "blocks/adjacent_blocks_book.epub")
    converted = to_markdown(archive, suffix=".zip").markdown
    assert "\ue000" not in converted
    assert _blocks(_render(converted)) == ADJACENT_BLOCKS_PAGE
