"""A feed that never ends, in a part of the page the capture removes (docs/09 P23-8).

A video watch page's comment feed appends more comments each time the window
nears its end, so the page never has a bottom, and the lazy-load pass ran to
`--max-scrolls` on every capture. Clean mode then removed the feed: by step
100 it held 89% of the page's elements, and the renderer carried them through
every later stage, peaking at 2.5 GB, and crashed in some runs. The pass now
stops once the content the capture keeps has stood still for a while with the
window past its end, when the only growth is inside what the capture removes,
and the manifest says so.

`tests/fixtures/endless_feed.html` lays the page out as the watch page is: the
log and its endless comments in one column, and in the other a list of
related logs that loads three more batches, each after a 600 ms "fetch".

A scroll reaches the page only when it renders, and on a loaded machine a fresh
renderer's first frame came seconds after `load` (docs/09 P34). The pass
counted steps the page could not yet have answered, saw nothing grow, and
ended "at the bottom" of a page that had not seen a scroll. `#late-frame`
holds the first frame back until the test lets it go.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Browser, async_playwright

from webshot.capture import prepare
from webshot.capture.prepare import (
    NOISE_SELECTORS,
    REMOVED_GROWTH_STEPS,
    FrameStall,
    LazyLoadPass,
    lazy_load_warning,
    scroll_to_load,
)
from webshot.cli import main
from webshot.config import DEFAULT_VIEWPORT

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "endless_feed.html"
#: The related list's full length: twenty, then three batches of twenty.
RELATED = 80

RELATED_COUNT_JS = """() => {
    const host = document.querySelector('station-related');
    const root = host ? host.shadowRoot : document;
    return root.querySelectorAll('#related li').length;
}"""


async def _processes(browser: Browser) -> list[dict[str, Any]]:
    """The browser's processes, each with its type, id and CPU time so far."""
    session = await browser.new_browser_cdp_session()
    try:
        info = await session.send("SystemInfo.getProcessInfo")
    finally:
        await session.detach()
    return list(info["processInfo"])


async def _processes_listed(browser: Browser) -> str:
    """The browser's processes, for a failing assertion to print.

    A pass that ends `unrendered` cannot see why the browser drew nothing; the
    processes can. A GPU process that is gone, idle or busy over the pass is a
    different failure each time (docs/09 P76), so the helpers below list them
    before and after it.
    """
    try:
        processes = await _processes(browser)
    except Exception as exc:
        # A diagnostic must not replace the failure it is there to explain.
        return f"unavailable ({exc!r})"
    return ", ".join(f"{p['type']} {p['id']} {p['cpuTime']:.2f}s" for p in processes)


def _scroll(
    variant: str,
    *,
    max_scrolls: int = 100,
    noise: tuple[str, ...] = (),
    excluded: tuple[str, ...] = (),
) -> tuple[LazyLoadPass, int, str]:
    """The pass over `variant`, the related logs it left, and the browser's processes."""

    async def run() -> tuple[LazyLoadPass, int, str]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport=DEFAULT_VIEWPORT)
                await page.goto(f"{FIXTURE.as_uri()}#{variant}")
                before = await _processes_listed(browser)
                result = await scroll_to_load(
                    page, max_scrolls, 0.25, noise=noise, excluded=excluded
                )
                after = await _processes_listed(browser)
                seen = f"processes before the pass: {before}; after: {after}"
                return result, await page.evaluate(RELATED_COUNT_JS), seen
            finally:
                await browser.close()

    return asyncio.run(run())


#: Where `_scroll_late_frame` serves the fixture from, so that it can hold
#: `late-frame.js` back. A `.test` name never resolves, and the route answers
#: every request to it, so nothing leaves the machine.
ORIGIN = "http://endless-feed.test"


#: Longer than any pass here takes. A pass that hangs fails the test rather
#: than stalling the run: absence of a result is not a result.
PASS_DEADLINE_S = 60


