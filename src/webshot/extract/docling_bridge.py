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
- The backend makes no item of an `<li>` with no text of its own, so a list
  that item holds hangs straight off the outer list, the item has no line in
  content.md, and an ordered list given a start number counts the items after
  it one short. A third marker character stands in for the item's text until
  the document is built (docs/09 P45).
- The backend reads a `<pre>` in a list item into the item's line with its
  whitespace joined, and docling-core indents only the first line of a list
  item's child, so an item's code reached content.md as one line. The bridge
  extends the backend to read it as a block of code under the item, and the
  serializers to write each of its lines inside the item (docs/09 P50).
- The backend files a `<footer>`'s content on the furniture layer, then puts
  a heading inside one, and the rest of that footer, on the body layer. The
  conversion runs through a subclass that files everything the backend made
  inside a footer on the furniture layer (docs/09 P65).
- The backend writes each `<br>` as a private-use character while it parses
  and turns it back into a line break only in a paragraph's or a cell's text,
  so a list item, heading, caption or `<pre>` with a `<br>` carried U+E000
  into every text surface, and an item or caption with nothing else in it
  was made where an empty one is not. The bridge takes out the `<br>` of an
  element with no text before the backend reads it, and writes every other
  one out as the backend reads whitespace there (docs/09 P56).
- The backend strips and space-joins the text nodes of a `<pre>`, so
  highlighted code, every token an element, lost its indentation and most of
  its lines, and docling-core fences code with three backticks whatever it
  holds. The bridge reads a `<pre>`'s text as the page lays it out, and fences
  it with more backticks than any run in the code (docs/09 P55).
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable, Iterator, Sequence
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
#: The ARIA landmark roles of those four elements (banner is a page header's,
#: contentinfo a page footer's), which a page gives a <div> or a <table> to
#: mark the same chrome without the element (docs/09 P71).
_CHROME_ROLES = frozenset({"banner", "navigation", "contentinfo", "complementary"})
#: Elements that never hold content, so never close: one carrying a landmark
#: role cannot open a chrome region, or all that follows it would be chrome,
#: and the item parsers below (P45, P56) never wait for one to close.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


