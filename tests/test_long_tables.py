"""A table too long to print whole is printed as consecutive pieces (docs/09 P94).

Chromium copies a table's per-row and per-cell layout data into every page the
table breaks across, so printing one table costs its rows and cells times its
pages. A 54,831-row CSV failed at 13.8 GiB in the renderer. That size cannot
be a test, so these hold the split's rules on `long_table.html` with the limits
turned down to five-row pieces, and the limits themselves on generated tables:
what a piece carries (its header and its column groups, never a width of its
own), where a piece may not start (inside a cell spanning rows), that the rows
keep their order and nothing else moves, that the pieces meet with no gap,
inside a shadow root too, that the warning reaches every record, and that at
the real limits an ordinary table is left exactly as it was.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.pipeline import renderer_crash_message
from webshot.render import long_tables
from webshot.render.long_tables import (
    SplitTable,
    printable_height_px,
    split_long_tables,
    split_tables_warning,
)

FIXTURE = Path(__file__).parent / "fixtures" / "long_table.html"

#: The fixture's 24 body rows in five-row pieces. A piece would start at R06,
#: inside the East cell spanning R04 to R06, so it starts at R07; R12 ends the
#: first body and R13 starts the second in the same piece.
PIECES = [
    ["R01", "R02", "R03", "R04", "R05", "R06"],
    ["R07", "R08", "R09", "R10", "R11"],
    ["R12", "R13", "R14", "R15", "R16"],
    ["R17", "R18", "R19", "R20", "R21"],
    ["R22", "R23", "R24"],
]

_DESCRIBE = """() => {
  const pieces = [];
  for (const table of document.querySelectorAll('table[data-webshot-split]')) {
    const rows = [];
    for (const body of table.tBodies) {
      for (const row of body.rows) {
        const hour = Array.from(row.cells).map((c) => c.textContent.trim())
          .find((t) => /^R\\d\\d$/.test(t));
        rows.push(hour);
      }
    }
    pieces.push({
      role: table.getAttribute('data-webshot-piece'),
      id: table.id,
      className: table.className,
      caption: table.caption ? table.caption.textContent : null,
      header: table.tHead ? table.tHead.textContent.trim() : null,
      ids: Array.from(table.querySelectorAll('[id]')).map((e) => e.id),
      groups: table.querySelectorAll(':scope > colgroup > col').length,
      bodies: table.tBodies.length,
      rows,
      footer: table.tFoot ? table.tFoot.textContent.trim() : null,
      layout: getComputedStyle(table).tableLayout,
      marginTop: getComputedStyle(table).marginTop,
      marginBottom: getComputedStyle(table).marginBottom,
      widths: Array.from(table.querySelectorAll(':scope > colgroup > col[width]')).length,
    });
  }
  return {
    pieces,
    short: document.querySelector('#short')?.outerHTML ?? null,
    inner: document.querySelector('table.inner')?.outerHTML ?? null,
    styleAttributes: document.querySelectorAll('table[style], col[style]').length,
  };
}"""


async def _split(source: str) -> tuple[list[SplitTable], dict[str, Any], str]:
    """Split the page as the capture does, in print media; describe the result."""
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            if source.startswith("file:"):
                await page.goto(source)
            else:
                await page.set_content(source)
            await page.emulate_media(media="print")
            before = await page.evaluate("document.body.outerHTML")
            split = await split_long_tables(page, options)
            described = await page.evaluate(_DESCRIBE)
            described["unchanged"] = before == await page.evaluate(
                "document.body.outerHTML"
            )
            return split, described, before
        finally:
            await browser.close()


@pytest.fixture
def five_row_pieces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any table is too costly, and a piece holds five rows."""
    monkeypatch.setattr(long_tables, "BUDGET_BYTES", -1)
    monkeypatch.setattr(long_tables, "PIECE_ROWS", 5)


