"""RENDER: reduce a picture printed far finer than paper can show (docs/09 P80).

Chromium embeds every picture at its natural size, whatever size the page
prints it at. A think tank's topic page set small display sizes on its
photographers' originals, and a 4-page capture was 86 MB: four thumbnails
were 6720x4480, 5100x3300, 5073x3382 and 4068x2712 pixels, printed at 1,400 to
2,300 pixels per inch.

This pass reads the printed file, finds the size each picture is drawn at on
the page, and gives a picture drawn at more than `REDUCE_ABOVE_PPI` the pixels
`PRINT_PPI` needs at its largest drawn size, in the codec Chromium chose for
it. It runs on the PDF rather than on the page because only the PDF knows the
printed size (print CSS, shrink-to-fit and `object-fit` all move it), and
because the file is all it needs: a picture from another origin, which a
canvas in the page could not read back, is reduced the same way.

Only pictures whose encoding can be rewritten exactly are touched: 8 bits per
sample, gray or RGB (directly or through an ICC profile), Flate or JPEG, with
no decode array or colour-key mask. A soft mask is reduced with its picture.
Any other picture printed above the limit is left as it is and named, so a
large file is never passed off as one that was checked and found small.
"""

from __future__ import annotations

import io
import math
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter, apply_configuration
from pypdf.errors import PyPdfError
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    IndirectObject,
    NameObject,
    NumberObject,
    StreamObject,
)

#: What a picture is given: a print resolution, at its largest drawn size.
PRINT_PPI = 300
#: Reduced only above twice that, so a reduction always drops at least three
#: quarters of the pixels, and a screen's 2x or 3x picture (192 or 288 ppi
#: at its CSS size) is never touched.
REDUCE_ABOVE_PPI = 2 * PRINT_PPI
#: JPEG quality for a reduced JPEG: the original's codec, at a quality that
#: does not show at print resolution.
JPEG_QUALITY = 90
#: Forms inside forms are followed this deep, which no printed page nears and
#: a cycle in a broken file does.
MAX_FORM_DEPTH = 12
#: The most pixels a picture may have and be rewritten: Pillow's own line
#: between a large picture and a decompression bomb. A picture past it is left
#: as it is and named, rather than decoded into gigabytes.
MAX_PICTURE_PIXELS = Image.MAX_IMAGE_PIXELS or 89_478_485

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_Matrix = tuple[float, float, float, float, float, float]


def _times(first: _Matrix, then: _Matrix) -> _Matrix:
    """`first` applied, then `then`: PDF's `cm` is `first x CTM`."""
    a, b, c, d, e, f = first
    p, q, r, s, t, u = then
    return (
        a * p + b * r,
        a * q + b * s,
        c * p + d * r,
        c * q + d * s,
        e * p + f * r + t,
        e * q + f * s + u,
    )


@dataclass(slots=True)
class _Drawn:
    """A picture, and the largest size it is drawn at, in points."""

    reference: IndirectObject
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True, slots=True)
class ReducedImage:
    """One picture the pass reduced."""

    original: tuple[int, int]
    reduced: tuple[int, int]
    #: The largest size it is drawn at, in inches.
    printed: tuple[float, float]
    #: Its resolution at that size before the pass, on its coarser axis.
    ppi: float


@dataclass(frozen=True, slots=True)
class ImageReduction:
    """What the pass did to one printed file."""

    reduced: tuple[ReducedImage, ...] = ()
    #: Pictures printed above the limit and left as they were, each with why.
    kept: tuple[str, ...] = ()
    #: Why the printed file could not be read at all, when it could not.
    unread: str | None = None
    bytes_before: int = 0
    bytes_after: int = 0
    #: For the tests: every picture's drawn size the walk found, by object.
    drawn: dict[int, tuple[float, float]] = field(default_factory=dict)


def _resolve(value: Any) -> Any:
    """The object a reference names, typed for the dictionary reads below."""
    return value.get_object()


def _matrix(operands: Iterable[Any]) -> _Matrix | None:
    values = [float(value) for value in operands]
    if len(values) != 6:
        return None
    return (values[0], values[1], values[2], values[3], values[4], values[5])


