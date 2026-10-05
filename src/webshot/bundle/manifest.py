"""Contract models for the artifacts WebShot publishes, and their JSON Schemas.

The current contracts are the web-path bundle at `bundle_format` 3, the
protected-viewer bundle (`schema_version` 1.1), the `--report` QA report
(`schema_version` 2) and the journey outline.  The web-path models of v2.2
(`schema_version` 1.0) come first in this file because they were written first,
before any v3 change, as an executable definition of "unchanged output"; they
stay to validate the `legacy/` artifacts until `--legacy-bundle` is removed at
v3.1.  Every contract is published as a JSON Schema so that consumers can depend
on it rather than on WebShot's implementation details (docs/04-spec.md §7).

Every model forbids unknown fields on purpose: a silently added or renamed key
is exactly the kind of drift this freeze is meant to catch.  Widening the
contract is a deliberate act — change the model, regenerate `schemas/`, and say
so in the pull request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializerFunctionWrapHandler,
    model_serializer,
)

WEB_SCHEMA_VERSION = "1.0"
VIEWER_SCHEMA_VERSION = "1.1"


class _Contract(BaseModel):
    """Base for every contract model: unknown keys are a contract violation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _relative_or_absent(schema: dict[str, Any]) -> None:
    """Publish `local_paths` as the one string it can be, never as a null.

    The writers put the key in only when a capture recorded its local paths
    relative (`--local-paths relative`, docs/09 P14-61), so that a capture
    which did not ask is byte for byte what it always was. A null is therefore
    a document no producer emits, and the schema should not bless one (the
    same reasoning as `QaReport.error`, docs/09 P5-10).
    """
    schema.pop("anyOf", None)
    schema.pop("default", None)
    schema["type"] = "string"
    schema["const"] = "relative"


def _true_or_absent(schema: dict[str, Any]) -> None:
    """Publish an opt-in gate as `true` or not at all, never as `false`.

    The report writes the key only when the gate was on, so a run without it
    is byte for byte what it was before the gate existed, and the schema should
    not bless a `false` no producer emits (as `_relative_or_absent`).
    """
    schema.pop("default", None)
    schema["type"] = "boolean"
    schema["const"] = True


# --------------------------------------------------------------------------- #
# Web path — content.json
# --------------------------------------------------------------------------- #


class PageMetadata(_Contract):
    title: str
    language: str
    description: str
    author: str
    keywords: str
    # These two fields keep the camelCase spelling of the emitted JSON.
    canonicalUrl: str
    publishedTime: str


class _Block(_Contract):
    id: str
    locator: str
    text: str


class HeadingBlock(_Block):
    type: Literal["heading"]
    level: int


class ParagraphBlock(_Block):
    type: Literal["paragraph"]


class BlockquoteBlock(_Block):
    type: Literal["blockquote"]


class CodeBlock(_Block):
    type: Literal["code"]


class ListBlock(_Block):
    type: Literal["list"]
    ordered: bool
    items: list[str]


class DefinitionEntry(_Contract):
    term: str
    definition: str


class DefinitionsBlock(_Block):
    type: Literal["definitions"]
    entries: list[DefinitionEntry]


class TableCell(_Contract):
    text: str
    header: bool
    colspan: int
    rowspan: int


class TableBlock(_Block):
    type: Literal["table"]
    caption: str
    rows: list[list[TableCell]]


class FigureBlock(_Block):
    type: Literal["figure"]
    caption: str
    assetIds: list[str]


class FormValueBlock(_Block):
    type: Literal["form-value"]
    name: str
    value: str


class MediaTrack(_Contract):
    kind: str
    language: str
    label: str
    url: str


class EmbeddedMediaBlock(_Block):
    type: Literal["embedded-media"]
    mediaType: str
    sourceUrl: str
    title: str
    tracks: list[MediaTrack]


ContentBlock = Annotated[
    HeadingBlock
    | ParagraphBlock
    | BlockquoteBlock
    | CodeBlock
    | ListBlock
    | DefinitionsBlock
    | TableBlock
    | FigureBlock
    | FormValueBlock
    | EmbeddedMediaBlock,
    Field(discriminator="type"),
]


class Link(_Contract):
    text: str
    url: str


class AssetOcr(_Contract):
    text: str
    confidence: float | None
    language: str | None
    engine: str | None


class VisualAssetRecord(_Contract):
    id: str
    file: str
    kind: str
    width: float
    height: float
    alt: str
    aria_label: str
    title: str
    caption: str
    source_url: str
    nearby_heading: str
    ocr: AssetOcr
    sha256: str


class BundleVisualAssetRecord(VisualAssetRecord):
    """A visual asset in v3's `assets.json`: v2's record, and one field more."""

    #: For an iframe, which path its content took (docs/04-spec.md §5.6):
    #: `dom` when its document was read into content.html, so its OCR repeats
    #: text the bundle already holds, and `screenshot` when this asset is all
    #: the bundle holds of it. Null on every other kind of asset. Required, so a
    #: record that lacks it is refused rather than read as "not a frame".
    frame_content: Literal["dom", "screenshot"] | None


class WebContent(_Contract):
    """`content.json` on the web path."""

    schema_version: Literal["1.0"]
    source: str
    metadata: PageMetadata
    blocks: list[ContentBlock]
    links: list[Link]
    visual_assets: list[VisualAssetRecord]


