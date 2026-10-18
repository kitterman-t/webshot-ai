"""Protected-viewer page stitching, PDF geometry, and the composed deliverable.

The composition tests build a real PDF from a real image: img2pdf, OCRmyPDF,
Chromium and pypdf all run.  They are marked `browser` because of the appendix
render, and they are the cheapest place where the page-count invariant, the
back-links, and the associated-file requirements of PDF/A-3 are checked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.async_api import async_playwright
from pypdf import PdfReader
from pypdf.generic import ArrayObject, NumberObject

from webshot.bundle.publish import EmbeddedPayload
from webshot.config import DEFAULT_VIEWPORT
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
from webshot.protected.assemble import (
    build_searchable_pdf,
    document_page_dimensions,
    pack_page_images,
    page_dimensions,
)
from webshot.protected.viewer import (
    PAGE_ELEMENT,
    PAGE_LABEL_PATTERN,
    VIEWER_CONTAINER,
    ZOOM_FIT_SLIDE,
    ZOOM_MENU_NAME,
    ZOOM_WINDOW_SIZE,
    capture_sharepoint_pdf_viewer,
    stitch_segmented_pages,
)

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

    result = stitch_segmented_pages(raw, pages, page_heights={1: 100})
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
        stitch_segmented_pages(raw, tmp_path / "pages", page_heights={1: 100})


def test_portrait_pages_use_letter_width() -> None:
    assert page_dimensions(850, 1100) == (612.0, 792.0)


def test_widescreen_pages_preserve_aspect_ratio() -> None:
    assert page_dimensions(960, 540) == (960.0, 540.0)


def _flat(pairs: list[tuple[float, float]]) -> list[float]:
    """`pytest.approx` compares flat sequences only."""
    return [side for pair in pairs for side in pair]


def test_a_document_s_pages_share_one_scale() -> None:
    """Each page was sized by itself, Letter-wide if portrait and 960pt if
    wider than tall, so a landscape Letter page among portrait ones came out
    13.33 x 10.30in and a half-width page Letter-wide (docs/09 P46). The
    pages of `sharepoint-portrait-mixed.html`, as the capture writes them:"""
    sizes = [(1632, 2112), (2112, 1632), (1632, 2688), (1632, 3072), (1056, 1224)]
    inches = [
        (width / 72, height / 72) for width, height in document_page_dimensions(sizes)
    ]
    assert _flat(inches) == pytest.approx(
        _flat([(8.5, 11), (11, 8.5), (8.5, 14), (8.5, 16), (5.5, 6.375)])
    )


def test_the_scale_is_the_commonest_page_s() -> None:
    """Not the first page's: a half-width cover would otherwise blow every
    page after it up by half again."""
    sizes = [(1056, 1224), (1632, 2112), (1632, 2112)]
    assert _flat(document_page_dimensions(sizes)) == pytest.approx(
        _flat([(396, 459), (612, 792), (612, 792)])
    )


def test_a_document_of_one_page_size_is_packed_as_it_was() -> None:
    """Exactly, not approximately: the packed PDF's page boxes, and with them
    the protected golden, must not move for a document whose pages agree."""
    assert (
        document_page_dimensions([(1275, 1650)] * 2)
        == [page_dimensions(1275, 1650)] * 2
    )
    assert (
        document_page_dimensions([(1700, 2200), (1700, 2200)])
        == [page_dimensions(1700, 2200)] * 2
    )


def test_packed_pages_keep_their_relative_sizes(tmp_path: Path) -> None:
    """The scale reaches the packed file, page by page."""
    images = []
    for number, size in enumerate(((816, 1056), (1056, 816), (528, 612)), start=1):
        path = tmp_path / f"page-{number:03d}.png"
        Image.new("RGB", size, "white").save(path)
        images.append(path)
    packed = tmp_path / "packed.pdf"
    pack_page_images(images, packed)
    boxes = [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in PdfReader(packed).pages
    ]
    assert _flat(boxes) == pytest.approx(_flat([(612, 792), (792, 612), (396, 459)]))


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
        stitch_segmented_pages(raw, tmp_path / "pages", page_heights={1: 1100})


def test_device_pixel_dimensions_stitch_to_the_exact_page(tmp_path: Path) -> None:
    """And the same geometry in one consistent unit works."""
    raw = _segments(tmp_path, (1600, 1800))
    pages = stitch_segmented_pages(raw, tmp_path / "pages", page_heights={1: 2200})
    with Image.open(pages[0]) as stitched:
        assert stitched.size == (1600, 2200)


def test_each_page_is_stitched_at_its_own_size(tmp_path: Path) -> None:
    """The stitch took one height for every page (docs/09 P46). Page 2 here is
    wider and taller than page 1, and page 3 was shot in one piece, so it is
    not the stitch's to touch."""
    raw = tmp_path / "segments"
    raw.mkdir()
    for number, size in ((1, (1600, 1800)), (2, (2000, 1800))):
        for half, colour in (("top", (240, 240, 240)), ("bottom", (200, 200, 200))):
            Image.new("RGB", size, colour).save(raw / f"page-{number:03d}-{half}.png")
    pages = stitch_segmented_pages(
        raw, tmp_path / "pages", page_heights={2: 3000, 1: 2200}
    )
    assert [path.name for path in pages] == ["page-001.png", "page-002.png"]
    sizes = []
    for path in pages:
        with Image.open(path) as stitched:
            sizes.append(stitched.size)
    assert sizes == [(1600, 2200), (2000, 3000)]


