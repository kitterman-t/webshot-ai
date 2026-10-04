"""Text lost inside frames, reported: in scrolling boxes, and at the page edge (docs/09 P16-18, P16-19).

The clipping check measured the page's own document and no frame, so a
scrolling box inside a frame lost its text with no warning. That happened when
the release declined the box, and always in a frame from another origin, which
the release may not touch. These tests hold the report on both kinds of frame.
They also hold what it leaves to others: a frame the capture hides, and a frame
that cuts off its own document, which is the frame-fitting work's to report.
"""

from __future__ import annotations

import asyncio
import html
import http.server
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from conftest import serve
from webshot.capture.prepare import release_clamped_text
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import PrintClipping, clipping_warnings, find_clipped_text

FRAMED_OVERFLOW = Path(__file__).parent / "fixtures" / "print_overflow_framed.html"

ROWS = "".join(f"<p>FROW{n:02d} logged in the frame.</p>" for n in range(30))
LOG = f'<div id="log" style="max-height:6em;overflow-y:auto">{ROWS}</div>'


def _srcdoc(body: str, *, attrs: str = 'title="Desk log"', height: int = 1600) -> str:
    inner = (
        f"<!doctype html><style>body{{margin:0;font:16px/1.5 Helvetica}}</style>{body}"
    )
    return (
        f'<iframe {attrs} style="width:600px;height:{height}px;border:0" '
        f'srcdoc="{html.escape(inner, quote=True)}"></iframe>'
    )


async def _measure(url_or_html: str, *, release: bool = False) -> PrintClipping:
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            if url_or_html.startswith(("http:", "file:")):
                await page.goto(url_or_html)
            else:
                await page.set_content(url_or_html)
            await page.wait_for_load_state("load")
            if release:
                await release_clamped_text(page)
            await page.emulate_media(media="print")
            return await find_clipped_text(page, options)
        finally:
            await browser.close()


def _handler(pages: dict[str, str]) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = pages.get(self.path, "").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    return Handler


def _cross_origin_page(other: str) -> str:
    return (
        "<!doctype html><title>Desk</title><p>Page text.</p>"
        '<iframe title="Desk log" style="width:600px;height:1600px;border:0" '
        f'src="{other}/log.html"></iframe>'
    )


FRAME_HTML = (
    f"<!doctype html><body style='margin:0;font:16px/1.5 Helvetica'>{LOG}</body>"
)
#: A frame the release never changes (P16-21): it holds a sign-in field, so the
#: loss in it stays, and only this report records it.
SIGN_IN_FRAME_HTML = FRAME_HTML.replace(
    "<body style='margin:0;font:16px/1.5 Helvetica'>",
    "<body style='margin:0;font:16px/1.5 Helvetica'><input type='password' name='pw'>",
)


@pytest.mark.browser
def test_a_scrolling_box_in_a_frame_is_reported_and_named() -> None:
    """Unreleased, a frame's log loses its rows, and the warning says where."""
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{_srcdoc(LOG)}"))
    assert clipping.scrolled >= 20
    assert clipping.scrolled_sample == 'div#log in iframe[title="Desk log"]'
    [message] = clipping_warnings(clipping)
    assert 'div#log in iframe[title="Desk log"]' in message


@pytest.mark.browser
def test_a_released_frame_has_nothing_left_to_report() -> None:
    """The release in frames (P16-17) and this report agree about the same box."""
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{_srcdoc(LOG)}", release=True))
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_a_frame_from_another_origin_is_reported_when_not_released() -> None:
    """The release leaves a sign-in frame alone (P16-21), so the report is the only record."""
    with serve(_handler({"/log.html": SIGN_IN_FRAME_HTML})) as other:
        with serve(_handler({"/": _cross_origin_page(other)})) as origin:
            clipping = asyncio.run(_measure(f"{origin}/", release=True))
    assert clipping.scrolled >= 20
    assert clipping.scrolled_sample == 'div#log in iframe[title="Desk log"]'


