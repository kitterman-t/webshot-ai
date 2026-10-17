#!/usr/bin/env python3
"""Regenerate the office-format fixtures under `tests/fixtures/office/`.

    uv run --extra office python tools/fixtures/office.py

A committed `.docx` is a zip full of XML: unreviewable in a diff, and
unmaintainable once whoever made it has moved on.  This script is the fixture —
the files it writes are build output that happens to be committed so the test
suite does not need the office extra installed to *exist*, only to run.

Each fixture carries a sentinel string that occurs nowhere else in
the repository, so `tests/test_office_formats.py` can prove the text reached
the bundle through the converter rather than through a filename.  The one
exception is `equation_archive.zip`, which holds the equation deck and report
as they are, and so the deck's sentinel.

DOCX and EPUB are written by hand, because the packages that read them
(mammoth, MarkItDown's EPUB converter) cannot write them and pulling in a
writer for each would add dependencies no shipped code path uses.  Both
formats are small, declared containers; the XML below is the minimum each
specification requires.  PPTX and XLSX use python-pptx and openpyxl, which
`webshot[office]` already installs to read them.

The two `equation_*` fixtures hold what those readers pass over (docs/09 P27):
an equation, `mc:AlternateContent`, SmartArt, an embedded object, and an
equation MarkItDown's LaTeX converter refuses.  Every word in them is made up.
`equation_archive.zip` holds both, to show the same rewrite reaching a zip's
members (docs/09 P29).

The five `markup_*` fixtures hold text that HTML or Markdown would read as
structure — an `<img>` aimed at a `.invalid` host, `# heading`, `3.`, `===`,
`: definition`, a link, alt text and a link target that close their own
syntax — in every place each format's reader takes text from (docs/09 P28).

`left_out_archive.zip` holds one member of each kind a zip's reader leaves
out of the page, each of which the manifest must name (docs/09 P33).

`adjacent_lists_book.epub` holds lists with nothing between them but markup
that writes no text, which Python-Markdown read as one list (docs/09 P32).
"""

from __future__ import annotations

import base64
import csv
import io
import plistlib
import re
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: The strings the smoke test looks for. One per fixture, unmistakable in a
#: grep, and never written anywhere but into the document body.
SENTINELS = {
    "sample_report.docx": "SENTINEL DOCX WORD MARMOSET",
    "sample_deck.pptx": "SENTINEL PPTX SLIDE PANGOLIN",
    "sample_workbook.xlsx": "SENTINEL XLSX CELL AXOLOTL",
    "sample_book.epub": "SENTINEL EPUB CHAPTER NARWHAL",
    "numbered_book.epub": "SENTINEL EPUB NUMBERED KINKAJOU",
    "sample_archive.zip": "SENTINEL ZIP MEMBER OKAPI",
    # Inside the text box PowerPoint wraps in `mc:AlternateContent`, so it
    # reaches the bundle only if that box does.
    "equation_deck.pptx": "SENTINEL PPTX EQUATION QUOKKA",
    "equation_report.docx": "SENTINEL DOCX EQUATION WOMBAT",
    # The deck's own, from the deck inside it: in the wrapped text box, so it
    # reaches the bundle only if the rewrite reaches a zip's members too.
    "equation_archive.zip": "SENTINEL PPTX EQUATION QUOKKA",
    "markup_deck.pptx": "SENTINEL PPTX MARKUP CASSOWARY",
    "markup_report.docx": "SENTINEL DOCX MARKUP DUGONG",
    "markup_workbook.xlsx": "SENTINEL XLSX MARKUP GECKO",
    "markup_book.epub": "SENTINEL EPUB MARKUP IBEX",
    "markup_archive.zip": "SENTINEL ZIP MARKUP JERBOA",
    "left_out_archive.zip": "SENTINEL ZIP KEPT KINKAJOU",
    "adjacent_lists_book.epub": "SENTINEL EPUB LISTS TAMARIN",
    "picture_deck.pptx": "SENTINEL PPTX PICTURE KOALA",
    "picture_report.docx": "SENTINEL DOCX PICTURE LEMUR",
    "picture_book.epub": "SENTINEL EPUB PICTURE MANATEE",
}

#: Deterministic timestamp for every archive member, so regenerating a fixture
#: with no content change produces the same bytes (zip records mtimes).
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def _write_archive(
    path: Path, members: list[tuple[str, str | bytes]], *, first_stored: bool = False
) -> None:
    """Write a reproducible zip. `first_stored` is EPUB's uncompressed mimetype."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, payload) in enumerate(members):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = (
                zipfile.ZIP_STORED
                if (first_stored and index == 0)
                else zipfile.ZIP_DEFLATED
            )
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)


# --------------------------------------------------------------------------- #
# DOCX — WordprocessingML
# --------------------------------------------------------------------------- #

_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""


def _docx_paragraph(text: str, style: str | None = None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}<w:r><w:t xml:space='preserve'>{text}</w:t></w:r></w:p>"


def _docx_row(cells: list[str]) -> str:
    return (
        "<w:tr>"
        + "".join(f"<w:tc><w:tcPr/>{_docx_paragraph(cell)}</w:tc>" for cell in cells)
        + "</w:tr>"
    )


def write_docx(path: Path) -> None:
    body = (
        _docx_paragraph("Quarterly Field Report", "Heading1")
        + _docx_paragraph(SENTINELS["sample_report.docx"])
        + _docx_paragraph("Regional totals", "Heading2")
        + "<w:tbl><w:tblPr/>"
        + _docx_row(["Region", "Revenue"])
        + _docx_row(["North", "1250000"])
        + _docx_row(["South", "980000"])
        + "</w:tbl>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>\n"
    )
    _write_archive(
        path,
        [
            ("[Content_Types].xml", _DOCX_CONTENT_TYPES),
            ("_rels/.rels", _DOCX_RELS),
            ("word/document.xml", document),
        ],
    )


# --------------------------------------------------------------------------- #
# EPUB — an OCF container around XHTML
# --------------------------------------------------------------------------- #

_EPUB_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

_EPUB_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:webshot-office-fixture</dc:identifier>
    <dc:title>The Field Notebook</dc:title>
    <dc:creator>WebShot fixtures</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""

_EPUB_CHAPTER = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter One</title></head>
<body>
  <h1>Chapter One</h1>
  <p>{SENTINELS["sample_book.epub"]}</p>
  <p>A second paragraph, so the chapter has more than a sentinel in it.</p>
</body></html>
"""