def _scroll_late_frame(
    hold_s: float | None, variant: str = "late-frame"
) -> tuple[LazyLoadPass, int, str]:
    """`#late-frame`, opened as the capture opens a page, and scrolled under `--exclude`.

    The page's first frame waits `hold_s` seconds, or for the pass to end when
    `hold_s` is None.
    """

    async def run() -> tuple[LazyLoadPass, int, str]:
        released = asyncio.Event()

        async def serve(route: Any) -> None:
            path = urlsplit(route.request.url).path
            if path == "/endless_feed.html":
                await route.fulfill(body=FIXTURE.read_bytes(), content_type="text/html")
            elif path == "/late-frame.js":
                if hold_s is None:
                    await released.wait()
                else:
                    await asyncio.sleep(hold_s)
                await route.fulfill(body="", content_type="text/javascript")
            else:
                await route.fulfill(status=404)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport=DEFAULT_VIEWPORT)
                await page.route(f"{ORIGIN}/**", serve)
                # `domcontentloaded`, as the capture navigates: `load` would
                # wait for the held script.
                await page.goto(
                    f"{ORIGIN}/endless_feed.html#{variant}",
                    wait_until="domcontentloaded",
                )
                before = await _processes_listed(browser)
                result = await asyncio.wait_for(
                    scroll_to_load(page, 100, 0.25, excluded=("#comments",)),
                    PASS_DEADLINE_S,
                )
                after = await _processes_listed(browser)
                seen = f"processes before the pass: {before}; after: {after}"
                released.set()
                # Rendered, so the held script has been served before closing.
                await page.evaluate("new Promise(r => requestAnimationFrame(r))")
                return result, await page.evaluate(RELATED_COUNT_JS), seen
            finally:
                released.set()
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
@pytest.mark.parametrize("variant", ["light", "shadow"])
def test_growth_only_where_clean_mode_removes_ends_the_pass(variant: str) -> None:
    """And not before the kept list has finished, in the light tree or a shadow root."""
    result, related, seen = _scroll(variant, noise=tuple(NOISE_SELECTORS))
    assert result.ended == "removed-growth", (result, seen)
    assert result.growing == (("section#comments", "clean"),)
    assert result.steps < 60, "the pass did not stop short of the limit"
    assert result.steady_steps >= REMOVED_GROWTH_STEPS
    assert related == RELATED, "the pass stopped before the kept list was complete"


@pytest.mark.browser
def test_growth_where_exclude_removes_ends_the_pass_too() -> None:
    result, related, seen = _scroll("light", excluded=("#comments",))
    assert result.ended == "removed-growth", (result, seen)
    assert result.growing == (("section#comments", "exclude"),)
    assert related == RELATED
    message = lazy_load_warning(result, 100)
    assert message is not None
    assert "section#comments, which --exclude removes" in message
    # Faithful mode would not bring an excluded region back.
    assert "--mode faithful" not in message


@pytest.mark.browser
def test_a_capture_that_keeps_the_feed_scrolls_to_the_limit() -> None:
    """Faithful mode removes nothing, so the feed is content and the limit holds."""
    result, _, seen = _scroll("light", max_scrolls=12)
    assert result.ended == "limit", (result, seen)
    assert result.steps == 12
    assert lazy_load_warning(result, 12) == (
        "Lazy-load scrolling reached the 12-step safety limit."
    )


@pytest.mark.browser
def test_a_removed_region_that_does_not_grow_leaves_the_original_rule() -> None:
    """Nothing grew, so the pass ends at the bottom as it always did."""
    result, related, seen = _scroll("still", noise=tuple(NOISE_SELECTORS))
    assert result.ended == "bottom", (result, seen)
    assert related == RELATED
    assert lazy_load_warning(result, 100) is None