# --------------------------------------------------------------------------- #
# Web path — chunks.jsonl
# --------------------------------------------------------------------------- #


class TextChunk(_Contract):
    id: str
    source: str
    heading_path: list[str]
    block_ids: list[str]
    text: str
    sha256: str


class VisualChunk(_Contract):
    id: str
    type: Literal["visual"]
    source: str
    heading_path: list[str]
    asset_ids: list[str]
    text: str
    sha256: str


class WebChunk(RootModel[VisualChunk | TextChunk]):
    """One line of `chunks.jsonl` on the web path."""


# --------------------------------------------------------------------------- #
# Web path — manifest.json
# --------------------------------------------------------------------------- #


class SourceResponse(_Contract):
    http_status: int | None
    content_type: str


class WebPdfRecord(_Contract):
    file: str
    pages: int
    bytes: int
    sha256: str
    tagged: bool
    bookmarks: bool
    #: Bundle files carried inside the PDF as associated files. Empty when
    #: `--no-embed-bundle` was used, so "the PDF is a rendering" and "the PDF
    #: is the whole deliverable" are distinguishable from the manifest alone.
    embedded_files: list[str] = []


class VideoAppendixRecord(_Contract):
    """The appended walkthrough pages, and the one thing not to claim of them.

    `tagged` is always false: pypdf's merge does not carry a structure tree
    across (docs/09 S8). The capture's own pages keep theirs, which is what
    `WebPdfRecord.tagged` describes — this says the appended ones have none,
    so the two claims cannot be confused.
    """

    pages: int
    starts_on_page: int
    videos: int
    steps: int
    tagged: Literal[False]


class WebContentCounts(_Contract):
    blocks: int
    tables: int
    links: int
    chunks: int
    text_characters: int
    visual_assets: int


class OcrSettings(_Contract):
    enabled: bool
    language: str
    page_segmentation_mode: int
    engine: str | None


class FileRecord(_Contract):
    bytes: int
    sha256: str


class WebManifest(_Contract):
    """`manifest.json` on the web path."""

    schema_version: Literal["1.0"]
    generator: str
    generator_version: str
    captured_at: str
    source: str
    final_url: str
    title: str
    source_response: SourceResponse
    pdf: WebPdfRecord
    content: WebContentCounts
    ocr: OcrSettings
    files: dict[str, FileRecord]
    warnings: list[str]
    ocr_notice: str


# --------------------------------------------------------------------------- #
# Web path, bundle_format 3 (Phase 2) — the current contract
# --------------------------------------------------------------------------- #

BUNDLE_FORMAT = 3


class VideoChunkProvenance(_Contract):
    """Where a video chunk came from, completely enough to cite it.

    Every field is here so that "this came from video X, step 3, at 0:42" is
    read off the record rather than reconstructed: without the timestamp a
    claim cannot be checked against the video, and without the playbook id it
    cannot be traced to *which* video on a page that had several.
    """

    provider: Literal["guidde"]
    playbook_id: str
    title: str
    step: int
    steps: int
    start_seconds: float
    end_seconds: float
    #: The same moment as a reader cites it: `0:42`.
    timestamp: str
    #: The annotated still for this step, or null if it could not be fetched.
    screenshot: str | None
    source_url: str
    #: The bundle-relative file `meta.doc_items[0]` points into.
    #:
    #: A video chunk's anchor cannot resolve in `content.json` the way a page
    #: chunk's does — the walkthrough is not part of the captured document —
    #: so the pointer needs its document named beside it. Together they address
    #: exactly one step, and they stay unambiguous on a page carrying several
    #: walkthroughs, where `#/steps/0` alone would not (docs/09 P8-23).
    steps_document: str


class ChunkMediaProvenance(_Contract):
    """Which media element a chunk came from, and whether it carries its words.

    On an `untranscribed_media` chunk it carries none of the media's content —
    that is the point. A corpus that cannot describe its own gaps invites
    confident answers built on absent material, so retrieval returns a record
    saying the media exists and its words are not here, rather than returning
    nothing.

    On a `media_transcript` chunk it carries the opposite claim, and the extra
    fields exist so the chunk can be trusted alone: a retrieved fragment is the
    whole context its consumer has, and "who wrote these words, in what
    language, and when in the media do they happen" must be answerable without
    leaving it.
    """

    provider: Literal["media-element"]
    media_kind: Literal["video", "audio"]
    title: str
    source_url: str
    transcribed: bool
    duration_seconds: float | None
    #: The four below are present on `media_transcript` chunks only.
    #: `authored` — the publisher shipped these words as a caption file. Never
    #: a machine transcript (`asr`), which is a different claim about the same
    #: text and one this repository does not produce (docs/13, L3).
    transcript_provenance: Literal["authored"] | None = None
    track_kind: str | None = None
    language: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None


class JourneyChunkPath(_Contract):
    """Where a chunk sits in the journey that produced it.

    All five levels by name plus the module's synthetic id, so a retrieved
    fragment knows where it belongs without a second lookup (docs/13). Two
    modules in different groups can carry the same heading, and a chunk without
    this is indistinguishable from the other one's.
    """

    journey: str
    journey_id: str
    section: str
    track: str
    group: str
    module: str
    node_id: str