def write_epub(path: Path) -> None:
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", _EPUB_OPF),
            ("OEBPS/chapter.xhtml", _EPUB_CHAPTER),
        ],
        first_stored=True,
    )


#: Lists that begin past 1, which markdownify writes as Markdown that begins
#: at that number (docs/09 P30). Each is closed by a paragraph: two adjacent
#: lists were one list to Python-Markdown until docs/09 P32.
_EPUB_NUMBERED_CHAPTER = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter Two</title></head>
<body>
  <h1>Chapter Two</h1>
  <p>{SENTINELS["numbered_book.epub"]}</p>
  <p>The survey resumes where the first chapter stopped.</p>
  <ol start="5"><li>Count the marsh plots</li><li>Weigh the samples</li></ol>
  <p>A list that begins at zero.</p>
  <ol start="0"><li>Calibrate the scale</li></ol>
  <p>A list inside a list.</p>
  <ol start="12"><li>Log the tide<ol start="4"><li>Note the wind</li></ol></li></ol>
  <p>A list that begins at one.</p>
  <ol><li>File the notebook</li></ol>
</body></html>
"""


def write_numbered_epub(path: Path) -> None:
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", _EPUB_OPF),
            ("OEBPS/chapter.xhtml", _EPUB_NUMBERED_CHAPTER),
        ],
        first_stored=True,
    )


# --------------------------------------------------------------------------- #
# ZIP — a folder of ordinary documents
# --------------------------------------------------------------------------- #


def write_zip(path: Path) -> None:
    _write_archive(
        path,
        [
            (
                "notes/summary.txt",
                f"Field notes\n\n{SENTINELS['sample_archive.zip']}\n",
            ),
            ("data/regions.csv", "region,revenue\nNorth,1250000\nSouth,980000\n"),
        ],
    )


# --------------------------------------------------------------------------- #
# PPTX / XLSX — written with the readers' own libraries
# --------------------------------------------------------------------------- #


def write_pptx(path: Path) -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Field Review"
    slide.placeholders[1].text = SENTINELS["sample_deck.pptx"]
    path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(path))


def write_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Regions"
    sheet.append(["Region", "Revenue", "Note"])
    sheet.append(["North", 1250000, SENTINELS["sample_workbook.xlsx"]])
    sheet.append(["South", 980000, "steady"])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))


# --------------------------------------------------------------------------- #
# Equations and what else the readers pass over (docs/09 P27)
# --------------------------------------------------------------------------- #

_OOXML = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}
_DECLARE = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in _OOXML.items())

#: A 1x1 PNG: the picture PowerPoint fills the Fallback shape with.
_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f80f0000010101005d0a2db4"
    "0000000049454e44ae426082"
)


def _math(inner: str) -> str:
    return f"<m:oMath>{inner}</m:oMath>"


def _math_run(text: str) -> str:
    # PowerPoint gives a math run DrawingML run properties; Word gives none.
    return f'<m:r><a:rPr lang="en-US" i="1"/><m:t>{text}</m:t></m:r>'


def _inline_equation(inner: str) -> str:
    return f"<a14:m>{_math(inner)}</a14:m>"


def _run(text: str, properties: str = "") -> str:
    return f"<a:r>{properties}<a:t>{text}</a:t></a:r>"


#: Fraction and delimiter properties are optional in the schema, and MarkItDown's
#: LaTeX converter needs them: these two are what it refuses.
_REFUSED_FRACTION = "<m:f><m:num><m:r><m:t>x</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>"
_REFUSED_DELIMITER = "<m:d><m:e><m:r><m:t>a+b</m:t></m:r></m:e></m:d>"


def _text_shape(
    shape_id: int, paragraphs: str, *, fill: str = "", y: int = 1600200
) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Text {shape_id}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="457200" y="{y}"/><a:ext cx="8229600" cy="1500000"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{fill}</p:spPr>'
        f"<p:txBody><a:bodyPr/><a:lstStyle/>{paragraphs}</p:txBody></p:sp>"
    )


def _alternate(choice: str, fallback: str, requires: str = "a14") -> str:
    """What PowerPoint writes for a shape a 2007 reader cannot draw."""
    return (
        f'<mc:AlternateContent><mc:Choice Requires="{requires}">{choice}'
        f"</mc:Choice><mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>"
    )


def _pictured(shape_id: int, picture: str, *, y: int = 1600200) -> str:
    """The Fallback: a no-break space over a picture of the real text."""
    fill = (
        f'<a:blipFill><a:blip r:embed="{picture}"/>'
        "<a:stretch><a:fillRect/></a:stretch></a:blipFill>"
    )
    return _text_shape(shape_id, f"<a:p>{_run(chr(0xA0))}</a:p>", fill=fill, y=y)


def _smartart_model() -> bytes:
    """Two steps under one diagram, stored in the opposite order to `srcOrd`."""
    point = (
        '<dgm:pt modelId="{{{id}}}"><dgm:prSet/><dgm:spPr/><dgm:t><a:bodyPr/>'
        "<a:lstStyle/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></dgm:t></dgm:pt>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<dgm:dataModel {_DECLARE}><dgm:ptLst>"
        '<dgm:pt modelId="{0}" type="doc"><dgm:prSet/><dgm:spPr/><dgm:t><a:bodyPr/>'
        "<a:lstStyle/><a:p/></dgm:t></dgm:pt>"
        + point.format(id=2, text="Then weigh the second step")
        + point.format(id=1, text="First gather the readings")
        + '<dgm:pt modelId="{3}" type="pres"><dgm:prSet presName="diagram"/><dgm:spPr/></dgm:pt>'
        "</dgm:ptLst><dgm:cxnLst>"
        '<dgm:cxn modelId="{8}" srcId="{0}" destId="{2}" srcOrd="1" destOrd="0"/>'
        '<dgm:cxn modelId="{9}" srcId="{0}" destId="{1}" srcOrd="0" destOrd="0"/>'
        '<dgm:cxn modelId="{7}" type="presOf" srcId="{1}" destId="{3}" srcOrd="0" destOrd="0"/>'
        "</dgm:cxnLst><dgm:bg/><dgm:whole/></dgm:dataModel>"
    ).encode()


def _repack(data: bytes, path: Path) -> None:
    """python-pptx's archive again, with fixed timestamps so it regenerates byte for byte."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members: list[tuple[str, str | bytes]] = [
            (info.filename, archive.read(info)) for info in archive.infolist()
        ]
    _write_archive(path, members)


