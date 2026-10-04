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
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
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
        MarkdownTableSerializer,
    )
    from docling_core.transforms.serializer.plain_text import PlainTextDocSerializer
    from docling_core.types.doc.document import (
        ContentLayer,
        DocItem,
        DoclingDocument,
        PictureDescriptionData,
        PictureMiscData,
    )
    from docling_core.types.doc.labels import DocItemLabel

    # The <title> tag would become a second, metadata-derived title item; the
    # page's own headings are the record. The element's shape is owned by
    # standalone_document, which defines the regex next to itself (the tag is
    # ours — body markup cannot contain <title>, it is not in the sanitizer's
    # allowlist).
    docling_input = TITLE_ELEMENT_RE.sub("", sanitized_html, count=1).encode("utf-8")
    try:
        in_doc = InputDocument(
            path_or_stream=BytesIO(docling_input),
            format=InputFormat.HTML,
            backend=HTMLDocumentBackend,
            filename="content.html",
        )
        document: DoclingDocument = HTMLDocumentBackend(
            in_doc=in_doc, path_or_stream=BytesIO(docling_input)
        ).convert()
    except Exception as exc:
        raise BundleBuildError(f"docling could not parse the snapshot: {exc}") from exc

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

    extraction_warnings: list[str] = []

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

    class _TableProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
            # Full-fidelity table text: the default triplet serialization drops
            # row-header cells, which chunk coverage would rightly flag.
            return _unpadded(
                ChunkingDocSerializer(
                    doc=doc,
                    table_serializer=MarkdownTableSerializer(),
                    annotation_serializer=_MarkedOcr(),
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
                MarkdownDocSerializer(doc=document, annotation_serializer=_MarkedOcr())
            )
            .serialize()
            .text
        )
        text = (
            _unpadded(
                PlainTextDocSerializer(doc=document, annotation_serializer=_MarkedOcr())
            )
            .serialize()
            .text
        )
        doctags = _doctags()

    return ExtractionResult(
        document_json=dump_json(document_dict),
        markdown=markdown.rstrip() + "\n",
        text=text.rstrip() + "\n",
        doctags=doctags.rstrip() + "\n",
        tables=[
            [[cell.text for cell in row] for row in table.data.grid]
            for table in document.tables
        ],
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
