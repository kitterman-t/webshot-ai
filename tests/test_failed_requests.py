"""Every failed request is counted, and the manifest says so (docs/09 P20-6).

The capture kept the first 20 distinct failed URLs and nothing else. The CLI
printed the length of that list as the total, "20 resource request(s) failed",
on a page that lost more, and the manifest said nothing, so a bundle read
without `--report` gave no sign that anything was missing.

`failed_resources.html` asks for 25 distinct images (one of them twice), two
stylesheets and a script, from a server that closes each of those connections
without answering: 28 distinct requests fail, more than the 20 the report lists.
"""

from __future__ import annotations

import asyncio
import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import serve
from webshot.cli import failed_requests_pointer
from webshot.config import CaptureOptions
from webshot.pipeline import (
    LISTED_FAILED_REQUESTS,
    RequestFailures,
    convert_url_to_pdf,
    failed_requests_warning,
)
from webshot.report import build_qa_report

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "failed_resources.html"
#: A fixed bar clean mode hides and prints the page again without, watched by
#: a script that asks for an image once the bar is hidden (docs/09 P123-6).
WATCHED = FIXTURE.with_name("covering_watched_bar_page.html")
SERVED = {f"/{page.name}": page for page in (FIXTURE, WATCHED)}

WARNING = (
    "28 requests for the page's resources failed (image 25, stylesheet 2, "
    "script 1), so the PDF and the bundle may lack what they would have loaded."
)


class _Origin(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/broken/"):
            # No status line at all: Chromium reports the request as failed,
            # which a 404 would not be.
            self.close_connection = True
            return
        if self.path not in SERVED:
            self.send_error(404)
            return
        body = SERVED[self.path].read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    directory = tmp_path_factory.mktemp("failed-requests")
    with serve(_Origin) as origin:
        options = CaptureOptions(
            source=f"{origin}/failed_resources.html",
            output=directory / "capture.pdf",
            ai_bundle_directory=directory / "capture.ai",
            ocr=False,
        )
        result = asyncio.run(convert_url_to_pdf(options))
        manifest = json.loads(
            (directory / "capture.ai" / "manifest.json").read_text("utf-8")
        )
        yield {"result": result, "options": options, "manifest": manifest}


@pytest.mark.browser
def test_every_distinct_failure_is_counted(captured: dict[str, Any]) -> None:
    result = captured["result"]
    assert result.failed_request_count == 28
    assert len(result.failed_requests) == LISTED_FAILED_REQUESTS
    assert len(set(result.failed_requests)) == LISTED_FAILED_REQUESTS


@pytest.mark.browser
def test_the_manifest_says_how_many_failed_and_of_what(
    captured: dict[str, Any],
) -> None:
    assert WARNING in captured["result"].warnings
    assert WARNING in captured["manifest"]["warnings"]


@pytest.mark.browser
def test_the_report_counts_them_all_and_lists_twenty(
    captured: dict[str, Any],
) -> None:
    report = build_qa_report(captured["result"], captured["options"]).model_dump(
        mode="json"
    )
    assert report["counts"]["failed_requests"] == 28
    assert len(report["failed_requests"]) == LISTED_FAILED_REQUESTS


@pytest.mark.browser
def test_a_request_that_fails_before_the_second_render_is_counted(
    tmp_path: Path,
) -> None:
    """Clean mode hid the bar and printed the page again: the second PDF is
    the one delivered, so what failed before it was printed is counted."""
    with serve(_Origin) as origin:
        result = asyncio.run(
            convert_url_to_pdf(
                CaptureOptions(
                    source=f"{origin}/{WATCHED.name}",
                    output=tmp_path / "capture.pdf",
                    ai_bundle_directory=tmp_path / "capture.ai",
                    ocr=False,
                )
            )
        )
    assert any(
        warning.startswith("Clean mode hid a fixed box") for warning in result.warnings
    ), result.warnings
    assert result.failed_requests == [f"{origin}/broken/notice-bar-hidden.png"]
    assert result.failed_request_count == 1
    assert (
        "1 request for the page's resources failed (image 1), so the PDF and the "
        "bundle may lack what it would have loaded."
    ) in result.warnings


def test_the_cli_says_the_report_lists_only_the_first_twenty() -> None:
    assert failed_requests_pointer(28, 20) == (
        "The capture report (--report) lists the first 20 of the 28 failed "
        "request URLs."
    )
    assert failed_requests_pointer(3, 3) == (
        "The capture report (--report) lists the failed request URLs."
    )


def _request(url: str, kind: str) -> SimpleNamespace:
    return SimpleNamespace(url=url, resource_type=kind)


def test_a_url_is_counted_once_and_nothing_after_close() -> None:
    failures = RequestFailures()
    failures.record(_request("https://a.example/x.png", "image"))
    failures.record(_request("https://a.example/x.png", "image"))
    failures.record(_request("https://a.example/y.css", "stylesheet"))
    assert failures.close() == (
        "2 requests for the page's resources failed (image 1, stylesheet 1), so "
        "the PDF and the bundle may lack what they would have loaded."
    )
    failures.record(_request("https://a.example/z.png", "image"))
    assert failures.count == 2
    assert failures.listed == ["https://a.example/x.png", "https://a.example/y.css"]


def test_nothing_is_counted_while_the_checks_print_the_page() -> None:
    """Their narrower window can fetch what the printed page never asked for;
    a render after them is counted again (docs/09 P123-6)."""
    failures = RequestFailures()
    with failures.paused():
        failures.record(_request("https://a.example/narrow.png", "image"))
    failures.record(_request("https://a.example/second.png", "image"))
    assert failures.count == 1
    assert failures.listed == ["https://a.example/second.png"]


def test_a_capture_with_no_failure_has_no_warning() -> None:
    assert RequestFailures().close() is None


def test_one_failure_is_counted_in_the_singular() -> None:
    assert failed_requests_warning(1, {"font": 1}) == (
        "1 request for the page's resources failed (font 1), so the PDF and the "
        "bundle may lack what it would have loaded."
    )
