"""Local text that is not UTF-8 is read by one rule, and a guess is named.

Before docs/09 P38, MarkItDown guessed such text's encoding from its first
64 KB and said nothing: Windows-1252 was read as cp1250 or cp775, or failed,
and WebShot read a CSV, a TSV or a Markdown file as UTF-8 with U+FFFD for
every other byte.  `text_encoding.decode_text` now decides, in this order: a
byte order mark, UTF-8, the document's own declaration, then windows-1252
unless it reads as mess, then charset-normalizer's choice.  The fixtures are
written by `tools/fixtures/encodings.py`; every word in them is made up or
generic prose.
"""

from __future__ import annotations

import html
import io
import json
import mimetypes
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from webshot.acquire import adapters
from webshot.acquire.markitdown_bridge import TEXT_SUFFIXES, to_markdown
from webshot.acquire.text_encoding import DecodedText, decode_text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.encodings import (  # noqa: E402
    JAPANESE,
    SENTINELS,
    UNDECODABLE_LABELS,
    WRITERS,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "encodings"
ARCHIVE = FIXTURES / "legacy_archive.zip"
LABELS = FIXTURES / "declared_labels.zip"
WIDE = FIXTURES / "wide_text.zip"
BOOK = FIXTURES / "legacy_book.epub"

#: The dish every Western European fixture names, in the letters it holds.
DISH = (
    "Cr\N{LATIN SMALL LETTER E WITH GRAVE}me "
    "br\N{LATIN SMALL LETTER U WITH CIRCUMFLEX}l\N{LATIN SMALL LETTER E WITH ACUTE}e"
)
#: The dish as MarkItDown's guess, cp1250, read it.
DISH_AS_CP1250 = (
    "Cr\N{LATIN SMALL LETTER C WITH CARON}me "
    "br\N{LATIN SMALL LETTER U WITH DOUBLE ACUTE}l\N{LATIN SMALL LETTER E WITH ACUTE}e"
)
RUSSIAN_WORD = "\N{CYRILLIC CAPITAL LETTER ES}\N{CYRILLIC SMALL LETTER O}\N{CYRILLIC SMALL LETTER BE}\N{CYRILLIC SMALL LETTER ER}\N{CYRILLIC SMALL LETTER A}\N{CYRILLIC SMALL LETTER EN}\N{CYRILLIC SMALL LETTER I}\N{CYRILLIC SMALL LETTER IE}"

GUESSED = "The file is not UTF-8 and does not say which encoding it is in"
AS_WINDOWS_1252 = f"{GUESSED}, so WebShot read it as windows-1252, "
AS_CP1251 = (
    f"{GUESSED}, and as windows-1252 it does not read as text, so WebShot "
    "read it as cp1251, charset-normalizer's guess; "
)
AS_ISO_2022_JP = (
    "The file holds ISO 2022 escapes and does not say which encoding it is in, "
    "and as windows-1252 it does not read as text, so WebShot read it as "
    "iso2022_jp, charset-normalizer's guess; "
)
#: The whole of one warning, so its wording is held somewhere.
CSV_WARNING = (
    f"{AS_WINDOWS_1252}which Windows wrote such text in for Western European "
    "languages and which browsers assume for it; if it is in another encoding, "
    "its 19 characters outside ASCII are wrong (docs/09 P38)."
)
#: The archive's members, and what the manifest says of each.  The declared,
#: marked and empty members say nothing: their encoding was not guessed.
MEMBERS = {
    "notes/recipe.txt": AS_WINDOWS_1252,
    "data/menu.json": AS_WINDOWS_1252,
    "data/menu.yaml": AS_WINDOWS_1252,
    "web/page.html": AS_WINDOWS_1252,
    "web/declared.html": None,
    "web/feed.xml": None,
    "notes/russian.txt": AS_CP1251,
    "notes/japanese.txt": AS_ISO_2022_JP,
    "data/unicode.txt": None,
    "data/empty.yaml": None,
}


def _sections(markdown: str) -> dict[str, str]:
    """Each member's part of a zip's page, by the name in its heading."""
    return {
        part.split("\n", 1)[0].strip(): part for part in markdown.split("## File: ")[1:]
    }


def _members() -> dict[str, bytes]:
    with zipfile.ZipFile(ARCHIVE) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# --------------------------------------------------------------------------- #
# The premise
# --------------------------------------------------------------------------- #


def test_markitdown_alone_misreads_or_refuses_each_fixture() -> None:
    """What WebShot no longer inherits, given the bytes the bridge was given.

    If this fails after an upgrade, MarkItDown, Magika or charset-normalizer
    now guesses these files differently: find out how, and say so in docs/09
    P38, before changing either side.
    """
    from markitdown import MarkItDown, MarkItDownException, StreamInfo

    markitdown = MarkItDown(enable_plugins=False)

    def read(data: bytes, name: str) -> str:
        info = StreamInfo(extension=Path(name).suffix, filename=Path(name).name)
        markdown: str = markitdown.convert_stream(
            io.BytesIO(data), stream_info=info
        ).markdown
        return markdown

    late = FIXTURES / "windows_1252_late.txt"
    with pytest.raises(MarkItDownException):
        read(late.read_bytes(), late.name)
    for name in ("empty.yaml", "empty.yml", "empty.xml"):
        # Upstream takes an empty file Magika cannot name by its MIME type,
        # which is the platform's: macOS's Apache `mime.types` makes XML
        # `application/xml`, which it refuses, and with no such file Python's
        # own `text/xml` stands, which it reads, as on the Linux runner
        # (docs/09 P38-10).  YAML has no type in either; a Debian-style
        # `/etc/mime.types`, as on GitHub's Ubuntu runners, makes it
        # `application/yaml`, which upstream refuses all the same.
        mimetype = mimetypes.guess_type(name, strict=False)[0] or ""
        if mimetype.startswith("text/"):
            assert read(b"", name) == "", name
        else:
            with pytest.raises(MarkItDownException):
                read(b"", name)
    # Python's own table, not the host's, which may add one.
    assert mimetypes.MimeTypes().guess_type("empty.yaml", strict=False)[0] is None
    members = _members()
    for name in ("notes/recipe.txt", "data/menu.json", "web/page.html"):
        assert DISH_AS_CP1250 in read(members[name], name), name
    assert DISH not in read(members["data/menu.yaml"], "menu.yaml")
    # What it read right, and a test for UTF-8 alone would not: seven-bit.
    assert JAPANESE.strip() in read(members["notes/japanese.txt"], "japanese.txt")
    assert DISH_AS_CP1250 in read(BOOK.read_bytes(), BOOK.name)


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("data", "encoding", "text"),
    [
        (b"plain\n", "utf-8", "plain\n"),
        (DISH.encode(), "utf-8", DISH),
        (b"\xef\xbb\xbf" + DISH.encode(), "utf-8-sig", DISH),
        (DISH.encode("utf-16-le").join([b"\xff\xfe", b""]), "utf-16", DISH),
        (DISH.encode("utf-16-be").join([b"\xfe\xff", b""]), "utf-16", DISH),
    ],
    ids=["ascii", "utf-8", "utf-8-mark", "utf-16-le-mark", "utf-16-be-mark"],
)
def test_what_is_known_is_not_a_guess(data: bytes, encoding: str, text: str) -> None:
    decoded = decode_text(data)

    assert (decoded.text, decoded.encoding, decoded.warning) == (text, encoding, None)


