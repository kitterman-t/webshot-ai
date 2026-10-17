"""Office-format inputs behind `webshot[office]` (docs/05 task 3.2).

One smoke test per format, and it is deliberately end-to-end: capture the
fixture, validate the published bundle against the frozen contract models, and
find the fixture's sentinel in the semantic artifacts.  A converter that ran
but produced nothing would pass a "did it crash" test and fail this one.

The sentinels are written into the documents by `tools/fixtures/office.py`,
which is also what regenerates the fixtures — they are not opaque binaries.

DOCX, PPTX and XLSX skip when their converter's dependencies are absent, which
is what an install without the extra looks like.  EPUB and ZIP need no extra:
MarkItDown reads both from its base install.
"""

from __future__ import annotations

import html
import importlib.util
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from webshot.bundle.manifest import AssetsFile, QaReport, WebChunkV3, WebManifestV3

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: (fixture, source kind, sentinel, module whose absence means "no extra").
CASES = (
    ("sample_report.docx", "docx", "SENTINEL DOCX WORD MARMOSET", "mammoth"),
    ("sample_deck.pptx", "pptx", "SENTINEL PPTX SLIDE PANGOLIN", "pptx"),
    ("sample_workbook.xlsx", "xlsx", "SENTINEL XLSX CELL AXOLOTL", "openpyxl"),
    ("sample_book.epub", "epub", "SENTINEL EPUB CHAPTER NARWHAL", None),
    ("numbered_book.epub", "epub", "SENTINEL EPUB NUMBERED KINKAJOU", None),
    ("sample_archive.zip", "zip", "SENTINEL ZIP MEMBER OKAPI", None),
    ("equation_deck.pptx", "pptx", "SENTINEL PPTX EQUATION QUOKKA", "pptx"),
    ("equation_report.docx", "docx", "SENTINEL DOCX EQUATION WOMBAT", "mammoth"),
    ("equation_archive.zip", "zip", "SENTINEL PPTX EQUATION QUOKKA", "pptx"),
    ("markup_deck.pptx", "pptx", "SENTINEL PPTX MARKUP CASSOWARY", "pptx"),
    ("markup_report.docx", "docx", "SENTINEL DOCX MARKUP DUGONG", "mammoth"),
    ("markup_workbook.xlsx", "xlsx", "SENTINEL XLSX MARKUP GECKO", "openpyxl"),
    ("markup_book.epub", "epub", "SENTINEL EPUB MARKUP IBEX", None),
    ("markup_archive.zip", "zip", "SENTINEL ZIP MARKUP JERBOA", None),
    # `mammoth`, because its `report.docx` is read with the office extra and
    # named without it: the two are different warnings.
    ("left_out_archive.zip", "zip", "SENTINEL ZIP KEPT KINKAJOU", "mammoth"),
    ("adjacent_lists_book.epub", "epub", "SENTINEL EPUB LISTS TAMARIN", None),
    ("picture_deck.pptx", "pptx", "SENTINEL PPTX PICTURE KOALA", "pptx"),
    ("picture_report.docx", "docx", "SENTINEL DOCX PICTURE LEMUR", "mammoth"),
    ("picture_book.epub", "epub", "SENTINEL EPUB PICTURE MANATEE", None),
    # Each sentinel is past MarkItDown's 64 KB sample (docs/09 P35).
    (
        "late_utf8.csv",
        "csv",
        "SENTINEL CSV LATE \N{LATIN CAPITAL LETTER N WITH TILDE}AND\N{LATIN CAPITAL LETTER U WITH ACUTE}",
        None,
    ),
    (
        "late_utf8_archive.zip",
        "zip",
        "SENTINEL ZIP LATE P\N{LATIN CAPITAL LETTER E WITH ACUTE}CARI",
        None,
    ),
    ("nested_book.epub", "epub", "SENTINEL EPUB NESTED CAPYBARA", None),
    ("nested_report.docx", "docx", "SENTINEL DOCX NESTED BINTURONG", "mammoth"),
    ("nested_archive.zip", "zip", "SENTINEL ZIP NESTED FOSSA", "mammoth"),
    ("code_items_book.epub", "epub", "SENTINEL EPUB CODE GENET", None),
    ("code_items_report.docx", "docx", "SENTINEL DOCX CODE HYRAX", "mammoth"),
    ("code_items_archive.zip", "zip", "SENTINEL ZIP CODE ICHNEUMON", "mammoth"),
    ("definition_book.epub", "epub", "SENTINEL EPUB DEFINITION MARGAY", None),
    ("definition_archive.zip", "zip", "SENTINEL ZIP DEFINITION SERVAL", None),
    ("inline_code_book.epub", "epub", "SENTINEL EPUB INLINE NUMBAT", None),
    ("inline_code_report.docx", "docx", "SENTINEL DOCX INLINE OLINGO", "mammoth"),
    ("inline_code_archive.zip", "zip", "SENTINEL ZIP INLINE QUOLL", "mammoth"),
    ("quoted_code_book.epub", "epub", "SENTINEL EPUB QUOTED JACANA", None),
    ("quoted_code_report.docx", "docx", "SENTINEL DOCX QUOTED LORIS", "mammoth"),
    ("quoted_code_archive.zip", "zip", "SENTINEL ZIP QUOTED MARGAY", "mammoth"),
    ("edge_space_report.docx", "docx", "SENTINEL DOCX EDGE SPACE LORIS", "mammoth"),
    ("edge_space_book.epub", "epub", "SENTINEL EPUB EDGE SPACE MARGAY", None),
    ("edge_space_archive.zip", "zip", "SENTINEL ZIP EDGE SPACE NUMBAT", None),
    (
        "whitespace_run_report.docx",
        "docx",
        "SENTINEL DOCX WHITESPACE RUN SERVAL",
        "mammoth",
    ),
    ("whitespace_run_book.epub", "epub", "SENTINEL EPUB WHITESPACE RUN TAPIR", None),
    ("whitespace_run_archive.zip", "zip", "SENTINEL ZIP WHITESPACE RUN VICUNA", None),
    ("adjacent_blocks_book.epub", "epub", "SENTINEL EPUB BLOCKS LORIS", None),
    ("adjacent_blocks_report.docx", "docx", "SENTINEL DOCX BLOCKS MARGAY", "mammoth"),
    # `mammoth`, because the report inside it is read with the office extra.
    ("adjacent_blocks_archive.zip", "zip", "SENTINEL ZIP BLOCKS NUMBAT", "mammoth"),
)

