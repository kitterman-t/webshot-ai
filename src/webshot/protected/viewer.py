"""Capture every page an authenticated document viewer is willing to render.

This is the second module docs/01-scope.md calls audit-confirmed unique value,
and it carries a product invariant (docs/04-spec.md §6.4, docs/11-capture-ethics.md):
it reads only what the viewer has already rendered for an authorized user.  It
never requests the native file and never touches credentials.  The page images
it collects are exactly what the person running it can already see on screen,
which is still a copy: a download or copy block the owner set is not detected
here, and docs/11 tells the user to treat one as covering WebShot too.

Tall pages are captured in overlapping segments and stitched back to the exact
page height, because a viewer only ever paints the visible slice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image
from playwright.async_api import FloatRect, Page

# --------------------------------------------------------------------------- #
# The viewer contract
# --------------------------------------------------------------------------- #
#
# Everything below is a promise the SharePoint viewer makes through its
# accessibility tree, and every one of them is a way this capture can break
# without any WebShot code changing.  They are constants rather than literals
# buried in JavaScript so that the DOM fixtures in `tests/fixtures/viewer/dom/`
# can be checked against the *same* strings the capture uses (docs/05 task 1.7)
# — a fixture test that re-typed the selectors would only be testing itself.

#: The scrollable element that holds the rendered document; its label is the
#: file name, which is the only thing that reliably identifies it.
VIEWER_CONTAINER = '[aria-label$=".pdf"]'
#: One element per rendered page, positioned absolutely inside the container.
PAGE_ELEMENT = '[aria-label^="Page "]'
#: …but only those whose label is exactly `Page N.` — the toolbar's controls
#: are labelled "Page N of M" and must not be mistaken for pages.
PAGE_LABEL_PATTERN = r"^Page \d+\.$"
#: The toolbar's own page counter, which is where the page *count* comes from.
PAGE_COUNTER_PATTERN = r"Page (\d+) of (\d+)"

#: Toolbar controls, by accessible name and role.
ZOOM_MENU_NAME = (
    "Change the current zoom level. Press enter to open the option menu and "
    "esc to close it."
)
ZOOM_FIT_SLIDE = "75%"
ZOOM_WINDOW_SIZE = "Window size"
PREVIOUS_PAGE_NAME = "Go to the previous page."
NEXT_PAGE_NAME = "Go to the next page."


def page_box_name(page_count: int) -> str:
    """The accessible name of the toolbar's "jump to page" text box."""
    return (
        f"Enter a value between 1 and {page_count}. Press enter to jump to that "
        "page. Press escape to exit the input control."
    )


#: Viewer builds this capture has been exercised against. The DOM fixtures
#: freeze the contract; only a run against a live tenant can tell you Microsoft
#: has changed it (docs/09 P1-6).
SUPPORTED_VIEWERS = (
    "Microsoft SharePoint Online PDF viewer, 2025-08 to 2026-08 accessibility tree",
)


