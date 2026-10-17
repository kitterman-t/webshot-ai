"""A bot check's page shown with HTTP 200 fails the capture (docs/09 P78).

A site behind DataDome answered a headless browser with 200 and its article,
and DataDome drew its block page in a frame over it. The capture printed the
block page's text under the article's title and exited 0. P23-3 named a bot
check only from a 403's headers, and this response had none. The page is read
now, for signatures that are each a vendor naming itself: DataDome's frame
from its own host, HUMAN's `#px-captcha`, and Akamai's "Access Denied" title
with a reference in its format. The pages here are served as each check serves
its own, and DataDome's frame host is answered by a route, so no request leaves
the machine.

A news page that failed with `net::ERR_HTTP2_PROTOCOL_ERROR` opened in an
ordinary browser a minute later, and the message said only that it could not
be reached. It now says what that error usually is (P78-3).
"""

from __future__ import annotations

import asyncio
import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, BrowserContext, Playwright, Route
from playwright.async_api import Error as PlaywrightError

from conftest import serve
from webshot import pipeline
from webshot.capture.landing import bot_check_page
from webshot.capture.session import launch_context
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.errors import http2_refusal_hint

ARTICLE = "<h1>A long article</h1>" + "".join(
    f"<p>Paragraph {n} of the article, long enough to read.</p>" for n in range(40)
)

#: As DataDome's tag draws it over the page it refuses.
DATADOME_FRAME = (
    '<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=test&amp;t=bv" '
    'title="DataDome CAPTCHA" style="position:fixed;inset:0;width:100%;height:100%;'
    'border:0;z-index:2147483647"></iframe>'
)

#: path -> body, every one served with 200.
PAGES: dict[str, str] = {
    "/datadome": f"<title>An article</title>{ARTICLE}{DATADOME_FRAME}",
    # The frame arrives with the first scroll, as a check that watches for
    # rapid scrolling would draw it: only a test after the lazy-load pass sees
    # it.
    "/datadome-on-scroll": (
        f"<title>An article</title>{ARTICLE}<div style='height:4000px'></div>"
        "<script>addEventListener('scroll', () => document.body.insertAdjacentHTML("
        f"'beforeend', {json.dumps(DATADOME_FRAME)}), {{once: true}});</script>"
    ),
    "/perimeterx": (
        "<title>Access to this page has been denied</title>"
        "<h1>Before we continue...</h1>"
        "<p>Press &amp; Hold to confirm you are a human (and not a bot).</p>"
        '<div id="px-captcha"></div><p>Reference ID 00000000-0000-0000-0000-000000000000</p>'
    ),
    "/akamai": (
        "<title>Access Denied</title><h1>Access Denied</h1>"
        'You don\'t have permission to access "http&#58;&#47;&#47;site&#46;test&#47;a" '
        "on this server.<p>Reference #18.6f1d2c17.1792000000.1a2b3c4d</p>"
        "<p>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18&#46;6f1d2c17</p>"
    ),
    # Pages that name every signature and are not a bot check's page: an
    # article quoting the block page, linking to the vendor and carrying a
    # reference under another title, and one whose frame and challenge
    # element are present and not shown.
    "/quotes": (
        "<title>Access Denied: a history of bot checks</title>"
        f"{ARTICLE}<blockquote>We detected unusual activity from your device or "
        "network. Reasons may include: rapid taps or clicks.</blockquote>"
        "<p>Reference #18.6f1d2c17.1792000000.1a2b3c4d, as Akamai prints it.</p>"
        '<p><a href="https://geo.captcha-delivery.com/captcha/">The frame\'s host</a>, '
        "and the element id px-captcha.</p>"
    ),
    "/hidden": (
        f"<title>An article</title>{ARTICLE}"
        '<iframe src="https://geo.captcha-delivery.com/captcha/" style="display:none"></iframe>'
        '<div id="px-captcha" hidden></div>'
    ),
}


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class _Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = PAGES[self.path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Pages) as origin:
        yield origin


