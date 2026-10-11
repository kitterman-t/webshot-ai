"""Office content MarkItDown's readers pass over (docs/09 P27).

The fixtures are written by `tools/fixtures/office.py`. The archive-level tests
need nothing but the standard library; the ones that read through MarkItDown
skip without the office extra, as `test_office_formats.py` does.
"""

from __future__ import annotations

import importlib.util
import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree.ElementTree import Element, fromstring

import pytest

from webshot.acquire import ooxml

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "office"

needs_office = pytest.mark.skipif(
    importlib.util.find_spec("pptx") is None
    or importlib.util.find_spec("mammoth") is None,
    reason="install webshot[office] to read decks and Word files",
)

DECLARE = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _never(equation: Element) -> str:
    raise AssertionError("no equation should have been converted")


def _deck(slide: str) -> bytes:
    """The fewest parts that make `slide` the first slide of a deck."""
    members = {
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>'
        ),
        "ppt/presentation.xml": (
            f'<p:presentation {DECLARE}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
            "</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/slide" '
            'Target="slides/slide1.xml"/></Relationships>'
        ),
        "ppt/slides/slide1.xml": slide,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _slide(shapes: str) -> str:
    return (
        f"<p:sld {DECLARE}><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{shapes}"
        "</p:spTree></p:cSld></p:sld>"
    )


def _text_shape(text: str) -> str:
    return (
        "<p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/>"
        f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _rewritten_slide(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("ppt/slides/slide1.xml").decode("utf-8")


# --------------------------------------------------------------------------- #
# The archive, without a reader
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "prepare"),
    [
        ("sample_deck.pptx", ooxml.prepare_pptx),
        ("sample_report.docx", ooxml.prepare_docx),
    ],
)
def test_a_document_with_nothing_to_rewrite_reaches_markitdown_unchanged(
    name: str,
    prepare: Callable[[bytes, ooxml.EquationConverter], tuple[bytes, list[str]]],
) -> None:
    """Output does not change by accident: the bytes MarkItDown is handed are
    the bytes on disk unless a part holds what P27 found missing."""
    data = (FIXTURES / name).read_bytes()
    assert prepare(data, _never) == (data, [])


def test_alternate_content_becomes_its_choice_nested_or_not() -> None:
    nested = (
        "<mc:AlternateContent><mc:Choice Requires='a14'>"
        "<mc:AlternateContent><mc:Choice Requires='a14'>"
        f"{_text_shape('INNER CHOICE')}</mc:Choice>"
        f"<mc:Fallback>{_text_shape('INNER FALLBACK')}</mc:Fallback>"
        "</mc:AlternateContent></mc:Choice>"
        f"<mc:Fallback>{_text_shape('OUTER FALLBACK')}</mc:Fallback>"
        "</mc:AlternateContent>"
    )
    data, warnings = ooxml.prepare_pptx(_deck(_slide(nested)), _never)
    slide = _rewritten_slide(data)
    assert "INNER CHOICE" in slide
    assert "FALLBACK" not in slide
    assert "AlternateContent" not in slide
    assert warnings == []


