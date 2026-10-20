"""How WebShot reads local text, whatever it is encoded in (docs/09 P38).

The one module that imports charset-normalizer (CONTRIBUTING rule 1).

Every text WebShot reads from a local file is decoded here: a CSV or TSV file
and a Markdown file, which WebShot reads itself, and plain text, JSON, XML,
YAML and an HTML member of a zip, which MarkItDown reads for it.  Left to
MarkItDown, an encoding was guessed from the first 64 KB and nothing said so:
Windows-1252 text was read as a Central European or Baltic code page, or
failed, and a CSV or TSV lost every accented letter (docs/09 P35-3).  The
order is what is known first, then what is guessed:

1. **A byte order mark**, UTF-8 or UTF-16 in either byte order, says what the
   text is.  It is not part of the text, so it does not reach the page.
2. **Text that is UTF-8 to its last byte is UTF-8** (docs/09 P35): a legacy
   encoding all but never forms valid UTF-8 sequences, and text that is UTF-8
   is the case to keep unchanged.  The exceptions go on to step 4: seven-bit
   text with ISO 2022 escapes, ISO-2022-JP's among them, which is ASCII byte
   by byte, and text holding a NUL byte, as UTF-16 and UTF-32 without a mark
   do, which is UTF-8 too when all of it is ASCII.
3. **An encoding the document declares**, where its format has a way to, if
   it decodes the bytes: an XML declaration, or an HTML `meta`, whose
   `iso-8859-1` and `us-ascii` mean windows-1252 as the Encoding Standard has
   it.  The caller finds the declaration; this module does not parse markup.
   A label that names no codec for text is no declaration, and nor is one
   naming an encoding the declaration itself could not be written in, such
   as UTF-16 or EBCDIC.
4. **A guess, which the manifest names.**  Windows-1252 — the encoding Windows
   wrote unlabeled text in for Western European languages, and that browsers
   assume for it — unless, decoded that way, the lines holding a byte outside
   ASCII, or an escape, read as mess to charset-normalizer; then
   charset-normalizer's own choice for those lines; and windows-1252 again if
   it has none.  Text holding a NUL is charset-normalizer's choice from all of
   it.  A guess that decodes the bytes as UTF-8 does is not one.

Why not charset-normalizer alone, which is what MarkItDown asks: it measures
five 512-byte stretches of a file, so a few accented letters among ASCII are
seldom in what it reads, and every single-byte code page scores alike.  Over 58
synthetic Western European files it chose correctly for 21; `è` became `č`
in cp1250, and Spanish became Big5.  Windows-1252 alone was right for all 49
Windows-1252 and Latin-1 files and for none in Cyrillic, Greek or CJK, whose
bytes it decodes as runs of accented capitals the mess test rejects.  The
order above was right for 56 of the 58 and for 145 of 259 in other encodings,
where charset-normalizer alone was right for 107 (docs/09 P38-2).
"""

from __future__ import annotations

import codecs
import functools
import re
import string
from dataclasses import dataclass

#: The marks a text may begin with, longest first, and the codec each names.
#: UTF-32 is not among them: the Encoding Standard does not sniff it, and its
#: little-endian mark is UTF-16's followed by a NUL.
BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF8, "utf-8-sig", "UTF-8"),
    (codecs.BOM_UTF16_LE, "utf-16", "UTF-16"),
    (codecs.BOM_UTF16_BE, "utf-16", "UTF-16"),
)

#: The highest mess charset-normalizer may find in windows-1252's reading of
#: a text's non-ASCII lines for that reading to stand.  Every Western European
#: file of docs/09 P38-2 scored 0.057 or less; text in Cyrillic, Greek,
#: Hebrew, Arabic, Thai or CJK scored above 1.0, or held a byte windows-1252
#: leaves undefined.
WINDOWS_1252_MESS = 0.1