@pytest.mark.browser
def test_a_frame_that_does_not_print_is_not_reported() -> None:
    hidden = _srcdoc(LOG, attrs='title="Hidden" data-webshot-hidden="true"')
    undisplayed = _srcdoc(LOG, attrs='title="Undisplayed"').replace(
        'style="width:600px', 'style="display:none;width:600px', 1
    )
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{hidden}{undisplayed}"))
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_a_frame_cutting_off_its_own_document_is_not_this_report() -> None:
    """A short frame over a long document is the frame-fitting work's (P16-16)."""
    long_document = "".join(
        f"<p>Entry {n} of a long framed page.</p>" for n in range(80)
    )
    clipping = asyncio.run(
        _measure(f"<p>Page text.</p>{_srcdoc(long_document, height=200)}")
    )
    assert clipping.scrolled == 0
    assert clipping.elements == 0


@pytest.mark.browser
def test_a_cross_origin_frame_s_loss_reaches_the_manifest(tmp_path: Path) -> None:
    """End to end over loopback: the capture's warning names the box and its frame."""
    output = tmp_path / "capture.pdf"
    with serve(_handler({"/log.html": SIGN_IN_FRAME_HTML})) as other:
        with serve(_handler({"/": _cross_origin_page(other)})) as origin:
            assert main([f"{origin}/", "--output", str(output)]) == 0
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    found = [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    assert len(found) == 1, manifest["warnings"]
    assert 'div#log in iframe[title="Desk log"]' in found[0]


#: P16-1's pushed print column, and a line long enough to reach its far side.
PUSHED = (
    "<style>@media print { .container { box-sizing: border-box; width: 750px;"
    " margin: 0 auto; padding: 0 15px; } .column { box-sizing: border-box;"
    " position: relative; float: left; width: 600px; margin-left: 75px;"
    " left: 250px; padding: 0 15px; } }</style>"
)
LONG = (
    "<p style='text-align:justify'>"
    + " ".join(["an ordinary sentence of the form"] * 40)
    + "</p>"
)


@pytest.mark.browser
def test_the_page_edge_cutting_a_frame_is_reported() -> None:
    """P16-1's column holding a frame: the edge cuts the frame's lines too (P16-19)."""
    frame = _srcdoc(LONG, attrs='title="Appointment form"', height=400).replace(
        "width:600px", "width:100%", 1
    )
    page = f'{PUSHED}<div class="container"><div class="column">{frame}</div></div>'
    clipping = asyncio.run(_measure(page))
    assert clipping.elements >= 1
    assert clipping.sample == 'p in iframe[title="Appointment form"]'
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_a_frame_cutting_its_own_lines_is_not_the_page_edge() -> None:
    """A narrow frame inside the page clips its own wide text: not the page's doing."""
    wide = (
        "<p style='white-space:nowrap'>"
        + "a line far wider than its frame " * 20
        + "</p>"
    )
    frame = _srcdoc(wide, attrs='title="Narrow"', height=200).replace(
        "width:600px", "width:300px", 1
    )
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{frame}"))
    assert clipping.elements == 0


@pytest.mark.browser
def test_a_frame_parked_off_the_page_is_not_reported() -> None:
    frame = _srcdoc(LONG, attrs='title="Parked"', height=200).replace(
        'style="width:600px', 'style="position:absolute;left:-9999px;width:600px', 1
    )
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{frame}"))
    assert clipping.elements == 0


@pytest.mark.browser
def test_the_left_edge_cutting_a_frame_is_reported() -> None:
    frame = _srcdoc(LONG, attrs='title="Pulled"', height=300).replace(
        'style="width:600px', 'style="position:relative;left:-150px;width:600px', 1
    )
    clipping = asyncio.run(_measure(f"<p>Page text.</p>{frame}"))
    assert clipping.elements >= 1
    assert clipping.sample == 'p in iframe[title="Pulled"]'


@pytest.mark.browser
def test_a_framed_overflow_capture_names_the_frame(tmp_path: Path) -> None:
    """End to end: the page-edge warning reaches the manifest and names the frame.

    No assertion that the frame's words are missing from the text layer: the
    capture's OCR layer for the frame carries its text, read from a screenshot
    taken at the screen's width, where nothing is cut.
    """
    output = tmp_path / "capture.pdf"
    assert main([str(FRAMED_OVERFLOW), "--output", str(output)]) == 0
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    found = [
        w
        for w in manifest["warnings"]
        if w.startswith("Text runs off the printed page")
    ]
    assert len(found) == 1, manifest["warnings"]
    assert 'in iframe[title="Appointment form"]' in found[0]
    rendered = "".join(p.extract_text() for p in PdfReader(str(output)).pages)
    assert "FRAMEEDGEBASELINEALPHA" in rendered