#: The warnings each fixture's manifest must carry: what the converter could
#: not carry onto the page (docs/09 P27). A fixture not named here must have
#: no warning that names a slide or the document body, in a member or not.
EXPECTED_WARNINGS = {
    "equation_deck.pptx": [
        "Slide 5: an equation could not be converted to LaTeX, so its "
        "characters are given in document order without its layout ('a+b').",
        "Slide 5: an embedded object (Equation.3) is not in the page.",
    ],
    "equation_report.docx": [
        "The document body: a chart is not in the page.",
        "The document body: an equation could not be converted to LaTeX, so "
        "its characters are given in document order without its layout ('x2').",
        "The document body: an equation could not be converted to LaTeX, so "
        "its characters are given in document order without its layout ('a+b').",
    ],
}
# The same two files inside a zip, each warning under its member's name
# (docs/09 P29).
EXPECTED_WARNINGS["equation_archive.zip"] = [
    f"{member}: {warning}"
    for name, member in (
        ("equation_deck.pptx", "slides/equation_deck.pptx"),
        ("equation_report.docx", "reports/equation_report.docx"),
    )
    for warning in EXPECTED_WARNINGS[name]
]

# A picture the page cannot draw, or that its file names but does not hold,
# is a line of text and one of these (docs/09 P36).
EXPECTED_WARNINGS["picture_deck.pptx"] = [
    "Slide 2: a picture (\u201cA red rectangle, stored as a metafile\u201d) is not "
    "in the page: a Windows Metafile (WMF), which a browser cannot draw.",
    "Slide 2: a picture (\u201cA picture the deck only links to\u201d) is not in "
    "the page: the deck links to it rather than holding it.",
]
EXPECTED_WARNINGS["picture_report.docx"] = [
    "The document: a picture (\u201cA red rectangle, stored as a metafile\u201d) is "
    "not in the page: an Enhanced Metafile (EMF), which a browser cannot draw.",
]
EXPECTED_WARNINGS["picture_book.epub"] = [
    "OEBPS/text/chapter.xhtml: a picture (\u201cA picture the book names but does "
    "not hold\u201d) is not in the page: it names ../images/missing.png, which the "
    "book does not hold.",
]