def test_a_wrong_byte_order_mark_shows_replacement_characters() -> None:
    decoded = decode_text(b"\xef\xbb\xbfcaf\xe9")

    assert decoded.text == "caf\N{REPLACEMENT CHARACTER}"
    assert decoded.warning == (
        "The file begins with a UTF-8 byte order mark but is not UTF-8 "
        "throughout, so the page shows U+FFFD (\N{REPLACEMENT CHARACTER}) where "
        "it is not (docs/09 P38)."
    )


def test_a_declaration_is_read_as_the_document_states_it() -> None:
    """An XML label means its codec; in HTML, `iso-8859-1` is windows-1252."""
    quoted = "\N{LEFT DOUBLE QUOTATION MARK}x\N{RIGHT DOUBLE QUOTATION MARK}"
    data = quoted.encode("cp1252")

    xml = decode_text(data, declared="iso-8859-1")
    page = decode_text(data, declared="iso-8859-1", html=True)

    assert (xml.text, xml.encoding, xml.warning) == ("\x93x\x94", "iso8859-1", None)
    assert (page.text, page.encoding, page.warning) == (quoted, "windows-1252", None)


@pytest.mark.parametrize(
    ("declared", "reason"),
    [
        ("utf-8", "The file says it is in utf-8, but it is not"),
        (
            "x-unheard-of",
            "The file says it is in x-unheard-of, which is not a text encoding "
            "WebShot knows",
        ),
        # Codecs from bytes to bytes, which `bytes.decode` refuses with
        # LookupError, and a NUL, which `codecs.lookup` refuses with
        # ValueError: each ended the decoder (docs/09 P38-9).
        *(
            (
                label,
                f"The file says it is in {label}, which is not a text encoding "
                "WebShot knows",
            )
            for label in ("hex", "base64", "zlib", "rot13")
        ),
        (
            "utf\x00",
            "The file says it is in utf\\x00, which is not a text encoding "
            "WebShot knows",
        ),
        # A text codec that raises UnicodeError, not UnicodeDecodeError.
        ("undefined", "The file says it is in undefined, but it is not"),
    ],
)
def test_a_declaration_that_does_not_decode_is_named(
    declared: str, reason: str
) -> None:
    decoded = decode_text(DISH.encode("cp1252"), declared=declared)

    assert decoded.text == DISH
    assert decoded.warning is not None
    assert decoded.warning.startswith(f"{reason}, so WebShot read it as windows-1252, ")


