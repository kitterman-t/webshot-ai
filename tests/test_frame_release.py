"""Scrolling boxes inside frames, released like the page's own (docs/09 P16-17).

`release_clamped_text` walked the page's own document only, so a capped log in
a frame printed 2 of 30 rows even after P16-10 released the same log on the
page. These tests hold the release inside frames to the page's rules, and hold
its boundary. It reaches only frames the page could script: none from another
origin, and none inside what the capture is removing.
"""

from __future__ import annotations

import asyncio
import html
import http.server
import io
import json
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from conftest import serve
from webshot.capture.prepare import BASE_PRINT_CSS, release_clamped_text
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.pdf import print_geometry

FIXTURE = Path(__file__).parent / "fixtures" / "framed_log.html"

ROWS = "".join(f"<p>FROW{n:02d} logged in the frame.</p>" for n in range(30))
LOG = f'<div id="log" style="max-height:6em;overflow-y:auto">{ROWS}</div>'
MARKS = (
    "() => [...document.querySelectorAll('[data-webshot-clamped]')]"
    ".map(e => `${e.id}:${e.dataset.webshotClamped}`)"
)


def _srcdoc(body: str, height: int = 1600) -> str:
    inner = (
        f"<!doctype html><style>body{{margin:0;font:16px/1.5 Helvetica}}</style>{body}"
    )
    return (
        f'<iframe style="width:600px;height:{height}px;border:0" '
        f'srcdoc="{html.escape(inner, quote=True)}"></iframe>'
    )


async def _release(url_or_html: str) -> dict[str, object]:
    """Release a page and its frames as the pipeline does, print it, report."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            if url_or_html.startswith(("http:", "file:")):
                await page.goto(url_or_html)
            else:
                await page.set_content(url_or_html)
            await page.wait_for_load_state("load")
            styles_before = [
                await f.evaluate(
                    "() => [...document.querySelectorAll('body *')].map(e => e.getAttribute('style'))"
                )
                for f in page.frames
            ]
            released = await release_clamped_text(page)
            marks = [await f.evaluate(MARKS) for f in page.frames]
            styles_after = [
                await f.evaluate(
                    "() => [...document.querySelectorAll('body *')].map(e => e.getAttribute('style'))"
                )
                for f in page.frames
            ]
            await page.add_style_tag(content=BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
            pdf = PdfReader(io.BytesIO(await page.pdf(**print_geometry(options))))
            text = "".join(p.extract_text() for p in pdf.pages)
            return {
                "released": released,
                "marks": marks,
                "rows": len(set(re.findall(r"FROW\d\d", text))),
                "styles_unchanged": styles_before == styles_after,
            }
        finally:
            await browser.close()


@pytest.mark.browser
def test_a_capped_log_in_a_frame_prints_every_row() -> None:
    """2 of 30 rows before; all of them once the frame's log is released too."""
    result = asyncio.run(_release(f"<p>Page text.</p>{_srcdoc(LOG)}"))
    assert result["marks"] == [[], ["log:scroll"]]
    assert result["released"] == 1
    assert result["rows"] == 30
    # Every trial undone in the frame as on the page.
    assert result["styles_unchanged"] is True


@pytest.mark.browser
def test_a_log_in_a_frame_in_a_frame_is_released() -> None:
    inner = _srcdoc(LOG, height=1500)
    result = asyncio.run(_release(_srcdoc(inner, height=1600)))
    assert result["marks"][-1] == ["log:scroll"]
    assert result["rows"] == 30


@pytest.mark.browser
def test_a_frame_the_capture_hides_is_left_alone() -> None:
    frame = _srcdoc(LOG).replace("<iframe ", '<iframe data-webshot-hidden="true" ', 1)
    result = asyncio.run(_release(f"<p>Page text.</p>{frame}"))
    assert result["marks"] == [[], []]
    assert result["released"] == 0


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


@pytest.mark.browser
def test_a_frame_from_another_origin_is_decided_on_its_own() -> None:
    """Two loopback ports are two origins: the page cannot script that frame.

    So the scriptable-frame pass leaves it, and since P16-21 the per-frame
    decision takes it instead. This frame is tall and holds no sign-in or
    payment field, so it is released.
    """
    frame_html = f"<!doctype html><body style='margin:0'>{LOG}</body>"
    with serve(_handler({"/log.html": frame_html})) as other:
        page_html = (
            "<!doctype html><p>Page text.</p>"
            f'<iframe style="width:600px;height:1600px;border:0" src="{other}/log.html"></iframe>'
        )
        with serve(_handler({"/": page_html})) as origin:
            result = asyncio.run(_release(f"{origin}/"))
    assert result["marks"] == [[], ["log:scroll"]]
    assert result["released"] == 1


@pytest.mark.browser
def test_a_framed_log_capture_prints_its_last_row(tmp_path: Path) -> None:
    """End to end: the frame's last entry prints, and the page's text too."""
    output = tmp_path / "capture.pdf"
    assert main([str(FIXTURE), "--output", str(output)]) == 0
    rendered = "".join(page.extract_text() for page in PdfReader(str(output)).pages)
    assert "FRAMEBASELINEALPHA" in rendered
    assert "FRAMELASTROWOMEGA" in rendered
    manifest = json.loads(
        (output.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert isinstance(manifest["warnings"], list)