#: How many warnings name the members a zip fixture's reader left out
#: (docs/09 P33): the encrypted one, the Word file that is not one, the two
#: nothing reads, and the two AppleDouble members.
LEFT_OUT = {"left_out_archive.zip": 4}

#: A warning that says what a deck's, a Word file's or a book's reader could
#: not carry, whether the file was captured on its own or as a member of a zip.
CONVERSION_WARNING = re.compile(
    r"^(?:[^:]+\.(?:pptx|docx): )?"
    r"(?:Slide |The notes of slide |The document(?: body)?: |[^:\s]+\.x?html: )"
)

#: The pictures each `picture_*` fixture's page draws, by alt text, in order.
DRAWN_PICTURES = {
    "picture_deck.pptx": ["A blue rectangle", "A green rectangle, stored as TIFF"],
    "picture_report.docx": ["A blue rectangle", "A green rectangle, stored as TIFF"],
    "picture_book.epub": ["A blue rectangle", "A green rectangle, stored as TIFF"],
}


def _capture(fixture: Path, workdir: Path) -> tuple[Path, Path]:
    pdf = workdir / f"{fixture.stem}.pdf"
    report = workdir / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            str(fixture),
            "--output",
            str(pdf),
            "--report",
            str(report),
            # These documents carry no rasters, so recognition has nothing to
            # do and its absence must not make the test environment-dependent.
            "--no-ocr",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return pdf, report


@pytest.mark.browser
@pytest.mark.parametrize(
    ("name", "kind", "sentinel", "requires"), CASES, ids=[case[1] for case in CASES]
)
def test_office_fixture_round_trips(
    tmp_path: Path, name: str, kind: str, sentinel: str, requires: str | None
) -> None:
    if requires and importlib.util.find_spec(requires) is None:
        pytest.skip(f"{requires} is absent — install webshot[office] to run this")
    fixture = FIXTURE_ROOT / name
    assert fixture.is_file(), "run tools/fixtures/office.py to write the fixtures"

    pdf, report = _capture(fixture, tmp_path)
    bundle = pdf.with_suffix(".ai")

    assert pdf.is_file() and pdf.stat().st_size > 0

    # The bundle is a valid bundle...
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    WebManifestV3.model_validate(manifest)
    AssetsFile.model_validate_json((bundle / "assets.json").read_text("utf-8"))
    chunk_lines = (bundle / "chunks.jsonl").read_text("utf-8").splitlines()
    for line in chunk_lines:
        WebChunkV3.model_validate_json(line)
    QaReport.model_validate_json(report.read_text("utf-8"))

    # ...the run recorded what it read...
    assert json.loads(report.read_text("utf-8"))["source_kind"] == kind
    assert (bundle / "source" / name).is_file(), "the input is preserved verbatim"

    # ...and the document's own words came through the converter.
    for artifact in ("content.md", "content.txt", "chunks.jsonl"):
        assert sentinel in (bundle / artifact).read_text("utf-8"), (
            f"{artifact} does not contain {sentinel!r}"
        )
    assert chunk_lines, "a document with text must produce at least one chunk"

    # ...and what it could not read is said, in the order it was found.
    conversion = [
        warning for warning in manifest["warnings"] if CONVERSION_WARNING.match(warning)
    ]
    assert conversion == EXPECTED_WARNINGS.get(name, []), manifest["warnings"]

    # ...and text that names a URL stayed text. Each `markup_*` fixture names
    # `.invalid` images in every place its reader takes text from; a capture
    # that rendered one as an image tried to fetch it, and a host that never
    # resolves makes every such attempt a failed request (docs/09 P28).
    if name.startswith("markup_"):
        assert json.loads(report.read_text("utf-8"))["failed_requests"] == []

    # ...and a picture is the file's own, drawn from its bytes, never a file of
    # the same name beside it: no request goes out, and `assets.json` records
    # the picture's `data:` URL by its type and length, since the bytes are in
    # `source/` (docs/09 P36).
    if name.startswith("picture_"):
        assert json.loads(report.read_text("utf-8"))["failed_requests"] == []
        visuals = json.loads((bundle / "assets.json").read_text("utf-8"))[
            "visual_assets"
        ]
        assert [asset["alt"] for asset in visuals] == DRAWN_PICTURES[name]
        for asset in visuals:
            assert asset["source_url"].startswith("data:image/png;base64,\u2026 (")
            assert asset["source_url"].endswith(
                " characters; the picture is in source/)"
            )

    # ...and a member the zip's reader left out is named, as the bridge names
    # it here (`tests/test_archive_members.py` holds the words), while an
    # AppleDouble member's download address stays off the page (docs/09 P33).
    if kind == "zip":
        from webshot.acquire.markitdown_bridge import to_markdown

        left_out = [
            warning
            for warning in to_markdown(fixture, suffix=".zip").warnings
            if " not in the page: " in warning
        ]
        assert len(left_out) == LEFT_OUT.get(name, 0), left_out
        named = [w for w in manifest["warnings"] if " not in the page: " in w]
        assert named == left_out, manifest["warnings"]
    if name == "left_out_archive.zip":
        from tools.fixtures.office import DOWNLOADED_FROM

        for artifact in ("content.md", "content.txt", "chunks.jsonl"):
            assert DOWNLOADED_FROM not in (bundle / artifact).read_text("utf-8")

    # ...and words kept apart only by a Unicode space at the edge of bold, a
    # link, code or the rest stay apart (docs/09 P43).
    if name.startswith("edge_space_"):
        _assert_edge_spaces_kept(name, bundle, pdf)

    # ...and so do words kept apart only by ASCII whitespace or a line break
    # in formatted text: a run of it alone, or at the run's edge (docs/09 P49).
    if name.startswith("whitespace_run_"):
        _assert_whitespace_runs_kept(name, bundle, pdf)


