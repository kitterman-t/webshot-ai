"""A Word file's lists keep the numbers Word shows on them (docs/09 P31).

mammoth records a list item's level and whether it is ordered, never its
number, so every `<ol>` it wrote counted from 1: a list Word starts at 3, one
it restarts at 4, one it counts on past a paragraph.  `ooxml` now works out
Word's numbers from `numbering.xml` and marks each numbered paragraph with its
own; the bridge sets each `<ol>`'s start from the marks and splits a list
where its numbers stop running on.

`WORD_SHOWS` is the model's specification: each case is a document built here
and the numbers Word 16.113 for Mac showed on its ordered items, read with
AppleScript's `list value` (docs/09 P31).  The archive-level tests need only
the standard library; the ones that read through MarkItDown skip without the
office extra, as `test_office_formats.py` does.
"""

from __future__ import annotations

import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from webshot.acquire import ooxml

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "office"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

needs_mammoth = pytest.mark.skipif(
    importlib.util.find_spec("mammoth") is None,
    reason="install webshot[office] to read Word files",
)


# --------------------------------------------------------------------------- #
# Documents, built here
# --------------------------------------------------------------------------- #


def _level(
    index: int, fmt: str = "decimal", start: int | None = 1, extra: str = ""
) -> str:
    start_xml = "" if start is None else f'<w:start w:val="{start}"/>'
    return (
        f'<w:lvl w:ilvl="{index}">{start_xml}<w:numFmt w:val="{fmt}"/>{extra}'
        f'<w:lvlText w:val="%{index + 1}."/></w:lvl>'
    )


def _abstract(number: int, *levels: str, extra: str = "") -> str:
    return f'<w:abstractNum w:abstractNumId="{number}">{extra}{"".join(levels)}</w:abstractNum>'


def _decimal(number: int, start: int | None = 1, fmt: str = "decimal") -> str:
    return _abstract(number, _level(0, fmt, start))


def _nested(number: int, restart_1: str = "", restart_2: str = "") -> str:
    return _abstract(
        number,
        _level(0),
        _level(1, "lowerLetter", extra=restart_1),
        _level(2, "lowerRoman", extra=restart_2),
    )


def _num(number: int, abstract: int, overrides: str = "") -> str:
    return f'<w:num w:numId="{number}"><w:abstractNumId w:val="{abstract}"/>{overrides}</w:num>'


def _override(level: int, start: int | None = None, definition: str = "") -> str:
    start_xml = "" if start is None else f'<w:startOverride w:val="{start}"/>'
    return f'<w:lvlOverride w:ilvl="{level}">{start_xml}{definition}</w:lvlOverride>'


def _p(
    text: str, num: int | None = None, level: int | None = 0, style: str = ""
) -> str:
    properties = f'<w:pStyle w:val="{style}"/>' if style else ""
    if num is not None:
        level_xml = "" if level is None else f'<w:ilvl w:val="{level}"/>'
        properties += f'<w:numPr>{level_xml}<w:numId w:val="{num}"/></w:numPr>'
    run = f"<w:r><w:t>{text}</w:t></w:r>" if text else ""
    return f"<w:p><w:pPr>{properties}</w:pPr>{run}</w:p>"


def _table(*paragraphs: str) -> str:
    return f"<w:tbl><w:tr><w:tc>{''.join(paragraphs)}</w:tc></w:tr></w:tbl>"


LIST_NUMBER_STYLE = (
    '<w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/>'
    '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
)


