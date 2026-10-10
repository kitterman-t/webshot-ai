"""A page that sends the browser to its own site's home page (docs/09 P23-5).

A chat assistant's shared page answered a signed-out browser with 200 and
then, from its script, replaced its address with the site's root. The capture
was of the home page and exited 0, with only the generic "nearly empty"
warning. The sign-in wall rule (P22-1) covers a move to another origin; this
one is the same origin with the whole path dropped. It is a warning, not a
failure: the home page is a page, and someone may have meant it.
"""

from __future__ import annotations

import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import serve
from webshot.capture.landing import home_page_message, landed_on_home_page
from webshot.cli import main

HOME = (
    "<!doctype html><title>Workspace</title><h1>Welcome</h1>"
    "<p>Choose a workspace from the list to open its notes and files.</p>"
)
#: path -> (status, extra headers, body)
ROUTES: dict[str, tuple[int, dict[str, str], str]] = {
    "/": (200, {}, HOME),
    "/index.html": (200, {}, HOME),
    "/w/item-7": (
        200,
        {},
        "<!doctype html><title>Loading</title><script>location.replace('/')</script>",
    ),
    "/old/report": (302, {"Location": "/"}, ""),
    "/w/item-8": (
        200,
        {},
        "<!doctype html><title>Item</title><h1>Item eight</h1>"
        "<p>The notes for this item are long enough to count as a page.</p>",
    ),
}


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class _Site(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = ROUTES[self.path]
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Site) as origin:
        yield origin


def _run(tmp_path: Path, source: str) -> tuple[int, dict[str, Any]]:
    report = tmp_path / "report.json"
    code = main(
        [
            source,
            "--output",
            str(tmp_path / "capture.pdf"),
            "--no-ocr",
            "--report",
            str(report),
        ]
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("requested", "landed", "home"),
    [
        ("https://site.test/w/item-7", "https://site.test/", True),
        ("https://site.test/w/item-7", "https://site.test", True),
        ("https://site.test/w/item-7", "https://site.test/?next=/w/item-7", True),
        # Asked for the home page, or for its index file: nothing moved.
        ("https://site.test/", "https://site.test/", False),
        ("https://site.test/index.html", "https://site.test/", False),
        ("https://site.test/en/Default.aspx", "https://site.test/en/", False),
        # Another page, or another origin (that is P22-1's rule).
        ("https://site.test/w/item-7", "https://site.test/w/", False),
        ("https://site.test/w/item-7", "https://login.site.test/", False),
        ("http://site.test/w/item-7", "https://site.test/", False),
        ("file:///tmp/page.html", "file:///", False),
    ],
)
def test_the_rule(requested: str, landed: str, home: bool) -> None:
    assert landed_on_home_page(requested, landed) is home


@pytest.mark.browser
@pytest.mark.parametrize("path", ["/w/item-7", "/old/report"])
def test_a_page_that_sends_the_browser_home_is_a_warning(
    tmp_path: Path, site: str, path: str
) -> None:
    """By its script after loading, or by a redirect: the capture says so."""
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 0
    assert report["final_url"] == f"{site}/"
    assert home_page_message(f"{site}{path}", f"{site}/") in report["warnings"]


@pytest.mark.browser
@pytest.mark.parametrize("path", ["/", "/index.html", "/w/item-8"])
def test_a_page_that_stays_carries_no_warning(
    tmp_path: Path, site: str, path: str
) -> None:
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 0
    assert report["warnings"] == []
