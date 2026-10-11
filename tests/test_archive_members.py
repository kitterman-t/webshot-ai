"""A zip member the reader leaves out of the page is named in the manifest.

Upstream left a member nothing reads out without a word, and so did WebShot
until docs/09 P33: a deck in a zip vanished on an install without the office
extra, a PDF on every install, and one encrypted or damaged member failed the
whole archive.  `left_out_archive.zip` is written by `tools/fixtures/office.py`
and holds one member of each kind; every word and address in it is made up.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

from webshot.acquire.markitdown_bridge import to_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import DOWNLOADED_FROM, SENTINELS  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "office" / "left_out_archive.zip"
SENTINEL = SENTINELS["left_out_archive.zip"]

needs_office = pytest.mark.skipif(
    importlib.util.find_spec("mammoth") is None,
    reason="mammoth is absent — install webshot[office] to read report.docx",
)

#: Where MarkItDown 0.1.x records that a reader's package failed to import.
#: Setting one makes the real reader fail as it does without that package.
OFFICE_MARKERS = (
    ("markitdown.converters._docx_converter", "_dependency_exc_info"),
    ("markitdown.converters._pptx_converter", "_dependency_exc_info"),
    ("markitdown.converters._xlsx_converter", "_xlsx_dependency_exc_info"),
)
PDF_MARKER = ("markitdown.converters._pdf_converter", "_dependency_exc_info")

ENCRYPTED = (
    "secret.txt is not in the page: it is encrypted, and WebShot does not "
    "decrypt archive members."
)
UNREAD = (
    "2 members are not in the page: figure.bin, scan.pdf. WebShot has no "
    "reader for them."
)
APPLE_DOUBLE = (
    "2 AppleDouble members are not in the page: __MACOSX/notes/._kept.txt, "
    "__MACOSX/._report.docx. macOS writes one for each file it zips, to hold "
    "that file's Finder metadata and extended attributes, such as the address "
    "it was downloaded from; it is not a file of its own."
)
#: With the office extra: `report.docx` is read, and `broken.docx` is a Word
#: file its reader opens and fails on.  The directory entries are not named.
WARNINGS = (
    "broken.docx is not in the page: its reader failed (BadZipFile: File is "
    "not a zip file).",
    ENCRYPTED,
    UNREAD,
    APPLE_DOUBLE,
)
#: Without it, neither Word file is opened, so both are named for the extra,
#: in WebShot's words rather than MarkItDown's `pip install` line.
WARNINGS_WITHOUT_OFFICE = (
    ENCRYPTED,
    "2 members are not in the page: report.docx, broken.docx. Reading .docx "
    "files needs the optional dependencies for that format. Install "
    "webshot[office] from your checkout: uv sync --extra office (name any "
    "other extras you use too).",
    UNREAD,
    APPLE_DOUBLE,
)


def _without(monkeypatch: pytest.MonkeyPatch, *markers: tuple[str, str]) -> None:
    """Each reader `markers` names fails as if its package were not installed."""
    try:
        raise ImportError("not installed")
    except ImportError:
        failure = sys.exc_info()
    for module_name, marker in markers:
        module = importlib.import_module(module_name)
        assert hasattr(module, marker), (
            f"{module_name} has no {marker}; its attributes are "
            f"{sorted(name for name in vars(module) if 'dependency' in name)}"
        )
        monkeypatch.setattr(module, marker, failure)


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


@needs_office
def test_each_member_left_out_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whether a PDF is read depends on a package no WebShot extra installs, so
    # its absence is set here rather than assumed of the environment.
    _without(monkeypatch, PDF_MARKER)
    converted = to_markdown(FIXTURE, suffix=".zip")
    assert converted.warnings == WARNINGS
    assert SENTINEL in converted.markdown
    assert "Left-out archive report" in converted.markdown


def test_without_the_office_extra_a_member_gets_webshots_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without(monkeypatch, PDF_MARKER, *OFFICE_MARKERS)
    converted = to_markdown(FIXTURE, suffix=".zip")
    assert converted.warnings == WARNINGS_WITHOUT_OFFICE
    assert SENTINEL in converted.markdown
    for warning in converted.warnings:
        assert "pip install" not in warning and "markitdown[" not in warning


def test_the_download_address_an_apple_double_member_holds_is_not_in_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read as text, an AppleDouble member put its file's download address and
    quarantine record on the page (docs/09 P33)."""
    with zipfile.ZipFile(FIXTURE) as archive:
        # Otherwise its absence below would prove nothing.
        assert DOWNLOADED_FROM.encode() in archive.read("__MACOSX/notes/._kept.txt")
    _without(monkeypatch, PDF_MARKER)
    markdown = to_markdown(FIXTURE, suffix=".zip").markdown
    assert DOWNLOADED_FROM not in markdown
    assert "Safari" not in markdown
    assert "MACOSX" not in markdown


