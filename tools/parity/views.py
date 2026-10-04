"""Read a bundle — either format — into the one shape the parity engine compares.

The engine never touches bundle files directly: a *reader* turns a bundle's
normalized snapshot (the `dict[str, str]` form the golden harness records and
produces) into a `BundleView`, and the engine compares two views.  That seam is
what made the harness testable before the v3 format existed: the mutation
self-test runs v2 bundles through the candidate side and proves the engine
catches planted defects, independent of any v3 code.

What counts as *document text* is defined here, once, for both formats: the
prose a reader would call the document — headings, paragraphs, quotes, code,
list items, definition entries, table cells, figure captions.  OCR output is
excluded (it is a function of the Tesseract build, and it has its own proof —
the golden sentinels), and so are form-field and embedded-media records, which
are measured by their own fidelity metrics rather than as text.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

Grid = tuple[tuple[str, ...], ...]
TrackRecord = tuple[str, str, str, str]  # kind, language, label, url
MediaRecord = tuple[str, str, str, tuple[TrackRecord, ...]]

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


#: Chunk kinds that describe something *other than the page's own document*,
#: and are therefore outside what parity measures.
#:
#: Parity compares v3 against a recorded v2 baseline, and v2 had no notion of
#: an embedded video — so these have nothing to be compared against. More to
#: the point, `chunk_doc_items` asks whether a chunk traces back into
#: `content.json`, and these deliberately do not: a walkthrough step anchors
#: into `videos/<id>/steps.json` and a gap record into the media it stands for.
#: Measuring them here would report a correct anchor as an unresolvable one.
#: Their provenance is asserted directly in `tests/test_video_capture.py`.
NON_PAGE_CHUNK_KINDS = frozenset({"video_step", "untranscribed_media"})


@dataclass(slots=True)
class ChunkView:
    text: str
    refs: tuple[str, ...]


def _asset_identity(asset: dict) -> tuple[str, object, object, str]:
    """How a visual asset is identified across bundle formats and machines.

    Everything here is authored or laid out, never rendered: `alt` comes from
    the page, and the pixel dimensions did not move when the system fonts did
    (`bundle-rasters.json` compared equal through that change while every
    digest differed).
    """
    return (
        str(asset.get("kind", "")),
        asset.get("width"),
        asset.get("height"),
        str(asset.get("alt", "")),
    )


@dataclass(slots=True)
class BundleView:
    """Everything the parity metrics measure, format-independent."""

    headings: Counter[tuple[int, str]] = field(default_factory=Counter)
    tables: Counter[Grid] = field(default_factory=Counter)
    links: Counter[tuple[str, str]] = field(default_factory=Counter)
    #: Visual assets by a *stable* identity, not by the digest of their
    #: pixels. A raster's digest moves with the fonts that drew its text: a
    #: macOS font update changed every text-bearing asset in the corpus while
    #: the assets themselves were untouched, and this metric scored 0% against
    #: baselines frozen before it (docs/09 P7-14). Kind, size and alt text are
    #: recorded in both bundle formats, identical across that change, and still
    #: lose an element when an asset is dropped -- which is the property the
    #: `strip-asset` mutations require this metric to catch.
    assets: Counter[tuple[str, object, object, str]] = field(default_factory=Counter)
    text_tokens: Counter[str] = field(default_factory=Counter)
    full_text: str = ""  # normalized, for substring-style fidelity checks
    form_fields: Counter[tuple[str, str]] = field(default_factory=Counter)
    definitions: Counter[tuple[str, str]] = field(default_factory=Counter)
    media: Counter[MediaRecord] = field(default_factory=Counter)
    #: The page's declared metadata (description, author, keywords, canonical
    #: URL, published time) as (key, value) entries — v2 kept it in
    #: content.json, format 3 in the manifest.
    page_metadata: Counter[tuple[str, str]] = field(default_factory=Counter)
    chunks: list[ChunkView] = field(default_factory=list)
    resolvable_refs: frozenset[str] = frozenset()


def _tables_from_csv(files: dict[str, str]) -> Counter[Grid]:
    """Both formats publish tables as `tables/*.csv`; compare what is published."""
    grids: Counter[Grid] = Counter()
    for name in sorted(files):
        if not re.fullmatch(r"bundle/tables/[^/]+\.csv", name):
            continue
        rows = list(csv.reader(io.StringIO(files[name])))
        grids[tuple(tuple(norm(cell) for cell in row) for row in rows)] += 1
    return grids


def _links(files: dict[str, str]) -> Counter[tuple[str, str]]:
    records = json.loads(files.get("bundle/links.json", "[]"))
    return Counter((norm(link["text"]), link["url"]) for link in records)


def chunk_rows(files: dict[str, str]) -> list[str]:
    """The records of `chunks.jsonl`, one string each, split where the writer split.

    At `\\n` only. The writer leaves U+0085, U+2028 and U+2029 raw inside a
    record, and `splitlines()` breaks at all three (docs/09 P10-28).
    """
    text = files.get("bundle/chunks.jsonl", "")
    return [line for line in text.split("\n") if line.strip()]


def _chunk_lines(files: dict[str, str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in chunk_rows(files)]


# --------------------------------------------------------------------------- #
# v2 (bundle_format implicit, schema_version 1.0)
# --------------------------------------------------------------------------- #

#: Block types whose text is document prose.  form-value and embedded-media are
#: deliberately absent: they are fidelity records with their own metrics, and
#: figures contribute their caption only (the pixels are assets, and OCR text
#: is recognition output, not document text).
_V2_TEXT_BEARING = ("heading", "paragraph", "blockquote", "code")

#: v2 injected OCR text into the live DOM as a span after each visual asset
#: (for the PDF's text layer), and a block whose element contained the asset
#: recorded that span inside its own innerText.  The basis excludes OCR, so
#: the recorded blocks are scrubbed of the injection — from its marker to the
#: end of the block text, which is where the injected span sits.
_INJECTED_OCR_RE = re.compile(
    r"\s*Text recognized within visual asset\s+\S+:.*\Z", re.DOTALL
)


def _without_injected_ocr(text: str) -> str:
    return _INJECTED_OCR_RE.sub("", text)


def _media_record(
    media_type: str, source_url: str, title: str, tracks: list[dict[str, Any]]
) -> MediaRecord:
    """The one comparable spelling of an embedded-media record.

    Both format readers build it through here, because the `embedded_media`
    metric compares these tuples across formats — a divergence in shape would
    be an invisible metric bug, not an error.
    """
    return (
        media_type,
        source_url,
        norm(title),
        tuple(
            sorted(
                (
                    track.get("kind", ""),
                    track.get("language", ""),
                    track.get("label", ""),
                    track.get("url", ""),
                )
                for track in tracks
            )
        ),
    )


def _metadata_entries(metadata: dict[str, Any]) -> Counter[tuple[str, str]]:
    return Counter(
        (key, str(value)) for key, value in metadata.items() if value not in ("", None)
    )


def read_v2(files: dict[str, str], content: dict[str, Any]) -> BundleView:
    view = BundleView()
    view.page_metadata = _metadata_entries(content.get("metadata", {}))
    text_parts: list[str] = []
    refs: set[str] = set()

    for block in content["blocks"]:
        kind = block["type"]
        refs.add(block["id"])
        if kind == "heading":
            view.headings[(block["level"], norm(block["text"]))] += 1
            text_parts.append(block["text"])
        elif kind in _V2_TEXT_BEARING:
            text_parts.append(_without_injected_ocr(block["text"]))
        elif kind == "list":
            text_parts.extend(block["items"])
        elif kind == "definitions":
            for entry in block["entries"]:
                view.definitions[(norm(entry["term"]), norm(entry["definition"]))] += 1
                text_parts.extend((entry["term"], entry["definition"]))
        elif kind == "table":
            text_parts.extend(cell["text"] for row in block["rows"] for cell in row)
            if block.get("caption"):
                text_parts.append(block["caption"])
        elif kind == "figure":
            if block.get("caption"):
                text_parts.append(block["caption"])
        elif kind == "form-value":
            view.form_fields[(norm(block["name"]), norm(block["value"]))] += 1
        elif kind == "embedded-media":
            view.media[
                _media_record(
                    block["mediaType"],
                    block["sourceUrl"],
                    block.get("title", ""),
                    block.get("tracks", []),
                )
            ] += 1

    for asset in content.get("visual_assets", []):
        view.assets[_asset_identity(asset)] += 1
        refs.add(asset["id"])

    view.tables = _tables_from_csv(files)
    view.links = _links(files)
    view.full_text = norm(" ".join(text_parts))
    view.text_tokens = Counter(tokens(view.full_text))
    view.resolvable_refs = frozenset(refs)
    view.chunks = [
        ChunkView(
            text=chunk["text"],
            refs=tuple(chunk.get("block_ids") or chunk.get("asset_ids") or ()),
        )
        for chunk in _chunk_lines(files)
    ]
    return view


# --------------------------------------------------------------------------- #
# v3 (bundle_format 3: content.json is a lossless DoclingDocument)
# --------------------------------------------------------------------------- #


def _docling_self_refs(document: dict[str, Any]) -> frozenset[str]:
    refs: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            self_ref = value.get("self_ref")
            if isinstance(self_ref, str):
                refs.add(self_ref)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    return frozenset(refs)


def _docling_table_cells(item: dict[str, Any]) -> list[str]:
    data = item.get("data") or {}
    cells = data.get("table_cells") or []
    if not cells and data.get("grid"):
        cells = [cell for row in data["grid"] for cell in row]
    return [cell.get("text", "") for cell in cells if isinstance(cell, dict)]


def read_v3(
    files: dict[str, str], document: dict[str, Any], manifest: dict[str, Any]
) -> BundleView:
    view = BundleView()
    view.page_metadata = _metadata_entries(manifest.get("page_metadata", {}))
    text_parts: list[str] = []

    for item in document.get("texts", []):
        # Furniture is the page's chrome (header/nav/footer): v2's block
        # extraction never modeled bare chrome text, and the exports skip it,
        # so it is not part of the comparison basis on either side.
        if item.get("content_layer") == "furniture":
            continue
        label = item.get("label")
        text = item.get("text", "")
        if label == "title":
            view.headings[(1, norm(text))] += 1
        elif label == "section_header":
            # docling levels the first heading depth at 1; the reader restores
            # the HTML numbering the v2 side recorded (h1 = title, h2 = level 1).
            view.headings[(int(item.get("level", 1)) + 1, norm(text))] += 1
        text_parts.append(text)

    for item in document.get("tables", []):
        text_parts.extend(_docling_table_cells(item))

    # Definition lists have no assets.json record on purpose: docling models
    # them natively, so their fidelity is measured through the document text
    # (engine._definitions_metric).
    assets = json.loads(files.get("bundle/assets.json", "{}"))
    for asset in assets.get("visual_assets", []):
        view.assets[_asset_identity(asset)] += 1
    for record in assets.get("form_fields", []):
        view.form_fields[(norm(record["name"]), norm(record["value"]))] += 1
    for record in assets.get("embedded_media", []):
        view.media[
            _media_record(
                record["media_type"],
                record["source_url"],
                record.get("title", ""),
                record.get("tracks", []),
            )
        ] += 1

    view.tables = _tables_from_csv(files)
    view.links = _links(files)
    view.full_text = norm(" ".join(text_parts))
    view.text_tokens = Counter(tokens(view.full_text))
    view.resolvable_refs = _docling_self_refs(document)
    view.chunks = [
        ChunkView(
            text=chunk["text"], refs=tuple(chunk.get("meta", {}).get("doc_items", ()))
        )
        for chunk in _chunk_lines(files)
        if chunk.get("meta", {}).get("kind") not in NON_PAGE_CHUNK_KINDS
    ]
    return view


def read_bundle(files: dict[str, str]) -> BundleView:
    """Dispatch on the format the bundle *declares*, never on a flag.

    Every bundle self-describes through its manifest (docs/04 §7); anything
    this reader does not recognize is an error, not a guess — a viewer bundle
    or a future format silently scored as a near-empty v3 view would be the
    vacuous-pass trap all over again.
    """
    manifest = json.loads(files["bundle/manifest.json"])
    content = json.loads(files["bundle/content.json"])
    if manifest.get("bundle_format") == 3:
        return read_v3(files, content, manifest)
    if manifest.get("schema_version") == "1.0":
        return read_v2(files, content)
    raise ValueError(
        "unrecognized bundle format: manifest declares neither bundle_format 3 "
        f"nor schema_version 1.0 (got {manifest.get('bundle_format')!r} / "
        f"{manifest.get('schema_version')!r})"
    )