def test_windows_1252_is_guessed_however_late_or_sparse() -> None:
    """charset-normalizer reads 5 stretches of 512 bytes; this rule, the lines
    outside ASCII, wherever they are."""
    data = ("x\n" * 50_000 + DISH + "\n").encode("cp1252")

    decoded = decode_text(data)

    assert decoded.text.endswith(DISH + "\n")
    assert decoded.encoding == "windows-1252"
    assert decoded.warning is not None
    assert decoded.warning.startswith(AS_WINDOWS_1252)
    assert "its 3 characters outside ASCII are wrong" in decoded.warning


def test_a_long_ascii_line_is_sampled_in_linear_time() -> None:
    """The lines outside ASCII were matched by a pattern that backtracked
    across every ASCII line from each of its positions: 13 s for this one
    line of 64 KB, and 10.8 s for a 40 MB log (docs/09 P38-7)."""
    data = b"x" * 64_000 + b"\n" + DISH.encode("cp1252") + b"\n"
    decode_text(b"caf\xe9")  # charset-normalizer's import is not the sample's

    start = time.perf_counter()
    decoded = decode_text(data)
    elapsed = time.perf_counter() - start

    assert decoded.text.endswith(DISH + "\n")
    assert elapsed < 2, f"{elapsed:.2f} s to decode {len(data)} bytes"


def test_windows_1252_is_the_encoding_standards_every_byte() -> None:
    """Python's cp1252 where it defines a byte, and the C1 control of that
    number for the five it refuses."""
    data = bytes(range(0x100))
    expected = "".join(
        bytes([byte]).decode("cp1252", errors="ignore") or chr(byte) for byte in data
    )

    decoded = decode_text(data[0x80:] + data[:0x80])

    assert decoded.encoding == "windows-1252"
    assert decoded.text == expected[0x80:] + expected[:0x80]


def test_one_character_is_counted_as_one() -> None:
    decoded = decode_text(b"caf\xe9")

    assert decoded.warning is not None
    assert "its 1 character outside ASCII is wrong" in decoded.warning