def write_equation_deck(path: Path) -> None:
    from pptx import Presentation
    from pptx.opc.package import Part
    from pptx.opc.packuri import PackURI
    from pptx.oxml import parse_xml

    def place(parent: Any, markup: str) -> None:
        """Append `markup` to an element of python-pptx's tree, namespaces declared."""
        for element in list(parse_xml(f"<wrapper {_DECLARE}>{markup}</wrapper>")):
            parent.append(element)

    presentation = Presentation()
    title_only = presentation.slide_layouts[5]

    # 1. The text box PowerPoint writes when one holds an inline equation.
    slide = presentation.slides.add_slide(title_only)
    slide.shapes.title.text = "Bounds on Two Readings"
    _, picture = slide.part.get_or_add_image_part(io.BytesIO(_PIXEL_PNG))
    subscript = '<a:rPr baseline="-25000"/>'
    body = (
        f"<a:p>{_run(SENTINELS['equation_deck.pptx'])}</a:p><a:p>"
        + _run("x ")
        + _inline_equation(_math_run("≤"))
        + _run(" 12; y")
        + _run("1", subscript)
        + _run(" ")
        + _inline_equation(_math_run(">"))
        + _run(" 7")
        + "</a:p>"
    )
    place(slide.shapes._spTree, _alternate(_text_shape(3, body), _pictured(3, picture)))
    notes = slide.notes_slide.notes_text_frame
    notes.text = "Say the bound aloud "
    place(notes.paragraphs[0]._p, _inline_equation(_math_run("≥")))

    # 2. The same, inside a group.
    slide = presentation.slides.add_slide(title_only)
    slide.shapes.title.text = "A Grouped Bound"
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(0, 3000000, 3000000, 500000).text = "Beside the group"
    _, picture = slide.part.get_or_add_image_part(io.BytesIO(_PIXEL_PNG))
    grouped = f"<a:p>{_run('z ')}{_inline_equation(_math_run('≥'))}{_run(' 3')}</a:p>"
    place(
        group._element,
        _alternate(_text_shape(30, grouped, y=0), _pictured(30, picture, y=0)),
    )

    # 3. An equation in a shape python-pptx does read.
    slide = presentation.slides.add_slide(title_only)
    slide.shapes.title.text = "A Bare Equation"
    bare = f"<a:p>{_run('a ')}{_inline_equation(_math_run('≠'))}{_run(' b')}</a:p>"
    place(slide.shapes._spTree, _text_shape(4, bare))

    # 4. SmartArt: its words are in a part of their own.
    slide = presentation.slides.add_slide(title_only)
    slide.shapes.title.text = "A Two-Step Process"
    model = Part(
        PackURI("/ppt/diagrams/data1.xml"),
        "application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml",
        slide.part.package,
        _smartart_model(),
    )
    data = slide.part.relate_to(
        model,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData",
    )
    place(
        slide.shapes._spTree,
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="Diagram 4"/>'
        "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
        '<p:xfrm><a:off x="457200" y="1600200"/><a:ext cx="8229600" cy="3000000"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="{_OOXML["dgm"]}"><dgm:relIds r:dm="{data}" '
        'r:lo="" r:qs="" r:cs=""/></a:graphicData></a:graphic></p:graphicFrame>',
    )

    # 5. What still cannot be read: an equation the converter refuses, and an
    # embedded object. Then ink, which stays wrapped: unwrapped, python-pptx
    # fails on it. Both wrapped as PowerPoint wraps them.
    slide = presentation.slides.add_slide(title_only)
    slide.shapes.title.text = "What Stays Unread"
    refused = f"<a:p>{_run('sum ')}{_inline_equation(_REFUSED_DELIMITER)}</a:p>"
    place(slide.shapes._spTree, _text_shape(6, refused))
    _, picture = slide.part.get_or_add_image_part(io.BytesIO(_PIXEL_PNG))
    embedded = (
        '<p:oleObj name="Equation" r:id="{rid}" progId="Equation.3">{inner}</p:oleObj>'
    )
    place(
        slide.shapes._spTree,
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="7" name="Object 6"/>'
        "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
        '<p:xfrm><a:off x="457200" y="3600000"/><a:ext cx="3000000" cy="1000000"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/presentationml/2006/ole">'
        + _alternate(
            embedded.format(rid=picture, inner="<p:embed/>"),
            embedded.format(
                rid=picture,
                inner=f'<p:embed/><p:pic><p:nvPicPr><p:cNvPr id="0" name=""/><p:cNvPicPr/>'
                f'<p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="{picture}"/>'
                "<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr/></p:pic>",
            ),
            requires="v",
        )
        + "</a:graphicData></a:graphic></p:graphicFrame>",
    )
    ink = (
        f'<p:contentPart p14:bwMode="auto" r:id="{picture}"><p14:nvContentPartPr>'
        '<p14:cNvPr id="8" name="Ink 7"/><p14:cNvContentPartPr/><p14:nvPr/>'
        '</p14:nvContentPartPr><p14:xfrm><a:off x="457200" y="5000000"/>'
        '<a:ext cx="1000000" cy="500000"/></p14:xfrm></p:contentPart>'
    )
    place(
        slide.shapes._spTree,
        _alternate(ink, _pictured(8, picture, y=5000000), requires="p14"),
    )

    buffer = io.BytesIO()
    presentation.save(buffer)
    _repack(buffer.getvalue(), path)