def _assert_edge_spaces_kept(name: str, bundle: Path, pdf: Path) -> None:
    """Each line of an `edge_space_*` fixture, in content.md and in the PDF.

    content.md writes every space as a plain one, so each line must be its
    words with one space between, once its Markdown is taken out.  The PDF's
    text is read for the lines that hold a no-break space, which every font
    draws: any whitespace between the words will do, since how a text layer
    gives a space back is the PDF writer's, not the page's.
    """
    from pypdf import PdfReader
    from tools.fixtures.office import EDGE_SPACES, edge_space_lines

    markdown_lines = {
        re.sub(r"[*~`\[]", "", re.sub(r"\]\([^)]*\)", "", line)).strip()
        for line in (bundle / "content.md").read_text("utf-8").splitlines()
    }
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    pdf_text = pdf_text.replace("~~", "")
    lines = edge_space_lines(name)
    assert lines
    for line in lines:
        words = line.text.split()
        assert " ".join(words) in markdown_lines, words
        if EDGE_SPACES["nbsp"] in line.text:
            pattern = r"\s+".join(map(re.escape, words))
            assert re.search(pattern, pdf_text), words


def _assert_whitespace_runs_kept(name: str, bundle: Path, pdf: Path) -> None:
    """Each line of a `whitespace_run_*` fixture, in content.md and the PDF.

    Each line must be its words with one space between in content.md, once
    its Markdown is taken out, and its words with whitespace between in the
    PDF's text.  The PDF is read for every line: a run that dropped its
    whitespace left no part boundary for docling to pad, but an edge it
    dropped did, and only the PDF, like the page, ran those words together.
    """
    from pypdf import PdfReader
    from tools.fixtures.office import whitespace_run_lines

    markdown_lines = {
        re.sub(r"[*~`\[<>]", "", re.sub(r"\]\([^)]*\)", "", line)).strip()
        for line in (bundle / "content.md").read_text("utf-8").splitlines()
    }
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    pdf_text = pdf_text.replace("~~", "")
    lines = whitespace_run_lines(name)
    assert lines
    for line in lines:
        assert line.text in markdown_lines, line.words
        pattern = r"\s+".join(map(re.escape, line.words))
        assert re.search(pattern, pdf_text), line.words


def _list_blocks(content: str) -> list[tuple[str, list[str], list[int]]]:
    """content.md's lists: each one's kind, its items, and their numbers.

    docling writes an item a line, `1. ` or `- `, and a blank line after a
    list; one list's items are consecutive lines.
    """
    blocks: list[tuple[str, list[str], list[int]]] = []
    previous = ""
    for line in content.splitlines():
        item = re.match(r"(?:(\d+)\.|-) (.*)", line)
        if item is None:
            previous = line
            continue
        kind = "ol" if item[1] else "ul"
        if not (blocks and previous and blocks[-1][0] == kind):
            blocks.append((kind, [], []))
        blocks[-1][1].append(item[2])
        if item[1]:
            blocks[-1][2].append(int(item[1]))
        previous = line
    return blocks