def test_a_member_named_like_apple_double_without_its_header_is_read(
    tmp_path: Path,
) -> None:
    """The name alone does not make a member metadata; its first bytes do."""
    source = _zip(tmp_path / "dotted.zip", {"._notes.txt": b"SENTINEL ZIP DOTTED"})
    converted = to_markdown(source, suffix=".zip")
    assert "SENTINEL ZIP DOTTED" in converted.markdown
    assert converted.warnings == ()


def test_many_members_nothing_reads_are_one_warning(tmp_path: Path) -> None:
    """A zipped virtualenv had 572 such members: a line each would bury the
    warnings that matter, so the first few are named and the rest counted by
    suffix."""
    members = {f"cache/m{index}.pyc": bytes(range(256)) for index in range(4)}
    members |= {"lib/core.so": bytes(range(256)), "bin/tool": bytes(range(256))}
    converted = to_markdown(_zip(tmp_path / "venv.zip", members), suffix=".zip")
    assert converted.warnings == (
        "6 members are not in the page: cache/m0.pyc, cache/m1.pyc, "
        "cache/m2.pyc and 3 more (4 .pyc, 1 .so, 1 with no suffix). WebShot "
        "has no reader for them.",
    )


def test_a_member_that_cannot_be_extracted_is_named_and_the_rest_read(
    tmp_path: Path,
) -> None:
    """Before, one damaged member failed the whole archive with exit 2."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("damaged.txt", b"original bytes")
        archive.writestr("intact.txt", b"SENTINEL ZIP INTACT")
    source = tmp_path / "damaged.zip"
    source.write_bytes(buffer.getvalue().replace(b"original", b"changed!", 1))

    converted = to_markdown(source, suffix=".zip")
    assert "SENTINEL ZIP INTACT" in converted.markdown
    assert converted.warnings == (
        "damaged.txt is not in the page: it could not be extracted (BadZipFile: "
        "Bad CRC-32 for file 'damaged.txt').",
    )


def test_a_member_whose_rows_cannot_be_split_is_named_and_the_rest_read(
    tmp_path: Path,
) -> None:
    """The CSV reader's refusal ended the whole archive; now it ends the member."""
    oversized = b'cell\n"' + b"x" * 140_000 + b'"\n'
    source = _zip(
        tmp_path / "rows.zip",
        {"data/big.csv": oversized, "notes.txt": b"SENTINEL ZIP ROWS"},
    )
    converted = to_markdown(source, suffix=".zip")
    assert "SENTINEL ZIP ROWS" in converted.markdown
    assert converted.warnings == (
        "data/big.csv is not in the page: Could not read the rows of big.csv: "
        "field larger than field limit (131072)",
    )


def test_a_zip_inside_a_zip_names_its_left_out_members_under_its_own_name(
    tmp_path: Path,
) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("figure.bin", bytes(range(256)))
    source = _zip(tmp_path / "outer.zip", {"inner.zip": inner.getvalue()})
    assert to_markdown(source, suffix=".zip").warnings == (
        "inner.zip: 1 member is not in the page: figure.bin. WebShot has no "
        "reader for it.",
    )


@needs_office
def test_the_warnings_reach_the_materialized_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webshot.acquire.adapters import materialize_source

    _without(monkeypatch, PDF_MARKER)
    materialized = materialize_source(FIXTURE.as_uri())
    try:
        assert materialized.warnings == list(WARNINGS)
    finally:
        materialized.cleanup()
