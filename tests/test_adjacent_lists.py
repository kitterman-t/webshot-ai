"""Two lists side by side in a converted document stay two lists (docs/09 P32).

markdownify writes a blank line between two HTML lists, and Python-Markdown
reads a list of the same kind after a blank line as the first one going on:
two EPUB `<ol>`s became one list, numbered on from the first, with every item
wrapped in a paragraph.  The bridge now marks where each list markdownify
writes begins and ends, and puts an HTML comment where two meet, which nh3
drops from the page.  Each case below is one that was measured to merge, or
one measured not to, which must come out exactly as upstream writes it.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
from markitdown.converters import HtmlConverter

from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import (
    LIST_SEPARATOR,
    _markitdown,
    _TextDocxConverter,
    _TextEpubConverter,
    _TextHtmlConverter,
    to_markdown,
)
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import ADJACENT_LISTS, ADJACENT_LISTS_PAGE  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"
BOOK = FIXTURE_ROOT / "adjacent_lists_book.epub"

Lists = list[tuple[str, list[str]]]


def _render(source: str) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _lists(page: str) -> Lists:
    """Each list on the page, in order: its tag and its items' text.

    An item that holds a paragraph is returned with its `<p>` in it, so a list
    that went loose cannot compare equal to a tight one.
    """
    return [
        (tag, [item.strip() for item in re.findall(r"<li>(.*?)</li>", body, re.S)])
        for tag, body in re.findall(r"<(ol|ul)\b[^>]*>(.*?)</\1>", page, re.S)
    ]


def _converted(body: str) -> str:
    """`body` as the bridge writes an HTML document's Markdown."""
    page = f"<html><body><p>Top</p>{body}<p>End</p></body></html>"
    converted: str = _TextHtmlConverter().convert_string(page).markdown
    return converted


def _ol(*items: str, start: int | None = None) -> str:
    opening = f'<ol start="{start}">' if start is not None else "<ol>"
    return opening + "".join(f"<li>{item}</li>" for item in items) + "</ol>"


def _ul(*items: str) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


#: (what stands between, the HTML, the lists the page must hold).  Every one of
#: these was measured as one list on the page before the bridge kept them apart.
MERGED = [
    (
        "start-5-then-1",
        _ol("Fifth", start=5) + _ol("One"),
        [("ol", ["Fifth"]), ("ol", ["One"])],
    ),
    (
        "ol-ol",
        _ol("Alpha", "Bravo") + _ol("Charlie", "Delta"),
        [("ol", ["Alpha", "Bravo"]), ("ol", ["Charlie", "Delta"])],
    ),
    (
        "ul-ul",
        _ul("Alpha", "Bravo") + _ul("Charlie", "Delta"),
        [("ul", ["Alpha", "Bravo"]), ("ul", ["Charlie", "Delta"])],
    ),
    (
        "ul-ul-ul",
        _ul("Alpha") + _ul("Bravo") + _ul("Charlie"),
        [("ul", ["Alpha"]), ("ul", ["Bravo"]), ("ul", ["Charlie"])],
    ),
    (
        "six-numbered",
        "".join(_ol(f"Item {n}", start=n) for n in range(1, 7)),
        [("ol", [f"Item {n}"]) for n in range(1, 7)],
    ),
    ("whitespace", _ol("Alpha") + "\n\n   \n" + _ol("Bravo"), None),
    ("empty-div", _ol("Alpha") + "<div></div>" + _ol("Bravo"), None),
    ("empty-paragraph", _ol("Alpha") + "<p></p>" + _ol("Bravo"), None),
    ("comment", _ol("Alpha") + "<!-- c -->" + _ol("Bravo"), None),
    ("line-break", _ol("Alpha") + "<br/>" + _ol("Bravo"), None),
    ("no-break-space", _ol("Alpha") + "<p>\u00a0</p>" + _ol("Bravo"), None),
    ("break-in-paragraph", _ol("Alpha") + "<p><br/></p>" + _ol("Bravo"), None),
    ("empty-anchor", _ol("Alpha") + '<a id="x"></a>' + _ol("Bravo"), None),
    ("empty-list", _ol("Alpha") + "<ol></ol>" + _ol("Bravo"), None),
    # markdownify writes an empty item as nothing, so the list between is gone.
    ("empty-item", _ol("Alpha") + "<ol><li></li></ol>" + _ol("Bravo"), None),
    ("each-in-a-div", f"<div>{_ol('Alpha')}</div><div>{_ol('Bravo')}</div>", None),
    ("second-in-a-div", f"{_ol('Alpha')}<div>{_ol('Bravo')}</div>", None),
    ("blockquote", f"<blockquote>{_ol('Alpha')}{_ol('Bravo')}</blockquote>", None),
    (
        "definition",
        f"<dl><dt>Term</dt><dd>{_ol('Alpha')}{_ol('Bravo')}</dd></dl>",
        None,
    ),
    # What mammoth writes for Word lists it does not join (docs/09 P31).
    (
        "mammoth",
        "<ol><li>Alpha</li><li>Bravo</li></ol><ol><li>Charlie</li></ol>",
        [("ol", ["Alpha", "Bravo"]), ("ol", ["Charlie"])],
    ),
]


