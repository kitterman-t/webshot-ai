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
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from webshot.capture.prepare import (
    NOISE_SELECTORS,
    REMOVED_GROWTH_STEPS,
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


def _scroll(
    variant: str,
    *,
    max_scrolls: int = 100,
    noise: tuple[str, ...] = (),
    excluded: tuple[str, ...] = (),
) -> tuple[LazyLoadPass, int]:
    async def run() -> tuple[LazyLoadPass, int]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport=DEFAULT_VIEWPORT)
                await page.goto(f"{FIXTURE.as_uri()}#{variant}")
                result = await scroll_to_load(
                    page, max_scrolls, 0.25, noise=noise, excluded=excluded
                )
                return result, await page.evaluate(RELATED_COUNT_JS)
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
@pytest.mark.parametrize("variant", ["light", "shadow"])
def test_growth_only_where_clean_mode_removes_ends_the_pass(variant: str) -> None:
    """And not before the kept list has finished, in the light tree or a shadow root."""
    result, related = _scroll(variant, noise=tuple(NOISE_SELECTORS))
    assert result.ended == "removed-growth", result
    assert result.growing == (("section#comments", "clean"),)
    assert result.steps < 60, "the pass did not stop short of the limit"
    assert result.steady_steps >= REMOVED_GROWTH_STEPS
    assert related == RELATED, "the pass stopped before the kept list was complete"


@pytest.mark.browser
def test_growth_where_exclude_removes_ends_the_pass_too() -> None:
    result, related = _scroll("light", excluded=("#comments",))
    assert result.ended == "removed-growth", result
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
    result, _ = _scroll("light", max_scrolls=12)
    assert result.ended == "limit"
    assert result.steps == 12
    assert lazy_load_warning(result, 12) == (
        "Lazy-load scrolling reached the 12-step safety limit."
    )


@pytest.mark.browser
def test_a_removed_region_that_does_not_grow_leaves_the_original_rule() -> None:
    """Nothing grew, so the pass ends at the bottom as it always did."""
    result, related = _scroll("still", noise=tuple(NOISE_SELECTORS))
    assert result.ended == "bottom", result
    assert related == RELATED
    assert lazy_load_warning(result, 100) is None


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
