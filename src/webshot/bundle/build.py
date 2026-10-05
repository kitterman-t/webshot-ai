"""Assemble the `.ai` bundle from a captured page.

The web path is bundle_format 3 (Phase 2): `content.json` is a lossless
DoclingDocument produced by the docling bridge, `content.md`/`.txt`/`.doctags`
and the table CSVs come out of docling's serializers, `chunks.jsonl` carries
DocItem provenance, and `assets.json` holds the typed records docling's HTML
backend does not model — visual assets with their OCR, form fields (passwords
pre-redacted), and embedded media with caption tracks (docs/02 fidelity
mapping: nothing vanishes silently).  `content.html` remains the capture
stage's sanitized snapshot — single producer.

The protected-viewer bundle is built here too (moved out of
`protected/assemble.py` in Phase 1, which the module map in
docs/02-architecture.md always described as a bridge over img2pdf and OCRmyPDF
rather than a bundle writer).  Its artifacts come entirely from WebShot's own
recognition pass and are untouched by Phase 2's extraction swap.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from functools import cache
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from ..acquire import ADAPTER_DISTRIBUTIONS
from ..capture import DISCOVERY_DISTRIBUTIONS
from ..capture.discover import DiscoveredMetadata
from ..capture.prepare import ContentSelection
from ..capture.session import devtools
from ..capture.snapshot import (
    _accessibility_snapshot,
    extract_semantic_content,
    unread_text_warnings,
    with_frame_content,
)
from ..capture.visuals import VisualAsset, capture_visual_assets
from ..config import AuthMode
from ..extract import EXTRACTION_DISTRIBUTIONS, docling_bridge, legacy
from ..extract.chunks import build_chunk_records
from ..extract.exports import write_chunks, write_exports
from ..ocr.engine import OcrEngine
from ..ocr.tesseract import OCRPage, complete_text
from ..version import VERSION as GENERATOR_VERSION
from .localpaths import LocalPathRecorder
from .manifest import BUNDLE_FORMAT, VIEWER_SCHEMA_VERSION
from .publish import (
    EmbeddedPayload,
    dump_json,
    relative_pdf_path,
    sha256_file,
    write_json,
)


@cache
def _tesseract_version() -> str | None:
    from ..doctor import tool_output

    executable = shutil.which("tesseract")
    if not executable:
        return None
    lines = tool_output(executable, "--version").splitlines()
    return lines[0].strip() if lines else None


async def _chromium_version(page: Any) -> str:
    """The browser build that produced this capture, however it was launched.

    `page.context.browser` is `None` for a persistent context — an
    `--auth-profile` capture *is* its own context, with no owning `Browser` to
    ask — so every authenticated bundle recorded `chromium: "unknown"`,
    defeating the provenance field precisely where reproducibility matters most
    (docs/09 P8-60).

    CDP rather than the user agent: `--user-agent` overrides `navigator.userAgent`,
    so reading the version from it would report whatever the caller typed. A
    session that cannot be opened falls back to `"unknown"`, which is the
    honest answer and the one this already gave.

    Through `devtools()`, which never detaches: the detach this used to do
    reset the emulated print media before every `--auth-profile` render
    (docs/09 P16-6).
    """
    browser = page.context.browser
    if browser is not None:
        return str(browser.version)
    try:
        payload = await (await devtools(page)).send("Browser.getVersion")
    except Exception:  # a CDP surface that is not there is not a capture failure
        return "unknown"
    # "HeadlessChrome/141.0.7390.37" or "Chrome/141.0.7390.37"; `browser.version`
    # reports the bare number, so the two agree.
    product = str(payload.get("product", ""))
    _, _, version = product.partition("/")
    return version or "unknown"


def tool_versions(chromium: str) -> dict[str, str | None]:
    """The upstream versions that actually produced this bundle (04 §2.2).

    The extraction-stack entries come from the same list `webshot doctor`
    reports, so the two descriptions cannot drift apart.
    """
    versions: dict[str, str | None] = {
        "webshot": GENERATOR_VERSION,
        "playwright": package_version("playwright"),
        "chromium": chromium,
    }
    for distribution in (
        *ADAPTER_DISTRIBUTIONS,
        *DISCOVERY_DISTRIBUTIONS,
        *EXTRACTION_DISTRIBUTIONS,
    ):
        versions[distribution.replace("-", "_")] = package_version(distribution)
    versions["tesseract"] = _tesseract_version()
    return versions


def checksummed_files(
    root: Path, *, known: dict[str, str] | None = None
) -> list[tuple[str, int, str]]:
    """Every published file under `root`, as (relative posix path, bytes, sha256).

    `known` lets a caller hand in digests it already computed — the protected
    path hashes each page image once, on the way into the bundle, rather than
    again here.  Paths are posix so a manifest recorded on Windows says the same
    thing as one recorded anywhere else.
    """
    known = known or {}
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        digest = known.get(relative) or sha256_file(path)
        records.append((relative, path.stat().st_size, digest))
    return records


@dataclass(slots=True)
class AIBundleResult:
    staging_directory: Path
    final_directory: Path
    assets: list[VisualAsset]
    #: Visuals the page may show that `assets` does not hold: past
    #: `--max-assets`, or whose capture failed. Not published; the
    #: empty-capture check reads it (docs/09 P22-2).
    unsaved_visuals: int
    page_metadata: dict[str, Any]
    item_count: int
    table_count: int
    link_count: int
    chunk_count: int
    #: The length of `content.txt`, as the manifest publishes it (docs/09 P20-4).
    text_characters: int
    #: The same text without the newline `content.txt` ends with, so 0 for a
    #: page with no text, where `text_characters` is 1. Not published: it is
    #: what the empty-capture check reads (docs/09 P22-2).
    page_text_characters: int
    #: Words recognized across `assets`, counted once. Both the manifest and
    #: the QA report read it from here rather than recomputing it, which is
    #: what "one definition" in `ocr_word_count` is actually worth.
    ocr_words: int
    ocr_enabled: bool
    #: What the engine actually applied, which is not always what was asked
    #: for — see `OcrEngine.applied_settings`.
    ocr_settings: dict[str, object]
    auth_mode: AuthMode
    chromium_version: str
    warnings: list[str] = field(default_factory=list)
    #: Manifest records for the videos embedded in the page — the walkthroughs
    #: that were documented and the media files that were not. Empty for a page
    #: with no video, and then absent from the manifest entirely, so a capture
    #: of a video-free page is byte-for-byte what it always was.
    videos: list[dict[str, Any]] = field(default_factory=list)
    video_tally: dict[str, int] = field(default_factory=dict)
    #: What the appended walkthrough pages are, when there are any.
    video_appendix: dict[str, Any] | None = None


def ocr_word_count(assets: Sequence[VisualAsset]) -> int:
    """Words recognized across a capture's visual assets.

    Called once, into `AIBundleResult.ocr_words`, which the manifest and the QA
    report both read — one definition *and* one evaluation.

    "Word" means a whitespace token here and a recognized word *box* on the
    protected-viewer path, where the count comes from Tesseract's own boxes
    (`viewer_manifest` below). Those are different measurements of the same
    idea, and the manifest field says so rather than implying the two paths are
    comparable (docs/09 P4-12).
    """
    return sum(len(asset.ocr.text.split()) for asset in assets)


def _fidelity_records(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Form-field and embedded-media records for assets.json.

    docling's HTML backend does not model either (docs/02 fidelity mapping),
    so their capture-side records are carried as data.  Password values were
    redacted in the browser before these blocks existed.
    """
    form_fields = [
        {"name": block["name"], "value": block["value"], "locator": block["locator"]}
        for block in blocks
        if block["type"] == "form-value"
    ]
    embedded_media = [
        {
            "media_type": block["mediaType"],
            "source_url": block["sourceUrl"],
            "title": block["title"],
            "tracks": [
                {
                    "kind": track.get("kind", ""),
                    "language": track.get("language", ""),
                    "label": track.get("label", ""),
                    "url": track.get("url", ""),
                }
                for track in block.get("tracks", [])
            ],
            "locator": block["locator"],
        }
        for block in blocks
        if block["type"] == "embedded-media"
    ]
    return form_fields, embedded_media


