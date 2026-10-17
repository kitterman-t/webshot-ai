"""The docling bridge: sanitized snapshot HTML in, WebShot's own types out.

This is the ONLY module allowed to import docling (CONTRIBUTING rule 1) — a
`DoclingDocument` never crosses this file's boundary.  Everything downstream
receives plain strings, grids, and `ChunkSeed` records.

Three upstream facts shape this file, all verified against the pinned
versions rather than assumed (docs/09 S3 and the Phase 2 corrections):

- The top-level `DocumentConverter` eagerly imports the PDF backend and
  crashes on slim installs, so the conversion drives `HTMLDocumentBackend`
  directly (docs/09 S3 — mandated in docs/02).
- The backend emits a `title` item for the document's `<title>` tag as well as
  for `<h1>`.  The bridge feeds it a title-less copy of the snapshot, so the
  published document has one title per heading that actually appeared on the
  page.  docling-slim 2.121 also dropped `<table><caption>` text entirely;
  2.130 keeps it.  The bridge still attaches any caption the backend did not,
  and warns only about a caption that reached neither (docs/09 P10-19), so no
  caption vanishes silently and none is reported lost when it was kept.
- The backend does not record image URIs on `PictureItem`s, so pictures are
  matched to harvested assets by document order, with a hard count check —
  a mismatch raises rather than mis-attributing OCR text.
- The backend strips every text node and keeps no record of where the source
  had whitespace, and docling-core's serializers join the parts of an inline
  group with a space, so "H<sub>0</sub>" reached every text surface as "H 0"
  and "un<b>break</b>able" as "un break able". Where a formatted part touches
  the text beside it is carried through the backend as two marker characters
  (docs/09 P14-56, P48).
- The backend reads a `<pre>` in a list item into the item's line with its
  whitespace joined, and docling-core indents only the first line of a list
  item's child, so an item's code reached content.md as one line. The bridge
  extends the backend to read it as a block of code under the item, and the
  serializers to write each of its lines inside the item (docs/09 P50).
- The backend files a `<footer>`'s content on the furniture layer, then puts
  a heading inside one, and the rest of that footer, on the body layer. The
  conversion runs through a subclass that files everything the backend made
  inside a footer on the furniture layer (docs/09 P65).
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import cache
from html.parser import HTMLParser
from io import BytesIO
from typing import TYPE_CHECKING, Any, TypeVar

from ..bundle.publish import dump_json
from ..capture.snapshot import DISPLAY_ATTRIBUTE, INLINE_TAGS, TITLE_ELEMENT_RE
from ..capture.visuals import VisualAsset
from ..errors import BundleBuildError

if TYPE_CHECKING:
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer

_Item = TypeVar("_Item")
_Fact = TypeVar("_Fact")
_Serializer = TypeVar("_Serializer", bound="MarkdownDocSerializer")


@dataclass(frozen=True, slots=True)
class ChunkSeed:
    """One chunker output, before size-splitting: WebShot's type, not docling's."""

    text: str
    headings: tuple[str, ...]
    doc_items: tuple[str, ...]
    kind: str  # "text" | "table" | "figure"


@dataclass(slots=True)
class ExtractionResult:
    """Everything EXTRACT hands the bundle writer, in plain types."""

    document_json: str
    markdown: str
    text: str
    doctags: str
    tables: list[list[list[str]]] = field(default_factory=list)
    chunk_seeds: list[ChunkSeed] = field(default_factory=list)
    item_count: int = 0
    picture_count: int = 0
    #: The harvested assets attached to a picture in the document's body, whose
    #: OCR text therefore reached content.md, content.txt and chunks.jsonl.
    #: Empty when the count check skipped attachment, which `warnings` then
    #: says.
    pictured_asset_ids: list[str] = field(default_factory=list)
    #: The harvested assets attached to a picture kept as page chrome (a logo
    #: in <header>), which no serializer writes: their OCR text reached
    #: content.json and their figure chunk in chunks.jsonl, and neither
    #: content.md nor content.txt (docs/09 P57).
    chrome_pictured_asset_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CHROME_TAGS = frozenset({"header", "nav", "footer", "aside"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


@dataclass(frozen=True, slots=True)
class _Position:
    """Where one `<img>` or `<table>` stands in the snapshot."""

    #: Inside header/nav/footer/aside.
    chrome: bool
    #: Where the backend files content as furniture: above the page's first
    #: heading outside a table, or inside a <footer>.
    furniture: bool


#: The provenance of the picture annotation that carries an asset's
#: recognized text in `content.json` (docs/04-spec.md §5.6, §5.13).
OCR_PROVENANCE = "webshot-ocr"

#: The confidence figure in `ocr_reading`'s opening line, as group 2. It is
#: the recognizer's measurement, so the golden harness masks it where it masks
#: recognized text. No anchors and no newline, quote or backslash inside, so
#: it matches inside a JSON string in `chunks.jsonl` as well.
OCR_CONFIDENCE_IN_READING_RE = re.compile(
    r"(\[OCR text from [^\]\n\"\\]*?, mean confidence )(\d+)(%:\])"
)


def ocr_reading(
    text: str, *, asset_id: str, engine: str | None, confidence: float | None
) -> str:
    """Recognized text as the reading surfaces carry it: marked, with its measure.

    docs/11 principle 7: machine-recognized text is labeled as OCR in the
    artifacts, with its confidence. It reached `content.md`, `content.txt` and
    the chunks bare, so a reader could not tell it from the page's own words:
    one mirrored Wikipedia page's `content.md` opened with about 130 lines
    read out of a mockup image (docs/09 P20-4). The closing line says where
    the recognized text ends, which nothing else in `content.txt` or a chunk
    would.
    """
    by = f" by {engine}" if engine else ""
    measured = (
        f"mean confidence {confidence:.0f}%"
        if confidence is not None
        else "no confidence reported"
    )
    return (
        f"[OCR text from {asset_id}, machine-recognized{by}, {measured}:]\n"
        f"{text}\n[End of OCR text from {asset_id}]"
    )


class _SnapshotFacts(HTMLParser):
    """Document-order facts the backend does not preserve: which `<img>` is
    which harvested asset, what each `<table>`'s caption said, and which text,
    images and tables belong to page chrome (header/nav/footer/aside) — the
    one kind of content that legitimately lands on the furniture layer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_asset_ids: list[str | None] = []
        #: Where each `<img>` stands, in the same order.
        self.image_positions: list[_Position] = []
        self.table_captions: list[str] = []
        #: Where each `<table>` stands, in the same order.
        self.table_positions: list[_Position] = []
        #: One entry per top-level chrome region: its whole-space-normalized
        #: text, in reading order.
        self.chrome_regions: list[str] = []
        self._caption_depth = 0
        self._caption_parts: list[str] = []
        self._chrome_depth = 0
        self._chrome_parts: list[str] = []
        self._footer_depth = 0
        self._table_depth = 0
        #: Past the backend's first heading, the one it starts the body at.
        self._headed = False

    def _position(self) -> _Position:
        return _Position(
            chrome=bool(self._chrome_depth),
            furniture=not self._headed or bool(self._footer_depth),
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.image_asset_ids.append(dict(attrs).get("data-webshot-asset-id"))
            self.image_positions.append(self._position())
        elif tag == "table":
            self.table_captions.append("")
            self.table_positions.append(self._position())
            self._table_depth += 1
        elif tag == "caption" and self.table_captions:
            self._caption_depth += 1
            self._caption_parts = []
        elif tag in _HEADING_TAGS and not self._table_depth:
            self._headed = True
        elif tag == "footer":
            self._footer_depth += 1
        if tag in _CHROME_TAGS:
            if not self._chrome_depth:
                self._chrome_parts = []
            self._chrome_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
        elif tag == "footer" and self._footer_depth:
            self._footer_depth -= 1
        if tag == "caption" and self._caption_depth:
            self._caption_depth -= 1
            self.table_captions[-1] = " ".join(" ".join(self._caption_parts).split())
        if tag in _CHROME_TAGS and self._chrome_depth:
            self._chrome_depth -= 1
            if not self._chrome_depth:
                self.chrome_regions.append(" ".join(self._chrome_parts))

    def handle_data(self, data: str) -> None:
        if self._caption_depth:
            self._caption_parts.append(data)
        if self._chrome_depth and data.strip():
            self._chrome_parts.extend(data.split())

    def is_chrome_text(self, text: str) -> bool:
        """Whether `text` reads out of one chrome region.

        Substring containment against each region's normalized text — not a
        pooled word bag, which would misfile a lede whose every word happens
        to appear *somewhere* across a large page's chrome.  Only ever asked
        about items the backend already classified as furniture, so a false
        positive keeps a furniture item on the furniture layer.
        """
        normalized = " ".join(text.split())
        return bool(normalized) and any(
            normalized in region for region in self.chrome_regions
        )


#: Two Unicode noncharacters, which the standard keeps for a program's internal
#: use: the first opens a formatted part's text that touches the text before
#: it, the second closes one that touches the text after it.
JOINED_BEFORE = "\ufdd0"
JOINED_AFTER = "\ufdd1"
_JOIN_MARKS = (JOINED_BEFORE, JOINED_AFTER)
#: A marker as a character reference, which the backend's parser reads as the
#: character and `html.unescape` drops, as it drops every noncharacter.
_JOIN_MARK_REF_RE = re.compile(
    r"&#[xX]0*[fF][dD][dD][01](?![0-9a-fA-F])|&#0*6497[67](?![0-9])"
)
#: A marker with the space the backend put beside it, which the source never
#: had: a marker is written only where no whitespace separated the two.
_JOIN_MARK_RE = re.compile(f" ?{JOINED_BEFORE}|{JOINED_AFTER} ?")

#: The backend method `extract` overrides to keep a footer's content in the
#: footer. It is private to docling, so `extract` checks it is still there.
_FOOTER_HOOK = "_use_footer"
#: A `<footer>` start tag, to say whether a page has one.
_FOOTER_TAG_RE = re.compile(r"<footer[\s>]", re.IGNORECASE)

#: Stands in for a part's text, to read what a serializer wraps text in.
_WRAPPER_PROBE = "\x00"

#: The elements whose text the backend gives a formatting or a link of its
#: own: its `_FORMAT_TAG_MAP` (emphasis, strong, strikethrough, underline,
#: sub/superscript, inline code) and `a`. Their text becomes an item apart
#: from the text beside it, which every serializer joins to the next with a
#: space, so "un<b>break</b>able" read "un **break** able" (docs/09 P48).
_JOINED_TAGS = frozenset(
    {
        "a",
        "b",
        "code",
        "del",
        "em",
        "i",
        "ins",
        "kbd",
        "s",
        "samp",
        "strong",
        "sub",
        "sup",
        "u",
        "var",
    }
)
#: The one element of `INLINE_TAGS` that has no end tag.
_VOID_INLINE_TAGS = frozenset({"wbr"})


@dataclass(slots=True)
class _OpenInline:
    """An inline element the parse is inside of."""

    tag: str
    #: Laid out as a box of its own, so its edges separate (`DISPLAY_ATTRIBUTE`).
    box: bool
    #: The character the element follows; "" where nothing in its run does.
    before: str = ""
    #: Where its text starts, with which character, once it has any.
    first: tuple[int, str] | None = None


class _InlineJoins(HTMLParser):
    """Where each formatted element touches the text beside it, as insertions.

    An element of `_JOINED_TAGS` whose text starts with no whitespace between
    it and the text before it gets `JOINED_BEFORE` at the start of its text;
    one whose text ends with no whitespace before the text after it gets
    `JOINED_AFTER` at the end. Offsets are into the parsed string, and a
    marker goes inside the element's innermost text, never into a text node
    of its own, which the backend would make an item of.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self.marks: set[tuple[int, str]] = set()
        self._last = ""  # the run's last character so far; "" after a separator
        self._open: list[_OpenInline] = []
        self._text_open = False
        self._text_end = 0
        #: Where a closed element's text ended, until the next character says
        #: whether the element touches it.
        self._after_at: int | None = None

    def _event(self) -> int:
        line, column = self.getpos()
        offset = self._line_starts[line - 1] + column
        if self._text_open:
            # Text runs up to the next event, so this is where it ended.
            self._text_end = offset
            self._text_open = False
        return offset

    def _separate(self) -> None:
        self._last = ""
        self._after_at = None
        # An element with no text yet starts after the separator, not after
        # what came before it: `foo<b><br>bar</b>` is two lines.
        for element in self._open:
            if element.first is None:
                element.before = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._event()
        if tag not in INLINE_TAGS:
            self._separate()
            return
        if tag in _VOID_INLINE_TAGS:
            return
        box = any(name == DISPLAY_ATTRIBUTE for name, _ in attrs)
        if box:
            self._separate()
        self._open.append(_OpenInline(tag, box, before=self._last))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._event()
        if tag not in INLINE_TAGS:
            self._separate()

    def handle_endtag(self, tag: str) -> None:
        self._event()
        if tag not in INLINE_TAGS:
            self._separate()
            return
        depth = next(
            (
                index
                for index in range(len(self._open) - 1, -1, -1)
                if self._open[index].tag == tag
            ),
            None,
        )
        if depth is None:
            return
        element = self._open[depth]
        del self._open[depth:]
        if element.box:
            self._separate()
            return
        if tag not in _JOINED_TAGS or element.first is None:
            return
        offset, character = element.first
        if element.before and not element.before.isspace() and not character.isspace():
            self.marks.add((offset, JOINED_BEFORE))
        if self._last and not self._last.isspace():
            self._after_at = self._text_end

    def handle_data(self, data: str) -> None:
        offset = self._event()
        if not data:
            return
        if self._after_at is not None:
            if not data[0].isspace():
                self.marks.add((self._after_at, JOINED_AFTER))
            self._after_at = None
        for element in self._open:
            if element.first is None:
                element.first = (offset, data[0])
        self._text_open = True
        self._last = data[-1]

    def handle_comment(self, data: str) -> None:
        self._event()


def mark_inline_joins(html: str) -> str:
    """`html` with each formatted element's touching edges marked for the backend.

    The backend normalizes every text node with `" ".join(text.split())`,
    which drops the one fact the serializers would need: whether "un" and its
    bold "break" had whitespace between them. Characters survive it, so the
    fact travels as one, and `extract` removes every marker once the document
    is built. A marked page opens with a byte-order mark: given bytes with no
    declared encoding, the backend's charset detection read the markers' bytes
    as cp775 or cp949 and garbled the text around them.
    """
    parser = _InlineJoins(html)
    parser.feed(html)
    parser.close()
    if not parser.marks:
        return html
    # Assembled once: inserting each marker by slicing the whole page copied
    # it once per marker, and a page has a marker for nearly every link
    # followed by a comma (docs/09 P48).
    pieces = ["\ufeff"]
    start = 0
    for offset, mark in sorted(parser.marks):
        pieces += (html[start:offset], mark)
        start = offset
    pieces.append(html[start:])
    return "".join(pieces)


def _marked(text: str) -> bool:
    return JOINED_BEFORE in text or JOINED_AFTER in text


def unmark_joins(text: str) -> str:
    """`text` with each join marker, and the space the backend put beside it, removed."""
    return _JOIN_MARK_RE.sub("", text)


def _aligned(
    items: Sequence[_Item], facts: Sequence[_Fact]
) -> list[tuple[_Item, _Fact]] | None:
    """Pair backend items with snapshot-parser facts by document order.

    Both attachment passes (asset OCR to pictures, captions to tables) rest on
    the two parsers agreeing about element count and order — the backend
    records no join key.  The backend only ever *skips or merges* elements the
    snapshot parser counts (nested tables flatten, an image inside `<pre>`
    yields no picture), so equal counts prove the sets match and the pairing
    is sound; unequal counts mean any pairing would be a guess.  Guessing
    would silently mis-attribute OCR text (risk R16), and failing the whole
    capture over an attachment is disproportionate — so a mismatch returns
    None, the caller skips the attachment, and the degradation surfaces as a
    manifest warning per the failure philosophy (docs/02): the full records
    still live in assets.json.
    """
    if len(items) != len(facts):
        return None
    return list(zip(items, facts, strict=True))


def _outside_chrome(
    items: Sequence[_Item], positions: Sequence[_Position]
) -> list[_Item] | None:
    """The items whose snapshot element stands outside page chrome.

    Paired by document order, as `_aligned` pairs them. When the counts
    disagree, every furniture item still came from an element where the
    backend files furniture (it only skips or merges), so if those elements
    are all chrome no furniture item is content, and if none is chrome every
    one is. Elements of both kinds there leave no answer, and None says so.
    """
    aligned = _aligned(items, positions)
    if aligned is not None:
        return [item for item, position in aligned if not position.chrome]
    filed = [position.chrome for position in positions if position.furniture]
    if filed and all(filed):
        return []
    if filed and not any(filed):
        return list(items)
    return None


def _captions_missing(snapshot: Sequence[str], carried: Sequence[str]) -> list[str]:
    """The snapshot's table captions that no table in the document carries.

    Compared as whitespace-collapsed text and as a multiset, so two tables
    captioned alike need two carried captions.  An empty caption is a table
    without one, not a caption to find.
    """
    remaining = [" ".join(text.split()) for text in carried]
    missing: list[str] = []
    for caption in snapshot:
        wanted = " ".join(caption.split())
        if not wanted:
            continue
        if wanted in remaining:
            remaining.remove(wanted)
        else:
            missing.append(caption)
    return missing


@cache
def _grid_built_once() -> Any:
    """docling-core's `TableData`, with `grid` the one built up front.

    Defined on first use, so importing the bridge still imports no docling.
    """
    from docling_core.types.doc.document import TableData

    class GridBuiltOnce(TableData):
        # pydantic makes an underscore attribute private: never a field, so
        # never validated or dumped. Set as each copy is made.
        _grid: list[Any]

        @property
        def grid(self) -> list[Any]:
            return self._grid

    return GridBuiltOnce


@contextmanager
def _grids_built_once(tables: Sequence[Any]) -> Iterator[None]:
    """Each table's grid built once, not once per cell, inside the block.

    `TableData.grid` is a computed property that builds the whole grid on
    every read, and docling-core's `TableItem.export_to_otsl`, which writes
    each table into `export_to_doctags()`, reads `self.data.grid[i][j]` for
    every cell. A table of n cells therefore built n grids and n² cells: a
    1,000-row, 2-column CSV built four million, and the time a capture took
    grew with the square of its rows (docs/09 P41). The grid is a function of
    the cells and the table's size, which an export does not change, so each
    table's `data` is swapped for a copy that shares its cells and answers
    every read with one grid, and put back on the way out. A `data` of any
    other type than `TableData` is left as it is, since the copy would drop
    whatever it adds.
    """
    from docling_core.types.doc.document import TableData

    built_once = _grid_built_once()
    swapped: list[tuple[Any, Any]] = []
    try:
        for table in tables:
            data = table.data
            if type(data) is not TableData:
                continue
            copy = built_once.model_construct(
                _fields_set=data.model_fields_set, **dict(data)
            )
            copy._grid = data.grid
            table.data = copy
            swapped.append((table, data))
        yield
    finally:
        for table, data in swapped:
            table.data = data


#: The elements the backend makes a list item of and then walks for nested
#: content: a list item and a description. A term (`<dt>`) it does not walk,
#: so a term's code stays in its line.
_CODE_ITEM_TAGS = frozenset({"li", "dd"})
#: An element between an item and its `<pre>` that the backend reads as a
#: block of its own, so the code is that block's, not the item's.
_CODE_HOLDING_BLOCKS = frozenset({"ul", "ol", "dl", "table", "pre"})
#: What the backend gives an item of its own when it walks a list item's
#: nested content, and makes nothing of inside a `<pre>` it reads as code.
_NESTED_CONTENT_TAGS = ["img", "ul", "ol", "dl", "table"]
#: A line ending, as CommonMark reads one: a carriage return on its own is one.
_LINE_END_RE = re.compile(r"\r\n|\r|\n")
#: The backend methods `_code_block_backend` overrides or calls. They are
#: private to docling, so `extract` checks they are still there, with
#: `AnnotatedTextList.simplify_text_elements`, which it calls too.
_BACKEND_HOOKS = (
    "_add_list_item_with_content",
    "_extract_text_and_hyperlink_recursively",
    "_handle_block",
    "_is_suppressed_tag",
    "_make_text_prov",
    "_process_nested_element",
)


def _backend_hooked() -> bool:
    """Whether docling's backend still has everything `_code_block_backend` uses."""
    from docling.backend import html_backend

    return all(
        hasattr(html_backend.HTMLDocumentBackend, hook) for hook in _BACKEND_HOOKS
    ) and hasattr(
        getattr(html_backend, "AnnotatedTextList", None), "simplify_text_elements"
    )


@cache
def _code_block_backend() -> type[Any]:
    """docling's HTML backend, reading a list item's `<pre>` as a block of code.

    The backend reads a list item's text from everything the item holds but
    its lists and tables, and joins each run of whitespace in it. A `<pre>`
    there was a `code` part of the item's line, its lines joined into one, and
    the code of an item that opened with it was the item's own text, no longer
    code (docs/09 P50). This backend reads that `<pre>` as it reads one outside
    a list, a `code` item holding its lines, and makes it a child of the item,
    walked in the order the item holds it, as the item's lists are. An item
    whose only text was its code becomes an item with no text that holds the
    code.
    """
    from docling.backend.html_backend import AnnotatedTextList, HTMLDocumentBackend

    class _CodeBlockBackend(HTMLDocumentBackend):
        #: The `<pre>` elements, by id, of the item whose own text is being read.
        _reading: frozenset[int] = frozenset()

        def _is_code_block(self, pre: Any, item: Any) -> bool:
            """Whether `item` holds `pre` as a block of code of its own.

            A list or table between the two reads the `<pre>` as its own
            content, and a `<pre>` the backend hides, or that is in an element
            it hides, gave the item no text, so it stays out. So does one that
            holds an image, a list or a table, which walking the item made an
            item of its own, and one the backend reads as several runs, which
            formatting in the code makes: those it writes inline, their lines
            joined, outside a list too, and in the item's line they read as
            they did.
            """
            node = pre
            while node is not None and node is not item:
                if self._is_suppressed_tag(node) or (
                    node is not pre and node.name in _CODE_HOLDING_BLOCKS
                ):
                    return False
                node = node.parent
            if node is not item:
                return False
            # An image, list or table in the `<pre>` the item's nested content
            # gave its own item, which `_handle_block` would drop.
            if pre.find(_NESTED_CONTENT_TAGS) is not None:
                return False
            # As `_handle_block` reads a `<pre>`.
            runs = self._extract_text_and_hyperlink_recursively(
                pre, find_parent_annotation=True, keep_newlines=True
            ).simplify_text_elements()
            return len(runs) == 1 and bool(runs[0].text.strip())

        def _add_list_item_with_content(
            self,
            tag: Any,
            doc: Any,
            parent: Any,
            enumerated: bool = False,
            marker: str = "",
            extra_formatting: Any = None,
        ) -> Any:
            code = (
                [pre for pre in tag.find_all("pre") if self._is_code_block(pre, tag)]
                if tag.name in _CODE_ITEM_TAGS
                else []
            )
            self._reading = frozenset(map(id, code))
            try:
                item = super()._add_list_item_with_content(
                    tag=tag,
                    doc=doc,
                    parent=parent,
                    enumerated=enumerated,
                    marker=marker,
                    extra_formatting=extra_formatting,
                )
            finally:
                self._reading = frozenset()
            if item is None and code:
                # The code was all the item's text. The backend makes no item
                # of one with none, so its code would go with it.
                item = doc.add_list_item(
                    text="",
                    enumerated=enumerated,
                    marker=marker,
                    parent=parent,
                    content_layer=self.content_layer,
                    prov=self._make_text_prov(text="", tag=tag),
                )
            return item

        def _extract_text_and_hyperlink_recursively(
            self,
            item: Any,
            ignore_list: bool = False,
            find_parent_annotation: bool = False,
            keep_newlines: bool = False,
        ) -> Any:
            # `ignore_list` is how the backend reads an item's own text.
            if ignore_list and id(item) in self._reading:
                return AnnotatedTextList()
            return super()._extract_text_and_hyperlink_recursively(
                item,
                ignore_list=ignore_list,
                find_parent_annotation=find_parent_annotation,
                keep_newlines=keep_newlines,
            )

        def _process_nested_element(
            self, elem: Any, li: Any, doc: Any, processed_elements: set[int]
        ) -> None:
            if (
                getattr(elem, "name", None) == "pre"
                and id(elem) not in processed_elements
                and li.name in _CODE_ITEM_TAGS
                and self._is_code_block(elem, li)
            ):
                processed_elements.add(id(elem))
                # Under the item, as the backend reads a `<pre>` anywhere else.
                self._handle_block(elem, doc)
                return
            super()._process_nested_element(elem, li, doc, processed_elements)

    return _CodeBlockBackend


@cache
def _code_block_serializers() -> tuple[type[Any], type[Any]]:
    """docling's Markdown and plain-text serializers for a text, writing a list
    item's block of code inside the item.

    docling-core writes each child of a list item on a line of its own and
    indents only the first line of each, to the item's depth. A block of code
    under an item had its fence there and every other line at the margin, so
    at depth 1 the fence ended the list (docs/09 P50). These write every line
    of it at the item's content column: the column docling indents the item's
    nested list to, or past the item's marker where that is wider, as
    CommonMark needs. A blank line in the code stays empty, and a carriage
    return is a line break, as CommonMark reads one. The fence is three
    backticks, or one more than the code's longest run of them, so no line of
    the code closes it. An item that has no text of its own and opens with its
    code writes the code's first line on its marker's line, `-   ````: a
    marker alone on a line is an empty item, which under an item's text reads
    as a setext underline. In a table cell, which docling writes on one line,
    an item's code is inline code on the item's line, its whitespace joined,
    as the backend wrote it there before.
    """
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import (
        MarkdownParams,
        MarkdownTextSerializer,
    )
    from docling_core.transforms.serializer.plain_text import PlainTextTextSerializer
    from docling_core.types.doc.document import CodeItem, ListGroup, ListItem

    def _depth(item: Any, doc: Any) -> int:
        """How many lists `item` is in, which is how deep docling indents it."""
        depth = 0
        node = item
        while node.parent is not None:
            node = node.parent.resolve(doc)
            depth += isinstance(node, ListGroup)
        return depth

    def _codes(item: Any, doc: Any) -> list[Any]:
        return [
            child
            for child in (ref.resolve(doc) for ref in item.children)
            if isinstance(child, CodeItem)
        ]

    class _CodeBlocks:
        def serialize(
            self,
            *,
            item: Any,
            doc_serializer: Any,
            doc: Any,
            is_inline_scope: bool = False,
            visited: set[str] | None = None,
            **kwargs: Any,
        ) -> Any:
            written = None
            if not is_inline_scope and isinstance(item, CodeItem | ListItem):
                written = self._in_item(item, doc_serializer, doc, visited, kwargs)
            if written is not None:
                return written
            return self._docling(
                item, doc_serializer, doc, is_inline_scope, visited, kwargs
            )

        def _docling(
            self,
            item: Any,
            doc_serializer: Any,
            doc: Any,
            is_inline_scope: bool,
            visited: set[str] | None,
            kwargs: dict[str, Any],
        ) -> Any:
            return super().serialize(  # type: ignore[misc]
                item=item,
                doc_serializer=doc_serializer,
                doc=doc,
                is_inline_scope=is_inline_scope,
                visited=visited,
                **kwargs,
            )

        def _in_item(
            self,
            item: Any,
            doc_serializer: Any,
            doc: Any,
            visited: set[str] | None,
            kwargs: dict[str, Any],
        ) -> Any:
            """`item` written with the code of its list item, or None to leave it."""
            args = (doc_serializer, doc)
            if kwargs.get("in_table_cell"):
                # Each code on the item's line, as inline code.
                if not isinstance(item, ListItem) or visited is None:
                    return None
                codes = [
                    code for code in _codes(item, doc) if code.self_ref not in visited
                ]
                if not codes:
                    return None
                line = self._docling(item, *args, False, visited, kwargs)
                inline = [
                    " ".join(
                        self._docling(code, *args, True, set(), kwargs).text.split()
                    )
                    for code in codes
                ]
                visited.update(code.self_ref for code in codes)
                return create_ser_result(
                    text=" ".join([line.text.rstrip(), *inline]),
                    span_source=[
                        line,
                        *(create_ser_result(span_source=code) for code in codes),
                    ],
                )
            if isinstance(item, CodeItem):
                owner = item.parent.resolve(doc) if item.parent else None
                if not isinstance(owner, ListItem):
                    return None
                params = MarkdownParams(**kwargs)
                column = self._column(owner, *args, params, kwargs)
                return create_ser_result(
                    text=_indented(
                        self._lines(item, *args, params, kwargs), column, " " * column
                    ),
                    span_source=item,
                )
            if item.text or not item.children or visited is None:
                return None
            code = item.children[0].resolve(doc)
            if not isinstance(code, CodeItem) or code.self_ref in visited:
                return None
            params = MarkdownParams(**kwargs)
            line = self._docling(item, *args, False, visited, kwargs)
            column = self._column(item, *args, params, kwargs)
            # The list writes the item's indentation before its first line.
            indent = (_depth(item, doc) - 1) * params.indent
            visited.add(code.self_ref)
            text = _indented(
                self._lines(code, *args, params, kwargs),
                column,
                line.text.strip().ljust(column - indent),
            )
            return create_ser_result(
                text=text,
                span_source=[line, create_ser_result(span_source=code)],
            )

        def _lines(
            self,
            code: Any,
            doc_serializer: Any,
            doc: Any,
            params: Any,
            kwargs: dict[str, Any],
        ) -> list[str]:
            """The lines of `code` as a block, before its item's indentation."""
            if params.include_hyperlinks and code.hyperlink:
                # docling writes a linked code item as the link's text, inline.
                text = self._docling(
                    code, doc_serializer, doc, False, set(), kwargs
                ).text
                return _LINE_END_RE.split(text)
            lines = _LINE_END_RE.split(code.text)
            if not params.format_code_blocks:
                return lines
            longest = max((len(run) for run in re.findall("`+", code.text)), default=0)
            fence = "`" * max(3, longest + 1)
            return [fence, *lines, fence]

        def _column(
            self,
            item: Any,
            doc_serializer: Any,
            doc: Any,
            params: Any,
            kwargs: dict[str, Any],
        ) -> int:
            """The column list item `item`'s nested content starts at."""
            line = self._docling(item, doc_serializer, doc, False, set(), kwargs)
            marker = line.text.split(" ", 1)[0]
            depth = _depth(item, doc)
            return int(
                max(
                    depth * params.indent, (depth - 1) * params.indent + len(marker) + 1
                )
            )

    def _indented(lines: list[str], column: int, first: str) -> str:
        """`lines` at `column`, the first after `first` in place of the indentation."""
        pad = " " * column
        return "\n".join(
            [
                first + lines[0] if lines[0] else first.rstrip(),
                *(pad + line if line else "" for line in lines[1:]),
            ]
        )

    class _MarkdownText(_CodeBlocks, MarkdownTextSerializer):
        pass

    class _PlainText(_CodeBlocks, PlainTextTextSerializer):
        pass

    return _MarkdownText, _PlainText


def extract(sanitized_html: str, assets: list[VisualAsset]) -> ExtractionResult:
    """Sanitized snapshot -> DoclingDocument -> serialized artifacts and seeds."""
    from docling.backend.html_backend import HTMLDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument
    from docling_core.transforms.chunker.hierarchical_chunker import (
        ChunkingDocSerializer,
        ChunkingSerializerProvider,
        HierarchicalChunker,
    )
    from docling_core.transforms.serializer.base import SerializationResult
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import (
        MarkdownAnnotationSerializer,
        MarkdownDocSerializer,
        MarkdownInlineSerializer,
        MarkdownTableSerializer,
    )
    from docling_core.transforms.serializer.plain_text import PlainTextDocSerializer
    from docling_core.types.doc.document import (
        CodeItem,
        ContentLayer,
        DocItem,
        DoclingDocument,
        Formatting,
        InlineGroup,
        ListItem,
        NodeItem,
        PictureDescriptionData,
        PictureMiscData,
        Script,
        TextItem,
    )
    from docling_core.types.doc.labels import DocItemLabel

    extraction_warnings: list[str] = []

    def _item_lists(doc: DoclingDocument) -> list[tuple[str, list[Any]]]:
        """Each of the document's lists of items (texts, tables, groups, ...)."""
        return [
            (name, value)
            for name, value in vars(doc).items()
            if isinstance(value, list)
        ]

    # The <title> tag would become a second, metadata-derived title item; the
    # page's own headings are the record. The element's shape is owned by
    # standalone_document, which defines the regex next to itself (the tag is
    # ours — body markup cannot contain <title>, it is not in the sanitizer's
    # allowlist).
    docling_html = TITLE_ELEMENT_RE.sub("", sanitized_html, count=1)
    # A page that carries the markers itself would have its own characters
    # read as joins and removed, so it keeps the backend's spacing instead.
    # It carries one written as a reference too: the backend reads `&#xFDD0;`
    # as the character (docs/09 P54).
    markable = not any(
        mark in docling_html for mark in _JOIN_MARKS
    ) and not _JOIN_MARK_REF_RE.search(docling_html)
    if not markable:
        extraction_warnings.append(
            "the page contains U+FDD0 or U+FDD1, the characters WebShot uses to "
            "keep formatted text (a link, emphasis, inline code, a subscript or "
            "superscript) joined to the text it touches, so content.md, "
            "content.txt and chunks.jsonl separate each such part from its "
            "neighbours with a space (docs/09 P14-56, P48)."
        )
    # The methods the backend's subclass relies on are private to docling.
    # Without one, the page is read by docling's own backend, an item's code
    # into the item's line, and the record says so.
    hooked = _backend_hooked()
    base = _code_block_backend() if hooked else HTMLDocumentBackend
    if not hooked:
        extraction_warnings.append(
            "docling's HTML backend no longer has the methods WebShot extends to "
            "read a list item's <pre> as a block of code, so content.md, "
            "content.txt and chunks.jsonl hold each such block on one line, in "
            "its item's text (docs/09 P50)."
        )
    # Every item the backend made inside a <footer>, which stays on the
    # furniture layer whatever its text.
    in_footer: set[str] = set()

    # Built on whichever backend reads the page, which mypy cannot follow.
    class _FooterBackend(base):  # type: ignore[misc,valid-type]
        """docling's HTML backend, with everything inside a <footer> kept there.

        The backend files a footer's content on the furniture layer, but its
        heading handler sets the body layer at every heading, inside a footer
        as anywhere, until the footer ends: a site footer's column headings
        and all that followed them reached content.md, content.txt and
        chunks.jsonl (docs/09 P65). The tree cannot say where a footer ends:
        a heading in a footer's table cell or <details> parents nothing after
        it, and content after a footer can hang from a heading group made
        inside it. The backend never deletes an item while it walks the page,
        so what it made inside a footer is what its lists gained meanwhile.
        """

        @contextmanager
        def _use_footer(self, tag: Any, doc: DoclingDocument) -> Iterator[None]:
            made = {name: len(items) for name, items in _item_lists(doc)}
            with super()._use_footer(tag, doc):
                yield
            for name, items in _item_lists(doc):
                for item in items[made.get(name, 0) :]:
                    if isinstance(item, NodeItem):
                        item.content_layer = ContentLayer.FURNITURE
                        in_footer.add(item.self_ref)

    # `_use_footer` is private to docling too. Without it the override never
    # runs, so a page with a footer says what that costs.
    footer_hooked = hasattr(HTMLDocumentBackend, _FOOTER_HOOK)
    backend = _FooterBackend if footer_hooked else base
    if not footer_hooked and _FOOTER_TAG_RE.search(docling_html):
        extraction_warnings.append(
            "docling's HTML backend no longer has the method WebShot extends to "
            "keep a <footer> out of the text, so a heading inside one sends the "
            "rest of that footer to content.md, content.txt and chunks.jsonl "
            "(docs/09 P65)."
        )
    docling_input = docling_html.encode("utf-8")
    try:
        # The input document is the page as given, so `content.json`'s origin
        # hash is the page's; the backend parses the marked copy.
        in_doc = InputDocument(
            path_or_stream=BytesIO(docling_input),
            format=InputFormat.HTML,
            backend=backend,
            filename="content.html",
        )
        parsed = mark_inline_joins(docling_html) if markable else docling_html
        document: DoclingDocument = backend(
            in_doc=in_doc, path_or_stream=BytesIO(parsed.encode("utf-8"))
        ).convert()
    except Exception as exc:
        raise BundleBuildError(f"docling could not parse the snapshot: {exc}") from exc

    # Take the join markers back out before anything reads a text, keeping
    # which items they opened or closed. A marker inside a text, where the
    # backend flattened an element into one string (a heading, a table cell),
    # takes the backend's space with it, which joins those two in place.
    joined_before: set[str] = set()
    joined_after: set[str] = set()
    if markable:
        for item in document.texts:
            if item.text.startswith(JOINED_BEFORE):
                joined_before.add(item.self_ref)
            if item.text.endswith(JOINED_AFTER):
                joined_after.add(item.self_ref)
            item.text = unmark_joins(item.text)
            item.orig = unmark_joins(item.orig)
        for table in document.tables:
            for cell in table.data.table_cells:
                cell.text = unmark_joins(cell.text)

    facts = _SnapshotFacts()
    facts.feed(sanitized_html)

    # The backend files everything before the first heading on the furniture
    # layer, which serializers and the chunker skip. That is right for page
    # chrome and wrong for document content (an adapter page's descriptor, a
    # lede before the h1) — v2 recorded such text and parity holds v3 to it.
    # Content that did not come from header/nav/footer/aside moves back to
    # the body layer. What the backend made inside a footer is known to have
    # come from one, so it stays without a text test.
    for item in document.texts:
        if (
            item.content_layer == ContentLayer.FURNITURE
            and item.self_ref not in in_footer
            and not facts.is_chrome_text(item.text)
        ):
            item.content_layer = ContentLayer.BODY
    # An item with no text that holds its code has no text to tell chrome by,
    # so it stays on its code's layer: moved alone, a footer's item was in the
    # body, and its code written there with it (docs/09 P50).
    for item in document.texts:
        if isinstance(item, ListItem) and not item.text and item.children:
            code = item.children[0].resolve(document)
            if isinstance(code, CodeItem):
                item.content_layer = code.content_layer

    # A table or picture has no text to tell chrome by, so where its element
    # stands in the snapshot decides it: one inside header/nav/footer/aside
    # stays furniture, any other goes back to the body. Left on furniture, a
    # table or picture above the first heading reached content.json and
    # content.doctags and none of content.md, content.txt or chunks.jsonl, and
    # a picture took the OCR text attached to it along (docs/09 P57).
    for kind, items, positions in (
        ("table", document.tables, facts.table_positions),
        ("picture", document.pictures, facts.image_positions),
    ):
        furniture = [
            item for item in items if item.content_layer == ContentLayer.FURNITURE
        ]
        if not furniture:
            continue
        outside = _outside_chrome(items, positions)
        if outside is None:
            extraction_warnings.append(
                f"{len(furniture)} {kind}(s) above the page's first heading or in "
                "a footer may be missing from content.md, content.txt and "
                f"chunks.jsonl: docling produced {len(items)} {kind}(s) for "
                f"{len(positions)} in the snapshot, and some of those docling files "
                "there sit in page chrome (header, nav, footer, aside) and some "
                "do not, so which of them is chrome could not be told by "
                "position, and all were left out as chrome. content.json and "
                "content.doctags hold them (docs/09 P57)."
            )
            continue
        for item in outside:
            item.content_layer = ContentLayer.BODY

    # Attach <table><caption> text the backend did not keep.
    aligned_tables = _aligned(document.tables, facts.table_captions)
    for table, caption in aligned_tables or []:
        if caption and not table.captions:
            caption_item = document.add_text(label=DocItemLabel.CAPTION, text=caption)
            table.captions.append(caption_item.get_ref())
    if aligned_tables is None:
        # The counts disagree, so no caption can be placed by position. That
        # is only a loss for a caption the backend did not keep itself: from
        # docling-slim 2.130 it keeps them, and warning about those would
        # report a caption missing that the document carries (P10-19).
        carried = [
            caption.resolve(document).text
            for table in document.tables
            for caption in table.captions
        ]
        missing = _captions_missing(facts.table_captions, carried)
        if missing:
            extraction_warnings.append(
                f"{len(missing)} table caption(s) could not be attached: docling "
                f"produced {len(document.tables)} table(s) for "
                f"{len(facts.table_captions)} in the snapshot (nested or unusual "
                "tables), so placing them would be a guess. Caption text is "
                "preserved in content.html."
            )

    assets_by_id = {asset.id: asset for asset in assets}
    matched: list[tuple[Any, VisualAsset]] = []
    aligned_pictures = _aligned(document.pictures, facts.image_asset_ids)
    if aligned_pictures is None:
        aligned_pictures = []
        if assets:
            extraction_warnings.append(
                f"OCR annotations and figure chunks were omitted from "
                f"content.json: docling produced {len(document.pictures)} "
                f"picture(s) for {len(facts.image_asset_ids)} snapshot images "
                "(e.g. an image inside a preformatted block), so attaching by "
                "position would risk mis-attribution. The complete OCR records "
                "remain in assets.json."
            )
    for picture, asset_id in aligned_pictures:
        asset = assets_by_id.get(asset_id or "")
        if asset is None:
            continue
        matched.append((picture, asset))
        details = {
            "asset_id": asset.id,
            "file": asset.file,
            "kind": asset.kind,
            "sha256": asset.sha256,
            "alt": asset.alt,
            "caption": asset.caption,
            "nearby_heading": asset.nearby_heading,
            "ocr": {
                "confidence": asset.ocr.confidence,
                "language": asset.ocr.language,
                "engine": asset.ocr.engine,
            },
        }
        with warnings.catch_warnings():
            # docling-core 2.92 deprecates `annotations` in favour of `meta`,
            # but `meta` has no field for descriptive text yet and the
            # ecosystem serializers still read annotations (the markdown
            # export prints them). Recorded as a Phase 2 correction.
            # catch_warnings mutates process-global filter state; running in
            # asyncio.to_thread this can briefly hide an unrelated main-thread
            # DeprecationWarning. Accepted: captures are one-at-a-time by the
            # concurrency model (docs/02), and the window is milliseconds.
            warnings.simplefilter("ignore", DeprecationWarning)
            picture.annotations.append(PictureMiscData(content={"webshot": details}))
            if asset.ocr.text:
                picture.annotations.append(
                    PictureDescriptionData(
                        text=asset.ocr.text, provenance=OCR_PROVENANCE
                    )
                )

    class _MarkedOcr(MarkdownAnnotationSerializer):
        """docling's annotation text, with an asset's recognized text marked.

        `content.json` keeps the annotation as recognized, beside the record
        that holds its confidence; every surface serialized from it marks it
        with `ocr_reading` (docs/09 P20-4).
        """

        def serialize(
            self, *, item: DocItem, doc: DoclingDocument, **kwargs: Any
        ) -> SerializationResult:
            with warnings.catch_warnings():
                # docling-core deprecates `annotations`; see the attach above.
                warnings.simplefilter("ignore", DeprecationWarning)
                annotations = list(item.get_annotations())
                recognized = [
                    annotation
                    for annotation in annotations
                    if isinstance(annotation, PictureDescriptionData)
                    and annotation.provenance == OCR_PROVENANCE
                ]
                if not recognized:
                    return super().serialize(item=item, doc=doc, **kwargs)
                rest = item.model_copy(
                    update={
                        "annotations": [
                            annotation
                            for annotation in annotations
                            if annotation not in recognized
                        ]
                    }
                )
                serialized = super().serialize(item=rest, doc=doc, **kwargs)
            record: dict[str, Any] = next(
                (
                    annotation.content["webshot"]
                    for annotation in annotations
                    if isinstance(annotation, PictureMiscData)
                    and isinstance(annotation.content.get("webshot"), dict)
                ),
                {},
            )
            ocr = record.get("ocr") or {}
            parts = [serialized.text] if serialized.text else []
            parts.extend(
                ocr_reading(
                    annotation.text,
                    asset_id=str(record.get("asset_id") or "an unrecorded asset"),
                    engine=ocr.get("engine"),
                    confidence=ocr.get("confidence"),
                )
                for annotation in recognized
            )
            return create_ser_result(text="\n\n".join(parts), span_source=item)

    def _unpadded(serializer: _Serializer) -> _Serializer:
        """The serializer with its own defaults, and table cells left unpadded.

        docling pads every cell of a Markdown table to its column's widest, so
        one long note in a column widens every row: on a mirrored Wikipedia
        page 71% of `content.md` was spaces, which an agent reads as tokens
        (docs/09 P20-7). `compact_tables` writes the same GFM table, one space
        either side of each cell, and keeps the delimiter row's alignment.
        Copied rather than built from `MarkdownParams()`, because the chunking
        serializer's defaults are not the Markdown one's.
        """
        serializer.params = serializer.params.model_copy(
            update={"compact_tables": True}
        )
        return serializer

    def _wrapper(
        serializer: Any, formatting: Formatting | None, hyperlink: Any
    ) -> tuple[str, str]:
        """What `serializer` writes either side of text with this formatting.

        Read from the serializer itself, so it is "**" for Markdown's bold,
        "[" and "](url)" for a link, and nothing at all for plain text.
        """
        probe = serializer.post_process(
            text=_WRAPPER_PROBE, formatting=formatting, hyperlink=hyperlink
        )
        prefix, found, suffix = str(probe).partition(_WRAPPER_PROBE)
        return (prefix, suffix) if found else ("", "")

    def _touching(left: SerializationResult, right: SerializationResult) -> bool:
        return bool(
            (left.spans and left.spans[-1].item.self_ref in joined_after)
            or (right.spans and right.spans[0].item.self_ref in joined_before)
        )

    def _unwrapped(
        part: SerializationResult, serializer: Any
    ) -> tuple[tuple[Formatting, Any, str], str, bool] | None:
        """A part's wrapping, the text inside it, and whether it is a script.

        The wrapping is its emphasis, its link, and an inline code item's
        backticks. None for a part that is not one plain text or code item,
        which is kept whole.
        """
        source = part.spans[0].item if len(part.spans) == 1 else None
        # A heading, list item or formula is a TextItem too, and none of them
        # is wrapped the way a run of text or inline code is.
        if not isinstance(source, TextItem) or type(source) not in (TextItem, CodeItem):
            return None
        code = isinstance(source, CodeItem)
        ticks = (
            "`"
            if code and getattr(serializer.params, "format_code_blocks", False)
            else ""
        )
        prefix, suffix = _wrapper(serializer, source.formatting, source.hyperlink)
        prefix, suffix = prefix + ticks, ticks + suffix
        text = part.text
        if (
            len(text) < len(prefix) + len(suffix)
            or not text.startswith(prefix)
            or not text.endswith(suffix)
        ):
            return None
        formatting = source.formatting or Formatting()
        return (
            (
                formatting.model_copy(update={"script": Script.BASELINE}),
                source.hyperlink,
                ticks,
            ),
            text[len(prefix) : len(text) - len(suffix)],
            formatting.script != Script.BASELINE,
        )

    def _joined_text(run: list[SerializationResult], serializer: Any) -> str:
        """One run of touching parts as the text it was in the source.

        Each part is unwrapped to the text inside its emphasis, link and
        backticks; a plain sub/superscript then takes the wrapping of the
        text it touches, and each stretch of one wrapping is wrapped once.
        Wrapping each part on its own gives "**(H**0" or "`a``1`", where no
        reader sees one word. A part with a wrapping of its own keeps it, and
        meets its neighbours with nothing between: "un**break**able".
        """
        if len(run) == 1:
            whole: str = run[0].text
            return whole
        pieces = [(_unwrapped(part, serializer), part.text) for part in run]
        wrappings = [found[0] if found else None for found, _ in pieces]
        plain = (Formatting(), None, "")
        for index, (found, _) in enumerate(pieces):
            # Only a plain script takes its neighbour's wrapping. One with a
            # link or emphasis of its own keeps it: a footnote marker
            # `word<sup><a href="#fn1">1</a></sup>` stays a link.
            if found and found[2] and found[0] == plain:
                before = wrappings[index - 1] if index else None
                after = wrappings[index + 1] if index + 1 < len(pieces) else None
                wrappings[index] = before or after or found[0]
        out: list[str] = []
        stretch: list[str] = []
        current: tuple[Formatting, Any, str] | None = None
        for (found, text), wrapping in [
            *zip(pieces, wrappings, strict=True),
            ((None, ""), None),
        ]:
            if stretch and current is not None and wrapping != current:
                formatting, hyperlink, ticks = current
                prefix, suffix = _wrapper(serializer, formatting, hyperlink)
                out.append(f"{prefix}{ticks}{''.join(stretch)}{ticks}{suffix}")
                stretch = []
            if found is None or wrapping is None:
                out.append(text)
            else:
                stretch.append(found[1])
                current = wrapping
        return "".join(out)

    class _JoinedInline(MarkdownInlineSerializer):
        """docling's inline group, without the space it puts beside formatted text.

        docling-core joins an inline group's parts with a space. Where the
        source had none, beside a link, emphasis, inline code or a
        sub/superscript, the parts join with nothing, so "H<sub>0</sub>" reads
        "H0" and "un<b>break</b>able" reads "unbreakable" in content.md,
        content.txt and chunks.jsonl, as they do on the page and in the PDF's
        text layer (docs/09 P14-56, P48). A group with no such join is joined
        exactly as docling joins it.
        """

        def serialize(
            self,
            *,
            item: InlineGroup,
            doc_serializer: Any,
            doc: DoclingDocument,
            list_level: int = 0,
            visited: set[str] | None = None,
            **kwargs: Any,
        ) -> SerializationResult:
            parts = doc_serializer.get_parts(
                item=item,
                list_level=list_level,
                is_inline_scope=True,
                visited=visited if visited is not None else set(),
                **kwargs,
            )
            runs: list[list[SerializationResult]] = []
            for part in (part for part in parts if part.text):
                if runs and _touching(runs[-1][-1], part):
                    runs[-1].append(part)
                else:
                    runs.append([part])
            return create_ser_result(
                text=" ".join(_joined_text(run, doc_serializer) for run in runs),
                span_source=parts,
            )

    # An item's code block written in its item (docs/09 P50).
    markdown_text, plain_text = _code_block_serializers()

    class _TableProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
            # Full-fidelity table text: the default triplet serialization drops
            # row-header cells, which chunk coverage would rightly flag.
            return _unpadded(
                ChunkingDocSerializer(
                    doc=doc,
                    text_serializer=markdown_text(),
                    table_serializer=MarkdownTableSerializer(),
                    annotation_serializer=_MarkedOcr(),
                    inline_serializer=_JoinedInline(),
                )
            )

    # By default the chunker drops a heading whose section has no body of its
    # own -- one followed directly by a heading at the same or a higher level,
    # or ending the page -- so no chunk carried its text while content.md and
    # content.txt kept it (P14-45). With always_emit_headings such a heading
    # arrives as a chunk with empty text and the heading path, whose doc_items
    # are that whole path; they are narrowed to the leaf, the heading the chunk
    # is for, so its locator anchors there as a content chunk's does.
    chunker = HierarchicalChunker(
        serializer_provider=_TableProvider(), always_emit_headings=True
    )
    seeds: list[ChunkSeed] = []
    for chunk in chunker.chunk(document):
        doc_items = chunk.meta.doc_items if chunk.text else chunk.meta.doc_items[-1:]
        labels = {str(item.label) for item in doc_items}
        seeds.append(
            ChunkSeed(
                text=chunk.text,
                headings=tuple(chunk.meta.headings or ()),
                doc_items=tuple(item.self_ref for item in doc_items),
                kind="table" if DocItemLabel.TABLE.value in labels else "text",
            )
        )
    # The chunker does not emit picture chunks; the figure seeds mirror v2's
    # visual chunks so text that exists only inside an image stays retrievable.
    for picture, asset in matched:
        caption_text = picture.caption_text(doc=document)
        parts = [
            f"Visual asset {asset.id}",
            caption_text,
            asset.caption,
            asset.alt,
            asset.aria_label,
            asset.title,
            ocr_reading(
                asset.ocr.text,
                asset_id=asset.id,
                engine=asset.ocr.engine,
                confidence=asset.ocr.confidence,
            )
            if asset.ocr.text
            else "",
        ]
        text = "\n\n".join(dict.fromkeys(part for part in parts if part)).strip()
        seeds.append(
            ChunkSeed(
                text=text or f"Visual asset {asset.id} ({asset.kind})",
                headings=(asset.nearby_heading,) if asset.nearby_heading else (),
                doc_items=(picture.self_ref,),
                kind="figure",
            )
        )

    def _doctags() -> str:
        """Doctags, tolerating docling-core's own label/token mismatch.

        The backend detects code languages the doctags token set cannot spell
        (CodeLanguageLabel.JSON exists; `<_JSON_>` is not a valid token), and
        the serializer raises on them.  Those items are downgraded to
        `unknown` for this export only — content.json keeps the detected
        language.  Each table's grid is built once for it, not once per
        cell (`_grids_built_once`, docs/09 P41).
        """
        from docling_core.types.doc.labels import CodeLanguageLabel
        from docling_core.types.doc.tokens import DocumentToken

        downgraded = []
        for item in document.texts:
            language = getattr(item, "code_language", None)
            if language is None:
                continue
            try:
                DocumentToken.get_code_language_token(str(language.value))
            except ValueError:
                downgraded.append((item, language))
                item.code_language = CodeLanguageLabel.UNKNOWN
        try:
            with _grids_built_once(document.tables):
                exported: str = document.export_to_doctags()
            return exported
        finally:
            for item, language in downgraded:
                item.code_language = language

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        document_dict = document.export_to_dict()
        # `export_to_markdown()` and `export_to_text()` with their defaults,
        # which are the serializers' own, and the OCR marked.
        markdown = (
            _unpadded(
                MarkdownDocSerializer(
                    doc=document,
                    text_serializer=markdown_text(),
                    annotation_serializer=_MarkedOcr(),
                    inline_serializer=_JoinedInline(),
                )
            )
            .serialize()
            .text
        )
        text = (
            _unpadded(
                PlainTextDocSerializer(
                    doc=document,
                    text_serializer=plain_text(),
                    annotation_serializer=_MarkedOcr(),
                    inline_serializer=_JoinedInline(),
                )
            )
            .serialize()
            .text
        )
        doctags = _doctags()

    surfaces = {
        "content.json": dump_json(document_dict),
        "content.md": markdown.rstrip() + "\n",
        "content.txt": text.rstrip() + "\n",
        "content.doctags": doctags.rstrip() + "\n",
    }
    tables = [
        [[cell.text for cell in row] for row in table.data.grid]
        for table in document.tables
    ]
    if markable:
        # Only the text items and table cells had their markers taken out. One
        # that reached any other field never joined anything, so it goes, and
        # the record says where, rather than deliver a character the page
        # never had.
        leaked = [name for name, value in surfaces.items() if _marked(value)]
        if any(_marked(cell) for grid in tables for row in grid for cell in row):
            leaked.append("tables")
        if any(
            _marked(seed.text) or any(map(_marked, seed.headings)) for seed in seeds
        ):
            leaked.append("chunks.jsonl")
        if leaked:
            surfaces = {name: unmark_joins(value) for name, value in surfaces.items()}
            tables = [
                [[unmark_joins(cell) for cell in row] for row in grid]
                for grid in tables
            ]
            seeds = [
                replace(
                    seed,
                    text=unmark_joins(seed.text),
                    headings=tuple(map(unmark_joins, seed.headings)),
                )
                for seed in seeds
            ]
            extraction_warnings.append(
                "a formatted part's join marker reached a field WebShot does not "
                f"rejoin ({', '.join(leaked)}); it was removed there, and that "
                "text may separate the part from its neighbours with a space "
                "(docs/09 P14-56, P48)."
            )

    return ExtractionResult(
        document_json=surfaces["content.json"],
        markdown=surfaces["content.md"],
        text=surfaces["content.txt"],
        doctags=surfaces["content.doctags"],
        tables=tables,
        chunk_seeds=seeds,
        item_count=len(document.texts) + len(document.tables) + len(document.pictures),
        picture_count=len(document.pictures),
        pictured_asset_ids=[
            asset.id
            for picture, asset in matched
            if picture.content_layer == ContentLayer.BODY
        ],
        chrome_pictured_asset_ids=[
            asset.id
            for picture, asset in matched
            if picture.content_layer != ContentLayer.BODY
        ],
        warnings=extraction_warnings,
    )


def validate_document_json(document_json: str) -> None:
    """Prove `content.json` loads as a DoclingDocument (acceptance G3)."""
    from docling_core.types.doc.document import DoclingDocument

    DoclingDocument.model_validate(json.loads(document_json))