def _walk(
    operations: list[tuple[Any, bytes]],
    resources: Any,
    ctm: _Matrix,
    drawn: dict[int, _Drawn],
    forms: tuple[int, ...],
    pdf: Any,
) -> None:
    """Record the size every picture under `operations` is drawn at.

    A picture's unit square is mapped by the CTM at its `Do`, so its drawn
    width and height are the lengths of the CTM's first two rows. A form is
    followed with its own matrix and resources each time it is drawn, since
    the same form drawn twice can put its pictures at two sizes.
    """
    stack: list[_Matrix] = []
    xobjects = resources.get("/XObject") if resources else None
    xobjects = xobjects.get_object() if xobjects is not None else None
    for operands, operator in operations:
        if operator == b"q":
            stack.append(ctm)
        elif operator == b"Q":
            ctm = stack.pop() if stack else ctm
        elif operator == b"cm":
            matrix = _matrix(operands)
            if matrix is not None:
                ctm = _times(matrix, ctm)
        elif operator == b"Do" and xobjects is not None and operands:
            reference = (
                xobjects.raw_get(operands[0]) if operands[0] in xobjects else None
            )
            if not isinstance(reference, IndirectObject):
                continue
            target = _resolve(reference)
            subtype = target.get("/Subtype")
            if subtype == "/Image":
                width = math.hypot(ctm[0], ctm[1])
                height = math.hypot(ctm[2], ctm[3])
                seen = drawn.setdefault(reference.idnum, _Drawn(reference))
                seen.width = max(seen.width, width)
                seen.height = max(seen.height, height)
            elif (
                subtype == "/Form"
                and reference.idnum not in forms
                and len(forms) < MAX_FORM_DEPTH
            ):
                own = _matrix(target.get("/Matrix", _IDENTITY)) or _IDENTITY
                _walk(
                    ContentStream(target, pdf).operations,
                    target.get("/Resources") or resources,
                    _times(own, ctm),
                    drawn,
                    (*forms, reference.idnum),
                    pdf,
                )


def _filter(stream: Any) -> str | None:
    """The stream's one filter, `""` for none, or None for a chain."""
    value = stream.get("/Filter")
    if value is None:
        return ""
    value = value.get_object()
    if isinstance(value, ArrayObject):
        if len(value) != 1:
            return None
        value = value[0].get_object()
    return str(value)


def _components(stream: Any) -> int | None:
    """1 or 3 for the colour spaces whose samples can be rewritten as read."""
    space = stream.get("/ColorSpace")
    space = space.get_object() if space is not None else None
    if space == "/DeviceGray":
        return 1
    if space == "/DeviceRGB":
        return 3
    if isinstance(space, ArrayObject) and len(space) == 2 and space[0] == "/ICCBased":
        components = int(_resolve(space[1]).get("/N", 0))
        return components if components in (1, 3) else None
    return None


def _why_not(stream: Any) -> str | None:
    """Why this picture's encoding cannot be rewritten exactly, or None."""
    if stream.get("/ImageMask"):
        return "it is a stencil mask"
    if int(stream.get("/BitsPerComponent", 0)) != 8:
        return "it is not 8 bits per sample"
    if "/Decode" in stream or "/Mask" in stream:
        return "it has a decode array or a colour-key mask"
    if _filter(stream) not in ("", "/FlateDecode", "/DCTDecode"):
        return f"it is encoded as {stream.get('/Filter')}"
    if _filter(stream) == "/DCTDecode" and "/DecodeParms" in stream:
        return "its JPEG carries decode parameters"
    return None


def _pixels(stream: Any, components: int) -> Image.Image | None:
    """The picture's samples as stored, without any colour conversion.

    pypdf refuses to inflate more than 75 MB, which one 6720x4480 photograph
    is (90 MB); the limit is raised for this read to exactly what the picture
    declares, so a stream that inflates past its own size is still refused.
    """
    mode = "L" if components == 1 else "RGB"
    size = (int(stream["/Width"]), int(stream["/Height"]))
    expected = size[0] * size[1] * components
    with apply_configuration(
        zlib_maximum_output_length=max(expected, 1),
        image_maximum_buffer_size=max(expected, 1),
    ):
        data = stream.get_data()
    if _filter(stream) == "/DCTDecode":
        picture = Image.open(io.BytesIO(data))
        picture.load()
        return picture if picture.mode == mode and picture.size == size else None
    if len(data) < expected:
        return None
    return Image.frombytes(mode, size, data[:expected])


def _encoded(picture: Image.Image, codec: str) -> bytes:
    """`picture` in `codec`: JPEG for a JPEG, Flate for anything else."""
    if codec == "/DCTDecode":
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()
    return zlib.compress(picture.tobytes(), 9)