def _word_run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _word_math_run(text: str) -> str:
    return f"<m:r><m:t>{text}</m:t></m:r>"


def write_equation_report(path: Path) -> None:
    sup = (
        "<m:sSup><m:sSupPr><m:ctrlPr/></m:sSupPr><m:e>"
        f"{_word_math_run('c')}</m:e><m:sup>{_word_math_run('2')}</m:sup></m:sSup>"
    )
    boxed = f"<w:txbxContent><w:p>{_word_run('box text ')}{_math(_REFUSED_DELIMITER)}</w:p></w:txbxContent>"
    text_box = (
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>'
        '<wp:anchor><wp:docPr id="1" name="Text Box 1"/><a:graphic>'
        f'<a:graphicData uri="{_OOXML["wps"]}"><wps:wsp><wps:txbx>{boxed}</wps:txbx>'
        "<wps:bodyPr/></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing>"
        f"</mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox>{boxed}</v:textbox>"
        "</v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    body = (
        _docx_paragraph("Equation Notes", "Heading1")
        + _docx_paragraph(SENTINELS["equation_report.docx"])
        + f"<w:p>{_word_run('inline a ')}{_math(_word_math_run('≠'))}{_word_run(' b')}</w:p>"
        + f"<w:p><m:oMathPara>{_math(_word_math_run('E=m') + sup)}</m:oMathPara></w:p>"
        + f"<w:p>{_word_run('half of x is ')}{_math(_REFUSED_FRACTION)}</w:p>"
        + text_box
        # A chart, which mammoth drops: named in a warning, not read.
        + '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="1" cy="1"/>'
        '<wp:docPr id="2" name="Chart 1"/><a:graphic>'
        f'<a:graphicData uri="{_OOXML["c"]}"><c:chart r:id="rId9"/></a:graphicData>'
        "</a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {_DECLARE}><w:body>{body}</w:body></w:document>\n"
    )
    _write_archive(
        path,
        [
            ("[Content_Types].xml", _DOCX_CONTENT_TYPES),
            ("_rels/.rels", _DOCX_RELS),
            ("word/document.xml", document),
        ],
    )


#: Where `equation_archive.zip` puts the two equation fixtures: in folders, so
#: a warning's member name is a path and not a bare file name.
EQUATION_MEMBERS = {
    "equation_deck.pptx": "slides/equation_deck.pptx",
    "equation_report.docx": "reports/equation_report.docx",
}


def write_equation_archive(path: Path) -> None:
    """The two equation fixtures, byte for byte, inside a zip (docs/09 P29).

    The same bytes, so each member's page can be held to the file's own; written
    fresh rather than copied from the fixtures beside it, so this does not
    depend on the order the fixtures are written in.
    """
    with tempfile.TemporaryDirectory() as scratch:
        members: list[tuple[str, str | bytes]] = []
        for name, member in EQUATION_MEMBERS.items():
            written = Path(scratch) / name
            WRITERS[name](written)
            members.append((member, written.read_bytes()))
    _write_archive(path, members)


# --------------------------------------------------------------------------- #
# Left out — the members a zip's reader cannot put on the page (docs/09 P33)
# --------------------------------------------------------------------------- #

#: Where the AppleDouble members say their files were downloaded from: the
#: address a capture must not put on the page.  `.invalid` never resolves.
DOWNLOADED_FROM = "https://downloads.example.invalid/week-1/notes.zip"


