"""Find page text printed over other page text, and say so.

`covering.py` measures text a fixed or sticky box prints over, and nothing
else: two blocks of the page's own flow laid over each other were invisible to
it. A consulting firm's article, laid out in floated full-width grid columns,
printed a "Related insights" heading over the last two lines of a paragraph at
a page break, and the capture said nothing (docs/09 P123-4).

This module reads the printed text and reports characters set over a
character of another line. Two characters are, when the centre of one lies
within the width of the other, their baselines are apart, and the bands
their lines take share a good part of the shorter one: `OVERLAP_SHARE` of it
for two characters of different sizes, `SAME_SIZE_OVERLAP_SHARE` for two of
one size. The band is PDFium's loose box, from the font's descent to its
ascent: the glyphs' own boxes are no guide, since a heading set between two
lines can sit in the gaps between their letters while plainly overlapping
them. The lines of a block share a sliver of their bands at most, even a
heading set tight in a font with a tall ascent, which is why two lines of one
size must share more; a block laid over another by a fraction of a line
shares much of them. A superscript sits off its line's baseline beside the
characters around it, not over them.

A run of `MINIMUM_RUN` such characters, one after another in the text, must be
found on a page before it is reported: a limit under a summation sign is two
or three characters stacked on purpose, and a block laid over a line meets
many at once. Text drawn too faintly to read, such as the recognized-text
layer WebShot adds, is left out, and so is text a fixed or sticky box draws,
which `covering.py` reports by the box's name. Text printed exactly over a
line on the same baseline is not told apart from the line itself, and is not
reported.

It warns and does not correct: which of two overlapping blocks is misplaced,
and how to move it, is the page's layout to answer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .covering import CoveredText, box_spans
from .pdfium_bridge import Glyph, PageInk, PdfInkError, read_ink

LOGGER = logging.getLogger("webshot")

#: How opaque text must be drawn to count, as `covering.COVER_ALPHA`: text a
#: reader cannot see is neither over nor under anything.
VISIBLE_ALPHA = 0.5

#: The shortest run of overprinted characters, one after another on one line
#: of the page's text with only spaces between, that reports a page.
MINIMUM_RUN = 6

#: Two baselines this share of the shorter band apart, or nearer, are one
#: line.
BASELINE_SHARE = 0.25

#: How much of the shorter of two characters' bands the two must share to be
#: set over each other: of different sizes, and of one size, which may be two
#: lines of one block set tight.
OVERLAP_SHARE = 0.3
SAME_SIZE_OVERLAP_SHARE = 0.5

#: The grid cell that characters are filed under, in points, so each is
#: tested against the characters near it alone.
CELL = 24.0

#: How many characters of the overprinted line a warning quotes.
SAMPLE_CHARS = 60


@dataclass(frozen=True, slots=True)
class Overprint:
    """The characters on one printed page that lie over or under another line."""

    page: int
    characters: int
    #: The first stretch of them, as the PDF's text layer has it.
    sample: str


def overprinted_text(
    pages: Sequence[PageInk],
    *,
    boxes: Collection[tuple[int, int, int]] = (),
    spans: Sequence[Mapping[int, tuple[int, int]]] | None = None,
) -> tuple[Overprint, ...]:
    """Each page's characters drawn over or under another line's.

    `boxes` are the colours the covered-text check painted its boxes in, and
    `spans` each page's paint-order span of every box (`covering.box_spans`),
    when `pages` is that check's measuring print. Text in a box is the box's
    to report there, so it is left out here.
    """
    found = []
    for index, page in enumerate(pages):
        span = spans[index] if spans is not None else {}
        hits = _page_overprint(page, boxes, span)
        if hits:
            found.append(
                Overprint(
                    page=page.number, characters=len(hits), sample=_sample(page, hits)
                )
            )
    return tuple(found)


def _page_overprint(
    page: PageInk,
    boxes: Collection[tuple[int, int, int]],
    spans: Mapping[int, tuple[int, int]],
) -> list[Glyph]:
    """The characters of the page in runs of `MINIMUM_RUN` or more set over
    another line, in text order."""
    visible: dict[int, tuple[float, float, float, float]] = {}
    for ink in page.inks:
        if ink.kind != "text" or ink.alpha < VISIBLE_ALPHA:
            continue
        if ink.color is not None and ink.color in boxes:
            continue
        if any(first <= ink.order <= last for first, last in spans.values()):
            continue
        visible[ink.order] = ink.box
    # A character is read where its object is drawn, cut to its clip: one a
    # box clips away is in the text layer and on no one's page.
    glyphs = [
        glyph
        for glyph in page.glyphs
        if glyph.order in visible
        and not glyph.char.isspace()
        and glyph.high > glyph.low
        and _inside(glyph, visible[glyph.order])
    ]
    grid: dict[tuple[int, int], list[Glyph]] = {}
    for glyph in glyphs:
        grid.setdefault((int(glyph.x // CELL), int(glyph.baseline // CELL)), []).append(
            glyph
        )
    over: set[int] = set()
    for glyph in glyphs:
        reach = 2 * (glyph.high - glyph.low)
        column = int(glyph.x // CELL)
        found = False
        for row in range(
            int((glyph.baseline - reach) // CELL),
            int((glyph.baseline + reach) // CELL) + 1,
        ):
            for near in (column - 1, column, column + 1):
                for other in grid.get((near, row), ()):
                    if _over(glyph, other):
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            over.add(glyph.index)
    # A run is one line's: it ends at a character not set over another line,
    # and where the next character is on another baseline.
    hits: list[Glyph] = []
    run: list[Glyph] = []
    for glyph in page.glyphs:
        if glyph.char.isspace():
            continue
        if (
            glyph.index in over
            and run
            and abs(glyph.baseline - run[-1].baseline)
            > BASELINE_SHARE * (glyph.high - glyph.low)
        ):
            if len(run) >= MINIMUM_RUN:
                hits.extend(run)
            run = []
        if glyph.index in over:
            run.append(glyph)
            continue
        if len(run) >= MINIMUM_RUN:
            hits.extend(run)
        run = []
    if len(run) >= MINIMUM_RUN:
        hits.extend(run)
    return hits


def _over(glyph: Glyph, other: Glyph) -> bool:
    """Whether `glyph` is set over or under `other`, a character of another line."""
    if other.index == glyph.index or other.order == glyph.order:
        return False
    if abs(glyph.x - other.x) > other.width / 2:
        return False
    shorter = min(glyph.high - glyph.low, other.high - other.low)
    if abs(glyph.baseline - other.baseline) <= BASELINE_SHARE * shorter:
        return False
    shared = min(glyph.high, other.high) - max(glyph.low, other.low)
    one_size = glyph.size > 0 and abs(glyph.size - other.size) < 0.01
    share = SAME_SIZE_OVERLAP_SHARE if one_size else OVERLAP_SHARE
    return shared >= share * shorter


def _inside(glyph: Glyph, box: tuple[float, float, float, float]) -> bool:
    left, bottom, right, top = box
    return (
        right > left
        and top > bottom
        and left <= glyph.x <= right
        and bottom <= glyph.y <= top
    )


def _sample(page: PageInk, hits: Sequence[Glyph]) -> str:
    """The first run of overprinted characters, with the spaces between them."""
    indices = {glyph.index for glyph in hits}
    first = min(indices)
    text = []
    for glyph in page.glyphs:
        if glyph.index < first:
            continue
        if glyph.char.isspace():
            text.append(" ")
            continue
        if glyph.index not in indices:
            break
        text.append(glyph.char)
    sample = " ".join("".join(text).split())
    return sample if len(sample) <= SAMPLE_CHARS else sample[: SAMPLE_CHARS - 1] + "…"


def _pages(numbers: Sequence[int]) -> str:
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    listed = ", ".join(str(number) for number in numbers[:-1])
    return f"pages {listed} and {numbers[-1]}"


async def find_overprinted_text(covering: CoveredText, rendered: Path) -> str | None:
    """The manifest warning for text printed over text in the PDF at
    `rendered`, or `None` when there is none.

    Read from the covered-text check's measuring print when it made one that
    matched the render (`covering.measured_pages`): the same layout, with each
    fixed or sticky box's paint told apart, so text a box draws is left to
    that check's warning, which names the box. From the render otherwise,
    where no box was measured. A PDF that cannot be read is said to be
    unchecked, never clean.
    """
    if covering.measured_pages is not None:
        pages = covering.measured_pages
        by_code = {code: index for index, code in enumerate(sorted(covering.box_codes))}
        return overprint_warning(
            overprinted_text(
                pages,
                boxes=covering.box_codes,
                spans=[box_spans(page, by_code) for page in pages],
            )
        )
    try:
        pages = await asyncio.to_thread(read_ink, rendered.read_bytes())
    except (OSError, PdfInkError) as exc:
        return (
            "WebShot could not read the PDF to check whether its text is printed "
            f"over other text ({exc}), so the PDF may show two blocks in one place "
            "without a warning."
        )
    return overprint_warning(overprinted_text(pages))


def overprint_warning(found: Sequence[Overprint]) -> str | None:
    """The manifest warning for text printed over text, or `None`."""
    if not found:
        return None
    characters = sum(item.characters for item in found)
    are = "character is" if characters == 1 else "characters are"
    return (
        f"Text is printed over other text on {_pages([item.page for item in found])}: "
        f"{characters:,} {are} set over or under another line, starting "
        f'"{found[0].sample}" on page {found[0].page}. The page\'s layout put two '
        "blocks in one place when it was printed, so the PDF shows neither clearly "
        "there, though its text layer holds both. A --css stylesheet that lays the "
        "blocks out one after the other keeps them apart."
    )