async def create_ai_bundle(
    page: Page,
    *,
    staging_directory: Path,
    final_directory: Path,
    source: str,
    title: str,
    max_assets: int,
    ocr: bool,
    ocr_language: str,
    ocr_psm: int,
    ocr_engine: OcrEngine,
    require_ocr: bool = False,
    original_local_path: Path | None,
    auth_mode: AuthMode = "none",
    legacy_bundle: bool = False,
    prior_warnings: Sequence[str] = (),
    local_paths: LocalPathRecorder | None = None,
) -> AIBundleResult:
    """Build the bundle. `prior_warnings` are the capture's own, carried in.

    CONTRIBUTING rule 5: if a stage did less than the user asked for, the
    *manifest* says so. Warnings raised before this function — a flag this run
    cannot honour, a lazy-load pass that hit its limit — would otherwise reach
    only the QA report, which would make where a warning appears a fact about
    which function raised it rather than about what it says.

    `local_paths` rewrites the resolved `file:` URLs this function records
    as it writes them (docs/09 P14-61). It never touches `assets` or the
    extracted blocks themselves, which later stages still read.
    """
    recorder = local_paths or LocalPathRecorder(source)
    staging_directory.mkdir(parents=True, exist_ok=False)
    assets, warnings, unsaved_visuals = await capture_visual_assets(
        page,
        staging_directory / "assets",
        max_assets=max_assets,
        ocr=ocr,
        ocr_language=ocr_language,
        ocr_psm=ocr_psm,
        engine=ocr_engine,
        require_ocr=require_ocr,
    )
    warnings = [*prior_warnings, *warnings]
    extracted = await extract_semantic_content(
        page, own_labels={asset.id: asset.aria_label for asset in assets}
    )
    assets = with_frame_content(extracted, assets)
    accessibility, accessibility_warning = await _accessibility_snapshot(page)
    chromium_version = await _chromium_version(page)

    # CPU-bound docling work off the event loop: the Playwright connection is
    # live above this line, and the render that follows needs the loop
    # responsive (same treatment the pipeline gives veraPDF).
    extraction = await asyncio.to_thread(
        docling_bridge.extract, extracted["html"], assets
    )
    # Built after the extraction, which decides whether an unread frame's OCR
    # text reached the text surfaces, and placed where it stood before it.
    warnings.extend(
        unread_text_warnings(extracted, assets, pictured=extraction.pictured_asset_ids)
    )
    if accessibility_warning:
        warnings.append(accessibility_warning)
    warnings.extend(extraction.warnings)
    chunk_records = build_chunk_records(extraction.chunk_seeds)

    (staging_directory / "content.html").write_text(
        extracted["html"], encoding="utf-8", newline="\n"
    )
    table_count = write_exports(extraction, staging_directory)
    write_chunks(chunk_records, staging_directory)

    blocks = extracted["blocks"]
    form_fields, embedded_media = _fidelity_records(blocks)
    # Copies, so the records are written as the bundle records them while the
    # captured objects keep the URLs the later stages resolve against.
    recorded_assets = [
        replace(asset, source_url=recorder.record(asset.source_url)) for asset in assets
    ]
    recorded_links = recorder.record_links(extracted["links"])
    write_json(
        staging_directory / "assets.json",
        {
            "bundle_format": BUNDLE_FORMAT,
            "visual_assets": [asdict(asset) for asset in recorded_assets],
            "form_fields": form_fields,
            "embedded_media": [
                recorder.record_media(media, "source_url") for media in embedded_media
            ],
        },
    )
    (staging_directory / "links.json").write_text(
        json.dumps(recorded_links, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (staging_directory / "structured-data.json").write_text(
        json.dumps(extracted["jsonLd"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (staging_directory / "accessibility.yaml").write_text(
        accessibility or "# Not available\n", encoding="utf-8", newline="\n"
    )
    page_metadata = dict(extracted["metadata"])
    if page_metadata.get("canonicalUrl"):
        page_metadata["canonicalUrl"] = recorder.record(page_metadata["canonicalUrl"])
    if legacy_bundle:
        legacy_directory = staging_directory / "legacy"
        legacy_directory.mkdir()
        recorded_source = recorder.record(source)
        recorded_blocks = [
            recorder.record_media(block, "sourceUrl")
            if block["type"] == "embedded-media"
            else block
            for block in blocks
        ]
        (legacy_directory / "content.json").write_text(
            legacy.legacy_content_json(
                recorded_source,
                page_metadata,
                recorded_blocks,
                recorded_links,
                recorded_assets,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (legacy_directory / "chunks.jsonl").write_text(
            legacy.legacy_chunks_jsonl(
                recorded_blocks, recorded_assets, recorded_source
            ),
            encoding="utf-8",
            newline="\n",
        )
    if original_local_path:
        source_directory = staging_directory / "source"
        source_directory.mkdir()
        shutil.copy2(original_local_path, source_directory / original_local_path.name)

    return AIBundleResult(
        staging_directory=staging_directory,
        final_directory=final_directory,
        assets=assets,
        unsaved_visuals=unsaved_visuals,
        ocr_words=ocr_word_count(assets),
        page_metadata=page_metadata,
        item_count=extraction.item_count,
        table_count=table_count,
        link_count=len(extracted["links"]),
        chunk_count=len(chunk_records),
        text_characters=len(extraction.text),
        page_text_characters=len(extraction.text.strip()),
        ocr_enabled=ocr,
        ocr_settings=ocr_engine.applied_settings(language=ocr_language, psm=ocr_psm),
        auth_mode=auth_mode,
        chromium_version=chromium_version,
        warnings=warnings,
    )


def embed_provenance(
    result: AIBundleResult,
    *,
    source: str,
    final_url: str,
    title: str,
    local_paths: str | None = None,
) -> dict[str, Any]:
    """Provenance for the copy of the bundle that travels inside the PDF.

    The manifest cannot be embedded: it records the published PDF's SHA-256,
    and embedding changes the file it just hashed.  This carries the same
    account of *where the content came from* without that circularity, so a
    reader holding only the PDF still knows the source, the moment, and which
    versions of which tools produced what they are reading.
    """
    return {
        "bundle_format": BUNDLE_FORMAT,
        "generator": "WebShot",
        "generator_version": GENERATOR_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": source,
        "final_url": final_url,
        # Only when set, as in the manifest: a reader holding only the PDF
        # must be able to tell `./page.html` was written relative on purpose.
        **({"local_paths": local_paths} if local_paths else {}),
        "title": title,
        "page_metadata": result.page_metadata,
        "auth_mode": result.auth_mode,
        "tool_versions": tool_versions(result.chromium_version),
        # `engine` derived exactly as the sidecar manifest derives it, and for
        # the same reason: `tool_versions` lists what is *installed*, which
        # under `--ocr-engine rapid` names two recognizers and identifies
        # neither as the one that produced the text. A consumer holding only
        # the PDF — which the README and the product documentation now present
        # as a supported way to receive a capture — could not tell which
        # engine's confidence figures it was reading (docs/09 P8-22).
        "ocr": {
            "enabled": result.ocr_enabled,
            **result.ocr_settings,
            "engine": next(
                (asset.ocr.engine for asset in result.assets if asset.ocr.engine), None
            ),
        },
        "content": {
            "items": result.item_count,
            "tables": result.table_count,
            "links": result.link_count,
            "chunks": result.chunk_count,
            "text_characters": result.text_characters,
            "visual_assets": len(result.assets),
            "ocr_words": result.ocr_words,
        },
        "warnings": list(result.warnings),
        "note": (
            "The sidecar bundle's manifest.json is the authority for file "
            "checksums; this record omits them because the files it would "
            "describe are the attachments of the PDF carrying it."
        ),
    }


def finalize_ai_bundle(
    result: AIBundleResult,
    *,
    pdf_path: Path,
    pages: int,
    pdf_bytes: int,
    source: str,
    final_url: str,
    title: str,
    discovered: DiscoveredMetadata,
    selection: ContentSelection,
    pdf_file_name: str | None = None,
    tagged: bool = True,
    bookmarks: bool = True,
    http_status: int | None = None,
    content_type: str = "",
    embedded_files: Sequence[str] = (),
    videos: Sequence[dict[str, Any]] = (),
    video_tally: dict[str, int] | None = None,
    video_appendix: dict[str, Any] | None = None,
    local_paths: str | None = None,
) -> None:
    manifest: dict[str, Any] = {
        "bundle_format": BUNDLE_FORMAT,
        "generator": "WebShot",
        "generator_version": GENERATOR_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": source,
        "final_url": final_url,
        # Present only under `--local-paths relative` (docs/09 P14-61), so a
        # capture that did not ask for it is byte for byte what it was.
        **({"local_paths": local_paths} if local_paths else {}),
        "title": title,
        # The page's own metadata, as the DOM declared it — description,
        # author, keywords, canonical URL, published time. v2 carried this in
        # content.json; format 3's content.json is a DoclingDocument, so the
        # manifest is its home (caught by the Phase 2 review: it had been
        # dropped entirely, and no parity metric measured it).
        "page_metadata": result.page_metadata,
        # What Trafilatura read from the same page, and how the content region
        # was chosen (docs/05 task 3.4).  Both are additive: `page_metadata`
        # above is still the DOM's own declarations, unchanged, and a capture
        # without --auto-selector records the same decision it always made.
        "discovered_metadata": asdict(discovered),
        "content_discovery": {
            "strategy": selection.strategy,
            "selector": selection.selector,
        },
        "auth_mode": result.auth_mode,
        "source_response": {
            "http_status": http_status,
            "content_type": content_type,
        },
        "pdf": {
            # Derived, not assumed: `--ai-bundle-dir` can put the bundle
            # anywhere, and `../<name>` resolves to a file that does not exist
            # from any directory that is not the PDF's own sibling
            # (docs/09 P8-49).
            "file": relative_pdf_path(
                pdf_path.with_name(pdf_file_name or pdf_path.name),
                result.final_directory,
            ),
            "pages": pages,
            "bytes": pdf_bytes,
            "sha256": sha256_file(pdf_path),
            "tagged": tagged,
            "bookmarks": bookmarks,
            # What the PDF carries inside it. A consumer holding both the
            # bundle and the PDF can tell, without opening the PDF, whether
            # the PDF alone would have been enough.
            "embedded_files": list(embedded_files),
        },
        "content": {
            "items": result.item_count,
            "tables": result.table_count,
            "links": result.link_count,
            "chunks": result.chunk_count,
            "text_characters": result.text_characters,
            "visual_assets": len(result.assets),
            # How much text recognition actually contributed. A bundle whose
            # assets were all recognized as nothing is not the same artifact as
            # one with no assets, and only this number tells them apart. The QA
            # report's `counts.ocr_words` comes from the same `ocr_word_count`,
            # so a bundle and its report cannot disagree (docs/04-spec.md §2.3).
            "ocr_words": result.ocr_words,
        },
        "ocr": {
            "enabled": result.ocr_enabled,
            # What was applied, not what was requested, and the engine that
            # actually recognized something rather than the one selected: a
            # capture with nothing to recognize names no engine, as it always
            # has (docs/05 task 3.5).
            **result.ocr_settings,
            "engine": next(
                (asset.ocr.engine for asset in result.assets if asset.ocr.engine), None
            ),
        },
        "tool_versions": tool_versions(result.chromium_version),
        "files": {},
        "warnings": result.warnings,
        "ocr_notice": "OCR is machine-generated and may contain errors. Inspect the corresponding asset for authoritative visual context.",
    }
    if videos:
        # Only when there are videos. A `videos: []` on every capture would
        # change the output of every video-free page for no information, and
        # "this manifest does not mention videos" already says there were none.
        manifest["videos"] = list(videos)
        manifest["video_tally"] = dict(video_tally or {})
    if video_appendix:
        manifest["pdf"]["video_appendix"] = dict(video_appendix)
    for relative, size, digest in checksummed_files(result.staging_directory):
        record: dict[str, Any] = {"bytes": size, "sha256": digest}
        if relative.startswith("legacy/"):
            record["legacy"] = True
        manifest["files"][relative] = record
    write_json(result.staging_directory / "manifest.json", manifest)


# --------------------------------------------------------------------------- #
# Protected-viewer bundle
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ViewerBundle:
    """The written bundle, plus the payloads the PDF embeds as associated files.

    Counts rather than the structures they came from: the page records carry a
    box per recognized word, and this object outlives the OCR phase by the whole
    of OCRmyPDF, Chromium and the pypdf compose.
    """

    directory: Path
    chunk_count: int
    page_text: list[str]
    checksums: dict[str, str]
    capture: dict[str, Any]
    payloads: dict[str, EmbeddedPayload]

    @property
    def text_layer_sidecar(self) -> Path:
        """Where OCRmyPDF's plain-text sidecar belongs inside this bundle.

        The bundle owns the path even though the composition writes the file, so
        the manifest's checksum walk is not silently depending on the order two
        calls happen to run in.
        """
        return self.directory / "ocr" / "text-layer.txt"


def viewer_chunks(page: OCRPage, target_size: int = 1800) -> Iterable[dict[str, Any]]:
    """Split one page's readings into retrieval-sized, page-scoped chunks."""
    readings = [("primary", page.text.strip(), page.confidence)]
    if page.alternate_text:
        readings.append(
            ("alternate", page.alternate_text.strip(), page.alternate_confidence)
        )
    if not any(text for _, text, _ in readings):
        yield {
            "id": f"page-{page.page:03d}-chunk-001",
            "page": page.page,
            "kind": "primary_ocr",
            "text": "",
            "ocr_confidence": page.confidence,
        }
        return

    for reading, text, confidence in readings:
        if not text:
            continue
        paragraphs = [part.strip() for part in text.split("\n") if part.strip()]
        current: list[str] = []
        current_size = 0
        chunk_number = 1
        prefix = "" if reading == "primary" else "alternate-"
        for paragraph in paragraphs:
            parts = (
                [
                    paragraph[index : index + target_size]
                    for index in range(0, len(paragraph), target_size)
                ]
                if len(paragraph) > target_size
                else [paragraph]
            )
            for part in parts:
                if current and current_size + len(part) + 1 > target_size:
                    yield {
                        "id": f"page-{page.page:03d}-{prefix}chunk-{chunk_number:03d}",
                        "page": page.page,
                        "kind": f"{reading}_ocr",
                        "text": "\n".join(current),
                        "ocr_confidence": confidence,
                    }
                    chunk_number += 1
                    current = []
                    current_size = 0
                current.append(part)
                current_size += len(part) + 1
        if current:
            yield {
                "id": f"page-{page.page:03d}-{prefix}chunk-{chunk_number:03d}",
                "page": page.page,
                "kind": f"{reading}_ocr",
                "text": "\n".join(current),
                "ocr_confidence": confidence,
            }


def build_viewer_bundle(
    staging: Path,
    *,
    pages: Sequence[Path],
    dimensions: Sequence[tuple[int, int]],
    ocr_pages: Sequence[OCRPage],
    title: str,
    source_url: str,
    ocr_language: str,
    ocr_psm: int,
    capture_metadata: dict[str, Any] | None,
) -> ViewerBundle:
    """Write the page-addressable bundle for a protected-viewer capture.

    Everything here is derived from WebShot's own recognition pass, so the
    bundle is identical whether or not the PDF is later converted to PDF/A.
    """
    staging_pages = staging / "pages"
    staging_ocr = staging / "ocr"
    staging_pages.mkdir()
    staging_ocr.mkdir()
    for page_path in pages:
        shutil.copy2(page_path, staging_pages / page_path.name)
    for ocr_page in ocr_pages:
        (staging_ocr / f"page-{ocr_page.page:03d}.tsv").write_text(
            ocr_page.tsv, encoding="utf-8", newline="\n"
        )

    page_records: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    # The staged copy is byte-identical to the source, so one digest serves both
    # the page record and the manifest's checksum table.
    checksums = {f"pages/{path.name}": sha256_file(path) for path in pages}
    for image_path, size, ocr_page in zip(pages, dimensions, ocr_pages, strict=True):
        page_records.append(
            {
                "page": ocr_page.page,
                "pdf_source_page": ocr_page.page,
                "image": f"pages/{image_path.name}",
                "width": size[0],
                "height": size[1],
                "sha256": checksums[f"pages/{image_path.name}"],
                "ocr": {
                    "text": ocr_page.text,
                    "confidence": ocr_page.confidence,
                    "word_count": len(ocr_page.words),
                    "language": ocr_language,
                    "engine": "Tesseract",
                    "page_segmentation_mode": ocr_psm,
                    "tsv": f"ocr/page-{ocr_page.page:03d}.tsv",
                    "alternate": {
                        "text": ocr_page.alternate_text,
                        "confidence": ocr_page.alternate_confidence,
                        "page_segmentation_mode": ocr_page.alternate_psm,
                        "purpose": (
                            "Additional reading for low-confidence or complex "
                            "visual layout; compare with the primary OCR and "
                            "original source-page image."
                        ),
                    }
                    if ocr_page.alternate_text
                    else None,
                    "words": [
                        {
                            "text": word.text,
                            "confidence": word.confidence,
                            "bbox": [word.left, word.top, word.width, word.height],
                            "line": [word.block, word.paragraph, word.line],
                        }
                        for word in ocr_page.words
                    ],
                },
            }
        )
        chunks.extend(viewer_chunks(ocr_page))

    content_json_text = dump_json(
        {
            "schema_version": VIEWER_SCHEMA_VERSION,
            "title": title,
            "source": source_url,
            "pages": page_records,
        }
    )
    # One reading per page, used by content.txt, content.md, the appendix, and
    # the manifest's character counts.
    page_text = [complete_text(page) for page in ocr_pages]
    content_text = (
        "\n\n".join(
            f"Source page {page.page} of {len(ocr_pages)}\n{text}"
            for page, text in zip(ocr_pages, page_text, strict=True)
        ).rstrip()
        + "\n"
    )
    content_markdown = (
        f"# {title}\n\n"
        + "\n\n".join(
            f"## Page {page.page}\n\n"
            f"![Page {page.page}](pages/page-{page.page:03d}.png)\n\n{text}"
            for page, text in zip(ocr_pages, page_text, strict=True)
        ).rstrip()
        + "\n"
    )
    chunks_text = "".join(
        json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks
    )
    capture = {
        "method": "authorized-protected-viewer-page-capture",
        "native_file_downloaded": False,
        "download_restriction_respected": True,
        **(capture_metadata or {}),
    }
    capture_text = dump_json(capture)

    (staging / "content.json").write_text(
        content_json_text, encoding="utf-8", newline="\n"
    )
    (staging / "content.txt").write_text(content_text, encoding="utf-8", newline="\n")
    (staging / "content.md").write_text(
        content_markdown, encoding="utf-8", newline="\n"
    )
    (staging / "chunks.jsonl").write_text(chunks_text, encoding="utf-8", newline="\n")
    (staging / "capture.json").write_text(capture_text, encoding="utf-8", newline="\n")

    readme = (
        f"{title}\n"
        f"Generated by WebShot {GENERATOR_VERSION}\n\n"
        "This single PDF contains three complementary AI-access layers:\n"
        "1. Original visual source pages at the beginning of the PDF.\n"
        "2. Positioned invisible OCR on every original page.\n"
        "3. A visible page-addressable transcript appendix.\n\n"
        "It also embeds webshot-ai-content.json with OCR text and word "
        "coordinates, webshot-ai-content.txt, webshot-ai-chunks.jsonl, and "
        "webshot-capture.json. OCR may contain errors; use the original visual "
        "page for charts, tables, images, layout, and verification.\n"
    )
    return ViewerBundle(
        directory=staging,
        chunk_count=len(chunks),
        page_text=page_text,
        checksums=checksums,
        capture=capture,
        payloads={
            "webshot-ai-readme.txt": EmbeddedPayload(
                readme.encode("utf-8"),
                "Guide to the PDF's AI-accessibility layers",
                "text/plain",
            ),
            "webshot-ai-content.json": EmbeddedPayload(
                content_json_text.encode("utf-8"),
                "Page-addressable OCR text, word coordinates, and confidence",
                "application/json",
            ),
            "webshot-ai-content.txt": EmbeddedPayload(
                content_text.encode("utf-8"),
                "Plain-text OCR transcript organized by source page",
                "text/plain",
            ),
            "webshot-ai-chunks.jsonl": EmbeddedPayload(
                chunks_text.encode("utf-8"),
                "Retrieval-ready page-scoped text chunks",
                "application/jsonl",
            ),
            "webshot-capture.json": EmbeddedPayload(
                capture_text.encode("utf-8"),
                "Capture provenance and protected-viewer policy record",
                "application/json",
            ),
        },
    )


def viewer_manifest(
    bundle: ViewerBundle,
    *,
    title: str,
    source_url: str,
    ocr_pages: Sequence[OCRPage],
    pdf: dict[str, Any],
    extra_warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """The protected-viewer `manifest.json`, checksummed over the written bundle.

    `extra_warnings` are the run's, not the bundle's: the ignored-option
    warnings the pipeline raises before this path is entered. They reached the
    QA report and the log and *not* this file, because the pipeline merged them
    into its own list after the manifest had already been written and
    published — so a bundle whose OCR engine was silently swapped said nothing
    about it, and a consumer holding only the bundle could not tell
    (docs/09 P8-40).
    """
    files = [
        {"file": relative, "bytes": size, "sha256": digest}
        for relative, size, digest in checksummed_files(
            bundle.directory, known=bundle.checksums
        )
    ]
    confidences = [page.confidence for page in ocr_pages if page.confidence is not None]
    return {
        "schema_version": VIEWER_SCHEMA_VERSION,
        "generator": "WebShot",
        "generator_version": GENERATOR_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "title": title,
        "source": source_url,
        "capture": bundle.capture,
        "pdf": pdf,
        "content": {
            "pages": len(ocr_pages),
            "chunks": bundle.chunk_count,
            "text_characters": sum(len(text) for text in bundle.page_text),
            "ocr_words": sum(len(page.words) for page in ocr_pages),
            "primary_ocr_text_characters": sum(len(page.text) for page in ocr_pages),
            "alternate_ocr_pages": sum(1 for page in ocr_pages if page.alternate_text),
            "ocr_mean_confidence": round(sum(confidences) / len(confidences), 2)
            if confidences
            else None,
        },
        "files": files,
        "warnings": [
            *extra_warnings,
            "This local copy was reconstructed from page images permitted by the "
            "authenticated viewer; it is not the protected native source file.",
            "OCR is probabilistic. The original visual source pages remain inside "
            "the PDF for verification and multimodal analysis.",
            "The transcript appendix is authored from tagged HTML, but merging it "
            "onto the page images does not preserve a structure tree: this PDF is "
            "searchable and bookmarked, not a tagged PDF.",
        ],
    }