def test_text_windows_1252_cannot_read_goes_to_charset_normalizer() -> None:
    member = _members()["notes/russian.txt"]

    decoded = decode_text(member)

    assert decoded.encoding == "cp1251"
    assert RUSSIAN_WORD in decoded.text
    assert decoded.warning is not None
    assert decoded.warning.startswith(AS_CP1251)


def test_seven_bit_text_with_iso_2022_escapes_is_not_taken_for_ascii() -> None:
    """ISO-2022-JP is ASCII byte by byte, and so valid UTF-8; MarkItDown's
    guess read it right, and the rule must too."""
    decoded = decode_text(JAPANESE.encode("iso2022_jp"))

    assert (decoded.text, decoded.encoding) == (JAPANESE, "iso2022_jp")
    assert decoded.warning is not None
    assert decoded.warning.startswith(AS_ISO_2022_JP)


def test_a_terminal_log_is_ascii_whatever_its_escapes() -> None:
    """Colour codes, and the `ESC ( B` that resets a terminal's character set:
    the guess reads it as UTF-8 does, so nothing was guessed."""
    log = b"\x1b[31mfailed\x1b(B\x1b[m in 3 s\n"

    assert decode_text(log) == DecodedText(log.decode(), "utf-8")


def test_utf8_with_an_escape_is_utf8() -> None:
    """ISO 2022 is seven-bit; text with a letter outside ASCII is not it,
    whatever escape it holds.  Taken for it, `Zoë` read as `ZoÃ«`."""
    log = "\x1b(B Zoë\n".encode()

    assert decode_text(log) == DecodedText(log.decode(), "utf-8")


#: How a guess for text with NUL bytes begins.
WITH_NULS = "The file holds NUL bytes and does not say which encoding it is in"


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_text_with_no_mark_and_nul_bytes_is_not_taken_for_utf8(codec: str) -> None:
    """All of it ASCII, UTF-16 is valid UTF-8 with a NUL beside every letter,
    and was read so, unnamed; MarkItDown's guess had read it (P38-12)."""
    text = "Meeting notes\nThe committee met on Tuesday.\n"

    decoded = decode_text(text.encode(codec))

    assert decoded.text == text
    assert decoded.encoding == codec.replace("-", "_")
    assert decoded.warning is not None
    assert decoded.warning.startswith(
        f"{WITH_NULS}, so WebShot read it as {decoded.encoding}, "
        "charset-normalizer's guess from all of it; "
    )


def test_utf8_padded_with_nul_bytes_is_utf8() -> None:
    """A log a crash left NULs at the end of is still UTF-8, unnamed."""
    log = "started: caf\N{LATIN SMALL LETTER E WITH ACUTE}\n".encode() + b"\x00" * 64

    assert decode_text(log) == DecodedText(log.decode(), "utf-8")


@pytest.mark.parametrize("html_label", [False, True])
@pytest.mark.parametrize("label", ["utf-16", "UTF-16LE", "utf-16be", "utf-32", "cp037"])
def test_a_declaration_cannot_name_an_encoding_it_is_not_written_in(
    label: str, html_label: bool
) -> None:
    """A declaration is read as ASCII, so one naming UTF-16, UTF-32 or EBCDIC
    is wrong about its file: decoded by it, a Windows-1252 page read as CJK,
    unnamed (P38-12)."""
    data = DISH.encode("cp1252") + b"!!"

    decoded = decode_text(data, declared=label, html=html_label)

    assert decoded.text == DISH + "!!"
    assert decoded.warning is not None
    assert decoded.warning.startswith(
        f"The file says it is in {label}, but it is not, so WebShot read it as "
        "windows-1252, "
    )


def test_bytes_no_encoding_reads_are_windows_1252_and_said_to_be() -> None:
    """Every byte decodes: the five Python's cp1252 refuses as the Encoding
    Standard's C1 controls."""
    decoded = decode_text(bytes(range(0x80, 0x100)) * 4)

    assert decoded.encoding == "windows-1252"
    assert decoded.text[:2] == "\N{EURO SIGN}\x81"
    assert decoded.warning is not None
    assert decoded.warning.startswith(
        f"{GUESSED}, and no encoding WebShot tried reads it as text, so it was "
        "read as windows-1252: its 512 characters outside ASCII are probably wrong"
    )