class ChunkMetaV3(_Contract):
    """Spec §2: provenance into content.json's DoclingDocument."""

    #: RFC 6901 JSON Pointers, each resolvable in a document this record
    #: names: `content.json` for `text`/`table`/`figure`, and the file in
    #: `video.steps_document` for `video_step`. "Resolvable" is the contract —
    #: video chunks once emitted `#/videos/<id>/steps/<n>`, which was 1-based
    #: and rooted at a key `steps.json` does not have, so the same field meant
    #: an address on one chunk kind and nothing on another (docs/09 P8-23).
    doc_items: list[str] = Field(min_length=1)
    headings: list[str]
    #: `video_step` is one step of an embedded walkthrough; its `doc_items`
    #: anchor into the `videos/<id>/steps.json` named by `video.steps_document`
    #: rather than into content.json.
    kind: Literal[
        "text",
        "table",
        "figure",
        "video_step",
        "untranscribed_media",
        "media_transcript",
    ]
    # Real page numbers exist on the protected path only; the web path has no
    # print-pagination mapping from the DOM (docs/02 §Data contracts).
    page: int | None
    # The leading DocItem self_ref: a stable anchor into content.json. v2's
    # CSS-path DOM anchor is not derivable from the docling model — recorded
    # as a Phase 2 correction (docs/09).
    locator: str
    sha256: str
    #: Present only on a chunk produced by a journey walk. **Optional, and it
    #: stays optional**: P8-62 is the entry about an additive field made
    #: required while the version stood still, which invalidated every bundle
    #: written before it. A page captured on its own belongs to no journey and
    #: omits the key entirely, which is why every existing bundle is unchanged.
    journey: JourneyChunkPath | None = None
    #: Present on `video_step` chunks only, and absent — not null — on every
    #: other kind, so a page with no video produces exactly the chunks it
    #: always did.
    video: VideoChunkProvenance | None = None
    #: Present on `untranscribed_media` chunks only, and absent elsewhere.
    media: ChunkMediaProvenance | None = None


class WebChunkV3(_Contract):
    """One line of `chunks.jsonl` in bundle_format 3."""

    id: str
    text: str
    meta: ChunkMetaV3


class VideoAssetRecord(_Contract):
    """One file downloaded from an embedded video's host."""

    id: str
    file: str
    kind: Literal["step-screenshot", "step-clip", "source-recording"]
    playbook_id: str
    step: int | None
    source_url: str
    bytes: int
    sha256: str
    width: int | None
    height: int | None
    #: Set when this record points at a file another video's download produced.
    #: One file, two references — the dedupe's visible half.
    shared_with: str | None = None


class FormFieldRecord(_Contract):
    """A form field's captured state; password values arrive pre-redacted."""

    name: str
    value: str
    locator: str


class MediaTrackRecord(_Contract):
    kind: str
    language: str
    label: str
    url: str


class EmbeddedMediaRecord(_Contract):
    media_type: str
    source_url: str
    title: str
    tracks: list[MediaTrackRecord]
    locator: str


class AssetsFile(_Contract):
    """`assets.json`: typed records for everything that is not document prose.

    The visual-asset records are the shape v2 embedded in content.json, plus
    `frame_content`.
    Form fields and embedded media live here because docling's HTML backend
    does not model them (docs/02 fidelity mapping — nothing vanishes
    silently); definition lists are absent because docling models them
    natively as list groups.
    """

    bundle_format: Literal[3]
    visual_assets: list[BundleVisualAssetRecord]
    form_fields: list[FormFieldRecord]
    embedded_media: list[EmbeddedMediaRecord]
    #: Files fetched from an embedded video's own host — step screenshots, and
    #: the clips `--video-assets` adds. Deliberately not merged into
    #: `visual_assets`: those are visuals harvested from the page and matched
    #: to the document model by position, and appending to that list would
    #: break the count check the extraction bridge depends on.
    video_assets: list[VideoAssetRecord] | None = None


class ToolVersions(_Contract):
    """What actually produced the bundle (docs/04 §2.2)."""

    webshot: str
    playwright: str
    chromium: str
    #: Phase 3: MarkItDown reads every non-Markdown local format and
    #: Trafilatura chooses the content region, so both shape what a bundle
    #: contains and both belong in its provenance.
    markitdown: str
    trafilatura: str
    docling_slim: str
    docling_core: str
    chonkie: str
    nh3: str
    tesseract: str | None


class WebContentCountsV3(_Contract):
    items: int
    tables: int
    links: int
    chunks: int
    text_characters: int
    visual_assets: int
    #: Whitespace-separated tokens recognized inside this capture's visual
    #: assets. The protected path's field of the same name counts Tesseract's
    #: word *boxes* instead — the same idea measured two ways, because the two
    #: paths recognize different things (pixels in a page image versus text in
    #: a harvested image). Comparable within a path, not across one.
    ocr_words: int


class OcrSettingsV3(_Contract):
    """`manifest.ocr` in bundle_format 3.

    Two fields are nullable here and were not in v2, because v2 had one
    engine. `--ocr-engine rapid` selects its own models and has no
    page-segmentation mode, so under it a manifest that reported the requested
    `--ocr-language`/`--ocr-psm` would be claiming settings that were never
    applied (docs/09 P3-7). Null means "this engine does not take it".
    """

    enabled: bool
    language: str | None
    page_segmentation_mode: int | None
    engine: str | None