def _docx(
    abstracts: list[str], nums: list[str], body: list[str], styles: str = ""
) -> bytes:
    """The fewest parts that make a Word file of `body`, numbered as given."""
    relationships = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    members = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{relationships}/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        ),
        "word/_rels/document.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{relationships}/numbering" Target="numbering.xml"/>'
            f'<Relationship Id="rId2" Type="{relationships}/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            f'<w:document xmlns:w="{W}"><w:body>{"".join(body)}</w:body></w:document>'
        ),
        "word/numbering.xml": (
            f'<w:numbering xmlns:w="{W}">{"".join(abstracts)}{"".join(nums)}</w:numbering>'
        ),
        "word/styles.xml": (
            f'<w:styles xmlns:w="{W}">'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            f'<w:name w:val="Normal"/></w:style>{LIST_NUMBER_STYLE}{styles}</w:styles>'
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# What Word shows
# --------------------------------------------------------------------------- #

#: Each case: the document, and Word's number on each of its ordered items in
#: order — None where Word shows no number.  A bulleted item, and one Word
#: numbers in a list or at a level `numbering.xml` does not define (which
#: mammoth does not read as a list item), carry no mark and are not listed.
WORD_SHOWS = {
    "a definition that starts at 3": (
        [_decimal(0, 3)],
        [_num(1, 0)],
        [_p("A", 1), _p("B", 1)],
        [3, 4],
    ),
    "a definition that starts at 0": (
        [_decimal(0, 0)],
        [_num(1, 0)],
        [_p("A", 1), _p("B", 1)],
        [0, 1],
    ),
    "a negative start, which Word shows no number for": (
        [_decimal(0, -2)],
        [_num(1, 0)],
        [_p("A", 1), _p("B", 1)],
        [None, None],
    ),
    "no w:start, which Word counts from 0": (
        [_decimal(0, None)],
        [_num(1, 0)],
        [_p("A", 1), _p("B", 1)],
        [0, 1],
    ),
    "a start override, which a later item goes on from": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0, _override(0, 4))],
        [_p("A", 2), _p("B", 2), _p("Between"), _p("C", 2)],
        [4, 5, 6],
    ),
    "a list that goes on past a paragraph": (
        [_decimal(0)],
        [_num(1, 0)],
        [_p("One", 1), _p("Two", 1), _p("Between"), _p("Three", 1)],
        [1, 2, 3],
    ),
    "two instances of one definition, counted as one list": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0)],
        [
            _p("a", 1),
            _p("b", 1),
            _p("Between"),
            _p("c", 2),
            _p("d", 2),
            _p("Between"),
            _p("e", 1),
        ],
        [1, 2, 3, 4, 5],
    ),
    "two instances of one definition that meet": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0)],
        [_p("a", 1), _p("b", 1), _p("c", 2), _p("d", 2)],
        [1, 2, 3, 4],
    ),
    "an override restarts the count the instances share": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0, _override(0, 1))],
        [
            _p("a", 1),
            _p("b", 1),
            _p("Between"),
            _p("c", 2),
            _p("d", 2),
            _p("Between"),
            _p("e", 1),
        ],
        [1, 2, 1, 2, 3],
    ),
    "two definitions, counted apart": (
        [_decimal(0), _decimal(1)],
        [_num(1, 0), _num(2, 1)],
        [
            _p("a", 1),
            _p("b", 1),
            _p("Between"),
            _p("c", 2),
            _p("d", 2),
            _p("Between"),
            _p("e", 1),
        ],
        [1, 2, 1, 2, 3],
    ),
    "two definitions that meet": (
        [_decimal(0), _decimal(1)],
        [_num(1, 0), _num(2, 1)],
        [_p("a", 1), _p("b", 1), _p("c", 2), _p("d", 2)],
        [1, 2, 1, 2],
    ),
    "nested levels, each restarted by the one above": (
        [_nested(0)],
        [_num(1, 0)],
        [
            _p("1", 1, 0),
            _p("a", 1, 1),
            _p("b", 1, 1),
            _p("2", 1, 0),
            _p("a", 1, 1),
            _p("i", 1, 2),
            _p("b", 1, 1),
            _p("3", 1, 0),
        ],
        [1, 1, 2, 2, 1, 1, 2, 3],
    ),
    "w:lvlRestart 0, a level that never restarts": (
        [_nested(0, restart_1='<w:lvlRestart w:val="0"/>')],
        [_num(1, 0)],
        [_p("1", 1, 0), _p("a", 1, 1), _p("b", 1, 1), _p("2", 1, 0), _p("c", 1, 1)],
        [1, 1, 2, 2, 3],
    ),
    "w:lvlRestart 1, a level only the first restarts": (
        [_nested(0, restart_2='<w:lvlRestart w:val="1"/>')],
        [_num(1, 0)],
        [
            _p("1", 1, 0),
            _p("a", 1, 1),
            _p("i", 1, 2),
            _p("b", 1, 1),
            _p("ii", 1, 2),
            _p("2", 1, 0),
            _p("a", 1, 1),
            _p("i", 1, 2),
        ],
        [1, 1, 1, 2, 2, 2, 1, 1],
    ),
    "a deeper level first, which counts the level above as used": (
        [_nested(0)],
        [_num(1, 0)],
        [_p("a", 1, 1), _p("b", 1, 1), _p("2", 1, 0), _p("a", 1, 1)],
        [1, 2, 2, 1],
    ),
    "a level two down first": (
        [_nested(0)],
        [_num(1, 0)],
        [_p("i", 1, 2), _p("2", 1, 0), _p("a", 1, 1)],
        [1, 2, 1],
    ),
    "bullets inside a numbered list and beside it": (
        [
            _abstract(0, _level(0), _level(1, "bullet")),
            _abstract(1, _level(0, "bullet")),
        ],
        [_num(1, 0), _num(2, 1)],
        [
            _p("1", 1, 0),
            _p("-", 1, 1),
            _p("-", 1, 1),
            _p("2", 1, 0),
            _p("Between"),
            _p("3", 1, 0),
            _p("-", 2),
            _p("4", 1, 0),
        ],
        [1, 2, 3, 4],
    ),
    "formats: letters, roman, none and decimal": (
        [
            _decimal(0, 3, "lowerLetter"),
            _decimal(1, 4, "upperRoman"),
            _decimal(2, 2, "none"),
            _decimal(3, 5),
        ],
        [_num(1, 0), _num(2, 1), _num(3, 2), _num(4, 3)],
        [
            _p("c", 1),
            _p("d", 1),
            _p("Between"),
            _p("IV", 2),
            _p("V", 2),
            _p("Between"),
            _p("none", 3),
            _p("none", 3),
            _p("Between"),
            _p("5", 4),
            _p("6", 4),
        ],
        [3, 4, 4, 5, None, None, 5, 6],
    ),
    "a paragraph style's numbering, and w:numId 0 taking it away": (
        [_abstract(0, _level(0, start=5, extra='<w:pStyle w:val="ListNumber"/>'))],
        [_num(1, 0)],
        [
            _p("5", style="ListNumber"),
            _p("6", style="ListNumber"),
            _p("none", 0, style="ListNumber"),
            _p("7", style="ListNumber"),
        ],
        [5, 6, 7],
    ),
    "an empty item, which keeps its number": (
        [_decimal(0)],
        [_num(1, 0)],
        [_p("One", 1), _p("", 1), _p("Three", 1)],
        [1, 2, 3],
    ),
    "an override's own w:lvl, whose start Word does not use": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0, _override(0, definition=_level(0, start=7)))],
        [_p("A", 2), _p("B", 2)],
        [1, 2],
    ),
    "items in a table, counted in document order": (
        [_decimal(0)],
        [_num(1, 0)],
        [_p("Before", 1), _table(_p("a", 1), _p("b", 1)), _p("After", 1)],
        [1, 2, 3, 4],
    ),
    "an override only at an instance's first item": (
        [_decimal(0), _decimal(1)],
        [_num(1, 1), _num(2, 0, _override(0, 4))],
        [_p("4", 2), _p("Between"), _p("1", 1), _p("Between"), _p("5", 2)],
        [4, 1, 5],
    ),
    "a w:lvlOverride with no start, which changes nothing": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0, _override(0))],
        [_p("a", 1), _p("b", 1), _p("Between"), _p("c", 2)],
        [1, 2, 3],
    ),
    "starts on nested levels": (
        [_abstract(0, _level(0, start=2), _level(1, start=3))],
        [_num(1, 0)],
        [_p("2", 1, 0), _p("3", 1, 1), _p("4", 1, 1), _p("3", 1, 0), _p("3", 1, 1)],
        [2, 3, 4, 3, 3],
    ),
    "an override on a nested level is its start from then on": (
        [_nested(0)],
        [_num(1, 0), _num(2, 0, _override(1, 5))],
        [_p("1", 2, 0), _p("e", 2, 1), _p("2", 2, 0), _p("e", 2, 1)],
        [1, 5, 2, 5],
    ),
    "a w:numPr with no w:ilvl, at level 0": (
        [_decimal(0, 6)],
        [_num(1, 0)],
        [_p("A", 1, None), _p("B", 1, None)],
        [6, 7],
    ),
    "an override on another level shares the instances' count": (
        [_nested(0)],
        [_num(1, 0), _num(2, 0, _override(1, 5))],
        [_p("1", 1, 0), _p("2", 2, 0), _p("3", 1, 0), _p("e", 2, 1), _p("f", 1, 1)],
        [1, 2, 3, 5, 6],
    ),
    "an override's restart, and the levels below it": (
        [_nested(0)],
        [_num(1, 0), _num(2, 0, _override(0, 5))],
        [
            _p("1", 1, 0),
            _p("a", 1, 1),
            _p("5", 2, 0),
            _p("a", 2, 1),
            _p("b", 1, 1),
            _p("6", 1, 0),
        ],
        [1, 1, 5, 1, 2, 6],
    ),
    "a w:numId numbering.xml does not define": (
        [_decimal(0, 3)],
        [_num(1, 0)],
        [_p("A", 9), _p("B", 1)],
        [3],
    ),
    "a level numbering.xml does not define, which counts the level above": (
        [_decimal(0, 3)],
        [_num(1, 0)],
        [_p("A", 1, 4), _p("B", 1)],
        [4],
    ),
    "two overrides and their instances, one count": (
        [_decimal(0)],
        [_num(1, 0), _num(2, 0, _override(0, 10)), _num(3, 0, _override(0, 20))],
        [_p("1", 1), _p("10", 2), _p("20", 3), _p("21", 2), _p("22", 1), _p("23", 3)],
        [1, 10, 20, 21, 22, 23],
    ),
}