@pytest.mark.parametrize(
    ("body", "expected"),
    [(body, expected) for _, body, expected in MERGED],
    ids=[case for case, *_ in MERGED],
)
def test_lists_that_met_stay_apart_and_tight(body: str, expected: Lists | None) -> None:
    page = _render(_converted(body))
    assert _lists(page) == (expected or [("ol", ["Alpha"]), ("ol", ["Bravo"])]), page
    # Tight: no item holds a paragraph.
    assert not re.search(r"<li>\s*<p>", page), page


@pytest.mark.parametrize("case", [case for case, *_ in MERGED])
def test_the_separator_shows_nothing(case: str) -> None:
    """The comment is a block to Markdown and gone from the page: between
    the two lists there is only whitespace, and the text is the same."""
    body = next(body for name, body, _ in MERGED if name == case)
    converted = _converted(body)
    assert LIST_SEPARATOR in converted
    page = _render(converted)
    assert "<!--" not in page
    assert re.search(r"</(ol|ul)>\s*(<[^>]+>\s*)*<\1\b", page), page
    upstream = _render(
        HtmlConverter()
        .convert_string(f"<html><body><p>Top</p>{body}<p>End</p></body></html>")
        .markdown
    )
    assert re.sub(r"<[^>]+>|\s", "", page) == re.sub(r"<[^>]+>|\s", "", upstream)


#: Lists that never met: written exactly as MarkItDown writes them.
APART = [
    ("ordered-then-bulleted", _ol("Alpha", "Bravo") + _ul("Charlie", "Delta")),
    ("bulleted-then-ordered", _ul("Alpha", "Bravo") + _ol("Charlie", "Delta")),
    ("text-between", _ol("Alpha") + "loose text" + _ol("Bravo")),
    ("paragraph-between", _ol("Alpha") + "<p>a paragraph</p>" + _ol("Bravo")),
    ("rule-between", _ol("Alpha") + "<hr/>" + _ol("Bravo")),
    ("second-in-a-quote", f"{_ol('Alpha')}<blockquote>{_ol('Bravo')}</blockquote>"),
    # Two lists nested in one item did merge, as upstream writes them (P32-5);
    # docs/09 P40 keeps them apart, in `test_two_lists_in_one_item_stay_two`.
    (
        "in-a-table-cell",
        f"<table><tr><th>H</th></tr><tr><td>{_ol('Alpha')}{_ol('Bravo')}</td></tr></table>",
    ),
    ("in-a-pre", f"<pre>{_ol('Alpha')}{_ol('Bravo')}</pre>"),
]


