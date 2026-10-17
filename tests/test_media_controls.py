"""A video's controls photographed mid-spinner (docs/09 P66).

Chromium's controls show a loading spinner on a `<video controls>` for about
1.4 s after the page loads, `preload="none"` or not, and `animations="disabled"`
does not reach it. A capture that photographed the video inside that window
recorded the spinner at whatever angle the clock gave it, which is how
`lms-module-videos` recorded a different asset digest from run to run on a fast
machine.

Here the race is certain rather than likely: the first capture runs with the
page's animation clock stopped (DevTools' `Animation.setPlaybackRate`), so the
spinners the page starts with never end on their own, however fast or slow the
machine. Its assets and its `content.html` must be those of a second capture
that settled nothing and instead waited, with the clock running, until no
spinner was left before building its bundle. Both read the spinners through
DevTools, the only way into the user-agent shadow root the controls live in.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, BrowserContext, Page, Playwright

from webshot import pipeline
from webshot.acquire.router import normalize_source
from webshot.bundle.build import create_ai_bundle
from webshot.capture.media_controls import settle_media_controls
from webshot.capture.session import launch_context
from webshot.config import CaptureOptions
from webshot.render.pdf import render_pdf

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "video_controls.html"
#: The file names of the videos whose controls the page shows.
CONTROLLED = ("document.mp4", "shadow.mp4", "framed.mp4")
#: Chromium's name for the element of the controls that holds the spinner.
LOADING_PANEL = "-internal-media-controls-loading-panel"
#: Far past the spinner's 1.4 s, so only a spinner that never ends reaches it.
NATURAL_END_TIMEOUT_S = 30.0


async def _spinning(page: Page) -> dict[str, bool]:
    """Whether each video's controls show the spinner, by the video's file name.

    A video whose controls have no spinner element is not listed, so a missing
    name means the probe could not see that video, not that it was still.
    """
    devtools = await page.context.new_cdp_session(page)
    try:
        document = await devtools.send("DOM.getDocument", {"depth": -1, "pierce": True})
    finally:
        await devtools.detach()
    shown: dict[str, bool] = {}

    def walk(node: dict[str, Any], video: str | None) -> None:
        listed = node.get("attributes", [])
        attributes = dict(zip(listed[::2], listed[1::2], strict=True))
        if node.get("localName") == "video":
            video = attributes.get("src", "").rsplit("/", 1)[-1]
        if video and attributes.get("pseudo") == LOADING_PANEL:
            shown[video] = "display: none" not in attributes.get("style", "")
        for child in node.get("children", []):
            walk(child, video)
        for root in node.get("shadowRoots", []):
            walk(root, video)
        if "contentDocument" in node:
            walk(node["contentDocument"], None)

    walk(document["root"], None)
    return shown


def _capture(directory: Path, *, raced: bool) -> dict[str, Any]:
    """Capture the fixture, reading the spinners as each stage that sees them begins."""
    seen: dict[str, Any] = {}

    async def launch(
        playwright: Playwright, options: CaptureOptions
    ) -> tuple[BrowserContext, Browser | None]:
        context, browser = await launch_context(playwright, options)
        if raced:
            # The pipeline opens the fixture in the context's first page, so
            # its clock is stopped before the page starts a spinner.
            page = await context.new_page()
            devtools = await context.new_cdp_session(page)
            await devtools.send("Animation.enable")
            await devtools.send("Animation.setPlaybackRate", {"playbackRate": 0})
        return context, browser

    async def settle(page: Page) -> int:
        seen["before"] = await _spinning(page)
        seen["settled"] = await settle_media_controls(page) if raced else 0
        return int(seen["settled"])

    async def bundle(page: Page, **kwargs: Any) -> Any:
        if not raced:
            # Here rather than in `settle`, so the reference stays one even
            # when the pipeline settles nothing.
            deadline = time.monotonic() + NATURAL_END_TIMEOUT_S
            while any((await _spinning(page)).values()) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
        seen["photographed"] = await _spinning(page)
        return await create_ai_bundle(page, **kwargs)

    async def render(page: Page, *args: Any, **kwargs: Any) -> None:
        seen["printed"] = await _spinning(page)
        await render_pdf(page, *args, **kwargs)

    options = CaptureOptions(
        source=normalize_source(str(FIXTURE)),
        output=directory / "capture.pdf",
        ai_bundle_directory=directory / "capture.ai",
        ocr=False,
        videos=False,
        # Straight to the prepare stage. The raced capture needs no head start
        # with its clock stopped, and the other waits as long as it must.
        wait_after_load_s=0,
        scroll=False,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pipeline, "launch_context", launch)
        patch.setattr(pipeline, "settle_media_controls", settle)
        patch.setattr(pipeline, "create_ai_bundle", bundle)
        patch.setattr(pipeline, "render_pdf", render)
        asyncio.run(pipeline.convert_url_to_pdf(options))
    bundle = directory / "capture.ai"
    seen["assets"] = [
        (asset["id"], asset["kind"], asset["sha256"])
        for asset in json.loads((bundle / "assets.json").read_text("utf-8"))[
            "visual_assets"
        ]
    ]
    seen["content"] = (bundle / "content.html").read_text("utf-8")
    return seen


@pytest.fixture(scope="module")
def raced(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _capture(tmp_path_factory.mktemp("raced"), raced=True)


@pytest.fixture(scope="module")
def waited(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _capture(tmp_path_factory.mktemp("waited"), raced=False)


@pytest.mark.browser
def test_every_spinner_was_showing_and_none_was_left_for_the_print(
    raced: dict[str, Any],
) -> None:
    assert "before" in raced, "the pipeline never settled the video controls"
    # Without this the test could pass on a browser that shows no spinner,
    # having proved nothing.
    assert {name: raced["before"].get(name) for name in CONTROLLED} == dict.fromkeys(
        CONTROLLED, True
    ), raced["before"]
    assert not raced["before"].get("no-controls.mp4"), raced["before"]
    assert raced["settled"] == len(CONTROLLED)
    for stage in ("photographed", "printed"):
        assert set(CONTROLLED) <= set(raced[stage]), (stage, raced[stage])
        assert not any(raced[stage].values()), (stage, raced[stage])


@pytest.mark.browser
def test_the_assets_are_the_ones_a_capture_gets_once_the_spinners_end(
    raced: dict[str, Any], waited: dict[str, Any]
) -> None:
    assert set(CONTROLLED) <= set(waited["photographed"]), waited["photographed"]
    assert not any(waited["photographed"].values()), (
        f"the spinners did not end on their own in {NATURAL_END_TIMEOUT_S} s: "
        f"{waited['photographed']}"
    )
    kinds = [kind for _, kind, _ in waited["assets"]]
    assert kinds.count("video") == 4, waited["assets"]
    assert raced["assets"] == waited["assets"], (
        f"stopped clock, settled: {raced['assets']}\n"
        f"clock running, waited: {waited['assets']}"
    )


@pytest.mark.browser
def test_settling_leaves_the_snapshot_as_it_was(
    raced: dict[str, Any], waited: dict[str, Any]
) -> None:
    # Removing and restoring `controls` would also end the spinner, and would
    # move the attribute to the end of the element's list, which
    # `content.html` writes in order.
    assert raced["content"] == waited["content"]
