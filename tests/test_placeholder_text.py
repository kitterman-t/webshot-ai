"""WebShot's own words never stand in the bundle as the page's (docs/09 P20-1).

Two kinds of text reached the reading surfaces as if the page had written them.
A visual the page gave no label got a placeholder alt in the snapshot ("Vector
graphic" for an `<svg>`, "Embedded frame" for an unread frame, "Rendered canvas
visual" for a canvas), and docling printed it in `content.md`, `content.txt`
and the chunks as the picture's caption, while `assets.json` said `alt: ""`.
And `embedded_media[].title` was read from `aria-label` after the visuals pass
had written its description there, so recognized text became a video's title.

`unlabeled_visuals.html` holds one of each, beside an `<svg>` named by its own
`<title>` and a video the page labelled, which must keep their labels. The
stub engine reads text in some visuals and none in others, because a
placeholder appeared only where there was no description to write.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from webshot.bundle.build import create_ai_bundle
from webshot.capture.visuals import OCR_PDF_PREFIX
from webshot.ocr.engine import OCRResult

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "unlabeled_visuals.html"

RECOGNIZED = "STUB RECOGNIZED WORDS"

#: Assets the stub reads nothing in, by id: the unlabeled `<svg>`, the canvas
#: and the second frame. Ids follow reading order (see the fixture).
UNREAD = {"asset-001", "asset-005", "asset-007"}

PLACEHOLDERS = ("Vector graphic", "Rendered canvas visual", "Embedded frame")


class _Stub:
    name = "stub"
    label = "Stub"

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        return {}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        if image.stem in UNREAD:
            return OCRResult(engine="Stub")
        return OCRResult(
            text=RECOGNIZED, confidence=31.5, language=language, engine="Stub"
        )


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("placeholder-text")

    async def run() -> Path:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                result = await create_ai_bundle(
                    page,
                    staging_directory=directory / "staging",
                    final_directory=directory / "final",
                    source=str(FIXTURE),
                    title="Unlabeled visuals",
                    max_assets=20,
                    ocr=True,
                    ocr_language="eng",
                    ocr_psm=3,
                    ocr_engine=_Stub(),
                    original_local_path=None,
                )
            finally:
                await browser.close()
        return result.staging_directory

    staging = asyncio.run(run())
    assets = json.loads((staging / "assets.json").read_text("utf-8"))
    return {
        "staging": staging,
        "assets": assets,
        "html": (staging / "content.html").read_text("utf-8"),
    }


@pytest.mark.browser
def test_the_fixture_holds_every_case(bundle: dict[str, Any]) -> None:
    """The ids the stub keys on are the visuals the fixture means them to be."""
    kinds = {a["id"]: a["kind"] for a in bundle["assets"]["visual_assets"]}
    assert kinds == {
        "asset-001": "svg",
        "asset-002": "svg",
        "asset-003": "video",
        "asset-004": "video",
        "asset-005": "canvas",
        "asset-006": "iframe",
        "asset-007": "iframe",
    }


@pytest.mark.browser
def test_each_picture_carries_only_the_label_the_page_wrote(
    bundle: dict[str, Any],
) -> None:
    alts = dict(
        re.findall(
            r'<img [^>]*data-webshot-asset-id="([^"]+)"[^>]*alt="([^"]*)"',
            bundle["html"],
        )
    )
    assert alts == {
        "asset-001": "",
        # Its <title> is never rendered, so it is read off the live element.
        "asset-002": "Weekly visits chart",
        "asset-005": "",
        "asset-006": "",
        "asset-007": "",
    }


@pytest.mark.browser
@pytest.mark.parametrize(
    "surface", ["content.html", "content.md", "content.txt", "chunks.jsonl"]
)
def test_no_placeholder_reaches_a_text_surface(
    bundle: dict[str, Any], surface: str
) -> None:
    text = (bundle["staging"] / surface).read_text("utf-8")
    for placeholder in PLACEHOLDERS:
        assert placeholder not in text, (placeholder, surface)
    assert OCR_PDF_PREFIX not in text, surface


@pytest.mark.browser
def test_a_media_title_is_the_pages_own(bundle: dict[str, Any]) -> None:
    titles = [
        (media["media_type"], media["title"])
        for media in bundle["assets"]["embedded_media"]
    ]
    assert titles == [
        ("video", ""),
        ("video", "Onboarding recording"),
        ("iframe", ""),
        ("iframe", ""),
    ]