async def _viewer_metrics(page: Page) -> dict[str, Any]:
    """Measure the viewer through its accessibility tree.

    The selectors are passed in rather than written into the script so that
    `VIEWER_CONTAINER` and friends stay the single definition of the contract.
    """
    metrics: dict[str, Any] | None = await page.evaluate(
        """(contract) => {
            const viewer = [...document.querySelectorAll(contract.container)]
                .find(element => element.scrollHeight > element.clientHeight);
            if (!viewer) return null;
            const labelPattern = new RegExp(contract.pageLabel);
            const counterPattern = new RegExp(contract.pageCounter);
            const pageElements = [...document.querySelectorAll(contract.page)]
                .filter(element => labelPattern.test(element.getAttribute('aria-label') || ''));
            const counter = [...document.querySelectorAll('[aria-label]')]
                .map(element => element.getAttribute('aria-label') || '')
                .find(label => counterPattern.test(label));
            const match = counter?.match(counterPattern);
            const rects = pageElements.slice(0, 2).map(element => {
                const rect = element.getBoundingClientRect();
                return {
                    page: Number((element.getAttribute('aria-label') || '').match(/\\d+/)?.[0]),
                    x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                    top: Number.parseFloat(element.style.top || '0')
                };
            });
            const viewerRect = viewer.getBoundingClientRect();
            return {
                title: viewer.getAttribute('aria-label') || document.title,
                viewer: {
                    x: viewerRect.x, y: viewerRect.y, width: viewerRect.width,
                    height: viewerRect.height, scrollTop: viewer.scrollTop,
                    scrollHeight: viewer.scrollHeight
                },
                pages: rects,
                currentPage: match ? Number(match[1]) : 1,
                pageCount: match ? Number(match[2]) : 0
            };
        }""",
        {
            "container": VIEWER_CONTAINER,
            "page": PAGE_ELEMENT,
            "pageLabel": PAGE_LABEL_PATTERN,
            "pageCounter": PAGE_COUNTER_PATTERN,
        },
    )
    if not metrics or not metrics["pages"] or metrics["pageCount"] < 1:
        raise RuntimeError("The SharePoint PDF viewer did not expose page geometry")
    return metrics


async def _hide_viewer_toolbar(page: Page) -> None:
    viewport = page.viewport_size or {"width": 1440, "height": 1000}
    await page.mouse.move(viewport["width"] - 4, 8)
    await page.wait_for_timeout(2200)


async def _open_pdf_toolbar(page: Page, metrics: dict[str, Any]) -> None:
    viewer = metrics["viewer"]
    await page.mouse.click(
        viewer["x"] + viewer["width"] / 2,
        viewer["y"] + min(viewer["height"], 500) / 2,
    )
    await page.keyboard.press("Alt+T")
    await page.wait_for_timeout(150)


async def _capture_landscape_viewer(
    page: Page, directory: Path, metrics: dict[str, Any]
) -> dict[str, Any]:
    await _open_pdf_toolbar(page, metrics)
    zoom = page.get_by_role("menuitem", name=ZOOM_MENU_NAME, exact=True)
    await zoom.click()
    await page.get_by_role("menuitemcheckbox", name=ZOOM_FIT_SLIDE, exact=True).click()
    await page.wait_for_timeout(250)

    previous = page.get_by_role("menuitem", name=PREVIOUS_PAGE_NAME, exact=True)
    page_box = page.get_by_role(
        "textbox", name=page_box_name(metrics["pageCount"]), exact=True
    )
    current = int(await page_box.input_value())
    while current > 1:
        await previous.click()
        await page.wait_for_timeout(220)
        current = int(await page_box.input_value())

    fit_metrics = await _viewer_metrics(page)
    page_rect = fit_metrics["pages"][0]
    if page_rect["height"] > fit_metrics["viewer"]["height"] + 1:
        raise RuntimeError("75% zoom did not fit the slide inside the viewer")

    for page_number in range(1, metrics["pageCount"] + 1):
        await _hide_viewer_toolbar(page)
        await page.screenshot(
            path=directory / f"page-{page_number:03d}.png",
            clip={
                "x": page_rect["x"],
                "y": fit_metrics["viewer"]["y"],
                "width": page_rect["width"],
                "height": page_rect["height"],
            },
            animations="disabled",
            caret="hide",
        )
        if page_number == metrics["pageCount"]:
            break
        await _open_pdf_toolbar(page, fit_metrics)
        next_page = page.get_by_role("menuitem", name=NEXT_PAGE_NAME, exact=True)
        await next_page.click()
        await page.wait_for_timeout(350)
        observed = int(await page_box.input_value())
        if observed != page_number + 1:
            raise RuntimeError(
                f"Viewer navigation mismatch: expected page {page_number + 1}, "
                f"observed {observed}"
            )

    await _open_pdf_toolbar(page, fit_metrics)
    await zoom.click()
    await page.get_by_role(
        "menuitemcheckbox", name=ZOOM_WINDOW_SIZE, exact=True
    ).click()
    await _hide_viewer_toolbar(page)
    return {
        "page_count": metrics["pageCount"],
        "page_width": round(page_rect["width"]),
        "page_height": round(page_rect["height"]),
        "segmented": False,
        "viewer_zoom_for_capture": "75%",
    }


