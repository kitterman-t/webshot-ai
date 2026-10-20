"""Print a table too long for Chromium to paginate as consecutive pieces.

Chromium keeps a break token for every page a table breaks across, and each
token holds its own copy of the table's per-row and per-cell layout data
(`TableBreakTokenData`: about 32 bytes a row and 32 a cell). Printing one
table therefore costs memory in proportion to its rows and cells times its
pages, which is the square of its length. A 54,831-row, 3-column CSV took
the renderer past 12 GiB before Chromium gave up ("Printing failed", or the
renderer crashed), and 40,000 rows peaked at 14 GiB. The same 40,000 rows laid
out as blocks printed in 1.8 GiB (docs/09 P94).

A table whose estimated cost is past `BUDGET_BYTES` is printed as a run of
tables instead: the original keeps its first rows, its caption and its id,
and each piece after it holds the next rows, a copy of its `<thead>` if it
has one, its column groups, and the original's attributes but for `id`. A
header row written into the body is a body row like the rest and is not
copied. The footer moves to the last piece. A piece boundary never cuts a
cell that spans rows. The cost is the header repeated wherever a piece starts
mid-page, and a tagged PDF that holds several tables where the page had one;
the manifest's warning says so, and whether there was a header to repeat. The
bundle is built before this runs, so it has every table whole.

Each piece lays its columns out for itself, as Chromium would print the table.
Fixing them to the widths the table had on screen was tried and changed the
print: the screen is wider than the paper, so the text wrapped differently,
and a 2,260-row sheet of long text printed in 3,287 pages instead of 3,576.
Pieces of the same kind of rows get the same widths, near enough; where the
rows differ, the columns can shift at a piece's first row, under its header.

The pieces meet with no margin between them, by a stylesheet keyed on
`data-webshot-*` attributes, never by a `style` attribute, which a page's own
rules can read (docs/09 P83-4).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from playwright.async_api import Page

from ..config import CaptureOptions

#: The estimated cost past which a table is split. Chromium failed at 11 to 14 GiB;
#: half a gibibyte keeps a split table's pages within what a small laptop can
#: spare, and leaves a 3-column table of one-line rows whole up to about 8,000
#: rows.
BUDGET_BYTES = 512 * 2**20

#: Bytes per row or cell, per page. Each break token copies about 32 bytes for
#: each; a table's peak exceeded the same rows laid out as blocks by 1.5 and
#: 1.85 times that at 10,000 and 40,000 rows (docs/09 P94).
BYTES_PER_ENTRY_PAGE = 64

#: Each piece's own estimated cost is held near this, so a split table costs in
#: proportion to its length rather than its square. Rows of long text that wrap
#: over many lines make pages fast, so they get smaller pieces: a sheet of
#: 2,260 such rows in 1,000-row pieces still took the renderer to 7.1 GiB.
PIECE_BUDGET_BYTES = 32 * 2**20

#: The most rows one piece holds however short they are. 1,000 one-line rows
#: of 3 columns cost about 8 MiB.
PIECE_ROWS = 1_000

#: Paper sizes in inches, width by height, for estimating a table's pages.
_PAPER_INCHES = {
    "Letter": (8.5, 11.0),
    "Legal": (8.5, 14.0),
    "Tabloid": (11.0, 17.0),
    "A3": (297 / 25.4, 420 / 25.4),
    "A4": (210 / 25.4, 297 / 25.4),
    "A5": (148 / 25.4, 210 / 25.4),
}
_UNIT_INCHES = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "px": 1 / 96}

_SPLIT_SCRIPT = r"""
({pagePx, budget, bytesPerEntryPage, pieceBudget, pieceRows}) => {
  const tables = [];
  const visit = (root) => {
    for (const table of root.querySelectorAll('table')) tables.push(table);
    for (const element of root.querySelectorAll('*')) {
      if (element.shadowRoot) visit(element.shadowRoot);
    }
  };
  visit(document);

  const stripIds = (node) => {
    if (node.nodeType !== 1) return node;
    node.removeAttribute('id');
    for (const element of node.querySelectorAll('[id]')) element.removeAttribute('id');
    return node;
  };

  // Where a piece may start: at a section's first row, or at a row that no
  // cell above it in the same section spans into.
  const allowedStarts = (body) => {
    const allowed = new Array(body.length).fill(false);
    let section = null, reach = -1, index = 0;
    for (let j = 0; j < body.length; j++) {
      const row = body[j];
      if (row.parentNode !== section) {
        section = row.parentNode;
        reach = -1;
        index = 0;
        allowed[j] = true;
      } else {
        allowed[j] = reach < index;
      }
      for (const cell of row.cells) {
        const span = cell.rowSpan === 0 ? section.rows.length - index : Math.max(1, cell.rowSpan);
        reach = Math.max(reach, index + span - 1);
      }
      index += 1;
    }
    return allowed;
  };

  const roots = new Set();
  const results = [];
  let index = 0;
  for (const table of tables) {
    if (table.hasAttribute('data-webshot-piece')) continue;
    const body = [];
    for (const section of table.tBodies) for (const row of section.rows) body.push(row);
    if (body.length === 0) continue;
    let cells = 0;
    for (const row of table.rows) cells += row.cells.length;
    const rect = table.getBoundingClientRect();
    const pages = Math.max(1, Math.ceil(rect.height / pagePx));
    const estimate = (table.rows.length + cells) * pages * bytesPerEntryPage;
    if (estimate <= budget) continue;
    // A piece of r rows costs r rows' entries times its r rows' pages, so
    // the rows that keep it within its budget are a square root.
    const entriesPerRow = 1 + cells / table.rows.length;
    const rowPx = Math.max(1, rect.height / table.rows.length);
    const fits = Math.sqrt((pieceBudget * pagePx) / (entriesPerRow * rowPx * bytesPerEntryPage));
    const size = Math.max(1, Math.min(pieceRows, Math.floor(fits)));
    if (body.length <= size) continue;

    const allowed = allowedStarts(body);
    const starts = [0];
    for (let next = size; next < body.length; ) {
      let j = next;
      while (j < body.length && !allowed[j]) j += 1;
      if (j >= body.length) break;
      starts.push(j);
      next = j + size;
    }
    if (starts.length === 1) continue;

    index += 1;
    const mark = String(index);
    const sectionOf = body.map((row) => row.parentNode);
    const groups = Array.from(table.querySelectorAll(':scope > colgroup'));
    const pieces = [];
    for (let p = 1; p < starts.length; p++) {
      const piece = stripIds(table.cloneNode(false));
      piece.setAttribute('data-webshot-split', mark);
      piece.setAttribute('data-webshot-piece', p === starts.length - 1 ? 'last' : 'middle');
      for (const group of groups) piece.appendChild(stripIds(group.cloneNode(true)));
      if (table.tHead) piece.appendChild(stripIds(table.tHead.cloneNode(true)));
      const end = p + 1 < starts.length ? starts[p + 1] : body.length;
      let source = null, copy = null;
      for (let j = starts[p]; j < end; j++) {
        if (sectionOf[j] !== source) {
          source = sectionOf[j];
          copy = stripIds(source.cloneNode(false));
          piece.appendChild(copy);
        }
        copy.appendChild(body[j]);
      }
      pieces.push(piece);
    }
    for (const section of new Set(sectionOf)) {
      if (section.rows.length === 0 && section.parentNode === table) section.remove();
    }
    if (table.tFoot) pieces[pieces.length - 1].appendChild(table.tFoot);
    table.setAttribute('data-webshot-split', mark);
    table.setAttribute('data-webshot-piece', 'first');
    let anchor = table;
    for (const piece of pieces) {
      anchor.after(piece);
      anchor = piece;
    }
    roots.add(table.getRootNode());
    results.push({rows: body.length, pieces: starts.length, estimate, header: !!table.tHead});
  }

  const css =
    'table[data-webshot-piece="middle"],table[data-webshot-piece="last"]{margin-top:0!important}' +
    'table[data-webshot-piece="first"],table[data-webshot-piece="middle"]{margin-bottom:0!important}';
  for (const root of roots) {
    const style = document.createElement('style');
    style.setAttribute('data-webshot-long-tables', '');
    style.textContent = css;
    (root === document ? document.head || document.documentElement : root).appendChild(style);
  }
  return results;
}
"""


@dataclass(frozen=True)
class SplitTable:
    """One table printed as pieces: its body rows, its pieces, its estimated cost,
    and whether it had a `<thead>` for each piece to repeat."""

    rows: int
    pieces: int
    estimate: int
    header: bool


def printable_height_px(options: CaptureOptions) -> float:
    """The CSS pixels of a table that fit one printed page, for the estimate.

    The paper's height less both margins, at 96 pixels an inch, divided by the
    print scale. A page's own `@page` size can change it; the estimate only
    decides whether a table is long enough to split, so it does not need to.
    """
    width, height = _PAPER_INCHES.get(options.paper_format, _PAPER_INCHES["Letter"])
    if options.landscape:
        height = width
    margin = 0.0
    found = re.fullmatch(r"(\d+(?:\.\d+)?)(px|in|cm|mm)?", options.margin)
    if found and found.group(2):
        margin = float(found.group(1)) * _UNIT_INCHES[found.group(2)]
    return max(1.0, (height - 2 * margin) * 96 / (options.scale or 1.0))


async def split_long_tables(page: Page, options: CaptureOptions) -> list[SplitTable]:
    """Split every table too costly to print whole in the page's own document.

    Runs on the page as it will print, after the bundle has been built from it
    and before the render, so the render and the checks that print the page
    again all see the pieces. Its open shadow roots are searched; its frames
    are not. Each frame was fitted to its document's height before the bundle
    (`frames.py`), and a split inside one would lengthen that document past
    the box the PDF prints, cutting its last rows with no warning. Splitting
    there would not save the print either: fitting a frame prints a probe of
    the page first, and Chromium paginates the whole page for it, frames and
    their tables included (docs/09 P94-5).
    """
    arguments = {
        "pagePx": printable_height_px(options),
        "budget": BUDGET_BYTES,
        "bytesPerEntryPage": BYTES_PER_ENTRY_PAGE,
        "pieceBudget": PIECE_BUDGET_BYTES,
        "pieceRows": PIECE_ROWS,
    }
    found = await page.main_frame.evaluate(_SPLIT_SCRIPT, arguments)
    return [SplitTable(**entry) for entry in found]


def split_tables_warning(split: Sequence[SplitTable], *, tagged: bool) -> str | None:
    """The manifest's account of a split, or None when no table was split.

    `tagged` is whether the PDF has a structure tree: only then is each piece
    a table of its own in it, and saying so of an untagged PDF would
    contradict `manifest.pdf.tagged` (as `embed.py` words its text too).
    """
    if not split:
        return None
    longest = max(split, key=lambda table: table.rows)
    estimate = math.ceil(longest.estimate / 2**30 * 10) / 10
    which = (
        f"A table of {longest.rows:,} rows was printed as {longest.pieces} "
        "consecutive tables"
        if len(split) == 1
        else f"{len(split)} long tables were printed as consecutive tables, the "
        f"longest, of {longest.rows:,} rows, as {longest.pieces}"
    )
    headers = sum(table.header for table in split)
    if headers == len(split):
        repeated = "Each piece repeats its table's header"
    elif headers == 0:
        repeated = (
            "The table has no header (`<thead>`) for its pieces to repeat"
            if len(split) == 1
            else "The tables have no header (`<thead>`) for their pieces to repeat"
        )
    else:
        repeated = "A piece repeats its table's header where it has a `<thead>`"
    structure = (
        ", and the PDF's structure tags each piece as a table of its own"
        if tagged
        else ""
    )
    return (
        f"{which}. {repeated}{structure}. Chromium's memory for printing one "
        "table grows with its rows times its pages: printing the longest whole was "
        f"estimated at {estimate} GiB, and Chromium has failed at 11 to 14 GiB (docs/09 "
        "P94). The bundle has every table whole."
    )
