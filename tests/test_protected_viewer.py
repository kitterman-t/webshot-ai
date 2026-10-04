"""Protected-viewer page stitching, PDF geometry, and the composed deliverable.

The composition tests build a real PDF from a real image: img2pdf, OCRmyPDF,
Chromium and pypdf all run.  They are marked `browser` because of the appendix
render, and they are the cheapest place where the page-count invariant, the
back-links, and the associated-file requirements of PDF/A-3 are checked.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ArrayObject, NumberObject

import webshot.protected.viewer as viewer_module
from webshot.bundle.publish import EmbeddedPayload
from webshot.errors import WebShotError
from webshot.ocr.tesseract import (
    OCRPage,
    OCRWord,
    complete_text,
    tesseract_executable,
)
from webshot.protected.appendix import (
    BACK_LINK_SCHEME,
    appendix_html,
    back_link_target,
)
from webshot.protected.assemble import build_searchable_pdf, page_dimensions
from webshot.protected.viewer import stitch_segmented_pages

PAYLOADS = {
    "webshot-ai-content.json": EmbeddedPayload(
        b'{"text":"Complete"}', "Structured OCR", "application/json"
    )
}


#: `build_searchable_pdf` runs OCRmyPDF, which runs Tesseract. The gated CI
#: installs it; the weekly Windows run does not, and failed these three on the
#: missing binary rather than on anything they assert (docs/09 P10-24).
needs_tesseract = pytest.mark.skipif(
    tesseract_executable() is None, reason="tesseract is not installed"
)


def _ocr_page(
    number: int = 1, text: str = "Complete machine-readable transcript."
) -> OCRPage:
    return OCRPage(
        page=number,
        text=text,
        confidence=98.0,
        words=[
            OCRWord(
                text="Complete",
                confidence=98.0,
                left=20,
                top=20,
                width=80,
                height=20,
                block=1,
                paragraph=1,
                line=1,
            )
        ],
        tsv="",
    )


def test_overlapping_segments_are_stitched_to_exact_page_height(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    pages = tmp_path / "pages"
    raw.mkdir()

    top = Image.new("RGB", (20, 60), "red")
    bottom = Image.new("RGB", (20, 60), "blue")
    top.save(raw / "page-001-top.png")
    bottom.save(raw / "page-001-bottom.png")

    result = stitch_segmented_pages(raw, pages, page_count=1, page_height=100)
    assert [path.name for path in result] == ["page-001.png"]
    with Image.open(result[0]) as stitched:
        assert stitched.size == (20, 100)
        assert stitched.getpixel((10, 10)) == (255, 0, 0)
        assert stitched.getpixel((10, 90)) == (0, 0, 255)


def test_missing_segment_is_rejected(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    Image.new("RGB", (20, 60), "red").save(raw / "page-001-top.png")
    with pytest.raises(FileNotFoundError):
        stitch_segmented_pages(raw, tmp_path / "pages", page_count=1, page_height=100)


def test_portrait_pages_use_letter_width() -> None:
    assert page_dimensions(850, 1100) == (612.0, 792.0)


def test_widescreen_pages_preserve_aspect_ratio() -> None:
    assert page_dimensions(960, 540) == (960.0, 540.0)


def test_back_link_targets_are_recognized_only_in_their_own_scheme() -> None:
    assert back_link_target(f"{BACK_LINK_SCHEME}12") == 12
    assert back_link_target("https://example.com/source-page/12") is None
    assert back_link_target(f"{BACK_LINK_SCHEME}not-a-page") is None


def test_appendix_names_every_page_and_escapes_its_text() -> None:
    pages = [_ocr_page(1), _ocr_page(2, "<script>alert(1)</script>")]
    html = appendix_html(
        title="Doc & Co",
        generator_version="9.9.9",
        pages=pages,
        page_text=[complete_text(page) for page in pages],
    )
    assert "Doc &amp; Co" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    for number in (1, 2):
        assert f"{BACK_LINK_SCHEME}{number}" in html
        assert f"Source Page {number}" in html


@pytest.mark.browser
@needs_tesseract
def test_composed_pdf_keeps_pages_transcript_and_embedded_data(tmp_path: Path) -> None:
    image_path = tmp_path / "page-001.png"
    output_path = tmp_path / "ai-ready.pdf"
    Image.new("RGB", (960, 540), "white").save(image_path)
    ocr_page = _ocr_page()

    composed = build_searchable_pdf(
        [image_path],
        [ocr_page],
        output_path,
        title="Test Document",
        payloads=PAYLOADS,
        sidecar=tmp_path / "sidecar.txt",
        ocr_language="eng",
        ocr_psm=11,
        ocr_workers=2,
        pdfa=False,
    )

    reader = PdfReader(str(output_path))
    assert composed.source_pages == 1
    assert composed.total_pages == composed.source_pages + composed.appendix_pages
    assert len(reader.pages) == composed.total_pages
    assert composed.transcribed_pages == [1]
    assert "Complete machine-readable transcript." in reader.pages[-1].extract_text()
    assert composed.embedded_names == ["webshot-ai-content.json"]
    assert "webshot-ai-content.json" in reader.attachments
    assert reader.metadata.get("/WebShotAIReady") == "true"
    assert (tmp_path / "sidecar.txt").is_file()


@pytest.mark.browser
@needs_tesseract
def test_embedded_files_carry_what_pdf_a_3_requires(tmp_path: Path) -> None:
    """ISO 19005-3 clause 6.8: /UF, a MIME /Subtype, and the catalog /AF array.

    pypdf writes none of the three on its own, and veraPDF rejects a file
    specification without them — which is how the gap was found.
    """
    image_path = tmp_path / "page-001.png"
    Image.new("RGB", (960, 540), "white").save(image_path)
    output_path = tmp_path / "embeds.pdf"

    build_searchable_pdf(
        [image_path],
        [_ocr_page()],
        output_path,
        title="Test Document",
        payloads=PAYLOADS,
        sidecar=tmp_path / "sidecar.txt",
        ocr_language="eng",
        ocr_psm=11,
        ocr_workers=2,
        pdfa=False,
    )

    reader = PdfReader(str(output_path))
    attachment = next(iter(reader.attachment_list))
    assert attachment.subtype == "/application/json"
    assert attachment.alternative_name == "webshot-ai-content.json"
    assert attachment.associated_file_relationship == "/Data"
    assert len(reader.trailer["/Root"]["/AF"]) == 1


@pytest.mark.browser
@needs_tesseract
def test_back_links_become_internal_jumps_and_no_uri_survives(tmp_path: Path) -> None:
    image_path = tmp_path / "page-001.png"
    Image.new("RGB", (960, 540), "white").save(image_path)
    output_path = tmp_path / "links.pdf"

    build_searchable_pdf(
        [image_path],
        [_ocr_page()],
        output_path,
        title="Test Document",
        payloads=PAYLOADS,
        sidecar=tmp_path / "sidecar.txt",
        ocr_language="eng",
        ocr_psm=11,
        ocr_workers=2,
        pdfa=False,
    )

    reader = PdfReader(str(output_path))
    actions = [
        annotation.get_object()["/A"].get_object()
        for page in reader.pages
        for annotation in page.get("/Annots") or []
        if "/A" in annotation.get_object()
    ]
    assert actions, "the appendix should carry at least one link"
    assert all(action["/S"] == "/GoTo" for action in actions)
    assert not any("/URI" in action for action in actions)
    assert BACK_LINK_SCHEME.encode() not in output_path.read_bytes()


def test_a_back_link_past_the_source_pages_is_refused(tmp_path: Path) -> None:
    """The guard is against the source-page count, not the merged page count.

    A link to "source page 7" of a two-page document must fail rather than land
    on the appendix's fifth page, which is what checking against the composed
    length would have allowed.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    from webshot.protected.appendix import BACK_LINK_SCHEME
    from webshot.protected.assemble import _rewrite_back_links

    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=200, height=200)
    writer.add_annotation(
        4,
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject(
                    [
                        NumberObject(0),
                        NumberObject(0),
                        NumberObject(10),
                        NumberObject(10),
                    ]
                ),
                NameObject("/A"): DictionaryObject(
                    {
                        NameObject("/S"): NameObject("/URI"),
                        NameObject("/URI"): TextStringObject(f"{BACK_LINK_SCHEME}7"),
                    }
                ),
            }
        ),
    )
    with pytest.raises(WebShotError, match="source page 7"):
        _rewrite_back_links(writer, source_pages=2)