@pytest.mark.parametrize(
    "body", [body for _, body in APART], ids=[case for case, _ in APART]
)
def test_lists_that_never_met_are_written_as_upstream_writes_them(body: str) -> None:
    page = f"<html><body><p>Top</p>{body}<p>End</p></body></html>"
    ours = _TextHtmlConverter().convert_string(page).markdown
    assert ours == HtmlConverter().convert_string(page).markdown
    assert LIST_SEPARATOR not in ours


def test_a_documents_own_private_use_characters_are_kept() -> None:
    """The marks are private-use characters around a token drawn per
    conversion, so the same characters in a document are its own text."""
    own = "\ue000keep\ue001"
    converted = _converted(f"<p>{own}</p><pre>{own}</pre>{_ol('Alpha')}{_ol('Bravo')}")
    assert converted.count(own) == 2, converted
    assert _lists(_render(converted)) == [("ol", ["Alpha"]), ("ol", ["Bravo"])]


def test_no_mark_is_left_in_any_office_fixture() -> None:
    for fixture in sorted(FIXTURE_ROOT.iterdir()):
        if fixture.suffix not in {".docx", ".epub", ".xlsx", ".zip", ".pptx"}:
            continue
        if fixture.suffix in {".docx", ".pptx", ".xlsx"}:
            pytest.importorskip(
                {".docx": "mammoth", ".pptx": "pptx", ".xlsx": "openpyxl"}[
                    fixture.suffix
                ]
            )
        converted = to_markdown(fixture, suffix=fixture.suffix).markdown
        assert "\ue000" not in converted and "\ue001" not in converted, fixture.name


def test_the_fixture_holds_every_pair() -> None:
    """Each pair in the generator's table, and the two across chapters."""
    assert len(ADJACENT_LISTS) * 2 + 2 == len(ADJACENT_LISTS_PAGE)


def test_lists_meeting_across_two_chapters_stay_apart() -> None:
    """MarkItDown converts each chapter on its own and joins them with a blank
    line; the first chapter ends with a list and the second begins with one."""
    page = _render(to_markdown(BOOK, suffix=".epub").markdown)
    assert _lists(page) == ADJACENT_LISTS_PAGE, page
    assert not re.search(r"<li>\s*<p>", page), page


def test_a_book_inside_a_zip_is_resolved_too(tmp_path: Path) -> None:
    archive = tmp_path / "books.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.write(BOOK, "lists/adjacent_lists_book.epub")
    converted = to_markdown(archive, suffix=".zip").markdown
    assert "\ue000" not in converted
    assert _lists(_render(converted)) == ADJACENT_LISTS_PAGE


def test_each_reader_resolves_what_it_writes() -> None:
    """EPUB joins its chapters before resolving; every other reader resolves
    each document as markdownify finishes it."""
    markitdown = _markitdown()
    readers = [registration.converter for registration in markitdown._converters]
    epub = next(reader for reader in readers if isinstance(reader, _TextEpubConverter))
    assert epub._html_converter._list_seams is epub._list_seams
    docx = next(reader for reader in readers if isinstance(reader, _TextDocxConverter))
    assert docx._html_converter._list_seams is None
    html = next(reader for reader in readers if type(reader) is _TextHtmlConverter)
    assert html._list_seams is None


def test_native_markdown_still_means_what_markdown_says(tmp_path: Path) -> None:
    """A `.md` file is its author's Markdown, and two lists with a blank line
    between are one list there; the bridge never sees it."""
    source = tmp_path / "notes.md"
    source.write_text("1. Alpha\n\n1. Bravo\n", "utf-8")
    materialized = materialize_source(source.as_uri())
    try:
        page = Path(file_url_path(materialized.browser_url)).read_text("utf-8")
    finally:
        materialized.cleanup()
    assert _lists(page) == [("ol", ["<p>Alpha</p>", "<p>Bravo</p>"])], page
