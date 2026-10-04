"""Scrolling boxes released for print, and only where that is safe (docs/09 P16-10).

A capped box that scrolls on screen prints only what was scrolled into view:
three rows of a thirty-row log (P16-8). `release_clamped_text` now lets such a
box take its full height. That changes what prints, which is the point, and it
must not change it anywhere it would do harm. A released box that overlapped
the content after it would hide text where no check looks.

So these tests hold both sides. The release must bring every row onto the
paper. It must also decline, and leave the box to the clipping check's warning,
wherever the page cannot make room: a panel positioned over the page, a pane
sized by a flex layout, a row of a fixed grid. And a box the capture is hiding
stays hidden.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import BASE_PRINT_CSS, release_clamped_text
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import find_clipped_text
from webshot.render.pdf import print_geometry

APP_SHELL = Path(__file__).parent / "fixtures" / "app_shell.html"
GRID_PANELS = Path(__file__).parent / "fixtures" / "grid_panels.html"
POSITIONED = Path(__file__).parent / "fixtures" / "positioned_panels.html"

ROWS = "".join(f"<p>ROW{n:02d} the desk reopened.</p>" for n in range(30))


async def _release(body: str) -> dict[str, object]:
    """Release a page, print it, and report what was marked and what printed."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            await page.set_content(
                "<!doctype html><html><head><style>"
                "body { margin: 0; font: 16px/1.5 Helvetica, Arial, sans-serif; }"
                f"</style></head><body>{body}</body></html>"
            )
            styles_before = await page.evaluate(
                "() => [...document.querySelectorAll('body *')].map(e => e.getAttribute('style'))"
            )
            released = await release_clamped_text(page)
            marks = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-clamped]')]
                    .map(e => `${e.id || e.localName}:${e.dataset.webshotClamped}`)"""
            )
            unflexed = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-unflexed]')]
                    .map(e => e.id || e.localName)"""
            )
            ungridded = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-ungridded]')]
                    .map(e => e.id || e.localName)"""
            )
            unpositioned = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-unpositioned]')]
                    .map(e => e.id || e.localName)"""
            )
            styles_after = await page.evaluate(
                "() => [...document.querySelectorAll('body *')].map(e => e.getAttribute('style'))"
            )
            # As the pipeline does it: the print CSS, then print media.
            await page.add_style_tag(content=BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
            pdf = PdfReader(io.BytesIO(await page.pdf(**print_geometry(options))))
            text = "".join(p.extract_text() for p in pdf.pages)
            clipping = await find_clipped_text(page, options)
            return {
                "released": released,
                "marks": marks,
                "unflexed": unflexed,
                "ungridded": ungridded,
                "unpositioned": unpositioned,
                "pages": len(pdf.pages),
                "rows": len(set(re.findall(r"ROW\d+", text))),
                "scrolled": clipping.scrolled,
                "styles_unchanged": styles_before == styles_after,
                "text": text,
            }
        finally:
            await browser.close()


def _run(body: str) -> dict[str, object]:
    return asyncio.run(_release(body))


@pytest.mark.browser
def test_a_capped_log_prints_every_row() -> None:
    """P16-8's log: three rows of thirty before the release, all of them after."""
    result = _run(f'<div id="log" style="max-height:6em;overflow-y:auto">{ROWS}</div>')
    assert result["marks"] == ["log:scroll"]
    assert result["rows"] == 30
    # Nothing left for the clipping check to report.
    assert result["scrolled"] == 0


@pytest.mark.browser
def test_a_fixed_height_scroll_box_prints_every_row() -> None:
    result = _run(f'<div id="box" style="height:120px;overflow:scroll">{ROWS}</div>')
    assert result["marks"] == ["box:scroll"]
    assert result["rows"] == 30


@pytest.mark.browser
def test_a_box_inside_a_released_box_is_released_too() -> None:
    """Measured with its ancestor already released: the page it will print in.

    Both boxes overflow. The walk reaches the outer one first, and keeps its
    trial applied, so the inner one's parent is free to grow when it is tried.
    """
    inner = f'<div id="inner" style="max-height:8em;overflow-y:auto">{ROWS}</div>'
    outer = (
        f'<div id="outer" style="max-height:10em;overflow-y:auto">{ROWS}{inner}</div>'
    )
    result = _run(outer)
    assert result["marks"] == ["outer:scroll", "inner:scroll"]
    assert result["rows"] == 30


@pytest.mark.browser
def test_a_box_that_would_overflow_a_capped_box_releases_both() -> None:
    """The outer box had room for the inner one, until the inner one grew.

    Released alone, the inner box would push the outer one into scrolling, so
    the first trial declines it (P16-10). The chain releases the outer box
    with it, and every row prints (P16-13).
    """
    inner = f'<div id="inner" style="max-height:8em;overflow-y:auto">{ROWS}</div>'
    result = _run(
        f'<div id="outer" style="max-height:10em;overflow-y:auto">{inner}</div>'
    )
    assert result["marks"] == ["outer:shell", "inner:scroll"]
    assert result["rows"] == 30
    assert result["scrolled"] == 0


@pytest.mark.browser
def test_the_page_keeps_no_inline_style_of_webshots() -> None:
    """Trials are undone, kept or not; the mark and the print rule do the work."""
    result = _run(
        f'<div id="log" style="max-height:6em;overflow-y:auto">{ROWS}</div>'
        f'<div style="position:absolute;top:0;max-height:6em;overflow:auto">{ROWS}</div>'
    )
    assert result["styles_unchanged"] is True


@pytest.mark.browser
def test_a_box_with_no_style_attribute_gets_none_back() -> None:
    """Styled by a class, so it has no `style` attribute, and must not gain one.

    Chromium writes a changed inline style back to the attribute lazily, so
    removing the attribute straight after a trial left `style=""` behind.
    Since P16-15 the positioned panel is moved rather than declined, so both
    boxes are released and kept; neither may carry the attribute.
    """
    body = (
        "<style>.log { max-height: 6em; overflow-y: auto; }"
        ".panel { position: absolute; top: 0; max-height: 6em; overflow-y: auto; }</style>"
        f'<div id="log" class="log">{ROWS}</div><div id="panel" class="panel">{ROWS}</div>'
    )
    result = _run(body)
    assert result["styles_unchanged"] is True
    assert result["marks"] == ["log:scroll", "panel:scroll"]
    assert result["unpositioned"] == ["panel"]


@pytest.mark.browser
def test_a_box_with_room_to_spare_is_left_alone() -> None:
    result = _run(
        '<div id="roomy" style="max-height:40em;overflow-y:auto"><p>Short.</p></div>'
    )
    assert result["released"] == 0


@pytest.mark.browser
def test_a_panel_positioned_over_the_page_is_put_into_the_flow() -> None:
    """Grown where it floats, it would cover the text under it (P16-15).

    In the flow, where it sits in the document, it pushes that text down
    instead, and every row prints.
    """
    panel = (
        f'<div id="panel" style="position:absolute;top:0;left:0;width:300px;'
        f'max-height:6em;overflow-y:auto">{ROWS}</div><p>UNDERTEXT beneath the panel.</p>'
    )
    result = _run(panel)
    assert result["marks"] == ["panel:scroll"]
    assert result["unpositioned"] == ["panel"]
    assert result["rows"] == 30
    assert "UNDERTEXT" in str(result["text"])
    assert result["scrolled"] == 0
    assert result["styles_unchanged"] is True


@pytest.mark.browser
def test_a_fixed_panel_prints_its_rows_once() -> None:
    """Fixed, it printed its first rows again on every page; in the flow, once."""
    filler = "".join(f"<p>Paragraph {n} of the page body.</p>" for n in range(80))
    body = (
        f"{filler}"
        f'<div id="chat" style="position:fixed;bottom:0;right:0;width:300px;'
        f'max-height:6em;overflow-y:auto">{ROWS}</div>'
    )
    result = _run(body)
    assert result["unpositioned"] == ["chat"]
    assert result["rows"] == 30
    assert str(result["text"]).count("ROW00 ") == 1


@pytest.mark.browser
def test_a_centred_modal_is_put_into_the_flow() -> None:
    """A `translate` that centred it would carry it off in the flow, so it goes too."""
    body = (
        "<p>The page behind the dialog.</p>"
        f'<div id="dialog" style="position:fixed;top:50%;left:50%;'
        f'transform:translate(-50%,-50%);width:400px;max-height:8em;overflow-y:auto">{ROWS}</div>'
    )
    result = _run(body)
    assert result["unpositioned"] == ["dialog"]
    assert result["rows"] == 30


@pytest.mark.browser
def test_a_panel_parked_off_the_page_is_neither_moved_nor_reported() -> None:
    """`left: -9999px` hides it from the screen too; surfacing it would be wrong."""
    body = (
        f'<div id="off" style="position:absolute;left:-9999px;top:0;width:300px;'
        f'max-height:6em;overflow-y:auto">{ROWS}</div><p>Visible.</p>'
    )
    result = _run(body)
    assert result["marks"] == []
    assert result["unpositioned"] == []
    assert result["rows"] == 0
    # The clipping check agrees: nothing a reader could reach was lost.
    assert result["scrolled"] == 0


@pytest.mark.browser
def test_an_invisible_panel_is_not_moved() -> None:
    body = (
        f'<div id="ghost" style="position:absolute;top:0;visibility:hidden;width:300px;'
        f'max-height:6em;overflow-y:auto">{ROWS}</div><p>Visible.</p>'
    )
    result = _run(body)
    assert result["unpositioned"] == []


@pytest.mark.browser
def test_a_pane_sized_by_a_flex_column_is_released_as_a_chain() -> None:
    """Its own cap is not what holds it, so the column is released with it (P16-13).

    `height: auto` alone leaves the pane as short as `flex: 1` makes it, which
    is why the first trial declines it. The chain releases the column above it
    and stops `flex` sizing the pane.
    """
    shell = (
        '<div id="shell" style="display:flex;flex-direction:column;height:400px">'
        "<header>Header</header>"
        f'<main id="pane" style="flex:1;min-height:0;overflow-y:auto">{ROWS}</main>'
        "</div>"
    )
    result = _run(shell)
    assert result["marks"] == ["shell:shell", "pane:scroll"]
    assert result["unflexed"] == ["pane"]
    assert result["rows"] == 30
    assert result["scrolled"] == 0
    assert result["styles_unchanged"] is True


#: The app shell as single-page apps build it: the root fixed at the viewport,
#: its own scrollbar turned off, and one pane that scrolls.
SHELL_CSS = (
    "html, body { height: 100%; overflow: hidden; }"
    ".app { display: flex; flex-direction: column; height: 100vh; }"
    "header { height: 60px; flex: none; }"
)
MANY_ROWS = "".join(f"<p>ROW{n:03d} the desk reopened.</p>" for n in range(200))


@pytest.mark.browser
def test_a_full_viewport_shell_prints_every_page() -> None:
    """One page of 200 rows before the release; every row, over several pages, after.

    The chain reaches the root here: `body` and `html` are 100% tall and hide
    their overflow, and both are released with the column.
    """
    body = (
        f"<style>{SHELL_CSS}</style>"
        '<div class="app" id="app"><header>Header</header>'
        f'<main id="pane" style="flex:1;min-height:0;overflow-y:auto">{MANY_ROWS}</main></div>'
    )
    result = _run(body)
    assert result["marks"] == ["html:shell", "body:shell", "app:shell", "pane:scroll"]
    assert result["rows"] == 200
    assert isinstance(result["pages"], int) and result["pages"] > 1
    assert result["scrolled"] == 0


@pytest.mark.browser
def test_a_row_shell_releases_its_pane_beside_the_sidebar() -> None:
    """In a row, `flex` sets widths, so nothing is unflexed and the sidebar keeps its place."""
    body = (
        f"<style>{SHELL_CSS} .app {{ flex-direction: row; }}</style>"
        '<div class="app" id="app"><nav id="side" style="width:200px">Sections</nav>'
        f'<main id="pane" style="flex:1;overflow-y:auto">{MANY_ROWS}</main></div>'
    )
    result = _run(body)
    assert "pane:scroll" in result["marks"]
    assert result["unflexed"] == []
    assert result["rows"] == 200


@pytest.mark.browser
def test_a_chain_that_would_cover_a_neighbour_is_undone() -> None:
    """A footer pulled up over the pane would sit on its rows once the pane grows.

    Everything the chain changed is put back, and the pane stays reported.
    """
    body = (
        '<div id="shell" style="display:flex;flex-direction:column;height:400px">'
        f'<main id="pane" style="flex:1;min-height:0;overflow-y:auto">{ROWS}</main>'
        '<footer style="margin-top:-150px;height:150px;background:#eee">Footer</footer>'
        "</div>"
    )
    result = _run(body)
    assert result["marks"] == []
    assert result["styles_unchanged"] is True
    assert isinstance(result["scrolled"], int) and result["scrolled"] > 0


@pytest.mark.browser
def test_a_box_in_a_fixed_grid_row_is_released_with_the_grid_s_rows() -> None:
    """The row track sets its height whatever the boxes above do (P16-14).

    `height: auto` and every box above it leave the cell exactly as short, so
    the grid's rows are left to their content. The cell grows, the second row
    moves down beneath it, and nothing overlaps.
    """
    grid = (
        '<div id="grid" style="display:grid;grid-template-rows:120px 120px">'
        f'<div id="cell" style="max-height:120px;overflow-y:auto">{ROWS}</div>'
        "<div>SECONDROWTEXT follows the cell.</div></div>"
    )
    result = _run(grid)
    assert result["marks"] == ["cell:scroll"]
    assert result["ungridded"] == ["grid"]
    assert result["rows"] == 30
    assert "SECONDROWTEXT" in str(result["text"])
    assert result["scrolled"] == 0
    assert result["styles_unchanged"] is True


@pytest.mark.browser
def test_a_tile_in_fixed_auto_rows_is_released() -> None:
    """A dashboard of tiles: `grid-auto-rows` fixes every row, not a template."""
    tiles = "".join(f"<div>TILE{n} summary</div>" for n in range(3))
    grid = (
        '<div id="grid" style="display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:120px">'
        f'<div id="cell" style="overflow-y:auto">{ROWS}</div>{tiles}</div>'
    )
    result = _run(grid)
    assert result["ungridded"] == ["grid"]
    assert result["rows"] == 30
    assert all(f"TILE{n}" in str(result["text"]) for n in range(3))


@pytest.mark.browser
def test_a_cell_in_a_named_area_is_released() -> None:
    grid = (
        "<div id=\"grid\" style=\"display:grid;grid-template-areas:'a' 'b';"
        'grid-template-rows:120px 80px">'
        f'<div id="cell" style="grid-area:a;overflow-y:auto">{ROWS}</div>'
        '<div style="grid-area:b">AREABTEXT below.</div></div>'
    )
    result = _run(grid)
    assert result["ungridded"] == ["grid"]
    assert result["rows"] == 30
    assert "AREABTEXT" in str(result["text"])


@pytest.mark.browser
def test_a_cell_sharing_its_area_with_an_overlay_stays_capped() -> None:
    """A caption laid over the cell in the same grid area would sit on its rows."""
    grid = (
        '<div id="grid" style="display:grid;grid-template-rows:120px">'
        f'<div id="cell" style="grid-row:1;grid-column:1;overflow-y:auto">{ROWS}</div>'
        '<div style="grid-row:1;grid-column:1;align-self:end;background:#fff">Caption</div>'
        "</div>"
    )
    result = _run(grid)
    assert result["marks"] == []
    assert result["ungridded"] == []
    assert result["styles_unchanged"] is True
    assert isinstance(result["scrolled"], int) and result["scrolled"] > 0


@pytest.mark.browser
def test_a_sideways_scroller_is_not_released() -> None:
    """The horizontal axis stays warn-only (P16-8): released, it runs off the page."""
    row = "".join(
        f"<td style='white-space:nowrap'>Column {n} of thirty</td>" for n in range(30)
    )
    result = _run(
        f'<div id="wrap" style="overflow-x:auto"><table><tr>{row}</tr></table></div>'
    )
    assert result["marks"] == []


@pytest.mark.browser
def test_a_hidden_box_stays_hidden() -> None:
    """The release never touches what the capture removes."""
    result = _run(
        f'<div id="log" data-webshot-hidden="true" style="max-height:6em;overflow-y:auto">'
        f"{ROWS}</div><p>Visible.</p>"
    )
    assert result["marks"] == []
    assert result["rows"] == 0


@pytest.mark.browser
def test_an_app_shell_capture_prints_the_whole_pane(tmp_path: Path) -> None:
    """End to end: the first and last entries print, and nothing is reported hidden."""
    output = tmp_path / "capture.pdf"
    assert main([str(APP_SHELL), "--output", str(output)]) == 0
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert not [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    reader = PdfReader(str(output))
    rendered = "".join(page.extract_text() for page in reader.pages)
    assert "SHELLFIRSTROWALPHA" in rendered
    assert "SHELLLASTROWOMEGA" in rendered
    assert len(reader.pages) > 1


@pytest.mark.browser
def test_a_grid_dashboard_capture_prints_every_panel_item(tmp_path: Path) -> None:
    """End to end: both panels' last items print, and nothing is reported hidden."""
    output = tmp_path / "capture.pdf"
    assert main([str(GRID_PANELS), "--output", str(output)]) == 0
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert not [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    rendered = "".join(page.extract_text() for page in PdfReader(str(output)).pages)
    for sentinel in ("GRIDBASELINEALPHA", "GRIDLASTTICKETOMEGA", "GRIDLASTVISITSIGMA"):
        assert sentinel in rendered, sentinel


@pytest.mark.browser
def test_a_positioned_panel_capture_prints_each_panel_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """End to end: both panels print whole and once, and the move is declared.

    `--mode faithful`, because clean mode removes a `nav` as page chrome, and
    the contents list is exactly that.
    """
    output = tmp_path / "capture.pdf"
    with caplog.at_level(logging.INFO, logger="webshot"):
        code = main([str(POSITIONED), "--output", str(output), "--mode", "faithful"])
    assert code == 0
    assert "Moved 2 positioned panel(s) into the page flow" in caplog.text
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert not [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    rendered = "".join(page.extract_text() for page in PdfReader(str(output)).pages)
    for sentinel in (
        "POSITIONEDBASELINEALPHA",
        "POSITIONEDTOCOMEGA",
        "POSITIONEDHELPSIGMA",
    ):
        assert rendered.count(sentinel) == 1, sentinel
