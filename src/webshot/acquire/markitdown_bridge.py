"""The one module that imports MarkItDown (CONTRIBUTING rule 1).

Bytes in, Markdown out.  Everything WebShot decides about a local file —
which formats it accepts, how big they may be, how the resulting page is
titled and styled — is decided in `adapters.py`; this file's whole job is to
hand MarkItDown a stream with an explicit type hint and hand back a string.

Twelve behaviors of MarkItDown are corrected here rather than inherited, because
each damages content WebShot promises to preserve (docs/09 P3-1):

1. **TSV is not a format MarkItDown knows.**  Its CSV converter is
   comma-only, so a tab-separated file falls through to the plain-text
   converter and the table disappears.  The bridge re-delimits with the
   standard library — a transcode, not a parse — so the CSV converter applies.
2. **Its CSV converter flattens a line break in a cell to a space.**  From
   0.1.8 it escapes the `|` delimiter itself, but a newline inside a cell
   still becomes a space, so a multi-line value loses its lines.  Newlines are
   rewritten to `<br>` on the way in, which is what HTML means by one.  The
   pipe is left to MarkItDown: escaping it here as well escaped it twice, and
   the page showed `a \\| b` (docs/09 P10-23).
3. **Its Office readers skip what sits beside a document's main structure.**
   python-pptx reads neither branch of `mc:AlternateContent`, where PowerPoint
   puts every shape that holds an equation, nor an equation, nor SmartArt; and
   MarkItDown's Word equations are all kept or all lost.  `ooxml` rewrites the
   archive first, and says what it still could not read (docs/09 P27): a file's
   own archive, or a member's inside a zip, by the same path (docs/09 P29).
4. **It writes a document's text into Markdown as if it were Markdown.**  The
   page is rendered from that Markdown, which passes raw HTML through and reads
   `#`, `*`, `[`, `3.` and the rest as structure.  So `<img src="https://…">`
   typed on a slide became an image the capture fetched, and `# Q3` became a
   heading (docs/09 P28: the class P8-9 closed for CSV cells alone).  Every
   string a document contributes is written as text here instead — its HTML
   and Markdown characters as character references, which the browser reads as
   the character and Markdown reads as nothing.  Where MarkItDown writes a
   string straight into Markdown (a deck's text, a sheet's name, an EPUB's
   metadata, a zip member's name and plain text) it is encoded on its way in;
   where markdownify writes it, markdownify is given a writer that encodes.
   Code is not encoded, being literal, but only a code block is literal
   whole: a `<pre>` written into a line of text, and inline code over
   several lines, were Markdown after a blank line or a line opening a
   heading, so they are written on one line, the `<pre>` as inline code
   (docs/09 P59).
   A link whose text is its target is the one string markdownify writes as
   markup on purpose, `<target>`, which Python-Markdown links only for http,
   https and ftp: `<a href="report.pdf">report.pdf</a>` became the tag
   `<report.pdf>`, which nh3 drops, and one reading `script` took the rest of
   the document with it.  The writer writes every link `[text](target)`, as
   markdownify writes one whose text differs, and a link in code as its text,
   since a code span holds no link (docs/09 P75).
5. **It writes two lists side by side as one.**  markdownify puts a blank line
   between two lists, and Python-Markdown reads a list of the same kind after a
   blank line as the first one going on: two EPUB `<ol>`s became one list,
   numbered on from the first, with every item a paragraph.  The writer marks
   where each list begins and ends, and an HTML comment, which nh3 drops from
   the page, goes where two meet (docs/09 P32).  Two quotations, or two
   definition lists, side by side were one in the same way, and are kept
   apart the same way (docs/09 P63).
6. **It guesses a text's encoding from its first 64 KB.**  A UTF-8 file whose
   first character outside ASCII comes later was decoded as ASCII, and its
   reader failed: on its own the capture ended with exit 2, and in a zip the
   member was left out (docs/09 P35).  The encoding of bytes WebShot wrote,
   and of any text that is UTF-8 to its last byte, is stated instead
   (`_declared_charset`).  Text that is not UTF-8 had the guess and nothing
   said so, so Windows-1252 was read as Central European; WebShot's plain-text
   and HTML readers decode it by `text_encoding` instead, and a guess is named
   in the manifest (docs/09 P38).
7. **It writes a list nested in an item at its parent's level.**  markdownify
   indents what belongs to an item by the width of the item's marker, two
   spaces under `* ` and three under `1. `, and Python-Markdown nests at four:
   a document's sub-items became items of their parent's list, or text inside
   it.  The writer indents by four, and leaves a blank line after a nested
   list that more of the item follows (docs/09 P40).  A list in a quotation
   in an item is the quotation's, and is written as one in no item is
   (docs/09 P74).  A `<pre>` in an item is an indented code block there,
   because Python-Markdown reads a fence only at the start of a line, and an
   item's fence printed its code as one line of text (docs/09 P44); so is one
   in a quotation or a definition, whose `> ` or `:   ` did the same (docs/09
   P47).
8. **It drops the whitespace at the edge of bold, italic, a link or code.**
   markdownify strips each inline element's text and gives back one ASCII
   space where the text began or ended with one, and nothing for anything
   else: a no-break space, a newline or a `<br>` at the edge was lost, and a
   run of whitespace alone was lost entirely, so
   `called<strong>&nbsp;SALT.&nbsp;</strong>We'll` read `calledSALT.We'll`
   and `This<strong> </strong>course` read `Thiscourse`.  The writer takes
   the edges off before markdownify sees them and writes them outside the
   markup (docs/09 P43, P49).
9. **It numbers every Word list from 1.**  mammoth reads a list item's level
   and whether it is ordered, never its number, so a list Word starts at 3,
   restarts at 4 or counts on past a paragraph was numbered 1, 2, 3 from the
   top of each `<ol>`, and two of Word's lists that met were one.  `ooxml`
   works out the number Word shows on each item and marks the paragraph with
   it; the HTML step here sets each `<ol>`'s start from the marks, splits it
   where the numbers stop running on, and takes the marks out (docs/09 P31).
10. **It writes a definition list that Python-Markdown reads as less of one.**
    markdownify puts a blank line before every term, and Python-Markdown takes
    only the lines straight above a definition as its terms: of two terms in a
    row the first became a paragraph.  It puts none before a definition, and
    one after a definition of two blocks was read into that definition's last
    block.  The writer puts terms in a row on adjacent lines, and a blank line
    after a definition of more than one block that another follows (docs/09
    P64).
11. **It writes a `<br>` as a space.**  markdownify writes Markdown's hard
    break, two spaces and a newline, and MarkItDown strips the end of every
    line, so `one<br/>two` and a Word file's manual line breaks reached the page
    as one line.  The writer writes `<br>` itself where text flows (docs/09
    P77).
12. **It reads an HTML file's line endings as text.**  An HTML parser reads
    `\\r\\n` and a lone `\\r` as `\\n` before anything else, so a page is
    the same page whichever line endings it was saved with; Python's
    `html.parser` does not, and markdownify kept each `\\r`.  The same file
    saved with Windows line endings gave other code than with Unix ones: a
    `<pre>`'s blank first and last lines stayed, and a `\\r` left before a
    `\\r\\n` was a line break inside an item but nothing outside it.  The
    reader takes the line endings as a browser does (docs/09 P92).

MarkItDown itself parses XML through `defusedxml` (its RSS and EPUB
converters), which is *not* what spec §6.6 rests on: the adapter defuses every
XML byte before this module is ever called, and the Office parts this module
and `ooxml` rewrite are read through `defusedxml` too.
"""

from __future__ import annotations

import base64
import binascii
import csv
import dataclasses
import functools
import html
import io
import itertools
import mimetypes
import posixpath
import re
import secrets
import warnings
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, unquote, urlsplit
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag
from bs4.builder._htmlparser import HTMLParserTreeBuilder
from bs4.dammit import EncodingDetector
from markitdown import (
    DocumentConverterResult,
    FileConversionException,
    MarkItDown,
    MarkItDownException,
    MissingDependencyException,
    StreamInfo,
    UnsupportedFormatException,
)
from markitdown.converter_utils.docx.math.omml import oMath2Latex
from markitdown.converters import (
    DocxConverter,
    EpubConverter,
    HtmlConverter,
    PlainTextConverter,
    PptxConverter,
    XlsxConverter,
    ZipConverter,
)

# MarkItDown's Markdown writer, a markdownify subclass every HTML step uses. It
# is internal, and imported here rather than where it is used so that a release
# which moves it fails the import instead of quietly writing text as Markdown.
from markitdown.converters._markdownify import _CustomMarkdownify

from ..errors import UsageError
from . import ooxml
from .adapters import CODE_LINES_CLASS, FORMATS
from .text_encoding import decode_text

#: Characters Python-Markdown (with `extra`) reads wherever they stand:
#: emphasis, code, links and images (`!` too, since text ending in one turns
#: the link markdownify writes after it into an image), escapes, attribute
#: lists, table cells, and the hashes it strips from the end of a heading.
INLINE_MARKDOWN = "!#*[\\]_`{|}"
#: Characters it reads only where a line begins, after up to three spaces:
#: bullets, a setext underline, a definition, a `~~~` fence, a table's
#: alignment row.  An ordered list's `3.` is caught by its full stop, and
#: indentation deep enough for a code block by `_LINE_START_RE` as well.
LINE_START_MARKDOWN = "+-:=~"

#: A document's text, written so that neither grammar reads it: HTML's three
#: characters as named references, Markdown's as numeric ones.  A reference
#: means its character to the browser and nothing to Markdown, and nh3 writes
#: the character back when it serializes the page, so text that needed none of
#: this renders exactly as it did — the anchors `toc` derives included.  Only
#: what Markdown would read is encoded: Python-Markdown stashes each reference
#: on its own, and encoding every full stop made a large CSV 70% slower.
_AS_TEXT = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
    | {character: f"&#{ord(character)};" for character in INLINE_MARKDOWN}
)
_LINE_START_RE = re.compile(
    rf"^(?P<indent>[ \t]*)(?P<opener>[{re.escape(LINE_START_MARKDOWN)}]|\d+\.)?",
    re.MULTILINE,
)

#: Attributes markdownify writes into Markdown as a link or image destination.
URL_ATTRIBUTES = ("href", "src", "data-src", "poster")
#: Attributes it writes into Markdown as text: an image's alt, a link's title.
TEXT_ATTRIBUTES = ("alt", "title")
#: What ends a Markdown destination early, or starts something inside it.
#: Percent-encoded, which a URL means the same by.
_DESTINATION_BREAK_RE = re.compile(r"[\s()<>\\`\"']")


def _reference(characters: str) -> str:
    return "".join(f"&#{ord(character)};" for character in characters)


def _line_start(match: re.Match[str]) -> str:
    indent, opener = match["indent"], match["opener"] or ""
    # A tab, or four spaces, is a code block; shallower indentation is stripped.
    if "\t" in indent or len(indent) >= 4:
        indent = _reference(indent)
    if opener:
        opener = opener[:-1] + _reference(opener[-1])
    return indent + opener


def _as_text(text: str) -> str:
    """A string Markdown meets inside a line, written so it reads as text.

    A table cell, an attribute, a heading's remainder: nothing here begins a
    line, so only `INLINE_MARKDOWN` and HTML's characters are encoded.
    """
    return text.translate(_AS_TEXT)


def _as_text_lines(text: str) -> str:
    """A string that may begin a line of Markdown, written so it reads as text.

    Each of its lines is taken to begin one: where MarkItDown or markdownify
    joins strings, one that began mid-line in the document may not mid-line in
    the Markdown, and an opener encoded where it did not need to be renders the
    same.
    """
    return _LINE_START_RE.sub(_line_start, _as_text(text))


def _as_destination(url: str) -> str:
    return _DESTINATION_BREAK_RE.sub(lambda match: quote(match.group(), safe=""), url)