def _store(stream: Any, size: tuple[int, int], encoded: bytes, codec: str) -> None:
    """Write `encoded` into `stream` in place, as a picture of `size`."""
    if codec != "/DCTDecode":
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        if "/DecodeParms" in stream:
            del stream["/DecodeParms"]
    stream[NameObject("/Width")] = NumberObject(size[0])
    stream[NameObject("/Height")] = NumberObject(size[1])
    # The base class's `set_data` writes the bytes as given; the encoded
    # stream's own refuses any filter but Flate, and would compress again.
    StreamObject.set_data(stream, encoded)
    stream.decoded_self = None


def _target(drawn: _Drawn, size: tuple[int, int]) -> tuple[int, int]:
    """`PRINT_PPI` at the drawn size, never more pixels than there were."""
    return (
        min(size[0], max(1, math.ceil(drawn.width / 72 * PRINT_PPI))),
        min(size[1], max(1, math.ceil(drawn.height / 72 * PRINT_PPI))),
    )


def _soft_masks(drawn: dict[int, _Drawn]) -> dict[int, int]:
    """How many of the drawn pictures share each soft mask."""
    shared: dict[int, int] = {}
    for item in drawn.values():
        stream = _resolve(item.reference)
        mask = stream.raw_get("/SMask") if "/SMask" in stream else None
        if isinstance(mask, IndirectObject):
            shared[mask.idnum] = shared.get(mask.idnum, 0) + 1
    return shared


def _ppi(item: _Drawn) -> float:
    """The picture's resolution at its largest drawn size, on its coarser axis."""
    stream = _resolve(item.reference)
    if item.width <= 0 or item.height <= 0:
        return 0.0
    return min(
        int(stream["/Width"]) / (item.width / 72),
        int(stream["/Height"]) / (item.height / 72),
    )


def _reduce(
    item: _Drawn, shared: dict[int, int]
) -> tuple[ReducedImage | None, str | None]:
    stream = _resolve(item.reference)
    size = (int(stream["/Width"]), int(stream["/Height"]))
    printed = (item.width / 72, item.height / 72)
    ppi = _ppi(item)
    if ppi <= REDUCE_ABOVE_PPI:
        return None, None
    described = f"{size[0]}x{size[1]} pixels at {ppi:,.0f} ppi"
    components = _components(stream)
    reason = _why_not(stream) or (
        None if components else f"its colour space is {stream.get('/ColorSpace')}"
    )
    mask_reference = stream.raw_get("/SMask") if "/SMask" in stream else None
    mask = _resolve(mask_reference) if mask_reference is not None else None
    if reason is None and size[0] * size[1] > MAX_PICTURE_PIXELS:
        reason = f"it has more than {MAX_PICTURE_PIXELS:,} pixels"
    if reason is None and mask is not None:
        if (
            isinstance(mask_reference, IndirectObject)
            and shared.get(mask_reference.idnum, 0) > 1
        ):
            reason = "its soft mask is shared with another picture"
        elif _why_not(mask) or _components(mask) != 1 or "/Matte" in mask:
            reason = "its soft mask cannot be rewritten exactly"
    if reason:
        return None, f"{described}: {reason}"
    assert components is not None, "checked above"
    target = _target(item, size)
    resample = Image.Resampling.LANCZOS
    # Everything is decoded and encoded before anything is written, so a
    # picture that fails part-way keeps its mask and its mask its picture.
    try:
        picture = _pixels(stream, components)
        alpha = _pixels(mask, 1) if mask is not None else None
        if picture is None or (mask is not None and alpha is None):
            return (
                None,
                f"{described}: its samples do not read as its dictionary declares",
            )
        codec = _filter(stream) or ""
        encoded = _encoded(picture.resize(target, resample), codec)
        mask_codec = (_filter(mask) or "") if mask is not None else ""
        encoded_mask = (
            _encoded(alpha.resize(target, resample), mask_codec)
            if alpha is not None
            else None
        )
    except (PyPdfError, OSError, ValueError, UnidentifiedImageError) as exc:
        return None, f"{described}: it could not be decoded ({exc})"
    _store(stream, target, encoded, codec)
    if mask is not None and encoded_mask is not None:
        _store(mask, target, encoded_mask, mask_codec)
    return ReducedImage(size, target, printed, ppi), None


