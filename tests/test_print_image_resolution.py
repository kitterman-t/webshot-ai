"""A picture printed far finer than paper can show is reduced (docs/09 P80).

A think tank's topic page set small display sizes on its photographers'
originals, and Chromium embedded each at its natural size: a 4-page capture
was 86 MB, its thumbnails 6720x4480 and 5100x3300 pixels printed at up to
2,300 pixels per inch. The pictures here are made in the test, of noise so
that neither JPEG nor Flate can make them small, and the page shows each the
way that page did: a large original at a small CSS size.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader

from webshot.cli import main
from webshot.render.images import (
    PRINT_PPI,
    REDUCE_ABOVE_PPI,
    image_reduction_warnings,
    reduce_oversized_images,
)


def _noise(size: tuple[int, int], mode: str = "RGB") -> Image.Image:
    bands = [Image.effect_noise(size, 64).convert("L") for _ in mode]
    return Image.merge(mode, bands)


def _page(directory: Path) -> Path:
    _noise((6000, 4000)).save(directory / "huge.jpg", quality=85)
    _noise((2000, 2000), "RGBA").save(directory / "alpha.png")
    _noise((3000, 2000)).save(directory / "faded.jpg", quality=85)
    _noise((400, 300)).save(directory / "ordinary.jpg", quality=85)
    page = directory / "page.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Pictures</title></head><body><h1>Pictures</h1>"
        '<p>A large original shown small.</p><img src="huge.jpg" width="200" alt="huge">'
        '<p>A transparent one.</p><img src="alpha.png" width="100" alt="alpha">'
        # Opacity puts the picture inside a transparency group, a form the
        # walk has to enter to find it.
        '<p>A faded one.</p><img src="faded.jpg" width="150" style="opacity:0.6" alt="faded">'
        '<p>An ordinary one.</p><img src="ordinary.jpg" width="200" alt="ordinary">'
        "</body></html>",
        encoding="utf-8",
    )
    return page


def _pictures(pdf: Path) -> list[Any]:
    """Every image object the pages draw, inside forms too, once each."""
    found: dict[int, Any] = {}

    def visit(resources: Any) -> None:
        xobjects = resources.get("/XObject") if resources else None
        for reference in (xobjects or {}).values():
            target = reference.get_object()
            if target["/Subtype"] == "/Image":
                found[reference.idnum] = target
            elif target["/Subtype"] == "/Form":
                visit(target.get("/Resources"))

    for page in PdfReader(str(pdf)).pages:
        visit(page.get("/Resources"))
    return list(found.values())


def _decoded(stream: Any) -> Image.Image:
    data = stream.get_data()
    if stream["/Filter"] == "/DCTDecode":
        return Image.open(io.BytesIO(data)).convert("RGB")
    mode = "L" if stream["/ColorSpace"] == "/DeviceGray" else "RGB"
    return Image.frombytes(mode, (int(stream["/Width"]), int(stream["/Height"])), data)


@pytest.mark.browser
def test_a_large_original_shown_small_prints_at_print_resolution(
    tmp_path: Path,
) -> None:
    page = _page(tmp_path)
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main([str(page), "--output", str(pdf), "--no-ocr", "--report", str(report)])
    assert code == 0

    # The originals are 11, 16 and 6 MB; Chromium embeds the PNG unpacked.
    assert pdf.stat().st_size < 2_000_000, pdf.stat().st_size
    sizes = sorted((int(p["/Width"]), int(p["/Height"])) for p in _pictures(pdf))
    assert (400, 300) in sizes, sizes  # 192 ppi: a screen's 2x picture, untouched
    assert all(width < 1300 for width, _ in sizes), sizes

    pictures = {(int(p["/Width"]), int(p["/Height"])): p for p in _pictures(pdf)}
    # The huge photo printed 200 CSS px wide, about 2.1 in at the print scale:
    # some 600 pixels, never the 6000 it arrived with.
    # The widest after the ordinary one, which is untouched: the faded one is
    # printed 150 px wide and the transparent one 100.
    huge = max(
        (size for size in pictures if size != (400, 300)), key=lambda size: size[0]
    )
    original = (
        Image.open(tmp_path / "huge.jpg")
        .convert("RGB")
        .resize(huge, Image.Resampling.LANCZOS)
    )
    difference = ImageStat.Stat(
        ImageChops.difference(original, _decoded(pictures[huge]))
    )
    # The same picture, the same way up, in the same colours.
    assert max(difference.mean) < 6, difference.mean

    for picture in pictures.values():
        if "/SMask" in picture:
            mask = picture["/SMask"].get_object()
            assert (mask["/Width"], mask["/Height"]) == (
                picture["/Width"],
                picture["/Height"],
            )
            break
    else:
        pytest.fail("the transparent picture lost its soft mask")

    warnings = json.loads(report.read_text(encoding="utf-8"))["warnings"]
    reduced = [w for w in warnings if w.startswith("Reduced ")]
    assert len(reduced) == 1, warnings
    assert "Reduced 3 pictures" in reduced[0]
    for original_size in ("6000x4000", "2000x2000", "3000x2000"):
        assert original_size in reduced[0], reduced[0]
    assert "400x300" not in reduced[0]
    assert "the originals are not in the capture" in reduced[0]
    bundle = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text(encoding="utf-8")
    )
    assert reduced[0] in bundle["warnings"]


def _pillow_pdf(path: Path, picture: Image.Image, ppi: float) -> Path:
    """A one-page PDF that draws `picture` filling the page at `ppi`."""
    picture.save(path, resolution=ppi)
    return path


def test_a_file_with_nothing_oversized_keeps_its_bytes(tmp_path: Path) -> None:
    pdf = _pillow_pdf(tmp_path / "plain.pdf", _noise((600, 400)), 200)
    before = pdf.read_bytes()
    result = reduce_oversized_images(pdf)
    assert result.reduced == () and result.kept == ()
    assert pdf.read_bytes() == before
    assert image_reduction_warnings(result) == []
    # The walk did find and measure the picture: a pass that saw nothing would
    # also have reduced nothing.
    [(width, height)] = result.drawn.values()
    assert math.isclose(width, 600 / 200 * 72) and math.isclose(height, 400 / 200 * 72)


def test_a_jpeg_stays_a_jpeg_at_the_print_resolution(tmp_path: Path) -> None:
    pdf = _pillow_pdf(tmp_path / "fine.pdf", _noise((3000, 2000)), 1500)
    result = reduce_oversized_images(pdf)
    [done] = result.reduced
    assert done.original == (3000, 2000)
    assert done.reduced == (math.ceil(2 * PRINT_PPI), math.ceil(4 / 3 * PRINT_PPI))
    assert done.ppi == pytest.approx(1500)
    [picture] = _pictures(pdf)
    assert picture["/Filter"] == "/DCTDecode"
    assert (picture["/Width"], picture["/Height"]) == done.reduced
    assert Image.open(io.BytesIO(picture.get_data())).size == done.reduced
    assert result.bytes_after < result.bytes_before / 4


def test_just_over_the_print_resolution_is_left_alone(tmp_path: Path) -> None:
    """Twice the print resolution or less is a sharp picture, not a heavy one."""
    pdf = _pillow_pdf(tmp_path / "sharp.pdf", _noise((1200, 800)), REDUCE_ABOVE_PPI)
    before = pdf.read_bytes()
    assert reduce_oversized_images(pdf).reduced == ()
    assert pdf.read_bytes() == before


def test_a_picture_that_cannot_be_rewritten_exactly_is_named(tmp_path: Path) -> None:
    """Absence must not read as success: a heavy picture left as it was says so."""
    pdf = _pillow_pdf(
        tmp_path / "bilevel.pdf", _noise((3000, 2000), "L").convert("1"), 1500
    )
    before = pdf.read_bytes()
    result = reduce_oversized_images(pdf)
    assert result.reduced == ()
    [kept] = result.kept
    assert kept.startswith("3000x2000 pixels at 1,500 ppi: ")
    assert pdf.read_bytes() == before
    [warning] = image_reduction_warnings(result)
    assert "was left at full size" in warning and kept in warning