def _fence(text: str, *, minimum: int) -> str:
    """Backticks that no run of backticks inside `text` can close."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(minimum, longest + 1)


@dataclass(frozen=True, slots=True)
class ConvertedMarkdown:
    """What a local file looks like once MarkItDown has read it.

    `title` is the document's own — an EPUB's `dc:title`, say — which is not
    always recoverable from the Markdown body, so the adapter can fall back to
    it before falling back to the filename.
    """

    markdown: str
    title: str | None
    #: What the conversion could not carry into `markdown`, for the manifest.
    warnings: tuple[str, ...] = ()


class _WarnedResult(DocumentConverterResult):
    """A converter's Markdown, and what it could not carry into it.

    MarkItDown's result has no field for that, and returns the object a
    converter made as it is, so whatever a converter here says reaches
    `_convert_bytes` the same way a zip's does.
    """

    def __init__(
        self, markdown: str, warnings: tuple[str, ...] = (), *, title: str | None = None
    ) -> None:
        super().__init__(markdown, title=title)
        self.warnings = warnings


# --------------------------------------------------------------------------- #
# Pictures
# --------------------------------------------------------------------------- #

#: The picture formats Chromium draws in an `<img>`.  Anything else renders as
#: a broken frame around the alt text, which the capture then keeps as if it
#: were the picture (docs/09 P36).
DRAWABLE_PICTURES = frozenset(
    {
        "image/apng",
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/vnd.microsoft.icon",
        "image/webp",
        "image/x-icon",
    }
)
#: Formats Pillow reads that Chromium does not draw: written again as PNG.
CONVERTED_PICTURES = frozenset({"image/tiff"})
#: What a format nothing here draws is called, for a person reading why.
PICTURE_FORMAT_NAMES = {
    "image/emf": "an Enhanced Metafile (EMF)",
    "image/x-emf": "an Enhanced Metafile (EMF)",
    "image/wmf": "a Windows Metafile (WMF)",
    "image/x-wmf": "a Windows Metafile (WMF)",
    "image/vnd.ms-photo": "an HD Photo (JPEG XR)",
}


def _as_png(data: bytes) -> bytes | None:
    """A TIFF's first page as PNG, or None when Pillow cannot read it."""
    from PIL import Image  # only a TIFF needs it

    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                picture = picture.convert("RGBA" if "A" in picture.mode else "RGB")
            output = io.BytesIO()
            picture.save(output, "PNG")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return output.getvalue()


def _drawable(src: str) -> tuple[str | None, str]:
    """A `data:` picture as the page can draw it, or why it cannot be.

    The reason completes "a picture is not in the page: …".
    """
    header, _, payload = src.partition(",")
    media_type = header[5:].split(";")[0].strip().lower()
    if media_type in DRAWABLE_PICTURES:
        return src, ""
    if media_type in CONVERTED_PICTURES and header.lower().endswith(";base64"):
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return None, "its data could not be decoded"
        png = _as_png(data)
        if png is not None:
            return f"data:image/png;base64,{base64.b64encode(png).decode()}", ""
        return None, "a TIFF that Pillow could not read"
    name = PICTURE_FORMAT_NAMES.get(media_type, media_type or "an unnamed format")
    return None, f"{name}, which a browser cannot draw"


def _picture_warning(alt: str, reason: str) -> str:
    named = f" (\u201c{alt}\u201d)" if alt.strip() else ""
    return f"a picture{named} is not in the page: {reason}."


def _picture_placeholder(alt: str, reason: str) -> str:
    """What the page shows where a picture could not be drawn, as plain text."""
    named = f": {alt}" if alt.strip() else ""
    return f"[Picture{named} \u2014 not shown: {reason}]"


@dataclass(slots=True)
class _BookPictures:
    """An EPUB's archive, for the pictures its chapters name by path."""

    archive: zipfile.ZipFile
    warnings: list[str] = dataclasses.field(default_factory=list)

    def picture(self, src: str, chapter: str) -> tuple[str | None, str]:
        path = posixpath.normpath(
            posixpath.join(posixpath.dirname(chapter), unquote(urlsplit(src).path))
        )
        if path not in self.archive.NameToInfo:
            return None, f"it names {src}, which the book does not hold"
        media_type = mimetypes.guess_type(path)[0] or ""
        data = base64.b64encode(self.archive.read(path)).decode()
        return _drawable(f"data:{media_type};base64,{data}")


def _pictures_as_data(
    soup: BeautifulSoup, book: _BookPictures | None, chapter: str | None
) -> list[str]:
    """Every `<img>` as a picture the page draws from its own bytes, or text.

    A picture the document holds becomes a `data:` URL; one in a format the
    browser cannot draw, or one named by a path, becomes a line of text saying
    so, and a warning.  A path is never left for the browser to resolve: the
    page's base is the document's folder, so a book's `images/a.png` loaded
    whatever file of that name sat beside the book.  A remote picture is the
    document's own, fetched as an HTML file's would be.
    """
    warnings: list[str] = []
    for image in soup.find_all("img"):
        src = image.get("src")
        if not isinstance(src, str) or not src.strip():
            continue
        scheme = urlsplit(src.strip()).scheme.lower()
        if scheme in ("http", "https"):
            continue
        if scheme == "data":
            drawn, reason = _drawable(src.strip())
        elif book is not None and chapter is not None and not scheme:
            drawn, reason = book.picture(src.strip(), chapter)
        else:
            drawn, reason = None, f"it names {src}, a path the page cannot reach"
        if drawn is not None:
            image["src"] = drawn
            continue
        alt = image.get("alt")
        alt = alt if isinstance(alt, str) else ""
        warnings.append(_picture_warning(alt, reason))
        image.replace_with(_picture_placeholder(alt, reason))
    return warnings


def _equation_latex(equation: Element) -> str:
    """One `m:oMath` as LaTeX, by the converter MarkItDown applies to Word.

    The same converter for decks as for documents, so an equation reads the
    same whichever format it arrived in.  It is MarkItDown's internal module,
    imported at the top of this file so that a release which moves it fails
    the import, not every equation.
    """
    latex: str = oMath2Latex(equation).latex
    return latex


def _escape_cell(cell: str) -> str:
    """One tabular value, rendered so it cannot become anything but a value.

    Two grammars stand between a cell and the page:

    * **HTML and Markdown**, through `_as_text`.  Markdown passes raw HTML
      straight through, so a cell reading `<img src="https://tracker/x">`
      became a real element, and rendering the document fetched that URL — a
      beacon fired by opening a local spreadsheet.  `img[src]` is in
      `MARKDOWN_POLICY`, so sanitization does not stop it and was never meant
      to: the allowlist decides what *Markdown* may emit, and this text is not
      Markdown (docs/09 P8-9).  The first fix escaped the HTML and put a
      backslash before a leading Markdown opener, which Python-Markdown read
      three ways wrong: no backslash escapes `=` or `~` there, so `=SUM(A1)`
      printed as `\\=SUM(A1)`; a trailing `{x}` was taken for attributes and
      dropped; and a backslash the cell held escaped what followed it, so
      `C:\\Users\\_x_` lost both of its own (docs/09 P28).
    * **The table row.**  A newline would end the row, so it becomes `<br>`.
      `|` would end the cell; by now it is a character reference, so MarkItDown,
      which from 0.1.8 escapes a pipe and the backslashes before it, finds
      neither to escape (docs/09 P10-23).

    The v2 CSV renderer HTML-escaped every cell; that property was lost in the
    move to MarkItDown and is restored here.
    """
    return _as_text(cell.replace("\r\n", "\n")).replace("\n", "<br>")


def _as_escaped_csv(
    data: bytes, delimiter: str, path_name: str
) -> tuple[bytes, list[str]]:
    """Re-delimit a separated-values file and escape its cells, in one pass.

    Both halves of this exist because MarkItDown's CSV converter is comma-only
    *and* flattens a line break in a cell to a space;
    doing them together means TSV is not parsed and re-serialized twice, and
    that a fix to quoting or encoding cannot reach one path and miss the other.
    The bytes returned are UTF-8, whatever the file's were, and the warnings
    say when their encoding was a guess: read as UTF-8 whatever it was, a
    Windows-1252 export from Excel lost every accented letter (docs/09 P38).
    """
    decoded = decode_text(data)
    conversion_warnings = [] if decoded.warning is None else [decoded.warning]
    rows = csv.reader(io.StringIO(decoded.text), delimiter=delimiter)
    buffer = io.StringIO()
    try:
        csv.writer(buffer, lineterminator="\n").writerows(
            [_escape_cell(cell) for cell in row] for row in rows
        )
    except csv.Error as exc:
        # `csv.Error` is not a ValueError, so nothing upstream catches it: a
        # cell past the 128 KB field limit would end the run in a traceback
        # rather than a message and an exit code.
        raise UsageError(f"Could not read the rows of {path_name}: {exc}") from exc
    return buffer.getvalue().encode("utf-8"), conversion_warnings


#: What a UTF-8 stream's first bytes are when it begins with a byte order mark.
_UTF8_BOM = b"\xef\xbb\xbf"


def _declared_charset(data: bytes) -> str | None:
    """The encoding MarkItDown is told a stream WebShot did not write is in.

    MarkItDown guesses a text stream's encoding from its first 64 KB
    (`_read_charset_sample` in 0.1.8), and decodes all of it by the guess.  A
    UTF-8 file whose first character outside ASCII came later was guessed to
    be ASCII and failed, or, where Magika could not name its content, found
    no reader at all; and a guess inside the sample can be wrong too, as when
    pip's `rich/box.py`, UTF-8 box drawing, was read as Kazakh `ptcp154`
    (docs/09 P35).  So text that decodes as UTF-8 to its last byte is
    declared UTF-8: a legacy encoding all but never forms valid UTF-8
    sequences, and a NUL, which text does not hold, rules out a binary file
    that happens to.  A byte order mark makes it `utf-8-sig`, which drops the
    mark: it says how the text is encoded and is no part of it.  MarkItDown
    dropped it only where Magika could not name the file, and otherwise put
    it at the start of the page as U+FEFF (docs/09 P35-2).

    Nothing is declared for ASCII, which MarkItDown's guess (`ascii`, or
    `utf-8`) decodes as itself, or for text that is not UTF-8, whose encoding
    is not known: WebShot's plain-text and HTML readers decode that by
    `text_encoding`, whatever MarkItDown guessed (docs/09 P38).
    """
    if data.isascii() or b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "utf-8-sig" if data.startswith(_UTF8_BOM) else "utf-8"


# --------------------------------------------------------------------------- #
# Text that MarkItDown writes through markdownify
# --------------------------------------------------------------------------- #

#: How deep Python-Markdown indents what belongs to a list item: its
#: `tab_length`.  A nested list indented less is read as more of the item's
#: text, or as items of the item's own list (docs/09 P40).
ITEM_INDENT = " " * 4
#: What keeps two lists apart in Python-Markdown, which reads two lists of one
#: kind with only blank lines between them as one list (docs/09 P32), and two
#: quotations or two definition lists likewise (docs/09 P63).  A comment is a
#: block of its own to Markdown, and nh3 drops it from the page.
LIST_SEPARATOR = "<!-- -->"
#: The text of an item that opens with a list, which goes on the next line: a
#: list begun on the marker's line had its items' own lists read as text, and
#: MarkItDown strips the space a bare marker needs to be one.  A comment, so
#: the page shows nothing there (docs/09 P40).
EMPTY_ITEM = "<!-- -->"
#: How far a code block in a list item, a quotation or a definition is
#: indented past their text.  Python-Markdown's `fenced_code` reads a fence
#: only at the start of a line, so a fence behind an item's indentation, a
#: quotation's `> ` or a definition's `:   ` printed its code as one line of
#: text; an indented code block is read in each (docs/09 P44, P47).
CODE_INDENT = " " * 4
#: The `_ItemPart.kind` of a code block in an item.
CODE = "pre"
#: What a code block is written as a block in, because each begins its lines
#: with something of its own: a list item (docs/09 P44), a quotation and a
#: definition (docs/09 P47).  Each takes its code blocks' marks away.
CODE_CONTAINERS = frozenset({"blockquote", "dd", "li"})
#: What may stand between a code block and its container: markdownify writes
#: what these hold at the start of a line, where an indented code block is
#: read.  Anything else that markdownify converts writes it into a line of
#: text — a link, emphasis, a term — and there the code is markdownify's, as
#: it was, but for code written into a line (`BLOCKS`, docs/09 P59).  A tag
#: markdownify does not convert passes its Markdown through, and may stand
#: there too.
CODE_HOLDERS = frozenset({"article", "div", "figcaption", "p", "section"})
#: A line Python-Markdown reads as an item nested in the one it is in, though
#: it is in the item's first block (its list's `INDENT_RE`): the second item
#: of a list that opens a definition that opens an item (docs/09 P62).
_NESTED_ITEM_LINE_RE = re.compile(r"[ ]{4,7}(?:\d+\.|[*+-])[ ]")
#: Elements markdownify writes on lines of their own, whatever stands beside
#: them.  Any other element it converts writes what it holds into a line of
#: text, a link's or emphasis's, and strips the blank lines a `<pre>`'s fence
#: stands between: the fence began mid-line, where Python-Markdown does not
#: read one, and the code was Markdown (docs/09 P59).  An element this does
#: not name is taken to write into a line, which is safe wherever it stands.
#: Under a heading or in a table cell (`_inline`), and in code (`_noformat`),
#: markdownify writes everything on one line, so neither needs naming here.
BLOCKS = frozenset(
    {
        "[document]",
        "article",
        "blockquote",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "li",
        "ol",
        "p",
        "section",
        "table",
        "tr",
        "ul",
    }
)
#: A line break as Python-Markdown reads one, which ends a line of code.
_LINE_BREAK_RE = re.compile(r"\r\n?|\n")
#: A line break in code written into a line of text: a line feed by
#: reference, which Markdown does not read and the page shows, its `<code>`
#: being `CODE_LINES_CLASS`.  A `<br>` showed it too, but the capture's text
#: surfaces drop one, and docling wrote one in a list item as its own private
#: mark (docs/09 P59).
CODE_LINE_BREAK = "&#10;"