def _drawn_pictures(document: PdfReader | PdfWriter) -> dict[int, _Drawn]:
    drawn: dict[int, _Drawn] = {}
    for page in document.pages:
        contents = page.get_contents()
        if contents is None:
            continue
        _walk(
            contents.operations,
            page.get("/Resources"),
            _IDENTITY,
            drawn,
            (),
            document,
        )
    return drawn


def reduce_oversized_images(pdf: Path) -> ImageReduction:
    """Reduce, in place, each picture `pdf` prints above `REDUCE_ABOVE_PPI`.

    The sizes are read first, from a reader. Only when a picture is over the
    limit is the file cloned into a writer and read again there, and it is
    rewritten only when a picture was reduced, staged beside it and renamed
    over it as `embed_bundle` does, so a file with nothing to reduce keeps its
    bytes.
    """
    before = pdf.stat().st_size
    try:
        reader = PdfReader(str(pdf))
        drawn = _drawn_pictures(reader)
        sizes = {key: (item.width, item.height) for key, item in drawn.items()}
        if not any(_ppi(item) > REDUCE_ABOVE_PPI for item in drawn.values()):
            return ImageReduction(bytes_before=before, bytes_after=before, drawn=sizes)
        writer = PdfWriter(clone_from=reader)
        drawn = _drawn_pictures(writer)
    except PyPdfError as exc:
        # The render is a file Chromium wrote, so this is not expected; when it
        # happens the pictures are as Chromium embedded them, and the manifest
        # says the pass did not run rather than nothing.
        return ImageReduction(bytes_before=before, bytes_after=before, unread=str(exc))
    shared = _soft_masks(drawn)
    reduced: list[ReducedImage] = []
    kept: list[str] = []
    for item in drawn.values():
        done, left = _reduce(item, shared)
        if done:
            reduced.append(done)
        if left:
            kept.append(left)
    if not reduced:
        return ImageReduction(
            kept=tuple(kept), bytes_before=before, bytes_after=before, drawn=sizes
        )
    staged = pdf.with_name(f".{pdf.name}.images")
    try:
        with staged.open("wb") as handle:
            writer.write(handle)
        staged.replace(pdf)
    finally:
        staged.unlink(missing_ok=True)
    return ImageReduction(
        reduced=tuple(reduced),
        kept=tuple(kept),
        bytes_before=before,
        bytes_after=pdf.stat().st_size,
        drawn=sizes,
    )


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:,.1f} MB"


def _each(reduced: Iterable[ReducedImage]) -> Iterator[str]:
    for item in reduced:
        yield (
            f"{item.original[0]}x{item.original[1]} to {item.reduced[0]}x"
            f"{item.reduced[1]} pixels (printed {item.printed[0]:.1f} by "
            f"{item.printed[1]:.1f} in, at {item.ppi:,.0f} ppi)"
        )


def image_reduction_warnings(result: ImageReduction, shown: int = 5) -> list[str]:
    """The manifest's record of what the pass changed, and of what it left."""
    warnings: list[str] = []
    if result.unread:
        warnings.append(
            "The check for pictures printed finer than paper shows could not read "
            f"the printed file ({result.unread}), so its pictures are as Chromium "
            "embedded them, at whatever size they arrived."
        )
    if result.reduced:
        listed = list(_each(result.reduced))
        rest = len(listed) - shown
        named = "; ".join(listed[:shown]) + (f"; and {rest} more" if rest > 0 else "")
        count = len(result.reduced)
        pictures = "picture" if count == 1 else "pictures"
        warnings.append(
            f"Reduced {count} {pictures} the page printed at more than "
            f"{REDUCE_ABOVE_PPI} pixels per inch to {PRINT_PPI} at the largest "
            "size each is printed, which took the printed pages from "
            f"{_megabytes(result.bytes_before)} to "
            f"{_megabytes(result.bytes_after)}: {named}. The PDF holds the "
            "reduced copies, and the originals are not in the capture."
        )
    if result.kept:
        rest = len(result.kept) - shown
        named = "; ".join(result.kept[:shown]) + (
            f"; and {rest} more" if rest > 0 else ""
        )
        count = len(result.kept)
        pictures = "picture" if count == 1 else "pictures"
        warnings.append(
            f"{count} {pictures} printed at more than {REDUCE_ABOVE_PPI} pixels "
            f"per inch {'was' if count == 1 else 'were'} left at full size, "
            f"because {'it' if count == 1 else 'they'} could not be rewritten "
            f"exactly: {named}."
        )
    return warnings