class FileRecordV3(_Contract):
    bytes: int
    sha256: str
    # Present (and true) only on legacy/* files emitted by --legacy-bundle.
    legacy: bool | None = None


class WalkthroughVideoRecord(_Contract):
    """A video whose content reached the bundle, and how it got there."""

    provider: Literal["guidde"]
    playbook_id: str
    title: str
    source_url: str
    duration_seconds: float
    steps: int
    narrated_steps: int
    transcript_cues: int
    organization: str
    language: str
    last_updated_by: str
    #: Where in the bundle this video's own artifacts are.
    directory: str
    assets: int
    chunks: int
    locator: str | None
    transcribed: Literal[True]
    #: How the text was produced. `authored` means a person wrote it and the
    #: audio was generated from it — the opposite of a machine transcript, and
    #: the thing a consumer needs to know before quoting it.
    #:
    #: `unverified` is the answer for every other narration type Guidde can
    #: carry. `extract/guidde.py` already recognised those and called them
    #: "unverified territory", but the only place that reached the manifest was
    #: the prose in `narration`; this field was the literal `"authored"` on
    #: every walkthrough. A consumer filtering on the structured field — which
    #: is what a structured field is for — was told the subtitles were
    #: authoritative in exactly the case the parser had flagged. The contract
    #: needs a way to say so, so it has one (docs/09 P8-18).
    transcript_provenance: Literal["authored", "unverified"]
    narration: str


class CaptionTrackRecord(_Contract):
    """One `<track>` a media element declared, and what came of reading it.

    `role` is stated rather than left to be inferred from `kind`: a subtitle
    track kept in the corpus is a translation of the speech, not a record of
    it, and a reader that treated the two alike would quote a translation as
    if it were what was said.
    """

    kind: Literal["subtitles", "captions", "descriptions", "chapters", "metadata", ""]
    srclang: str | None
    label: str | None
    source_url: str | None
    #: False when the browser could not parse the track — a cross-origin file
    #: under opaque-origin rules, a 404, a malformed body. The distinction
    #: matters: an unreadable track is content this bundle is missing, while
    #: an absent track is content the page never offered.
    readable: bool
    cues: int
    #: True when the read budget cut this track short of its own end. The
    #: cue count alone cannot say so — a reader would have to know the budget
    #: and compare against it — and a partial transcript that publishes as
    #: complete is worse than an absent one: absent, nobody quotes it as the
    #: whole of what was said.
    truncated: bool = False
    #: How many cues the track had before the budget. Equal to `cues` on any
    #: track that was not cut, give or take the blank-text cues dropped on the
    #: way in, which is why `truncated` is stated rather than derived from it.
    total_cues: int = 0
    role: Literal[
        "transcript",
        "translation",
        "unreadable",
        "empty",
        "descriptions",
        "chapters",
        "metadata",
    ]


class MediaVideoRecord(_Contract):
    """Timed media the bundle could not document, recorded rather than omitted.

    Everything knowable without playing it, plus `note` saying in plain words
    what is missing. Media an agent cannot hear is worse unlabelled than
    labelled: unlabelled, a bundle reads as complete when it is not.
    """

    provider: Literal["media-element"]
    #: `video` or `audio`. Audio counts: the question this record answers is
    #: what spoken content the bundle does not have, and an audio briefing is
    #: nothing but spoken content.
    media_kind: Literal["video", "audio"]
    title: str
    source_url: str
    duration_seconds: float | None
    poster_url: str | None
    poster_asset: str | None
    #: Kept as it was: one human-readable line per track. `tracks` says the
    #: same thing in fields, and a reader that needs to tell a transcript from
    #: a translation must use that rather than parse these.
    caption_tracks: list[str]
    tracks: list[CaptionTrackRecord]
    #: `videos/media-NN`, where this element's caption artifacts were written.
    #: Present only when at least one track was readable and had cues — a
    #: record that named a directory the bundle does not contain would be one
    #: more file an agent is sent to and does not find.
    directory: str | None = None
    locator: str | None
    #: True once a readable `captions` track supplied the words. A media
    #: element is no longer transcribed only by a walkthrough provider, which
    #: is why the union below no longer discriminates on this field.
    transcribed: bool
    #: `authored` — a caption file the publisher shipped. The same value a
    #: walkthrough's own steps carry, and distinct from a machine transcript
    #: (`asr`), which this repository does not produce (docs/13, L3).
    transcript_provenance: Literal["authored"] | None
    #: Present exactly when `transcribed` is false: what is missing and why.
    note: str | None


#: Discriminated on `provider`. It was `transcribed` while that field was a
#: proxy for the provider — a media element could only ever be untranscribed.
#: Caption path 1 ended that: a plain `<video>` with a readable `captions`
#: track is transcribed and is still a media element, so the two axes came
#: apart and the discriminator had to move to the one that still separates
#: the shapes.
VideoRecord = Annotated[
    WalkthroughVideoRecord | MediaVideoRecord, Field(discriminator="provider")
]


