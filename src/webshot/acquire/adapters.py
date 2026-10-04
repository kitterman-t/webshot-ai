"""Adapters that turn local data files into printable, semantic HTML.

MarkItDown does the reading (`markitdown_bridge`), Python-Markdown does the
rendering, and nh3 does the sanitizing.  What stays WebShot's own is what
docs/03 §9 says stays WebShot's own, and each piece is here because delegating
it would lose something:

- **Format routing.**  WebShot decides what it accepts from the filename and
  tells MarkItDown what it is handing over.  Sniffing the bytes instead would
  make the accepted-format list a property of an upstream library's heuristics.
- **The 100 MB guard, and archive safety** (spec §6.7).  A zip-shaped input —
  `.zip`, `.epub`, and the OOXML formats, which are all zip containers — is
  inspected before any converter opens it: expanded size and member count
  against the same policy, and no member path that escapes an extraction root.
- **defusedxml on every XML byte** (spec §6.6).  MarkItDown parses XML safely
  where it parses XML at all, but a plain `.xml` file reaches its plain-text
  converter unparsed, so the guarantee has to be ours and testable.
- **The image wrapper page.**  MarkItDown reduces an image to its EXIF fields;
  WebShot's whole job is to show the picture (docs/03 §9).
- **Sanitization.**  Rendered Markdown is untrusted HTML like any other.
"""

from __future__ import annotations

import html
import io
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, ParseError

import markdown  # type: ignore[import-untyped]
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from markdown.extensions import Extension  # type: ignore[import-untyped]
from markdown.treeprocessors import Treeprocessor  # type: ignore[import-untyped]

from ..netpolicy import file_url_path
from ..sanitize import Policy, sanitize

MAX_LOCAL_SOURCE_BYTES = 100 * 1024 * 1024
#: An archive may not claim to expand past the same limit a plain file is held
#: to; both figures are read from the central directory, so a bomb is refused
#: before anything is decompressed.  The member cap is deliberately loose,
#: because expansion is what the size limit above guards: OOXML carries about
#: four parts per slide, so a few thousand members is an ordinary large deck
#: and only an archive built to make a converter spin reaches this.
MAX_ARCHIVE_MEMBERS = 20_000

HTML_SUFFIXES = {".html", ".htm"}
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
}
#: Inputs that are XML, whoever ends up reading them (spec §6.6).
XML_SUFFIXES = {".xml", ".svg"}
#: Inputs that are zip containers, whoever ends up opening them (spec §6.7).
ARCHIVE_SUFFIXES = {".zip", ".epub", ".docx", ".pptx", ".xlsx"}

#: How a format reaches the page.  `native` is Markdown itself, rendered
#: directly so that relative links and images keep resolving against the
#: file's own directory; `converted` is MarkItDown's Markdown; `verbatim` is
#: text MarkItDown passes through unchanged, which is presented as a code
#: block rather than reflowed as prose (JSON is not Markdown).
Presentation = Literal["native", "converted", "verbatim"]


@dataclass(frozen=True, slots=True)
class SourceFormat:
    kind: str
    label: str
    presentation: Presentation
    #: A section heading v2 published above this format's content, retained so
    #: that swapping the reader does not silently change a document's outline
    #: (and its PDF bookmarks).  Data rather than a branch, because that is all
    #: it ever was; the parity corpus is what holds it in place.
    section: str = ""
    #: The WebShot extra that installs this format's converter, if any. The
    #: bridge quotes it in the message a user gets when the converter is
    #: missing, so "which formats need the extra" is stated once.
    extra: str | None = None


FORMATS: dict[str, SourceFormat] = {
    ".md": SourceFormat("markdown", "Markdown document", "native"),
    ".markdown": SourceFormat("markdown", "Markdown document", "native"),
    ".csv": SourceFormat("csv", "Tabular data", "converted"),
    ".tsv": SourceFormat("tsv", "Tabular data", "converted"),
    ".json": SourceFormat("json", "JSON data", "verbatim"),
    ".jsonl": SourceFormat("jsonl", "JSON Lines data", "verbatim", section="Records"),
    ".txt": SourceFormat("txt", "Text document", "verbatim"),
    ".log": SourceFormat("log", "Text document", "verbatim"),
    ".xml": SourceFormat("xml", "Text document", "verbatim"),
    ".yaml": SourceFormat("yaml", "Text document", "verbatim"),
    ".yml": SourceFormat("yml", "Text document", "verbatim"),
    ".epub": SourceFormat("epub", "EPUB book", "converted"),
    ".zip": SourceFormat("zip", "Archive contents", "converted"),
    ".docx": SourceFormat("docx", "Word document", "converted", extra="office"),
    ".pptx": SourceFormat(
        "pptx", "PowerPoint presentation", "converted", extra="office"
    ),
    ".xlsx": SourceFormat("xlsx", "Excel workbook", "converted", extra="office"),
}