class _ListSeams:
    """Where markdownify ends one list and the next begins, kept apart.

    markdownify writes a blank line after a list, and Python-Markdown reads a
    list of the same kind after a blank line as more of the first: two EPUB
    `<ol>`s became one, numbered on from the first, the items either side of
    the blank line made paragraphs.
    Whether anything stands between two lists is only known once the Markdown
    is written — an empty `<div>`, `<p>` or `<br>` writes nothing, and a list
    closing one `<div>` meets one opening the next — so each list markdownify
    writes at the top of a block is marked where it begins and ends, and the
    marks are resolved once the document is whole: `LIST_SEPARATOR` where one
    list's end meets the next one's start, nothing anywhere else.  A list
    nested in an item, a table cell, a heading or a `<pre>` is not marked; one
    in a quotation in an item is (docs/09 P74).

    Quotations and definition lists are marked too, because Python-Markdown
    makes a quotation that follows a quotation part of it, and adds a term
    that follows a definition list to it (docs/09 P63).  Their seams are
    resolved in a pass of their own, before the lists', and their marks taken
    away then, so that the lists' pass reads what it read before they were
    marked: a document with no two of them side by side is written as before.
    As with a list nested in an item, one in an item is not marked.

    A mark holds a token drawn for this instance, so no document's text, a
    `<pre>`'s included, can hold one.  `resolve` must see every string these
    marks went into before any of them is used.
    """

    def __init__(self) -> None:
        # Private-use characters around the token, so that a mark cannot run
        # into the text either side of it.
        token = f"\ue000{secrets.token_hex(8)}"
        self._begin, self._end = f"{token}begin", f"{token}end"

        def seams(kinds: str) -> re.Pattern[str]:
            # What markdownify writes between the two: blank lines, which in a
            # blockquote are `>`, and on the second one's line the indentation
            # that keeps it in its blockquote (`> `) or definition (four
            # spaces).  A blockquote in an item's first block is fewer than
            # four spaces in, at the width of the item's marker (docs/09 P40,
            # P74).
            return re.compile(
                f"{self._end}(?P<kind>{kinds})\ue001"
                r"(?P<gap>[ \t>]*\n(?:[ \t>]*\n)*)(?P<indent>(?: {0,3}> ?| {4})*)"
                f"{self._begin}(?P=kind)\ue001"
            )

        def marks(kinds: str) -> re.Pattern[str]:
            return re.compile(f"{token}(?:begin|end)(?:{kinds})\ue001")

        self._passes = [
            (seams(kinds), marks(kinds)) for kinds in ("blockquote|dl", "ol|ul")
        ]

    def mark(self, kind: str, block: str) -> str:
        """`block`, a list, quotation or definition list markdownify wrote,
        marked inside its blank lines, as `kind`: its tag.

        Inside, because markdownify collapses the blank lines between two
        blocks by counting the newlines each one starts and ends with.  A list
        with no items writes nothing, and is not marked: the lists either side
        of it meet.
        """
        body = block.strip("\n")
        if not body.strip():
            return block
        leading = block[: len(block) - len(block.lstrip("\n"))]
        trailing = block[len(block.rstrip("\n")) :]
        return (
            f"{leading}{self._begin}{kind}\ue001{body}{self._end}{kind}\ue001{trailing}"
        )

    def resolve(self, markdown: str) -> str:
        """`markdown` with its marks gone, and a separator where two meet."""

        def separate(seam: re.Match[str]) -> str:
            gap, indent = seam["gap"], seam["indent"]
            # The separator goes on a line of its own at the second one's
            # indentation.  Where a blank line between them has other
            # indentation the two are in different blocks, which Markdown
            # keeps apart already.
            blank = indent.rstrip()
            if any(line.rstrip() != blank for line in gap.split("\n")[1:-1]):
                return seam[0]
            # A definition list that opens with a definition, `:   `, would
            # take the separator's paragraph for its term: an empty `<dt>`.
            if seam["kind"] == "dl" and seam.string.startswith(":", seam.end()):
                return seam[0]
            return f"{gap}{indent}{LIST_SEPARATOR}\n{blank}\n{indent}"

        for seams, marks in self._passes:
            markdown = marks.sub("", seams.sub(separate, markdown))
        return markdown


@dataclass(slots=True)
class _ItemPart:
    """Some of an item's Markdown: its own, a list nested in it, or its code."""

    #: `ol` or `ul` for a nested list, `CODE` for a code block; None for the
    #: item's own Markdown.
    kind: str | None
    #: The list's items, each as markdownify wrote it; the code block; or the
    #: item's own Markdown in the pieces it was found in.
    pieces: list[str]
    #: Which list the items are of, so that two lists side by side stay two;
    #: which `<pre>` the code is, so that two side by side stay two.
    list_id: int | None = None


class _NestedItems:
    """The items of lists nested in an item, found again in the item's Markdown.

    markdownify hands an item its children's Markdown as one string, and by
    then where a nested list ends, and whether another begins after it, is
    lost.  So each nested item is written between marks that name its list,
    and the item it sits in reads its lists back out and takes the marks away:
    none is left once the outermost item is written.  A code block in an item
    is marked the same way, so that what follows it, and what it follows, can
    be kept apart from it, and so that nothing between it and the item strips
    its first line's indentation.  A quotation or a definition takes its own
    code blocks' marks away (`code_blocks`), so an item finds only its own.  A
    mark that does not begin a line is in a table cell or a heading, and is
    only taken away.  A list in a definition or a quotation in an item is that
    block's, and its items are not marked (docs/09 P62, P74).

    A mark holds a token drawn for this instance, so no document's text, a
    `<pre>`'s included, can hold one.
    """

    def __init__(self) -> None:
        # Private-use characters around the token, so that a mark cannot run
        # into the text either side of it.
        token = f"\ue002{secrets.token_hex(8)}"
        self._begin, self._end = token, f"{token}end\ue003"
        self._items = re.compile(
            f"{token}(?P<kind>ol|ul|{CODE})(?P<list>[0-9]+)\ue003(?P<item>.*?){self._end}",
            re.DOTALL,
        )
        self._code = re.compile(
            f"{token}{CODE}[0-9]+\ue003(?P<code>.*?){self._end}", re.DOTALL
        )

    def mark(self, kind: str, list_id: int, item: str) -> str:
        return f"{self._begin}{kind}{list_id}\ue003{item}{self._end}"

    def code_blocks(self, text: str) -> str:
        """`text`, a quotation's or a definition's Markdown, its code unmarked.

        Each code block follows `LIST_SEPARATOR` and a blank line.  An indented
        block straight after a list, a definition list or another code block is
        more of it to Python-Markdown, which places such a block by its depth
        alone; a definition's text cannot be a code block on its `:` line; and
        with something before it, the container's strip of its own text leaves
        the code's first line's indentation alone (docs/09 P47).
        """
        return self._code.sub(
            lambda match: f"{LIST_SEPARATOR}\n\n{match['code']}", text
        )

    def parts(self, text: str) -> list[_ItemPart]:
        """`text`, an item's Markdown, as its own and the lists nested in it."""
        parts: list[_ItemPart] = []

        def own(markdown: str) -> None:
            if parts and parts[-1].kind is None:
                parts[-1].pieces.append(markdown)
            else:
                parts.append(_ItemPart(None, [markdown]))

        position = 0
        for match in self._items.finditer(text):
            before, position = text[position : match.start()], match.end()
            list_id = int(match["list"])
            if match.start() and text[match.start() - 1] != "\n":
                own(before + match["item"])
            elif parts and parts[-1].list_id == list_id and not before.strip():
                parts[-1].pieces.append(match["item"])
            else:
                own(before)
                parts.append(_ItemPart(match["kind"], [match["item"]], list_id))
        own(text[position:])
        return parts


def _code_of(block: str) -> str:
    """The code in `block`, a `<pre>` as markdownify fences it.

    As its `strip_pre` option trims it, without the fence or a language.
    """
    opening = block.index("\n", block.index("```"))
    return block[opening + 1 : block.rindex("```") - 1]


def _with_its_gap(item: str) -> str:
    """A list item and the break after it.

    A blank line after an item of more than one block, or Python-Markdown
    reads the next item as more of its last block.
    """
    return item + ("\n\n" if "\n\n" in item else "\n")


def _item_lines(parts: list[_ItemPart]) -> tuple[list[str], int]:
    """An item's Markdown as lines to indent, and how many are its first block.

    The blocks keep the blank lines markdownify wrote between them, but for
    two places Python-Markdown needs one.  It reads a line straight after a
    nested list as more of the list's last item, so a blank line goes there,
    and `LIST_SEPARATOR` too where the next block is a list of the same kind.
    And it nests a list with no blank line before it only straight after the
    item's first block: after any later one, the list is that block's text.
    The first block is the item's own text up to a blank line or a list; it is
    nothing where the item opens with a list or a code block.

    A code block has a blank line either side, or it is read as text.  It
    follows `LIST_SEPARATOR` where it follows a nested list or another code
    block: Python-Markdown places an indented block by its depth alone, and a
    code block's depth is a nested item's text's, so after a list it became
    the list's last item's paragraph; after a code block it was more of it.
    """
    # Each block's kind, its Markdown, and whether a blank line follows it.
    blocks: list[tuple[str | None, str, bool]] = []
    for part in parts:
        if part.kind is None:
            markdown = "".join(part.pieces)
            body, blank_after = (
                markdown.strip(),
                markdown.rstrip(" \t").endswith("\n\n"),
            )
        elif part.kind == CODE:
            # Indented already, and the first line's indentation is the code's.
            body, blank_after = "".join(part.pieces), True
        else:
            body = "".join(map(_with_its_gap, part.pieces)).rstrip("\n")
            blank_after = True
        if body:
            blocks.append((part.kind, body, blank_after))
    lines: list[str] = []
    first_block = 0
    for index, (kind, body, _) in enumerate(blocks):
        if index:
            previous_kind, _, blank = blocks[index - 1]
            if blank or kind == CODE or not (index == 1 and first_block == len(lines)):
                lines.append("")
            if previous_kind is not None and kind in (previous_kind, CODE):
                lines += [LIST_SEPARATOR, ""]
        elif kind == CODE:
            # After `EMPTY_ITEM`, which `convert_li` writes first.
            lines.append("")
        own = [line if line.strip() else "" for line in body.split("\n")]
        if index == 0 and kind is None:
            first_block = own.index("") if "" in own else len(own)
        lines += own
    return lines, first_block


def _in_a_definition_or_quotation(el: Any, parent_tags: set[str]) -> bool:
    """Whether `el` is in a `<dd>` or `<blockquote>` nearer to it than any `<li>`.

    markdownify asks only whether an item is anywhere above a list, and writes
    one that is as the item's own: on the line after the item's text, with no
    blank line after it.  A list in a definition or a quotation is that
    block's, wherever the block is: written as the item's, it was read as the
    definition's text (docs/09 P62), and in a quotation the quotation's next
    line ran into its last item, so the text after it read `xtail` and a list
    after it was that item's text or more of its items (docs/09 P74).

    Not in a table cell or heading, nor in a `<pre>`, where a list is text
    and is written as before: there, a list taken for the block's left its
    items unmarked, and the cell ran the list's last word into the word after
    it (docs/09 P74).
    """
    if {"_inline", "_noformat"} & parent_tags:
        return False
    for ancestor in el.parents:
        if ancestor.name in ("dd", "blockquote", "li"):
            return bool(ancestor.name != "li")
    return False


def _code_on_one_line(text: str, parent_tags: Iterable[str]) -> str:
    """Inline code's text, written on one line.

    A code span's text is not encoded, Markdown keeping it literal, but
    Python-Markdown reads a heading, a quotation, a rule or raw HTML wherever a
    line of a paragraph begins with one, and a blank line ends the paragraph:
    `<code>` whose text went on to a line `# h` and then to
    `<img src="https://…">` put the image on the page (docs/09 P59).  The
    browser shows a line break in inline code as a space, so it is written as
    one.  Inside `pre`, `code`, `kbd` or `samp` it is markdownify's, part of
    the code around it.
    """
    if "_noformat" in parent_tags:
        return text
    return _LINE_BREAK_RE.sub(" ", text)


#: The ASCII characters `str.strip()` takes as whitespace, which `chomp`
#: strips: space, tab, the line breaks, and the four separators.  Derived from
#: Python rather than listed, so the set is the one `chomp` actually uses.
_ASCII_WHITESPACE_RUN = re.compile(
    "[" + re.escape("".join(c for c in map(chr, range(128)) if c.isspace())) + "]+"
)