class VideoTally(_Contract):
    """How many videos of each kind. The per-page half of counting a corpus.

    The three parts are exhaustive and sum to `total`. They have to: a reader
    subtracting two of them from the total to derive the third would silently
    absorb any case the tally forgot, which is how a count starts reading as
    authority for something nobody measured (docs/09 P7-8).
    """

    total: int
    documented_walkthroughs: int
    #: A `<video>` or `<audio>` whose own `kind="captions"` track was read.
    #: Zero before caption path 1, when a media element could not be
    #: transcribed at all and `untranscribed_media` was every media element.
    transcribed_media: int
    untranscribed_media: int


class WebPdfRecordV3(WebPdfRecord):
    """`manifest.pdf` in bundle_format 3.

    Its own model rather than a widening of `WebPdfRecord`: that one is the
    *frozen* v2.2 contract, and a field v2 could never produce has no business
    appearing in the schema v2 consumers validate against.
    """

    #: The walkthrough pages appended after the capture's own. Absent when the
    #: page had no video, which is every capture this feature does not touch.
    video_appendix: VideoAppendixRecord | None = None


class DiscoveredMetadata(_Contract):
    """What Trafilatura read from the captured page (docs/05 task 3.4).

    Deliberately separate from `page_metadata`, which is the DOM's own
    declarations as the snapshot recorded them: a consumer that needs to know
    where a value came from can tell the two apart. Empty strings mean the
    page declared nothing — WebShot does not infer (docs/09 P3-3).
    """

    title: str
    author: str
    date: str
    sitename: str


class ContentDiscovery(_Contract):
    """How the captured content region was chosen, and what it was."""

    strategy: Literal["explicit", "trafilatura", "static-selectors", "full-page"]
    selector: str | None


class WebManifestV3(_Contract):
    """`manifest.json` in bundle_format 3."""

    bundle_format: Literal[3]
    generator: str
    generator_version: str
    captured_at: str
    source: str
    final_url: str
    #: `relative` when `source`, `final_url` and every local file URL the
    #: bundle records are written relative to the source file's own directory
    #: (`./page.html`) rather than as this machine's absolute `file:` URLs.
    #: Absent otherwise. docs/09 P14-61.
    local_paths: Literal["relative"] | None = Field(
        default=None, json_schema_extra=_relative_or_absent
    )
    title: str
    # The page's own metadata as the DOM declared it. v2 carried this inside
    # content.json; the DoclingDocument that replaced it has no slot for DOM
    # meta tags, so the manifest is its format-3 home (same record shape).
    page_metadata: PageMetadata
    #: Additive in Phase 3; every capture carries both (docs/05 task 3.4).
    discovered_metadata: DiscoveredMetadata
    content_discovery: ContentDiscovery
    auth_mode: Literal["none", "storage-state", "auth-profile"]
    source_response: SourceResponse
    pdf: WebPdfRecordV3
    content: WebContentCountsV3
    ocr: OcrSettingsV3
    tool_versions: ToolVersions
    files: dict[str, FileRecordV3]
    warnings: list[str]
    ocr_notice: str
    #: The videos embedded in the page — those documented as walkthroughs and
    #: those recorded as untranscribed. Absent when the page had none, so a
    #: capture of a video-free page is unchanged by this feature existing.
    videos: list[VideoRecord] | None = None
    #: How many videos of each kind. The per-page half of counting a whole
    #: journey's video types: summing these answers it without re-measuring.
    video_tally: VideoTally | None = None


# --------------------------------------------------------------------------- #
# Protected-viewer path
# --------------------------------------------------------------------------- #


class ViewerOcrAlternate(_Contract):
    text: str
    confidence: float | None
    page_segmentation_mode: int | None
    purpose: str


class ViewerOcrWord(_Contract):
    text: str
    confidence: float
    bbox: list[int]
    line: list[int]


class ViewerPageOcr(_Contract):
    text: str
    confidence: float | None
    word_count: int
    language: str
    engine: str
    page_segmentation_mode: int
    tsv: str
    alternate: ViewerOcrAlternate | None
    words: list[ViewerOcrWord]


class ViewerPage(_Contract):
    page: int
    pdf_source_page: int
    image: str
    width: int
    height: int
    sha256: str
    ocr: ViewerPageOcr


class ViewerContent(_Contract):
    """`content.json` on the protected-viewer path."""

    schema_version: Literal["1.1"]
    title: str
    source: str
    pages: list[ViewerPage]


class ViewerChunk(_Contract):
    """One line of `chunks.jsonl` on the protected-viewer path."""

    id: str
    page: int
    kind: str
    text: str
    ocr_confidence: float | None


class ViewerCapture(_Contract):
    """`capture.json`: the protected-viewer policy record.

    Only the invariants WebShot itself writes are fixed; the viewer metadata
    merged in by the capture stage is open-ended by design.
    """

    model_config = ConfigDict(extra="allow")

    method: str
    native_file_downloaded: bool
    download_restriction_respected: bool


class ViewerPdfRecord(_Contract):
    file: str
    pages: int
    source_pages: int
    transcript_appendix_pages: int
    bytes: int
    sha256: str
    searchable_ocr_layer: bool
    # Which OCR engine built the invisible text layer. Phase 1 moved this from
    # WebShot's own drawing code to OCRmyPDF, and a consumer comparing two
    # bundles should be able to see that from the manifest.
    text_layer_engine: str
    # The PDF/A conformance level this file *claims* (`--pdfa`), or null. What
    # it actually conforms to is a veraPDF result, not a manifest field.
    pdfa: str | None
    # False by construction: the source pages are images, and the appendix's
    # structure tree does not survive the merge (docs/09 S8).
    tagged: bool
    bookmarks: bool
    visible_transcript_appendix: bool
    embedded_machine_readable_files: list[str]
    word_coordinates_embedded: bool
    original_visual_pages_first: bool