# --------------------------------------------------------------------------- #
# Geometry: the units the stitch is given must be the units of the images
# --------------------------------------------------------------------------- #


def _segments(directory: Path, size: tuple[int, int], count: int = 1) -> Path:
    raw = directory / "segments"
    raw.mkdir(parents=True, exist_ok=True)
    for number in range(1, count + 1):
        for half, colour in (("top", (240, 240, 240)), ("bottom", (200, 200, 200))):
            Image.new("RGB", size, colour).save(raw / f"page-{number:03d}-{half}.png")
    return raw


def test_a_css_page_height_against_device_pixel_segments_is_rejected(
    tmp_path: Path,
) -> None:
    """The bug, stated as the harness sees it.

    The capture context sets `device_scale_factor=2` and `page.screenshot`
    defaults to device-scale output, so each segment PNG is twice as tall as
    the CSS rectangle it was clipped from — while `page_height` came from that
    CSS rectangle. `stitch_segmented_pages` then compared 1100 against 1800
    and refused the capture outright, on every multi-page portrait document
    (docs/09 P8-46).

    This test pins the *rejection*, so a future caller that mixes the units
    again fails here rather than in a SharePoint session nobody can re-run.
    """
    raw = _segments(tmp_path, (1600, 1800))
    with pytest.raises(ValueError, match="unnecessary"):
        stitch_segmented_pages(raw, tmp_path / "pages", page_count=1, page_height=1100)


