"""A text's encoding is decided from all of it, not from its first 64 KB.

MarkItDown guesses a text stream's encoding from a 64 KB sample and decodes
the whole stream by that guess, so a UTF-8 file whose first character outside
ASCII came later was decoded as ASCII and failed: on its own the capture
ended with exit 2, and in a zip the member was left out (docs/09 P35).
`late_utf8.csv` and `late_utf8_archive.zip` are written by
`tools/fixtures/office.py`; every word in them is made up.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from webshot.acquire.markitdown_bridge import (
    _as_escaped_csv,
    _declared_charset,
    to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import LATE_WORDS, SENTINELS  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "office"
LATE_CSV = FIXTURES / "late_utf8.csv"
LATE_ARCHIVE = FIXTURES / "late_utf8_archive.zip"

#: How much of a stream MarkItDown 0.1.8's `_read_charset_sample` reads.
SAMPLE = 65_536
#: The members whose first character outside ASCII is past the sample.
LATE_MEMBERS = (
    "src/values.py",
    "data/records.json",
    "data/records.yaml",
    "notebooks/count.ipynb",
    "data/rows.tsv",
)
#: Short files behind a byte order mark: YAML, which Magika could not name,
#: and text, which it named, and whose mark MarkItDown then put on the page.
MARKED = "data/marked.yaml"
MARKED_TEXT = "notes/marked.txt"
#: A Windows-1252 CSV, which WebShot read as UTF-8 until docs/09 P38, and
#: now reads as the windows-1252 it guesses, and says so.
WINDOWS = "data/windows.csv"
WINDOWS_WARNING = (
    f"{WINDOWS}: The file is not UTF-8 and does not say which encoding it is "
    "in, so WebShot read it as windows-1252, which Windows wrote such text in "
    "for Western European languages and which browsers assume for it; if it "
    "is in another encoding, its 2 characters outside ASCII are wrong "
    "(docs/09 P38)."
)


def _members() -> dict[str, bytes]:
    with zipfile.ZipFile(LATE_ARCHIVE) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _sections(markdown: str) -> dict[str, str]:
    """Each member's part of a zip's page, by the name in its heading."""
    return {
        part.split("\n", 1)[0].strip(): part for part in markdown.split("## File: ")[1:]
    }


def _first_outside_ascii(data: bytes) -> int:
    return next(index for index, byte in enumerate(data) if byte > 0x7F)


def test_each_late_character_is_past_the_sample() -> None:
    """A fixture regenerated shorter would test nothing, and pass."""
    members = _members()
    assert set(members) == {*LATE_MEMBERS, MARKED, MARKED_TEXT, WINDOWS}
    for name in LATE_MEMBERS:
        assert _first_outside_ascii(members[name]) > SAMPLE, name
    assert _first_outside_ascii(LATE_CSV.read_bytes()) > SAMPLE


def test_markitdown_alone_still_fails_each_late_file() -> None:
    """The premise: given what the bridge gives it, less the encoding,
    MarkItDown's own guess fails each late file and the marked YAML, and puts
    the marked text's byte order mark on the page.

    A TSV is handed over as the CSV WebShot writes from it; MarkItDown reads a
    raw one as plain text, from all of its bytes.  If this fails after an
    upgrade, MarkItDown or Magika no longer guesses from a sample these files
    defeat, and the fixtures no longer hold the case `_declared_charset`
    exists for: find out which, and say so in docs/09 P35, before changing
    either.
    """
    from markitdown import MarkItDown, MarkItDownException, StreamInfo

    markitdown = MarkItDown(enable_plugins=False)
    cases = [(LATE_CSV.name, LATE_CSV.read_bytes())]
    members = _members()
    cases += [
        (name, data)
        for name, data in members.items()
        if name not in (WINDOWS, MARKED_TEXT)
    ]
    for name, data in cases:
        suffix = Path(name).suffix
        if suffix == ".tsv":
            data, suffix = _as_escaped_csv(data, "\t", name)[0], ".csv"
        info = StreamInfo(extension=suffix, filename=Path(name).name)
        with pytest.raises(MarkItDownException):
            markitdown.convert_stream(io.BytesIO(data), stream_info=info)
    marked = markitdown.convert_stream(
        io.BytesIO(members[MARKED_TEXT]), stream_info=StreamInfo(extension=".txt")
    )
    assert marked.markdown.startswith("\N{ZERO WIDTH NO-BREAK SPACE}")


def test_a_late_utf8_csv_is_read() -> None:
    converted = to_markdown(LATE_CSV, suffix=".csv")

    assert SENTINELS["late_utf8.csv"] in converted.markdown
    assert converted.warnings == ()


def test_every_member_of_the_late_archive_is_in_the_page() -> None:
    converted = to_markdown(LATE_ARCHIVE, suffix=".zip")
    sections = _sections(converted.markdown)

    assert set(sections) == {*LATE_MEMBERS, MARKED, MARKED_TEXT, WINDOWS}
    # The module carries the sentinel; every other member, the same words.
    assert SENTINELS["late_utf8_archive.zip"] in sections["src/values.py"]
    for name in sorted({*LATE_MEMBERS, MARKED} - {"src/values.py"}):
        assert LATE_WORDS in sections[name], name
    # Only the Windows-1252 member is named, and for its encoding.
    assert converted.warnings == (WINDOWS_WARNING,)


def test_a_byte_order_mark_does_not_reach_the_page() -> None:
    """Declared `utf-8-sig`, whether or not Magika could name the file."""
    sections = _sections(to_markdown(LATE_ARCHIVE, suffix=".zip").markdown)

    assert f"name: {LATE_WORDS}" in sections[MARKED]
    assert "The first line of a note" in sections[MARKED_TEXT]
    for name in (MARKED, MARKED_TEXT):
        assert "\N{ZERO WIDTH NO-BREAK SPACE}" not in sections[name], name


def test_a_csv_that_is_not_utf8_is_read_as_its_guess() -> None:
    """Read as UTF-8 regardless before P38, with U+FFFD for each letter, and
    before P35 garbled twice: MarkItDown took WebShot's own UTF-8 for another
    encoding."""
    sections = _sections(to_markdown(LATE_ARCHIVE, suffix=".zip").markdown)

    assert "\N{REPLACEMENT CHARACTER}" not in sections[WINDOWS]
    assert LATE_WORDS in sections[WINDOWS]


@pytest.mark.parametrize(
    ("data", "charset"),
    [
        (b"", None),
        (b"plain,ascii\n", None),
        ("caf\N{LATIN SMALL LETTER E WITH ACUTE}\n".encode(), "utf-8"),
        (
            b"\xef\xbb\xbf" + "caf\N{LATIN SMALL LETTER E WITH ACUTE}".encode(),
            "utf-8-sig",
        ),
        ("caf\N{LATIN SMALL LETTER E WITH ACUTE}\n".encode("cp1252"), None),
        ("caf\N{LATIN SMALL LETTER E WITH ACUTE}\x00".encode(), None),
    ],
    ids=["empty", "ascii", "utf-8", "byte-order-mark", "windows-1252", "nul"],
)
def test_declared_charset(data: bytes, charset: str | None) -> None:
    assert _declared_charset(data) == charset