# --------------------------------------------------------------------------- #
# Every reader that meets text
# --------------------------------------------------------------------------- #


def test_a_windows_csv_is_read_and_its_guess_named() -> None:
    converted = to_markdown(FIXTURES / "windows_1252.csv", suffix=".csv")

    assert SENTINELS["windows_1252.csv"] in converted.markdown
    assert "\N{REPLACEMENT CHARACTER}" not in converted.markdown
    assert converted.warnings == (CSV_WARNING,)


def test_text_whose_first_letter_is_late_is_read() -> None:
    """docs/09 P35-3's case: MarkItDown guessed ASCII, and the run ended."""
    converted = to_markdown(FIXTURES / "windows_1252_late.txt", suffix=".txt")

    assert SENTINELS["windows_1252_late.txt"] in converted.markdown
    assert len(converted.warnings) == 1
    assert converted.warnings[0].startswith(AS_WINDOWS_1252)


def test_a_windows_markdown_file_is_read_and_its_guess_named() -> None:
    page, kind, warnings = adapters._render_local_file(FIXTURES / "windows_1252.md")

    assert kind == "markdown"
    assert SENTINELS["windows_1252.md"] in html.unescape(page)
    assert "\N{REPLACEMENT CHARACTER}" not in page
    assert len(warnings) == 1
    assert warnings[0].startswith(AS_WINDOWS_1252)


@pytest.mark.parametrize("name", ["empty.yaml", "empty.yml", "empty.xml"])
def test_an_empty_text_file_is_an_empty_page(name: str) -> None:
    assert Path(name).suffix in TEXT_SUFFIXES
    converted = to_markdown(FIXTURES / name, suffix=Path(name).suffix)

    assert (converted.markdown, converted.warnings) == ("", ())


def test_every_text_format_is_taken_by_the_plain_text_reader() -> None:
    """The suffixes it takes are the formats WebShot presents as text."""
    assert {
        suffix
        for suffix, kind in adapters.FORMATS.items()
        if kind.presentation == "verbatim"
    } == TEXT_SUFFIXES
    assert {".txt", ".log", ".json", ".jsonl", ".xml", ".yaml", ".yml"} <= TEXT_SUFFIXES


def test_each_member_is_read_and_each_guess_named() -> None:
    converted = to_markdown(ARCHIVE, suffix=".zip")
    sections = _sections(converted.markdown)

    assert set(sections) == set(MEMBERS)
    assert SENTINELS["legacy_archive.zip"] in sections["notes/recipe.txt"]
    assert JAPANESE.strip() in sections["notes/japanese.txt"]
    for name in set(MEMBERS) - {
        "notes/russian.txt",
        "notes/japanese.txt",
        "data/empty.yaml",
    }:
        assert DISH in html.unescape(sections[name]), name
    assert RUSSIAN_WORD in sections["notes/russian.txt"]
    named = [warning.split(": ", 1) for warning in converted.warnings]
    assert [name for name, _ in named] == [
        name for name, prefix in MEMBERS.items() if prefix is not None
    ]
    for name, warning in named:
        prefix = MEMBERS[name]
        assert prefix is not None
        assert warning.startswith(prefix), name


