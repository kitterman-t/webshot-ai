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


def test_every_fixture_has_a_generator_entry() -> None:
    """A fixture nobody can regenerate is the opaque binary this avoids."""
    from tools.fixtures.office import SENTINELS, WRITERS

    assert set(WRITERS) == {name for name, *_ in CASES}
    assert set(SENTINELS) == {kind for _, kind, *_ in CASES}
    for _, kind, sentinel, _ in CASES:
        assert SENTINELS[kind] == sentinel