def _table(rows: int, columns: int = 3) -> str:
    head = "".join(f"<th>Column {c}</th>" for c in range(columns))
    body = "".join(
        "<tr>" + "".join(f"<td>{r}.{c}</td>" for c in range(columns)) + "</tr>"
        for r in range(rows)
    )
    return (
        "<!doctype html><html><body><table><thead><tr>"
        f"{head}</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


# --------------------------------------------------------------------------- #
# The words and the numbers, without a browser
# --------------------------------------------------------------------------- #


def test_the_page_height_comes_from_the_paper_margins_and_scale() -> None:
    options = CaptureOptions(source="x", output=Path("x.pdf"))
    # Letter, 0.6in margins, scale 0.9: 9.8in of content at 96 px an inch.
    assert printable_height_px(options) == pytest.approx(9.8 * 96 / 0.9)
    landscape = CaptureOptions(
        source="x", output=Path("x.pdf"), landscape=True, margin="0", scale=1.0
    )
    assert printable_height_px(landscape) == pytest.approx(8.5 * 96)
    a4 = CaptureOptions(
        source="x", output=Path("x.pdf"), paper_format="A4", margin="10mm"
    )
    assert printable_height_px(a4) == pytest.approx((297 - 20) / 25.4 * 96 / 0.9)


def test_no_split_is_no_warning() -> None:
    assert split_tables_warning([], tagged=True) is None


def test_the_warning_names_the_table_its_pieces_and_what_changed() -> None:
    message = split_tables_warning(
        [SplitTable(rows=54_831, pieces=55, estimate=25_041_993_728, header=True)],
        tagged=True,
    )
    assert message is not None
    assert message.startswith("A table of 54,831 rows was printed as 55 ")
    assert "repeats its table's header" in message
    assert "the PDF's structure tags each piece as a table of its own" in message
    assert "23.4 GiB" in message
    assert "bundle has every table whole" in message
    assert "P94" in message


def test_several_tables_are_counted_and_the_longest_named() -> None:
    message = split_tables_warning(
        [
            SplitTable(rows=12_000, pieces=12, estimate=2**30, header=True),
            SplitTable(rows=40_000, pieces=40, estimate=10 * 2**30, header=True),
        ],
        tagged=True,
    )
    assert message is not None
    assert message.startswith("2 long tables were printed as consecutive tables")
    assert "the longest, of 40,000 rows, as 40" in message


def test_an_untagged_pdf_is_not_said_to_have_a_structure() -> None:
    """`--no-tagged-pdf` prints no structure tree; the warning must not
    contradict `manifest.pdf.tagged: false`."""
    table = SplitTable(rows=54_831, pieces=55, estimate=25_041_993_728, header=True)
    message = split_tables_warning([table], tagged=False)
    assert message is not None
    assert "structure" not in message
    assert "Each piece repeats its table's header. Chromium's memory" in message


def test_a_table_without_a_thead_is_not_said_to_repeat_one() -> None:
    """Only a `<thead>` is copied; a header row in the body stays a body row."""
    [alone] = [SplitTable(rows=9_000, pieces=9, estimate=2**30, header=False)]
    message = split_tables_warning([alone], tagged=True)
    assert message is not None
    assert "repeats" not in message
    assert "has no header (`<thead>`) for its pieces to repeat" in message
    mixed = split_tables_warning(
        [alone, SplitTable(rows=12_000, pieces=12, estimate=2**30, header=True)],
        tagged=True,
    )
    assert mixed is not None
    assert "repeats its table's header where it has a `<thead>`" in mixed
    neither = split_tables_warning([alone, alone], tagged=True)
    assert neither is not None
    assert "The tables have no header (`<thead>`)" in neither


def test_a_crash_after_a_split_names_the_table() -> None:
    message = renderer_crash_message(
        "rendering the PDF",
        None,
        None,
        [SplitTable(rows=54_831, pieces=55, estimate=0, header=True)],
    )
    assert "a table of 54,831 rows" in message
    assert "into 55 tables" in message


def test_a_crash_without_a_split_says_nothing_of_tables() -> None:
    assert "table" not in renderer_crash_message("rendering the PDF", None, None)


# --------------------------------------------------------------------------- #
# The split, in a real print layout
# --------------------------------------------------------------------------- #


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_the_rows_keep_their_order_and_never_split_a_spanning_cell() -> None:
    split, described, _ = asyncio.run(_split(FIXTURE.as_uri()))
    assert split == [
        SplitTable(rows=24, pieces=5, estimate=split[0].estimate, header=True)
    ]
    assert [piece["rows"] for piece in described["pieces"]] == PIECES
    assert [piece["role"] for piece in described["pieces"]] == [
        "first",
        "middle",
        "middle",
        "middle",
        "last",
    ]
    # R12 closes the first body and R13 opens the second: two bodies, still
    # two bodies, so a style set on the second still reaches its rows.
    assert described["pieces"][2]["bodies"] == 2


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_each_piece_carries_the_header_and_columns_but_not_the_ids() -> None:
    _, described, _ = asyncio.run(_split(FIXTURE.as_uri()))
    first, *rest = described["pieces"]
    assert first["id"] == "readings"
    assert first["caption"] == "Hourly readings"
    assert "h-station" in first["ids"]
    assert first["footer"] is None
    for piece in rest:
        assert piece["id"] == ""
        assert piece["ids"] == []
        assert piece["className"] == "data"
        assert piece["caption"] is None
        assert piece["header"] == first["header"]
        assert piece["groups"] == first["groups"] == 3
    # The footer moves to the last piece, where the table ends.
    footers = [piece["footer"] for piece in rest]
    assert footers[:-1] == [None, None, None]
    assert "FOOTERSENTINEL" in footers[-1]


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_each_piece_lays_its_columns_out_and_the_pieces_meet() -> None:
    """Widths fixed from the screen changed the print: the screen is wider
    than the paper, so text wrapped differently (P94-3). Each piece keeps the
    page's own table layout, and only the margins between pieces go."""
    _, described, _ = asyncio.run(_split(FIXTURE.as_uri()))
    pieces = described["pieces"]
    assert {piece["layout"] for piece in pieces} == {"auto"}
    assert {piece["widths"] for piece in pieces} == {0}
    assert pieces[0]["marginTop"] != "0px"
    assert all(piece["marginTop"] == "0px" for piece in pieces[1:])
    assert all(piece["marginBottom"] == "0px" for piece in pieces[:-1])
    assert pieces[-1]["marginBottom"] != "0px"
    # Set by a stylesheet and attributes; a page's own rules can read a
    # `style` attribute (docs/09 P83-4).
    assert described["styleAttributes"] == 0


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_a_short_table_and_a_nested_one_are_left_alone() -> None:
    _, described, before = asyncio.run(_split(FIXTURE.as_uri()))
    assert described["short"] in before
    assert described["inner"] in before


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_a_table_in_a_shadow_root_is_split_and_styled_there() -> None:
    rows = "".join(f"<tr><td>S{n:02d}</td></tr>" for n in range(1, 13))
    page = (
        "<!doctype html><html><body><div id='host'></div><script>"
        "const root = document.getElementById('host').attachShadow({mode: 'open'});"
        "root.innerHTML = '<style>table { margin: 20px 0; }</style>"
        f"<table><thead><tr><th>Shadow</th></tr></thead><tbody>{rows}</tbody></table>';"
        "</script></body></html>"
    )
    split, _, _ = asyncio.run(_split_shadow(page))
    assert split == [
        SplitTable(rows=12, pieces=3, estimate=split[0].estimate, header=True)
    ]


async def _split_shadow(
    source: str,
) -> tuple[list[SplitTable], list[dict[str, str]], None]:
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(source)
            await page.emulate_media(media="print")
            split = await split_long_tables(page, options)
            margins = await page.evaluate(
                """() => Array.from(
                  document.getElementById('host').shadowRoot.querySelectorAll('table'),
                  (t) => ({top: getComputedStyle(t).marginTop, bottom: getComputedStyle(t).marginBottom}))"""
            )
            assert margins == [
                {"top": "20px", "bottom": "0px"},
                {"top": "0px", "bottom": "0px"},
                {"top": "0px", "bottom": "20px"},
            ]
            return split, margins, None
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_a_header_row_in_the_body_is_a_body_row_and_nothing_is_copied() -> None:
    rows = "".join(f"<tr><td>B{n:02d}</td><td>x</td></tr>" for n in range(1, 13))
    page = (
        "<!doctype html><html><body><table><tbody>"
        f"<tr><th>Name</th><th>Value</th></tr>{rows}</tbody></table></body></html>"
    )
    split, described, _ = asyncio.run(_split(page))
    assert [(table.rows, table.header) for table in split] == [(13, False)]
    assert {piece["header"] for piece in described["pieces"]} == {None}
    message = split_tables_warning(split, tagged=True)
    assert message is not None and "repeats" not in message


@pytest.mark.browser
@pytest.mark.usefixtures("five_row_pieces")
def test_a_table_in_a_frame_is_left_whole() -> None:
    """A frame was fitted to its document before the bundle; splitting a
    table inside it would push its last rows out of the box the PDF prints."""
    rows = "".join(f"<tr><td>F{n:02d}</td></tr>" for n in range(1, 13))
    inner = (
        f"<table><thead><tr><th>Framed</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    page = (
        "<!doctype html><html><body><iframe id='f' srcdoc=\""
        + inner.replace('"', "&quot;")
        + '"></iframe></body></html>'
    )
    split, _, _ = asyncio.run(_split_framed(page))
    assert split == []


async def _split_framed(source: str) -> tuple[list[SplitTable], int, None]:
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(source)
            frame = page.frame_locator("#f")
            await frame.locator("table").wait_for()
            split = await split_long_tables(page, options)
            tables = await frame.locator("table").count()
            assert tables == 1
            return split, tables, None
        finally:
            await browser.close()


@pytest.mark.browser
def test_at_the_real_limits_the_fixture_prints_as_it_was() -> None:
    split, described, _ = asyncio.run(_split(FIXTURE.as_uri()))
    assert split == []
    assert described["unchanged"]


@pytest.mark.browser
def test_the_limit_splits_ten_thousand_rows_and_not_two_thousand() -> None:
    """At 3 columns: 2,000 rows print in about 70 pages and stay whole;
    10,000 rows in about 345 cost an estimated 0.8 GiB and are split."""
    short, described_short, _ = asyncio.run(_split(_table(2_000)))
    assert short == [] and described_short["unchanged"]
    long, _, _ = asyncio.run(_split(_table(10_000)))
    assert [(table.rows, table.pieces) for table in long] == [(10_000, 10)]
    assert long[0].estimate > long_tables.BUDGET_BYTES


# --------------------------------------------------------------------------- #
# End to end: the PDF has every row, the bundle has the table whole
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def split_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("long-table")
    output = directory / "capture.pdf"
    report = directory / "report.json"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(long_tables, "BUDGET_BYTES", -1)
        patch.setattr(long_tables, "PIECE_ROWS", 5)
        code = main(
            [str(FIXTURE), "--output", str(output), "--report", str(report), "--no-ocr"]
        )
    assert code == 0, f"capture failed with exit {code}"
    return output


def _records(capture: Path) -> list[list[str]]:
    manifest = json.loads(
        (capture.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    report = json.loads((capture.parent / "report.json").read_text("utf-8"))
    embedded = json.loads(
        PdfReader(str(capture)).attachments["capture.json"][0].decode()
    )
    return [manifest["warnings"], report["warnings"], embedded["warnings"]]


@pytest.mark.browser
def test_the_warning_reaches_every_record(split_capture: Path) -> None:
    for warnings in _records(split_capture):
        [message] = [w for w in warnings if "consecutive tables" in w]
        assert message.startswith("A table of 24 rows was printed as 5 ")


@pytest.mark.browser
def test_the_pdf_has_every_row_once_in_order_and_the_bundle_one_table(
    split_capture: Path,
) -> None:
    rendered = "".join(
        page.extract_text() for page in PdfReader(str(split_capture)).pages
    )
    # The text layer runs a row's cells together ("NorthR0112.1steady").
    hours = re.findall(r"R\d\d", rendered)
    assert hours == [f"R{n:02d}" for n in range(1, 25)]
    # The footer is the last piece's alone. Chromium repeats a footer on each
    # page its table spans, so it prints wherever that piece does, and nowhere
    # before it.
    assert rendered.index("FOOTERSENTINEL") > rendered.index("R22")
    # The header once per piece at least, and once more per page a piece
    # breaks across.
    assert rendered.count("Station") >= len(PIECES)
    markdown = (split_capture.with_suffix(".ai") / "content.md").read_text("utf-8")
    assert markdown.count("| Station") == 1
    assert re.findall(r"R\d\d", markdown) == hours