# --------------------------------------------------------------------------- #
# The segmented capture, driven in a browser
# --------------------------------------------------------------------------- #

VIEWER_DOM = Path(__file__).resolve().parent / "fixtures" / "viewer" / "dom"

#: Every band the fixture draws, page by page: its offset from its page's top
#: and its height in CSS pixels, and its colour; and each page's own size. Read
#: from the page rather than retyped, so the fixture stays the one description
#: of what each page shows.
BANDS_JS = """(contract) => {
    const labelPattern = new RegExp(contract.pageLabel);
    const colour = element => getComputedStyle(element).backgroundColor
        .match(/\\d+/g).slice(0, 3).map(Number);
    const viewer = document.querySelector(contract.container);
    const elements = [...document.querySelectorAll(contract.page)]
        .filter(element => labelPattern.test(element.getAttribute('aria-label') || ''));
    const pages = elements.map(element => {
        const top = element.getBoundingClientRect().top;
        return [...element.querySelectorAll('.canvas-stand-in > div')].map(band => {
            const rect = band.getBoundingClientRect();
            return {offset: rect.top - top, height: rect.height, colour: colour(band)};
        });
    });
    const sizes = elements.map(element => {
        const rect = element.getBoundingClientRect();
        return {width: rect.width, height: rect.height};
    });
    return {pages, sizes, viewer: colour(viewer)};
}"""


