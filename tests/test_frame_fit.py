"""A frame is grown to its document for print, or named (docs/09 P16-16).

A frame prints its box. The snapshot reads a frame the page can script whole,
so the bundle held text the PDF did not, and nothing said so. Each frame in
`scrolling_frames_page.html` has a first sentence its box shows and a last one
it does not; the PDF's text layer says which frames were grown.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render import frames as frames_module
from webshot.render.frames import FrameFit, fit_frames

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "scrolling_frames_page.html"

#: Grown: the last sentence prints. The inner frame grows first, then the
#: outer one around it.
GROWN = ("tall", "outer", "inner", "shadow")
#: Not grown: one would cover what follows it, and two cannot be scrolled, one
#: by its `scrolling` attribute and one by its own document's overflow.
KEPT = ("capped", "unscrollable", "hidden-overflow")


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def capture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("frame-fit")
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main(
        [str(FIXTURE), "--output", str(output), "--no-ocr", "--report", str(report)]
    )
    assert code == 0
    bundle = output.with_suffix(".ai")
    return {
        "pdf": _flat(
            "\n".join(p.extract_text() or "" for p in PdfReader(output).pages)
        ),
        "markdown": _flat((bundle / "content.md").read_text("utf-8")),
        "warnings": json.loads(report.read_text("utf-8"))["warnings"],
        "manifest": json.loads((bundle / "manifest.json").read_text("utf-8")),
    }


@pytest.mark.browser
def test_a_grown_frame_prints_its_whole_document(capture: dict[str, Any]) -> None:
    for name in (*GROWN, *KEPT):
        assert f"Top of the {name} frame." in capture["pdf"], name
    for name in GROWN:
        assert f"Bottom of the {name} frame" in capture["pdf"], name
    for name in KEPT:
        assert f"Bottom of the {name} frame" not in capture["pdf"], name


@pytest.mark.browser
def test_what_still_does_not_fit_is_named(capture: dict[str, Any]) -> None:
    """The capped frame, below its box, and the wide one, beside it, as text
    the PDF leaves out. The two unscrollable ones, as text neither the page
    nor the PDF shows: not grown, and no longer silent about it."""
    named = [
        w
        for w in capture["warnings"]
        if w.startswith("The frame at `") and "outside its box" in w
    ]
    assert len(named) == 4, capture["warnings"]
    scrolls = [w for w in named if " scrolls, and " in w]
    fixed = [w for w in named if " cannot be scrolled (" in w]
    assert len(scrolls) == 2 and len(fixed) == 2, named
    capped, wide = scrolls
    assert capped.startswith("The frame at `div#capped > iframe` scrolls")
    assert "above or below it" in capped and "beside it" not in capped
    assert "so the PDF does not print that text" in capped
    assert wide.startswith("The frame at `iframe#wide` scrolls")
    assert "beside it" in wide and "above or below it" not in wide
    by_attribute, by_document = fixed
    assert "(its `scrolling` is `no`)" in by_attribute
    assert "(its document hides its overflow)" in by_document
    for warning in fixed:
        assert "above or below it" in warning
        assert "so neither the page nor the PDF shows that text" in warning
    assert set(named) <= set(capture["manifest"]["warnings"])


@pytest.mark.browser
def test_the_bundle_holds_every_frames_whole_text(capture: dict[str, Any]) -> None:
    """What the warning says content.html holds, it holds."""
    for name in (*GROWN, *KEPT):
        assert f"Bottom of the {name} frame" in capture["markdown"], name
    assert "and further still." in capture["markdown"]


_STATE_JS = """() => {
    const frames = [];
    const visit = root => {
        for (const element of root.querySelectorAll('iframe')) {
            frames.push({title: element.title,
                         clamped: element.getAttribute('data-webshot-clamped'),
                         style: element.getAttribute('style')});
            try { if (element.contentDocument) visit(element.contentDocument); } catch {}
        }
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) visit(element.shadowRoot);
        }
    };
    visit(document);
    return {frames, width: innerWidth};
}"""


def _options() -> CaptureOptions:
    return CaptureOptions(source="fixture", output=Path("unused.pdf"))


def _on(fixture: Path, body: Any) -> Any:
    async def run() -> Any:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(fixture.as_uri(), wait_until="load")
                await page.emulate_media(media="print")
                return await body(page)
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
def test_frames_grow_on_trial_and_a_failed_trial_leaves_nothing() -> None:
    async def body(page: Any) -> tuple[FrameFit, Any, int]:
        width = page.viewport_size["width"]
        fitted = await fit_frames(page, _options())
        return fitted, await page.evaluate(_STATE_JS), width

    fitted, state, width = _on(FIXTURE, body)
    assert fitted.released == len(GROWN)
    grown = {f["title"] for f in state["frames"] if f["clamped"] == "frame"}
    assert grown == {"Tall frame", "Outer frame", "Inner frame", "Shadow frame"}
    capped = next(f for f in state["frames"] if f["title"] == "Capped frame")
    # The trial's height is gone, not left behind for the print.
    assert capped["style"] is None and capped["clamped"] is None
    # Measured at the printed width, and the viewport put back.
    assert state["width"] == width


@pytest.mark.browser
def test_a_page_without_a_frame_to_fit_pays_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not even the print-width probe. `lms_page.html`'s player is an empty
    `about:blank` frame, the golden corpus's only frame."""

    async def no_probe(*_: Any) -> int:
        raise AssertionError("the print-width probe ran")

    monkeypatch.setattr(frames_module, "printed_width", no_probe)
    fitted = _on(FIXTURES / "lms_page.html", lambda page: fit_frames(page, _options()))
    assert fitted == FrameFit()
