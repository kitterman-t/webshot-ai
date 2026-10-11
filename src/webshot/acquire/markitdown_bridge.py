"""The one module that imports MarkItDown (CONTRIBUTING rule 1).

Bytes in, Markdown out.  Everything WebShot decides about a local file —
which formats it accepts, how big they may be, how the resulting page is
titled and styled — is decided in `adapters.py`; this file's whole job is to
hand MarkItDown a stream with an explicit type hint and hand back a string.

Four behaviors of MarkItDown are corrected here rather than inherited, because
each silently damages content WebShot promises to preserve (docs/09 P3-1):

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

MarkItDown itself parses XML through `defusedxml` (its RSS and EPUB
converters), which is *not* what spec §6.6 rests on: the adapter defuses every
XML byte before this module is ever called, and the Office parts this module
and `ooxml` rewrite are read through `defusedxml` too.
"""

from __future__ import annotations

import csv
import dataclasses
import html
import io
import posixpath
import re
import warnings
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup
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


def _as_escaped_csv(data: bytes, delimiter: str, path_name: str) -> bytes:
    """Re-delimit a separated-values file and escape its cells, in one pass.

    Both halves of this exist because MarkItDown's CSV converter is comma-only
    *and* flattens a line break in a cell to a space;
    doing them together means TSV is not parsed and re-serialized twice, and
    that a fix to quoting or encoding cannot reach one path and miss the other.
    """
    rows = csv.reader(
        io.StringIO(data.decode("utf-8-sig", errors="replace")), delimiter=delimiter
    )
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
    return buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Text that MarkItDown writes through markdownify
# --------------------------------------------------------------------------- #


class _TextMarkdownify(_CustomMarkdownify):
    """MarkItDown's Markdown writer, writing a document's text as text.

    markdownify escapes `*` and `_` and, optionally, a list aimed at CommonMark
    that Python-Markdown does not honour: it reads `\\<img>` as a backslash and
    a tag.  So `escape` is replaced rather than added to — escaping on top of
    markdownify's would print its backslashes.  It is not called inside `pre`,
    `code`, `kbd` or `samp`, whose text Markdown keeps literal.
    """

    def escape(self, text: str, parent_tags: Iterable[str]) -> str:
        # A text node can open a block, and keeps any newline its HTML had;
        # not in a heading or a table cell, which markdownify writes as one
        # line after its own marker (`_inline`).  Every negative number in a
        # sheet was a reference otherwise, and a large workbook 10% slower.
        if "_inline" in parent_tags:
            return _as_text(text)
        return _as_text_lines(text)

    def convert_pre(self, el: object, text: str, parent_tags: object) -> str:
        """A code block, fenced so that none of its own lines can close it.

        markdownify always fences with three backticks, and Python-Markdown
        ends the block at the first line that is those three alone: a `<pre>`
        showing a fenced example closed early, and everything after it was
        read as Markdown, raw HTML included.
        """
        block: str = super().convert_pre(el, text, parent_tags)
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


class _TextHtmlConverter(HtmlConverter):
    """MarkItDown's HTML reader, writing through `_TextMarkdownify`.

    `convert` is MarkItDown 0.1.8's `HtmlConverter.convert`, because upstream
    names its writer inside that method rather than taking it as a parameter;
    `test_the_text_html_converter_is_upstreams_on_plain_text` holds the copy to
    the original.  It differs in three places: the attributes markdownify
    writes are made safe first, the writer is `_TextMarkdownify`, and the text
    the too-deeply-nested fallback returns is encoded, where upstream writes it
    into Markdown raw.
    """

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        strict: bool = kwargs.pop("strict", False)
        encoding = "utf-8" if stream_info.charset is None else stream_info.charset
        soup = BeautifulSoup(file_stream, "html.parser", from_encoding=encoding)
        for script in soup(["script", "style"]):
            script.extract()
        _attributes_as_text(soup)
        body = soup.find("body")
        target = body if body else soup
        try:
            markdown: str = _TextMarkdownify(**kwargs).convert_soup(target)
        except RecursionError:
            if strict:
                raise
            warnings.warn(
                "HTML document is too deeply nested for markdown conversion "
                "(RecursionError). Falling back to plain-text extraction.",
                stacklevel=2,
            )
            markdown = _as_text_lines(target.get_text("\n", strip=True))
        return DocumentConverterResult(
            markdown=markdown.strip(),
            title=None if soup.title is None else soup.title.string,
        )


def _writing_html_as_text(converter: Any) -> None:
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
    converter._html_converter = _TextHtmlConverter()


class _TextDocxConverter(DocxConverter):
    """Word, whose every string mammoth hands markdownify as HTML."""

    def __init__(self) -> None:
        super().__init__()
        _writing_html_as_text(self)


class _TextEpubConverter(EpubConverter):
    """EPUB: chapters through markdownify, metadata written straight in.

    The metadata (`**Title:** …`) is the one string MarkItDown writes itself,
    read through `_get_all_texts_from_nodes`; it comes back encoded, so the
    title the adapter falls back on is decoded again on the way out.
    """

    def __init__(self) -> None:
        super().__init__()
        _writing_html_as_text(self)

    def _get_all_texts_from_nodes(self, dom: Any, tag_name: str) -> list[str]:
        texts: list[str] = super()._get_all_texts_from_nodes(dom, tag_name)
        return [_as_text_lines(text) for text in texts]

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        result: DocumentConverterResult = super().convert(
            file_stream, stream_info, **kwargs
        )
        if result.title:
            result.title = html.unescape(result.title)
        return result


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