class ViewerContentCounts(_Contract):
    pages: int
    chunks: int
    text_characters: int
    #: Recognized word *boxes* across the captured pages — Tesseract's own
    #: segmentation, not a whitespace split. See the web path's field of the
    #: same name for why the two are not directly comparable.
    #:
    #: Optional at `schema_version` 1.1, and that is a compatibility rule
    #: rather than a doubt about the value: every bundle WebShot writes today
    #: carries it. Making it *required* while leaving the version at 1.1 meant
    #: every protected-viewer manifest produced before the field existed failed
    #: validation against the schema it still identifies itself as — while the
    #: migration guide called 1.1 unchanged. Additive fields are optional or
    #: the version moves; it cannot be both (docs/09 P8-62).
    ocr_words: int | None = None
    primary_ocr_text_characters: int
    alternate_ocr_pages: int
    ocr_mean_confidence: float | None


class ViewerFileRecord(_Contract):
    file: str
    bytes: int
    sha256: str


class ViewerManifest(_Contract):
    """`manifest.json` on the protected-viewer path."""

    schema_version: Literal["1.1"]
    generator: str
    generator_version: str
    captured_at: str
    title: str
    source: str
    capture: ViewerCapture
    pdf: ViewerPdfRecord
    content: ViewerContentCounts
    files: list[ViewerFileRecord]
    warnings: list[str]


# --------------------------------------------------------------------------- #
# QA report (`--report`)
# --------------------------------------------------------------------------- #


class PdfValidationRecord(_Contract):
    """A `--validate-pdf` result, present only when the gate actually ran.

    `compliant: null` is the one way of saying "not established"; `tool` says
    whether that was because no validator was found or because the file
    declares no conformance level to measure.
    """

    tool: str | None
    flavour: str
    compliant: bool | None
    passed_rules: int | None
    failed_rules: int | None
    failed_checks: int | None
    failures: list[str]
    report_file: str | None
    detail: str