#: Windows-1252 as the Encoding Standard defines it, which differs from
#: Python's cp1252 in five bytes, 0x81, 0x8D, 0x8F, 0x90 and 0x9D: Python
#: refuses them, and a browser reads each as the C1 control of that number,
#: which is the code point Latin-1 gives it.  One character per byte, for
#: `codecs.charmap_decode`: `str.translate` over the Latin-1 decoding gave
#: the same text at 1.4 s per 40 MB (docs/09 P38-7).
_WINDOWS_1252 = "".join(
    bytes([byte]).decode("latin-1")
    if byte in (0x81, 0x8D, 0x8F, 0x90, 0x9D)
    else bytes([byte]).decode("cp1252")
    for byte in range(256)
)
#: The characters a declaration is written in.  It is found by reading the
#: bytes as ASCII, so it is the document's word only about an encoding that
#: reads these as themselves: one that names UTF-16, UTF-32 or EBCDIC was
#: read in an encoding it does not name, so it is wrong about its own file,
#: and decoding by it read a windows-1252 page as CJK (docs/09 P38-12).  The
#: Encoding Standard reads a `meta` naming UTF-16 as UTF-8 for this reason.
_DECLARATION = (string.ascii_letters + string.digits + " \t\r\n<>?!/=\"'-_.:;").encode(
    "ascii"
)
#: Labels that the Encoding Standard reads as windows-1252 in HTML, by the
#: name Python's codec registry gives each.
_HTML_WINDOWS_1252 = frozenset({"iso8859-1", "ascii", "cp1252"})
#: An ISO 2022 designation: the escape with which ISO-2022-JP, -KR and -CN
#: switch character sets inside seven-bit bytes.  Such text is ASCII, and so
#: valid UTF-8, to a byte-level test; MarkItDown's guess had read it right.
#: A terminal's colour codes (`ESC [`) are not designations.
_ISO_2022_DESIGNATION = re.compile(rb"\x1b[$()*+\-./]")


@dataclass(frozen=True, slots=True)
class DecodedText:
    """A text, the encoding it was read in, and whether that was a guess."""

    text: str
    #: The codec it was read by: Python's name for it, or `windows-1252` for
    #: the Encoding Standard's, which Python has no codec for.
    encoding: str
    #: For the manifest: why the text may not be what its author wrote, or
    #: `None` where its encoding was known.
    warning: str | None = None


def _windows_1252(data: bytes) -> str:
    text: str = codecs.charmap_decode(data, "strict", _WINDOWS_1252)[0]
    return text


def _non_ascii_lines(data: bytes) -> bytes:
    """The lines of `data` that hold a byte outside ASCII, or an escape.

    Split, not matched: a pattern for such a line tried every position of
    every ASCII line and backtracked across the rest of it, so a 40 MB log
    with an accented line in a thousand took 10.8 s to sample (docs/09 P38-7).
    """
    return b"\n".join(
        line for line in data.split(b"\n") if not line.isascii() or b"\x1b" in line
    )


def _outside_ascii(text: str) -> str:
    """How many of `text`'s characters are outside ASCII, as a subject."""
    count = len(text) - len(text.encode("ascii", "ignore"))
    if count == 1:
        return "its 1 character outside ASCII is"
    return f"its {count} characters outside ASCII are"


def _shown(label: str) -> str:
    """`label` with each character that does not print as an escape."""
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in label
    )


def _codec(label: str, *, html: bool) -> str | None:
    """The codec a declared label names, if it is one that decodes text.

    A declaration is the document's own word, so any label can arrive: one
    holding a NUL, which `codecs.lookup` refuses with ValueError, and `hex`,
    `base64`, `zlib` and the other codecs from bytes to bytes, which
    `bytes.decode` refuses with LookupError.  Each ended the capture where it
    should have gone to the guess (docs/09 P38-9).
    """
    try:
        info = codecs.lookup(label)
    except (LookupError, ValueError):
        return None
    if not info._is_text_encoding:
        return None
    name = info.name
    return "windows-1252" if html and name in _HTML_WINDOWS_1252 else name


def _decode(data: bytes, codec: str) -> str | None:
    if codec == "windows-1252":
        return _windows_1252(data)
    try:
        return data.decode(codec)
    except UnicodeError:
        # Not only UnicodeDecodeError: `punycode` and `undefined` raise the
        # base class (docs/09 P38-9).
        return None


