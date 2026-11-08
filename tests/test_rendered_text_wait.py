"""A page that shows no text yet is given time for its script (docs/09 P113-5).

A library catalogue's record page, a single-page app, showed no text for 4.5 s
after DOMContentLoaded and its record 5 s after it. WebShot captured one
second after DOMContentLoaded and printed a blank page; the empty-capture
warning was the only sign. tests/fixtures/landing/late_render.html renders its
record 2.5 s after it loads; empty_shell.html never renders, and gets the
bounded wait and a warning that says it was given.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from pypdf import PdfReader

from webshot.capture import prepare
from webshot.capture.landing import empty_capture_message
from webshot.capture.prepare import TextWait, text_wait_bound, wait_for_rendered_text
from webshot.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "landing"


class _Page:
    """Answers `RENDERED_TEXT_JS` from a script of readings."""

    def __init__(self, readings: list[dict[str, int] | Exception]) -> None:
        self.readings = readings
        self.calls = 0

    async def evaluate(self, script: str) -> dict[str, int]:
        assert script == prepare.RENDERED_TEXT_JS
        reading = self.readings[min(self.calls, len(self.readings) - 1)]
        self.calls += 1
        if isinstance(reading, Exception):
            raise reading
        return reading


def _wait(page: _Page, **limits: float) -> TextWait | None:
    return asyncio.run(wait_for_rendered_text(page, **limits))  # type: ignore[arg-type]


def test_a_page_with_text_or_without_a_script_is_not_waited_for() -> None:
    page = _Page([{"text": 12, "nodes": 40, "scripts": 3}])
    assert _wait(page) is None and page.calls == 1
    static = _Page([{"text": 0, "nodes": 5, "scripts": 0}])
    assert _wait(static) is None and static.calls == 1


def test_text_that_comes_is_waited_for_until_it_stands_still() -> None:
    empty = {"text": 0, "nodes": 60, "scripts": 4}
    header = {"text": 212, "nodes": 430, "scripts": 4}
    record = {"text": 1126, "nodes": 1093, "scripts": 4}
    page = _Page([empty, empty, empty, header, record])
    wait = _wait(page, limit_s=10.0, settle_s=0.6)
    assert wait is not None and wait.characters == 1126
    assert not wait.ran_out and text_wait_bound(wait) is None
    # Three empty polls, the header, the record, then 0.6 s of the record
    # standing still: well under the limit.
    assert 1.5 <= wait.waited_s < 5


def test_text_that_never_comes_runs_out_and_says_none_came() -> None:
    page = _Page([{"text": 0, "nodes": 6, "scripts": 1}])
    wait = _wait(page, limit_s=0.6)
    assert wait is not None and wait.characters == 0
    assert 0.5 <= wait.waited_s < 2
    # The warning says the bound it was given, not the time measured.
    assert wait.ran_out and text_wait_bound(wait) == 0.6


def test_a_page_that_navigates_under_the_wait_stops_it() -> None:
    page = _Page([{"text": 0, "nodes": 6, "scripts": 1}, PlaywrightError("gone")])
    wait = _wait(page, limit_s=10.0)
    assert wait is not None and wait.characters == 0 and wait.waited_s < 2
    # Review of P113-5: zero characters here is not a wait that ran out, so
    # the empty-capture warning must not say the script was given 10 s.
    assert not wait.ran_out and text_wait_bound(wait) is None
    assert text_wait_bound(None) is None


def test_the_empty_warning_says_how_long_the_script_was_given() -> None:
    message = empty_capture_message(
        page_text_characters=0, visuals=0, videos=0, required=False, waited_s=10.0
    )
    assert message is not None
    assert (
        "and there is no image and no video. WebShot gave its script 10 s more to "
        "render text after the page loaded. A page shows this" in message
    )
    unwaited = empty_capture_message(
        page_text_characters=0, visuals=0, videos=0, required=False
    )
    assert unwaited is not None and "WebShot gave its script" not in unwaited


def _capture(tmp_path: Path, fixture: str) -> tuple[str, dict[str, Any]]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(FIXTURES / fixture), "--output", str(pdf), "--no-ocr"]) == 0
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    manifest = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text(encoding="utf-8")
    )
    return text, manifest


@pytest.mark.browser
def test_a_record_rendered_late_is_captured(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="webshot"):
        text, manifest = _capture(tmp_path, "late_render.html")
    assert "Lighthouse keepers of the north coast" in text
    assert "with the years each one served." in text
    assert not [w for w in manifest["warnings"] if "The capture is" in w]
    assert any(
        "The page showed no text when it had loaded; its script rendered" in r.message
        for r in caplog.records
    )


@pytest.mark.browser
def test_a_shell_that_never_renders_says_the_wait_ran_out(tmp_path: Path) -> None:
    _, manifest = _capture(tmp_path, "empty_shell.html")
    [empty] = [w for w in manifest["warnings"] if w.startswith("The capture is empty")]
    assert "WebShot gave its script 10 s more to render text" in empty