def _apple_double(attributes: dict[str, bytes]) -> bytes:
    """An AppleDouble file as the Finder writes one into `__MACOSX/`.

    RFC 1740's header and two entries, Finder info and an empty resource fork,
    with Apple's `ATTR` block for extended attributes after the Finder info,
    which is where a file's quarantine record and download address are kept.
    """
    names = [name.encode() + b"\0" for name in attributes]
    entries_size = sum((11 + len(name) + 3) // 4 * 4 for name in names)
    attr_start = 50 + 32 + 2  # header and two entry descriptors, Finder info, pad
    data_start = attr_start + 36 + entries_size
    data_length = sum(map(len, attributes.values()))
    total = data_start + data_length
    entries = b""
    data = b""
    for name, value in zip(names, attributes.values(), strict=True):
        entry = struct.pack(">IIHB", data_start + len(data), len(value), 0, len(name))
        entry += name
        entries += entry + b"\0" * (-len(entry) % 4)
        data += value
    attr_header = b"ATTR" + struct.pack(
        ">IIIIIIIHH", 0, total, data_start, data_length, 0, 0, 0, 0, len(names)
    )
    header = struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", 2)
    header += struct.pack(">III", 9, 50, total - 50)  # Finder info, with ATTR
    header += struct.pack(">III", 2, total, 0)  # resource fork, empty
    return header + b"TEXTttxt" + b"\0" * 24 + b"\0\0" + attr_header + entries + data


def _flagged_encrypted(data: bytes, name: str) -> bytes:
    """`data` with `name`'s encryption bit set in both of its headers.

    zipfile writes no encrypted member, and an archive tool would write
    different bytes on every run.  A reader decides by this bit before it reads
    the member, and so does WebShot, so the member's bytes can stay as written.
    """
    patched = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo(name)
        offset = archive.start_dir
    patched[entry.header_offset + 6] |= 0x1
    while patched[offset : offset + 4] == b"PK\x01\x02":
        lengths = struct.unpack_from("<HHH", patched, offset + 28)
        if patched[offset + 46 : offset + 46 + lengths[0]] == name.encode():
            patched[offset + 8] |= 0x1
            return bytes(patched)
        offset += 46 + sum(lengths)
    raise ValueError(f"{name} is not in the central directory")


def write_left_out_archive(path: Path) -> None:
    """One member of each kind a zip's reader leaves out, beside one it reads.

    A directory entry, a format nothing reads, a PDF (MarkItDown reads one, but
    with a package no WebShot extra installs), a Word file that is not one, an
    encrypted member, and two AppleDouble members, one of which records a
    download address.  `report.docx` is read with the office extra and left
    out, with WebShot's own advice, without it.
    """
    report = _docx_paragraph("Left-out archive report", "Heading1")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{report}</w:body></w:document>\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        docx = Path(scratch) / "report.docx"
        _write_archive(
            docx,
            [
                ("[Content_Types].xml", _DOCX_CONTENT_TYPES),
                ("_rels/.rels", _DOCX_RELS),
                ("word/document.xml", document),
            ],
        )
        report_bytes = docx.read_bytes()
    where_from = plistlib.dumps([DOWNLOADED_FROM], fmt=plistlib.FMT_BINARY)
    members: list[tuple[str, str | bytes]] = [
        ("notes/", b""),
        ("notes/kept.txt", f"Kept notes\n\n{SENTINELS['left_out_archive.zip']}\n"),
        ("report.docx", report_bytes),
        ("figure.bin", bytes(range(256))),
        ("scan.pdf", _tiny_pdf("Left-out scan")),
        ("broken.docx", b"PK\x03\x04" + bytes(range(256))),
        ("secret.txt", "Encrypted notes\n"),
        ("__MACOSX/", b""),
        ("__MACOSX/notes/", b""),
        (
            "__MACOSX/notes/._kept.txt",
            _apple_double(
                {
                    "com.apple.metadata:kMDItemWhereFroms": where_from,
                    "com.apple.quarantine": b"0083;00000000;Safari;",
                }
            ),
        ),
        ("__MACOSX/._report.docx", _apple_double({})),
    ]
    _write_archive(path, members)
    path.write_bytes(_flagged_encrypted(path.read_bytes(), "secret.txt"))


def _tiny_pdf(text: str) -> bytes:
    """A one-page PDF of `text`, its cross-reference table offsets computed."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, content)
    table = b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return (
        body
        + b"xref\n0 %d\n0000000000 65535 f \n%s" % (len(objects) + 1, table)
        + b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
        + b"startxref\n%d\n%%%%EOF\n" % len(body)
    )


# --------------------------------------------------------------------------- #
# Markup — text a document holds that HTML or Markdown would read as structure
# --------------------------------------------------------------------------- #

#: Where an image would be fetched from if a document's text reached the page
#: as markup.  `.invalid` never resolves (RFC 2606), so a capture that tried
#: says so as a failed request, and nothing here can reach a real host.
BEACON = "https://tracker.invalid"

#: An alt text and a link target that close the Markdown syntax holding them
#: and open an image of their own.
BREAKOUT_ALT = f"a chart]({BEACON}/alt.png) ![x"
BREAKOUT_HREF = f"https://example.invalid/?q=)![x]({BEACON}/href.png"
#: A code block whose own line closes markdownify's three-backtick fence.
BREAKOUT_PRE = f'one\n```\n<img src="{BEACON}/pre.png">\n```\ntwo'


def markup_lines(where: str) -> list[str]:
    """Lines of text that are markup to HTML or to Python-Markdown."""
    return [
        f'<img src="{BEACON}/{where}.png"> and *stars*',
        "# Not a heading",
        f"[a link](https://example.invalid/{where}) and ![an image]({BEACON}/{where}-md.png)",
        "3. Third, as typed",
        "Total",
        "===",
        "Term",
        ": not a definition",
        "~~~",
        "Set {x}",
        "C:\\Users\\_x_ and a\\*b and __init__",
        "&copy; as typed",
    ]


def _without_clock(data: bytes) -> bytes:
    """A python-pptx or openpyxl package, with its clock readings fixed.

    Both stamp the save time into `docProps/core.xml`, and python-pptx embeds a
    chart's data as an XlsxWriter workbook that does the same, so the zip's
    timestamps are not the only thing that would change on every run.
    """
    stamp = re.compile(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:)")
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            payload = source.read(info)
            if info.filename.endswith("docProps/core.xml"):
                payload = stamp.sub(rb"\g<1>2026-01-01T00:00:00Z\g<2>", payload)
            elif info.filename.endswith(".xlsx"):
                payload = _without_clock(payload)
            entry = zipfile.ZipInfo(info.filename, date_time=FIXED_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            target.writestr(entry, payload)
    return output.getvalue()


def write_markup_deck(path: Path) -> None:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = f'<img src="{BEACON}/title.png"> and *title*'
    box = slide.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(9), Inches(3))
    lines = [SENTINELS["markup_deck.pptx"], *markup_lines("slide")]
    # A blank line, then a tab: Markdown's indented code block.
    lines += ["", "\tIndented, not code"]
    box.text_frame.text = lines[0]
    for line in lines[1:]:
        box.text_frame.add_paragraph().text = line
    table = slide.shapes.add_table(
        2, 2, Inches(0.5), Inches(4.6), Inches(9), Inches(0.8)
    ).table
    for column, (head, cell) in enumerate(
        [("Header", f'<img src="{BEACON}/cell.png">'), ("Other", "# cell")]
    ):
        table.cell(0, column).text = head
        table.cell(1, column).text = cell
    data = CategoryChartData()
    data.categories = [f'<img src="{BEACON}/category.png">']
    data.add_series("<b>series</b>", (1,))
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(0.5),
        Inches(5.5),
        Inches(4),
        Inches(1.5),
        data,
    ).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = "[chart](https://example.invalid/chart) _x_"
    slide.notes_slide.notes_text_frame.text = f'<img src="{BEACON}/notes.png"> *notes*'
    buffer = io.BytesIO()
    presentation.save(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_without_clock(buffer.getvalue()))


_MARKUP_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""


def write_markup_report(path: Path) -> None:
    from xml.sax.saxutils import escape, quoteattr

    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
        f' Target={quoteattr(BREAKOUT_HREF)} TargetMode="External"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"'
        ' Target="media/image1.png"/></Relationships>\n'
    )
    picture = (
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="9525" cy="9525"/>'
        f'<wp:docPr id="1" name="Picture 1" descr={quoteattr(BREAKOUT_ALT)}/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:nvPicPr><pic:cNvPr id="0" name="pixel.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId3"/></pic:blipFill><pic:spPr/></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )
    body = (
        _docx_paragraph(SENTINELS["markup_report.docx"])
        + "".join(_docx_paragraph(escape(line)) for line in markup_lines("word"))
        + f'<w:p><w:hyperlink r:id="rId2">{_word_run("a link out")}</w:hyperlink></w:p>'
        + picture
        + "<w:tbl><w:tblPr/>"
        + _docx_row(["Header", "Other"])
        + _docx_row([escape(f'<img src="{BEACON}/cell.png">'), "# cell"])
        + "</w:tbl>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {_DECLARE}><w:body>{body}</w:body></w:document>\n"
    )
    _write_archive(
        path,
        [
            ("[Content_Types].xml", _MARKUP_DOCX_CONTENT_TYPES),
            ("_rels/.rels", _DOCX_RELS),
            ("word/document.xml", document),
            ("word/_rels/document.xml.rels", relationships),
            ("word/media/image1.png", _PIXEL_PNG),
        ],
    )


#: A sheet name is a heading MarkItDown writes itself.  Excel forbids
#: `[]:*?/\` in one, which still leaves HTML and most of Markdown.
MARKUP_SHEET = "<i>Q1 #2 _x_ & {y}"


def write_markup_workbook(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = MARKUP_SHEET
    rows = [["Where", "Text"], ["sentinel", SENTINELS["markup_workbook.xlsx"]]]
    rows += [
        [f"line {number}", line] for number, line in enumerate(markup_lines("cell"))
    ]
    rows.append(["formula text", "=SUM(A1)"])
    for row in rows:
        sheet.append(row)
        # openpyxl takes a leading `=` for a formula; these cells hold the text.
        for cell in sheet[sheet.max_row]:
            cell.data_type = "s"
    buffer = io.BytesIO()
    workbook.save(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_without_clock(buffer.getvalue()))


#: The book's own picture, inline: a file beside it would not be beside the
#: page WebShot renders, and its failed request would read as the text's.
_PIXEL_URI = "data:image/png;base64," + base64.b64encode(_PIXEL_PNG).decode()


def write_markup_book(path: Path) -> None:
    from xml.sax.saxutils import escape, quoteattr

    title = escape(f"<b>Markup</b> *book* & {{{SENTINELS['markup_book.epub']}}}")
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:webshot-markup-fixture</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator># Creator</dc:creator>
    <dc:description>{escape(f'<img src="{BEACON}/description.png">')}</dc:description>
    <dc:language>en</dc:language>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in markup_lines("chapter"))
    chapter = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>
<body>
  <h1>Chapter One</h1>
  <p>{SENTINELS["markup_book.epub"]}</p>
  {paragraphs}
  <p>A sentence that ends robots!<a href="#note">1</a></p>
  <p><a href={quoteattr(BREAKOUT_HREF)}>a link out</a></p>
  <p><img src="{_PIXEL_URI}" alt={quoteattr(BREAKOUT_ALT)}/></p>
  <pre>{escape(BREAKOUT_PRE)}</pre>
  <p id="note">1. The note.</p>
</body></html>
"""
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", opf),
            ("OEBPS/chapter.xhtml", chapter),
        ],
        first_stored=True,
    )


