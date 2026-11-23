"""Name the text a page prints in columns, and say what that does to its text layer.

A consulting firm's print stylesheet set its article in two CSS columns. The
PDF printed them side by side, as the page meant, and its text layer was
whole, but a reader that takes a PDF's text line by line across the page
joined each line of the first column to the line beside it in the second:
pdftotext's copy of the article scored 0.78 against the bundle's text, where
the same page printed in one column scored 0.975 (docs/09 P135-5). Copying from
the PDF, or searching it for a phrase that runs onto the next line, meets the
same join. Nothing said the layout was in columns.

The layout is the page's, so WebShot does not change it: a multi-column
article is what the page prints, and P16-4 leaves reflowing a layout to a
stylesheet written for the page. The check names each block of text set in
columns at the printed width, with the `--css` rule that prints it in one. The
rule names no media: the check runs under the capture's `--media`, and a rule
wrapped in `@media print` did nothing under `--media screen` to columns the
page sets for every medium (review of P135-5).
It runs inside the clipping check's layout at the printed width
(`render/clipping.find_clipped_text`), so it costs no layout of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: How much text, without spaces, a block set in columns must hold to be
#: named: an article's paragraphs, not a list of links set in a few columns.
COLUMN_TEXT_CHARACTERS = 400

#: How many blocks a warning names.
NAMED = 3

#: Each outermost block of text the page lays out in two columns or more, with
#: how many and how much text it holds, in document order. Run with the window
#: at the printed width and the capture's media, as the clipping check runs.
#:
#: The columns are the used count, as CSS works it out: `column-count`, capped
#: by how many columns of `column-width` (and the gap) the box is wide enough
#: for, or that number alone where `column-count` is `auto`. A block that is
#: hidden, or inside what clean mode or `--exclude` removed, does not print;
#: one inside the page's header, navigation, footer or an aside is page chrome,
#: whose few lines a reader does not read across.
COLUMNS_SCRIPT = r"""(minimum) => {
    const chrome = 'header, nav, footer, aside, [role="banner"], [role="navigation"], [role="contentinfo"], [role="complementary"]';
    const describe = element => {
        const id = element.id && !/^\d/.test(element.id) ? `#${CSS.escape(element.id)}` : '';
        const classes = [...element.classList].slice(0, 2).map(name => `.${CSS.escape(name)}`).join('');
        return element.localName + id + classes;
    };
    const found = [];
    for (const element of document.body ? document.body.querySelectorAll('*') : []) {
        if (found.some(block => block.element.contains(element))) continue;
        const style = getComputedStyle(element);
        const count = style.columnCount === 'auto' ? 0 : parseInt(style.columnCount, 10) || 0;
        const width = style.columnWidth === 'auto' ? 0 : parseFloat(style.columnWidth) || 0;
        if (count < 2 && width <= 0) continue;
        if (element.closest('[data-webshot-hidden="true"]') || element.closest(chrome)) continue;
        const box = element.getBoundingClientRect();
        if (box.width <= 0 || box.height <= 0) continue;
        const gap = style.columnGap === 'normal' ? parseFloat(style.fontSize) || 16 : parseFloat(style.columnGap) || 0;
        const inner = element.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
        const fit = width > 0 ? Math.max(1, Math.floor((inner + gap) / (width + gap))) : Infinity;
        const columns = count >= 2 ? Math.min(count, fit) : fit;
        if (!Number.isFinite(columns) || columns < 2) continue;
        const characters = (element.innerText || '').replace(/\s+/g, '').length;
        if (characters < minimum) continue;
        found.push({element, name: describe(element), columns, characters});
    }
    return found.map(({name, columns, characters}) => ({name, columns, characters}));
}"""


@dataclass(frozen=True, slots=True)
class ColumnBlock:
    """A block of text the page prints in columns."""

    #: A selector naming it: tag, id and up to two classes.
    name: str
    #: How many columns it is laid out in at the printed width.
    columns: int
    #: Characters of text it holds, spaces not counted.
    characters: int


def column_blocks(found: object) -> tuple[ColumnBlock, ...]:
    """`COLUMNS_SCRIPT`'s answer as blocks; an answer of another shape is none."""
    if not isinstance(found, list):
        return ()
    blocks = []
    for item in found:
        if not isinstance(item, dict):
            continue
        try:
            blocks.append(
                ColumnBlock(
                    name=str(item["name"]),
                    columns=int(item["columns"]),
                    characters=int(item["characters"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(blocks)


def columns_warning(
    blocks: Sequence[ColumnBlock], unread: str | None = None
) -> str | None:
    """The manifest warning for the blocks a page prints in columns, or `None`;
    or, where they could not be read (`unread`), the warning that says so."""
    if unread is not None:
        return (
            "WebShot could not check whether the page prints its text in columns "
            f"({unread}), so a PDF whose text a reader taking it line by line "
            "across the page would join from two columns may carry no warning."
        )
    if not blocks:
        return None
    named = "; ".join(
        f"{block.name}, in {block.columns} columns, {block.characters:,} characters"
        for block in blocks[:NAMED]
    )
    rest = len(blocks) - NAMED
    more = f", and {rest} more" if rest > 0 else ""
    one = len(blocks) == 1
    selectors = ", ".join(block.name for block in blocks[:NAMED])
    return (
        f"The page prints {'a block' if one else f'{len(blocks)} blocks'} of its "
        f"text in columns: {named}{more}. The PDF sets {'its' if one else 'their'} "
        "columns side by side, as the page does, but a reader that takes a PDF's "
        "text line by line across the page joins each line of one column to the line beside it in the next, as "
        "pdftotext did on such a page, and so does copying a passage that runs "
        "on to the next line. The bundle holds the text in order. To print "
        f"{'it' if one else 'them'} in one column, pass --css with a file holding "
        f"{selectors} {{ columns: auto !important; }}."
    )
