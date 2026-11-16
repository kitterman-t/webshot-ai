"""Page text printed over other page text, named in a warning (docs/09 P123-4).

A consulting firm's article printed a "Related insights" heading over the last
two lines of a paragraph at a page break, and nothing said so: the covered-text
check (P83) measures fixed and sticky boxes only. This check reads the printed
characters and their bands, from the font's descent to its ascent, and reports
a page where a run of characters is set over another line.

It must fire on a block laid over another by a fraction of a line, and stay
quiet on what only sits close: a line's own characters, a superscript beside
them, two lines of one block set tight, a heading set over a smaller line
with room between, text drawn too faintly to read, text a fixed box draws
(the covered-text check names the box), and text a clip hides. Run over 86
real captures, it found nothing but overlaps a reader sees and fixed boxes,
which the pipeline leaves to the covered-text check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webshot.cli import main
from webshot.render.overprint import (
    MINIMUM_RUN,
    Overprint,
    overprint_warning,
    overprinted_text,
)
from webshot.render.pdfium_bridge import Glyph, Ink, PageInk

FIXTURE = Path(__file__).parent / "fixtures" / "overprinted_blocks_page.html"


def _line(
    order: int,
    x: float,
    baseline: float,
    chars: str,
    *,
    size: float = 16.0,
    em: float = 10.8,
    index: int = 0,
    alpha: float = 1.0,
    color: tuple[int, int, int] | None = (0, 0, 0),
    clip: tuple[float, float, float, float] | None = None,
) -> tuple[Ink, list[Glyph]]:
    """One text object of 6pt-wide characters on `baseline`, its band from a
    quarter of `em` below the baseline to three quarters above."""
    low, high = baseline - em / 4, baseline + em * 3 / 4
    box = clip or (x, low, x + 6 * len(chars), high)
    glyphs = [
        Glyph(
            index=index + offset,
            order=-1 if char == " " else order,
            char=char,
            x=x + 6 * offset + 3,
            y=baseline + em / 4,
            baseline=baseline,
            low=low,
            high=high,
            width=6.0,
            size=size,
        )
        for offset, char in enumerate(chars)
    ]
    return Ink(order, "text", box, color, alpha), glyphs


def _page(*lines: tuple[Ink, list[Glyph]]) -> PageInk:
    return PageInk(
        number=1,
        inks=tuple(ink for ink, _ in lines),
        glyphs=tuple(glyph for _, glyphs in lines for glyph in glyphs),
    )


PARAGRAPH = "harbor office reports"


def test_a_block_laid_a_third_of_a_line_over_another_is_found() -> None:
    page = _page(
        _line(0, 10, 100, PARAGRAPH),
        _line(1, 10, 104, "Related notices", size=18, em=12, index=100),
    )
    [found] = overprinted_text([page])
    assert found.page == 1
    assert found.characters > MINIMUM_RUN
    assert found.sample.startswith("harbor")


def test_two_lines_of_one_block_set_tight_are_not() -> None:
    """Bands that share a third of their height: a heading at line-height 0.9
    in a font whose ascent and descent take 1.3 em."""
    page = _page(
        _line(0, 10, 100, PARAGRAPH), _line(1, 10, 107.5, PARAGRAPH, index=100)
    )
    assert overprinted_text([page]) == ()


def test_a_large_line_over_a_small_one_with_room_between_is_not() -> None:
    page = _page(
        _line(0, 10, 100, PARAGRAPH, size=40, em=36),
        _line(1, 10, 76, PARAGRAPH, size=16, em=14, index=100),
    )
    assert overprinted_text([page]) == ()


def test_a_line_s_own_characters_and_a_superscript_beside_them_are_not() -> None:
    page = _page(
        _line(0, 10, 100, "the outer roads"),
        _line(1, 100, 104, "1", size=12, em=8, index=100),
        _line(2, 106, 100, " are marked", index=200),
    )
    assert overprinted_text([page]) == ()


def test_fewer_than_a_run_of_characters_is_not() -> None:
    """A limit stacked under a summation sign."""
    page = _page(
        _line(0, 10, 100, "abc"),
        _line(1, 10, 104, "xyz", size=12, em=12, index=100),
    )
    assert overprinted_text([page]) == ()


def test_text_too_faint_to_read_is_neither_over_nor_under() -> None:
    page = _page(
        _line(0, 10, 100, PARAGRAPH),
        _line(1, 10, 104, "recognized text", size=1, em=12, index=100, alpha=0.01),
    )
    assert overprinted_text([page]) == ()


def test_text_a_fixed_box_draws_is_the_covered_text_check_s() -> None:
    box = (1, 2, 3)
    page = _page(
        _line(0, 10, 100, PARAGRAPH),
        _line(1, 10, 104, "Tide office", size=18, em=12, index=100, color=box),
    )
    assert overprinted_text([page], boxes={box}, spans=[{}]) == ()
    # Or drawn between the marks that bracket it, in a colour of its own.
    assert overprinted_text([page], spans=[{0: (1, 1)}]) == ()


def test_text_a_clip_hides_is_on_no_one_s_page() -> None:
    page = _page(
        _line(0, 10, 100, PARAGRAPH),
        _line(
            1, 10, 104, "Related notices", size=18, em=12, index=100, clip=(0, 0, 0, 0)
        ),
    )
    assert overprinted_text([page]) == ()


def test_the_warning_names_the_pages_the_count_and_where_it_starts() -> None:
    assert overprint_warning(
        [Overprint(page=3, characters=12, sample="at that"), Overprint(7, 40, "of the")]
    ) == (
        "Text is printed over other text on pages 3 and 7: 52 characters are set "
        'over or under another line, starting "at that" on page 3. The page\'s '
        "layout put two blocks in one place when it was printed, so the PDF shows "
        "neither clearly there, though its text layer holds both. A --css "
        "stylesheet that lays the blocks out one after the other keeps them apart."
    )
    assert overprint_warning([]) is None


@pytest.mark.browser
def test_the_block_pulled_over_a_paragraph_is_named_and_nothing_else(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "capture.pdf"
    assert main([str(FIXTURE), "-o", str(pdf), "--no-ocr"]) == 0
    manifest = json.loads((pdf.with_suffix(".ai") / "manifest.json").read_text("utf-8"))
    [warning] = manifest["warnings"]
    assert warning.startswith("Text is printed over other text on page 1: ")
    assert "A --css stylesheet that lays the blocks out" in warning
