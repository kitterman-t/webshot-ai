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
  group with a space, so "H<sub>0</sub>" reached every text surface as
  "H 0". Where a sub/superscript touches the text beside it is carried through
  the backend as two marker characters (docs/09 P14-56).
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from io import BytesIO
from typing import TYPE_CHECKING, Any, TypeVar

from ..bundle.publish import dump_json
from ..capture.snapshot import TITLE_ELEMENT_RE
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
    #: The harvested assets attached to a picture, whose OCR text therefore
    #: reached content.md, content.txt and chunks.jsonl. Empty when the count
    #: check skipped attachment, which `warnings` then says.
    pictured_asset_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CHROME_TAGS = frozenset({"header", "nav", "footer", "aside"})

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
    which harvested asset, what each `<table>`'s caption said, and which text
    belongs to page chrome (header/nav/footer/aside) — the one kind of content
    that legitimately lands on the furniture layer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_asset_ids: list[str | None] = []
        self.table_captions: list[str] = []
        #: One entry per top-level chrome region: its whole-space-normalized
        #: text, in reading order.
        self.chrome_regions: list[str] = []
        self._caption_depth = 0
        self._caption_parts: list[str] = []
        self._chrome_depth = 0
        self._chrome_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.image_asset_ids.append(dict(attrs).get("data-webshot-asset-id"))
        elif tag == "table":
            self.table_captions.append("")
        elif tag == "caption" and self.table_captions:
            self._caption_depth += 1
            self._caption_parts = []
        if tag in _CHROME_TAGS:
            if not self._chrome_depth:
                self._chrome_parts = []
            self._chrome_depth += 1

    def handle_endtag(self, tag: str) -> None:
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
#: use: the first opens a sub/superscript's text that touches the text before
#: it, the second closes one that touches the text after it.
JOINED_BEFORE = "\ufdd0"
JOINED_AFTER = "\ufdd1"
_JOIN_MARKS = (JOINED_BEFORE, JOINED_AFTER)
#: A marker with the space the backend put beside it, which the source never
#: had: a marker is written only where no whitespace separated the two.
_JOIN_MARK_RE = re.compile(f" ?{JOINED_BEFORE}|{JOINED_AFTER} ?")

#: Stands in for a part's text, to read what a serializer wraps text in.
_WRAPPER_PROBE = "\x00"

_SCRIPT_TAGS = frozenset({"sub", "sup"})
#: Elements a line of text runs through. Text either side of one is one run,
#: so a sub/superscript can touch it; any other element (a block, a line
#: break, an image) separates the two.
_INLINE_TAGS = _SCRIPT_TAGS | frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "big",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    }
)