def _marked(data: bytes) -> list[str]:
    """The marks in each paragraph of `data`'s body, in order."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    return re.findall(
        f"{ooxml.LIST_MARK_OPEN}[0-9a-f]{{16}}\\d+{ooxml.LIST_MARK_CLOSE}", document
    )


@pytest.mark.parametrize("case", list(WORD_SHOWS), ids=list(WORD_SHOWS))
def test_each_item_carries_the_number_word_shows(case: str) -> None:
    abstracts, nums, body, shown = WORD_SHOWS[case]
    data, numbers = ooxml.number_word_lists(_docx(abstracts, nums, body))
    assert numbers is not None
    assert numbers.values == shown
    assert _marked(data) == [numbers.item_mark(index) for index in range(len(shown))]


def test_a_numbering_style_link_counts_with_the_definition_it_names() -> None:
    data = _docx(
        [
            _abstract(0, extra='<w:numStyleLink w:val="Outline"/>'),
            _abstract(1, _level(0, start=2), extra='<w:styleLink w:val="Outline"/>'),
        ],
        [_num(1, 0), _num(2, 1)],
        [_p("a", 1), _p("b", 1)],
        styles=(
            '<w:style w:type="numbering" w:styleId="Outline"><w:name w:val="Outline"/>'
            '<w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr></w:style>'
        ),
    )
    _, numbers = ooxml.number_word_lists(data)
    assert numbers is not None and numbers.values == [2, 3]  # Word: 2., 3.


def test_a_style_based_on_a_numbered_one_is_numbered_with_it() -> None:
    data = _docx(
        [_abstract(0, _level(0, start=5, extra='<w:pStyle w:val="ListNumber"/>'))],
        [_num(1, 0)],
        [
            _p("a", style="ListNumber"),
            _p("b", style="ListChild"),
            _p("c", style="ListNumber"),
        ],
        styles=(
            '<w:style w:type="paragraph" w:styleId="ListChild"><w:name w:val="List Child"/>'
            '<w:basedOn w:val="ListNumber"/></w:style>'
        ),
    )
    _, numbers = ooxml.number_word_lists(data)
    assert numbers is not None and numbers.values == [5, 6, 7]  # Word: 5., 6., 7.


def test_a_style_link_that_loops_numbers_nothing() -> None:
    data = _docx(
        [_abstract(0, extra='<w:numStyleLink w:val="Loop"/>')],
        [_num(1, 0)],
        [_p("a", 1)],
        styles=(
            '<w:style w:type="numbering" w:styleId="Loop"><w:name w:val="Loop"/>'
            '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        ),
    )
    assert ooxml.number_word_lists(data) == (data, None)


def test_a_start_that_is_no_number_leaves_its_list_unmarked() -> None:
    data = _docx(
        [
            _decimal(0, start=None).replace(
                "<w:numFmt", '<w:start w:val="three"/><w:numFmt'
            ),
            _decimal(1),
        ],
        [_num(1, 0), _num(2, 1)],
        [_p("a", 1), _p("b", 2)],
    )
    _, numbers = ooxml.number_word_lists(data)
    assert numbers is not None and numbers.values == [1]  # only "b"


@pytest.mark.parametrize(
    "data",
    [
        _docx([], [], [_p("Plain")]),
        _docx([_abstract(0, _level(0, "bullet"))], [_num(1, 0)], [_p("a", 1)]),
        b"not an archive",
    ],
    ids=["no list", "only bullets", "not an archive"],
)
def test_a_file_with_nothing_to_number_reaches_mammoth_unchanged(data: bytes) -> None:
    result, numbers = ooxml.number_word_lists(data)
    assert result is data
    assert numbers is None


def test_a_paragraph_mammoth_reads_from_a_fallback_is_counted_once() -> None:
    """mammoth reads an `mc:AlternateContent`'s Fallback; its Choice holds the
    same paragraphs again, and counting both would number every one twice."""
    boxed = _p("In a box", 1)
    alternate = (
        '<w:p><w:r><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
        f'markup-compatibility/2006"><mc:Choice Requires="wps">{boxed}</mc:Choice>'
        f"<mc:Fallback>{boxed}</mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    data, numbers = ooxml.number_word_lists(
        _docx([_decimal(0)], [_num(1, 0)], [_p("a", 1), alternate, _p("b", 1)])
    )
    assert numbers is not None and numbers.values == [1, 2, 3]
    assert len(_marked(data)) == 3


def test_a_paragraph_whose_mark_is_deleted_is_read_with_the_next() -> None:
    deleted = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        '<w:rPr><w:del w:id="1" w:author="x" w:date="2026-01-01T00:00:00Z"/></w:rPr>'
        "</w:pPr><w:r><w:t>joined</w:t></w:r></w:p>"
    )
    _, numbers = ooxml.number_word_lists(
        _docx([_decimal(0)], [_num(1, 0)], [_p("a", 1), deleted, _p("b", 1)])
    )
    assert numbers is not None and numbers.values == [1, 2]


def test_the_document_is_read_by_namespace_not_by_prefix() -> None:
    """`prepare_docx` writes a part it rewrites back with ElementTree's own
    prefixes, `ns0:` for `w:`: a document with an equation reaches this so."""
    plain = _docx([_decimal(0, 3)], [_num(1, 0)], [_p("a", 1), _p("b", 1)])
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["word/document.xml"] = (
        members["word/document.xml"]
        .replace(b"xmlns:w=", b"xmlns:ns0=")
        .replace(b"<w:", b"<ns0:")
        .replace(b"</w:", b"</ns0:")
        .replace(b" w:", b" ns0:")
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    _, numbers = ooxml.number_word_lists(buffer.getvalue())
    assert numbers is not None and numbers.values == [3, 4]


def test_the_mark_is_the_documents_own() -> None:
    """The mark holds a digest of the file it marks, so no document's text can
    already hold its own; and the same file is marked the same way twice."""
    data = _docx([_decimal(0)], [_num(1, 0)], [_p("a", 1)])
    _, first = ooxml.number_word_lists(data)
    _, again = ooxml.number_word_lists(data)
    assert first is not None and again is not None
    assert first.mark == again.mark
    other = _docx([_decimal(0)], [_num(1, 0)], [_p("b", 1)])
    _, different = ooxml.number_word_lists(other)
    assert different is not None and different.mark != first.mark


# --------------------------------------------------------------------------- #
# Setting the numbers on mammoth's lists
# --------------------------------------------------------------------------- #

MARK = f"{ooxml.LIST_MARK_OPEN}0123456789abcdef"


def _numbered(html: str, values: list[int | None]) -> tuple[str, ooxml.WordListNumbers]:
    from webshot.acquire.markitdown_bridge import _number_word_lists

    numbers = ooxml.WordListNumbers(mark=MARK, values=values)
    html = re.sub(r"\[(\d+)\]", lambda match: numbers.item_mark(int(match[1])), html)
    soup = BeautifulSoup(html, "html.parser")
    _number_word_lists(soup, numbers)
    assert numbers.applied
    return str(soup), numbers


def test_a_list_starts_at_its_first_items_number() -> None:
    html, numbers = _numbered("<ol><li>[0]a</li><li>[1]b</li></ol>", [3, 4])
    assert html == '<ol start="3"><li>a</li><li>b</li></ol>'
    assert (numbers.unmatched, numbers.unnumbered, numbers.unlisted) == (0, 0, 0)


def test_a_list_numbered_from_1_is_written_as_mammoth_wrote_it() -> None:
    html, _ = _numbered("<ol><li>[0]a</li><li>[1]b</li></ol>", [1, 2])
    assert html == "<ol><li>a</li><li>b</li></ol>"


def test_a_list_is_split_where_its_numbers_stop_running_on() -> None:
    html, _ = _numbered(
        "<ol><li>[0]a</li><li>[1]b</li><li>[2]c</li><li>[3]d</li></ol>", [1, 2, 1, 2]
    )
    assert html == "<ol><li>a</li><li>b</li></ol><ol><li>c</li><li>d</li></ol>"


def test_an_empty_item_goes_and_keeps_its_number() -> None:
    html, numbers = _numbered(
        "<ol><li>[0]a</li><li>[1]</li><li>[2]c</li></ol>", [1, 2, 3]
    )
    assert html == '<ol><li>a</li></ol><ol start="3"><li>c</li></ol>'
    assert numbers.unlisted == 0


def test_a_list_of_empty_items_goes_whole() -> None:
    html, _ = _numbered("<p>x</p><ol><li>[0]</li><li>[1]</li></ol>", [1, 2])
    assert html == "<p>x</p>"


def test_a_nested_list_is_numbered_on_its_own() -> None:
    html, _ = _numbered(
        "<ol><li>[0]a<ol><li>[1]i</li><li>[2]ii</li></ol></li><li>[3]b</li></ol>",
        [2, 5, 6, 3],
    )
    assert (
        html
        == '<ol start="2"><li>a<ol start="5"><li>i</li><li>ii</li></ol></li><li>b</li></ol>'
    )


def test_a_list_with_an_item_it_cannot_number_is_left_and_counted() -> None:
    html, numbers = _numbered("<ol><li>[0]a</li><li>b</li></ol>", [3])
    assert html == "<ol><li>a</li><li>b</li></ol>"
    assert numbers.unmatched == 1


def test_a_list_word_shows_no_numbers_on_is_left_and_counted() -> None:
    html, numbers = _numbered("<ol><li>[0]a</li><li>[1]b</li></ol>", [None, None])
    assert html == "<ol><li>a</li><li>b</li></ol>"
    assert numbers.unnumbered == 2


def test_a_numbered_paragraph_the_page_shows_unnumbered_is_counted() -> None:
    html, numbers = _numbered("<h1>[0]Scope</h1><ul><li>[1]x</li></ul>", [1, 2])
    assert html == "<h1>Scope</h1><ul><li>x</li></ul>"
    assert numbers.unlisted == 2


def test_an_empty_numbered_paragraph_elsewhere_goes_uncounted() -> None:
    html, numbers = _numbered("<h1>[0]</h1><p>[1]</p><p>x</p>", [1, None])
    assert html == "<p>x</p>"
    assert numbers.unlisted == 0


def test_mammoths_notes_lists_are_its_own_and_left_alone() -> None:
    notes = '<ol><li id="footnote-1"><p>n</p></li><li id="endnote-2"><p>m</p></li></ol>'
    html, numbers = _numbered(f"<ol><li>[0]a</li></ol>{notes}", [4])
    assert html == f'<ol start="4"><li>a</li></ol>{notes}'
    assert numbers.unmatched == 0


def test_a_mark_outside_every_element_is_taken_out_and_the_rest_kept() -> None:
    html, numbers = _numbered("[0]<p>x</p>", [1])
    assert html == "<p>x</p>"
    assert numbers.unlisted == 1


def test_text_like_a_mark_from_elsewhere_is_left_as_text() -> None:
    other = f"{ooxml.LIST_MARK_OPEN}fedcba98765432100{ooxml.LIST_MARK_CLOSE}"
    html, _ = _numbered(f"<ol><li>[0]{other}a</li></ol>", [2])
    assert html == f'<ol start="2"><li>{other}a</li></ol>'


# --------------------------------------------------------------------------- #
# Through MarkItDown
# --------------------------------------------------------------------------- #


#: What the fixture's manifest says of its numbering: the list Word shows no
#: numbers on, and the heading numbered as a list.
FIXTURE_WARNINGS = (
    "Word shows 2 list items without a number, which the page numbers (docs/09 P31).",
    "Word numbers 1 paragraph that the page shows without a number, such as a "
    "numbered heading (docs/09 P31).",
)


def _lists(markdown: str) -> list[list[str]]:
    """The Markdown's lists, each a run of `N. text` lines."""
    found: list[list[str]] = []
    previous = ""
    for line in markdown.splitlines():
        if re.match(r"-?\d+\. ", line):
            if not re.match(r"-?\d+\. ", previous):
                found.append([])
            found[-1].append(line)
        previous = line
    return found


