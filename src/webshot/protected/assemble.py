"""Compose the protected-viewer deliverable: pack, recognize, append, embed.

The order is normative (docs/02-architecture.md §PDF deliverable):

1. the source pages are packed at captured quality by **img2pdf** — lossless,
   the PNG bytes are embedded rather than re-encoded;
2. **OCRmyPDF** adds the invisible positioned text layer with a plain-text
   sidecar, and under ``--pdfa`` converts to PDF/A-3 (A-3 permits the embedded
   files below; the A-2b default forbids them);
3. the transcript appendix — authored as HTML, printed by the RENDER stage — is
   appended with **pypdf**, which also builds the bookmarks and turns the
   appendix's back-links into jumps to the source pages;
4. the machine-readable bundle is embedded as associated files.

Steps 3 and 4 happen *after* the conversion, so PDF/A conformance of the final
composed file is something ``--validate-pdf`` proves, never something this
module assumes.

This is the bridge to img2pdf and OCRmyPDF: no other module imports either, and
nothing of theirs crosses the boundary.  The word-coordinate TSVs the bundle
publishes come from WebShot's own Tesseract pass instead, because OCRmyPDF does
not emit TSV — so protected pages are recognized twice, an accepted cost
(docs/03 §4, docs/07 R9).

The composed PDF is **not** a tagged PDF: pypdf's append does not carry the
appendix's structure tree across (docs/09 S8).  The appendix is authored from
tagged HTML for rendering quality; the manifest claims searchable and
bookmarked, nothing more.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import img2pdf
import ocrmypdf
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    NameObject,
    TextStringObject,
)

from ..bundle.build import build_viewer_bundle, viewer_manifest
from ..bundle.publish import (
    EmbeddedPayload,
    announce_replacements,
    publish_directory,
    relative_pdf_path,
    sha256_file,
    write_json,
)
from ..errors import CaptureIntegrityError, EnvironmentFailure, PdfValidationError
from ..ocr.tesseract import OCRPage, complete_text, recognize_pages
from ..render.appendix import assert_no_marker_links, rewrite_back_links
from ..render.embed import embed_associated_files
from ..render.ligatures import ligature_forms, ligature_warning
from ..render.pdf import render_html_to_pdf
from ..version import VERSION as GENERATOR_VERSION
from .appendix import PAGE_FORMAT, PAGE_MARGIN, appendix_html

__all__ = [
    "assert_no_marker_links",
    "build_protected_viewer_artifacts",
    "build_searchable_pdf",
    "compose",
    "page_dimensions",
]

LOGGER = logging.getLogger("webshot")

PDFA_OUTPUT_TYPE = "pdfa-3"
PDFA_LABEL = "PDF/A-3B"


@dataclass(slots=True)
class ComposedPdf:
    """What the composition produced, for the manifest and the invariant check."""

    source_pages: int
    total_pages: int
    embedded_names: list[str] = field(default_factory=list)
    transcribed_pages: list[int] = field(default_factory=list)

    @property
    def appendix_pages(self) -> int:
        return self.total_pages - self.source_pages


# --------------------------------------------------------------------------- #
# Step 1 — pack the captured pages losslessly
# --------------------------------------------------------------------------- #


def page_dimensions(pixel_width: int, pixel_height: int) -> tuple[float, float]:
    """The PDF page size for one captured image, in points.

    Portrait pages get Letter width; wider-than-tall pages (slides) get a
    presentation width.  Either way the aspect ratio of the capture is kept, so
    nothing is stretched.
    """
    aspect = pixel_width / pixel_height
    if aspect <= 1:
        width = 612.0
        return width, width / aspect
    width = 960.0
    return width, width / aspect


def _layout(
    pixel_width: int, pixel_height: int, dpi: object
) -> tuple[float, float, float, float]:
    """img2pdf layout hook: fill a page of WebShot's own size with the image.

    Without this, img2pdf would size the page from the image's DPI metadata,
    which a screenshot does not meaningfully carry.
    """
    width, height = page_dimensions(pixel_width, pixel_height)
    return width, height, width, height


def pack_page_images(pages: Sequence[Path], destination: Path) -> None:
    """Embed the captured PNGs into a PDF without re-encoding a single pixel.

    Streamed rather than returned: without `outputstream`, img2pdf builds the
    whole packed PDF in a BytesIO and copies it out again, so a large capture
    would hold two extra copies of it in memory while the browser is still up.
    """
    with destination.open("wb") as handle:
        img2pdf.convert(
            [str(page) for page in pages], layout_fun=_layout, outputstream=handle
        )


# --------------------------------------------------------------------------- #
# Step 2 — the invisible text layer (and, optionally, PDF/A)
# --------------------------------------------------------------------------- #


def add_text_layer(
    packed: Path,
    destination: Path,
    *,
    sidecar: Path,
    language: str,
    psm: int,
    workers: int,
    pdfa: bool,
) -> None:
    """Run OCRmyPDF over the packed pages, leaving the images untouched."""
    # OCRmyPDF narrates its pipeline at INFO. WebShot's own log is the one the
    # operator asked for, so only OCRmyPDF's warnings are let through.
    logging.getLogger("ocrmypdf").setLevel(logging.WARNING)
    try:
        ocrmypdf.ocr(
            packed,
            destination,
            sidecar=sidecar,
            language=language,
            tesseract_pagesegmode=psm,
            output_type=PDFA_OUTPUT_TYPE if pdfa else "pdf",
            optimize=0,
            jobs=max(1, min(workers, 8)),
            progress_bar=False,
        )
    except ocrmypdf.exceptions.MissingDependencyError as exc:
        raise EnvironmentFailure(
            f"OCRmyPDF cannot build the text layer: {exc}. Run `webshot doctor` to "
            "see which tool is missing and how to install it."
        ) from exc
    except ocrmypdf.exceptions.ExitCodeException as exc:
        raise PdfValidationError(
            f"OCRmyPDF failed to add the searchable text layer: {exc}"
        ) from exc
    # OCRmyPDF writes the sidecar in text mode, so on Windows each "\n" in it
    # arrives as "\r\n" (docs/09 P10-27). It builds the file from page text it
    # has just read back with universal newlines, which leaves no "\r" of the
    # text's own: every "\r\n" here is that translation, and undoing it gives
    # the bytes a macOS or Linux run writes.
    if sidecar.is_file():
        written = sidecar.read_bytes()
        if b"\r\n" in written:
            sidecar.write_bytes(written.replace(b"\r\n", b"\n"))


# --------------------------------------------------------------------------- #
# Steps 3 and 4 — appendix, bookmarks, back-links, associated files
# --------------------------------------------------------------------------- #


def _rewrite_back_links(writer: PdfWriter, source_pages: int) -> dict[int, int]:
    """The shared rewrite, named for the source pages it links back to.

    Kept as a thin alias rather than inlined at the call site: the protected
    path's back-links mean "the original visual source page", and that is worth
    saying in the name where the composition reads it.
    """
    return rewrite_back_links(writer, source_pages, label="source page")


def compose(
    text_layer: Path,
    appendix: Path,
    destination: Path,
    *,
    source_pages: int,
    title: str,
    payloads: dict[str, EmbeddedPayload],
) -> ComposedPdf:
    """Append the appendix to the recognized pages and finish the document.

    The writer is cloned from the OCRmyPDF output rather than started empty, so
    the catalog that conversion produced — the PDF/A identification XMP and the
    output intent — is carried into the composed file instead of being dropped.
    """
    writer = PdfWriter(clone_from=str(text_layer))
    writer.append(str(appendix), import_outline=False)
    total_pages = len(writer.pages)

    section_start = _rewrite_back_links(writer, source_pages)

    original = writer.add_outline_item("Original visual document", 0)
    for number in range(1, source_pages + 1):
        writer.add_outline_item(f"Source page {number}", number - 1, parent=original)
    transcript = writer.add_outline_item(
        "AI-readable transcript appendix", source_pages
    )
    for number, index in sorted(section_start.items()):
        writer.add_outline_item(
            f"Transcript for source page {number}", index, parent=transcript
        )

    embedded_names = embed_associated_files(writer, payloads)
    writer.root_object[NameObject("/Lang")] = TextStringObject("en-US")
    writer.root_object[NameObject("/PageMode")] = NameObject("/UseOutlines")
    writer.add_metadata(
        {
            "/Title": title,
            "/Subject": (
                "Self-contained AI-ready document with original visual pages, "
                "positioned OCR, transcript appendix, and embedded structured data"
            ),
            "/Creator": f"WebShot {GENERATOR_VERSION}",
            "/Keywords": (
                "AI-ready, OCR, machine-readable transcript, embedded JSON, "
                "word coordinates, charts, tables, images"
            ),
            "/WebShotAIReady": "true",
            "/WebShotSourcePages": str(source_pages),
            "/WebShotEmbeddedFiles": ", ".join(embedded_names),
        }
    )
    with destination.open("wb") as handle:
        writer.write(handle)
    return ComposedPdf(
        source_pages=source_pages,
        total_pages=total_pages,
        embedded_names=embedded_names,
        transcribed_pages=sorted(section_start),
    )


def build_searchable_pdf(
    pages: Sequence[Path],
    ocr_pages: Sequence[OCRPage],
    destination: Path,
    *,
    title: str,
    payloads: dict[str, EmbeddedPayload],
    sidecar: Path,
    ocr_language: str,
    ocr_psm: int,
    ocr_workers: int,
    pdfa: bool,
) -> ComposedPdf:
    """Run the four composition steps into `destination`, in order."""
    work = Path(tempfile.mkdtemp(prefix="webshot-protected-assembly-"))
    try:
        LOGGER.info("Packing %d source page(s) losslessly...", len(pages))
        packed = work / "pages.pdf"
        pack_page_images(pages, packed)

        LOGGER.info(
            "Adding the searchable text layer%s...",
            " and converting to PDF/A-3" if pdfa else "",
        )
        text_layer = work / "text-layer.pdf"
        add_text_layer(
            packed,
            text_layer,
            sidecar=sidecar,
            language=ocr_language,
            psm=ocr_psm,
            workers=ocr_workers,
            pdfa=pdfa,
        )

        appendix = work / "appendix.pdf"
        render_html_to_pdf(
            appendix_html(
                title=title,
                generator_version=GENERATOR_VERSION,
                pages=ocr_pages,
                page_text=[complete_text(page) for page in ocr_pages],
            ),
            appendix,
            paper_format=PAGE_FORMAT,
            margin=PAGE_MARGIN,
        )
        return compose(
            text_layer,
            appendix,
            destination,
            source_pages=len(pages),
            title=title,
            payloads=payloads,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Publication
# --------------------------------------------------------------------------- #


def _verify(pdf_path: Path, composed: ComposedPdf) -> None:
    """Everything that must be true before a protected capture is published.

    Checked against the file rather than against the numbers that produced it:
    the point of docs/04-spec.md §2.2's page-count invariant is to catch a
    composition that lost or gained a page, and comparing a value to itself
    would never catch anything.
    """
    reader = PdfReader(str(pdf_path))
    expected_pages = composed.source_pages
    if len(reader.pages) != expected_pages + composed.appendix_pages:
        raise CaptureIntegrityError(
            f"Page-count invariant broken: the composed PDF has "
            f"{len(reader.pages)} pages, not {expected_pages} source + "
            f"{composed.appendix_pages} appendix"
        )
    missing_transcripts = set(range(1, expected_pages + 1)) - set(
        composed.transcribed_pages
    )
    if missing_transcripts:
        raise CaptureIntegrityError(
            "The transcript appendix has no back-linked section for source page(s): "
            + ", ".join(str(number) for number in sorted(missing_transcripts))
        )
    missing_attachments = set(composed.embedded_names) - set(reader.attachments)
    if missing_attachments:
        raise CaptureIntegrityError(
            "Composed PDF is missing embedded AI data: "
            + ", ".join(sorted(missing_attachments))
        )
    assert_no_marker_links(reader)


def _verified_pages(page_directory: Path, expected_pages: int) -> list[Path]:
    pages = sorted(page_directory.glob("page-*.png"))
    if len(pages) != expected_pages:
        raise CaptureIntegrityError(
            f"Expected {expected_pages} page images but found {len(pages)} in "
            f"{page_directory}"
        )
    expected_names = [
        f"page-{number:03d}.png" for number in range(1, expected_pages + 1)
    ]
    if [page_path.name for page_path in pages] != expected_names:
        raise CaptureIntegrityError("Page image sequence is incomplete or out of order")
    return pages


def build_protected_viewer_artifacts(
    page_directory: Path,
    output_pdf: Path,
    *,
    source_url: str,
    title: str,
    expected_pages: int,
    ocr_language: str = "eng",
    ocr_psm: int = 11,
    ocr_workers: int = 4,
    pdfa: bool = False,
    capture_metadata: dict[str, Any] | None = None,
    ai_directory: Path | None = None,
    extra_warnings: Sequence[str] = (),
    on_stage: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Create a searchable PDF and page-addressable AI bundle atomically."""
    pages = _verified_pages(page_directory, expected_pages)
    dimensions: list[tuple[int, int]] = []
    for page_path in pages:
        # `verify` walks and CRCs every chunk, so it is a full read of the file.
        # One open does both jobs: `size` is populated before `verify` consumes
        # the stream.
        with Image.open(page_path) as image:
            dimensions.append(image.size)
            image.verify()

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    ai_directory = ai_directory or output_pdf.with_suffix(".ai")
    # The staging directory is a sibling of the destination — so the publish is
    # a rename on one filesystem — which means the destination's parent has to
    # exist before `mkdtemp` is asked to create anything in it. With
    # `--ai-bundle-dir` pointing somewhere new, it did not, and `mkdtemp`
    # raised `FileNotFoundError` before a single artifact was built: a
    # documented output path that only worked if the user had already made the
    # directories. The web path creates its destination's parent; this one now
    # does too (docs/09 P8-48).
    ai_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{ai_directory.name}.", dir=str(ai_directory.parent))
    )
    temporary_pdf = output_pdf.with_name(
        f".{output_pdf.stem}.{os.getpid()}.viewer.tmp.pdf"
    )

    def mark(stage: str) -> None:
        """Report a finished stage to the caller's clock, if it kept one.

        This function is one call from the pipeline's point of view and four
        stages from the user's: OCR, bundle, render, publish. Reported as one
        `package` mark, the QA report attributed every second of recognition
        and rendering to packaging and showed no `extract` or `render` at all
        — for a run that unmistakably did both (docs/09 P8-41).
        """
        if on_stage is not None:
            on_stage(stage)

    try:
        ocr_pages = recognize_pages(pages, ocr_language, ocr_psm, ocr_workers)
        mark("extract")
        bundle = build_viewer_bundle(
            staging,
            pages=pages,
            dimensions=dimensions,
            ocr_pages=ocr_pages,
            title=title,
            source_url=source_url,
            ocr_language=ocr_language,
            ocr_psm=ocr_psm,
            capture_metadata=capture_metadata,
        )
        composed = build_searchable_pdf(
            pages,
            ocr_pages,
            temporary_pdf,
            title=title,
            payloads=bundle.payloads,
            sidecar=bundle.text_layer_sidecar,
            ocr_language=ocr_language,
            ocr_psm=ocr_psm,
            ocr_workers=ocr_workers,
            pdfa=pdfa,
        )
        _verify(temporary_pdf, composed)
        # docs/04-spec.md §5.11 on this path too. The web path measures its
        # text layer in the pipeline, and this path returns from the pipeline
        # before that line, so the check never ran on a protected capture
        # (docs/09 P18-3). Measured here because this is the last point before
        # the manifest is written: on the composed file, appendix and
        # embedded files included, which is the file that is published. The
        # OCRmyPDF layer's font maps only the glyphs it uses, as Chromium's
        # does, so a clean capture reads clean rather than naming every form.
        warnings = list(extra_warnings)
        forms = ligature_forms(temporary_pdf)
        if forms:
            warnings.append(ligature_warning(forms))
        mark("render")
        manifest = viewer_manifest(
            bundle,
            title=title,
            source_url=source_url,
            ocr_pages=ocr_pages,
            pdf={
                "file": relative_pdf_path(output_pdf, ai_directory),
                "pages": composed.total_pages,
                "source_pages": expected_pages,
                "transcript_appendix_pages": composed.appendix_pages,
                "bytes": temporary_pdf.stat().st_size,
                "sha256": sha256_file(temporary_pdf),
                "searchable_ocr_layer": True,
                "text_layer_engine": f"OCRmyPDF {ocrmypdf.__version__}",
                "pdfa": PDFA_LABEL if pdfa else None,
                # Honest per docs/09 S8: the appendix's structure tree does not
                # survive the merge, and image pages have nothing to tag.
                "tagged": False,
                "bookmarks": True,
                "visible_transcript_appendix": True,
                "embedded_machine_readable_files": composed.embedded_names,
                "word_coordinates_embedded": True,
                "original_visual_pages_first": True,
            },
            extra_warnings=warnings,
        )
        write_json(staging / "manifest.json", manifest)

        pdf_backup = output_pdf.with_name(f".{output_pdf.name}.{os.getpid()}.backup")
        pdf_backup.unlink(missing_ok=True)
        announce_replacements(output_pdf, ai_directory)
        if output_pdf.exists():
            os.replace(output_pdf, pdf_backup)
        try:
            os.replace(temporary_pdf, output_pdf)
            publish_directory(staging, ai_directory)
        except Exception:
            if output_pdf.exists():
                output_pdf.unlink()
            if pdf_backup.exists():
                os.replace(pdf_backup, output_pdf)
            raise
        else:
            pdf_backup.unlink(missing_ok=True)
        return manifest
    finally:
        temporary_pdf.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