class _TextPptxConverter(PptxConverter):
    """PowerPoint, whose text MarkItDown appends to the Markdown as it reads it."""

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        deck = ooxml.rewrite_parts(file_stream.read(), DECK_TEXT_PARTS)
        result: DocumentConverterResult = super().convert(
            io.BytesIO(deck), stream_info, **kwargs
        )
        return result


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


class _ArchiveResult(DocumentConverterResult):
    """A zip's Markdown, and what its members' readers could not carry into it.

    MarkItDown's result has no field for that, and returns the object a
    converter made as it is, so a zip nested in a zip hands its warnings up
    the same way.
    """

    def __init__(self, markdown: str, warnings: tuple[str, ...]) -> None:
        super().__init__(markdown)
        self.warnings = warnings


class _TextZipConverter(ZipConverter):
    """A zip, as MarkItDown 0.1.8 writes one, but for how its members are read.

    The heading lines are upstream's, so an archive of ordinary files renders
    as it did; the member names in them are the archive's own text, and are
    encoded like any other.  Each member's warnings come back named for it.
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
        with zipfile.ZipFile(file_stream, "r") as archive:
            for entry in archive.infolist():
                try:
                    member = _member_markdown(
                        self._markitdown, archive.read(entry), entry.filename
                    )
                except ValueError as exc:
                    # `ooxml` refusing a part: one that declares an entity
                    # (spec §6.6), or one the member's own archive cannot give
                    # up.  Left out like a member nothing reads, but WebShot
                    # decided it, so the manifest says so.
                    conversion_warnings.append(
                        f"{entry.filename} is not in the page: "
                        f"{' '.join(str(exc).split())}"
                    )
                    continue
                except (UnsupportedFormatException, FileConversionException):
                    # Upstream's choice, kept: a member nothing reads is left
                    # out rather than failing the archive.
                    continue
                markdown += f"## File: {_as_text_lines(entry.filename)}\n\n"
                markdown += member.markdown + "\n\n"
                conversion_warnings += [
                    f"{entry.filename}: {warning}" for warning in member.warnings
                ]
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
    # From the checkout, never `pip install webshot[...]`: WebShot is not on
    # PyPI, and that name there belongs to an unrelated project. A format with
    # no extra is one the base install reads, so a missing dependency there
    # means the install is incomplete, not that an extra is absent.
    if extra is None:
        advice = (
            "a dependency of WebShot's base install, which is missing. "
            "Restore it from your checkout: uv sync (name any extras you "
            "use too)"
        )
    else:
        advice = (
            f"the optional dependencies for that format. Install "
            f"webshot[{extra}] from your checkout: uv sync --extra {extra} "
            f"(name any other extras you use too)"
        )
    return UsageError(f"Reading {path.suffix} files needs {advice}.")


def _convert_bytes(
    markitdown: MarkItDown, data: bytes, *, suffix: str, name: str
) -> ConvertedMarkdown:
    """One file's bytes as Markdown, read as the format `suffix` names.

    What a file on its own and a member of a zip share: the CSV escape, the
    `ooxml` rewrite and its fallback, and the warnings each leaves.  Kept in
    one place so a member cannot be read by a path the file was not, which is
    how a zipped deck came to skip the rewrite (docs/09 P29).  `name` is the
    file's own, for the reader and for an error.  A failed read raises, and
    the caller decides what that costs.
    """
    original = data
    conversion_warnings: list[str] = []
    if suffix in (".csv", ".tsv"):
        data = _as_escaped_csv(data, "\t" if suffix == ".tsv" else ",", name)
        suffix = ".csv"
    elif suffix == ".pptx":
        data, conversion_warnings = ooxml.prepare_pptx(data, _equation_latex)
    elif suffix == ".docx":
        data, conversion_warnings = ooxml.prepare_docx(data, _equation_latex)

    stream_info = StreamInfo(extension=suffix, filename=name)
    try:
        result = markitdown.convert_stream(io.BytesIO(data), stream_info=stream_info)
    except MarkItDownException as exc:
        rewritten = suffix in (".pptx", ".docx") and data is not original
        if not rewritten or _needs_a_dependency(exc):
            raise
        # The rewrite is WebShot's, and a file the reader opened as it was
        # must not become one it cannot open. Read it as it is, and say what
        # that costs: the earlier warnings described the rewrite, so they go.
        # The file is not named: a zip puts its member's name in front.
        result = markitdown.convert_stream(
            io.BytesIO(original), stream_info=stream_info
        )
        conversion_warnings = [
            "The file could not be read once its equations, AlternateContent "
            f"and SmartArt were made readable ({' '.join(str(exc).split())}), so "
            "it was read as it is: "
            "the text in those is not in the page (docs/09 P27)."
        ]
    if isinstance(result, _ArchiveResult):
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
