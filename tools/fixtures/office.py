#!/usr/bin/env python3
"""Regenerate the office-format fixtures under `tests/fixtures/office/`.

    uv run --extra office python tools/fixtures/office.py

A committed `.docx` is a zip full of XML: unreviewable in a diff, and
unmaintainable once whoever made it has moved on.  This script is the fixture —
the files it writes are build output that happens to be committed so the test
suite does not need the office extra installed to *exist*, only to run.

Each fixture carries a sentinel string that occurs nowhere else in
the repository, so `tests/test_office_formats.py` can prove the text reached
the bundle through the converter rather than through a filename.

DOCX and EPUB are written by hand, because the packages that read them
(mammoth, MarkItDown's EPUB converter) cannot write them and pulling in a
writer for each would add dependencies no shipped code path uses.  Both
formats are small, declared containers; the XML below is the minimum each
specification requires.  PPTX and XLSX use python-pptx and openpyxl, which
`webshot[office]` already installs to read them.

The two `equation_*` fixtures hold what those readers pass over (docs/09 P27):
an equation, `mc:AlternateContent`, SmartArt, an embedded object, and an
equation MarkItDown's LaTeX converter refuses.  Every word in them is made up.
"""

from __future__ import annotations

import io
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
    "sample_archive.zip": "SENTINEL ZIP MEMBER OKAPI",
    # Inside the text box PowerPoint wraps in `mc:AlternateContent`, so it
    # reaches the bundle only if that box does.
    "equation_deck.pptx": "SENTINEL PPTX EQUATION QUOKKA",
    "equation_report.docx": "SENTINEL DOCX EQUATION WOMBAT",
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


WRITERS = {
    "sample_report.docx": write_docx,
    "sample_deck.pptx": write_pptx,
    "sample_workbook.xlsx": write_xlsx,
    "sample_book.epub": write_epub,
    "sample_archive.zip": write_zip,
    "equation_deck.pptx": write_equation_deck,
    "equation_report.docx": write_equation_report,
}


def main() -> int:
    for name, writer in WRITERS.items():
        destination = FIXTURE_ROOT / name
        writer(destination)
        print(f"wrote {destination.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