async def _capture_from_the_last_page(
    fixture: Path,
    directory: Path,
    *,
    style: str | None = None,
    script: str | None = None,
    zoom: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the protected capture on a fixture a reader left at its last page.

    That is P8-45's case: the viewer is wherever the person signing in left
    it, so the capture's first scroll crosses the document rather than one
    page, and a capture that took the viewer's scroll position for the
    page's offset would shoot every page from the wrong place. `script` is a
    function of the viewer contract, run once the bands are read. `zoom`
    names the zoom-menu item the bands are read at, for a deck the capture
    shoots at another zoom than the one it opens at; the toolbar is put back
    to "Window size" before the capture starts.
    """
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            # The geometry `launch_context` gives a real capture.
            context = await browser.new_context(
                viewport=DEFAULT_VIEWPORT, device_scale_factor=2
            )
            page = await context.new_page()
            await page.goto(fixture.as_uri())
            if style:
                await page.add_style_tag(content=style)
            contract = {
                "container": VIEWER_CONTAINER,
                "page": PAGE_ELEMENT,
                "pageLabel": PAGE_LABEL_PATTERN,
            }
            zoom_menu = page.get_by_role("menuitem", name=ZOOM_MENU_NAME, exact=True)
            if zoom:
                await zoom_menu.click()
                await page.get_by_role(
                    "menuitemcheckbox", name=zoom, exact=True
                ).click()
            drawn: dict[str, Any] = await page.evaluate(BANDS_JS, contract)
            if zoom:
                await zoom_menu.click()
                await page.get_by_role(
                    "menuitemcheckbox", name=ZOOM_WINDOW_SIZE, exact=True
                ).click()
            if script:
                await page.evaluate(script, contract)
            await page.evaluate(
                """(selector) => {
                    const viewer = document.querySelector(selector);
                    viewer.scrollTo({top: viewer.scrollHeight, behavior: 'instant'});
                }""",
                VIEWER_CONTAINER,
            )
            result = await capture_sharepoint_pdf_viewer(page, directory)
        finally:
            await browser.close()
    return result, drawn


def _band_runs(
    image: Image.Image, colours: dict[str, tuple[int, ...]]
) -> list[tuple[str, int, int]]:
    """Name each row of the image's middle column by the band it shows.

    Neighbouring rows of one band merge into `(name, first row, row after)`.
    A colour that is no band's is named by its value, so a failure prints
    what the stitched page holds rather than only that it is wrong.
    """
    rgb = image.convert("RGB")
    middle = rgb.width // 2
    runs: list[tuple[str, int, int]] = []
    for row in range(rgb.height):
        pixel = rgb.getpixel((middle, row))
        assert isinstance(pixel, tuple)
        name = next(
            (
                label
                for label, colour in colours.items()
                if all(abs(a - b) <= 2 for a, b in zip(pixel, colour, strict=True))
            ),
            f"rgb{pixel}",
        )
        if runs and runs[-1][0] == name and runs[-1][2] == row:
            runs[-1] = (name, runs[-1][1], row + 1)
        else:
            runs.append((name, row, row + 1))
    return runs


def _misplaced_pages(directory: Path, ratio: float, drawn: dict[str, Any]) -> list[str]:
    """Every stitched page that is not its own page, band for band and size
    for size, each named with what it holds.

    Every page is checked before anything is reported, so a failure shows the
    whole document rather than the first page that went wrong.
    """
    colours = {
        f"page {number} band {index}": tuple(band["colour"])
        for number, bands in enumerate(drawn["pages"], start=1)
        for index, band in enumerate(bands)
    }
    colours["viewer"] = tuple(drawn["viewer"])
    wrong: list[str] = []
    for number, (bands, size) in enumerate(
        zip(drawn["pages"], drawn["sizes"], strict=True), start=1
    ):
        expected = [
            (
                f"page {number} band {index}",
                round(band["offset"] * ratio),
                round((band["offset"] + band["height"]) * ratio),
            )
            for index, band in enumerate(bands)
        ]
        expected_size = (round(size["width"] * ratio), round(size["height"] * ratio))
        with Image.open(directory / f"page-{number:03d}.png") as image:
            found = _band_runs(image, colours)
            found_size = image.size
        if found != expected or found_size != expected_size:
            wrong.append(
                f"page {number}: {found_size} px, expected {expected_size};\n"
                f"  found    {found}\n  expected {expected}"
            )
    return wrong


@pytest.mark.browser
@pytest.mark.parametrize(
    "fixture", ["sharepoint-portrait.html", "sharepoint-portrait-smooth.html"]
)
def test_each_stitched_page_is_its_own_page_band_for_band(
    fixture: str, tmp_path: Path
) -> None:
    """No two of the fixture's bands share a colour, so a segment shot from
    anywhere but where its clip assumes puts a band out of place, or out of
    length, in the stitched page. On the smooth fixture page 1's top segment
    was shot 89 to 159px short, with the viewer still on its way up from the
    last page (docs/09 P42)."""
    pages = tmp_path / "pages"
    result, drawn = asyncio.run(
        _capture_from_the_last_page(VIEWER_DOM / fixture, pages)
    )

    assert result["segmented"], "the fixture's pages must be taller than its viewer"
    assert result["page_count"] == len(drawn["pages"]) == 3
    wrong = _misplaced_pages(pages, result["device_pixel_ratio"], drawn)
    assert not wrong, "\n".join(wrong)
    # Device pixels, the size of every image written, so a reader can tell
    # them from CSS pixels without guessing the scale (docs/09 P8-46).
    assert result["device_pixel_ratio"] == 2
    assert (result["page_width"], result["page_height"]) == (1632, 2112)
    assert result["segment_height"] == 1640
    assert result["segment_overlap"] == 1640 * 2 - 2112


#: A viewer that keeps only the pages it shows in the DOM, putting each back
#: where it was when the view reaches it. Whether SharePoint's does is not
#: recorded, so the capture must not depend on it either way.
VIRTUALIZE_JS = """(contract) => {
    const viewer = document.querySelector(contract.container);
    const labelPattern = new RegExp(contract.pageLabel);
    const slots = [...viewer.querySelectorAll(contract.page)]
        .filter(element => labelPattern.test(element.getAttribute('aria-label')))
        .map(element => ({
            element,
            top: element.offsetTop,
            bottom: element.offsetTop + element.offsetHeight,
            stand: document.createComment(element.getAttribute('aria-label'))
        }));
    const draw = () => {
        const from = viewer.scrollTop, to = from + viewer.clientHeight;
        for (const slot of slots) {
            const shown = slot.top < to && slot.bottom > from;
            if (shown && !slot.element.isConnected) slot.stand.replaceWith(slot.element);
            if (!shown && slot.element.isConnected) slot.element.replaceWith(slot.stand);
        }
    };
    viewer.addEventListener('scroll', draw);
    draw();
}"""


def _sizes_in_device_pixels(
    drawn: dict[str, Any], ratio: float
) -> list[tuple[int, int]]:
    return [
        (round(size["width"] * ratio), round(size["height"] * ratio))
        for size in drawn["sizes"]
    ]


@pytest.mark.browser
@pytest.mark.parametrize("virtual", [False, True], ids=["every-page", "pages-in-view"])
def test_pages_of_differing_sizes_are_each_shot_from_their_own_rectangle(
    virtual: bool, tmp_path: Path
) -> None:
    """Every page was shot where page 1's position and height, and the step
    from page 1 to page 2, put it, at page 1's width (docs/09 P42-5). In a
    document of Letter, landscape, Legal and appended-form pages and a short
    last page, every page after the first came out page 1's size: pages 2, 4
    and 5 held parts of their neighbours, page 3 began 240px into itself, and
    the capture reported nothing (docs/09 P46).

    The short last page cannot be scrolled to its top, so it is shot where
    it stands. With only the pages in view in the DOM, the reader's last
    page is the first one there, and every other page has to be scrolled to
    before it can be found."""
    pages = tmp_path / "pages"
    result, drawn = asyncio.run(
        _capture_from_the_last_page(
            VIEWER_DOM / "sharepoint-portrait-mixed.html",
            pages,
            script=VIRTUALIZE_JS if virtual else None,
        )
    )

    assert result["page_count"] == len(drawn["pages"]) == 5
    ratio = result["device_pixel_ratio"]
    wrong = _misplaced_pages(pages, ratio, drawn)
    assert not wrong, "\n".join(wrong)

    sizes = _sizes_in_device_pixels(drawn, ratio)
    assert [
        (image["page"], image["width"], image["height"], image["segments"])
        for image in result["page_images"]
    ] == [
        (number, width, height, 2 if height > result["segment_height"] else 1)
        for number, (width, height) in enumerate(sizes, start=1)
    ]
    # The fixture is built to need both: pages that fit the viewer and pages
    # that do not.
    assert {image["segments"] for image in result["page_images"]} == {1, 2}
    # No one size describes these pages, so none is claimed.
    assert result["page_width"] is None
    assert result["page_height"] is None
    assert result["segment_overlap"] is None
    assert result["segmented"] is True


@pytest.mark.browser
def test_a_single_page_document_is_captured(tmp_path: Path) -> None:
    """A single-page letter exposes one page rectangle, and the capture
    stopped for want of a second to measure the spacing from (docs/09
    P8-47). Nothing about another page is needed to shoot one now."""
    pages = tmp_path / "pages"
    result, drawn = asyncio.run(
        _capture_from_the_last_page(
            VIEWER_DOM / "sharepoint-portrait.html",
            pages,
            script="""() => {
                document.querySelector('[aria-label="Page 2."]').remove();
                document.querySelector('[aria-label="Page 3."]').remove();
                document.querySelector('[aria-label="Page 1 of 3"]')
                    .setAttribute('aria-label', 'Page 1 of 1');
            }""",
        )
    )

    assert result["page_count"] == 1
    assert sorted(path.name for path in pages.glob("page-*.png")) == ["page-001.png"]
    drawn = {**drawn, "pages": drawn["pages"][:1], "sizes": drawn["sizes"][:1]}
    wrong = _misplaced_pages(pages, result["device_pixel_ratio"], drawn)
    assert not wrong, "\n".join(wrong)


@pytest.mark.browser
def test_a_page_wider_than_the_viewer_is_refused(tmp_path: Path) -> None:
    """The capture never scrolls sideways, so a page the viewer shows only
    part of across would be shot without its edges. In a 900px viewer the
    1,056px landscape page is."""
    with pytest.raises(RuntimeError, match=r"Page 2 is 1056px wide and the viewer"):
        asyncio.run(
            _capture_from_the_last_page(
                VIEWER_DOM / "sharepoint-portrait-mixed.html",
                tmp_path / "pages",
                style=".viewer { width: 900px; }",
            )
        )


@pytest.mark.browser
def test_a_segment_the_viewer_would_not_scroll_to_is_refused(tmp_path: Path) -> None:
    """A viewer that snaps its scroll to points inside its pages keeps a
    segment's scroll from taking. Asked for page 1's bottom segment at 260px,
    this one goes to the band that starts at 216px. Nothing read the position
    back, so every bottom segment was shot 44px high: each stitched page
    carried 44px of itself twice at the seam and lost its last 44px, and the
    capture reported nothing (docs/09 P42).

    The snap points are the bands, not the pages: a snap area taller than the
    viewer lets any position inside it stand, so snapping to whole pages would
    hold every segment where it was put."""
    with pytest.raises(RuntimeError, match=r"scrolled to 260px .* is at 216px"):
        asyncio.run(
            _capture_from_the_last_page(
                VIEWER_DOM / "sharepoint-portrait.html",
                tmp_path / "pages",
                style=(
                    ".viewer { scroll-snap-type: y mandatory; } "
                    ".canvas-stand-in > div { scroll-snap-align: start; }"
                ),
            )
        )


# --------------------------------------------------------------------------- #
# The deck, paged through the toolbar
# --------------------------------------------------------------------------- #


@pytest.mark.browser
@pytest.mark.parametrize("virtual", [False, True], ids=["every-page", "pages-in-view"])
def test_slides_of_differing_sizes_are_each_shot_from_their_own_rectangle(
    virtual: bool, tmp_path: Path
) -> None:
    """Every slide was clipped at page 1's rectangle at the top of the viewer.
    In a deck of a 16:9 slide, a 4:3 one and a portrait page, slides 2 and 3
    came out 2112 x 1188, slide 1's size: slide 2 with the viewer's
    background beside it and 18px of it below, slide 3 without its last 18px
    (docs/09 P51).

    With only the slides in view in the DOM, the reader's last slide, the
    portrait page, was the one the orientation was read from. The deck took
    the scroll path at "Window size", and every slide came out a third
    larger than the toolbar path shoots it. Page 1 decides now, and the
    viewer is scrolled to its top to find it."""
    pages = tmp_path / "pages"
    result, drawn = asyncio.run(
        _capture_from_the_last_page(
            VIEWER_DOM / "sharepoint-landscape-mixed.html",
            pages,
            zoom=ZOOM_FIT_SLIDE,
            script=VIRTUALIZE_JS if virtual else None,
        )
    )

    assert result["page_count"] == len(drawn["pages"]) == 3
    # The helper's scale factor, so the slides are checked before anything
    # the capture reports about itself.
    ratio = 2
    wrong = _misplaced_pages(pages, ratio, drawn)
    assert not wrong, "\n".join(wrong)
    assert result["viewer_zoom_for_capture"] == ZOOM_FIT_SLIDE
    assert result["device_pixel_ratio"] == ratio
    assert [
        (image["page"], image["width"], image["height"], image["segments"])
        for image in result["page_images"]
    ] == [
        (number, width, height, 1)
        for number, (width, height) in enumerate(
            _sizes_in_device_pixels(drawn, ratio), start=1
        )
    ]
    # No one size describes these slides, so none is claimed.
    assert result["page_width"] is None
    assert result["page_height"] is None
    assert result["segmented"] is False


@pytest.mark.browser
def test_a_slide_the_viewer_does_not_show_whole_is_refused(tmp_path: Path) -> None:
    """Only page 1 was checked against the viewer. In a 600px viewer the
    portrait page, 612px tall at 75%, was shot at page 1's 594px and lost
    its last 18px without a word (docs/09 P51)."""
    with pytest.raises(
        RuntimeError,
        match=r"Page 3 measures 456 x 612px at 75% zoom, .* the viewer shows "
        r"\d+(\.\d+)? x 600px",
    ):
        asyncio.run(
            _capture_from_the_last_page(
                VIEWER_DOM / "sharepoint-landscape-mixed.html",
                tmp_path / "pages",
                style=".viewer { height: 600px; }",
            )
        )


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