def _csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def write_markup_archive(path: Path) -> None:
    lines = [SENTINELS["markup_archive.zip"], *markup_lines("member")]
    cells = [
        f'<img src="{BEACON}/csv.png">',
        "=SUM(A1)",
        "~approx",
        "Set {x}",
        "C:\\Users\\_x_",
    ]
    _write_archive(
        path,
        [
            ("notes/markup.txt", "\n".join(lines) + "\n"),
            ("data/markup.csv", _csv([["Cell"], *([cell] for cell in cells)])),
            ("data/markup.tsv", "Cell\tOther\n" + f'<img src="{BEACON}/tsv.png">\t#\n'),
            # A member's name is text the archive's reader writes as a heading.
            ("notes/<img src=https:tracker.invalid>.txt", "named\n"),
        ],
    )


# --------------------------------------------------------------------------- #
# Lists side by side (docs/09 P32)
# --------------------------------------------------------------------------- #

#: Each pair of lists, by what stands between them in the chapter.  Python-
#: Markdown read every pair but the last as one list, until the bridge kept
#: them apart; the last is a bulleted list and a numbered one, which it never
#: joined.  The words are made up.
ADJACENT_LISTS = {
    "Two numbered lists": (
        '<ol start="5"><li>Brallow</li><li>Crindet</li></ol>'
        "<ol><li>Dulvane</li><li>Fenwisk</li></ol>"
    ),
    "Two bulleted lists": (
        "<ul><li>Glorvet</li><li>Hustrel</li></ul><ul><li>Imbrisk</li></ul>"
    ),
    "Nothing that shows between them": (
        "<ol><li>Jomvet</li></ol>\n  <div></div><p></p><!-- a note --><br/>"
        "<ol><li>Kravish</li></ol>"
    ),
    "Each in a division of its own": (
        "<div><ol><li>Lomtrel</li></ol></div><div><ol><li>Morvisk</li></ol></div>"
    ),
    "Inside one quotation": (
        "<blockquote><ul><li>Nuvrel</li></ul><ul><li>Opvane</li></ul></blockquote>"
    ),
    "A bulleted list, then a numbered one": (
        "<ul><li>Plindex</li></ul><ol><li>Quorvel</li></ol>"
    ),
}