class _ScriptJoins(HTMLParser):
    """Where each `<sub>`/`<sup>` touches the text beside it, as insertions.

    A script whose text starts with no whitespace between it and the text
    before it gets `JOINED_BEFORE` at the start of its text; one whose text
    ends with no whitespace before the text after it gets `JOINED_AFTER` at
    the end. Offsets are into the parsed string, and a marker goes inside the
    script's innermost text, never into a text node of its own, which the
    backend would make an item of.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self.marks: set[tuple[int, str]] = set()
        self._last = ""  # the run's last character so far; "" after a separator
        #: Per open script: the character it follows, and where its text
        #: starts with which character, once it has any.
        self._open: list[tuple[str, tuple[int, str] | None]] = []
        self._text_open = False
        self._text_end = 0
        #: Where a closed script's text ended, until the next character says
        #: whether the script touches it.
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._event()
        if tag in _SCRIPT_TAGS:
            self._open.append((self._last, None))
        elif tag not in _INLINE_TAGS:
            self._separate()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._event()
        if tag not in _INLINE_TAGS:
            self._separate()

    def handle_endtag(self, tag: str) -> None:
        self._event()
        if tag in _SCRIPT_TAGS and self._open:
            before, first = self._open.pop()
            if first is None:
                return
            offset, character = first
            if before and not before.isspace() and not character.isspace():
                self.marks.add((offset, JOINED_BEFORE))
            if self._last and not self._last.isspace():
                self._after_at = self._text_end
        elif tag not in _INLINE_TAGS:
            self._separate()

    def handle_data(self, data: str) -> None:
        offset = self._event()
        if not data:
            return
        if self._after_at is not None:
            if not data[0].isspace():
                self.marks.add((self._after_at, JOINED_AFTER))
            self._after_at = None
        if self._open:
            self._open = [
                (before, first or (offset, data[0])) for before, first in self._open
            ]
            self._text_open = True
        self._last = data[-1]

    def handle_comment(self, data: str) -> None:
        self._event()


def mark_script_joins(html: str) -> str:
    """`html` with each sub/superscript's touching edges marked for the backend.

    The backend normalizes every text node with `" ".join(text.split())`,
    which drops the one fact the serializers would need: whether "H" and its
    "0" had whitespace between them. Characters survive it, so the fact
    travels as one, and `extract` removes every marker once the document is
    built. A marked page opens with a byte-order mark: given bytes with no
    declared encoding, the backend's charset detection read the markers' bytes
    as cp775 or cp949 and garbled the text around them.
    """
    parser = _ScriptJoins(html)
    parser.feed(html)
    parser.close()
    if not parser.marks:
        return html
    marked = html
    for offset, mark in sorted(parser.marks, reverse=True):
        marked = marked[:offset] + mark + marked[offset:]
    return "\ufeff" + marked


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
        PictureDescriptionData,
        PictureMiscData,
        Script,
        TextItem,
    )
    from docling_core.types.doc.labels import DocItemLabel

    extraction_warnings: list[str] = []

    # The <title> tag would become a second, metadata-derived title item; the
    # page's own headings are the record. The element's shape is owned by
    # standalone_document, which defines the regex next to itself (the tag is
    # ours — body markup cannot contain <title>, it is not in the sanitizer's
    # allowlist).
    docling_html = TITLE_ELEMENT_RE.sub("", sanitized_html, count=1)
    # A page that carries the markers itself would have its own characters
    # read as joins and removed, so it keeps the backend's spacing instead.
    markable = not any(mark in docling_html for mark in _JOIN_MARKS)
    if not markable:
        extraction_warnings.append(
            "the page contains U+FDD0 or U+FDD1, the characters WebShot uses to "
            "keep a subscript or superscript joined to the text it touches, so "
            "content.md, content.txt and chunks.jsonl separate each one from "
            "its neighbours with a space (docs/09 P14-56)."
        )
    docling_input = docling_html.encode("utf-8")
    try:
        # The input document is the page as given, so `content.json`'s origin
        # hash is the page's; the backend parses the marked copy.
        in_doc = InputDocument(
            path_or_stream=BytesIO(docling_input),
            format=InputFormat.HTML,
            backend=HTMLDocumentBackend,
            filename="content.html",
        )
        parsed = mark_script_joins(docling_html) if markable else docling_html
        document: DoclingDocument = HTMLDocumentBackend(
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
    # the body layer.
    for item in document.texts:
        if item.content_layer == ContentLayer.FURNITURE and not facts.is_chrome_text(
            item.text
        ):
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
        text it touches, and each stretch of one wrapping is wrapped once. Wrapping
        each part on its own gives "**(H**0" or "`a``1`", where no reader sees
        one word.
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
        """docling's inline group, without the space it puts beside a sub/superscript.

        docling-core joins an inline group's parts with a space. Where the
        source had none, beside a sub/superscript, the parts join with
        nothing, so "H<sub>0</sub>" reads "H0" in content.md, content.txt and
        chunks.jsonl, as it does in the PDF's text layer (docs/09 P14-56). A
        group with no such join is joined exactly as docling joins it.
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

    class _TableProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
            # Full-fidelity table text: the default triplet serialization drops
            # row-header cells, which chunk coverage would rightly flag.
            return _unpadded(
                ChunkingDocSerializer(
                    doc=doc,
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
        language.
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
                "a subscript or superscript's join marker reached a field WebShot "
                f"does not rejoin ({', '.join(leaked)}); it was removed there, and "
                "that text may separate the script from its neighbours with a "
                "space (docs/09 P14-56)."
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
        pictured_asset_ids=[asset.id for _, asset in matched],
        warnings=extraction_warnings,
    )


def validate_document_json(document_json: str) -> None:
    """Prove `content.json` loads as a DoclingDocument (acceptance G3)."""
    from docling_core.types.doc.document import DoclingDocument

    DoclingDocument.model_validate(json.loads(document_json))
