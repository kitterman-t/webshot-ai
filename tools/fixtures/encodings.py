"""Write the local text fixtures that are not UTF-8 (docs/09 P38).

    uv run python tools/fixtures/encodings.py

Each is text as a legacy editor or export writes it: Windows-1252 from Excel
and Notepad, a page and a feed that name their encoding, Russian in
Windows-1251, UTF-16 behind a byte order mark as Excel's "Unicode Text"
writes it, and empty YAML and XML files.  Before docs/09 P38 MarkItDown
guessed each encoding from 64 KB and said nothing, or failed.  Every word is
made up or generic prose; no fixture holds data from a course, a person or an
organization.

A separate generator from `office.py`, whose decks and workbooks come out
with new bytes on every run: these fixtures are plain bytes, and regenerating
them changes nothing unless their text did.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # run as a script, `tools` is not a package root
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import _EPUB_CONTAINER, _write_archive  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "encodings"

#: One per fixture, each with a letter outside ASCII, so it reaches the bundle
#: only if the file is decoded in the encoding it is in.
SENTINELS = {
    "windows_1252.csv": "SENTINEL CSV WINDOWS JALAPE\N{LATIN CAPITAL LETTER N WITH TILDE}O",
    "windows_1252_late.txt": "SENTINEL TXT LATE CR\N{LATIN CAPITAL LETTER E WITH GRAVE}ME",
    "windows_1252.md": "SENTINEL MARKDOWN WINDOWS PI\N{LATIN CAPITAL LETTER N WITH TILDE}ATA",
    "legacy_archive.zip": "SENTINEL ZIP WINDOWS SOUFFL\N{LATIN CAPITAL LETTER E WITH ACUTE}",
    "legacy_book.epub": "SENTINEL EPUB WINDOWS FA\N{LATIN CAPITAL LETTER C WITH CEDILLA}ADE",
    "declared_labels.zip": "SENTINEL ZIP DECLARED R\N{LATIN CAPITAL LETTER E WITH ACUTE}SUM\N{LATIN CAPITAL LETTER E WITH ACUTE}",
    "wide_text.zip": "SENTINEL ZIP WIDE NA\N{LATIN CAPITAL LETTER I WITH DIAERESIS}VE",
}

#: Bytes of ASCII before the late file's first letter outside it: past the
#: 65,536 MarkItDown guesses an encoding from, with room to spare.
LATE_AFTER = 80_000

#: Generic prose in Russian, long enough for charset-normalizer to name its
#: encoding once windows-1252 is ruled out.  Cyrillic on purpose, so the
#: letter RUF001 takes for a Latin look-alike is what the fixture is for.
RUSSIAN = (
    "Собрание общества состоялось во вторник вечером в городской библиотеке. "
    "После краткого отчёта казначея участники обсудили бюджет на следующий "
    "год. Несколько добровольцев предложили устроить в июне праздник с "  # noqa: RUF001
    "ярмаркой и играми для детей.\n"
)


#: A generic Japanese sentence, for ISO-2022-JP: seven-bit, so ASCII byte by
#: byte, which MarkItDown's guess read right and a UTF-8 test would not.
JAPANESE = "協会の会合は火曜日の夕方、市立図書館で開かれました。\n"


def write_windows_csv(path: Path) -> None:
    """A CSV as Excel's "CSV (Comma delimited)" writes it on Windows.

    Windows-1252 with CRLF line ends: before P38 every letter outside ASCII
    became U+FFFD, and the curly quotes and the euro sign with them.
    """
    rows = [
        "item,origin,price,note",
        "Cr\u00e8me br\u00fbl\u00e9e,Dijon,\u20ac7,\u201cclassic\u201d",
        "Jalape\u00f1o poppers,Oaxaca,\u20ac5,\u2014 spicy",
        "Sm\u00f6rg\u00e5sbord,Malm\u00f6,\u20ac24,serves \u201cfour\u201d",
        f"{SENTINELS['windows_1252.csv']},\u2013,\u20ac0,\u2026",
    ]
    path.write_bytes(("\r\n".join(rows) + "\r\n").encode("cp1252"))


def write_windows_late_text(path: Path) -> None:
    """Text whose first letter outside ASCII is past MarkItDown's sample.

    The case docs/09 P35-3 left: MarkItDown guessed ASCII from the first
    64 KB and the capture ended with exit 2.  Numbered prose, so that Magika
    names it as text and MarkItDown guesses at all.
    """
    line = "{:05d} Counting the rows of a ledger, one entry at a time.\n"
    count = LATE_AFTER // len(line.format(0)) + 1
    text = (
        "".join(line.format(index) for index in range(count))
        + f"{SENTINELS['windows_1252_late.txt']} \u2014 \u201cdone\u201d\n"
    )
    path.write_bytes(text.encode("cp1252"))


def write_windows_markdown(path: Path) -> None:
    """Markdown saved as "ANSI" in Notepad, which WebShot read as UTF-8 itself."""
    text = (
        "# Cr\u00e8me br\u00fbl\u00e9e\n\n"
        "A recipe card, saved as \u201cANSI\u201d.\n\n"
        "- Sugar \u2014 50 g\n"
        f"- {SENTINELS['windows_1252.md']}\n"
    )
    path.write_bytes(text.replace("\n", "\r\n").encode("cp1252"))


def write_legacy_archive(path: Path) -> None:
    """A member for each way WebShot now decides a text's encoding.

    Guessed windows-1252, and named in the manifest: the note, the JSON, the
    YAML and the page, which MarkItDown had read as cp1250 or cp775.  Guessed
    by charset-normalizer, because windows-1252 does not read as text: the
    Russian note, and the Japanese one in seven-bit ISO-2022-JP, which a test
    for UTF-8 alone takes for ASCII.  Declared, and not named: a page's
    `meta` and a feed's XML declaration.  Marked: UTF-16, as Excel's "Unicode
    Text" writes it.  And an empty YAML file, which no reader took.
    """
    cp1252 = "cp1252"
    members: list[tuple[str, str | bytes]] = [
        (
            "notes/recipe.txt",
            (
                "Cr\u00e8me br\u00fbl\u00e9e for the caf\u00e9 \u2014 \u201ceasy\u201d\n"
                f"{SENTINELS['legacy_archive.zip']}\n"
            ).encode(cp1252),
        ),
        (
            "data/menu.json",
            '{"dish": "Cr\u00e8me br\u00fbl\u00e9e", "price": "\u20ac7"}\n'.encode(
                cp1252
            ),
        ),
        (
            "data/menu.yaml",
            "dish: Cr\u00e8me br\u00fbl\u00e9e\nprice: \u20ac7\n".encode(cp1252),
        ),
        (
            "web/page.html",
            (
                "<html><head><title>Menu</title></head><body><p>Cr\u00e8me "
                "br\u00fbl\u00e9e, \u00e0 la carte</p></body></html>\n"
            ).encode(cp1252),
        ),
        (
            "web/declared.html",
            (
                '<html><head><meta charset="windows-1252"><title>Menu</title>'
                "</head><body><p>Cr\u00e8me br\u00fbl\u00e9e, \u00e0 la "
                "carte</p></body></html>\n"
            ).encode(cp1252),
        ),
        (
            "web/feed.xml",
            (
                '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
                "<menu><dish>Cr\u00e8me br\u00fbl\u00e9e \u00e0 "
                "l'ancienne</dish></menu>\n"
            ).encode("latin-1"),
        ),
        ("notes/russian.txt", RUSSIAN.encode("cp1251")),
        ("notes/japanese.txt", JAPANESE.encode("iso2022_jp")),
        (
            "data/unicode.txt",
            "dish\tprice\r\nCr\u00e8me br\u00fbl\u00e9e\t\u20ac7\r\n".encode("utf-16"),
        ),
        ("data/empty.yaml", b""),
    ]
    _write_archive(path, members)


#: Labels a document can declare that name no codec for text: codecs from
#: bytes to bytes, which `bytes.decode` refuses with LookupError; one Python's
#: `undefined` codec refuses with UnicodeError; and one holding a NUL, which
#: `codecs.lookup` refuses with ValueError.
UNDECODABLE_LABELS = ("hex", "base64", "undefined", "utf\x00")


def write_declared_labels(path: Path) -> None:
    """Windows-1252 members whose declarations name no codec for text.

    Each ended its reader with an exception, so the member was left out of
    the page, where it should have gone to the guess (docs/09 P38-9): an XML
    declaration for each label in `UNDECODABLE_LABELS`, and a page whose
    `meta` says `base64`.
    """
    body = f"Cr\u00e8me br\u00fbl\u00e9e \u2014 {SENTINELS['declared_labels.zip']}"
    members: list[tuple[str, str | bytes]] = [
        (
            f"notes/{label.replace(chr(0), 'nul')}.xml",
            (f'<?xml version="1.0" encoding="{label}"?>\n<note>{body}</note>\n').encode(
                "cp1252"
            ),
        )
        for label in UNDECODABLE_LABELS
    ]
    members.append(
        (
            "web/base64.html",
            (
                '<html><head><meta charset="base64"><title>Menu</title></head>'
                f"<body><p>{body}</p></body></html>\n"
            ).encode("cp1252"),
        )
    )
    _write_archive(path, members)


def _even(data: bytes) -> bytes:
    """`data`, a line feed longer if its length is odd: UTF-16 decodes it."""
    return data + b"\n" if len(data) % 2 else data


def write_wide_text(path: Path) -> None:
    """Text with NUL bytes, and declarations no ASCII-read file can mean.

    UTF-16 with no byte order mark: all of it ASCII, in either byte order,
    which is valid UTF-8 with a NUL beside every letter and was read so, and
    with letters outside ASCII.  A UTF-8 log padded with NULs, which is UTF-8.
    And a Windows-1252 page and feed, each an even number of bytes, declaring
    UTF-16, which decoded as CJK (docs/09 P38-12).
    """
    letters = f"Cr\u00e8me br\u00fbl\u00e9e \u2014 {SENTINELS['wide_text.zip']}\n"
    members: list[tuple[str, str | bytes]] = [
        (
            "notes/ascii-utf16le.txt",
            "Meeting notes\nThe committee met on Tuesday.\n".encode("utf-16-le"),
        ),
        (
            "data/ascii-utf16be.json",
            '{"note": "The committee met."}\n'.encode("utf-16-be"),
        ),
        ("notes/letters-utf16le.txt", letters.encode("utf-16-le")),
        ("logs/padded.log", "started: caf\u00e9\n".encode() + b"\x00" * 64),
        (
            "web/utf16-meta.html",
            _even(
                (
                    '<html><head><meta charset="utf-16"><title>Menu</title></head>'
                    f"<body><p>{letters}</p></body></html>\n"
                ).encode("cp1252")
            ),
        ),
        (
            "data/utf16-declaration.xml",
            _even(
                f'<?xml version="1.0" encoding="UTF-16"?>\n<note>{letters}</note>\n'.encode(
                    "cp1252"
                )
            ),
        ),
    ]
    _write_archive(path, members)


_LEGACY_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:webshot-legacy-fixture</dc:identifier>
    <dc:title>Legacy chapter</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""


def write_legacy_book(path: Path) -> None:
    """An EPUB whose chapter is Windows-1252 and does not say so.

    Out of spec, which asks for UTF-8 or UTF-16, but written that way by
    converters that copied a legacy page as it was.  MarkItDown's EPUB reader
    keeps only each chapter's Markdown, so the guess is named by WebShot's.
    """
    chapter = (
        "<html><head><title>Chapter</title></head><body>"
        f"<p>{SENTINELS['legacy_book.epub']}</p>"
        "<p>Cr\u00e8me br\u00fbl\u00e9e, \u201cserved cold\u201d.</p>"
        "</body></html>\n"
    ).encode("cp1252")
    _write_archive(
        path,
        [
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", _EPUB_CONTAINER),
            ("OEBPS/content.opf", _LEGACY_OPF),
            ("OEBPS/chapter.xhtml", chapter),
        ],
        first_stored=True,
    )


def write_empty(path: Path) -> None:
    """Empty: no reader took a YAML or XML file Magika could not name."""
    path.write_bytes(b"")


WRITERS = {
    "windows_1252.csv": write_windows_csv,
    "windows_1252_late.txt": write_windows_late_text,
    "windows_1252.md": write_windows_markdown,
    "legacy_archive.zip": write_legacy_archive,
    "legacy_book.epub": write_legacy_book,
    "declared_labels.zip": write_declared_labels,
    "wide_text.zip": write_wide_text,
    "empty.yaml": write_empty,
    "empty.yml": write_empty,
    "empty.xml": write_empty,
}


def main() -> int:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    for name, writer in WRITERS.items():
        destination = FIXTURE_ROOT / name
        writer(destination)
        print(f"wrote {destination.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
