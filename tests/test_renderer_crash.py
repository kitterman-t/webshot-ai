"""A page's renderer that crashes mid-capture (docs/09 P23-8).

On a long video watch page the renderer died during the visual harvest, at
2.5 GB, and the capture ended with `ERROR: Error: Page.evaluate: Target
crashed`, exit 5. The harvest read every visual after the crash as a picture
the page had removed, so the crash surfaced only at the snapshot's
`Page.evaluate`, which named neither the step nor anything to try. Here the
renderer is crashed for real (DevTools' `Page.crash`) at a chosen step.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, async_playwright

import webshot.pipeline as pipeline
from webshot.capture.prepare import LazyLoadPass
from webshot.capture.session import CrashWatch
from webshot.capture.visuals import capture_visual_assets
from webshot.cli import main
from webshot.ocr.tesseract import TesseractEngine
from webshot.pipeline import renderer_crash_message

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hidden_and_tiny_visuals.html"


async def _crash(page: Page) -> None:
    """Crash the page's renderer and wait until Playwright has seen it."""
    session = await page.context.new_cdp_session(page)
    # The command never answers: the renderer that would answer it is gone.
    sent = asyncio.ensure_future(session.send("Page.crash"))
    await page.wait_for_event("crash")
    sent.cancel()
    with contextlib.suppress(asyncio.CancelledError, PlaywrightError):
        await sent


def _crash_after_first_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    screenshot = Locator.screenshot

    async def then_crash(self: Locator, **kwargs: Any) -> bytes:
        taken = await screenshot(self, **kwargs)
        if await self.get_attribute("aria-label") == "First chart":
            await _crash(self.page)
        return taken

    monkeypatch.setattr(Locator, "screenshot", then_crash)


@pytest.mark.browser
def test_the_harvest_does_not_read_a_crash_as_removed_pictures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _crash_after_first_chart(monkeypatch)

    async def run() -> object:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                return await capture_visual_assets(
                    page,
                    tmp_path / "assets",
                    max_assets=50,
                    ocr=False,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=TesseractEngine(),
                )
            finally:
                await browser.close()

    # Before, this returned the first chart and a warning per later chart:
    # "Visual element 67 was removed by the page while it was being captured".
    with pytest.raises(PlaywrightError, match="Target crashed"):
        asyncio.run(run())


def _capture(tmp_path: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    report = tmp_path / "report.json"
    code = main(
        [
            FIXTURE.as_uri(),
            "--output",
            str(tmp_path / "page.pdf"),
            "--no-ocr",
            "--report",
            str(report),
            *extra,
        ]
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.browser
def test_a_crash_in_the_harvest_says_where_and_what_to_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _crash_after_first_chart(monkeypatch)
    code, report = _capture(tmp_path)
    assert code == 5
    error = report["error"]
    assert error.startswith(
        "The browser's process for this page crashed while building the AI "
        "bundle, so nothing was captured."
    ), error
    assert "Here the lazy-load pass had scrolled" in error
    assert (
        "a lower --max-scrolls (this run allowed 100), --no-scroll, or a --selector"
        in error
    )
    assert "Target crashed" not in error
    assert not (tmp_path / "page.pdf").exists()
    assert not (tmp_path / "page.ai").exists()


@pytest.mark.browser
@pytest.mark.parametrize(
    ("extra", "advice"),
    [
        (
            (),
            "a lower --max-scrolls (this run allowed 100), --no-scroll, or a --selector",
        ),
        (("--no-scroll",), "A smaller capture usually completes: a --selector"),
    ],
)
def test_a_crash_names_the_step_it_happened_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: tuple[str, ...],
    advice: str,
) -> None:
    """The step the crash arrived in, though a later call is the one that fails."""
    wait: Callable[..., Awaitable[list[str]]] = pipeline._wait_for_fonts_and_images

    async def crash_then_wait(page: Page, *args: Any, **kwargs: Any) -> list[str]:
        await _crash(page)
        return await wait(page, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_wait_for_fonts_and_images", crash_then_wait)
    code, report = _capture(tmp_path, *extra)
    assert code == 5
    assert "crashed while preparing the page for print" in report["error"]
    assert advice in report["error"]


@pytest.mark.browser
def test_a_crash_found_only_by_the_print_is_not_a_print_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step that turns failures into warnings hides a crash until `page.pdf()`.

    `render_pdf` reports any failure of the print as exit 7, "Chromium could not
    print the page", which is the wrong step and the wrong code for a renderer
    that died while the videos were being read.
    """

    async def crash_while_reading_videos(page: Page, **_: Any) -> Any:
        await _crash(page)
        return pipeline.VideoEnrichment()

    monkeypatch.setattr(pipeline, "enrich_with_videos", crash_while_reading_videos)
    code, report = _capture(tmp_path)
    assert code == 5
    assert "crashed while reading the page's videos" in report["error"]
    assert "could not print" not in report["error"]


@pytest.mark.browser
def test_a_crash_while_printing_is_still_a_render_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 7, as docs/09 P8-59 decided, with the message that says what to try."""
    render = pipeline.render_pdf

    async def crash_then_print(page: Page, *args: Any, **kwargs: Any) -> None:
        await _crash(page)
        await render(page, *args, **kwargs)

    monkeypatch.setattr(pipeline, "render_pdf", crash_then_print)
    code, report = _capture(tmp_path)
    assert code == 7
    assert "crashed while rendering the PDF" in report["error"]


def test_the_message_says_what_the_lazy_load_pass_left() -> None:
    lazy = LazyLoadPass(steps=100, ended="limit", height=63920)
    assert renderer_crash_message("building the AI bundle", lazy, 100) == (
        "The browser's process for this page crashed while building the AI "
        "bundle, so nothing was captured. WebShot cannot see why; on a long "
        "page the usual cause is memory, which grows with everything the page "
        "has loaded. Here the lazy-load pass had scrolled 100 step(s) and left "
        "the page 63,920 px tall. A smaller capture usually completes: a lower "
        "--max-scrolls (this run allowed 100), --no-scroll, or a --selector "
        "that keeps only the content."
    )


class _Page:
    """Enough of a page for `CrashWatch`: listeners, and nothing else."""

    def __init__(self) -> None:
        self.listeners: list[Callable[[Any], None]] = []

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        assert event == "crash"
        self.listeners.append(handler)

    def remove_listener(self, event: str, handler: Callable[[Any], None]) -> None:
        self.listeners.remove(handler)


def test_only_a_crash_is_read_as_one() -> None:
    page = _Page()
    watch = CrashWatch(page)  # type: ignore[arg-type]
    assert not watch.caused(PlaywrightError("Locator.click: Timeout 5000ms exceeded"))
    watch.step = "rendering the PDF"
    for listener in page.listeners:
        listener(page)
    watch.step = "checking the printed page for clipped text"
    assert watch.caused(PlaywrightError("Page.evaluate: anything at all"))
    assert watch.crashed_in == "rendering the PDF"
    watch.close()
    assert page.listeners == []
