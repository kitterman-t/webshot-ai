#!/usr/bin/env python3
"""Regenerate the office-format fixtures under `tests/fixtures/office/`.

    uv run --extra office python tools/fixtures/office.py

A committed `.docx` is a zip full of XML: unreviewable in a diff, and
unmaintainable once whoever made it has moved on.  This script is the fixture —
the files it writes are build output that happens to be committed so the test
suite does not need the office extra installed to *exist*, only to run.

Each fixture carries a per-format sentinel string that occurs nowhere else in
the repository, so `tests/test_office_formats.py` can prove the text reached
the bundle through the converter rather than through a filename.

DOCX and EPUB are written by hand, because the packages that read them
(mammoth, MarkItDown's EPUB converter) cannot write them and pulling in a
writer for each would add dependencies no shipped code path uses.  Both
formats are small, declared containers; the XML below is the minimum each
specification requires.  PPTX and XLSX use python-pptx and openpyxl, which
`webshot[office]` already installs to read them.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: The strings the smoke test looks for. One per format, unmistakable in a
#: grep, and never written anywhere but into the document body.
SENTINELS = {
    "docx": "SENTINEL DOCX WORD MARMOSET",
    "pptx": "SENTINEL PPTX SLIDE PANGOLIN",
    "xlsx": "SENTINEL XLSX CELL AXOLOTL",
    "epub": "SENTINEL EPUB CHAPTER NARWHAL",
    "zip": "SENTINEL ZIP MEMBER OKAPI",
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
        + _docx_paragraph(SENTINELS["docx"])
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
  <p>{SENTINELS["epub"]}</p>
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
            ("notes/summary.txt", f"Field notes\n\n{SENTINELS['zip']}\n"),
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
    slide.placeholders[1].text = SENTINELS["pptx"]
    path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(path))


def write_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Regions"
    sheet.append(["Region", "Revenue", "Note"])
    sheet.append(["North", 1250000, SENTINELS["xlsx"]])
    sheet.append(["South", 980000, "steady"])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))


WRITERS = {
    "sample_report.docx": write_docx,
    "sample_deck.pptx": write_pptx,
    "sample_workbook.xlsx": write_xlsx,
    "sample_book.epub": write_epub,
    "sample_archive.zip": write_zip,
}


def main() -> int:
    for name, writer in WRITERS.items():
        destination = FIXTURE_ROOT / name
        writer(destination)
        print(f"wrote {destination.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