#: What `_TextMarkdownify` writes for a `<br>`: HTML's line break, which
#: Markdown passes through and nh3 keeps (docs/09 P77).
LINE_BREAK = "<br>"
#: Where markdownify adds `_noformat` for the children: their text is written
#: as it stands, so a `<br>` in it is the document's text, not this writer's.
_NOFORMAT_TAGS = frozenset({"pre", "code", "kbd", "samp"})
#: What markdownify writes as a heading, `h` and any number.
_HEADING_TAG_RE = re.compile(r"h\d+")
#: Elements whose children are blocks, not a line of text: a `<br>` directly in
#: one is written as markdownify writes it, because the space after
#: `LINE_BREAK` would join the next item, row or definition to its line.
_BLOCK_CONTAINERS = frozenset(
    {"ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr"}
)
#: What may stand around a run of `<br>`s that markdownify's blank line
#: divides into two paragraphs and does nothing worse to: paragraphs and the
#: blocks that hold them, not an item, a definition, a cell or formatted text.
_PARAGRAPH_FLOW = frozenset(
    {
        "[document]",
        "html",
        "body",
        "main",
        "article",
        "section",
        "div",
        "blockquote",
        "p",
    }
)


def _beside_a_break(el: Any) -> bool:
    """Whether another `<br>` stands next to `el`, with only whitespace between."""
    for step in ("previous_sibling", "next_sibling"):
        sibling = getattr(el, step)
        while isinstance(sibling, NavigableString) and not sibling.strip():
            sibling = getattr(sibling, step)
        if isinstance(sibling, Tag) and sibling.name == "br":
            return True
    return False


def _edges(text: str, *, breaks: bool) -> tuple[str, str, str]:
    """`text` as its leading edge, what is between, and its trailing edge.

    An edge is whitespace and, where `breaks`, the line breaks `convert_br`
    wrote there.  Text that is all edge is returned as the leading edge.
    """
    rest = text.lstrip()
    while breaks and rest.startswith(LINE_BREAK):
        rest = rest[len(LINE_BREAK) :].lstrip()
    lead = len(text) - len(rest)
    rest = rest.rstrip()
    while breaks and rest.endswith(LINE_BREAK):
        rest = rest[: -len(LINE_BREAK)].rstrip()
    return text[:lead], rest, text[lead + len(rest) :]


def _outside_markup(edge: str, *, literal: bool) -> str:
    """The whitespace at one edge of an inline element, as written outside it.

    Each run of ASCII whitespace is one space, which is what `chomp` writes
    for an edge of spaces, so output that was right does not move; a newline
    is one too, so an edge never begins a line, which beside the newline of
    the next text could make a blank one and end the paragraph or list item
    (docs/09 P49).  A `<br>` stays the line break `convert_br` wrote, which
    holds no newline (docs/09 P77).  A space outside ASCII is written as the
    document has it (docs/09 P43).  Inside `pre`, `code`, `kbd` and `samp`
    (`literal`) markdownify writes text as it stands, so every edge is written
    so too.
    """
    if literal:
        return edge
    return _ASCII_WHITESPACE_RUN.sub(" ", edge)


def _keeping_edge_spaces(
    convert: Callable[..., str], *, one_line: bool = False
) -> Callable[..., str]:
    """`convert`, with the whitespace at the edges of its text kept.

    markdownify's `chomp` takes an inline element's edges with `str.strip()`,
    and puts back one space where the text began or ended with a plain space,
    and nothing otherwise.  So a no-break space, a newline or a `<br>` at the
    edge of bold text was lost, and an element holding whitespace alone wrote
    nothing at all: the words either side of it ran together on the page
    (docs/09 P43, P49).  MarkItDown's `<a>` dropped even a plain space where it
    returns before its markup: with no target, inside `pre`, and, while the
    writer still wrote `<address>` (docs/09 P75), where the text is the address.

    So `convert` is never shown an edge.  It converts the text between them,
    and each edge is written outside the markup (`_outside_markup`); whitespace
    alone is written as one edge.  The page shows each as it shows the same
    whitespace between two words, and content.md and content.txt read it as a
    space, as they already read one between two words.  A line break at an
    edge, or alone, is an edge as whitespace is (docs/09 P77): formatting that
    holds nothing else writes no markup, where `<del><br/></del>` had printed
    `~~` against the words either side, and a link holding only a `<br>` was
    one with nothing to click.

    `one_line` is for inline code, whose text is put on one line first
    (`_code_on_one_line`), so that its edges are the ones of the line it is
    written as.  It is done here, not in a code converter of the writer's
    own, because such a converter takes this one's place in the class, and
    the edges were lost when one did.
    """

    @functools.wraps(convert)
    def implementation(self: Any, el: Any, text: str, *args: Any, **kwargs: Any) -> str:
        parent_tags = kwargs.get("parent_tags", args[0] if args else ())
        literal = "_noformat" in parent_tags
        if one_line:
            text = _code_on_one_line(text, parent_tags)
        lead, inner, trail = _edges(
            text, breaks=not literal and el.name not in _NOFORMAT_TAGS
        )
        if not inner:
            return _outside_markup(text, literal=literal)
        converted: str = convert(self, el, inner, *args, **kwargs)
        return (
            _outside_markup(lead, literal=literal)
            + converted
            + _outside_markup(trail, literal=literal)
        )

    return implementation


class _DefinitionSeams:
    """Where a definition list's terms and definitions meet, as Python-Markdown
    reads them.

    Python-Markdown takes as a definition's terms only the lines straight
    above its `:` line, and reads a `:` line straight after an indented one as
    part of that indented block.  markdownify writes a blank line before every
    term, so of two terms in a row only the second was a term; and it writes
    a definition's `:` line straight after the one before it, so after a
    definition of more than one block the next was read into that block's
    paragraph (docs/09 P64).  Whether a definition follows is only known once
    the list is written — a `<dd>` can write nothing — so each term, and the
    end of each definition of more than one block, is marked as it is
    written, and `resolve` settles them once the list is whole: terms that
    follow one another in the document and in the Markdown, and that a
    definition follows, are put on adjacent lines; a blank line goes between
    a definition of more than one block and the next; and every other mark is
    taken away.

    A mark holds a token drawn for this instance, so no document's text, a
    `<pre>`'s included, can hold one.
    """

    def __init__(self) -> None:
        # Private-use characters around the token, so that a mark cannot run
        # into the text either side of it.
        token = f"\ue004{secrets.token_hex(8)}"
        #: Before a term that does not follow a term, and before one that does.
        self.term = f"{token}term\ue005"
        self.next_term = f"{token}next\ue005"
        #: After a definition of more than one block.
        self.blocks = f"{token}blocks\ue005"
        term, next_term = re.escape(self.term), re.escape(self.next_term)
        # A term is one line, and only a definition begins one with `:`.  A
        # term that writes nothing writes no mark, so the lines either side of
        # it meet: a run is only lines that are marked terms.
        self._term_runs = re.compile(
            f"(?:{term}|{next_term})[^\n]*(?:\n+{next_term}[^\n]*)+(?=\n:)"
        )
        self._term_breaks = re.compile(f"\n+{next_term}")
        self._blocks_then_definition = re.compile(f"{re.escape(self.blocks)}(?=\n:)")

    def resolve(self, markdown: str) -> str:
        """`markdown`, one definition list's, with its marks settled."""
        markdown = self._term_runs.sub(
            lambda run: self._term_breaks.sub("\n", run[0]), markdown
        )
        markdown = self._blocks_then_definition.sub("\n", markdown)
        for mark in (self.term, self.next_term, self.blocks):
            markdown = markdown.replace(mark, "")
        return markdown


def _in_a_definition_list(el: Any, parent_tags: Iterable[str]) -> bool:
    """Whether `el`, a term or a definition, is written as one of a list's.

    Its list's `convert_dl` settles its marks, so it must be the list's own:
    a child of the `<dl>`, or of a `<div>` grouping a term with its
    definitions, as HTML allows.  In a heading or a table cell markdownify
    writes it on the cell's line, and in a `<pre>` it is code.
    """
    if {"_inline", "_noformat"} & set(parent_tags):
        return False
    parent = el.parent
    if parent is not None and parent.name == "div":
        parent = parent.parent
    return parent is not None and parent.name == "dl"


def _follows_a_term(el: Any) -> bool:
    """Whether the sibling before `el` is a term, as markdownify sees siblings:
    blank text and comments, which it writes nothing for, are passed over.

    A term after a definition, after text, or opening a `<div>` group starts
    a run of its own, as the document has it.
    """
    sibling = el.previous_sibling
    while sibling is not None:
        if isinstance(sibling, Tag):
            return bool(sibling.name == "dt")
        if not isinstance(sibling, Comment | Doctype) and sibling.strip():
            return False
        sibling = sibling.previous_sibling
    return False


def _in_code_as_text(convert: Callable[..., str]) -> Callable[..., str]:
    """`convert`, a link's converter, writing a link in code as its text.

    Markdown has no link inside a code span, so markdownify writes a link in
    `pre`, `code`, `kbd` or `samp` as its text.  MarkItDown's `<a>` replaced
    markdownify's and kept that for `pre` alone, so a link in code showed its
    Markdown, `[text](target)`, on the page (docs/09 P75).
    """

    @functools.wraps(convert)
    def implementation(self: Any, el: Any, text: str, *args: Any, **kwargs: Any) -> str:
        parent_tags = kwargs.get("parent_tags", args[0] if args else ())
        if "_noformat" in parent_tags:
            return text
        converted: str = convert(self, el, text, *args, **kwargs)
        return converted

    return implementation