def test_an_equation_becomes_a_run_of_its_latex_in_place() -> None:
    shape = (
        "<p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/><a:p>"
        "<a:r><a:t>x </a:t></a:r>"
        "<a14:m><m:oMath><m:r><m:t>≤</m:t></m:r></m:oMath></a14:m>"
        "<a:r><a:t> 12</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    data, warnings = ooxml.prepare_pptx(_deck(_slide(shape)), lambda equation: "\\leq ")
    slide = _rewritten_slide(data)
    assert "oMath" not in slide
    texts = fromstring(slide).iter(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
    )
    assert [t.text for t in texts] == ["x ", "$\\leq $", " 12"]
    assert warnings == []


def test_hostile_markup_in_a_slide_is_refused() -> None:
    slide = '<?xml version="1.0"?><!DOCTYPE p:sld [<!ENTITY boom "x">]>' + _slide(
        "<mc:AlternateContent/>"
    )
    with pytest.raises(ValueError, match=r"§6\.6"):
        ooxml.prepare_pptx(_deck(slide), _never)


def test_a_malformed_slide_is_left_for_the_reader_to_report() -> None:
    deck = _deck("<p:sld><mc:AlternateContent>")
    assert ooxml.prepare_pptx(deck, _never) == (deck, [])


def test_a_frame_nothing_renders_is_named() -> None:
    frame = (
        "<p:graphicFrame><a:graphic><a:graphicData "
        'uri="http://schemas.microsoft.com/office/drawing/2014/chartex"/>'
        "</a:graphic></p:graphicFrame>"
    )
    data, warnings = ooxml.prepare_pptx(_deck(_slide(frame)), _never)
    assert warnings == [
        "Slide 1: a chart of a type python-pptx cannot read is not in the page."
    ]
    # Reported, not rewritten: there was nothing to put in its place.
    assert data == _deck(_slide(frame))


def test_a_smartart_frame_whose_text_cannot_be_read_is_reported() -> None:
    frame = (
        "<p:graphicFrame><a:graphic><a:graphicData "
        'uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
        '<dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
        'r:dm="rId9"/></a:graphicData></a:graphic></p:graphicFrame>'
    )
    _, warnings = ooxml.prepare_pptx(_deck(_slide(frame)), _never)
    assert warnings == [
        "Slide 1: a SmartArt diagram's text could not be read, so it is not "
        "in the page."
    ]


INK = (
    "<mc:AlternateContent><mc:Choice "
    'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" '
    'Requires="p14"><p:contentPart p14:bwMode="auto" r:id="rId2">'
    "<p14:nvContentPartPr/></p:contentPart></mc:Choice>"
    f"<mc:Fallback>{_text_shape(chr(0xA0))}</mc:Fallback></mc:AlternateContent>"
)


def test_a_choice_that_needs_more_than_equations_stays_wrapped() -> None:
    """Ink unwrapped is a `p:contentPart`, and python-pptx fails on one: a
    deck it read before must not become one it cannot (docs/09 P27)."""
    deck = _deck(_slide(INK))
    assert ooxml.prepare_pptx(deck, _never) == (deck, [])


def test_text_left_wrapped_is_named() -> None:
    wrapped = (
        "<mc:AlternateContent><mc:Choice "
        'xmlns:x9="urn:example:newer-feature" Requires="x9">'
        f"{_text_shape('WORDS ONLY A NEWER READER SEES')}</mc:Choice>"
        "</mc:AlternateContent>"
    )
    _, warnings = ooxml.prepare_pptx(_deck(_slide(wrapped)), _never)
    assert warnings == [
        "Slide 1: text PowerPoint wrote for readers of 'x9' is not in the page "
        "('WORDS ONLY A NEWER READER SEES')."
    ]


def test_a_requirement_is_read_by_its_namespace_not_its_prefix() -> None:
    """`a14` bound to some other namespace is not DrawingML 2010."""
    impostor = (
        "<mc:AlternateContent><mc:Choice "
        'xmlns:a14="urn:example:not-drawingml" Requires="a14">'
        f"{_text_shape('IMPOSTOR')}</mc:Choice></mc:AlternateContent>"
    )
    data, warnings = ooxml.prepare_pptx(_deck(_slide(impostor)), _never)
    assert "AlternateContent" in _rewritten_slide(data)
    assert len(warnings) == 1 and "IMPOSTOR" in warnings[0]


def test_an_equation_left_wrapped_is_not_converted_or_counted_twice() -> None:
    wrapped = (
        "<mc:AlternateContent><mc:Choice "
        'xmlns:x9="urn:example:newer-feature" Requires="x9">'
        "<p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/><a:p>"
        "<a14:m><m:oMath><m:r><m:t>≤</m:t></m:r></m:oMath></a14:m>"
        "</a:p></p:txBody></p:sp></mc:Choice></mc:AlternateContent>"
    )
    deck = _deck(_slide(wrapped))
    data, warnings = ooxml.prepare_pptx(deck, _never)
    assert data == deck
    assert warnings == [
        "Slide 1: text PowerPoint wrote for readers of 'x9' is not in the page ('≤')."
    ]


def test_something_that_is_not_an_archive_is_left_for_the_reader() -> None:
    assert ooxml.prepare_pptx(b"not a zip", _never) == (b"not a zip", [])
    assert ooxml.prepare_docx(b"not a zip", _never) == (b"not a zip", [])


def test_a_table_left_wrapped_is_named() -> None:
    table = (
        "<mc:AlternateContent><mc:Choice "
        'xmlns:x9="urn:example:newer-feature" Requires="x9">'
        "<p:graphicFrame><a:graphic><a:graphicData "
        'uri="http://schemas.openxmlformats.org/drawingml/2006/table"/>'
        "</a:graphic></p:graphicFrame></mc:Choice></mc:AlternateContent>"
    )
    _, warnings = ooxml.prepare_pptx(_deck(_slide(table)), _never)
    assert warnings == [
        "Slide 1: a table PowerPoint wrapped for newer readers is not in the page."
    ]


# --------------------------------------------------------------------------- #
# Through MarkItDown
# --------------------------------------------------------------------------- #

DECK_MARKDOWN = """<!-- Slide number: 1 -->
# Bounds on Two Readings
SENTINEL PPTX EQUATION QUOKKA
x $\\leq $ 12; y1 $>$ 7

### Notes:
Say the bound aloud $\\geq $

<!-- Slide number: 2 -->
# A Grouped Bound
z $\\geq $ 3
Beside the group

<!-- Slide number: 3 -->
# A Bare Equation
a $\\ne $ b

<!-- Slide number: 4 -->
# A Two-Step Process
First gather the readings
Then weigh the second step

<!-- Slide number: 5 -->
# What Stays Unread
sum a+b"""

DECK_WARNINGS = (
    "Slide 5: an equation could not be converted to LaTeX, so its characters "
    "are given in document order without its layout ('a+b').",
    "Slide 5: an embedded object (Equation.3) is not in the page.",
)

REPORT_MARKDOWN = """# Equation Notes

SENTINEL DOCX EQUATION WOMBAT

inline a $\\ne $ b

$$E=mc^{2}$$

half of x is x2

box text a+b"""

REPORT_WARNINGS = (
    "The document body: a chart is not in the page.",
    "The document body: an equation could not be converted to LaTeX, so its "
    "characters are given in document order without its layout ('x2').",
    # Once, though the text box holds it twice: mammoth reads the Fallback.
    "The document body: an equation could not be converted to LaTeX, so its "
    "characters are given in document order without its layout ('a+b').",
)


def _markitdown_alone(name: str) -> str:
    from markitdown import MarkItDown, StreamInfo

    stream = io.BytesIO((FIXTURES / name).read_bytes())
    result = MarkItDown(enable_plugins=False).convert_stream(
        stream, stream_info=StreamInfo(extension=Path(name).suffix)
    )
    return str(result.markdown)


@needs_office
def test_markitdown_alone_still_loses_this_text() -> None:
    """Why `ooxml` exists, pinned to the upstream release that needs it.

    When this fails after a MarkItDown or python-pptx upgrade, the upstream
    reader has started reading some of this itself: compare its output with
    DECK_MARKDOWN and REPORT_MARKDOWN, and retire what it now does.
    """
    deck = _markitdown_alone("equation_deck.pptx")
    # The slide body PowerPoint wrapped, all of it, sentinel included.
    assert "# Bounds on Two Readings\n\n### Notes:" in deck
    assert "SENTINEL PPTX EQUATION QUOKKA" not in deck
    assert "\na  b\n" in deck, "a bare equation's characters"
    assert "First gather" not in deck, "SmartArt"
    report = _markitdown_alone("equation_report.docx")
    # One equation its converter refuses, and every equation is gone.
    assert "inline a b" in report
    assert "E=m" not in report


@needs_office
def test_a_deck_reaches_the_page_whole() -> None:
    from webshot.acquire.markitdown_bridge import to_markdown

    converted = to_markdown(FIXTURES / "equation_deck.pptx", suffix=".pptx")
    assert converted.markdown == DECK_MARKDOWN
    assert converted.warnings == DECK_WARNINGS


@needs_office
def test_word_equations_are_converted_one_at_a_time() -> None:
    from webshot.acquire.markitdown_bridge import to_markdown

    converted = to_markdown(FIXTURES / "equation_report.docx", suffix=".docx")
    assert converted.markdown == REPORT_MARKDOWN
    assert converted.warnings == REPORT_WARNINGS


@needs_office
def test_word_equations_read_as_markitdown_reads_them_when_it_can(
    tmp_path: Path,
) -> None:
    """Taking Word's equations over from MarkItDown changes no output it got right."""
    from tools.fixtures.office import write_equation_report

    from webshot.acquire.markitdown_bridge import to_markdown

    fixture = tmp_path / "convertible.docx"
    write_equation_report(fixture)
    with zipfile.ZipFile(fixture) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    document = members["word/document.xml"].decode("utf-8")
    start = document.index('<w:p><w:r><w:t xml:space="preserve">half of x')
    members["word/document.xml"] = (
        document[:start] + document[document.index("</w:body>") :]
    ).encode("utf-8")
    with zipfile.ZipFile(fixture, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)

    from markitdown import MarkItDown, StreamInfo

    upstream = MarkItDown(enable_plugins=False).convert_stream(
        io.BytesIO(fixture.read_bytes()), stream_info=StreamInfo(extension=".docx")
    )
    converted = to_markdown(fixture, suffix=".docx")
    assert "$$E=mc^{2}$$" in upstream.markdown
    assert converted.markdown == upstream.markdown
    assert converted.warnings == ()


@needs_office
def test_the_warnings_reach_the_materialized_source() -> None:
    from webshot.acquire.adapters import materialize_source

    materialized = materialize_source((FIXTURES / "equation_deck.pptx").as_uri())
    try:
        assert materialized.warnings == list(DECK_WARNINGS)
    finally:
        materialized.cleanup()


@needs_office
def test_a_rewrite_the_reader_cannot_open_falls_back_to_the_file_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rewrite is WebShot's own code; if it ever yields an archive the
    reader cannot open, the file is still read as it was, and that is said.

    Made to happen the way it did on a real deck: unwrapping the fixture's ink
    hands python-pptx a `p:contentPart`, which it fails on.
    """
    from webshot.acquire import markitdown_bridge

    monkeypatch.setattr(
        ooxml,
        "UNDERSTOOD",
        ooxml.UNDERSTOOD | {"http://schemas.microsoft.com/office/powerpoint/2010/main"},
    )
    converted = markitdown_bridge.to_markdown(
        FIXTURES / "equation_deck.pptx", suffix=".pptx"
    )
    assert converted.markdown == _markitdown_alone("equation_deck.pptx")
    assert len(converted.warnings) == 1
    assert converted.warnings[0].startswith(
        "equation_deck.pptx could not be read once its equations, "
        "AlternateContent and SmartArt were made readable ("
    )
    assert "AttributeError" in converted.warnings[0]
    assert "\n" not in converted.warnings[0]
