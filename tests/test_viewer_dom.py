"""The protected-viewer DOM contract, checked against recorded fixtures.

docs/07 lists R6 — "the SharePoint viewer's DOM changes and the capture breaks
silently" — and its mitigation referred to fixtures that did not exist. These
tests are that mitigation: `tests/fixtures/viewer/dom/` reproduces the
accessibility tree the capture reads, and every selector below comes from
`webshot.protected.viewer` rather than being retyped, so a change to the
capture's assumptions fails here first.

What they cover and what they do not is written down in the fixtures' README,
and repeated in docs/09 P1-6: they catch drift in WebShot, not drift in
Microsoft's viewer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from webshot.protected.viewer import (
    NEXT_PAGE_NAME,
    PREVIOUS_PAGE_NAME,
    SUPPORTED_VIEWERS,
    VIEWER_CONTAINER,
    ZOOM_FIT_SLIDE,
    ZOOM_MENU_NAME,
    ZOOM_WINDOW_SIZE,
    _viewer_metrics,
    page_box_name,
)

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "viewer" / "dom"
VIEWPORT = {"width": 1440, "height": 1000}


async def _measure(fixture: Path) -> tuple[dict[str, Any], dict[str, int]]:
    """Load a fixture in a real browser and read it exactly as capture does."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            await page.goto(fixture.as_uri())
            await page.locator(VIEWER_CONTAINER).first.wait_for(state="visible")
            metrics = await _viewer_metrics(page)
            named = {
                "zoom_menu": await page.get_by_role(
                    "menuitem", name=ZOOM_MENU_NAME, exact=True
                ).count(),
                "zoom_fit": await page.get_by_role(
                    "menuitemcheckbox", name=ZOOM_FIT_SLIDE, exact=True
                ).count(),
                "zoom_window": await page.get_by_role(
                    "menuitemcheckbox", name=ZOOM_WINDOW_SIZE, exact=True
                ).count(),
                "previous": await page.get_by_role(
                    "menuitem", name=PREVIOUS_PAGE_NAME, exact=True
                ).count(),
                "next": await page.get_by_role(
                    "menuitem", name=NEXT_PAGE_NAME, exact=True
                ).count(),
                "page_box": await page.get_by_role(
                    "textbox",
                    name=page_box_name(metrics["pageCount"]),
                    exact=True,
                ).count(),
            }
            return metrics, named
        finally:
            await browser.close()


@pytest.mark.browser
def test_portrait_fixture_exposes_the_geometry_a_capture_needs() -> None:
    metrics, _ = asyncio.run(_measure(FIXTURES / "sharepoint-portrait.html"))

    assert metrics["title"] == "fixture-document.pdf"
    assert metrics["pageCount"] == 3
    assert metrics["currentPage"] == 1
    # The capture measures the step between pages from the first two, so both
    # have to be found — and the toolbar's "Page 1 of 3" must not be one of them.
    assert [page["page"] for page in metrics["pages"]] == [1, 2]
    assert metrics["viewer"]["scrollHeight"] > metrics["viewer"]["height"]

    first, second = metrics["pages"]
    assert first["height"] > first["width"], "portrait pages drive the segmented path"
    assert second["y"] - first["y"] == pytest.approx(1080, abs=2)
    # Taller than the viewport is exactly why the portrait path stitches
    # overlapping segments rather than taking one screenshot.
    assert first["height"] > metrics["viewer"]["height"]


@pytest.mark.browser
def test_landscape_fixture_exposes_every_toolbar_control_by_name() -> None:
    metrics, named = asyncio.run(_measure(FIXTURES / "sharepoint-landscape.html"))

    assert metrics["title"] == "fixture-deck.pdf"
    assert metrics["pageCount"] == 2
    first = metrics["pages"][0]
    assert first["width"] > first["height"], "wide pages drive the toolbar path"
    assert named == dict.fromkeys(named, 1), (
        "every toolbar control the landscape capture drives must resolve to "
        f"exactly one element: {named}"
    )


def test_supported_viewer_builds_are_recorded() -> None:
    """The note docs/05 task 1.7 asks for cannot quietly disappear."""
    assert SUPPORTED_VIEWERS
    assert all("SharePoint" in entry for entry in SUPPORTED_VIEWERS)


def test_fixtures_carry_no_tenant_or_document_data() -> None:
    """A capture tool must not be the thing that leaks a document.

    The fixtures are structure only. This is a cheap, permanent check that
    nobody later pastes a real export in (docs/04-spec.md §6).
    """
    forbidden = ("sharepoint.com", "onedrive", "@", "sites/", "personal/")
    for fixture in sorted(FIXTURES.glob("*.html")):
        text = fixture.read_text(encoding="utf-8").lower()
        found = [token for token in forbidden if token in text]
        assert not found, f"{fixture.name} looks like a real export: {found}"
