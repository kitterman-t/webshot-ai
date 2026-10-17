"""Bridge to pypdfium2: what a PDF page paints, in the order it paints it.

The only module that imports pypdfium2 (docs/02-architecture.md). What crosses
out is `PageInk`, `Ink` and `Glyph`, WebShot's own records, never a pdfium
handle.

pypdf reads a PDF's structure, and its text, but not where each glyph lands or
what is drawn over it. A covered-text check needs both, from the PDF Chromium
printed rather than from the screen: Chromium lays a page out again to print
it, and prints a box fixed to the window once per page (docs/09 P83). PDFium
reads the page as a viewer draws it: every object in paint order, each with its
bounds and colour, and every character with its box and the object that drew
it. It is in the dependency tree already, through OCRmyPDF.

Three things are corrected here, at the seam. An object inside a form XObject
(Chromium draws a group with `opacity` as one) reports its bounds and its clip
in the form's space, so both are carried to the page through every enclosing
form's matrix. An object's bounds ignore its clip, so a box clipped by an
`overflow: hidden` parent reported the whole of what it would have drawn; the
bounds are cut to the clip. And the alpha an object is drawn with is its own
times every enclosing group's, which is the `opacity` a reader sees.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

#: A rectangle in PDF page space: left, bottom, right, top, in points.
Box = tuple[float, float, float, float]

_KINDS = {
    raw.FPDF_PAGEOBJ_TEXT: "text",
    raw.FPDF_PAGEOBJ_PATH: "path",
    raw.FPDF_PAGEOBJ_IMAGE: "image",
    raw.FPDF_PAGEOBJ_SHADING: "shading",
    raw.FPDF_PAGEOBJ_FORM: "form",
}

#: Form XObjects nest no deeper than this in anything Chromium prints; a form
#: deeper still is read as one object, its contents uncounted.
_MAX_DEPTH = 32


class PdfInkError(RuntimeError):
    """The PDF could not be read as painted objects."""


@dataclass(frozen=True, slots=True)
class Ink:
    """One object a page paints."""

    #: Its place in the page's paint order: a later object is drawn over an
    #: earlier one wherever the two overlap.
    order: int
    #: `text`, `path`, `image`, `shading` or `form`.
    kind: str
    #: Where it paints, cut to its clip.
    box: Box
    #: The colour it paints with, for text and paths; `None` for an image, a
    #: shading, a form, or a path that is neither filled nor stroked.
    color: tuple[int, int, int] | None
    #: How opaque it is drawn, 0 to 1: its own alpha times its groups'. Text
    #: drawn in an invisible render mode is 0.
    alpha: float


@dataclass(frozen=True, slots=True)
class Glyph:
    """One character of a page's text layer, where it was drawn."""

    #: Its index in the page's text.
    index: int
    #: The paint order of the text object that drew it, or -1 for a character
    #: PDFium generated between objects (a space, a line break).
    order: int
    char: str
    #: The centre of its box.
    x: float
    y: float
    #: Its baseline.
    baseline: float


@dataclass(frozen=True, slots=True)
class PageInk:
    """Everything one page paints."""

    number: int
    inks: tuple[Ink, ...]
    glyphs: tuple[Glyph, ...]


def page_count(path: Path) -> int:
    """How many pages the PDF at `path` has."""
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise PdfInkError(f"PDFium could not open {path.name}: {exc}") from exc
    try:
        return len(document)
    finally:
        document.close()


def read_ink(pdf: bytes) -> tuple[PageInk, ...]:
    """Every page's objects in paint order, and its characters."""
    try:
        document = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError as exc:
        raise PdfInkError(f"PDFium could not open the PDF: {exc}") from exc
    try:
        return tuple(_read_page(document, index) for index in range(len(document)))
    except pdfium.PdfiumError as exc:
        raise PdfInkError(f"PDFium could not read the PDF: {exc}") from exc
    finally:
        document.close()


def _read_page(document: pdfium.PdfDocument, index: int) -> PageInk:
    page = document[index]
    textpage = page.get_textpage()
    try:
        inks: list[Ink] = []
        orders: dict[int | None, int] = {}
        _walk(
            raw.FPDFPage_CountObjects,
            raw.FPDFPage_GetObject,
            page.raw,
            0,
            None,
            1.0,
            None,
            inks,
            orders,
        )
        glyphs = []
        for char in range(textpage.count_chars()):
            code = raw.FPDFText_GetUnicode(textpage.raw, char)
            if code in (0x0D, 0x0A, 0xFFFE, 0xFFFF):
                continue
            left, bottom, right, top = (ctypes.c_double() for _ in range(4))
            raw.FPDFText_GetCharBox(textpage.raw, char, left, right, bottom, top)
            x, y = ctypes.c_double(), ctypes.c_double()
            raw.FPDFText_GetCharOrigin(textpage.raw, char, x, y)
            generated = raw.FPDFText_IsGenerated(textpage.raw, char) == 1
            owner = (
                None
                if generated
                else _address(raw.FPDFText_GetTextObject(textpage.raw, char))
            )
            glyphs.append(
                Glyph(
                    index=char,
                    order=orders.get(owner, -1) if owner is not None else -1,
                    char=chr(code),
                    x=(left.value + right.value) / 2,
                    y=(bottom.value + top.value) / 2,
                    baseline=y.value,
                )
            )
        return PageInk(number=index + 1, inks=tuple(inks), glyphs=tuple(glyphs))
    finally:
        textpage.close()
        page.close()


