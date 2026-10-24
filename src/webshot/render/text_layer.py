"""How much of the bundle's text the printed PDF holds, and say so when little.

The bundle is read from the page's document; the PDF is what Chromium printed.
Usually the two hold the same text. When the page draws a wall over its
article and hides the article from print, they do not: a news site's
registration box left one paragraph and the box on each of ten pages, while
`content.md` held the whole 2,227-word article, and nothing said so (docs/09
P97-1). The PDF carries the bundle as its record, so a PDF and an embedded
bundle that disagree that far is a record that contradicts itself.

The measure is the share of the bundle text's runs of letters and digits that
the PDF's text layer also holds. Everything that is not a letter or a digit is
dropped first, on both sides, because the two lay the same text out
differently: line breaks, list markers, table rules, the spaces a PDF reader
infers between runs, and scripts written without spaces. Recognized text
(OCR) is dropped from the bundle's side, because it is the bundle's reading of
a picture, not text the page printed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

#: How many letters and digits one compared run holds: about four words of
#: English, so a run is shared by chance only between the same words.
RUN_CHARACTERS = 20

#: Below this share of the bundle's runs in the PDF, the capture says so.
#: Over 291 earlier captures, 282 measured 0.6 or more, 278 of them 0.9 and
#: up. The walled article measured 0.06, and the eight others under 0.5 with
#: enough text to compare each had a PDF without text its bundle held: titles
#: of related videos printed as blank boxes, a description left collapsed, a
#: carousel's hidden slides, a sidebar's folded filters (docs/09 P97-2).
LOW_SHARE = 0.5

#: A bundle with fewer distinct runs than this is too short for a share to
#: mean anything. A chart's page of axis labels, each shorter than a run, had
#: 60, and lost half of them to three canvases' accessible names.
MIN_RUNS = 200

#: The block `create_ai_bundle` writes for the text recognized in one visual
#: asset, markers included.
_OCR_BLOCK = re.compile(
    r"\[OCR text from [^\]\n]*\][\s\S]*?\[End of OCR text from [^\]\n]*\]"
)


@dataclass(frozen=True, slots=True)
class PrintedShare:
    """How much of the bundle's text the PDF holds."""

    #: Distinct runs in the bundle's text.
    runs: int
    #: How many of them the PDF's text layer holds.
    printed: int

    @property
    def share(self) -> float:
        return self.printed / self.runs if self.runs else 1.0


def _letters(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in folded if character.isalnum())


def _runs(letters: str) -> set[str]:
    return {
        letters[start : start + RUN_CHARACTERS]
        for start in range(len(letters) - RUN_CHARACTERS + 1)
    }


def printed_share(bundle_text: str, pages: Sequence[str]) -> PrintedShare:
    """The share of `bundle_text`'s runs that the PDF's `pages` hold.

    The bundle's runs are taken within each of its lines, never across two:
    a line is a paragraph, a heading or an item, and a page that sets its
    items side by side, as cards or a grid, prints them in another order than
    the bundle lists them. Runs across a break in the bundle measured a film
    site's page that printed every one of its lines at 0.14 (docs/09 P97-2).
    The PDF's pages are joined without a break, because a paragraph it carries
    over a page break is one line in the bundle.
    """
    wanted: set[str] = set()
    for line in _OCR_BLOCK.sub("\n", bundle_text).splitlines():
        wanted |= _runs(_letters(line))
    held = _runs(_letters("".join(pages)))
    return PrintedShare(runs=len(wanted), printed=len(wanted & held))


def printed_text_warning(
    measured: PrintedShare, walls: Sequence[str] = ()
) -> str | None:
    """The manifest warning for a PDF that holds too little of the bundle's
    text, or `None`. `walls` names the access walls the page showed."""
    if measured.runs < MIN_RUNS or measured.share >= LOW_SHARE:
        return None
    held = (
        f"The PDF's text layer holds {measured.printed:,} of the "
        f"{measured.runs:,} distinct {RUN_CHARACTERS}-character runs of the "
        f"bundle's text ({measured.share:.0%}), so the PDF shows much less of "
        "the page than content.md and content.txt, which are embedded in it, "
        "say it has."
    )
    if walls:
        named = " and ".join(walls)
        return (
            f"The page showed {named}'s access wall, which asks the reader to "
            f"sign in, register or subscribe. {held} The site withholds or "
            "hides its article under the wall, so --exclude would not bring "
            "the article back. Open the page in your own browser, signed in if "
            "you have an account, and save it from there."
        )
    return (
        f"{held} Something kept the rest from printing: an overlay over the "
        "text, a wall that hides the article, or content the page keeps "
        "collapsed, scrolled out of its box or hidden from print. Look at the "
        "PDF before relying on it."
    )