class _TextMarkdownify(_CustomMarkdownify):
    """MarkItDown's Markdown writer, writing a document's text as text.

    markdownify escapes `*` and `_` and, optionally, a list aimed at CommonMark
    that Python-Markdown does not honour: it reads `\\<img>` as a backslash and
    a tag.  So `escape` is replaced rather than added to — escaping on top of
    markdownify's would print its backslashes.  It is not called inside `pre`,
    `code`, `kbd` or `samp`, whose text Markdown keeps literal.

    Its lists, quotations and definition lists are marked with `list_seams`,
    which whoever reads the Markdown resolves.

    Every converter that `chomp`s its text keeps the whitespace at its edges
    (`_keeping_edge_spaces`); `strike` is `s`'s, through `self`.  Inline code's
    converters write it on one line there too (`_code_on_one_line`).

    Every link is written `[text](target)`, and in code as its text
    (`_in_code_as_text`).  markdownify's `autolinks` writes one whose text is
    its target as `<target>`, which Python-Markdown links only for http, https
    and ftp and reads otherwise as an HTML tag: nh3 drops `<report.pdf>`,
    makes the rest of a paragraph italic after `<em>`, and the rest of the
    document disappears after `<script>`.  An http or https link renders the
    same page either way (docs/09 P75).
    """

    convert_a = _keeping_edge_spaces(_in_code_as_text(_CustomMarkdownify.convert_a))
    convert_b = _keeping_edge_spaces(_CustomMarkdownify.convert_b)
    convert_code = _keeping_edge_spaces(_CustomMarkdownify.convert_code, one_line=True)
    convert_del = _keeping_edge_spaces(_CustomMarkdownify.convert_del)
    convert_em = _keeping_edge_spaces(_CustomMarkdownify.convert_em)
    convert_i = _keeping_edge_spaces(_CustomMarkdownify.convert_i)
    convert_kbd = _keeping_edge_spaces(_CustomMarkdownify.convert_kbd, one_line=True)
    convert_s = _keeping_edge_spaces(_CustomMarkdownify.convert_s)
    convert_samp = _keeping_edge_spaces(_CustomMarkdownify.convert_samp, one_line=True)
    convert_strong = _keeping_edge_spaces(_CustomMarkdownify.convert_strong)
    convert_sub = _keeping_edge_spaces(_CustomMarkdownify.convert_sub)
    convert_sup = _keeping_edge_spaces(_CustomMarkdownify.convert_sup)
    convert_u = _keeping_edge_spaces(_CustomMarkdownify.convert_u)

    def __init__(self, *, list_seams: _ListSeams, **options: Any) -> None:
        # Overridden, not defaulted: an option a converter passes through
        # must not bring `<target>` back.
        super().__init__(**{**options, "autolinks": False})
        self._list_seams = list_seams

    def escape(self, text: str, parent_tags: Iterable[str]) -> str:
        # A text node can open a block, and keeps any newline its HTML had;
        # not in a heading or a table cell, which markdownify writes as one
        # line after its own marker (`_inline`).  Every negative number in a
        # sheet was a reference otherwise, and a large workbook 10% slower.
        if "_inline" in parent_tags:
            return _as_text(text)
        return _as_text_lines(text)

    @functools.cached_property
    def _nested_items(self) -> _NestedItems:
        return _NestedItems()

    def convert_li(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """An item, with what belongs to it indented as Python-Markdown reads it.

        markdownify indents an item's other lines by its marker's width, and
        Python-Markdown takes a nested list only at `ITEM_INDENT`: under `* `
        a sub-list became items of the parent's list, under `1. ` text in the
        parent item, and an item's second paragraph left the list (docs/09
        P40).  Every line after the first is indented by `ITEM_INDENT` here,
        but for the rest of the item's first block, kept shallower: a `>` four
        spaces in is not a blockquote there.  An item that opens with a list
        has `EMPTY_ITEM` for its text, and so has one whose first block holds
        a line Python-Markdown would read as an item nested in it; there the
        block follows after a blank line, at `ITEM_INDENT` (docs/09 P62).
        Inside `pre`, `code`, `kbd` or `samp`, whose text Markdown keeps
        literal, it is markdownify's.
        """
        tags = set(parent_tags)
        if "_noformat" in tags:
            literal: str = super().convert_li(el, text, parent_tags)
            return literal
        lines, first_block = _item_lines(self._nested_items.parts(text or ""))
        if not lines:
            return "\n"
        # markdownify's marker for this item: `*`, `+` or `-` by its depth,
        # or its number in an ordered list.
        marker = super().convert_li(el, "-", parent_tags).removesuffix("-\n")
        shallow = " " * min(len(marker), len(ITEM_INDENT) - 1)
        if not first_block:
            lines = [EMPTY_ITEM, *lines]
        elif any(
            _NESTED_ITEM_LINE_RE.match(shallow + line) for line in lines[1:first_block]
        ):
            # The first block would not stay one, so it is the item's second.
            lines, first_block = [EMPTY_ITEM, "", *lines], 0
        item = "\n".join(
            [marker + lines[0]]
            + [
                line and (shallow if index < first_block else ITEM_INDENT) + line
                for index, line in enumerate(lines[1:], 1)
            ]
        )
        parent = el.parent
        if (
            "li" in tags
            and parent is not None
            and parent.name in ("ol", "ul")
            and not _in_a_definition_or_quotation(parent, tags)
        ):
            # Nested: the item this list sits in takes the marks away.
            item = self._nested_items.mark(parent.name, id(parent), item)
        return _with_its_gap(item)

    def convert_br(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A `<br>` as HTML's line break, followed by a space.

        markdownify writes Markdown's hard break, two spaces and a newline,
        and MarkItDown strips the end of every line before WebShot sees the
        Markdown, so Python-Markdown read a soft break and every `<br>` reached
        the page as a space (docs/09 P77).  `<br>` itself survives that, and
        Python-Markdown and nh3 pass it through, in a table cell too, where
        markdownify wrote a space because a Markdown row cannot hold a newline.

        A space follows it, not a newline.  A space cannot begin a line or
        make a blank one: markdownify's newline beside a document's own made
        one (docs/09 P49), and two `<br>`s wrote a blank line that ended a list
        item or split bold text in two.  The browser draws no space at the
        start of a line, and whatever reads the text without its `<br>`s, as
        `textContent` does, still finds the words apart.

        Where it is markdownify's still:

        - **In `pre` and code** (`_noformat`), which hold no HTML: a newline,
          which Markdown keeps in a code block and the capture's print CSS
          (`white-space: pre-wrap`) draws.  Inline code is then written on
          one line (`_code_on_one_line`, docs/09 P59), so there the newline
          is a space, as any line break in inline code is.
        - **In a heading**, a space.  Chromium names the PDF's bookmark for a
          heading from its text with the `<br>` and the hidden space both
          gone, so `one<br> two` was bookmarked `onetwo`.
        - **Directly in a list, a definition list or a table**, where the
          space would join the next item or row to its line.
        - **Two or more in a row in a paragraph or a quotation**: a
          paragraph break, which shows much as the empty line between them
          does.  docling reads a run of `<br>`s as a paragraph break too, but
          drops one that touches bold, italic text or a link, so a contact
          block of bold labels, each after two `<br>`s, ran into one
          paragraph in content.md.  In an item, a definition, a cell or
          formatted text, where the blank line did the damage above, a run is
          `<br>`s.
        """
        if (
            "_noformat" in parent_tags
            or any(_HEADING_TAG_RE.fullmatch(tag) for tag in parent_tags)
            or el.parent.name in _BLOCK_CONTAINERS
            or (_PARAGRAPH_FLOW.issuperset(parent_tags) and _beside_a_break(el))
        ):
            written: str = super().convert_br(el, text, parent_tags)
            return written
        return f"{LINE_BREAK} {text}"

    def convert_pre(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A code block, fenced so that none of its own lines can close it.

        markdownify always fences with three backticks, and Python-Markdown
        ends the block at the first line that is those three alone: a `<pre>`
        showing a fenced example closed early, and everything after it was
        read as Markdown, raw HTML included.  In what writes it into a line of
        text (`BLOCKS`) it is inline code (`_code_in_a_line`), however deep in
        it, a list item, a quotation or a definition between them or not: no
        block survives being written into a line whole.  Elsewhere in a list
        item, a quotation or a definition it is indented instead
        (`_code_block`), where nothing but `CODE_HOLDERS` stands between them
        (`_held_as_a_block`); in `code`, `kbd` or `samp` it is their text; in
        a table cell or a heading, which is one line, inside `pre`, and
        anywhere else, it is markdownify's.
        """
        block: str = super().convert_pre(el, text, parent_tags)
        tags = set(parent_tags)
        if block and "_noformat" in tags and el.find_parent("pre") is None:
            # A code span, which `convert_code` writes on one line.
            return f" {_code_of(block)} "
        if (
            block
            and not tags & {"_inline", "_noformat"}
            and self._written_into_a_line(el)
        ):
            return self._code_in_a_line(block)
        if (
            block
            and tags & CODE_CONTAINERS
            and not tags & {"_inline", "_noformat"}
            and self._held_as_a_block(el)
        ):
            return self._code_block(el, block)
        fence = _fence(text, minimum=3)
        if fence == "```":
            return block
        opening, closing = block.index("```"), block.rindex("```")
        return (
            block[:opening]
            + fence
            + block[opening + 3 : closing]
            + fence
            + block[closing + 3 :]
        )

    def _written_into_a_line(self, el: Any) -> bool:
        """Whether an element `el` is in writes it into a line of text."""
        return any(
            parent.name not in BLOCKS
            and self.get_conv_fn_cached(parent.name) is not None
            for parent in el.parents
        )

    @staticmethod
    def _code_in_a_line(block: str) -> str:
        """`block`, markdownify's fenced code, as code in a line of text.

        A link, emphasis, `<sub>` or `<q>` writes what it holds into a line,
        and there markdownify's fence began mid-line, where `fenced_code` does
        not read one.  The page showed the code as inline code while it was
        one paragraph; but its text is not encoded, a code block's being
        literal, so a blank line in it ended the paragraph, as a line opening
        with `#`, `>`, `***` or `<div>` split it, and what followed was
        Markdown: `<img src="https://…">` typed in the code was an image the
        capture fetched (docs/09 P59, the class of P28).  No block can begin
        in a line of text, so the code is `<code>`, its text encoded as every
        other string a document holds is, and all of it on the one line of
        Markdown.  Each line break is `CODE_LINE_BREAK`, which the page shows
        as a line break in `CODE_LINES_CLASS`.  Tabs are expanded
        first, so that a tab stop falls where it does in the same `<pre>` at
        the top level.
        """
        lines = _LINE_BREAK_RE.split(_code_of(block))
        code = CODE_LINE_BREAK.join(
            _as_text(line.expandtabs(len(ITEM_INDENT))) for line in lines
        )
        return f' <code class="{CODE_LINES_CLASS}">{code}</code> '

    # Inline code is written on one line (`_code_on_one_line`, docs/09 P59) by
    # `convert_code`, `convert_kbd` and `convert_samp` above, which keep its
    # edges too (docs/09 P43).  A method here would take their place.

    def _held_as_a_block(self, el: Any) -> bool:
        """Whether nothing between `el` and its container writes it into a line.

        Its container is the nearest of `CODE_CONTAINERS` around it.  A
        definition in a list item is none, unless a quotation stands between
        them: Python-Markdown's list indentation is read before a definition's,
        so a block indented in such a definition is the item's, and its code
        reached the page in the item, four spaces deeper (docs/09 P47-4).
        """
        parents = iter(el.parents)
        for parent in parents:
            if parent.name == "dd":
                return all(
                    outer.name != "li"
                    for outer in itertools.takewhile(
                        lambda outer: outer.name != "blockquote", parents
                    )
                )
            if parent.name in CODE_CONTAINERS:
                return True
            if (
                parent.name not in CODE_HOLDERS
                and self.get_conv_fn_cached(parent.name) is not None
            ):
                return False
        return False

    def _code_block(self, el: object, block: str) -> str:
        """`block`, markdownify's fenced code, as a container's code block.

        Python-Markdown's `fenced_code` reads a fence only where a line begins,
        and in an item the fence is indented, in a quotation behind `> ` and in
        a definition behind `:   ` or indented: the code was printed as one
        line of text in a paragraph (docs/09 P44, P47).  So the code is
        indented by `CODE_INDENT`, which its container adds its own
        indentation or `> ` to, and needs no fence, so a run of backticks in it
        is text.  The code is markdownify's, as its `strip_pre` option trims
        it; tabs are expanded here, as Python-Markdown expands them from the
        start of a line, so that how far in the code is written moves no tab
        stop.  Each `\\r` is a line break, as it is to Python-Markdown.  A
        `<pre>` of blank lines is nothing: an indented code block cannot be
        empty.
        """
        lines = _LINE_BREAK_RE.split(_code_of(block).expandtabs(len(CODE_INDENT)))
        if not any(line.strip() for line in lines):
            return ""
        indented = "\n".join(line and CODE_INDENT + line for line in lines)
        return f"\n\n{self._nested_items.mark(CODE, id(el), indented)}\n\n"

    def convert_blockquote(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A quotation, its code blocks unmarked (`_NestedItems.code_blocks`),
        and marked where it begins and ends (docs/09 P63).

        Not marked in an item, which markdownify indents by its marker's width
        and Python-Markdown reads at four (docs/09 P32-5): a seam found there
        is found by chance, and its separator can fall in what Python-Markdown
        takes for code, and print (docs/09 P63-4).  Nor in a table cell or
        heading, which markdownify writes into the cell's line, nor in a
        `<pre>`, which is code.  Its code blocks are unmarked wherever it is:
        a quotation in an item holds its own (docs/09 P47).  One method does
        both, as a second would take the first's place in the class.
        """
        block: str = super().convert_blockquote(
            el, self._nested_items.code_blocks(text or ""), parent_tags
        )
        if {"li", "_inline", "_noformat"} & set(parent_tags):
            return block
        return self._list_seams.mark("blockquote", block)

    def convert_dd(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A definition, its code blocks unmarked (`_NestedItems.code_blocks`),
        and marked at its end where it is more than one block
        (`_DefinitionSeams`, docs/09 P64).

        markdownify indents what it holds by four, after `:   `, and an
        indented code block is four further in, as in an item.  The code
        blocks are unmarked first: each follows `LIST_SEPARATOR` and a blank
        line, so a definition holding one is more than one block.  One method
        does both, as a second would take the first's place in the class.
        """
        definition: str = super().convert_dd(
            el, self._nested_items.code_blocks(text or ""), parent_tags
        )
        body = definition.strip("\n")
        if "\n\n" in body and _in_a_definition_list(el, parent_tags):
            return f"{body}{self._definition_seams.blocks}\n"
        return definition

    def convert_list(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A list, marked where it begins and ends unless it sits in another.

        One nested in an item is written without a blank line before it, and
        one in a table cell or heading on the cell's line, so neither is a
        list Markdown starts after a blank line; one in a `<pre>` is code.
        One in a definition or a quotation is a block of that one's, even
        where it is in an item: markdownify wrote it on the line after the
        definition's text, and Python-Markdown read it as that text (docs/09
        P62), and the line after it in a quotation ran into its last item
        (docs/09 P74).  So it is written as one in no item is.
        """
        tags = set(parent_tags)
        if _in_a_definition_or_quotation(el, tags):
            tags.discard("li")
        block: str = super().convert_list(el, text, tags)
        if {"li", "_inline", "_noformat"} & tags:
            return block
        return self._list_seams.mark(el.name, block)

    # markdownify binds these to its own `convert_list` when its class is made.
    convert_ol = convert_list
    convert_ul = convert_list

    @functools.cached_property
    def _definition_seams(self) -> _DefinitionSeams:
        return _DefinitionSeams()

    def convert_dt(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A term, marked where it begins (`_DefinitionSeams`)."""
        term: str = super().convert_dt(el, text, parent_tags)
        if term.strip() and _in_a_definition_list(el, parent_tags):
            seams = self._definition_seams
            mark = seams.next_term if _follows_a_term(el) else seams.term
            return f"\n\n{mark}{term.lstrip(chr(10))}"
        return term

    def convert_dl(self, el: Any, text: str, parent_tags: Iterable[str]) -> str:
        """A definition list, its terms' and definitions' marks settled
        (`_DefinitionSeams`, docs/09 P64), and marked as a quotation is
        (docs/09 P63).  One method does both, as a second would take the
        first's place in the class.
        """
        block: str = super().convert_dl(
            el, self._definition_seams.resolve(text), parent_tags
        )
        if {"li", "_inline", "_noformat"} & set(parent_tags):
            return block
        return self._list_seams.mark("dl", block)


def _attributes_as_text(soup: BeautifulSoup) -> None:
    """Make the attributes markdownify writes into Markdown safe to write.

    An image's alt text goes between `![` and `]` as it is, so alt reading
    `x](https://tracker/a.png) ![y` closed the image and opened another; a
    link's target goes between `(` and `)`, so one holding `)![y](https://…`
    did the same.  Either way the document chose a URL the page then fetched.
    """
    for tag in soup.find_all(True):
        for name in URL_ATTRIBUTES:
            value = tag.get(name)
            if isinstance(value, str):
                tag[name] = _as_destination(value)
        for name in TEXT_ATTRIBUTES:
            value = tag.get(name)
            if isinstance(value, str):
                tag[name] = _as_text(value).replace('"', "&#34;")


#: The `id` mammoth gives each item of the lists it writes for a document's
#: notes, which it numbers itself, in order, from 1.
NOTE_ITEM_RE = re.compile(r"(?:foot|end)note-")


def _number_word_lists(soup: BeautifulSoup, numbers: ooxml.WordListNumbers) -> None:
    """Number mammoth's lists as Word does, and take the marks out.

    `ooxml.number_word_lists` put a mark at the head of each paragraph Word
    numbers.  An item's own mark is the first one inside it and outside any
    list nested in it.  Each `<ol>` then starts at its first item's number, and
    is split where its numbers stop running on: mammoth puts paragraphs of two
    lists that meet into one `<ol>`, and Word counts a list on past the
    paragraphs between its items, which mammoth starts afresh.

    An `<ol>` with an item that holds no mark, or one Word shows no number on,
    is left as mammoth wrote it, and counted for the manifest; so is a mark
    that no list item owns, a paragraph Word numbers that the page does not.
    A mark that was all its paragraph held stands in for one mammoth would
    have dropped as empty, so it goes, and its item with it.
    """
    marks = re.compile(
        f"{re.escape(numbers.mark)}(\\d+){re.escape(ooxml.LIST_MARK_CLOSE)}"
    )
    # By identity: a Tag hashes and compares by its markup, which this edits.
    owned: dict[int, int] = {}
    empty: set[int] = set()
    for text in soup.find_all(string=marks):
        holder = text.parent
        item = text.find_parent("li")
        found = [int(match.group(1)) for match in marks.finditer(text)]
        if (
            item is not None
            and id(item) not in owned
            and isinstance(item.parent, Tag)
            and item.parent.name == "ol"
        ):
            owned[id(item)] = found.pop(0)
        # mammoth writes every paragraph in an element of its own; the
        # document itself is never one to take out.
        alone = (
            holder is not None
            and holder.parent is not None
            and marks.fullmatch(text)
            and len(holder.contents) == 1
        )
        stripped = marks.sub("", text)
        if stripped:
            text.replace_with(stripped)
        else:
            text.extract()
        if alone and holder is not None:
            if id(holder) in owned:
                empty.add(id(holder))
            else:
                holder.decompose()
                continue
        numbers.unlisted += sum(numbers.values[index] is not None for index in found)
    for ordered in soup.find_all("ol"):
        items = [child for child in ordered.children if isinstance(child, Tag)]
        if not items or all(
            NOTE_ITEM_RE.match(str(item.get("id", ""))) for item in items
        ):
            continue
        if any(item.name != "li" or id(item) not in owned for item in items):
            numbers.unmatched += sum(id(item) not in owned for item in items)
            continue
        shown = [numbers.values[owned[id(item)]] for item in items]
        values = [value for value in shown if value is not None]
        if len(values) < len(shown):
            numbers.unnumbered += len(shown) - len(values)
            continue
        # An empty paragraph keeps its number in Word and is not on the page,
        # so the items either side of it are two runs.
        runs: list[list[tuple[Tag, int]]] = []
        expected: int | None = None
        for item, value in zip(items, values, strict=True):
            if id(item) in empty:
                item.decompose()
                continue
            if value != expected:
                runs.append([])
            runs[-1].append((item, value))
            expected = value + 1
        if not runs:
            ordered.decompose()
            continue
        current = ordered
        for number, run in enumerate(runs):
            if number:
                following = soup.new_tag("ol")
                current.insert_after(following)
                current = following
                for item, _ in run:
                    following.append(item.extract())
            if run[0][1] != 1:
                current["start"] = str(run[0][1])
    numbers.applied = True


class _HtmlLineEndings(HTMLParserTreeBuilder):
    """BeautifulSoup's `html.parser` reader, reading line endings as HTML does.

    An HTML parser turns each `\\r\\n`, and each `\\r` alone, into `\\n` before
    it reads anything else (the input stream's preprocessing), so one file
    saved with Windows line endings and one saved with Unix ones are the same
    page.  `html.parser` keeps every `\\r`, and markdownify kept them in the
    Markdown: a `<pre>`'s blank first and last lines survived its trim, and a
    `\\r` left before a `\\r\\n` was a line break in an item's code and none
    outside a list (docs/09 P92).  The text is decoded exactly as before and
    its line endings are then the browser's; a `&#13;` is a reference, read
    after this, and stays the character it names, as it does in a browser.
    """

    def prepare_markup(
        self, markup: Any, *args: Any, **kwargs: Any
    ) -> Iterable[tuple[str, str | None, str | None, bool]]:
        for text, *rest in super().prepare_markup(markup, *args, **kwargs):
            yield (_LINE_BREAK_RE.sub("\n", text), *rest)  # type: ignore[misc]


#: The formats WebShot presents as text, which the plain-text reader takes by
#: suffix whatever Magika made of them.
TEXT_SUFFIXES = frozenset(
    suffix for suffix, kind in FORMATS.items() if kind.presentation == "verbatim"
)


class _TextPlainTextConverter(PlainTextConverter):
    """MarkItDown's plain-text reader, decoding by `text_encoding`.

    Upstream decodes by the encoding MarkItDown guessed from 64 KB, or, with
    none, by charset-normalizer's guess from all of it, else as UTF-8 with
    every other byte dropped — and says nothing either way.  The bytes are
    decoded here by WebShot's rule instead, whatever the stream was told, and
    a guess comes back as a warning (docs/09 P38).  For UTF-8 and ASCII the
    rule and the declaration agree.  An XML declaration counts as the text's
    own word on its encoding.

    It also takes every format WebShot itself presents as text by its
    suffix: YAML has no MIME type in Python 3.12's `mimetypes`, and XML's is
    `text/` only where the platform has no MIME table of its own (macOS's
    says `application/xml`), so where Magika could not name such a file, as
    when it is empty, upstream took it for a format no reader has (docs/09
    P38-10).
    """

    def accepts(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> bool:
        extension = (stream_info.extension or "").lower()
        if extension in TEXT_SUFFIXES:
            return True
        accepted: bool = super().accepts(file_stream, stream_info, **kwargs)
        return accepted

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        data = file_stream.read()
        decoded = decode_text(
            data, declared=EncodingDetector.find_declared_encoding(data)
        )
        return _WarnedResult(
            decoded.text, () if decoded.warning is None else (decoded.warning,)
        )


class _TextHtmlConverter(HtmlConverter):
    """MarkItDown's HTML reader, writing through `_TextMarkdownify`.

    `convert` is MarkItDown 0.1.8's `HtmlConverter.convert`, because upstream
    names its writer inside that method rather than taking it as a parameter;
    `test_the_text_html_converter_is_upstreams_on_plain_text` holds the copy to
    the original.  It differs in eight places: the document is decoded by
    `text_encoding` (docs/09 P38), its line endings are read as a browser
    reads them (`_HtmlLineEndings`, docs/09 P92), a Word file's lists are
    numbered as Word numbers them, when the Word reader hands over their
    numbers as `word_lists` (docs/09 P31); pictures are drawn from their own
    bytes or said to be missing (docs/09 P36), the attributes
    markdownify writes are made safe, the writer is `_TextMarkdownify`, the
    text the too-deeply-nested fallback returns is encoded, where upstream
    writes it into Markdown raw, and two lists, quotations or definition lists
    that meet are kept apart.

    `list_seams` is for a reader that joins several documents' Markdown, as
    EPUB joins its chapters: their lists are left marked, and it resolves them
    once they are joined.  Without it each document is resolved on its own.

    Upstream decodes by the stream's charset, which for a zip member is
    MarkItDown's guess from 64 KB: a Windows-1252 page that did not say so
    was read as cp1250 (docs/09 P38).  One whose `meta` said so was read
    right, but only because charset-normalizer favours an encoding it finds
    named in the bytes; here a declaration is read as one.
    """

    def __init__(self, list_seams: _ListSeams | None = None) -> None:
        super().__init__()
        self._list_seams = list_seams
        #: The book whose chapters this is reading, set by `_TextEpubConverter`
        #: for the length of one conversion; its chapters name pictures by
        #: their path inside it.
        self.book: _BookPictures | None = None

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        strict: bool = kwargs.pop("strict", False)
        word_lists: ooxml.WordListNumbers | None = kwargs.pop("word_lists", None)
        data = file_stream.read()
        decoded = decode_text(
            data,
            declared=EncodingDetector.find_declared_encoding(data, is_html=True),
            html=True,
        )
        soup = BeautifulSoup(decoded.text, builder=_HtmlLineEndings())
        for script in soup(["script", "style"]):
            script.extract()
        # First, while the lists are as mammoth wrote them and every mark is
        # still in its paragraph; it takes the marks out.
        if word_lists is not None:
            _number_word_lists(soup, word_lists)
        # Before the attributes are encoded: a placeholder quotes the alt text
        # as text, which markdownify then encodes once.
        chapter = getattr(file_stream, "name", None) if self.book else None
        said = [] if decoded.warning is None else [decoded.warning]
        said += _pictures_as_data(
            soup, self.book, chapter if isinstance(chapter, str) else None
        )
        # MarkItDown's EPUB reader keeps only each chapter's Markdown, so a
        # chapter says what it could not carry through the book, by its name:
        # a chapter whose encoding was guessed is named there (docs/09 P38).
        if self.book is not None:
            self.book.warnings += [f"{chapter}: {w}" for w in said]
        _attributes_as_text(soup)
        body = soup.find("body")
        target = body if body else soup
        seams = self._list_seams or _ListSeams()
        try:
            markdown: str = _TextMarkdownify(list_seams=seams, **kwargs).convert_soup(
                target
            )
        except RecursionError:
            if strict:
                raise
            warnings.warn(
                "HTML document is too deeply nested for markdown conversion "
                "(RecursionError). Falling back to plain-text extraction.",
                stacklevel=2,
            )
            markdown = _as_text_lines(target.get_text("\n", strip=True))
        if self._list_seams is None:
            markdown = seams.resolve(markdown)
        return _WarnedResult(
            markdown.strip(),
            title=None if soup.title is None else soup.title.string,
            warnings=tuple(said),
        )


def _writing_html_as_text(
    converter: Any, writer: _TextHtmlConverter | None = None
) -> None:
    """Give one of MarkItDown's converters `_TextHtmlConverter` to write with.

    Its HTML step is the attribute `_html_converter`, not a parameter, so a
    release that renamed it would quietly bring the old writer back.  That is
    refused here, at construction, where every conversion meets it.
    """
    if not isinstance(getattr(converter, "_html_converter", None), HtmlConverter):
        raise RuntimeError(
            f"MarkItDown's {type(converter).__name__} no longer writes through "
            "`_html_converter`, so WebShot cannot keep a document's text from "
            "being read as Markdown (docs/09 P28)."
        )
    converter._html_converter = writer or _TextHtmlConverter()


class _TextDocxConverter(DocxConverter):
    """Word, whose every string mammoth hands markdownify as HTML.

    mammoth writes each picture as a `data:` URL, which MarkItDown truncates
    unless asked to keep it (`keep_data_uris`, docs/09 P36).
    """

    def __init__(self) -> None:
        super().__init__()
        _writing_html_as_text(self)

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        result: DocumentConverterResult = super().convert(
            file_stream, stream_info, **kwargs
        )
        said = getattr(result, "warnings", ())
        return _WarnedResult(
            result.markdown,
            title=result.title,
            warnings=tuple(f"The document: {warning}" for warning in said),
        )


class _TextEpubConverter(EpubConverter):
    """EPUB: chapters through markdownify, metadata written straight in.

    The metadata (`**Title:** …`) is the one string MarkItDown writes itself,
    read through `_get_all_texts_from_nodes`; it comes back encoded, so the
    title the adapter falls back on is decoded again on the way out.

    MarkItDown joins the chapters with a blank line, so a chapter that ends
    with a list and one that begins with a list of the same kind made one list
    too; their lists are resolved once the book is joined (docs/09 P32), and
    their quotations and definition lists with them (docs/09 P63).  A
    chapter whose encoding was guessed is named in the book's warnings
    (docs/09 P38).
    """

    def __init__(self) -> None:
        super().__init__()
        self._list_seams = _ListSeams()
        _writing_html_as_text(self, _TextHtmlConverter(self._list_seams))

    def _get_all_texts_from_nodes(self, dom: Any, tag_name: str) -> list[str]:
        texts: list[str] = super()._get_all_texts_from_nodes(dom, tag_name)
        return [_as_text_lines(text) for text in texts]

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        data = file_stream.read()
        writer: _TextHtmlConverter = self._html_converter
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            writer.book = _BookPictures(archive)
            try:
                result: DocumentConverterResult = super().convert(
                    io.BytesIO(data), stream_info, **kwargs
                )
                said = tuple(writer.book.warnings)
            finally:
                writer.book = None
        return _WarnedResult(
            self._list_seams.resolve(result.markdown),
            said,
            title=html.unescape(result.title) if result.title else result.title,
        )


# --------------------------------------------------------------------------- #
# Text that MarkItDown writes straight into Markdown, encoded in the file first
# --------------------------------------------------------------------------- #

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml"
_SPREADSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _encode_text_of(elements: Iterable[Element]) -> bool:
    changed = False
    for element in elements:
        if element.text:
            encoded = _as_text_lines(element.text)
            changed |= encoded != element.text
            element.text = encoded
    return changed


def _deck_strings(root: Element) -> bool:
    """A slide, its notes, or a chart: what MarkItDown prints from each.

    Every run of text (`a:t`) — shapes, tables, titles, notes — and a chart's
    category labels and series names (`c:v` under `c:cat` and `c:tx`).  Not
    its values, which python-pptx reads as numbers.
    """
    strings = list(root.iter(f"{_A}t"))
    for holder in (*root.iter(f"{_C}cat"), *root.iter(f"{_C}tx")):
        strings.extend(holder.iter(f"{_C}v"))
    return _encode_text_of(strings)


def _sheet_names(root: Element) -> bool:
    """A workbook's sheet names, which MarkItDown writes as `## name`."""
    changed = False
    for sheet in root.iter(f"{_S}sheet"):
        name = sheet.get("name")
        if name:
            encoded = _as_text_lines(name)
            changed |= encoded != name
            sheet.set("name", encoded)
    return changed


#: Where in a deck MarkItDown's text comes from, by OPC content type: the same
#: declaration python-pptx reads a part by.  A table's text reaches markdownify
#: too, but already encoded, and markdownify has nothing left to escape in it.
DECK_TEXT_PARTS: dict[str, Callable[[Element], bool]] = {
    f"{_PRESENTATION}.slide+xml": _deck_strings,
    f"{_PRESENTATION}.notesSlide+xml": _deck_strings,
    "application/vnd.openxmlformats-officedocument.drawingml.chart+xml": _deck_strings,
}
#: The workbook part, in each of the forms openpyxl opens.
WORKBOOK_TEXT_PARTS: dict[str, Callable[[Element], bool]] = {
    f"{_SPREADSHEET}.sheet.main+xml": _sheet_names,
    f"{_SPREADSHEET}.template.main+xml": _sheet_names,
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml": _sheet_names,
}


#: MarkItDown's Markdown for one picture: `![alt](src)` on a line of its own,
#: with `[`, `]` and line breaks taken out of the alt text.
_PICTURE_MARKDOWN_RE = re.compile(r"\n!\[(?P<alt>[^\]\n]*)\]\((?P<src>[^)\s]*)\)\n")


class _TextPptxConverter(PptxConverter):
    """PowerPoint, whose text MarkItDown appends to the Markdown as it reads it.

    A picture is the deck's own bytes as a `data:` URL (`keep_data_uris`), or
    a line of text and a warning (docs/09 P36).  Without that, MarkItDown wrote
    `![alt](Picture2.jpg)`, a name it made up, which the page resolved in the
    deck's folder: a request that failed, or another file of that name.
    """

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        self._picture_warnings: list[str] = []
        deck = ooxml.rewrite_parts(file_stream.read(), DECK_TEXT_PARTS)
        result: DocumentConverterResult = super().convert(
            io.BytesIO(deck), stream_info, **kwargs
        )
        return _WarnedResult(
            result.markdown,
            title=result.title,
            warnings=tuple(self._picture_warnings),
        )

    def _convert_picture_to_markdown(self, shape: Any, **kwargs: Any) -> str:
        markdown: str = super()._convert_picture_to_markdown(shape, **kwargs)
        match = _PICTURE_MARKDOWN_RE.fullmatch(markdown)
        if match is None:
            raise RuntimeError(
                "MarkItDown no longer writes a picture as `![alt](src)` on a line "
                "of its own, so WebShot cannot tell what it would have the page "
                "load (docs/09 P36)."
            )
        alt, src = match["alt"], match["src"]
        if src.lower().startswith("data:"):
            drawn, reason = _drawable(src)
        else:
            # No bytes to read: the picture is linked, not held in the deck.
            drawn, reason = None, "the deck links to it rather than holding it"
        if drawn is not None:
            return f"\n![{alt}]({drawn})\n"
        self._picture_warnings.append(
            f"{_slide_of(shape)}: {_picture_warning(alt, reason)}"
        )
        return f"\n{_as_text_lines(_picture_placeholder(alt, reason))}\n"


def _slide_of(shape: Any) -> str:
    """The slide a shape is on, as "Slide N", numbered as MarkItDown numbers them."""
    try:
        slide = shape.part.slide
        slides = shape.part.package.presentation_part.presentation.slides
        return f"Slide {slides.index(slide) + 1}"
    except (AttributeError, ValueError):
        return "A slide"


class _TextXlsxConverter(XlsxConverter):
    """Excel: cells through markdownify, sheet names written straight in."""

    def __init__(self) -> None:
        super().__init__()
        _writing_html_as_text(self)

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        workbook = ooxml.rewrite_parts(file_stream.read(), WORKBOOK_TEXT_PARTS)
        result: DocumentConverterResult = super().convert(
            io.BytesIO(workbook), stream_info, **kwargs
        )
        return result


# --------------------------------------------------------------------------- #
# Archives
# --------------------------------------------------------------------------- #

#: Member formats whose converter here already writes their text as text.
TEXT_SAFE_MEMBERS = frozenset(
    {".csv", ".docx", ".epub", ".htm", ".html", ".pptx", ".tsv", ".xlsx", ".zip"}
)
#: Member formats that are Markdown, rendered as Markdown as the top-level
#: adapter renders a `.md` file.  A notebook's cells are Markdown and fenced
#: code, and MarkItDown writes them as such.
MARKDOWN_MEMBERS = frozenset({".ipynb", ".markdown", ".md"})


def _code_span(text: str) -> str:
    """`text` as inline code, fenced longer than any backtick run inside it."""
    text = text.replace("\r", " ").replace("\n", " ")
    fence = _fence(text, minimum=1)
    pad = " " if len(fence) > 1 else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _member_markdown(
    markitdown: MarkItDown, data: bytes, name: str
) -> ConvertedMarkdown:
    """One archive member, its text written as text.

    MarkItDown's own zip reader hands members straight to its converters, so
    a CSV inside a zip skipped `_escape_cell` and a TSV was not a table.  What
    a member's converter writes raw — plain text, source code, a PDF's text —
    was inlined as Markdown, so a `.txt` could fetch an image and a `.py`'s
    comments became headings.  It is a code block here, as a top-level `.txt`
    is: literal by construction, and with its lines intact.  A member is read
    by the path a file on its own is, so a deck or a Word file inside a zip
    gets `ooxml`'s rewrite and its fallback too: without them, a zipped deck
    lost every equation, wrapped text box and SmartArt diagram, and said
    nothing (docs/09 P29).  The warnings are the member's own; the zip names it.
    """
    suffix = posixpath.splitext(name)[1].lower()
    converted = _convert_bytes(
        markitdown, data, suffix=suffix, name=posixpath.basename(name)
    )
    if suffix in TEXT_SAFE_MEMBERS or suffix in MARKDOWN_MEMBERS:
        return converted
    body = converted.markdown.rstrip("\n")
    if not body.strip():
        return converted
    fence = _fence(body, minimum=3)
    return dataclasses.replace(converted, markdown=f"{fence}\n{body}\n{fence}")


class _ArchiveResult(_WarnedResult):
    """A zip's Markdown, and what its members' readers could not carry into it.

    MarkItDown's result has no field for that, and returns the object a
    converter made as it is, so a zip nested in a zip hands its warnings up
    the same way.
    """

    def __init__(self, markdown: str, warnings: tuple[str, ...]) -> None:
        super().__init__(markdown, warnings=warnings)


#: An AppleDouble file's first four bytes (RFC 1740).  macOS keeps a file's
#: Finder metadata and extended attributes in one, named `._<file>`, wherever
#: the file itself cannot carry them: beside the file, or under `__MACOSX/` in
#: a zip the Finder made.
APPLE_DOUBLE_MAGIC = b"\x00\x05\x16\x07"
#: How many members a warning about many names before it counts the rest.
NAMED_MEMBERS = 3
#: What `ZipFile.read` raises for one member it cannot extract: an unsupported
#: compression method, a CRC that does not match, a truncated stream.
#: Encryption is told apart before the read, from the member's own flag.
_EXTRACTION_ERRORS = (
    EOFError,
    NotImplementedError,
    RuntimeError,
    zipfile.BadZipFile,
    zlib.error,
)


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


def _named(names: list[str]) -> str:
    """Names joined for a sentence, the ones past `NAMED_MEMBERS` counted."""
    rest = len(names) - NAMED_MEMBERS
    shown = ", ".join(names[:NAMED_MEMBERS])
    return f"{shown} and {rest} more" if rest > 0 else shown


def _by_suffix(names: list[str]) -> str:
    """How many of `names` have each suffix, most first: what the unnamed are."""
    counts = Counter(posixpath.splitext(name)[1].lower() for name in names)
    return ", ".join(
        f"{count} {suffix or 'with no suffix'}"
        for suffix, count in sorted(counts.items(), key=lambda item: -item[1])
    )


def _left_out(names: list[str], why: str, *, kind: str = "member") -> str:
    """One warning for every member left out for the same reason."""
    count = len(names)
    listed = _named(names)
    if count > NAMED_MEMBERS and kind == "member":
        listed += f" ({_by_suffix(names)})"
    verb = "is" if count == 1 else "are"
    return f"{count} {kind}{'' if count == 1 else 's'} {verb} not in the page: {listed}. {why}"


def _reader_failure(exc: MarkItDownException) -> str:
    """What each reader that tried a member raised, without MarkItDown's framing."""
    attempts = exc.attempts if isinstance(exc, FileConversionException) else None
    failures = [
        f"{type(attempt.exc_info[1]).__name__}: {_one_line(attempt.exc_info[1])}"
        for attempt in attempts or ()
        if attempt.exc_info is not None
    ]
    return "; ".join(dict.fromkeys(failures)) or _one_line(exc)


@dataclass
class _LeftOut:
    """The members of one archive left out for a reason they share with others.

    Each reason is one warning naming its first few members and counting the
    rest: a zipped virtualenv had 572 members nothing reads, and as many lines
    would bury the one warning that matters (docs/09 P33).  A member left out
    for a reason of its own (a refused part, a reader that failed, encryption)
    is a warning of its own.
    """

    #: Members no reader WebShot installs could read: no reader at all, or one
    #: MarkItDown has but whose package no WebShot extra installs.
    unread: list[str] = dataclasses.field(default_factory=list)
    #: Members a missing package kept out, by the WebShot extra that installs
    #: it (`None`: the base install's own).
    needing: dict[str | None, list[str]] = dataclasses.field(default_factory=dict)
    #: macOS's metadata files, which are not files of the archive.
    apple_double: list[str] = dataclasses.field(default_factory=list)

    def warnings(self) -> list[str]:
        found: list[str] = []
        for extra, names in sorted(self.needing.items(), key=lambda kv: kv[0] or ""):
            suffixes = sorted({posixpath.splitext(name)[1].lower() for name in names})
            found.append(_left_out(names, _dependency_advice(suffixes, extra)))
        if self.unread:
            them = "it" if len(self.unread) == 1 else "them"
            found.append(_left_out(self.unread, f"WebShot has no reader for {them}."))
        if self.apple_double:
            found.append(
                _left_out(
                    self.apple_double,
                    "macOS writes one for each file it zips, to hold that "
                    "file's Finder metadata and extended attributes, such as "
                    "the address it was downloaded from; it is not a file of "
                    "its own.",
                    kind="AppleDouble member",
                )
            )
        return found


class _TextZipConverter(ZipConverter):
    """A zip, as MarkItDown 0.1.8 writes one, but for how its members are read.

    The heading lines are upstream's, so an archive of ordinary files renders
    as it did; the member names in them are the archive's own text, and are
    encoded like any other.  Each member's warnings come back named for it.

    Every member is in the page or named in a warning, except a directory
    entry, which holds no bytes: the files under it are members of their own.
    Upstream left a member nothing could read out without a word, which is
    how a deck in a zip vanished from a capture on an install without the
    office extra, and a PDF on every install (docs/09 P33).
    """

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        file_path = (
            stream_info.url
            or stream_info.local_path
            or stream_info.filename
            or "(unknown)"
        )
        markdown = f"Content from the zip file {_code_span(file_path)}:\n\n"
        conversion_warnings: list[str] = []
        left_out = _LeftOut()
        with zipfile.ZipFile(file_stream, "r") as archive:
            for entry in archive.infolist():
                name = entry.filename
                if entry.is_dir():
                    continue
                if entry.flag_bits & 0x1:
                    conversion_warnings.append(
                        f"{name} is not in the page: it is encrypted, and "
                        "WebShot does not decrypt archive members."
                    )
                    continue
                try:
                    data = archive.read(entry)
                except _EXTRACTION_ERRORS as exc:
                    # The member's loss, not the archive's: let through, this
                    # ended the whole capture with exit 2 (docs/09 P33).
                    conversion_warnings.append(
                        f"{name} is not in the page: it could not be extracted "
                        f"({type(exc).__name__}: {_one_line(exc)})."
                    )
                    continue
                if posixpath.basename(name).startswith("._") and data.startswith(
                    APPLE_DOUBLE_MAGIC
                ):
                    # Read as text, one put the address its file was
                    # downloaded from into the page (docs/09 P33).
                    left_out.apple_double.append(name)
                    continue
                try:
                    member = _member_markdown(self._markitdown, data, name)
                except (ValueError, UsageError) as exc:
                    # WebShot refusing the member: `ooxml` a part that
                    # declares an entity (spec §6.6) or that the member's own
                    # archive cannot give up, the CSV reader a row it cannot
                    # split.
                    conversion_warnings.append(
                        f"{name} is not in the page: {_one_line(exc)}"
                    )
                    continue
                except MarkItDownException as exc:
                    suffix = posixpath.splitext(name)[1].lower()
                    if isinstance(exc, UnsupportedFormatException):
                        left_out.unread.append(name)
                    elif not _needs_a_dependency(exc):
                        conversion_warnings.append(
                            f"{name} is not in the page: its reader failed "
                            f"({_reader_failure(exc)})."
                        )
                    elif suffix in FORMATS:
                        left_out.needing.setdefault(FORMATS[suffix].extra, []).append(
                            name
                        )
                    else:
                        # MarkItDown has a reader (PDF, .xls, Outlook), but
                        # its package is in no WebShot extra, so its
                        # `pip install` advice is no advice here.
                        left_out.unread.append(name)
                    continue
                markdown += f"## File: {_as_text_lines(name)}\n\n"
                markdown += member.markdown + "\n\n"
                conversion_warnings += [
                    f"{name}: {warning}" for warning in member.warnings
                ]
        conversion_warnings += left_out.warnings()
        return _ArchiveResult(markdown.strip(), tuple(conversion_warnings))


def _markitdown() -> MarkItDown:
    """MarkItDown, with each converter that writes text as Markdown replaced.

    Replaced in place, not registered beside: when a converter raises,
    MarkItDown moves on to the next that accepts the file, so a builtin left
    behind would read a deck the WebShot converter had refused — and write its
    text raw.  Each replacement keeps its original's position and priority, and
    one that is not found is refused, because the original would then still be
    there, unreplaced.
    """
    markitdown = MarkItDown(enable_plugins=False)
    replacements: dict[type, Callable[[], object]] = {
        DocxConverter: _TextDocxConverter,
        EpubConverter: _TextEpubConverter,
        HtmlConverter: _TextHtmlConverter,
        PlainTextConverter: _TextPlainTextConverter,
        PptxConverter: _TextPptxConverter,
        XlsxConverter: _TextXlsxConverter,
        ZipConverter: lambda: _TextZipConverter(markitdown=markitdown),
    }
    registrations = markitdown._converters
    replaced: set[type] = set()
    for index, registration in enumerate(registrations):
        kind = type(registration.converter)
        if kind in replacements:
            registrations[index] = dataclasses.replace(
                registration, converter=replacements[kind]()
            )
            replaced.add(kind)
    missing = sorted(kind.__name__ for kind in set(replacements) - replaced)
    if missing:
        raise RuntimeError(
            f"MarkItDown no longer registers {', '.join(missing)}, so WebShot "
            "cannot keep a document's text from being read as Markdown "
            "(docs/09 P28)."
        )
    return markitdown


def _needs_a_dependency(exc: MarkItDownException) -> bool:
    """Whether a converter failed for want of a package, wherever it says so.

    MarkItDown 0.1.x does not let a converter's `MissingDependencyException`
    escape `convert_stream`: it records it as a failed attempt and raises
    `FileConversionException`, whose message is that converter's own advice,
    `pip install 'markitdown[docx]'`. Matching the outer type alone therefore
    passed that advice through to a user who installs WebShot from a checkout,
    where it does not apply. The unwrapped form is still recognised, so a
    release that lets the exception through is answered the same way.
    """
    if isinstance(exc, MissingDependencyException):
        return True
    attempts = exc.attempts if isinstance(exc, FileConversionException) else None
    return any(
        attempt.exc_info is not None
        and isinstance(attempt.exc_info[1], MissingDependencyException)
        for attempt in attempts or ()
    )


def _conversion_error(
    exc: MarkItDownException, path: Path, suffix: str, extra: str | None
) -> UsageError:
    """What a user is told when MarkItDown could not convert `path`."""
    if not _needs_a_dependency(exc):
        return UsageError(f"Could not read {path.name} as {suffix}: {exc}")
    return UsageError(_dependency_advice([path.suffix], extra))


def _dependency_advice(suffixes: list[str], extra: str | None) -> str:
    """What to install when a reader's package is missing, for a file or a member.

    One wording for both, so the advice a zip member gets is the advice its
    file would get on its own, and never MarkItDown's `pip install` line.
    """
    # From the checkout, never `pip install webshot[...]`: WebShot is not on
    # PyPI, and that name there belongs to an unrelated project. A format with
    # no extra is one the base install reads, so a missing dependency there
    # means the install is incomplete, not that an extra is absent.
    formats = "that format" if len(suffixes) == 1 else "those formats"
    if extra is None:
        advice = (
            "a dependency of WebShot's base install, which is missing. "
            "Restore it from your checkout: uv sync (name any extras you "
            "use too)"
        )
    else:
        advice = (
            f"the optional dependencies for {formats}. Install "
            f"webshot[{extra}] from your checkout: uv sync --extra {extra} "
            f"(name any other extras you use too)"
        )
    return f"Reading {_joined(suffixes)} files needs {advice}."


def _joined(items: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return " and ".join(filter(None, (", ".join(items[:-1]), items[-1])))


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _list_warnings(lists: ooxml.WordListNumbers) -> list[str]:
    """Where the page's numbering of a Word file's lists is not Word's."""
    warnings: list[str] = []
    if lists.unmatched:
        warnings.append(
            "The number Word shows could not be worked out for "
            f"{_counted(lists.unmatched, 'list item')}, so the page numbers the "
            "list each is in from 1 (docs/09 P31)."
        )
    if lists.unnumbered:
        warnings.append(
            f"Word shows {_counted(lists.unnumbered, 'list item')} without a "
            "number, which the page numbers (docs/09 P31)."
        )
    if lists.unlisted:
        warnings.append(
            f"Word numbers {_counted(lists.unlisted, 'paragraph')} that the page "
            "shows without a number, such as a numbered heading (docs/09 P31)."
        )
    return warnings


def _convert_bytes(
    markitdown: MarkItDown, data: bytes, *, suffix: str, name: str
) -> ConvertedMarkdown:
    """One file's bytes as Markdown, read as the format `suffix` names.

    What a file on its own and a member of a zip share: the CSV escape, the
    encoding MarkItDown is told, the `ooxml` rewrite and its fallback, Word's
    list numbers, and the warnings each leaves.  Kept in one place so a member
    cannot be read by a path the file was not, which is how a zipped deck came
    to skip the rewrite (docs/09 P29).  `name` is the file's own, for the
    reader and for an error.  A failed read raises, and the caller decides
    what that costs.
    """
    original = data
    conversion_warnings: list[str] = []
    charset: str | None = None
    lists: ooxml.WordListNumbers | None = None
    if suffix in (".csv", ".tsv"):
        data, conversion_warnings = _as_escaped_csv(
            data, "\t" if suffix == ".tsv" else ",", name
        )
        suffix = ".csv"
        # WebShot wrote these bytes, so their encoding is a fact, not a guess.
        charset = "utf-8"
    elif suffix == ".pptx":
        data, conversion_warnings = ooxml.prepare_pptx(data, _equation_latex)
    elif suffix == ".docx":
        data, conversion_warnings = ooxml.prepare_docx(data, _equation_latex)
    else:
        charset = _declared_charset(data)
    prepared = data is not original
    if suffix == ".docx":
        data, lists = ooxml.number_word_lists(data)

    stream_info = StreamInfo(extension=suffix, filename=name, charset=charset)
    try:
        result = markitdown.convert_stream(
            io.BytesIO(data),
            stream_info=stream_info,
            keep_data_uris=True,
            **({} if lists is None else {"word_lists": lists}),
        )
    except MarkItDownException as exc:
        rewritten = suffix in (".pptx", ".docx") and data is not original
        if not rewritten or _needs_a_dependency(exc):
            raise
        # The rewrite is WebShot's, and a file the reader opened as it was
        # must not become one it cannot open. Read it as it is, and say what
        # that costs: the earlier warnings described the rewrite, so they go.
        # The file is not named: a zip puts its member's name in front.
        result = markitdown.convert_stream(
            io.BytesIO(original), stream_info=stream_info, keep_data_uris=True
        )
        reason = " ".join(str(exc).split())
        conversion_warnings = []
        if prepared:
            conversion_warnings.append(
                "The file could not be read once its equations, AlternateContent "
                f"and SmartArt were made readable ({reason}), so it was read as "
                "it is: the text in those is not in the page (docs/09 P27)."
            )
        if lists is not None:
            conversion_warnings.append(
                "The file could not be read once its list items were marked with "
                f"Word's numbers ({reason}), so it was read as it is: its lists "
                "are numbered from 1, whatever Word numbers them (docs/09 P31)."
            )
        lists = None
    if lists is not None:
        if not lists.applied:
            # The numbers travel to the HTML step as a converter option. A
            # release that stopped passing it on would leave every mark in
            # the text, and number nothing.
            raise RuntimeError(
                "MarkItDown's Word reader no longer hands its options to its "
                "HTML step, so WebShot cannot number a Word file's lists as "
                "Word does (docs/09 P31)."
            )
        conversion_warnings += _list_warnings(lists)
    if isinstance(result, _WarnedResult):
        conversion_warnings = [*conversion_warnings, *result.warnings]
    return ConvertedMarkdown(
        markdown=result.markdown,
        title=result.title,
        warnings=tuple(conversion_warnings),
    )


def to_markdown(
    path: Path, *, suffix: str, extra: str | None = None
) -> ConvertedMarkdown:
    """Convert one local file to Markdown, as the format `suffix` names.

    `suffix` is the adapter's decision, not a sniff: WebShot routes by its own
    table (docs/03 §9 keeps MIME sniffing custom) and MarkItDown is told what
    it is being given rather than left to guess.  `extra` is the WebShot extra
    that installs this format's converter, if any — MarkItDown's own missing
    dependency message names *its* extras, and a user cannot install those.
    """
    try:
        return _convert_bytes(
            _markitdown(), path.read_bytes(), suffix=suffix, name=path.name
        )
    except MarkItDownException as exc:
        raise _conversion_error(exc, path, suffix, extra) from exc