@needs_mammoth
def test_a_word_files_lists_are_numbered_as_word_numbers_them() -> None:
    from tools.fixtures.office import NUMBERED_PAGE

    from webshot.acquire.markitdown_bridge import to_markdown

    converted = to_markdown(FIXTURES / "numbered_report.docx", suffix=".docx")
    assert _lists(converted.markdown) == NUMBERED_PAGE, converted.markdown
    assert ooxml.LIST_MARK_OPEN not in converted.markdown
    assert converted.warnings == FIXTURE_WARNINGS


@needs_mammoth
def test_a_word_file_in_a_zip_is_numbered_too(tmp_path: Path) -> None:
    """A zip's members are read by the path a file on its own is (docs/09
    P29), so a Word file inside one is numbered, and its warnings named."""
    from tools.fixtures.office import NUMBERED_PAGE

    from webshot.acquire.markitdown_bridge import to_markdown

    archive = tmp_path / "reports.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(FIXTURES / "numbered_report.docx", "week/numbered_report.docx")
    converted = to_markdown(archive, suffix=".zip")
    assert _lists(converted.markdown) == NUMBERED_PAGE, converted.markdown
    assert converted.warnings == tuple(
        f"week/numbered_report.docx: {warning}" for warning in FIXTURE_WARNINGS
    )


