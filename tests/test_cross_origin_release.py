"""Scrolling boxes in frames from another origin: decided, released, and checked (docs/09 P16-21).

No page script can reach into a frame from another origin, so P16-17 left such
frames alone, and P16-18 reports what their scrolling boxes hide. A user's
`--css` cannot reach them either, so the report was also the end of it. Now
WebShot decides frame by frame. It never touches a frame with a sign-in or
payment field. It keeps a release only if every released box still fits inside
the frame, and it checks the printed layout afterwards to confirm the release
held. These tests hold each of those decisions, and the check that catches one
that went wrong.
"""

from __future__ import annotations

import asyncio
import http.server
import io
import json
import logging
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from conftest import serve
from webshot.capture.prepare import BASE_PRINT_CSS, release_clamped_text
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import PrintClipping, clipping_warnings, find_clipped_text
from webshot.render.pdf import print_geometry

ROWS = "".join(f"<p>XROW{n:02d} logged in the embedded desk.</p>" for n in range(30))
LOG = f'<div id="log" style="max-height:6em;overflow-y:auto">{ROWS}</div>'
FRAME_HEAD = "<!doctype html><body style='margin:0;font:16px/1.5 Helvetica'>"


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


def _page(other: str, height: int) -> str:
    return (
        "<!doctype html><title>Desk</title><p>Page text.</p>"
        f'<iframe id="frame" title="Desk log" style="width:600px;height:{height}px;border:0" '
        f'src="{other}/frame.html"></iframe>'
    )


async def _run(
    origin: str, *, shrink_after_release: bool = False
) -> tuple[dict[str, object], PrintClipping]:
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            await page.goto(f"{origin}/")
            await page.wait_for_load_state("load")
            released = await release_clamped_text(page)
            frame = page.frames[1]
            marks = await frame.evaluate(
                "() => [...document.querySelectorAll('[data-webshot-clamped]')].map(e => e.id)"
            )
            if shrink_after_release:
                # Something after the decision leaves the frame too short: the
                # page's own print stylesheet, say. Only the check after the
                # render can see it.
                await page.evaluate(
                    "() => { document.getElementById('frame').style.height = '150px'; }"
                )
            await page.add_style_tag(content=BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            pdf = PdfReader(io.BytesIO(await page.pdf(**print_geometry(options))))
            text = "".join(p.extract_text() for p in pdf.pages)
            clipping = await find_clipped_text(page, options)
            return (
                {
                    "released": released,
                    "marks": marks,
                    "rows": len(set(re.findall(r"XROW\d\d", text))),
                },
                clipping,
            )
        finally:
            await browser.close()


def _capture(
    frame_html: str, height: int, **kwargs: bool
) -> tuple[dict[str, object], PrintClipping]:
    with serve(_handler({"/frame.html": frame_html})) as other:
        with serve(_handler({"/": _page(other, height)})) as origin:
            return asyncio.run(_run(origin, **kwargs))


@pytest.mark.browser
def test_a_tall_frame_from_another_origin_is_released_and_confirmed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="webshot"):
        result, clipping = _capture(FRAME_HEAD + LOG, height=1600)
    assert result["marks"] == ["log"]
    assert result["rows"] == 30
    assert "inside iframe#frame, a frame from another origin" in caplog.text
    # The check after the render confirms it: nothing hidden, nothing unheld.
    assert clipping.scrolled == 0
    assert clipping.unheld == ()


@pytest.mark.browser
def test_a_frame_too_short_for_the_release_is_left_alone_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Released, the log would run past the frame's bottom, where nothing reports it."""
    with caplog.at_level(logging.INFO, logger="webshot"):
        result, clipping = _capture(FRAME_HEAD + LOG, height=200)
    assert result["marks"] == []
    assert result["released"] == 0
    assert "would run past the frame's own bottom edge" in caplog.text
    assert clipping.scrolled > 0
    assert clipping.unheld == ()


@pytest.mark.browser
def test_a_frame_with_a_sign_in_field_is_never_released(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sign_in = (
        FRAME_HEAD + '<label>Password <input type="password" name="pw"></label>' + LOG
    )
    with caplog.at_level(logging.INFO, logger="webshot"):
        result, clipping = _capture(sign_in, height=1600)
    assert result["marks"] == []
    assert "it holds a sign-in or payment field" in caplog.text
    assert clipping.scrolled > 0


@pytest.mark.browser
def test_a_release_that_did_not_hold_in_print_is_caught() -> None:
    """The decision was right when made; the printed layout proved it wrong."""
    result, clipping = _capture(
        FRAME_HEAD + LOG, height=1600, shrink_after_release=True
    )
    assert result["marks"] == ["log"]
    [unheld] = clipping.unheld
    assert unheld.startswith("iframe#frame")
    assert "run past the frame's own bottom edge" in unheld
    assert any("did not hold" in w for w in clipping_warnings(clipping))


@pytest.mark.browser
def test_a_released_cross_origin_frame_capture_prints_its_last_row(
    tmp_path: Path,
) -> None:
    """End to end over loopback: every row prints and nothing is reported."""
    last = ROWS.replace("XROW29", "XROWLASTOMEGA")
    frame_html = (
        FRAME_HEAD
        + f'<div id="log" style="max-height:6em;overflow-y:auto">{last}</div>'
    )
    output = tmp_path / "capture.pdf"
    with serve(_handler({"/frame.html": frame_html})) as other:
        with serve(_handler({"/": _page(other, 1600)})) as origin:
            assert main([f"{origin}/", "--output", str(output)]) == 0
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert not [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    assert not [w for w in manifest["warnings"] if "did not hold" in w]
    rendered = "".join(p.extract_text() for p in PdfReader(str(output)).pages)
    assert "XROWLASTOMEGA" in rendered
