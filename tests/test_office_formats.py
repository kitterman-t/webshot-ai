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

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

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

#: How many warnings name the members a zip fixture's reader left out
#: (docs/09 P33): the encrypted one, the Word file that is not one, the two
#: nothing reads, and the two AppleDouble members.
LEFT_OUT = {"left_out_archive.zip": 4}

#: A warning that says what a deck's or a Word file's reader could not carry,
#: whether the file was captured on its own or as a member of a zip.
CONVERSION_WARNING = re.compile(
    r"^(?:[^:]+\.(?:pptx|docx): )?(?:Slide |The notes of slide |The document body)"
)


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


def test_every_fixture_has_a_generator_entry() -> None:
    """A fixture nobody can regenerate is the opaque binary this avoids."""
    from tools.fixtures.office import SENTINELS, WRITERS

    assert set(WRITERS) == {name for name, *_ in CASES}
    assert set(SENTINELS) == set(WRITERS)
    for name, _, sentinel, _ in CASES:
        assert SENTINELS[name] == sentinel