async def _capture_portrait_viewer(
    page: Page, directory: Path, metrics: dict[str, Any]
) -> dict[str, Any]:
    """Capture every portrait page, stitching each from two overlapping shots.

    Three things here are measured rather than assumed, and each was assumed:

    * **The page's absolute offset inside the scroller.** `pages[0].y` and
      `viewer.y` are both viewport-relative, so their difference is the page's
      offset *in the current scroll state*. It was then written straight to
      `scrollTop`, which is a document coordinate — correct only when the
      viewer happened to be at the top. A viewer restored to a nonzero
      `scrollTop`, or scrolled by the user during interactive authentication,
      shifted every page by that amount: each stitched page silently carried
      its neighbour's whitespace and lost part of itself (docs/09 P8-45).
      `_viewer_metrics` already reports `scrollTop`; it is now added.

    * **The device pixel ratio.** The context is created with
      `device_scale_factor=2` and `page.screenshot` defaults to device-scale
      output, so each segment PNG is twice as tall as the CSS rectangle it was
      clipped from — while `page_height` came from that CSS rectangle. For an
      ordinary letter page inside a ~900px viewer, `stitch_segmented_pages`
      compared 1100 against 1800 and refused the capture outright with
      "stitching is unnecessary". Every multi-page portrait protected capture,
      at the scale factor this code sets itself (docs/09 P8-46). The stitch
      is a pixel operation, so it is given pixels.

    * **That there are at least two pages.** A single-page letter, form or
      report exposes one rectangle, so the spacing sample does not exist —
      and the check for it aborted the capture rather than noticing that a
      document with one page needs no inter-page spacing (docs/09 P8-47).
    """
    page_rect = metrics["pages"][0]
    viewer = metrics["viewer"]
    ratio = float(await page.evaluate("() => window.devicePixelRatio")) or 1.0
    if metrics["pageCount"] > 1 and len(metrics["pages"]) < 2:
        raise RuntimeError("The viewer did not expose enough pages to measure spacing")
    # One page needs no spacing sample: nothing is ever placed at `step`.
    step = (
        metrics["pages"][1]["y"] - metrics["pages"][0]["y"]
        if len(metrics["pages"]) > 1
        else 0.0
    )
    # The page's own offset from the top of the scrolled content, not from the
    # top of the viewport.
    page_top = viewer["scrollTop"] + (page_rect["y"] - viewer["y"])
    page_height = round(page_rect["height"] * ratio)
    segment_height = round(viewer["height"] * ratio)
    raw = directory / "segments"
    raw.mkdir()

    async def set_scroll(value: float) -> None:
        await page.evaluate(
            """([selector, value]) => {
                const viewer = [...document.querySelectorAll(selector)]
                    .find(element => element.scrollHeight > element.clientHeight);
                viewer.scrollTop = value;
                viewer.dispatchEvent(new Event('scroll'));
            }""",
            [VIEWER_CONTAINER, value],
        )
        await page.wait_for_timeout(500)

    # A page that already fits inside the viewer needs one shot, not two
    # overlapping ones — and `stitch_segmented_pages` refuses to stitch what
    # does not overlap, so segmenting it would fail rather than degrade. The
    # commonest case is a single-page letter or form, which is exactly what
    # could not be captured at all before (docs/09 P8-47).
    segmented = page_height > segment_height
    directory.mkdir(parents=True, exist_ok=True)
    for page_number in range(1, metrics["pageCount"] + 1):
        top = page_top + (page_number - 1) * step
        clip: FloatRect = {
            "x": float(page_rect["x"]),
            "y": float(viewer["y"]),
            "width": float(page_rect["width"]),
            "height": float(viewer["height"] if segmented else page_rect["height"]),
        }
        await set_scroll(top)
        await page.screenshot(
            path=(
                raw / f"page-{page_number:03d}-top.png"
                if segmented
                else directory / f"page-{page_number:03d}.png"
            ),
            clip=clip,
            animations="disabled",
            caret="hide",
        )
        if not segmented:
            continue
        await set_scroll(top + page_rect["height"] - viewer["height"])
        await page.screenshot(
            path=raw / f"page-{page_number:03d}-bottom.png",
            clip=clip,
            animations="disabled",
            caret="hide",
        )
    await set_scroll(0)
    if segmented:
        stitch_segmented_pages(
            raw, directory, page_count=metrics["pageCount"], page_height=page_height
        )
    return {
        "page_count": metrics["pageCount"],
        # Device pixels, matching the images actually written. `page_height`
        # is the stitched output's height and `page_width` describes the same
        # images, so reporting one in CSS pixels and one in device pixels
        # would make the pair describe no file that exists.
        "page_width": round(page_rect["width"] * ratio),
        "page_height": page_height,
        "segment_height": segment_height,
        "segmented": segmented,
        "segment_overlap": (segment_height * 2 - page_height) if segmented else 0,
        "device_pixel_ratio": ratio,
    }


