"""The wait for images waits for what can print, and names what it gave up on.

Every capture of a page holding a lazy image with no box waited out the whole
30-second deadline: such an image never starts loading, so it fires neither
`load` nor `error`. The run then warned "Some fonts or images were still
loading", which named nothing and was false. On mirrored Wikipedia pages that
was most of a 39.6-second capture (docs/09 P20-2).

`lazy_images.html` holds one lazy image with no box and one with a box far
below the first screen. Served over loopback, because the request log is how
the test knows which images were fetched.
"""

from __future__ import annotations

import asyncio
import http.server
import io
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from conftest import serve
from webshot.capture.prepare import (
    _wait_for_fonts_and_images,
    image_name,
    pending_images_warning,
)
from webshot.capture.session import DEFAULT_VIEWPORT

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lazy_images.html"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "#06c").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def origin() -> Iterator[dict[str, Any]]:
    requested: list[str] = []
    release = threading.Event()
    png = _png()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested.append(self.path)
            if self.path.startswith("/stall/"):
                # Never answers within the test's deadline.
                release.wait(timeout=10)
                try:
                    self.send_error(404)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the browser closed while this request was held
                return
            if self.path == "/lazy_images.html":
                body, kind = FIXTURE.read_bytes(), "text/html"
            elif self.path.endswith(".png"):
                body, kind = png, "image/png"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    with serve(Handler) as address:
        yield {"url": address, "requested": requested}
        release.set()


async def _waited(url: str, timeout_s: float, stalled: int = 0) -> dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport=DEFAULT_VIEWPORT)
            await page.goto(f"{url}/lazy_images.html")
            await page.evaluate(
                """count => {
                    // Chromium opens six connections to a host. Stalled
                    // images queue the rest, which this image is not about.
                    if (count) document.getElementById('below').remove();
                    for (let index = 0; index < count; index += 1) {
                        const image = document.createElement('img');
                        image.width = 40;
                        image.height = 40;
                        image.src = `/stall/${index}.png?token=secret-${index}`;
                        document.body.append(image);
                    }
                }""",
                stalled,
            )
            started = time.monotonic()
            warnings = await _wait_for_fonts_and_images(page, timeout_s=timeout_s)
            elapsed = time.monotonic() - started
            loaded = await page.evaluate(
                """() => Object.fromEntries(['unshown', 'below'].map(id => {
                    const image = document.getElementById(id);
                    return [id, Boolean(image?.complete && image.naturalWidth > 0)];
                }))"""
            )
            return {"warnings": warnings, "elapsed": elapsed, "loaded": loaded}
        finally:
            await browser.close()


@pytest.mark.browser
def test_an_image_with_no_box_is_not_waited_for(origin: dict[str, Any]) -> None:
    waited = asyncio.run(_waited(origin["url"], timeout_s=8))
    assert waited["warnings"] == []
    assert waited["elapsed"] < 8, waited
    # Never fetched: it does not print, so loading it would change nothing.
    assert "/unshown.png" not in origin["requested"]
    assert waited["loaded"]["unshown"] is False


@pytest.mark.browser
def test_a_lazy_image_the_scroll_did_not_reach_is_loaded_before_print(
    origin: dict[str, Any],
) -> None:
    """No scroll ran here, as under `--no-scroll`: without the switch to eager
    loading the image is never fetched and the PDF prints its box empty."""
    waited = asyncio.run(_waited(origin["url"], timeout_s=8))
    assert "/below.png" in origin["requested"]
    assert waited["loaded"]["below"] is True


@pytest.mark.browser
def test_the_warning_names_the_images_still_loading(origin: dict[str, Any]) -> None:
    waited = asyncio.run(_waited(origin["url"], timeout_s=1, stalled=7))
    names = [f"{origin['url']}/stall/{index}.png" for index in range(5)]
    assert waited["warnings"] == [
        "7 images had not finished loading after a 1-second wait, so the PDF "
        f"may show them blank or incomplete: {', '.join(names)}, and 2 more."
    ]
    assert waited["elapsed"] < 8, waited


def test_an_image_is_named_without_its_query() -> None:
    assert (
        image_name("https://cdn.example/a/b.png?token=abc#frag")
        == "https://cdn.example/a/b.png"
    )
    assert image_name("data:image/png;base64,AAAA") == "a data: URL"
    long = "https://cdn.example/" + "x" * 300 + ".png"
    assert len(image_name(long)) == 160


def test_and_more_counts_images_not_addresses() -> None:
    """Two images share an address once the query is gone: each image is
    named or counted, and none is counted twice."""
    urls = [f"https://cdn.example/{index}.png" for index in range(6)]
    urls.append("https://cdn.example/5.png?size=2")
    assert pending_images_warning(urls, 30).endswith(
        ": https://cdn.example/0.png, https://cdn.example/1.png, "
        "https://cdn.example/2.png, https://cdn.example/3.png, "
        "https://cdn.example/4.png, and 2 more."
    )
    shared = ["https://cdn.example/a.png?v=1", "https://cdn.example/a.png?v=2"]
    assert pending_images_warning(shared, 30) == (
        "2 images had not finished loading after a 30-second wait, so the PDF "
        "may show them blank or incomplete: https://cdn.example/a.png."
    )


def test_one_image_is_counted_in_the_singular() -> None:
    assert pending_images_warning(["https://cdn.example/a.png"], 30) == (
        "1 image had not finished loading after a 30-second wait, so the PDF "
        "may show it blank or incomplete: https://cdn.example/a.png."
    )