@pytest.mark.browser
def test_adjacent_lists_reach_content_md_apart_and_tight(tmp_path: Path) -> None:
    """Two lists side by side in a converted document are two lists in the
    record, each numbered from its own start, its items not paragraphs
    (docs/09 P32): before, content.md numbered `Dulvane` 3 after `Crindet`."""
    from tools.fixtures.office import ADJACENT_LISTS_PAGE

    pdf, _ = _capture(FIXTURE_ROOT / "adjacent_lists_book.epub", tmp_path)
    bundle = pdf.with_suffix(".ai")
    content = (bundle / "content.md").read_text("utf-8")
    blocks = _list_blocks(content)
    assert [(kind, items) for kind, items, _ in blocks] == ADJACENT_LISTS_PAGE, content
    for kind, items, numbers in blocks[1:]:
        # Only the book's first list begins past 1; the rest count from 1.
        if kind == "ol":
            assert numbers == list(range(1, len(items) + 1)), content
    snapshot = (bundle / "content.html").read_text("utf-8")
    assert not re.search(r"<li[^>]*>\s*<p", snapshot), "an item went loose"
    for artifact in ("content.md", "content.html", "content.txt"):
        assert "<!--" not in (bundle / artifact).read_text("utf-8"), artifact


#: A nested list as `tools/fixtures/office.py` states it: its tag, and each
#: item's own text with the lists nested in it.
NestedList = tuple[str, list[tuple[str, list[Any]]]]


def _page_lists(page: str) -> list[NestedList]:
    """Every list on a page that is not inside another, as a `NestedList`.

    An item's own text is all of its text but its nested lists', so a
    sub-item flattened into its parent's list, or written into the parent's
    text as `* sub`, compares unequal.
    """
    from bs4 import BeautifulSoup, Tag

    def own_text(item: Tag) -> str:
        words = [
            str(text)
            for text in item.find_all(string=True)
            if not any(
                parent.name in ("ol", "ul") and item in parent.parents
                for parent in text.parents
            )
        ]
        return " ".join(" ".join(words).split())

    def tree(element: Tag) -> NestedList:
        return (
            element.name,
            [
                (
                    own_text(item),
                    [
                        tree(child)
                        for child in item.find_all(["ol", "ul"])
                        if child.find_parent("li") is item
                    ],
                )
                for item in element.find_all("li", recursive=False)
            ],
        )

    soup = BeautifulSoup(page, "html.parser")
    return [
        tree(element)
        for element in soup.find_all(["ol", "ul"])
        if element.find_parent("li") is None
    ]


def _depths(lists: list[NestedList], depth: int = 0) -> list[tuple[int, str, str]]:
    """Each item with text, in order: how deep it is, its list's tag, its text."""
    lines: list[tuple[int, str, str]] = []
    for tag, items in lists:
        for text, nested in items:
            if text:
                lines.append((depth, tag, text))
            lines += _depths(nested, depth + 1)
    return lines