@pytest.mark.browser
@pytest.mark.parametrize("variant", ["late-frame", "late-frame-smooth"])
def test_the_pass_waits_for_a_page_that_has_not_rendered_yet(variant: str) -> None:
    """Held 2 s, the first frame came after the pass used to end (docs/09 P34).

    The pass ended at its fifth step, 1.1 s in, "at the bottom" of the page as
    it loaded: 1,968 px, nothing grown, twenty related logs of eighty.
    """
    result, related, seen = _scroll_late_frame(2.0, variant)
    assert result.ended == "removed-growth", (result, seen)
    assert result.growing == (("section#comments", "exclude"),)
    assert related == RELATED


@pytest.mark.browser
@pytest.mark.parametrize("variant", ["late-frame", "late-frame-smooth"])
def test_a_page_that_does_not_render_ends_the_pass_and_says_so(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """Smoothly scrolled, too: a scroll the page never finished hung the pass."""
    monkeypatch.setattr(prepare, "FRAME_TIMEOUT_S", 1.5)
    result, related, seen = _scroll_late_frame(None, variant)
    assert result.ended == "unrendered", (result, seen)
    assert result.steps == 1
    assert related == 20, "nothing loaded: the page never saw the scroll"
    # Held back from its first frame, and running: the page can say both.
    assert result.stall is not None, result
    assert result.stall.visibility == "visible", result
    assert not result.stall.painted, result
    message = lazy_load_warning(result, 100)
    assert message is not None
    assert "did not render within 1.5 seconds of a scroll" in message


@pytest.mark.browser
@pytest.mark.skipif(
    not hasattr(signal, "SIGSTOP"), reason="stops a process, which needs SIGSTOP"
)
def test_a_browser_that_stops_drawing_is_told_apart_from_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GPU process draws nothing while the page runs on (docs/09 P76).

    A pass in a full verify run on a loaded Mac ended `unrendered` at its first
    step. Stopping the browser's GPU process, which draws every frame, ends it
    that way, with the page visible, drawn before and its timers on time;
    stopping the whole browser does not, because the frame comes first when
    it resumes. The page cannot see the GPU process, but it can rule itself
    out, and the warning says so.
    """
    monkeypatch.setattr(prepare, "FRAME_TIMEOUT_S", 1.5)

    async def run() -> tuple[LazyLoadPass, int, list[dict[str, Any]]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            stopped: list[int] = []
            try:
                page = await browser.new_page(viewport=DEFAULT_VIEWPORT)
                await page.goto(f"{FIXTURE.as_uri()}#light")
                # Drawn once, so a first paint is on record. Polled on a timer:
                # polling by frame would ask for the frames this test is about.
                await page.wait_for_function(
                    "performance.getEntriesByType('paint').length > 0", polling=50
                )
                processes = await _processes(browser)
                gpu = [p["id"] for p in processes if p["type"] == "GPU"]
                assert gpu, f"no GPU process to stop: {processes}"
                for pid in gpu:
                    os.kill(pid, signal.SIGSTOP)
                    stopped.append(pid)
                # A frame already on its way still reaches the page.
                await asyncio.sleep(0.5)
                result = await asyncio.wait_for(
                    scroll_to_load(page, 100, 0.25, excluded=("#comments",)),
                    PASS_DEADLINE_S,
                )
                return result, await page.evaluate(RELATED_COUNT_JS), processes
            finally:
                for pid in stopped:
                    os.kill(pid, signal.SIGCONT)
                await browser.close()

    result, related, processes = asyncio.run(run())
    assert result.ended == "unrendered", (result, processes)
    assert result.steps == 1
    assert related == 20, "nothing loaded: the page never saw the scroll"
    assert result.stall is not None, result
    assert result.stall.visibility == "visible", result
    assert result.stall.painted, result


UNRENDERED = (
    "The lazy-load pass stopped after 1 step(s): the page did not render "
    "within 30 seconds of a scroll, and a page sees a scroll only when it "
    "renders, so content it loads as it is scrolled may not have been loaded."
)


def test_the_unrendered_warning_says_what_was_not_seen() -> None:
    result = LazyLoadPass(steps=1, ended="unrendered", height=1968)
    assert lazy_load_warning(result, 100) == UNRENDERED


@pytest.mark.parametrize(
    ("stall", "seen"),
    [
        pytest.param(
            FrameStall(visibility="visible", painted=True, late_s=0.004),
            "The page had been drawn before, was visible, and its own timers ran "
            "on time: the page was running, and the browser drew no frame for it. "
            "Capturing again may get further.",
            id="browser-stopped-drawing",
        ),
        pytest.param(
            FrameStall(visibility="visible", painted=False, late_s=0.0),
            "The page was visible and its own timers ran on time, but it had "
            "never been drawn: something held back its first frame, such as a "
            "render-blocking resource that had not arrived, or a browser that "
            "had not yet drawn one.",
            id="never-drawn",
        ),
        pytest.param(
            FrameStall(visibility="visible", painted=True, late_s=12.34),
            "The page's own timer fired 12.3 seconds late, so for part of the "
            "wait the page could not run anything: its main thread was busy, or "
            "the machine did not run it.",
            id="page-not-running",
        ),
        pytest.param(
            FrameStall(visibility="hidden", painted=True, late_s=12.34),
            'The page was hidden (its visibilityState was "hidden"), and a '
            "browser does not render a hidden page.",
            id="hidden",
        ),
    ],
)
def test_the_unrendered_warning_says_what_the_page_saw(
    stall: FrameStall, seen: str
) -> None:
    """Which side stopped, from what the page could see (docs/09 P76).

    The warning used to say only that the page did not render, and a GPU
    process that stopped drawing, with the page running on, ended the pass
    the same way.
    """
    result = LazyLoadPass(steps=1, ended="unrendered", height=1968, stall=stall)
    assert lazy_load_warning(result, 100) == f"{UNRENDERED} {seen}"


def test_the_warning_names_the_region_and_what_removes_it() -> None:
    result = LazyLoadPass(
        steps=34,
        ended="removed-growth",
        height=26407,
        growing=(("app-comments#comments", "clean"),),
        steady_steps=7,
    )
    assert lazy_load_warning(result, 100) == (
        "The lazy-load pass stopped after 34 step(s) with the page still "
        "growing: it grew only inside app-comments#comments, which clean mode "
        "removes as page chrome, and the content the capture keeps did not "
        "change over the last 7 steps. Whatever more the page would have "
        "added there was not loaded. --mode faithful keeps that part of the "
        "page and scrolls on, up to --max-scrolls."
    )


def _capture(tmp_path: Path, *extra: str) -> tuple[int, dict[str, Any], str]:
    report = tmp_path / "report.json"
    code = main(
        [
            FIXTURE.as_uri(),
            "--output",
            str(tmp_path / "log.pdf"),
            "--no-ocr",
            "--report",
            str(report),
            *extra,
        ]
    )
    content = (tmp_path / "log.ai" / "content.md").read_text(encoding="utf-8")
    return code, json.loads(report.read_text(encoding="utf-8")), content


@pytest.mark.browser
def test_a_clean_capture_says_why_the_pass_stopped(tmp_path: Path) -> None:
    code, report, content = _capture(tmp_path)
    assert code == 0
    stopped = [w for w in report["warnings"] if w.startswith("The lazy-load pass")]
    assert len(stopped) == 1, report["warnings"]
    assert "inside section#comments, which clean mode removes" in stopped[0]
    assert not any("safety limit" in w for w in report["warnings"])
    manifest = json.loads(
        (tmp_path / "log.ai" / "manifest.json").read_text(encoding="utf-8")
    )
    assert stopped[0] in manifest["warnings"], "the manifest says so too"
    assert f"Related log {RELATED}" in content
    assert "Comment 1:" not in content, "clean mode still removes the feed"