#: Every suffix `materialize_source` reads, derived rather than listed so that a
#: format added above is recognised by the router too. The router uses it to
#: tell a mistyped file name from a web address: `report.csv` that is not on
#: disk is a missing file, where it used to become `https://report.csv` and fail
#: as a network error with exit 3 instead of a usage error with exit 2.
LOCAL_SUFFIXES = frozenset(HTML_SUFFIXES | IMAGE_SUFFIXES | FORMATS.keys())

#: The import that proves each office converter is really usable, for
#: `webshot doctor`. Which formats need the extra is `FORMATS` above; this is
#: the separate fact of what to import to check one, and a test holds the two
#: in agreement.
OFFICE_CONVERTER_MODULES = {".docx": "mammoth", ".pptx": "pptx", ".xlsx": "openpyxl"}

#: The v2 bleach allowlist, ported to nh3 (docs/05 task 3.3).  It describes
#: what rendered Markdown may contain: document structure and tables, no
#: scripting, no embedding.
MARKDOWN_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
MARKDOWN_ATTRIBUTES = {
    "*": {"id", "class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
}
#: Deliberately *not* built on nh3's defaults: a local document is rendered
#: from Markdown, so what it may contain is exactly what Markdown emits.  The
#: URL-scheme half of spec §6.5 is shared with the snapshot and lives with the
#: sanitizer; relative references are untouched by any allowlist, so the
#: `<base href>` below still resolves a Markdown document's own images.
MARKDOWN_POLICY = Policy(tags=frozenset(MARKDOWN_TAGS), attributes=MARKDOWN_ATTRIBUTES)


@dataclass(slots=True)
class MaterializedSource:
    browser_url: str
    original_source: str
    kind: str
    original_path: Path | None = None
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    def cleanup(self) -> None:
        if self.temporary_directory:
            self.temporary_directory.cleanup()


def sanitize_markdown_html(rendered: str) -> str:
    """The allowlist pass that makes a rendered local document publishable."""
    return sanitize(rendered, MARKDOWN_POLICY)


def _document_template(
    title: str,
    source_type: str,
    body: str,
    *,
    base_href: str = "",
    display_title: bool = True,
) -> str:
    safe_title = html.escape(title)
    base_tag = (
        f'<base href="{html.escape(base_href, quote=True)}">' if base_href else ""
    )
    title_heading = f"<h1>{safe_title}</h1>" if display_title else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {base_tag}
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="webshot-source-type" content="{html.escape(source_type)}">
  <title>{safe_title}</title>
  <style>
    :root {{ font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; }}
    body {{ margin: 0; background: #f1f5f9; }}
    main {{ box-sizing: border-box; width: min(100% - 40px, 1080px); margin: 28px auto; padding: 48px; background: #fff; }}
    .source-type {{ color: #64748b; font-size: 0.875rem; letter-spacing: 0.06em; text-transform: uppercase; }}
    h1 {{ color: #173f73; overflow-wrap: anywhere; }}
    h2, h3, h4 {{ color: #245b96; }}
    p, li {{ line-height: 1.6; }}
    a {{ color: #245b96; }}
    pre {{ padding: 18px; overflow-wrap: anywhere; white-space: pre-wrap; background: #f8fafc; border: 1px solid #dbe3ee; border-radius: 6px; line-height: 1.45; tab-size: 4; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ padding: 8px 10px; border: 1px solid #cbd5e1; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #e8f1fb; }}
    figure {{ margin: 24px 0; text-align: center; }}
    figure img {{ max-width: 100%; height: auto; }}
    figcaption {{ margin-top: 10px; color: #64748b; }}
    dl {{ display: grid; grid-template-columns: minmax(140px, 0.35fr) 1fr; gap: 8px 18px; }}
    dt {{ font-weight: 700; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
  </style>
</head>
<body><main><p class="source-type">{html.escape(source_type)}</p>{title_heading}{body}</main></body>
</html>"""


def _as_code_block(text: str) -> str:
    """Fence verbatim text, with a fence longer than any run inside it.

    Trailing newlines are dropped because the fence supplies the last one:
    keeping the file's own would render a blank line inside the block that is
    not in the document.  The byte-exact original is preserved separately, as
    the `source/` copy in the bundle.
    """
    body = text.rstrip("\n")
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{body}\n{fence}"


class _HeaderCellScope(Treeprocessor):
    """Mark rendered table header cells as column headers.

    Python-Markdown's `tables` extension emits bare `<th>`; v2's hand-written
    CSV renderer emitted `scope="col"`, and that attribute is what tells a
    screen reader — and the PDF structure tree Chromium builds from this page
    — which cells head which columns.  Restored through Markdown's own
    tree-processing hook so it applies to every table on an adapter page, not
    just the CSV ones v2 covered.
    """

    def run(self, root: Element) -> None:
        for head in root.iter("thead"):
            for cell in head.iter("th"):
                cell.set("scope", "col")


class TableHeaderScope(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.treeprocessors.register(_HeaderCellScope(md), "webshot-th-scope", 5)


#: The rendering configuration for every local document, in one place.
MARKDOWN_EXTENSIONS: tuple[str | Extension, ...] = (
    "extra",
    "sane_lists",
    "toc",
    TableHeaderScope(),
)


HEADING_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _render_markdown_document(
    source_markdown: str, *, fallback_title: str, label: str, base_href: str
) -> str:
    """Markdown to a sanitized, printable page — the one rendering path."""
    body = sanitize_markdown_html(
        markdown.markdown(
            source_markdown,
            extensions=list(MARKDOWN_EXTENSIONS),
            output_format="html5",
        )
    )
    first_heading = HEADING_RE.search(body)
    document_title = (
        html.unescape(re.sub(r"<[^>]+>", "", first_heading.group(1))).strip()
        if first_heading
        else fallback_title
    )
    return _document_template(
        document_title,
        label,
        body,
        base_href=base_href,
        display_title=not bool(first_heading),
    )


def guard_xml(path: Path) -> None:
    """Parse untrusted XML through defusedxml before anything else reads it.

    Spec §6.6.  Only *hostile* structure is refused — entity declarations and
    external references.  A malformed file is not a security problem and is
    still rendered, exactly as v2 rendered it, because nothing here depends on
    the parse succeeding.
    """
    try:
        DefusedElementTree.fromstring(path.read_bytes())
    except DefusedXmlException as exc:
        raise ValueError(
            f"{path.name} declares XML entities or external references, which "
            f"WebShot refuses to parse ({type(exc).__name__}; docs/04-spec.md §6.6)."
        ) from exc
    except ParseError:
        pass


def _escapes_extraction_root(member: str) -> bool:
    normalized = member.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return True
    return any(part == ".." for part in normalized.split("/"))


#: How deep `guard_archive` follows archives inside archives before refusing
#: to look further. MarkItDown converts zip members recursively, so the limit
#: it enforces has to be measured recursively too; three levels covers every
#: real nested document (an EPUB inside a zip inside a zip) and stops a
#: quine-shaped input from making the *guard* the expensive part.
MAX_ARCHIVE_DEPTH = 3
#: Suffixes whose members MarkItDown will itself expand.
NESTED_ARCHIVE_SUFFIXES = (".zip", ".epub", ".docx", ".pptx", ".xlsx")


def guard_archive(path: Path) -> None:
    """Refuse zip-shaped inputs that would expand past policy or escape it.

    Read from the central directory, so a decompression bomb is refused
    without decompressing anything.  MarkItDown's zip converter reads members
    into memory rather than onto disk, so a traversing path cannot escape
    *today* — the check is here because "no member may escape the extraction
    root" is a property of the input WebShot accepts, not of the converter it
    currently hands the input to.
    """
    _guard_archive_bytes(path.read_bytes(), path.name, depth=0, budget=[0], seen=[0])


def _guard_archive_bytes(
    data: bytes, name: str, *, depth: int, budget: list[int], seen: list[int]
) -> None:
    """One archive level, charging its members to a budget shared with its parents.

    **Why recursion.** The size and member limits are spec §6.7's, and they are
    limits on what a capture *expands*, not on what the outer central directory
    happens to declare. MarkItDown converts zip members recursively, so an
    outer archive holding a small `nested.zip` that expands to gigabytes passed
    a guard that summed only the compressed inner file and was then decompressed
    by the converter anyway — the advertised limit bypassed by one level of
    indirection (docs/09 P8-16). `budget` and `seen` are single-element lists so
    every level charges the same running totals.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            nested = [
                member
                for member in members
                if not member.is_dir()
                and member.filename.lower().endswith(NESTED_ARCHIVE_SUFFIXES)
            ]
            payloads = (
                [(member, archive.read(member)) for member in nested]
                if depth < MAX_ARCHIVE_DEPTH
                else []
            )
    except zipfile.BadZipFile as exc:
        if depth:
            # A member that is not really an archive is ordinary data; it was
            # already charged to the budget as its own bytes.
            return
        raise ValueError(f"{name} is not a readable archive: {exc}") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{name} could not be inspected: {exc}") from exc

    seen[0] += len(members)
    if seen[0] > MAX_ARCHIVE_MEMBERS:
        raise ValueError(
            f"{name} contains {seen[0]} members, more than the "
            f"{MAX_ARCHIVE_MEMBERS} WebShot will expand."
        )
    budget[0] += sum(member.file_size for member in members)
    if budget[0] > MAX_LOCAL_SOURCE_BYTES:
        raise ValueError(
            f"{name} expands to {budget[0] // (1024 * 1024)} MB, past the "
            f"{MAX_LOCAL_SOURCE_BYTES // (1024 * 1024)} MB safety limit."
        )
    if nested and depth >= MAX_ARCHIVE_DEPTH:
        raise ValueError(
            f"{name} nests archives more than {MAX_ARCHIVE_DEPTH} deep, which "
            "WebShot will not expand."
        )
    for member, payload in payloads:
        _guard_archive_bytes(
            payload,
            f"{name}!{member.filename}",
            depth=depth + 1,
            budget=budget,
            seen=seen,
        )
    for member in members:
        if _escapes_extraction_root(member.filename):
            raise ValueError(
                f"{name} contains a member path that escapes the extraction "
                f"root: {member.filename!r}"
            )
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError(
                f"{name} contains a symbolic link ({member.filename!r}), "
                "which could redirect extraction outside the archive."
            )


def _render_local_file(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    base_href = path.parent.resolve().as_uri() + "/"

    if suffix in IMAGE_SUFFIXES:
        if suffix in XML_SUFFIXES:
            guard_xml(path)
        source = html.escape(path.resolve().as_uri(), quote=True)
        body = f'<figure><img src="{source}" alt="{html.escape(path.stem)}"><figcaption>{html.escape(path.name)}</figcaption></figure>'
        return _document_template(
            path.name, "Image document", body, base_href=base_href
        ), "image"

    source_format = FORMATS.get(suffix)
    if source_format is None:
        raise ValueError(
            f"Unsupported local file type '{suffix or '(none)'}'. Supported: HTML, "
            "Markdown, JSON/JSONL, CSV/TSV, text/log/XML/YAML, EPUB, ZIP, "
            "DOCX/PPTX/XLSX (with the office extra: uv sync --extra office), and common images."
        )
    if suffix in XML_SUFFIXES:
        guard_xml(path)
    if suffix in ARCHIVE_SUFFIXES:
        guard_archive(path)

    fallback_title = path.name
    if source_format.presentation == "native":
        source_markdown = path.read_text(encoding="utf-8", errors="replace")
    else:
        # Imported here, in the one branch that reads through MarkItDown, and
        # not at module scope: markitdown imports magika, and magika imports
        # onnxruntime, a native inference runtime.  The pipeline imports this
        # module for every capture, so a web page, an HTML file, a Markdown
        # file and an image were all loading an ML runtime none of them uses
        # (docs/09 P10-15).  An ImportError raised here is not swallowed:
        # nothing between here and the CLI catches it, and `classify` answers
        # it with 9, as it does for every stage P10-6 deferred.
        from .markitdown_bridge import to_markdown

        converted = to_markdown(path, suffix=suffix, extra=source_format.extra)
        source_markdown = converted.markdown
        # A document that states its own title (an EPUB's `dc:title`) knows it
        # better than its filename does; the body's first heading still wins,
        # because that is what a reader sees at the top of the page.
        fallback_title = converted.title or fallback_title
        if source_format.presentation == "verbatim":
            source_markdown = _as_code_block(source_markdown)
        if source_format.section:
            source_markdown = f"## {source_format.section}\n\n{source_markdown}"

    return _render_markdown_document(
        source_markdown,
        fallback_title=fallback_title,
        label=source_format.label,
        base_href=base_href,
    ), source_format.kind


def materialize_source(source: str) -> MaterializedSource:
    parsed = urlparse(source)
    if parsed.scheme != "file":
        return MaterializedSource(
            browser_url=source, original_source=source, kind="web"
        )

    path = file_url_path(source).resolve()
    if not path.is_file():
        raise ValueError(f"Local source does not exist: {path}")
    if path.stat().st_size > MAX_LOCAL_SOURCE_BYTES:
        raise ValueError(
            f"Local source is larger than the {MAX_LOCAL_SOURCE_BYTES // (1024 * 1024)} MB safety limit: {path}"
        )
    if path.suffix.lower() in HTML_SUFFIXES:
        return MaterializedSource(
            browser_url=source,
            original_source=source,
            kind="html",
            original_path=path,
        )

    rendered, kind = _render_local_file(path)
    temporary_directory = tempfile.TemporaryDirectory(prefix="webshot-source-")
    generated = Path(temporary_directory.name) / "index.html"
    generated.write_text(rendered, encoding="utf-8", newline="\n")
    return MaterializedSource(
        browser_url=generated.as_uri(),
        original_source=source,
        kind=kind,
        original_path=path,
        temporary_directory=temporary_directory,
    )