def _walk(
    count: object,
    get: object,
    parent: object,
    depth: int,
    matrix: tuple[float, ...] | None,
    alpha: float,
    clip: Box | None,
    inks: list[Ink],
    orders: dict[int | None, int],
) -> None:
    """Append the objects in `parent`, and in each form inside it, in paint order.

    `matrix` carries the parent's space to the page's (`None` on the page
    itself), `alpha` is the parent's groups' alpha, and `clip` is the parent's
    clip on the page.
    """
    for position in range(count(parent)):  # type: ignore[operator]
        handle = get(parent, position)  # type: ignore[operator]
        if not handle:
            continue
        kind = _KINDS.get(raw.FPDFPageObj_GetType(handle), "other")
        own_alpha = alpha * _fill_alpha(handle)
        box = _bounds(handle) if matrix is None else _on_page(_bounds(handle), matrix)
        local_clip = _clip_box(handle)
        own_clip = _intersect(
            clip,
            local_clip
            if local_clip is None or matrix is None
            else _on_page(local_clip, matrix),
        )
        color: tuple[int, int, int] | None = None
        drawn_alpha = own_alpha
        if kind == "text":
            color = _fill_color(handle)
            if raw.FPDFTextObj_GetTextRenderMode(handle) in (
                raw.FPDF_TEXTRENDERMODE_INVISIBLE,
                raw.FPDF_TEXTRENDERMODE_CLIP,
            ):
                drawn_alpha = 0.0
        elif kind == "path":
            fill_mode, stroked = ctypes.c_int(), ctypes.c_int()
            raw.FPDFPath_GetDrawMode(handle, fill_mode, stroked)
            if fill_mode.value:
                color = _fill_color(handle)
            elif stroked.value:
                color, drawn_alpha = _stroke_color(handle, alpha)
            else:
                drawn_alpha = 0.0
        order = len(inks)
        orders[_address(handle)] = order
        inks.append(
            Ink(
                order=order,
                kind=kind,
                box=box if own_clip is None else _clip(box, own_clip),
                color=color,
                alpha=drawn_alpha,
            )
        )
        if kind == "form" and depth < _MAX_DEPTH:
            local = _matrix(handle)
            _walk(
                raw.FPDFFormObj_CountObjects,
                raw.FPDFFormObj_GetObject,
                handle,
                depth + 1,
                local if matrix is None else _multiply(local, matrix),
                own_alpha,
                own_clip,
                inks,
                orders,
            )


def _address(handle: Any) -> int | None:
    return ctypes.cast(handle, ctypes.c_void_p).value


def _bounds(handle: object) -> Box:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    if not raw.FPDFPageObj_GetBounds(handle, left, bottom, right, top):
        return (0.0, 0.0, 0.0, 0.0)
    return (left.value, bottom.value, right.value, top.value)


def _fill_alpha(handle: object) -> float:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not raw.FPDFPageObj_GetFillColor(handle, r, g, b, a):
        return 1.0
    return a.value / 255


def _fill_color(handle: object) -> tuple[int, int, int] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not raw.FPDFPageObj_GetFillColor(handle, r, g, b, a):
        return None
    return (r.value, g.value, b.value)


def _stroke_color(
    handle: object, alpha: float
) -> tuple[tuple[int, int, int] | None, float]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not raw.FPDFPageObj_GetStrokeColor(handle, r, g, b, a):
        return None, 0.0
    return (r.value, g.value, b.value), alpha * a.value / 255


def _clip_box(handle: object) -> Box | None:
    """The bounding box of the object's clip, in the object's own space."""
    clip = raw.FPDFPageObj_GetClipPath(handle)
    if not clip:
        return None
    found: Box | None = None
    for path in range(raw.FPDFClipPath_CountPaths(clip)):
        xs: list[float] = []
        ys: list[float] = []
        for segment in range(raw.FPDFClipPath_CountPathSegments(clip, path)):
            point = raw.FPDFClipPath_GetPathSegment(clip, path, segment)
            x, y = ctypes.c_float(), ctypes.c_float()
            if point and raw.FPDFPathSegment_GetPoint(point, x, y):
                xs.append(x.value)
                ys.append(y.value)
        if xs:
            # The clip is the intersection of its paths.
            path_box = (min(xs), min(ys), max(xs), max(ys))
            found = path_box if found is None else _clip(found, path_box)
    return found


def _matrix(handle: object) -> tuple[float, ...]:
    matrix = raw.FS_MATRIX()
    if not raw.FPDFPageObj_GetMatrix(handle, matrix):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)


def _multiply(inner: tuple[float, ...], outer: tuple[float, ...]) -> tuple[float, ...]:
    """`inner` then `outer`, as PDF composes them (row vectors)."""
    a1, b1, c1, d1, e1, f1 = inner
    a2, b2, c2, d2, e2, f2 = outer
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _on_page(box: Box, matrix: tuple[float, ...]) -> Box:
    """`box`, in a form's space, carried to the page by `matrix`."""
    a, b, c, d, e, f = matrix
    left, bottom, right, top = box
    points = [
        (x * a + y * c + e, x * b + y * d + f)
        for x in (left, right)
        for y in (bottom, top)
    ]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def _clip(box: Box, clip: Box) -> Box:
    """`box` cut to `clip`: empty, at a corner, where they do not overlap."""
    left, bottom = max(box[0], clip[0]), max(box[1], clip[1])
    right, top = min(box[2], clip[2]), min(box[3], clip[3])
    if right <= left or top <= bottom:
        return (left, bottom, left, bottom)
    return (left, bottom, right, top)


def _intersect(first: Box | None, second: Box | None) -> Box | None:
    """The overlap of two boxes, either of which may be absent (no limit).

    Two boxes that do not overlap give an empty box at the first's corner,
    never `None`, which would read as no limit at all.
    """
    if first is None:
        return second
    if second is None:
        return first
    return _clip(first, second)