class ResolvedOptions(_Contract):
    """`options` — what the run was actually configured with (docs/04 §2.3).

    "As resolved" means after flags, `WEBSHOT_*`, and `webshot.toml` have been
    reconciled, so a report answers "why did this capture behave that way?"
    without the reader having to reconstruct the precedence.

    Credential-bearing options are deliberately *not* here.  `--storage-state`
    and `--auth-profile` reduce to `auth_mode`: a QA report is a file people
    attach to tickets, and the location of a session store is not something it
    needs to carry (spec §6.1's spirit, applied one artifact wider).
    """

    mode: str
    selector: str | None
    auto_selector: bool
    exclude: list[str]
    wait_for: str | None
    delay_seconds: float
    navigation_timeout_seconds: float
    scroll: bool
    max_scrolls: int
    scroll_delay_seconds: float
    paper_format: str
    landscape: bool
    margin: str
    scale: float
    media: str
    header_footer: bool
    document_title: str | None
    tagged: bool
    outline: bool
    prefer_css_page_size: bool
    protected_viewer: bool
    #: Whether the bundle travels inside the PDF as associated files. A
    #: consumer handed only the PDF can tell from the report whether it should
    #: expect to find the content in there.
    embed_bundle: bool
    embed_assets: bool
    #: `relative` under `--local-paths relative`, and absent otherwise, as in
    #: the manifest. The report's own `source` and `final_url` stay absolute
    #: either way: it describes the run on the machine that made it.
    local_paths: Literal["relative"] | None = Field(
        default=None, json_schema_extra=_relative_or_absent
    )
    css: str | None
    user_agent: str | None
    allow_http_errors: bool
    #: Whether requests to internal addresses were blocked during the capture.
    #: A capture that could not reach an intranet subresource looks different
    #: from one that could, so the report says which it was.
    block_private_requests: bool
    ai_bundle: bool
    #: Whether the walkthroughs of embedded videos were read into the bundle
    #: and the PDF, and whether their media was downloaded too. A reader of a
    #: capture of a video-bearing page needs to know which of the two it is.
    videos: bool
    video_assets: bool
    ai_bundle_directory: str | None
    legacy_bundle: bool
    max_assets: int
    ocr: bool
    #: Whether missing recognition was a failure rather than a warning
    #: (`--require-ocr`). Published because a reader of a *successful*
    #: report needs to know whether this capture's OCR was guaranteed or
    #: merely happened to work — which is the whole question the flag
    #: exists to answer.
    require_ocr: bool
    #: Whether an empty capture was a failure rather than a warning
    #: (`--require-content`, docs/04-spec.md §5 item 14), for the reason
    #: `require_ocr` is published. Present only when it was on.
    require_content: bool = Field(default=False, json_schema_extra=_true_or_absent)
    ocr_language: str
    ocr_page_segmentation_mode: int
    ocr_engine: str
    pdfa: bool
    validate_pdf: str | None
    auth_mode: Literal["none", "storage-state", "auth-profile"]

    @model_serializer(mode="wrap")
    def _drop_unset_later_options(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Serialize `local_paths` and `require_content` only when they say something.

        Absent means the default, so every report written before the options
        existed, and every one written without them, keeps its exact bytes.
        """
        written: dict[str, Any] = dict(handler(self))
        if written.get("local_paths") is None:
            written.pop("local_paths", None)
        if not written.get("require_content"):
            written.pop("require_content", None)
        return written


class ReportCounts(_Contract):
    """`counts` — the spec §2.3 tally, normalized across both capture paths.

    `None` means "this path does not produce that number" rather than zero: the
    protected-viewer bundle has pages and chunks but no links or tables, and a
    `--no-ai-bundle` run has no semantic counts at all.  Zero would read as a
    measurement, and measuring nothing is not the same as measuring none.
    """

    pages: int
    items: int | None
    tables: int | None
    links: int | None
    chunks: int | None
    visual_assets: int | None
    ocr_words: int | None
    text_characters: int | None
    #: Every distinct resource URL the page asked for and did not get, including
    #: requests WebShot's own request policy refused. `failed_requests` beside
    #: `counts` lists the first 20 of them; this is the count (docs/09 P20-6).
    #: Every report WebShot writes now carries it. Optional all the same,
    #: because a report written before it existed says schema v2 as well, and
    #: an additive field is optional or the version moves (docs/09 P8-62).
    failed_requests: int | None = None


class PageCountInvariant(_Contract):
    """spec §2.2's protected-path invariant: pages == source + appendix."""

    expected: int
    actual: int
    source_pages: int
    appendix_pages: int
    passed: bool


class ReportValidation(_Contract):
    """`validation` — every gate the published file went through.

    Only gates that actually ran are reported as run.  `pdfa` is null unless
    `--validate-pdf` was asked for, and `page_count_invariant` is null on the
    web path, which has no appendix to account for.
    """

    pdf_readable: bool
    pdf_pages: int
    page_count_invariant: PageCountInvariant | None
    pdfa: PdfValidationRecord | None


def _error_is_never_null(schema: dict[str, Any]) -> None:
    """Publish `error` as an optional *string*, never a nullable one.

    The field is typed `str | None` so the model can carry "no error", but
    `_drop_absent_error` guarantees the key is either a string or absent — a
    null is a document this producer cannot emit. Left alone, pydantic would
    publish `anyOf: [string, null]` with `default: null`, which blesses exactly
    the shape the serializer exists to prevent and forces every consumer to
    handle a value it will never see (docs/09 P5-10).
    """
    schema.pop("anyOf", None)
    schema.pop("default", None)
    schema["type"] = "string"


class QaReport(_Contract):
    """The JSON written by `--report` — schema v2 (docs/04-spec.md §2.3).

    v2 keeps every v2.2 key except `ai_summary`, whose two path-dependent
    shapes are replaced by the typed `counts` above, and turns `validation`
    from an optional veraPDF record into the full gate report.  It adds
    `options`, `timings`, `exit_code`, and `manifest_sha256`.  The rename is
    what the version bump is for; docs/migration-v2-to-v3.md lists it.
    """

    schema_version: Literal["2"]
    source: str
    #: Null on a run that failed before the value was known. Phase 5.1 made
    #: `--report` write a report for a failed capture too (spec §3), and the
    #: fields a failure never reached say so with null rather than with a
    #: plausible-looking zero. On a successful capture every one of them is
    #: populated exactly as it was in Phase 4 — this widening adds no key.
    final_url: str | None
    output: str | None
    title: str | None
    pages: int | None
    bytes: int | None
    http_status: int | None
    content_type: str
    duration_seconds: float
    #: Wall-clock seconds per pipeline stage: acquire, capture, extract,
    #: render, package, validate. A stage a run never entered is absent.
    timings: dict[str, float]
    captured_at: str
    content_selector: str | None
    source_kind: str | None
    ai_bundle: str | None
    counts: ReportCounts | None
    #: Null only when the run failed before the settings layers resolved — a
    #: bad `webshot.toml`, or an option combination the model refuses.
    options: ResolvedOptions | None
    validation: ReportValidation | None
    #: The first 20 distinct URLs that failed; `counts.failed_requests` counts
    #: them all.
    failed_requests: list[str]
    warnings: list[str]
    #: SHA-256 of the published `manifest.json`, which is the one bundle file
    #: the manifest's own checksum table cannot cover (spec §2.2). Null when the
    #: run published no bundle.
    manifest_sha256: str | None
    #: The code this run exits with, from the spec §3 taxonomy.
    exit_code: int
    #: Why the run failed, and **absent entirely when it did not** — the same
    #: way `timings` omits a stage a run never entered. Its presence is the
    #: machine-readable "this capture did not happen"; a null on every
    #: successful report would turn that into something a consumer has to
    #: interpret (docs/09 P5-2).
    error: str | None = Field(default=None, json_schema_extra=_error_is_never_null)

    @model_serializer(mode="wrap")
    def _drop_absent_error(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Serialize `error` only when there is one.

        On the model rather than in the writer, because it is a property of the
        *contract*: any caller that dumps a `QaReport` — the `--report` writer
        today, anything else tomorrow — has to produce the same JSON, or
        "presence is the failure signal" holds only where someone remembered to
        strip the null (docs/09 P5-9).

        Not `exclude_none`: every other nullable field here means something
        specific by being null. `counts: null` says a capture path produced no
        numbers, and dropping it would say nothing at all.

        One pydantic limitation to know about: a wrap serializer erases the
        *serialization-mode* JSON Schema (`model_json_schema(mode=
        "serialization")` returns `{}`), because pydantic cannot see through
        the callable. `render_schema` publishes the **validation** schema, which
        is correct and is what `schemas/qa-report.schema.json` holds — and
        `tests/test_contracts.py` asserts the `error` property in it stays a
        plain optional string, so the contract cannot drift back to nullable
        (docs/09 P5-10).
        """
        written: dict[str, Any] = dict(handler(self))
        if written.get("error") is None:
            written.pop("error", None)
        return written


# --------------------------------------------------------------------------- #
# JSON Schema export
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Journey path — the enumeration manifest
# --------------------------------------------------------------------------- #


class JourneyNodeV1(_Contract):
    """One node of an enumerated journey (docs/13, five levels — P9-1).

    `listed` is what the application said this node held when it was read a
    second time; `children` is what the walk actually recorded. Both are
    published because the capture pass is required to compare them per node,
    and a consumer handed only a total cannot (docs/13).
    """

    node_id: str
    level: Literal["journey", "section", "track", "group", "module"]
    ordinal: int
    title: str
    #: The group's own description. Groups only — it exists at no other level.
    prose: str | None = None
    #: Modules only: `article` or `assessment`. The capture stage MUST refuse to
    #: open an assessment (docs/13), so the enumeration has to name which ones
    #: they are.
    module_type: str | None = None
    #: Modules only, as authored. Known to disagree with the artifact (P9-8).
    duration: str | None = None
    listed: int | None = None
    children: list[JourneyNodeV1] = []


class JourneyOutlineV1(_Contract):
    """`outline.json` — what a `--dry-run` enumeration of one journey produced.

    This is the contract the capture pass is checked against, which is why it
    is a published schema before any capture exists to read it: a module in
    this manifest and not captured is an error, and one captured but absent
    here is an error, so its shape has to be fixed first.
    """

    outline_format: Literal[1]
    generator: str
    generator_version: str
    enumerated_at: str
    source: str
    journey_id: str
    title: str
    #: Read before and after the walk. docs/13 makes a completed journey a
    #: precondition, so these must agree; the walk refuses to publish if not.
    progress_before: str
    progress_after: str
    #: How many modules the walk recorded, so a consumer can check the tree it
    #: was handed without walking it.
    module_count: int
    #: Tri-state, deliberately. `[]` is the *result* of a check that ran and
    #: found nothing; a non-empty list means the walk raced the application and
    #: nothing keyed on these ids can be trusted; and **`null` means the check
    #: did not run** (`--no-verify-determinism`). Publishing `[]` for the third
    #: case would make a skipped check indistinguishable from a passed one,
    #: which is the defect shape this repository keeps meeting.
    determinism_findings: list[str] | None
    #: Per-node count disagreements, from **both** walks, empty when every node
    #: agreed. In the artifact rather than only in the log, so a consumer
    #: holding this file can tell an outline that failed its own assertions
    #: from one that passed them.
    count_findings: list[str]
    root: JourneyNodeV1
    warnings: list[str]


SCHEMAS: dict[str, type[BaseModel]] = {
    # bundle_format 3 — the current web-path contract. There is deliberately no
    # "content" schema anymore: content.json is a lossless DoclingDocument,
    # whose schema is owned and versioned by docling-core (the manifest records
    # the version used); WebShot proves loadability instead of duplicating it.
    "manifest": WebManifestV3,
    "chunk": WebChunkV3,
    "assets": AssetsFile,
    # The journey walker's stage-1 output (docs/13). Published before the
    # capture that consumes it exists, because it is what that capture is
    # checked against.
    "journey-outline": JourneyOutlineV1,
    # The frozen v2.2 contract, kept until --legacy-bundle is removed at v3.1:
    # it validates the legacy/ artifacts and documents what v2 published.
    "legacy-manifest": WebManifest,
    "legacy-content": WebContent,
    "legacy-chunk": WebChunk,
    "viewer-manifest": ViewerManifest,
    "viewer-content": ViewerContent,
    "viewer-chunk": ViewerChunk,
    "viewer-capture": ViewerCapture,
    "qa-report": QaReport,
}

SCHEMA_ID_PREFIX = "https://github.com/kitterman-t/webshot-ai/schemas"


def json_schema(name: str) -> dict[str, Any]:
    """Build the published JSON Schema for one contract."""
    model = SCHEMAS[name]
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_ID_PREFIX}/{name}.schema.json",
        **model.model_json_schema(),
    }
    return schema


def render_schema(name: str) -> str:
    """Serialize one schema exactly as it is stored in `schemas/`."""
    return json.dumps(json_schema(name), indent=2, ensure_ascii=False) + "\n"


def export_schemas(directory: Path) -> list[Path]:
    """Write every published JSON Schema into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SCHEMAS:
        path = directory / f"{name}.schema.json"
        path.write_text(render_schema(name), encoding="utf-8", newline="\n")
        written.append(path)
    return written