#: The book's lists as its page holds them, in order: each tag and its items.
ADJACENT_LISTS_PAGE = [
    ("ol", ["Brallow", "Crindet"]),
    ("ol", ["Dulvane", "Fenwisk"]),
    ("ul", ["Glorvet", "Hustrel"]),
    ("ul", ["Imbrisk"]),
    ("ol", ["Jomvet"]),
    ("ol", ["Kravish"]),
    ("ol", ["Lomtrel"]),
    ("ol", ["Morvisk"]),
    ("ul", ["Nuvrel"]),
    ("ul", ["Opvane"]),
    ("ul", ["Plindex"]),
    ("ol", ["Quorvel"]),
    ("ol", ["Rendisk"]),
    ("ol", ["Stumbrel"]),
]


def write_adjacent_lists_book(path: Path) -> None:
    """Pairs of lists with nothing but markup between them, in two chapters.

    The first chapter ends with a list and the second begins with one:
    MarkItDown converts each chapter on its own and joins them with a blank
    line, so the two met only once the book was whole.
    """
    sections = "".join(
        f"<h2>{heading}</h2>{lists}" for heading, lists in ADJACENT_LISTS.items()
    )
    first = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lists</title></head>
<body>
  <h1>Lists Side by Side</h1>
  <p>{SENTINELS["adjacent_lists_book.epub"]}</p>
  {sections}
  <h2>Across two chapters</h2>
  <ol><li>Rendisk</li></ol>
</body></html>
"""
    second = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>More</title></head>
<body>
  <ol><li>Stumbrel</li></ol>
  <p>The second chapter goes on after its list.</p>
</body></html>
"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:webshot-adjacent-lists-fixture</dc:identifier>
    <dc:title>Lists Side by Side</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>
    <item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="one"/><itemref idref="two"/></spine>
</package>
"""
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", opf),
            ("OEBPS/one.xhtml", first),
            ("OEBPS/two.xhtml", second),
        ],
        first_stored=True,
    )


# --------------------------------------------------------------------------- #
# Pictures — held, convertible, undrawable, linked, and named but missing
# --------------------------------------------------------------------------- #

#: A picture's alt text, which a placeholder and a warning both quote.
PICTURE_ALTS = {
    "png": "A blue rectangle",
    "tiff": "A green rectangle, stored as TIFF",
    "metafile": "A red rectangle, stored as a metafile",
    "linked": "A picture the deck only links to",
    "missing": "A picture the book names but does not hold",
}


def _picture(rgb: tuple[int, int, int], kind: str) -> bytes:
    """A small solid rectangle, in `kind` (`PNG` or `TIFF`), the same each run."""
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (120, 80), rgb).save(output, kind)
    return output.getvalue()


#: The smallest Windows Metafile Pillow and python-pptx take for one: a
#: placeable header, the metafile header and its end record, and nothing
#: drawn.  Nothing here draws a metafile, so the type is what the fixture is
#: for.  Real decks hold WMF clip art and Equation Editor previews (docs/09 P36).
_METAFILE = (
    struct.pack("<IHhhhhHIH", 0x9AC6CDD7, 0, 0, 0, 1200, 800, 1440, 0, 0)
    + struct.pack("<HHHIHIH", 1, 9, 0x300, 12, 0, 3, 0)
    + struct.pack("<IH", 3, 0)
)