async def capture_sharepoint_pdf_viewer(
    page: Page, destination_directory: Path
) -> dict[str, Any]:
    """Capture every page exposed by an authenticated SharePoint PDF viewer."""
    destination_directory.mkdir(parents=True, exist_ok=True)
    await page.locator(VIEWER_CONTAINER).first.wait_for(
        state="visible", timeout=120_000
    )
    metrics = await _viewer_metrics(page)
    page_rect = metrics["pages"][0]
    if page_rect["width"] > page_rect["height"]:
        result = await _capture_landscape_viewer(page, destination_directory, metrics)
    else:
        result = await _capture_portrait_viewer(page, destination_directory, metrics)
    return {"viewer_title": metrics["title"], **result}


def stitch_segmented_pages(
    raw_directory: Path,
    destination_directory: Path,
    *,
    page_count: int,
    page_height: int,
) -> list[Path]:
    """Stitch overlapping top/bottom viewer screenshots without a visible seam."""
    destination_directory.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for page_number in range(1, page_count + 1):
        label = f"{page_number:03d}"
        top_path = raw_directory / f"page-{label}-top.png"
        bottom_path = raw_directory / f"page-{label}-bottom.png"
        if not top_path.is_file() or not bottom_path.is_file():
            raise FileNotFoundError(f"Missing capture segment for page {page_number}")

        with Image.open(top_path).convert("RGB") as top_image:
            with Image.open(bottom_path).convert("RGB") as bottom_image:
                if top_image.size != bottom_image.size:
                    raise ValueError(
                        f"Page {page_number} segment dimensions differ: "
                        f"{top_image.size} vs {bottom_image.size}"
                    )
                width, segment_height = top_image.size
                if page_height <= segment_height:
                    raise ValueError(
                        "Segment stitching is unnecessary when the complete page fits "
                        "inside one segment."
                    )
                if page_height >= segment_height * 2:
                    raise ValueError(
                        f"Page {page_number} segments do not overlap; completeness "
                        "cannot be guaranteed."
                    )

                bottom_offset = page_height - segment_height
                seam = (segment_height + bottom_offset) // 2
                bottom_crop_y = seam - bottom_offset
                output = Image.new("RGB", (width, page_height), "white")
                output.paste(top_image.crop((0, 0, width, seam)), (0, 0))
                output.paste(
                    bottom_image.crop((0, bottom_crop_y, width, segment_height)),
                    (0, seam),
                )
                output_path = destination_directory / f"page-{label}.png"
                output.save(output_path, format="PNG", optimize=True)
                pages.append(output_path)
    return pages