@pytest.fixture
def vendor_host(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer DataDome's host from inside the browser, and record each request.

    A route answers before the network does, so the frame loads a page of the
    shape DataDome's does and nothing reaches the public internet.
    """
    asked: list[str] = []

    async def answer(route: Route) -> None:
        asked.append(route.request.url)
        await route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body="<p>We detected unusual activity from your device or network.</p>",
        )

    async def launch(
        playwright: Playwright, options: CaptureOptions
    ) -> tuple[BrowserContext, Browser | None]:
        context, browser = await launch_context(playwright, options)
        await context.route("https://geo.captcha-delivery.com/**", answer)
        return context, browser

    monkeypatch.setattr(pipeline, "launch_context", launch)
    return asked


def _run(tmp_path: Path, source: str) -> tuple[int, dict[str, Any]]:
    report = tmp_path / "report.json"
    code = main(
        [
            source,
            "--output",
            str(tmp_path / "capture.pdf"),
            "--no-ocr",
            "--max-scrolls",
            "3",
            "--report",
            str(report),
        ]
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.browser
@pytest.mark.parametrize(
    ("path", "names"),
    [
        (
            "/datadome",
            "DataDome's bot protection (its challenge frame from geo.captcha-delivery.com)",
        ),
        (
            "/datadome-on-scroll",
            "DataDome's bot protection (its challenge frame from geo.captcha-delivery.com)",
        ),
        ("/perimeterx", "HUMAN's bot protection, formerly PerimeterX (#px-captcha)"),
        (
            "/akamai",
            'Akamai\'s "Access Denied" page (Reference #18.6f1d2c17.1792000000.1a2b3c4d)',
        ),
    ],
)
def test_a_bot_check_page_served_200_fails(
    tmp_path: Path, site: str, vendor_host: list[str], path: str, names: str
) -> None:
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 4 and report["exit_code"] == 4, report.get("error")
    error = str(report["error"])
    assert names in error
    assert "not a sign-in page" in error and "save it there" in error
    assert "--storage-state" not in error and "--auth-profile" not in error
    assert report["http_status"] == 200
    assert report["final_url"] == f"{site}{path}"
    assert not (tmp_path / "capture.pdf").exists()


@pytest.mark.browser
@pytest.mark.parametrize("path", ["/quotes", "/hidden"])
def test_a_page_that_names_the_signatures_is_captured(
    tmp_path: Path, site: str, vendor_host: list[str], path: str
) -> None:
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 0, report.get("error")
    assert (tmp_path / "capture.pdf").exists()
    assert not any("bot-check test" in warning for warning in report["warnings"])


@pytest.mark.browser
def test_the_vendor_frame_never_left_the_machine(
    tmp_path: Path, site: str, vendor_host: list[str]
) -> None:
    """The route answered the frame, so the failure was not of a missing frame."""
    code, _ = _run(tmp_path, f"{site}/datadome")
    assert code == 4
    assert vendor_host and all(
        url.startswith("https://geo.captcha-delivery.com/") for url in vendor_host
    )


class _Unreadable:
    """A page whose every read fails, as a document destroyed mid-read does."""

    url = "https://site.test/a"

    def __init__(self) -> None:
        self.reads = 0

    async def evaluate(self, script: str) -> object:
        self.reads += 1
        raise PlaywrightError("Execution context was destroyed")

    async def wait_for_load_state(self, state: str) -> None:
        return None


def test_a_page_that_cannot_be_read_is_not_a_pass() -> None:
    page = _Unreadable()
    found = asyncio.run(bot_check_page(page))
    assert found.check is None
    assert found.unread is not None
    assert "could not read the page (https://site.test/a)" in found.unread
    assert "Execution context was destroyed" in found.unread
    assert page.reads == 2


def test_only_the_http2_protocol_error_is_called_a_probable_refusal() -> None:
    assert "HTTP/2" in http2_refusal_hint(
        "Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://site.test/a"
    )
    # A connection that stopped answering its pings is a network that dropped.
    assert (
        http2_refusal_hint("Page.goto: net::ERR_HTTP2_PING_FAILED at https://a") == ""
    )
    assert (
        http2_refusal_hint("Page.goto: net::ERR_CONNECTION_REFUSED at https://a") == ""
    )


@pytest.mark.browser
def test_an_http2_reset_says_what_it_usually_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The navigation's own error, raised where Chromium raises it.

    Chromium speaks HTTP/2 only over TLS to a certificate it trusts, so a
    loopback server cannot make it reset a stream. The error is raised from
    `goto` instead, with Chromium's wording, and everything after it is the
    pipeline's own.
    """

    async def refuse(*args: object, **kwargs: object) -> None:
        raise PlaywrightError(
            "Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://site.test/a\n"
            'Call log:\n  - navigating to "https://site.test/a"'
        )

    async def launch(
        playwright: Playwright, options: CaptureOptions
    ) -> tuple[BrowserContext, Browser | None]:
        context, browser = await launch_context(playwright, options)
        page = context.pages[0] if context.pages else await context.new_page()
        monkeypatch.setattr(page, "goto", refuse)
        return context, browser

    monkeypatch.setattr(pipeline, "launch_context", launch)
    code, report = _run(tmp_path, "https://site.test/a")
    assert code == 3 and report["exit_code"] == 3
    error = str(report["error"])
    assert "could not be reached" in error and "ERR_HTTP2_PROTOCOL_ERROR" in error
    assert "broke off the connection at the HTTP/2 layer" in error
    assert "save it there and capture the saved file" in error
