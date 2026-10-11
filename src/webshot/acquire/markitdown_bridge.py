"""The one module that imports MarkItDown (CONTRIBUTING rule 1).

Bytes in, Markdown out.  Everything WebShot decides about a local file —
which formats it accepts, how big they may be, how the resulting page is
titled and styled — is decided in `adapters.py`; this file's whole job is to
hand MarkItDown a stream with an explicit type hint and hand back a string.

Two behaviors of MarkItDown are corrected here rather than inherited, because
both silently damage content WebShot promises to preserve (docs/09 P3-1):

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
   archive first, and says what it still could not read (docs/09 P27).

MarkItDown itself parses XML through `defusedxml` (its RSS and EPUB
converters), which is *not* what spec §6.6 rests on: the adapter defuses every
XML byte before this module is ever called.
"""

from __future__ import annotations

import csv
import html
import io
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element

from markitdown import (
    FileConversionException,
    MarkItDown,
    MarkItDownException,
    MissingDependencyException,
    StreamInfo,
)
from markitdown.converter_utils.docx.math.omml import oMath2Latex

from ..errors import UsageError
from . import ooxml

#: Markdown constructs that change what a cell *is* rather than how it looks.
#: Anchored per cell, not per line: after the newline collapse below a cell is
#: one line, so a leading marker is the only position that can restructure it —
#: and `[` is escaped anywhere, because a link may start mid-cell.  `|` is not
#: here: MarkItDown escapes every pipe in a cell, and a second escape would
#: print the backslash (docs/09 P10-23).
MARKDOWN_OPENER_RE = re.compile(r"^[#>\-+*=`~]|\[")


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

    Three separate escapes, because a cell passes through three grammars on its
    way to the page:

    * **HTML.** MarkItDown emits cell text into Markdown, and Markdown passes
      raw HTML straight through. A cell reading `<img src="https://tracker/x">`
      therefore became a real element, and rendering the document fetched that
      URL — a beacon fired by opening a local spreadsheet, and an outbound
      request to an address chosen by whoever wrote the file. `img[src]` is in
      `MARKDOWN_POLICY`, so sanitization does not stop it and was never meant
      to: the allowlist decides what *Markdown* may emit, and this text is not
      Markdown (docs/09 P8-9).
    * **Markdown.** A leading `#`, `>`, `-`, or backtick restructures the cell,
      and `[text](url)` makes a link out of tabular data. Backslash-escaping
      the openers keeps the value looking like itself.
    * **The table row.** A newline would end the row, so it becomes `<br>`.
      `|` would end the cell, and from 0.1.8 MarkItDown escapes it — together
      with any backslashes in front of it — so it is left alone here; escaping
      it twice put a visible backslash in the cell (docs/09 P10-23).

    The v2 CSV renderer HTML-escaped every cell; that property was lost in the
    move to MarkItDown and is restored here.
    """
    escaped = html.escape(cell, quote=False)
    escaped = MARKDOWN_OPENER_RE.sub(r"\\\g<0>", escaped)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


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
    data = path.read_bytes()
    original = data
    warnings: list[str] = []
    if suffix in (".csv", ".tsv"):
        data = _as_escaped_csv(data, "\t" if suffix == ".tsv" else ",", path.name)
        suffix = ".csv"
    elif suffix == ".pptx":
        data, warnings = ooxml.prepare_pptx(data, _equation_latex)
    elif suffix == ".docx":
        data, warnings = ooxml.prepare_docx(data, _equation_latex)

    converter = MarkItDown(enable_plugins=False)
    stream_info = StreamInfo(extension=suffix, filename=path.name)
    try:
        result = converter.convert_stream(io.BytesIO(data), stream_info=stream_info)
    except MarkItDownException as exc:
        rewritten = suffix in (".pptx", ".docx") and data is not original
        if not rewritten or _needs_a_dependency(exc):
            raise _conversion_error(exc, path, suffix, extra) from exc
        # The rewrite is WebShot's, and a file the reader opened as it was
        # must not become one it cannot open. Read it as it is, and say what
        # that costs: the earlier warnings described the rewrite, so they go.
        try:
            result = converter.convert_stream(
                io.BytesIO(original), stream_info=stream_info
            )
        except MarkItDownException as again:
            raise _conversion_error(again, path, suffix, extra) from again
        warnings = [
            f"{path.name} could not be read once its equations, AlternateContent "
            f"and SmartArt were made readable ({' '.join(str(exc).split())}), so "
            "it was read as it is: "
            "the text in those is not in the page (docs/09 P27)."
        ]
    return ConvertedMarkdown(
        markdown=result.markdown, title=result.title, warnings=tuple(warnings)
    )