def write_picture_deck(path: Path) -> None:
    from pptx import Presentation
    from pptx.opc.constants import RELATIONSHIP_TYPE
    from pptx.oxml.ns import qn
    from pptx.util import Inches

    presentation = Presentation()

    def picture(slide: Any, data: bytes, alt: str, left: float) -> Any:
        shape = slide.shapes.add_picture(
            io.BytesIO(data), Inches(left), Inches(2.5), Inches(2.5), Inches(1.7)
        )
        shape._element.nvPicPr.cNvPr.set("descr", alt)
        return shape

    held = presentation.slides.add_slide(presentation.slide_layouts[5])
    held.shapes.title.text = "Pictures the deck holds"
    box = held.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(9), Inches(0.6))
    box.text_frame.text = SENTINELS["picture_deck.pptx"]
    picture(held, _picture((20, 60, 200), "PNG"), PICTURE_ALTS["png"], 0.5)
    picture(held, _picture((20, 160, 60), "TIFF"), PICTURE_ALTS["tiff"], 3.5)

    unread = presentation.slides.add_slide(presentation.slide_layouts[5])
    unread.shapes.title.text = "Pictures the page cannot draw"
    picture(unread, _METAFILE, PICTURE_ALTS["metafile"], 0.5)
    # A linked picture: the blip names an external target and holds nothing.
    linked = picture(unread, _picture((90, 90, 90), "PNG"), PICTURE_ALTS["linked"], 3.5)
    blip = linked._element.blipFill.blip
    target = unread.part.relate_to(
        "https://example.invalid/linked.png", RELATIONSHIP_TYPE.IMAGE, is_external=True
    )
    del blip.attrib[qn("r:embed")]
    blip.set(qn("r:link"), target)

    buffer = io.BytesIO()
    presentation.save(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_without_clock(buffer.getvalue()))


def write_picture_report(path: Path) -> None:
    def drawing(number: int, alt: str) -> str:
        return (
            f'<w:p><w:r><w:drawing><wp:inline><wp:extent cx="1143000" cy="762000"/>'
            f'<wp:docPr id="{number}" name="Picture {number}" descr="{alt}"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="0" name="picture{number}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="rId{number}"/></pic:blipFill><pic:spPr/></pic:pic>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    media = [
        ("image1.png", _picture((20, 60, 200), "PNG"), PICTURE_ALTS["png"]),
        ("image2.tiff", _picture((20, 160, 60), "TIFF"), PICTURE_ALTS["tiff"]),
        ("image3.emf", _METAFILE, PICTURE_ALTS["metafile"]),
    ]
    body = _docx_paragraph(SENTINELS["picture_report.docx"]) + "".join(
        drawing(number, alt) for number, (_, _, alt) in enumerate(media, start=1)
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {_DECLARE}><w:body>{body}</w:body></w:document>\n"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{number}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/image" Target="media/{name}"/>'
            for number, (name, _, _) in enumerate(media, start=1)
        )
        + "</Relationships>\n"
    )
    content_types = _DOCX_CONTENT_TYPES.replace(
        "  <Override",
        '  <Default Extension="png" ContentType="image/png"/>\n'
        '  <Default Extension="tiff" ContentType="image/tiff"/>\n'
        '  <Default Extension="emf" ContentType="image/x-emf"/>\n'
        "  <Override",
        1,
    )
    _write_archive(
        path,
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", _DOCX_RELS),
            ("word/document.xml", document),
            ("word/_rels/document.xml.rels", relationships),
            *((f"word/media/{name}", data) for name, data, _ in media),
        ],
    )


def write_picture_book(path: Path) -> None:
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:webshot-picture-fixture</dc:identifier>
    <dc:title>The Picture Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="blue" href="images/blue.png" media-type="image/png"/>
    <item id="green" href="images/green.tif" media-type="image/tiff"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    # The chapter sits a folder down, so its pictures are named `../images/…`.
    chapter = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>
<body>
  <h1>Chapter One</h1>
  <p>{SENTINELS["picture_book.epub"]}</p>
  <p><img src="../images/blue.png" alt="{PICTURE_ALTS["png"]}"/></p>
  <p><img src="../images/green.tif" alt="{PICTURE_ALTS["tiff"]}"/></p>
  <p><img src="../images/missing.png" alt="{PICTURE_ALTS["missing"]}"/></p>
</body></html>
"""
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", opf),
            ("OEBPS/text/chapter.xhtml", chapter),
            ("OEBPS/images/blue.png", _picture((20, 60, 200), "PNG")),
            ("OEBPS/images/green.tif", _picture((20, 160, 60), "TIFF")),
        ],
        first_stored=True,
    )


WRITERS = {
    "sample_report.docx": write_docx,
    "sample_deck.pptx": write_pptx,
    "sample_workbook.xlsx": write_xlsx,
    "sample_book.epub": write_epub,
    "numbered_book.epub": write_numbered_epub,
    "sample_archive.zip": write_zip,
    "equation_deck.pptx": write_equation_deck,
    "equation_report.docx": write_equation_report,
    "equation_archive.zip": write_equation_archive,
    "markup_deck.pptx": write_markup_deck,
    "markup_report.docx": write_markup_report,
    "markup_workbook.xlsx": write_markup_workbook,
    "markup_book.epub": write_markup_book,
    "markup_archive.zip": write_markup_archive,
    "left_out_archive.zip": write_left_out_archive,
    "adjacent_lists_book.epub": write_adjacent_lists_book,
    "picture_deck.pptx": write_picture_deck,
    "picture_report.docx": write_picture_report,
    "picture_book.epub": write_picture_book,
}


def main() -> int:
    for name, writer in WRITERS.items():
        destination = FIXTURE_ROOT / name
        writer(destination)
        print(f"wrote {destination.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