@dataclass(frozen=True, slots=True)
class _Position:
    """Where one `<img>` or `<table>` stands in the snapshot."""

    #: Inside header/nav/footer/aside, or an element with one of their roles.
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
    images and tables belong to page chrome (header/nav/footer/aside, or an
    element with one of their landmark roles) — the one kind of content that
    legitimately lands on the furniture layer.

    `clean` writes text as the backend writes an item's text. Chrome text is
    compared that way, because that is how every item's text arrives: the
    backend makes dashes and quotes plain and drops soft hyphens, so a menu
    item the page spells with a curly apostrophe is an item with a straight
    one, which the region as the page spells it does not contain (docs/09
    P58). It is required, not defaulted, so no comparison can be made without
    it."""

    def __init__(self, *, clean: Callable[[str], str]) -> None:
        super().__init__(convert_charrefs=True)
        self._clean = clean
        self.image_asset_ids: list[str | None] = []
        #: Where each `<img>` stands, in the same order.
        self.image_positions: list[_Position] = []
        self.table_captions: list[str] = []
        #: Where each `<table>` stands, in the same order.
        self.table_positions: list[_Position] = []
        #: One entry per top-level chrome region: its text cleaned as the
        #: backend cleans text and whole-space-normalized, in reading order.
        self.chrome_regions: list[str] = []
        self._caption_depth = 0
        self._caption_parts: list[str] = []
        self._chrome_depth = 0
        self._chrome_parts: list[str] = []
        #: The open elements, innermost last, each with whether it opened a
        #: chrome region: a <div> given a landmark role closes at its own
        #: </div>, which a count of open chrome tags cannot tell apart.
        self._open: list[tuple[str, bool]] = []
        self._footer_depth = 0
        self._table_depth = 0
        #: Past the backend's first heading, the one it starts the body at.
        self._headed = False
        #: The open `<pre>`s, and the text of the outermost so far. The bridge
        #: reads a `<pre>`'s text as the page lays it out, so a token of code
        #: split across elements is one word, as in its code item (P55).
        self._pre_depth = 0
        self._pre_parts: list[str] = []

    def _position(self) -> _Position:
        return _Position(
            chrome=bool(self._chrome_depth),
            furniture=not self._headed or bool(self._footer_depth),
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._pre_depth and (tag == "br" or tag in _PRE_LINE_TAGS):
            self._pre_parts.append(" ")
        if tag == "pre":
            self._pre_depth += 1
        attributes = dict(attrs)
        # Any tokens after the first are fallbacks, which ARIA reads only for
        # a role it does not know; it knows every landmark role.
        role = next(iter((attributes.get("role") or "").lower().split()), "")
        chrome = tag not in _VOID_TAGS and (
            tag in _CHROME_TAGS or role in _CHROME_ROLES
        )
        if tag not in _VOID_TAGS:
            self._open.append((tag, chrome))
        # Opened before the element's own position is taken, so a
        # <table role="navigation"> stands inside the region it opens.
        if chrome:
            if not self._chrome_depth:
                self._chrome_parts = []
            self._chrome_depth += 1
        if tag == "img":
            self.image_asset_ids.append(attributes.get("data-webshot-asset-id"))
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

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
        elif tag == "footer" and self._footer_depth:
            self._footer_depth -= 1
        if tag == "pre" and self._pre_depth:
            self._pre_depth -= 1
            if not self._pre_depth:
                self._chrome_data("".join(self._pre_parts))
                self._pre_parts = []
        elif self._pre_depth and tag in _PRE_LINE_TAGS:
            self._pre_parts.append(" ")
        if tag == "caption" and self._caption_depth:
            self._caption_depth -= 1
            self.table_captions[-1] = " ".join(" ".join(self._caption_parts).split())
        # The end tag closes the innermost element of its name, and every
        # element still open inside it; one that closes nothing open is ignored.
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                closed = self._open[index:]
                del self._open[index:]
                break
        else:
            closed = []
        for _, chrome in closed:
            if chrome:
                self._chrome_depth -= 1
                if not self._chrome_depth:
                    self.chrome_regions.append(
                        self._normalized(" ".join(self._chrome_parts))
                    )

    def handle_data(self, data: str) -> None:
        if self._caption_depth:
            self._caption_parts.append(data)
        if self._pre_depth:
            self._pre_parts.append(data)
        else:
            self._chrome_data(data)

    def _chrome_data(self, data: str) -> None:
        if self._chrome_depth and data.strip():
            self._chrome_parts.extend(data.split())

    def _normalized(self, text: str) -> str:
        # Cleaned before it is split: a soft hyphen or a zero-width space is
        # not whitespace, so the backend joins the two halves of the word.
        return " ".join(self._clean(text).split())

    def is_chrome_text(self, text: str) -> bool:
        """Whether `text` reads out of one chrome region.

        Substring containment against each region's normalized text — not a
        pooled word bag, which would misfile a lede whose every word happens
        to appear *somewhere* across a large page's chrome.  Only ever asked
        about items the backend already classified as furniture, so a false
        positive keeps a furniture item on the furniture layer.  Both sides
        are cleaned as the backend cleans text, so a lede with a straight
        apostrophe stays furniture under a menu holding the same words with a
        curly one, as it would under a menu with a straight one: the same
        false positive, now at either spelling (docs/09 P53, P58).
        """
        normalized = self._normalized(text)
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


#: A third noncharacter: the text of a list item that has none of its own but
#: holds a list, so the backend makes an item of it (docs/09 P45).
EMPTY_ITEM = "\ufdd2"
#: The stand-in as a character reference, which the backend's parser reads as
#: the character and `html.unescape` drops, as it drops every noncharacter.
_EMPTY_ITEM_REF_RE = re.compile(
    r"&#[xX]0*[fF][dD][dD]2(?![0-9a-fA-F])|&#0*64978(?![0-9])"
)

#: The lists an `<li>` belongs to, and the lists one can hold.
_ITEM_LIST_TAGS = frozenset({"ul", "ol"})
_LIST_TAGS = _ITEM_LIST_TAGS | {"dl"}
#: What a list item may hold beside its lists and still have no text of its
#: own, as the backend reads one: elements that carry nothing but their text.
#: Anything else (an image, a checkbox, a table) is left as the backend reads it,
#: and so is a `<br>`, which the backend writes as a character of its own
#: (U+E000), so it makes an item of one with nothing else in it. `extract`
#: takes out the `<br>`s of an item with no text before this reads the page
#: (`drop_lone_line_breaks`, docs/09 P56), so an item whose only content
#: beside its lists was line breaks reaches this holding only its lists.
_CARRIER_TAGS = INLINE_TAGS | frozenset({"div", "p"})


@dataclass(slots=True)
class _OpenItem:
    #: Where the item's content starts, which is where its stand-in text goes.
    at: int
    #: How many lists of its own are open, so text inside them is theirs.
    lists: int = 0
    holds_list: bool = False
    holds_other: bool = False


class _EmptyItems(HTMLParser):
    """Where each `<li>` that holds a list and no text of its own opens.

    The backend makes no item of such an `<li>`: it reads the item's text with
    its lists left out, finds none, and hangs the lists straight off the outer
    list. content.md then has no line for the item, and a list holding nothing
    but lists is the shape an invalid `<ul><ul>` gives too, so it cannot be
    told apart once the document is built (docs/09 P45).
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self.marks: list[int] = []
        self._open: list[str] = []
        self._items: list[tuple[int, _OpenItem]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _inside(self) -> _OpenItem | None:
        """The innermost open item, when what comes next is its own content."""
        if self._items and not self._items[-1][1].lists:
            return self._items[-1][1]
        return None

    def _element(self, tag: str) -> None:
        item = self._inside()
        if item is not None:
            if tag in _LIST_TAGS:
                item.holds_list = True
            elif tag not in _CARRIER_TAGS:
                item.holds_other = True
        if self._items and tag in _LIST_TAGS:
            self._items[-1][1].lists += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element(tag)
        if tag == "li" and self._open and self._open[-1] in _ITEM_LIST_TAGS:
            at = self._offset() + len(self.get_starttag_text() or "")
            self._items.append((len(self._open), _OpenItem(at)))
        if tag not in _VOID_TAGS:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._open:
            return
        while self._open:
            closed = self._open.pop()
            if self._items and self._items[-1][0] == len(self._open):
                _, item = self._items.pop()
                if item.holds_list and not item.holds_other:
                    self.marks.append(item.at)
            elif closed in _LIST_TAGS and self._items:
                self._items[-1][1].lists -= 1
            if closed == tag:
                return

    def handle_data(self, data: str) -> None:
        item = self._inside()
        if item is not None and data.strip():
            item.holds_other = True


def mark_empty_items(html: str) -> str:
    """`html` with `EMPTY_ITEM` as the text of each item that holds only lists.

    The backend then makes an item of it, numbered among its siblings, with
    its lists inside it, and `extract` empties its text once the document is
    built. A marked page opens with a byte-order mark, as `mark_inline_joins`
    explains.
    """
    if "<li" not in html.lower():
        return html
    parser = _EmptyItems(html)
    parser.feed(html)
    parser.close()
    if not parser.marks:
        return html
    # Built in one pass: slicing the page once per mark, as the join markers
    # are inserted, copies it each time, and 2,000 items on a 1.1 MB page took
    # 1.5 s (docs/09 P45-3).
    pieces: list[str] = []
    start = 0
    for offset in sorted(parser.marks):
        pieces += (html[start:offset], EMPTY_ITEM)
        start = offset
    pieces.append(html[start:])
    marked = "".join(pieces)
    return marked if marked.startswith("\ufeff") else "\ufeff" + marked


#: A list item's marker, as docling-core's Markdown serializer writes one.
_ITEM_MARKER_RE = re.compile(r"[-*+]|\d+[.)]")
#: The stand-in as the end of its item's line.
_STAND_IN = f" {EMPTY_ITEM}"
#: A stand-in inside a line, where a table cell flattened its list onto one:
#: the marker before it, then the spaces of the line break and indent after.
_INLINE_STAND_IN_RE = re.compile(
    rf"(?:^|(?<= ))({_ITEM_MARKER_RE.pattern}){_STAND_IN} +(?=\S)", re.MULTILINE
)


def write_empty_items(markdown: str) -> str:
    """`markdown` with each stand-in item written as CommonMark writes one.

    An item that holds only a list goes on the line of its list's first item,
    `-   - Gorvane`, its marker padded to where docling indents the list, so
    the next item of that list lines up beneath it. A marker on a line of its
    own (`-`) is an empty item, which CommonMark does not let interrupt a
    paragraph: under an item with text, it read as that text's setext
    underline and the list beneath it as a code block (docs/09 P45). A
    stand-in with no deeper line after it, or with a marker too wide for
    docling's four-space indent (`1000.`), keeps its line with the marker
    alone. In a table cell, which docling writes on one line, the item is its
    marker before its list's first, `- - Gorvane`. A stand-in anywhere else is
    left for the caller to find.
    """
    if EMPTY_ITEM not in markdown:
        return markdown
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        while line.endswith(_STAND_IN) and index < len(lines):
            head = line[: -len(_STAND_IN)]
            nested = lines[index]
            pad = len(nested) - len(nested.lstrip(" ")) - len(head)
            if not (
                _ITEM_MARKER_RE.fullmatch(head.rsplit(" ", 1)[-1])
                and nested.strip()
                and 1 <= pad <= 4
            ):
                break
            line = head + " " * pad + nested.lstrip(" ")
            index += 1
        out.append(line.removesuffix(_STAND_IN))
    return _INLINE_STAND_IN_RE.sub(r"\1 ", "\n".join(out))


#: The character the backend writes for each `<br>` while it parses
#: (`_BR_SENTINEL` in docling's HTML backend). It deletes the page's own from
#: the page's text first, but not from an attribute it reads into a text (an
#: image's alt text, a field's value), where the page's own reads as one.
LINE_BREAK = "\ue000"
#: The page's own U+E000, as a character reference.
_LINE_BREAK_REF_RE = re.compile(r"&#[xX]0*[eE]000(?![0-9a-fA-F])|&#0*57344(?![0-9])")


def write_line_breaks(text: str, *, lines: bool) -> str:
    """`text` with each line break the backend left in it written out.

    The backend joins the runs of a text with a space, so a line break
    arrives with one either side of it, and those go with it. In a block of
    code (`lines`) each one is a line ending, as a paragraph's is. Anywhere
    else the backend reads the text onto one line, joining each run of
    whitespace in it into a space, and a line break is whitespace there too.
    One at either end of the text ends a line that ends there anyway.
    """
    if LINE_BREAK not in text:
        return text
    pieces = text.split(LINE_BREAK)
    pieces = [
        pieces[0].rstrip(" "),
        *(piece.strip(" ") for piece in pieces[1:-1]),
        pieces[-1].lstrip(" "),
    ]
    if not lines:
        return " ".join(piece for piece in pieces if piece)
    written = [index for index, piece in enumerate(pieces) if piece]
    return "\n".join(pieces[written[0] : written[-1] + 1]) if written else ""


#: The elements the backend reads as one line of text and makes an item of
#: only when that text is not empty, by the element each must sit in to be read.
_LINE_HOLDERS = {
    "li": frozenset({"ul", "ol"}),
    "dt": frozenset({"dl"}),
    "dd": frozenset({"dl"}),
}
#: The captions, which the backend reads whole and keeps only with text.
_CAPTION_TAGS = frozenset({"caption", "figcaption"})
#: What a list item's own text leaves out, as the backend reads it: the lists
#: and tables it holds, which are content of their own.
_OWN_TEXT_SKIPS = frozenset({"ul", "ol", "dl", "table"})


@dataclass(slots=True)
class _Holder:
    caption: bool
    #: Where each of its own `<br>` tags starts and ends.
    breaks: list[tuple[int, int]] = field(default_factory=list)
    text: bool = False
    #: How many lists or tables of its own are open, whose text is theirs.
    skipping: int = 0


class _LoneBreaks(HTMLParser):
    """The `<br>` tags of each list item, term, description or caption with no text.

    What counts as an element's text is what the backend reads as it: a list
    item's, term's or description's leaves out the lists and tables it holds,
    and a caption's is all of it. The page's own U+E000 is no text, as the
    backend deletes it.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self.spans: list[tuple[int, int]] = []
        #: Per open element: its tag, the holder it opened, and the holder
        #: whose own text it is left out of.
        self._open: list[tuple[str, _Holder | None, _Holder | None]] = []
        self._holders: list[_Holder] = []

    def _line_break(self) -> None:
        holder = self._holders[-1] if self._holders else None
        if holder is not None and not holder.skipping:
            line, column = self.getpos()
            start = self._line_starts[line - 1] + column
            holder.breaks.append((start, start + len(self.get_starttag_text() or "")))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._line_break()
        if tag in _VOID_TAGS:
            return
        within = self._open[-1][0] if self._open else ""
        opened = None
        if tag in _CAPTION_TAGS or within in _LINE_HOLDERS.get(tag, ()):
            opened = _Holder(caption=tag in _CAPTION_TAGS)
            self._holders.append(opened)
        skipped = None
        if (
            opened is None
            and tag in _OWN_TEXT_SKIPS
            and self._holders
            and not self._holders[-1].caption
        ):
            skipped = self._holders[-1]
            skipped.skipping += 1
        self._open.append((tag, opened, skipped))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._line_break()

    def handle_endtag(self, tag: str) -> None:
        if all(open_tag != tag for open_tag, _, _ in self._open):
            return
        while self._open:
            closed, opened, skipped = self._open.pop()
            self._close(opened, skipped)
            if closed == tag:
                return

    def _close(self, opened: _Holder | None, skipped: _Holder | None) -> None:
        if opened is not None:
            self._holders.pop()
            if not opened.text:
                self.spans.extend(opened.breaks)
        if skipped is not None:
            skipped.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not data.replace(LINE_BREAK, "").strip():
            return
        for holder in reversed(self._holders):
            if holder.skipping:
                break
            holder.text = True

    def close(self) -> None:
        super().close()
        while self._open:
            _, opened, skipped = self._open.pop()
            self._close(opened, skipped)


def drop_lone_line_breaks(html: str) -> str:
    """`html` without the `<br>` tags of the elements that hold no text but them.

    The backend makes an item of a list item, term or description, or a
    caption, only when its text is not empty, and the character it writes
    for a `<br>` is not whitespace to it: `<li><br></li>` was an item, and
    `<li><br><ul>` an item holding the list, where `<li></li>` is none and
    the list it holds is the outer list's. Without its line breaks such an
    element reads as it does empty, numbered, nested and captioned as the
    backend does that (docs/09 P56).
    """
    if "<br" not in html:
        return html
    parser = _LoneBreaks(html)
    parser.feed(html)
    parser.close()
    if not parser.spans:
        return html
    pieces: list[str] = []
    start = 0
    for begin, end in sorted(parser.spans):
        pieces.append(html[start:begin])
        start = end
    pieces.append(html[start:])
    return "".join(pieces)


def _write_document_line_breaks(document: Any) -> None:
    """Every line break the backend left in `document`'s texts, written out.

    A `<pre>`'s code keeps its lines. Every other text the backend left one
    in, it read onto one line: a list item's, with its parts and any code in
    it, a heading's, a caption's, a checkbox's label. A text that was only
    line breaks is what the backend makes of whitespace there: none for a
    part of an item's line, which goes, and an empty heading, checkbox or
    block of code, which stays. A list item or caption that held only line
    breaks had them taken out before the backend read it
    (`drop_lone_line_breaks`), so one that comes here empty is one that
    function did not foresee, and goes as the backend makes none.
    """
    from docling_core.types.doc.document import CodeItem, InlineGroup, ListItem
    from docling_core.types.doc.labels import DocItemLabel

    def parent_of(item: Any) -> Any:
        return item.parent.resolve(document) if item.parent else None

    gone: list[Any] = []
    for item in document.texts:
        if LINE_BREAK not in item.text and LINE_BREAK not in item.orig:
            continue
        holder = parent_of(item)
        part = isinstance(holder, InlineGroup)
        in_item_line = part and isinstance(parent_of(holder), ListItem)
        lines = isinstance(item, CodeItem) and not in_item_line
        item.text = write_line_breaks(item.text, lines=lines)
        item.orig = write_line_breaks(item.orig, lines=lines)
        if not (item.text or item.children) and (
            part or isinstance(item, ListItem) or item.label == DocItemLabel.CAPTION
        ):
            gone.append(item)
    # An inline group, and a list item whose text was all its line's parts,
    # go with the last of the parts they held.
    refs = {item.self_ref for item in gone}
    holders = [parent_of(item) for item in gone]
    while holders:
        climbed = []
        for holder in {id(holder): holder for holder in holders}.values():
            if (
                (
                    isinstance(holder, InlineGroup)
                    or (isinstance(holder, ListItem) and not holder.text)
                )
                and holder.self_ref not in refs
                and all(child.cref in refs for child in holder.children)
            ):
                refs.add(holder.self_ref)
                climbed.append(parent_of(holder))
        holders = climbed
    if refs:
        _remove_items(document, refs)


def _remove_items(document: Any, refs: set[str]) -> None:
    """`document` without the text items and groups whose references are `refs`.

    The document docling-core's `delete_items` leaves, in one pass over it.
    `delete_items` counts, for every reference left, the items removed before
    it, so removing 1,000 empty list items took it 2.3 s, and ten times as
    many would take a hundred times as long (docs/09 P56). Here every
    reference in the document is renumbered at once, and one to a removed
    item is dropped from the list that held it. No removed item holds
    anything that stays.
    """
    from docling_core.types.doc.document import RefItem
    from pydantic import BaseModel

    renumbered: dict[str, str] = {}
    for label in ("texts", "groups"):
        kept = [item for item in getattr(document, label) if item.self_ref not in refs]
        for index, item in enumerate(kept):
            renumbered[item.self_ref] = f"#/{label}/{index}"
        setattr(document, label, kept)

    def visit(value: Any) -> None:
        if isinstance(value, RefItem):
            value.cref = renumbered.get(value.cref, value.cref)
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                # Read past the attribute, which raises for a field
                # docling-core has deprecated.
                field_value = value.__dict__.get(name)
                if name == "self_ref" and isinstance(field_value, str):
                    setattr(value, name, renumbered.get(field_value, field_value))
                elif isinstance(field_value, list) and any(
                    isinstance(entry, RefItem) and entry.cref in refs
                    for entry in field_value
                ):
                    field_value = [
                        entry
                        for entry in field_value
                        if not (isinstance(entry, RefItem) and entry.cref in refs)
                    ]
                    setattr(value, name, field_value)
                visit(field_value)
        elif isinstance(value, list | tuple):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            for entry in value.values():
                visit(entry)

    visit(document)


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


def _captions_missing(
    snapshot: Sequence[str], carried: Sequence[str], *, clean: Callable[[str], str]
) -> list[str]:
    """The snapshot's table captions that no table in the document carries.

    Compared as whitespace-collapsed text and as a multiset, so two tables
    captioned alike need two carried captions.  An empty caption is a table
    without one, not a caption to find.  Both sides go through `clean`, which
    writes text as the backend does, as `_SnapshotFacts` compares chrome: the
    backend writes a caption's curly quote straight, so the page's spelling
    would report missing a caption the document carries (docs/09 P58).
    """
    remaining = [" ".join(clean(text).split()) for text in carried]
    missing: list[str] = []
    for caption in snapshot:
        wanted = " ".join(clean(caption).split())
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
#: The blank lines before a `<pre>`'s first line of text.
_LEADING_BLANK_LINES_RE = re.compile(r"(?:[^\S\r\n]*(?:\r\n|\r|\n))+")
#: Elements a browser lays out as blocks, so in a `<pre>` each of them starts
#: a line and ends one: Docusaurus writes each line of code as a `<div>`
#: ending in a `<br>`, with no line ending between them (docs/09 P55).
_PRE_LINE_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tr",
        "ul",
    }
)
#: The backend methods `_code_block_backend` overrides or calls. They are
#: private to docling, so `extract` checks they are still there.
_BACKEND_HOOKS = (
    "_add_list_item_with_content",
    "_clean_unicode",
    "_code_language_hint",
    "_extract_text_and_hyperlink_recursively",
    "_handle_block",
    "_is_suppressed_tag",
    "_make_prov",
    "_make_text_prov",
    "_process_nested_element",
)
#: What `_code_block_backend` reads from the backend's module: the character
#: the backend puts in place of each `<br>`, and its guess at a code's language.
_BACKEND_MODULE_HOOKS = ("_BR_SENTINEL", "detect_code_language")


def _without_edge_blank_lines(text: str) -> str:
    """`text` without the blank lines before its first line of text and after
    its last. A line's own indentation is the code's; a line of only
    whitespace is not, and the HTML parser drops a line ending just after
    `<pre>`, which docling's parser keeps."""
    leading = _LEADING_BLANK_LINES_RE.match(text)
    if leading:
        text = text[leading.end() :]
    ending = _LINE_END_RE.search(text, len(text.rstrip()))
    return text[: ending.start()] if ending else text


def _backend_hooked() -> bool:
    """Whether docling's HTML backend still has all `_code_block_backend` uses."""
    from docling.backend import html_backend

    return all(
        hasattr(html_backend.HTMLDocumentBackend, hook) for hook in _BACKEND_HOOKS
    ) and all(hasattr(html_backend, hook) for hook in _BACKEND_MODULE_HOOKS)


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

    It reads every `<pre>` as the page lays its text out, into one `code`
    item (docs/09 P55). The backend strips each text node in it, drops one of
    only whitespace unless it is only line endings, joins what is left with a
    space, and strips the result, so highlighted code, whose every token is an
    element, lost its indentation and most of its lines, and the first line of
    any code its indentation. Code with emphasis or a link in it was several
    runs, each a `code` item of an inline group, written inline with its lines
    joined; one inside a link the backend reread without its lines.
    """
    from bs4.element import NavigableString, PreformattedString, Tag
    from docling.backend import html_backend
    from docling.backend.html_backend import AnnotatedTextList, HTMLDocumentBackend

    class _CodeBlockBackend(HTMLDocumentBackend):
        #: The `<pre>` elements, by id, of the item whose own text is being read.
        _reading: frozenset[int] = frozenset()

        def _pre_text(self, pre: Any) -> str:
            """The text of `pre` as the page lays it out, blank edge lines and all.

            Each text node as it is, a `<br>` a line ending, and an element a
            browser lays out as a block on lines of its own. An element the
            backend hides, and a comment, give no text, as they give the
            backend's own reading none.
            """
            parts: list[str] = []
            # A block closed or opened since the last text, so the next text
            # starts a line, unless the text before it ended one.
            pending = False

            def add(text: str) -> None:
                nonlocal pending
                if not text:
                    return
                if pending and parts and not parts[-1].endswith(("\n", "\r")):
                    parts.append("\n")
                pending = False
                parts.append(text)

            def walk(node: Any) -> None:
                nonlocal pending
                for child in node.children:
                    if isinstance(child, PreformattedString):
                        continue
                    if isinstance(child, NavigableString):
                        add(str(child).replace(html_backend._BR_SENTINEL, "\n"))
                    elif isinstance(child, Tag) and not self._is_suppressed_tag(child):
                        block = child.name in _PRE_LINE_TAGS
                        pending = pending or block
                        walk(child)
                        pending = pending or block

            walk(pre)
            return "".join(parts)

        def _gutter_code(self, table: Any) -> Any:
            """The `<pre>` of code that `table` sets beside its line numbers.

            Pygments, Rouge and Chroma number a block's lines in a table of one
            row: a `<pre>` of the numbers, one a line, and a `<pre>` of the
            code. The backend wrote it as a table row, the code on one line.
            A table is one only if it holds nothing else, no other row, cell
            or text, and its numbers count up by one, with no fewer of them
            than the code has lines.
            """
            rows = table.find_all("tr")
            if len(rows) != 1:
                return None
            cells = rows[0].find_all(["td", "th"], recursive=False)
            held = [cell.find_all("pre") for cell in cells]
            if len(cells) != 2 or any(len(pres) != 1 for pres in held):
                return None
            pres = [pre for (pre,) in held]
            if any(self._is_suppressed_tag(pre) for pre in pres):
                return None
            # By identity: bs4 compares two tags by their markup.
            beside = (
                text
                for text in table.find_all(string=True)
                if not any(parent is pre for parent in text.parents for pre in pres)
            )
            if any(
                text.replace(html_backend._BR_SENTINEL, "").strip() for text in beside
            ):
                return None
            numbers, code = (
                _without_edge_blank_lines(self._pre_text(pre)) for pre in pres
            )
            lines = _LINE_END_RE.split(numbers)
            if not code.strip() or not all(
                re.fullmatch(r"\s*[0-9]+\s*", line) for line in lines
            ):
                return None
            first = int(lines[0])
            if [int(line) for line in lines] != list(range(first, first + len(lines))):
                return None
            if len(_LINE_END_RE.split(code)) > len(lines):
                return None
            return pres[1]

        def _handle_block(self, tag: Any, doc: Any) -> Any:
            if tag.name.lower() == "table":
                code = self._gutter_code(tag)
                if code is not None:
                    # The code, as a `<pre>` on its own; the numbers are the
                    # page's count of its lines (docs/09 P55).
                    return self._handle_block(code, doc)
            if tag.name.lower() != "pre":
                return super()._handle_block(tag, doc)
            orig = _without_edge_blank_lines(self._pre_text(tag))
            if not orig.strip():
                return []
            # The backend's own runs, for what annotates them: a link or
            # formatting all of the code has is the code item's, as the
            # backend gave one run's to its item. Parts of it have no field.
            runs = [
                run
                for run in self._extract_text_and_hyperlink_recursively(
                    tag, find_parent_annotation=True, keep_newlines=True
                )
                if run.text.strip()
            ]

            def common(values: list[Any]) -> Any:
                return (
                    values[0]
                    if values and values.count(values[0]) == len(values)
                    else None
                )

            text = HTMLDocumentBackend._clean_unicode(orig)
            source_tag_id = common([run.source_tag_id for run in runs])
            code = doc.add_code(
                parent=self.parents[self.level],
                text=text,
                orig=orig,
                code_language=html_backend.detect_code_language(
                    text, hint=self._code_language_hint(tag)
                ),
                content_layer=self.content_layer,
                formatting=common([run.formatting for run in runs]),
                hyperlink=common([run.hyperlink for run in runs]),
                prov=self._make_prov(text=text, tag=tag, source_tag_id=source_tag_id),
            )
            return [code.get_ref()]

        def _is_code_block(self, pre: Any, item: Any) -> bool:
            """Whether `item` holds `pre` as a block of code of its own.

            A list or table between the two reads the `<pre>` as its own
            content, and a `<pre>` the backend hides, or that is in an element
            it hides, or that holds no text, gave the item no text, so it stays
            out. So does one that holds an image, a list or a table, which
            walking the item made an item of its own.
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
            return bool(self._pre_text(pre).strip())

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
                params = MarkdownParams(**kwargs)
                if isinstance(owner, ListItem):
                    column = self._column(owner, *args, params, kwargs)
                    return create_ser_result(
                        text=_indented(
                            self._lines(item, *args, params, kwargs),
                            column,
                            " " * column,
                        ),
                        span_source=item,
                    )
                if not params.format_code_blocks or item.captions:
                    return None
                # At the margin, as docling writes it, in a fence no line of
                # the code closes (docs/09 P55).
                return create_ser_result(
                    text="\n".join(self._lines(item, *args, params, kwargs)),
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
            """The lines of `code` as a block, before its item's indentation.

            One line of code with a link of its own is inline code in the
            link, as docling writes it: a paragraph that is only linked code
            stays a link. docling writes more lines with a link as inline code
            too, which joins them, and wraps emphasis around a fence, which
            makes the fence text, so those are a block with neither:
            content.json keeps them, and links.json the link (docs/09 P55).
            """
            if (
                params.include_hyperlinks
                and code.hyperlink
                and not _LINE_END_RE.search(code.text)
            ):
                return [
                    self._docling(code, doc_serializer, doc, False, set(), kwargs).text
                ]
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
    from docling.backend.html_backend import _BR_SENTINEL, HTMLDocumentBackend
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
        FloatingItem,
        Formatting,
        GroupItem,
        InlineGroup,
        ListItem,
        NodeItem,
        PictureDescriptionData,
        PictureMiscData,
        Script,
        TextItem,
    )
    from docling_core.types.doc.labels import DocItemLabel, GroupLabel

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
    # Likewise a page carrying the stand-in for an item's missing text.
    items_markable = EMPTY_ITEM not in docling_html and not _EMPTY_ITEM_REF_RE.search(
        docling_html
    )
    if not items_markable:
        extraction_warnings.append(
            "the page contains U+FDD2, the character WebShot uses to stand in "
            "for the text of a list item that holds only a list, so content.md, "
            "content.txt and chunks.jsonl write such an item as docling does, "
            "with no line of its own, and an ordered list given a start number "
            "numbers the items after it one too low (docs/09 P45)."
        )
    own_line_breaks = LINE_BREAK in docling_html or bool(
        _LINE_BREAK_REF_RE.search(docling_html)
    )
    if own_line_breaks:
        extraction_warnings.append(
            "the page contains U+E000, the character docling's HTML backend "
            "writes for a line break while it parses: the backend deletes the "
            "page's own from its text, and one an attribute carried into a "
            "text (an image's alt text, a field's value) is read as a line "
            "break there, a space (docs/09 P56)."
        )
    # The methods the backend's subclass relies on are private to docling.
    # Without one, the page is read by docling's own backend, an item's code
    # into the item's line, and the record says so.
    hooked = _backend_hooked()
    base = _code_block_backend() if hooked else HTMLDocumentBackend
    if not hooked:
        extraction_warnings.append(
            "docling's HTML backend no longer has the methods WebShot extends to "
            "read a <pre>'s text as the page lays it out and a list item's <pre> "
            "as a block of code, so content.md, content.txt and chunks.jsonl "
            "hold code as docling reads it: highlighted code with its lines "
            "joined, a first line without its indentation, and each list item's "
            "block on one line, in its item's text (docs/09 P50, P55)."
        )
    # Every item the backend made inside a <footer>, which stays on the
    # furniture layer whatever its text. Held as the items, not their
    # references, until the document is done changing: writing out its
    # line breaks removes an item made of one and renumbers every item
    # after it (docs/09 P56).
    made_in_footer: list[NodeItem] = []

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
                        made_in_footer.append(item)

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
        parsed = drop_lone_line_breaks(docling_html)
        if markable:
            parsed = mark_inline_joins(parsed)
        # After the lone line breaks are out: an item whose only content
        # beside its lists was `<br>`s holds only its lists (docs/09 P45, P56).
        if items_markable:
            parsed = mark_empty_items(parsed)
        document: DoclingDocument = backend(
            in_doc=in_doc, path_or_stream=BytesIO(parsed.encode("utf-8"))
        ).convert()
    except Exception as exc:
        raise BundleBuildError(f"docling could not parse the snapshot: {exc}") from exc

    # Before anything reads a text: the chrome and caption checks below match
    # texts against the page's own, where a `<br>` reads as a space, so a
    # chrome item with one moved into the body and a caption with one read as
    # lost (docs/09 P56).
    _write_document_line_breaks(document)
    # The footer's items by the references they have now. One the line breaks
    # removed is in none of the document's lists, and its old reference may
    # now be another item's.
    held = {id(item) for _, items in _item_lists(document) for item in items}
    in_footer = {item.self_ref for item in made_in_footer if id(item) in held}

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

    # Each stand-in becomes what the page had, an item with no text. Chunking
    # and content.md put it back while they write the item's line.
    empty_items: list[ListItem] = []
    if items_markable:
        for item in document.texts:
            if isinstance(item, ListItem) and item.text == EMPTY_ITEM:
                item.text = item.orig = ""
                empty_items.append(item)
        # A cell's own text is its content's, flattened, stand-in and all.
        for table in document.tables:
            for cell in table.data.table_cells:
                cell.text = cell.text.replace(EMPTY_ITEM, "")

    @contextmanager
    def _standing_in() -> Iterator[None]:
        """The empty items with their stand-in, while a surface writes their lines.

        content.md, content.txt and the chunks write an item as `marker text`,
        so an item with no text is a bare marker there, which
        `write_empty_items` cannot tell from any other; with the stand-in it
        can. content.json and the doctags hold the item as it is, empty.
        """
        for item in empty_items:
            item.text = EMPTY_ITEM
        try:
            yield
        finally:
            for item in empty_items:
                item.text = ""

    def as_the_backend_writes(text: str) -> str:
        """`text` as the backend writes an item's text, for comparing the two.

        Cleaned as it cleans every item's text, and with the character it
        writes for a `<br>` in a list item or a caption read as the break it
        stands for: it deletes the page's own copies of that character before
        parsing, so in an item's text it is always a `<br>` (docs/09 P58).
        """
        cleaned: str = HTMLDocumentBackend._clean_unicode(text)
        return cleaned.replace(_BR_SENTINEL, " ")

    facts = _SnapshotFacts(clean=as_the_backend_writes)
    facts.feed(sanitized_html)

    # The backend files everything before the first heading on the furniture
    # layer, which serializers and the chunker skip. That is right for page
    # chrome and wrong for document content (an adapter page's descriptor, a
    # lede before the h1) — v2 recorded such text and parity holds v3 to it.
    # Content outside header/nav/footer/aside and their landmark roles moves
    # back to the body layer (docs/09 P71). What the backend made inside a footer is known to have
    # come from one, so it stays without a text test. The stand-in made for
    # an <li> that holds only a list is placed by the loop below (P45).
    #
    # What the backend filed under a <footer>, before the first heading or
    # after it, is chrome by where it sits, the same element the bridge counts
    # as chrome, so it is not tested again by its text. A test there could
    # only agree, or misfire where the backend's text is not the page's, as
    # a picture's alt text is: it becomes a caption no page text holds
    # (docs/09 P58). The backend hangs a picture's caption from the body, not
    # from the footer, so each table's and picture's captions are carried
    # along with it.
    # P65's backend notes what it made inside each footer while it reads the
    # page, which is the footer exactly: under the footer's group can also
    # hang content after the footer, from a heading made inside it. Without
    # that hook, what hangs under a footer group is all that is known.
    if not footer_hooked:
        for group in document.groups:
            if group.name != "footer" or group.label != GroupLabel.SECTION:
                continue
            for node, _ in document.iterate_items(
                root=group,
                with_groups=True,
                traverse_pictures=True,
                included_content_layers=set(ContentLayer),
            ):
                in_footer.add(node.self_ref)
                if isinstance(node, FloatingItem):
                    in_footer.update(caption.cref for caption in node.captions)

    # A group the backend hung off the body (a list, a paragraph's inline
    # runs, a details section) moves as one block, with everything
    # under it, or stays as one, by its whole text. Item by item, its items
    # went back and the group did not, so the serializers met each one on its
    # own: a nested list was written flat and loose, a paragraph a line per
    # bold or linked run, and a run as short as "Terms" read out of a menu and
    # left its sentence (docs/09 P53). The block's text is compared as the
    # backend writes text, as every text is (`as_the_backend_writes`), or one
    # curly quote or line break in a menu sends it all to the body. A group
    # with no text has nothing to tell chrome by and stays.
    # What the backend made inside a footer is in no block: it stays where
    # it was filed, and a footer's own section group, hung off the body, is
    # no block at all, since content after the footer can hang from what
    # the footer made (P65). Without P65's hook, the footer is what hangs
    # under its group (P58).
    def _descendants(node: NodeItem) -> list[NodeItem]:
        """Every node under `node`, on any layer, in document order."""
        found: list[NodeItem] = []
        pending = [ref.resolve(document) for ref in reversed(node.children)]
        while pending:
            current = pending.pop()
            found.append(current)
            pending += (ref.resolve(document) for ref in reversed(current.children))
        return found

    decided: set[str] = set()
    for child in document.body.children:
        group = child.resolve(document)
        if (
            group.content_layer != ContentLayer.FURNITURE
            or not isinstance(group, GroupItem)
            or group.self_ref in in_footer
        ):
            continue
        block = [
            node
            for node in (group, *_descendants(group))
            if node.self_ref not in in_footer
        ]
        decided.update(node.self_ref for node in block)
        block_text = " ".join(node.text for node in block if isinstance(node, TextItem))
        if not block_text.strip():
            continue
        layer = (
            ContentLayer.FURNITURE
            if facts.is_chrome_text(block_text)
            else ContentLayer.BODY
        )
        for node in block:
            node.content_layer = layer
    # A text on its own keeps the test it had, and so does furniture under a
    # heading, which only the backend's <footer> rule puts there.
    standing_in = {item.self_ref for item in empty_items}
    for item in document.texts:
        if (
            item.content_layer == ContentLayer.FURNITURE
            and item.self_ref not in in_footer
            and item.self_ref not in standing_in
            and item.self_ref not in decided
            and not facts.is_chrome_text(item.text)
        ):
            item.content_layer = ContentLayer.BODY
    # An empty item has no text to tell chrome by, so it goes where its own
    # items went: back to the body if any of them did, innermost first. Moved
    # on its own, an item of a nav menu was a bare marker in content.md.
    for empty in reversed(empty_items):
        if empty.content_layer == ContentLayer.FURNITURE and any(
            item.content_layer == ContentLayer.BODY
            for item, _ in document.iterate_items(
                root=empty, included_content_layers=set(ContentLayer)
            )
            if item is not empty
        ):
            empty.content_layer = ContentLayer.BODY
    # An item with no text that holds its code has no text to tell chrome by,
    # so it stays on its code's layer: moved alone, a footer's item was in the
    # body, and its code written there with it (docs/09 P50).
    for item in document.texts:
        if isinstance(item, ListItem) and not item.text and item.children:
            code = item.children[0].resolve(document)
            if isinstance(code, CodeItem):
                item.content_layer = code.content_layer

    # A table or picture has no text to tell chrome by, so where its element
    # stands in the snapshot decides it: one inside header/nav/footer/aside, or
    # an element with one of their landmark roles (docs/09 P71), stays
    # furniture, any other goes back to the body. Left on furniture, a
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
                "there sit in page chrome (a header, nav, footer or aside, or an "
                "element with one of their landmark roles) and some do not, so "
                "which of them is chrome could not be told by position, and all "
                "were left out as chrome. content.json and content.doctags hold "
                "them (docs/09 P57)."
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
        missing = _captions_missing(
            facts.table_captions, carried, clean=as_the_backend_writes
        )
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
    with _standing_in():
        chunks = list(chunker.chunk(document))
    for chunk in chunks:
        doc_items = chunk.meta.doc_items if chunk.text else chunk.meta.doc_items[-1:]
        labels = {str(item.label) for item in doc_items}
        seeds.append(
            ChunkSeed(
                text=write_empty_items(chunk.text),
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
        with _standing_in():
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
        markdown = write_empty_items(markdown)
        text = write_empty_items(text)
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
    if items_markable:
        # The stand-in was taken out of its item, the cells, and the item's
        # line. One anywhere else stood in for no item there, so it goes too,
        # and the record says where.
        strayed = [name for name, value in surfaces.items() if EMPTY_ITEM in value]
        if any(EMPTY_ITEM in cell for grid in tables for row in grid for cell in row):
            strayed.append("tables")
        if any(
            EMPTY_ITEM in seed.text or any(EMPTY_ITEM in part for part in seed.headings)
            for seed in seeds
        ):
            strayed.append("chunks.jsonl")
        if strayed:
            surfaces = {
                name: value.replace(EMPTY_ITEM, "") for name, value in surfaces.items()
            }
            tables = [
                [[cell.replace(EMPTY_ITEM, "") for cell in row] for row in grid]
                for grid in tables
            ]
            seeds = [
                replace(
                    seed,
                    text=seed.text.replace(EMPTY_ITEM, ""),
                    headings=tuple(
                        part.replace(EMPTY_ITEM, "") for part in seed.headings
                    ),
                )
                for seed in seeds
            ]
            extraction_warnings.append(
                "the stand-in for the text of a list item that holds only a "
                f"list reached a field it does not belong in ({', '.join(strayed)}); "
                "it was removed there, and the item may have no line of its own "
                "there (docs/09 P45)."
            )
    if not own_line_breaks:
        # Every text item had its line breaks written out. One that reached
        # any other field came from a path of the backend's that the bridge
        # has not measured, so it is read there as whitespace, and the record
        # says where, rather than deliver a character the page never had.
        stray = [name for name, value in surfaces.items() if LINE_BREAK in value]
        if any(LINE_BREAK in cell for grid in tables for row in grid for cell in row):
            stray.append("tables")
        if any(
            LINE_BREAK in seed.text or any(LINE_BREAK in h for h in seed.headings)
            for seed in seeds
        ):
            stray.append("chunks.jsonl")
        if stray:

            def unbroken(value: str) -> str:
                return value.replace(LINE_BREAK, " ")

            surfaces = {name: unbroken(value) for name, value in surfaces.items()}
            tables = [
                [[unbroken(cell) for cell in row] for row in grid] for grid in tables
            ]
            seeds = [
                replace(
                    seed,
                    text=unbroken(seed.text),
                    headings=tuple(map(unbroken, seed.headings)),
                )
                for seed in seeds
            ]
            extraction_warnings.append(
                "docling's HTML backend left the character it writes for a line "
                f"break (U+E000) in a field WebShot does not write it out of "
                f"({', '.join(stray)}); it was written there as a space "
                "(docs/09 P56)."
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