def _content_md_depths(content: str) -> list[tuple[int, str, str]]:
    """content.md's list items as `_depths` gives them.

    docling writes an item a line, `1. ` or `- `, four spaces deeper for each
    list it is nested in.  An item that holds only a list it leaves out, or
    writes as a marker on its list's first line padded to four columns
    (`-   - Gorvane`); either way the item has no text, so it is not one of
    `_depths`, and each such marker counts as a level.
    """
    return [
        ((len(item[1]) + len(item[2])) // 4, "ol" if item[3] else "ul", item[4])
        for item in _CONTENT_MD_ITEM.finditer(content)
    ]


#: One content.md list line: the markers of items that hold only a list, if
#: any, then the indentation, the marker and the text of the item it names.
_CONTENT_MD_ITEM = re.compile(
    r"(?m)^((?: *(?:\d+\.|-) +(?=\d+\. |- ))*)( *)(?:(\d+)\.|-) (.*)$"
)


@pytest.mark.browser
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("nested_book.epub", "NESTED_BOOK_PAGE"),
        ("nested_report.docx", "NESTED_REPORT_PAGE"),
        ("nested_archive.zip", "NESTED_ARCHIVE_PAGE"),
    ],
)
def test_nested_lists_reach_the_page_and_content_md_nested(
    tmp_path: Path, name: str, expected: str
) -> None:
    """A list nested in a converted document's item is nested on the page and
    in the record (docs/09 P40).  Before, markdownify indented it by its
    parent's marker width, two spaces or three, and Python-Markdown nests at
    four: under `* ` the sub-items became the parent list's own, under `1. `
    they were printed into the parent item as `* Umbrel`."""
    if name != "nested_book.epub" and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    from tools.fixtures import office

    lists: list[NestedList] = getattr(office, expected)
    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")

    page = (bundle / "content.html").read_text("utf-8")
    assert _page_lists(page) == lists
    content = (bundle / "content.md").read_text("utf-8")
    assert _content_md_depths(content) == _depths(lists), content
    for artifact in ("content.md", "content.html", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        # The comments that keep lists apart, and the bridge's own marks.
        assert "<!--" not in text, artifact
        assert not re.search("[\ue000-\uf8ff]", text), artifact


#: A code block as `tools/fixtures/office.py` states it: how many items it
#: sits in, the text of the item that holds it but for its code and its
#: nested lists, and the code.
ItemCode = tuple[int, str, str]


def _page_code(page: str) -> tuple[list[ItemCode], list[NestedList]]:
    """Each `<pre>` on a page as an `ItemCode`, and the page's lists without
    them."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page, "html.parser")
    code: list[ItemCode] = []
    for pre in soup("pre"):
        item = pre.find_parent("li")
        owner = ""
        if item is not None:
            owner = " ".join(
                " ".join(
                    str(text)
                    for text in item.find_all(string=True)
                    if not any(
                        parent.name == "pre"
                        or (parent.name in ("ol", "ul") and item in parent.parents)
                        for parent in text.parents
                    )
                ).split()
            )
        # Python-Markdown ends every code block with a line break.
        text = pre.get_text()
        assert text.endswith("\n"), text
        code.append((len(pre.find_parents("li")), owner, text[:-1]))
    for pre in soup("pre"):
        pre.decompose()
    return code, _page_lists(str(soup))


@pytest.mark.browser
@pytest.mark.parametrize(
    "name", ["code_items_book.epub", "code_items_report.docx", "code_items_archive.zip"]
)
def test_code_in_list_items_reaches_the_page_with_its_lines(
    tmp_path: Path, name: str
) -> None:
    """A `<pre>` in a converted document's list item is a code block on the
    page, in its item, line for line (docs/09 P44).  Before, markdownify's
    fence was indented in the item, Python-Markdown read it as inline code,
    and the code's lines ran together into the item's text."""
    if name != "code_items_book.epub" and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    from tools.fixtures import office

    stem = name.split(".")[0].upper()
    expected_code: list[ItemCode] = getattr(office, f"{stem}_CODE")
    expected_lists: list[NestedList] = getattr(office, f"{stem}_PAGE")
    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")

    page = (bundle / "content.html").read_text("utf-8")
    assert _page_code(page) == (expected_code, expected_lists)
    # content.md holds each block line for line, in its item's indentation;
    # docling strips a block's leading whitespace, so a line is compared
    # without its own (docs/09 P50-4).  content.txt puts an item's marker on
    # its code's first line, so it is held to the code's words in order.
    record = [
        line.strip()
        for line in (bundle / "content.md").read_text("utf-8").splitlines()
        if line.strip()
    ]
    text = " ".join((bundle / "content.txt").read_text("utf-8").split())
    for _, _, code in expected_code:
        lines = [line.strip() for line in code.splitlines() if line.strip()]
        assert any(
            record[start : start + len(lines)] == lines for start in range(len(record))
        ), ("content.md", code)
        assert " ".join(code.split()) in text, ("content.txt", code)
    for artifact in ("content.md", "content.html", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        assert "<!--" not in text, artifact
        assert not re.search("[\ue000-\uf8ff]", text), artifact


#: The elements whose nesting a definition list in an item must keep on the
#: page; a `<p>` is one block of text.
_DEFINITION_BLOCKS = frozenset({"ul", "ol", "li", "dl", "dt", "dd", "blockquote"})


def _blocks(element: Any) -> list[Any]:
    """What `element` holds: each paragraph or run of loose text, and each of
    `_DEFINITION_BLOCKS` as its tag and what it holds."""
    from bs4 import NavigableString, Tag

    held: list[Any] = []
    run: list[str] = []

    def end_run() -> None:
        text = " ".join("".join(run).split())
        if text:
            held.append(text)
        run.clear()

    for child in element.children:
        if isinstance(child, Tag) and child.name in _DEFINITION_BLOCKS:
            end_run()
            held.append((child.name, _blocks(child)))
        elif isinstance(child, Tag) and child.name == "p":
            end_run()
            run.append(child.get_text())
            end_run()
        elif isinstance(child, Tag):
            run.append(child.get_text())
        elif type(child) is NavigableString:  # not a comment
            run.append(str(child))
    end_run()
    return held


@pytest.mark.browser
@pytest.mark.parametrize("name", ["definition_book.epub", "definition_archive.zip"])
def test_definition_lists_in_items_reach_the_page_whole(
    tmp_path: Path, name: str
) -> None:
    """A definition list in a converted document's item keeps everything its
    definitions hold (docs/09 P62).  Before, Python-Markdown read every block
    after a definition's first into the item: a second paragraph as code, a
    nested list or definition list as a code block's text."""
    from bs4 import BeautifulSoup
    from tools.fixtures import office

    sources = list(office.DEFINITION_BOOK_SECTIONS.values())
    if name == "definition_archive.zip":
        sources = [office.DEFINITION_MEMBER_ITEMS, *sources]
    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")

    soup = BeautifulSoup((bundle / "content.html").read_text("utf-8"), "html.parser")
    page = [
        _blocks(element)
        for element in soup.find_all(["ol", "ul"])
        if element.find_parent(_DEFINITION_BLOCKS) is None
    ]
    expected = [
        _blocks(BeautifulSoup(source, "html.parser").find(["ol", "ul"]))
        for source in sources
    ]
    assert page == expected

    # Every word reaches the record, but for a definition's own text where it
    # holds a definition list, which docling's reader leaves out wherever the
    # definition list is (docs/09 P60-5).
    words, dropped = set(), set()
    for source in sources:
        document = BeautifulSoup(source, "html.parser")
        words |= set(document.get_text(" ").split())
        for definition in document.find_all("dd"):
            if definition.find("dl", recursive=False) is not None:
                dropped |= {
                    word
                    for text in definition.find_all(string=True, recursive=False)
                    for word in text.split()
                }
    assert words - dropped and dropped <= words
    for artifact in ("content.md", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        assert not {word for word in words - dropped if word not in text}, artifact
    for artifact in ("content.md", "content.html", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        # The comment an item opening with a definition list has for its
        # text, and the bridge's own marks.
        assert "<!--" not in text, artifact
        assert not re.search("[\ue000-\uf8ff]", text), artifact


@pytest.mark.browser
@pytest.mark.parametrize(
    "name",
    ["inline_code_book.epub", "inline_code_report.docx", "inline_code_archive.zip"],
)
def test_code_in_a_line_of_text_reaches_the_bundle_as_text(
    tmp_path: Path, name: str
) -> None:
    """A `<pre>` that a link, emphasis, struck text, a subscript or a quotation
    holds, and inline code over lines, are code in content.html, line for line,
    and nothing in them is an element (docs/09 P59).  Before, a blank line or a
    line opening a heading, a rule, a quotation or a `<div>` let what followed
    be Markdown, and an `<img>` typed in the code was fetched."""
    if name != "inline_code_book.epub" and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    from bs4 import BeautifulSoup
    from tools.fixtures import office

    stem = name.split(".")[0].upper()
    expected: list[tuple[str, str]] = getattr(office, f"{stem}_CODE")
    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")

    page = (bundle / "content.html").read_text("utf-8")
    soup = BeautifulSoup(page, "html.parser", preserve_whitespace_tags=["code", "pre"])
    assert soup("img") == [], page
    # A line break is a line feed, the code's own text, which the snapshot
    # keeps; the page shows it by its class.
    held = [
        (code.parent.name, code.get_text())
        for code in soup("code")
        if code.parent is not None
        and code.find_parent("pre") is None
        and code.get_text() != name
    ]
    assert held == expected
    assets = (bundle / "assets.json").read_text("utf-8")
    assert "tracker.invalid" not in assets
    # docling writes code that is all an item holds as the item's text, which
    # content.md escapes (`&lt;img`): read as text, the words are the code's.
    for artifact in ("content.md", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        assert not re.search("[\ue000-\uf8ff]", text), artifact
        record = " ".join(html.unescape(text).split())
        for _, code in expected:
            assert " ".join(code.split()) in record, (artifact, code)


#: A code block as `tools/fixtures/office.py` states it: the quotations,
#: definitions and list items it sits in, outermost first, and the code.
QuotedCode = tuple[str, str]


@pytest.mark.browser
@pytest.mark.parametrize(
    "name",
    ["quoted_code_book.epub", "quoted_code_report.docx", "quoted_code_archive.zip"],
)
def test_code_in_quotations_and_definitions_reaches_the_page_with_its_lines(
    tmp_path: Path, name: str
) -> None:
    """A `<pre>` in a converted document's quotation or definition is a code
    block on the page, in its quotation or definition, line for line (docs/09
    P47).  Before, `> ` or `:   ` stood before markdownify's fence, which
    Python-Markdown then read as inline code, and the lines after a blank line
    in the code as Markdown: the book's `<img>` was an image on the page."""
    if name != "quoted_code_book.epub" and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    from bs4 import BeautifulSoup
    from tools.fixtures import office

    expected: list[QuotedCode] = getattr(office, f"{name.split('.')[0].upper()}_CODE")
    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")

    soup = BeautifulSoup((bundle / "content.html").read_text("utf-8"), "html.parser")
    code = []
    for pre in soup("pre"):
        holders = reversed(pre.find_parents(["blockquote", "dd", "li"]))
        # Python-Markdown ends every code block with a line break.
        assert pre.get_text().endswith("\n"), pre
        code.append(("/".join(holder.name for holder in holders), pre.get_text()[:-1]))
    assert code == expected
    assert soup("img") == []
    # No code is left as inline code but the zip's name, which its reader
    # writes as a code span.
    assert [span.get_text() for span in soup("code") if span.parent.name != "pre"] == (
        [name] if name.endswith(".zip") else []
    )
    for artifact in ("content.md", "content.txt"):
        record = " ".join((bundle / artifact).read_text("utf-8").split())
        for _, block in expected:
            assert " ".join(block.split()) in record, (artifact, block)
    for artifact in ("content.md", "content.html", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        assert "<!--" not in text, artifact
        assert not re.search("[\ue000-\uf8ff]", text), artifact


class _QuotationsAndDefinitionLists(HTMLParser):
    """Each `<blockquote>` and `<dl>` on a page, in the order its tag opens,
    with the words it holds outside the quotations and lists in it."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, list[str]]] = []
        self._open: list[int] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("blockquote", "dl"):
            self.blocks.append((tag, []))
            self._open.append(len(self.blocks) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("blockquote", "dl"):
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._open:
            self.blocks[self._open[-1]][1].extend(data.split())


@pytest.mark.browser
@pytest.mark.parametrize(
    ("name", "page", "requires"),
    [
        ("adjacent_blocks_book.epub", "ADJACENT_BLOCKS_PAGE", None),
        ("adjacent_blocks_report.docx", "ADJACENT_BLOCKS_REPORT_PAGE", "mammoth"),
        ("adjacent_blocks_archive.zip", "ADJACENT_BLOCKS_ARCHIVE_PAGE", "mammoth"),
    ],
    ids=["epub", "docx", "zip"],
)
def test_adjacent_quotations_reach_the_record_apart(
    tmp_path: Path, name: str, page: str, requires: str | None
) -> None:
    """Two quotations, or two definition lists, side by side in a converted
    document are two in the captured page (docs/09 P63), and the comment that
    keeps them apart is in no artifact."""
    if requires and importlib.util.find_spec(requires) is None:
        pytest.skip(f"{requires} is absent — install webshot[office] to run this")
    from tools.fixtures import office

    pdf, _ = _capture(FIXTURE_ROOT / name, tmp_path)
    bundle = pdf.with_suffix(".ai")
    snapshot = (bundle / "content.html").read_text("utf-8")
    parser = _QuotationsAndDefinitionLists()
    parser.feed(snapshot)
    assert parser.blocks == getattr(office, page), snapshot
    for artifact in ("content.md", "content.html", "content.txt"):
        text = (bundle / artifact).read_text("utf-8")
        assert "<!--" not in text and "\ue000" not in text, artifact


def test_every_fixture_has_a_generator_entry() -> None:
    """A fixture nobody can regenerate is the opaque binary this avoids."""
    from tools.fixtures.office import SENTINELS, WRITERS

    assert set(WRITERS) == {name for name, *_ in CASES}
    assert set(SENTINELS) == set(WRITERS)
    for name, _, sentinel, _ in CASES:
        assert SENTINELS[name] == sentinel