def test_device_pixel_dimensions_stitch_to_the_exact_page(tmp_path: Path) -> None:
    """And the same geometry in one consistent unit works."""
    raw = _segments(tmp_path, (1600, 1800))
    pages = stitch_segmented_pages(
        raw, tmp_path / "pages", page_count=1, page_height=2200
    )
    with Image.open(pages[0]) as stitched:
        assert stitched.size == (1600, 2200)


def test_the_portrait_capture_reports_the_ratio_it_measured() -> None:
    """The returned metrics describe the files that were written, so a reader
    can tell CSS pixels from device pixels without guessing the scale."""
    source = inspect.getsource(viewer_module._capture_portrait_viewer)
    assert "window.devicePixelRatio" in source
    assert '"device_pixel_ratio": ratio' in source
    # `page_width` and `page_height` describe the same images, so both are
    # scaled or neither is.
    assert 'round(page_rect["width"] * ratio)' in source
    assert 'round(page_rect["height"] * ratio)' in source


def test_a_single_page_document_needs_no_spacing_sample() -> None:
    """One rectangle means no inter-page spacing to measure, and the check for
    it aborted the capture instead of noticing that (docs/09 P8-47)."""
    source = inspect.getsource(viewer_module._capture_portrait_viewer)
    assert 'metrics["pageCount"] > 1 and len(metrics["pages"]) < 2' in source
    # A page that fits inside the viewer is shot once, not stitched from two
    # segments that would not overlap.
    assert "segmented = page_height > segment_height" in source


def test_the_scroll_target_is_an_absolute_offset() -> None:
    """`pages[0].y` and `viewer.y` are both viewport-relative, so their
    difference is the page's offset in the *current* scroll state — written
    straight to `scrollTop`, which is a document coordinate (docs/09 P8-45)."""
    source = inspect.getsource(viewer_module._capture_portrait_viewer)
    assert 'page_top = viewer["scrollTop"] + (page_rect["y"] - viewer["y"])' in source


@pytest.mark.browser
@needs_tesseract
def test_a_second_assembly_announces_what_it_replaces(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The protected path publishes on its own, so it announces on its own too."""
    import logging

    from tools.golden.harness import assemble_viewer_fixture

    pdf, bundle = tmp_path / "capture.pdf", tmp_path / "capture.ai"
    with caplog.at_level(logging.INFO, logger="webshot"):
        assemble_viewer_fixture(tmp_path, pdf, bundle)
        first = caplog.text
        caplog.clear()
        assemble_viewer_fixture(tmp_path, pdf, bundle)
    assert "Replacing existing" not in first, first
    assert f"Replacing existing {pdf}" in caplog.text, caplog.text
    assert f"Replacing existing {bundle}" in caplog.text, caplog.text