def decode_text(
    data: bytes, *, declared: str | None = None, html: bool = False
) -> DecodedText:
    """`data` as text, by the order in this module's docstring.

    `declared` is the label the document gives its own encoding, if its
    format has a way to; `html` reads that label as HTML does.  Every byte is
    decoded: where the encoding was guessed, or a byte order mark is wrong,
    `warning` says so.
    """
    for mark, codec, name in BYTE_ORDER_MARKS:
        if data.startswith(mark):
            try:
                return DecodedText(data.decode(codec), codec)
            except UnicodeDecodeError:
                return DecodedText(
                    data.decode(codec, errors="replace"),
                    codec,
                    f"The file begins with a {name} byte order mark but is not "
                    f"{name} throughout, so the page shows U+FFFD "
                    "(\N{REPLACEMENT CHARACTER}) where it is not (docs/09 P38).",
                )
    try:
        utf8: str | None = data.decode("utf-8")
    except UnicodeDecodeError:
        utf8 = None
    # Seven-bit first: UTF-8 with a letter outside ASCII and a terminal's
    # `ESC ( B` is not ISO 2022, and the guess read it as windows-1252
    # (docs/09 P38-8).  The escape is looked for before the pattern is run.
    seven_bit = (
        utf8 is not None
        and data.isascii()
        and b"\x1b" in data
        and _ISO_2022_DESIGNATION.search(data) is not None
    )
    # NUL is UTF-8 too, so UTF-16 without a byte order mark, all of it ASCII,
    # was read as UTF-8 with a NUL beside every letter (docs/09 P38-12).
    nul = b"\x00" in data
    if utf8 is not None and not seven_bit and not nul:
        return DecodedText(utf8, "utf-8")
    named = None if declared is None else _codec(declared, html=html)
    if named is not None and _reads_declarations(named):
        text = _decode(data, named)
        if text is not None:
            return DecodedText(text, named)
    # The label is the document's text, so a NUL in it is shown, not written.
    shown = None if declared is None else _shown(declared)
    if shown is not None and named is None:
        reason = (
            f"The file says it is in {shown}, which is not a text encoding "
            "WebShot knows"
        )
    elif shown is not None:
        reason = f"The file says it is in {shown}, but it is not"
    elif nul:
        reason = "The file holds NUL bytes and does not say which encoding it is in"
    elif seven_bit:
        reason = (
            "The file holds ISO 2022 escapes and does not say which encoding it is in"
        )
    else:
        reason = "The file is not UTF-8 and does not say which encoding it is in"
    decoded = _guess(data, reason)
    # A guess that reads the bytes as UTF-8 does changed nothing to name: a
    # terminal log whose `ESC ( B` resets its character set is ASCII still.
    if decoded.text == utf8:
        return DecodedText(utf8, "utf-8")
    return decoded


@functools.cache
def _reads_declarations(codec: str) -> bool:
    """Whether a declaration could be written in `codec`, and so mean it."""
    return _decode(_DECLARATION, codec) == _DECLARATION.decode("ascii")


def _guess_wide(data: bytes, reason: str) -> DecodedText:
    """Text that holds NUL bytes, which is UTF-16 or UTF-32 with no mark.

    Neither the windows-1252 reading nor the lines outside ASCII mean
    anything for it: its ASCII letters are each beside a NUL, and its lines
    hold the NUL of their own line feed.  So charset-normalizer is asked
    about all of it, as MarkItDown asked it (docs/09 P38-12).
    """
    import charset_normalizer

    best = charset_normalizer.from_bytes(data, preemptive_behaviour=False).best()
    if best is not None:
        try:
            text = data.decode(best.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
        else:
            return DecodedText(
                text,
                best.encoding,
                f"{reason}, so WebShot read it as {best.encoding}, "
                "charset-normalizer's guess from all of it; if it is in another "
                "encoding, its text may be wrong throughout (docs/09 P38).",
            )
    return DecodedText(
        _windows_1252(data),
        "windows-1252",
        f"{reason}, and no encoding WebShot tried reads it as text, so it was "
        "read as windows-1252, NUL bytes and all: its text is probably wrong "
        "(docs/09 P38).",
    )


def _guess(data: bytes, reason: str) -> DecodedText:
    """Windows-1252 unless it reads as mess, then charset-normalizer's choice.

    Both are asked about the lines that hold a byte outside ASCII, not the
    whole file: charset-normalizer reads five 512-byte stretches of what it
    is given, and in a file of ASCII those are ASCII, which every single-byte
    code page decodes alike (docs/09 P38-2).
    """
    if b"\x00" in data:
        return _guess_wide(data, reason)
    # Imported here: only text that is not UTF-8 pays for it, and a Markdown
    # file's capture reaches this module without MarkItDown (docs/09 P10-15).
    import charset_normalizer

    sample = _non_ascii_lines(data)
    windows = charset_normalizer.from_bytes(
        sample,
        cp_isolation=["cp1252"],
        threshold=WINDOWS_1252_MESS,
        preemptive_behaviour=False,
    ).best()
    if windows is not None:
        text = _windows_1252(data)
        return DecodedText(
            text,
            "windows-1252",
            f"{reason}, so WebShot read it as windows-1252, which Windows "
            "wrote such text in for Western European languages and which "
            "browsers assume for it; if it is in another encoding, "
            f"{_outside_ascii(text)} wrong (docs/09 P38).",
        )
    best = charset_normalizer.from_bytes(sample, preemptive_behaviour=False).best()
    if best is not None:
        try:
            text = data.decode(best.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
        else:
            return DecodedText(
                text,
                best.encoding,
                f"{reason}, and as windows-1252 it does not read as text, so "
                f"WebShot read it as {best.encoding}, charset-normalizer's "
                "guess; if it is in another encoding, "
                f"{_outside_ascii(text)} wrong (docs/09 P38).",
            )
    text = _windows_1252(data)
    return DecodedText(
        text,
        "windows-1252",
        f"{reason}, and no encoding WebShot tried reads it as text, so it was "
        f"read as windows-1252: {_outside_ascii(text)} probably wrong "
        "(docs/09 P38).",
    )