@needs_mammoth
def test_mammoth_alone_still_numbers_every_list_from_1() -> None:
    """Why the numbering exists, pinned to the upstream release that needs it.

    When this fails after a mammoth upgrade, mammoth has started writing a
    list's start itself: compare, and retire what it now does.
    """
    import mammoth

    html = mammoth.convert_to_html(
        io.BytesIO((FIXTURES / "numbered_report.docx").read_bytes())
    ).value
    assert "start=" not in html
    lists = re.findall(r"<ol>(.*?)</ol>", html)
    assert "<li>Brindle</li><li>Carrow</li>" in lists  # Word: 3., 4.
    assert "<li>Hollin</li>" in lists  # Word counts it on: 3.
    # Two of Word's lists in one <ol>, and an empty item's number gone with it.
    assert "<li>Lunt</li><li>Morrow</li><li>Nebbit</li><li>Orlen</li>" in lists
    assert "<li>Rusk</li><li>Sorrel</li>" in lists


@needs_mammoth
def test_a_rewrite_mammoth_cannot_open_falls_back_to_the_file_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webshot.acquire import markitdown_bridge

    marked = ooxml.number_word_lists

    def broken(data: bytes) -> tuple[bytes, ooxml.WordListNumbers | None]:
        result, numbers = marked(data)
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members["word/document.xml"] = b"<w:document"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        return buffer.getvalue(), numbers

    monkeypatch.setattr(ooxml, "number_word_lists", broken)
    converted = markitdown_bridge.to_markdown(
        FIXTURES / "numbered_report.docx", suffix=".docx"
    )
    assert "1. Carrow" not in converted.markdown and "2. Carrow" in converted.markdown
    # The fixture's equation was rewritten too, and is read as it is with it.
    equations, lists = converted.warnings
    assert equations.startswith(
        "The file could not be read once its equations, "
        "AlternateContent and SmartArt were made readable ("
    )
    assert lists.startswith(
        "The file could not be read once its list items were marked with "
        "Word's numbers ("
    )
    assert lists.endswith(
        "so it was read as it is: its lists are numbered from 1, whatever Word "
        "numbers them (docs/09 P31)."
    )


@needs_mammoth
def test_numbers_that_never_reach_the_html_step_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The numbers travel as a converter option.  A MarkItDown that stopped
    passing it on would leave the marks in the text and number nothing."""
    from webshot.acquire import markitdown_bridge

    convert = markitdown_bridge._TextHtmlConverter.convert

    def dropping(self, file_stream, stream_info, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.pop("word_lists", None)
        return convert(self, file_stream, stream_info, **kwargs)

    monkeypatch.setattr(markitdown_bridge._TextHtmlConverter, "convert", dropping)
    with pytest.raises(RuntimeError, match="P31"):
        markitdown_bridge.to_markdown(FIXTURES / "numbered_report.docx", suffix=".docx")


@needs_mammoth
def test_the_warnings_reach_the_materialized_source() -> None:
    from webshot.acquire.adapters import materialize_source

    materialized = materialize_source((FIXTURES / "numbered_report.docx").as_uri())
    try:
        assert sum("docs/09 P31" in warning for warning in materialized.warnings) == 2
    finally:
        materialized.cleanup()