def test_a_member_whose_declaration_names_no_text_codec_is_read() -> None:
    """Each was left out of the page, its reader ended by the declaration's
    own label, where it should have gone to the guess (docs/09 P38-9)."""
    converted = to_markdown(LABELS, suffix=".zip")
    sections = _sections(converted.markdown)
    members = {
        f"notes/{label.replace(chr(0), 'nul')}.xml": label
        for label in UNDECODABLE_LABELS
    } | {"web/base64.html": "base64"}

    assert set(sections) == set(members)
    named = dict(warning.split(": ", 1) for warning in converted.warnings)
    assert set(named) == set(members)
    for name, label in members.items():
        assert SENTINELS["declared_labels.zip"] in html.unescape(sections[name]), name
        assert DISH in html.unescape(sections[name]), name
        shown = label.replace("\x00", "\\x00")  # as the warning shows a NUL
        assert named[name].startswith(f"The file says it is in {shown}"), name
        # The NUL in `utf\x00` is in the file too, so it is read by the guess
        # for text with NUL bytes, which reads it as cp1252.
        assert "so WebShot read it as " in named[name], name


def test_wide_text_and_impossible_declarations_are_read_and_named() -> None:
    converted = to_markdown(WIDE, suffix=".zip")
    sections = {
        name: html.unescape(part)
        for name, part in _sections(converted.markdown).items()
    }
    named = dict(warning.split(": ", 1) for warning in converted.warnings)

    assert set(sections) == {
        "notes/ascii-utf16le.txt",
        "data/ascii-utf16be.json",
        "notes/letters-utf16le.txt",
        "logs/padded.log",
        "web/utf16-meta.html",
        "data/utf16-declaration.xml",
    }
    for name, part in sections.items():
        if name != "logs/padded.log":
            assert "\x00" not in part, name
    assert "The committee met on Tuesday." in sections["notes/ascii-utf16le.txt"]
    assert "The committee met." in sections["data/ascii-utf16be.json"]
    for name in (
        "notes/letters-utf16le.txt",
        "web/utf16-meta.html",
        "data/utf16-declaration.xml",
    ):
        assert f"{DISH} \N{EM DASH} {SENTINELS['wide_text.zip']}" in sections[name], (
            name
        )
    assert set(named) == set(sections) - {"logs/padded.log"}
    for name in (
        "notes/ascii-utf16le.txt",
        "data/ascii-utf16be.json",
        "notes/letters-utf16le.txt",
    ):
        assert named[name].startswith(WITH_NULS), name
    assert named["web/utf16-meta.html"].startswith(
        "The file says it is in utf-16, but it is not"
    )
    assert named["data/utf16-declaration.xml"].startswith(
        "The file says it is in utf-16, but it is not"
    )


def test_a_chapter_whose_encoding_was_guessed_is_named() -> None:
    """MarkItDown's EPUB reader keeps only a chapter's Markdown, so its guess,
    cp1250, said nothing."""
    converted = to_markdown(BOOK, suffix=".epub")

    assert SENTINELS["legacy_book.epub"] in html.unescape(converted.markdown)
    assert DISH in html.unescape(converted.markdown)
    assert converted.title == "Legacy chapter"
    assert len(converted.warnings) == 1
    assert converted.warnings[0].startswith(f"OEBPS/chapter.xhtml: {AS_WINDOWS_1252}")


def test_every_fixture_has_a_generator_entry() -> None:
    """A fixture nobody can regenerate is the opaque binary this avoids."""
    assert {path.name for path in FIXTURES.iterdir()} == set(WRITERS)
    assert set(SENTINELS) <= set(WRITERS)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


@pytest.mark.browser
@pytest.mark.parametrize("name", sorted(SENTINELS))
def test_each_guess_reaches_the_manifest(tmp_path: Path, name: str) -> None:
    fixture = FIXTURES / name
    pdf = tmp_path / f"{fixture.stem}.pdf"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            str(fixture),
            "--output",
            str(pdf),
            "--report",
            str(tmp_path / "report.json"),
            "--no-ocr",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    bundle = pdf.with_suffix(".ai")
    for artifact in ("content.md", "content.txt", "chunks.jsonl"):
        assert SENTINELS[name] in (bundle / artifact).read_text("utf-8"), artifact

    _page, _kind, expected = adapters._render_local_file(fixture)
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    guesses = [
        warning for warning in manifest["warnings"] if "(docs/09 P38)" in warning
    ]
    assert guesses == expected
    assert guesses, "every fixture holds at least one guessed encoding"
