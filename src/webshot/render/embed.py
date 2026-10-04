"""Embed an AI bundle into a PDF as associated files.

A WebShot capture produces two deliverables that describe the same content: a
PDF a person reads, and a bundle a machine reads.  Handing both to an agent
means handing it a directory and hoping the two stay together.  Embedding the
bundle *into* the PDF collapses that to one file which carries its own
structured data — the design the protected-viewer path has always used, and
which docs/02 §PDF deliverable assigns to the web path too.

The mechanics are the fussy part.  `pypdf` writes the embedded-file name tree
but not the document-level `/AF` array, and ISO 19005-3 clause 6.8 additionally
requires `/UF` and a well-formed MIME `/Subtype` on every file specification —
veraPDF rejects a file missing either, which is how both were found on the
protected path (docs/09 P1-3).  `embed_associated_files` is therefore shared by
both paths rather than written twice; it was proven by veraPDF before it was
generalized.

**Ordering matters.** The bundle's `manifest.json` records the published PDF's
SHA-256, so embedding has to happen *before* the manifest is written or the
hash it records is the hash of a file that no longer exists.  The pipeline
embeds between rendering and finalizing for exactly that reason, and the
manifest is deliberately not among the embedded files — `capture.json` carries
the same provenance without being self-referential.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    NameObject,
    TextStringObject,
    create_string_object,
)

from ..bundle.publish import EmbeddedPayload, dump_json
from ..version import VERSION as GENERATOR_VERSION

LOGGER = logging.getLogger("webshot")

#: Bundle files worth carrying inside the PDF, in the order a reader should
#: meet them, with the description each file specification advertises.
#:
#: `manifest.json` is absent on purpose (see the module docstring).  Binary
#: assets are absent by default because the images are already *visible* in the
#: PDF and `assets.json` carries their recognized text and context; embedding
#: the pixels again is for callers who ask, not a default that doubles the file.
EMBEDDABLE: tuple[tuple[str, str, str], ...] = (
    (
        "content.json",
        "application/json",
        "Lossless DoclingDocument: the page's full semantic structure — "
        "headings, paragraphs, lists, tables, and figures — with stable "
        "self_ref anchors the chunks point back to",
    ),
    (
        "content.md",
        "text/markdown",
        "The page as Markdown, including figures and their recognized text",
    ),
    (
        "content.txt",
        "text/plain",
        "Plain-text rendering of the page, for full-text search",
    ),
    (
        # `create_ai_bundle` always writes and checksums this file, and it was
        # the one always-present artifact the PDF did not carry. It is not a
        # duplicate of content.md: `docling_bridge` warns, in the bundle's own
        # warnings, that a caption it could not re-attach to a nested table
        # "is preserved in content.html" — so a consumer handed only the PDF,
        # which the README and the product documentation now say is a
        # supported way to receive a capture, lost data that exists nowhere
        # else in the file (docs/09 P8-19).
        "content.html",
        "text/html",
        "The captured DOM as a sanitized standalone document — the fallback "
        "for structure the document model could not carry, including table "
        "captions that could not be re-attached",
    ),
    (
        "chunks.jsonl",
        "application/jsonl",
        "Retrieval-ready chunks, one JSON object per line, each carrying the "
        "doc_items it came from and the heading path above it",
    ),
    (
        "assets.json",
        "application/json",
        "Every visual on the page: alt text, captions, nearby headings, "
        "dimensions, checksums, and OCR text with per-word confidence — plus "
        "the form-field and embedded-media records the document model omits",
    ),
    (
        "links.json",
        "application/json",
        "Resolved link text and destinations",
    ),
    (
        "structured-data.json",
        "application/json",
        "JSON-LD structured data as published by the page",
    ),
    (
        "accessibility.yaml",
        "text/yaml",
        "The page's accessibility tree as the browser exposed it",
    ),
    (
        "content.doctags",
        "text/plain",
        "DocTags serialization of the document model",
    ),
)

#: What `capture.json` advertises.  It is built in memory rather than read from
#: the bundle: the sidecar already carries this in `manifest.json`, which the
#: PDF cannot embed without recording its own hash before it has one.
PROVENANCE_DESCRIPTION = (
    "Capture provenance: source, final URL, timestamp, tool versions, OCR "
    "settings, and content counts — the manifest's contents minus its "
    "self-referential PDF checksum"
)

#: Written into the PDF's `Info` dictionary so a consumer can tell that this
#: file carries structured data without opening the attachments first.
AI_READY_KEY = "/WebShotAIReady"
EMBEDDED_FILES_KEY = "/WebShotEmbeddedFiles"
BUNDLE_FORMAT_KEY = "/WebShotBundleFormat"


def _video_section(videos: Sequence[dict[str, Any]], *, tagged: bool = True) -> str:
    """What the README says about the page's videos, or nothing if it had none.

    This is what makes the walkthroughs discoverable: an agent that does not
    know `videos/<id>/walkthrough.md` exists will never open it, and a video
    that was *not* transcribed has to be named here too — an omission reads as
    "the page had no video", which is the one thing that must not happen.
    """
    if not videos:
        return ""
    # Three groups, not two. `transcribed` alone was the split while it implied
    # the provider — and the lines below name *files*, which differ by provider:
    # a walkthrough has `walkthrough.md`, `steps.json` and appendix pages, and a
    # captioned `<video>` has none of the three. Splitting on `transcribed` sent
    # a reader to three files that do not exist, and claimed "0 steps,
    # documented in full" about a video with no steps (docs/09 P10-10).
    walkthroughs = [video for video in videos if video.get("steps") is not None]
    captioned = [
        video
        for video in videos
        if video.get("steps") is None and video.get("transcribed")
    ]
    untranscribed = [video for video in videos if not video.get("transcribed")]
    lines = ["", "Videos embedded in this page:"]
    for video in walkthroughs:
        # The record carries its own directory, so this never has to rebuild a
        # path that the bundle writer already decided.
        directory = video.get("directory", "videos")
        lines += [
            f"  - {video.get('title', 'Untitled')} — {video.get('steps', 0)} steps, "
            f"documented in full ({video.get('transcript_provenance', 'authored')}).",
            f"    Read it as prose in the appendix at the end of this PDF, or as "
            f"{directory}/walkthrough.md in the bundle.",
            f"    {directory}/steps.json addresses one step at a time; "
            f"transcript.vtt carries the narration on the video's own clock, so "
            f"a timestamp can be cited.",
            "    Its per-step chunks are in chunks.jsonl, each naming the video, "
            "the step number and the moment it happens.",
        ]
    for video in captioned:
        directory = video.get("directory", "videos")
        lines += [
            f"  - {video.get('title') or 'Untitled video'} — transcribed from "
            f"the caption track the page shipped "
            f"({video.get('transcript_provenance', 'authored')}).",
            "    Its words are inlined in content.md and content.txt where the "
            "player appeared, and chunked in chunks.jsonl.",
            f"    {directory}/transcript.txt is the same text on its own; "
            f"{directory}/transcript.vtt carries it on the media's own clock, "
            f"so a timestamp can be cited.",
            "    It has no appendix pages and no steps: it is a media file with "
            "captions, not an authored walkthrough.",
        ]
    for video in untranscribed:
        lines += [
            f"  - {video.get('title') or 'Untitled video'} — NOT TRANSCRIBED.",
            f"    {video.get('note', '')}",
            f"    Source: {video.get('source_url', 'unknown')}",
        ]
    if walkthroughs:
        # The second sentence used to be unconditional, and under
        # `--no-tagged-pdf` it contradicted `manifest.pdf.tagged: false` — two
        # provenance surfaces in the same deliverable disagreeing, precisely
        # when the user had asked for the thing one of them denied
        # (docs/09 P8-21). It is measured now, and the appendix half stays
        # true either way because merging never preserves a structure tree.
        lines.append(
            "  The appendix pages carry no PDF structure tags: merging an "
            "appendix does not preserve one. "
            + (
                "The captured pages before them do."
                if tagged
                else "Nor do the captured pages before them: this capture was "
                "made with --no-tagged-pdf."
            )
        )
    return "\n".join(lines) + "\n"


def readme_text(
    embedded: Iterable[str],
    *,
    title: str,
    source: str,
    videos: Sequence[dict[str, Any]] = (),
    tagged: bool = True,
) -> str:
    """The guide that ships as the first attachment.

    An agent handed a PDF has no way to know the attachments exist, let alone
    which one to read first.  This says so in plain language, says the one
    thing about OCR that a reader acting on recognized text needs to know, and
    names every video the page carried — including any it could not document.
    """
    listing = "\n".join(f"  - {name}" for name in embedded)
    return (
        f"{title}\n"
        f"Captured from: {source}\n"
        f"Generated by WebShot {GENERATOR_VERSION}\n"
        "\n"
        "This PDF is self-contained: everything WebShot extracted from the "
        "page travels inside this file as PDF attachments (associated files), "
        "so no sidecar directory is needed.\n"
        "\n"
        "Three complementary ways to read it:\n"
        "1. The rendered pages, as a person sees them — layout, images, "
        "charts, and tables in place.\n"
        "2. The PDF's own text layer, selectable and searchable.\n"
        "3. The attachments below, which are the same content as structured "
        "data.\n"
        "\n"
        "Attachments:\n"
        f"{listing}\n"
        f"{_video_section(videos, tagged=tagged)}"
        "\n"
        "Where to start: content.md reads well as prose; content.json is the "
        "lossless structure; chunks.jsonl is ready for retrieval and every "
        "chunk names the doc_items it came from, so a claim can be traced back "
        "to its place in content.json.\n"
        "\n"
        "About recognized text: text read out of images, charts, and canvases "
        "is machine-generated and can be wrong. The visual is always preserved "
        "in the rendered page — use it to verify anything that matters, and to "
        "read what OCR cannot express, such as trends, shapes, and spatial "
        "relationships.\n"
    )


#: A documented video's own artifacts, and what each is for. Globbed rather
#: than listed because there is one set per video on the page.
VIDEO_EMBEDDABLE: tuple[tuple[str, str, str], ...] = (
    (
        "walkthrough.md",
        "text/markdown",
        "The embedded video as prose: the instruction for each step, the "
        "narration on the video's own clock, and the annotated screenshot",
    ),
    (
        "steps.json",
        "application/json",
        "The video's steps as addressable records, with start and end times "
        "and the screenshot for each",
    ),
    (
        "transcript.txt",
        "text/plain",
        "The video's narration as timestamped plain text",
    ),
    (
        "transcript.vtt",
        "text/vtt",
        "The video's narration as WebVTT, on the video's own clock, so a "
        "moment in it can be cited",
    ),
)


def collect_payloads(
    staging: Path,
    *,
    title: str,
    source: str,
    provenance: dict[str, Any] | None = None,
    include_assets: bool = False,
    videos: Sequence[dict[str, Any]] = (),
    tagged: bool = True,
) -> dict[str, EmbeddedPayload]:
    """Gather the bundle files that exist into payloads, README first.

    Files are skipped rather than demanded: a capture with `--no-ocr`, no
    tables, or no structured data legitimately produces fewer of them, and a
    missing optional artifact is not a reason to fail a capture that otherwise
    succeeded.
    """
    payloads: dict[str, EmbeddedPayload] = {}
    for name, mime, description in EMBEDDABLE:
        candidate = staging / name
        if not candidate.is_file():
            continue
        payloads[name] = EmbeddedPayload(candidate.read_bytes(), description, mime)

    if provenance is not None:
        payloads["capture.json"] = EmbeddedPayload(
            dump_json(provenance).encode("utf-8"),
            PROVENANCE_DESCRIPTION,
            "application/json",
        )

    for directory in sorted((staging / "videos").glob("*")):
        if not directory.is_dir():
            continue
        for name, mime, description in VIDEO_EMBEDDABLE:
            candidate = directory / name
            if not candidate.is_file():
                continue
            payloads[f"videos/{directory.name}/{name}"] = EmbeddedPayload(
                candidate.read_bytes(), description, mime
            )

    tables = sorted((staging / "tables").glob("*.csv"))
    for table in tables:
        payloads[f"tables/{table.name}"] = EmbeddedPayload(
            table.read_bytes(),
            f"Lossless CSV of the table in {table.stem}",
            "text/csv",
        )

    if include_assets:
        for asset in sorted((staging / "assets").glob("*")):
            if not asset.is_file():
                continue
            payloads[f"assets/{asset.name}"] = EmbeddedPayload(
                asset.read_bytes(),
                f"Original visual asset {asset.name}",
                _asset_mime(asset),
            )

    # Prepended, so it is both the first attachment a viewer lists and the
    # first thing in the `/AF` array.
    readme = readme_text(
        payloads.keys(), title=title, source=source, videos=videos, tagged=tagged
    )
    return {
        "README.txt": EmbeddedPayload(
            readme.encode("utf-8"),
            "Start here: what this PDF contains and how to read it",
            "text/plain",
        ),
        **payloads,
    }


def _asset_mime(path: Path) -> str:
    suffixes = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        # `--video-assets` writes clips and source recordings into the bundle,
        # and `--embed-assets` gathers whatever is there — so these arrived in
        # the PDF labelled `application/octet-stream`, which tells a consumer
        # nothing and stops it choosing a player (docs/09 P8-20).
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".m4v": "video/x-m4v",
        ".mov": "video/quicktime",
        ".vtt": "text/vtt",
    }
    return suffixes.get(path.suffix.lower(), "application/octet-stream")


def embed_associated_files(
    writer: PdfWriter, payloads: dict[str, EmbeddedPayload]
) -> list[str]:
    """Attach `payloads` as PDF/A-3 associated files and return their names.

    Shared by the web and protected paths.  `/UF` and a well-formed MIME
    `/Subtype` are what ISO 19005-3 clause 6.8 is picky about, and the
    document-level `/AF` array is the part `pypdf` does not write (docs/09 S2).
    """
    associated_files = ArrayObject()
    embedded_names: list[str] = []
    for filename, payload in payloads.items():
        embedded = writer.add_attachment(filename, payload.data)
        embedded.description = TextStringObject(payload.description)
        embedded.subtype = NameObject(f"/{payload.mime_type}")
        embedded.alternative_name = cast(
            TextStringObject, create_string_object(filename)
        )
        embedded.associated_file_relationship = NameObject("/Data")
        associated_files.append(embedded.pdf_object.indirect_reference)
        embedded_names.append(filename)
    writer.root_object[NameObject("/AF")] = associated_files
    return embedded_names


def embed_bundle(
    pdf: Path,
    payloads: dict[str, EmbeddedPayload],
    *,
    title: str,
    bundle_format: int,
) -> list[str]:
    """Rewrite `pdf` in place with `payloads` attached.

    The writer is cloned from the rendered file so Chromium's structure tree
    and outline survive — spike S2 confirmed they do, and the golden PDF
    assertions hold it to that.  The rewrite is staged beside the target and
    renamed over it, so an interrupted embed leaves the rendered PDF intact
    rather than truncated.
    """
    if not payloads:
        return []

    writer = PdfWriter(clone_from=str(pdf))
    embedded_names = embed_associated_files(writer, payloads)
    existing = _existing_metadata(pdf)
    existing.update(
        {
            AI_READY_KEY: "true",
            BUNDLE_FORMAT_KEY: str(bundle_format),
            EMBEDDED_FILES_KEY: ", ".join(embedded_names),
            "/Subject": (
                "Self-contained AI-ready capture: rendered pages, searchable "
                "text, and the extracted content embedded as associated files"
            ),
        }
    )
    writer.add_metadata(existing)

    staged = pdf.with_name(f".{pdf.name}.embed")
    try:
        with staged.open("wb") as handle:
            writer.write(handle)
        staged.replace(pdf)
    finally:
        staged.unlink(missing_ok=True)
    # Deliberately not naming the file: at this point it is still the
    # process-scoped staging name, and a pid in the log makes the capture's own
    # output irreproducible (docs/09 P6-2). The next line names the published
    # PDF, which is the name a reader is looking for anyway.
    LOGGER.info("Embedded %d bundle file(s) into the PDF", len(embedded_names))
    return embedded_names


def _existing_metadata(pdf: Path) -> dict[Any, Any]:
    """The rendered file's `Info` dictionary, so adding keys does not drop it."""
    try:
        return dict(PdfReader(str(pdf)).metadata or {})
    except Exception:  # pragma: no cover - a PDF this broken fails validation
        return {}


def embedded_names(pdf: Path) -> list[str]:
    """Names of the files embedded in `pdf`, for tests and verification."""
    return list(PdfReader(str(pdf)).attachments.keys())
