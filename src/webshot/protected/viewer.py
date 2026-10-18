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

from collections.abc import Mapping
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


async def _page_box(page: Page, number: int) -> dict[str, Any] | None:
    """Page `number`'s rectangle as the viewer has it now, or None.

    The page is found by its own `Page N.` label, not by its place among the
    page elements: whether the viewer keeps every page in the DOM or only the
    ones near what it shows is not recorded (`tests/fixtures/viewer/dom/` holds
    its accessibility tree, not its behaviour), and in a viewer that drops
    the far ones the first element present need not be page 1.

    `top` is the page's offset in the viewer's scrolled content, the
    coordinate `scrollTop` takes. Everything else is in viewport coordinates,
    which a screenshot clip takes. `viewer` is the container's client box,
    the part of it that shows content.
    """
    box: dict[str, Any] | None = await page.evaluate(
        """(contract) => {
            const viewer = [...document.querySelectorAll(contract.container)]
                .find(element => element.scrollHeight > element.clientHeight);
            if (!viewer) throw new Error('The viewer container is no longer scrollable');
            const labelPattern = new RegExp(contract.pageLabel);
            const element = [...document.querySelectorAll(contract.page)]
                .find(candidate => {
                    const label = candidate.getAttribute('aria-label') || '';
                    return labelPattern.test(label)
                        && Number(label.match(/\\d+/)[0]) === contract.number;
                });
            if (!element) return null;
            const rect = element.getBoundingClientRect();
            const outer = viewer.getBoundingClientRect();
            const visible = {
                x: outer.x + viewer.clientLeft, y: outer.y + viewer.clientTop,
                width: viewer.clientWidth, height: viewer.clientHeight
            };
            return {
                x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                top: viewer.scrollTop + rect.y - visible.y,
                scrollTop: viewer.scrollTop,
                viewer: visible
            };
        }""",
        {
            "container": VIEWER_CONTAINER,
            "page": PAGE_ELEMENT,
            "pageLabel": PAGE_LABEL_PATTERN,
            "number": number,
        },
    )
    return box


async def _scroll_viewer(page: Page, value: float) -> None:
    """Put the viewer at `value` now, and give it half a second to draw there."""
    # `scrollTop =` obeys the viewer's `scroll-behavior`: on a smooth one it
    # starts an animation and returns. The first scroll comes from wherever
    # the viewer was left, so it can cross the whole document, and from the
    # last page of a three-page one it was still moving when the first
    # segment was shot (docs/09 P42). `instant` puts the viewer there now,
    # and the synthetic `scroll` event follows the move rather than preceding
    # it. The function hands back nothing, because Chromium 153's `scrollTo`
    # returns a promise `evaluate` would wait on (docs/09 P34-5).
    await page.evaluate(
        """([selector, value]) => {
            const viewer = [...document.querySelectorAll(selector)]
                .find(element => element.scrollHeight > element.clientHeight);
            viewer.scrollTo({top: value, behavior: 'instant'});
            viewer.dispatchEvent(new Event('scroll'));
        }""",
        [VIEWER_CONTAINER, value],
    )
    await page.wait_for_timeout(500)


async def _first_page_box(page: Page) -> dict[str, Any]:
    """Page 1's rectangle, scrolling the viewer to its top if page 1 is not drawn.

    The capture took the orientation from the first `Page N.` element
    present. In a viewer that keeps only the pages near the view, that is
    whichever page the reader left it on, and a deck left on a portrait page
    took the scroll path at the viewer's own zoom (docs/09 P51).
    """
    box = await _page_box(page, 1)
    if box is None:
        await _scroll_viewer(page, 0)
        box = await _page_box(page, 1)
    if box is None:
        raise RuntimeError(
            "The viewer has no `Page 1.` element, even scrolled to its top"
        )
    return box


def _shared(values: list[Any]) -> Any:
    """The one value every page has, or None.

    One number for a document whose pages differ would describe some pages
    and not others, so it is null, and `page_images` says what each page is
    (docs/09 P46).
    """
    return values[0] if len(set(values)) == 1 else None


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
    """Page through a deck at 75% zoom, shooting each slide where it is.

    Every slide was clipped at page 1's rectangle, measured once after the
    zoom, at the top of the viewer, and only page 1's height was checked
    against the viewer's. A slide of another size came out page 1's size: a
    narrower, shorter one with the viewer's background beside and below it,
    a taller one cut off at page 1's height, and the capture reported page
    1's size for the deck (docs/09 P51). Each slide is now found by its own
    `Page N.` element once the toolbar has gone to it and hidden itself,
    measured there, and shot as itself. One the viewer does not show whole
    stops the capture, with half a pixel of slack, as the portrait path
    allows.
    """
    ratio = float(await page.evaluate("() => window.devicePixelRatio")) or 1.0
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

    for page_number in range(1, metrics["pageCount"] + 1):
        await _hide_viewer_toolbar(page)
        # Measured after the toolbar's wait, as the screenshot will find it.
        slide = await _page_box(page, page_number)
        if slide is None:
            raise RuntimeError(
                f"The viewer's toolbar is at page {page_number}, and the "
                f"viewer has no `Page {page_number}.` element"
            )
        visible = slide["viewer"]
        if (
            slide["x"] < visible["x"] - 0.5
            or slide["y"] < visible["y"] - 0.5
            or slide["x"] + slide["width"] > visible["x"] + visible["width"] + 0.5
            or slide["y"] + slide["height"] > visible["y"] + visible["height"] + 0.5
        ):
            raise RuntimeError(
                f"Page {page_number} measures {slide['width']:g} x "
                f"{slide['height']:g}px at {ZOOM_FIT_SLIDE} zoom, at "
                f"({slide['x']:g}, {slide['y']:g}), and the viewer shows "
                f"{visible['width']:g} x {visible['height']:g}px at "
                f"({visible['x']:g}, {visible['y']:g}), so the page cannot be "
                "shot whole"
            )
        clip: FloatRect = {
            "x": float(slide["x"]),
            "y": float(slide["y"]),
            "width": float(slide["width"]),
            "height": float(slide["height"]),
        }
        await page.screenshot(
            path=directory / f"page-{page_number:03d}.png",
            clip=clip,
            animations="disabled",
            caret="hide",
        )
        if page_number == metrics["pageCount"]:
            break
        await _open_pdf_toolbar(page, metrics)
        next_page = page.get_by_role("menuitem", name=NEXT_PAGE_NAME, exact=True)
        await next_page.click()
        await page.wait_for_timeout(350)
        observed = int(await page_box.input_value())
        if observed != page_number + 1:
            raise RuntimeError(
                f"Viewer navigation mismatch: expected page {page_number + 1}, "
                f"observed {observed}"
            )

    await _open_pdf_toolbar(page, metrics)
    await zoom.click()
    await page.get_by_role(
        "menuitemcheckbox", name=ZOOM_WINDOW_SIZE, exact=True
    ).click()
    await _hide_viewer_toolbar(page)

    # Device pixels, read from the images written, as the portrait path
    # reports them (docs/09 P8-46, P46-4).
    images: list[dict[str, Any]] = []
    for number in range(1, metrics["pageCount"] + 1):
        with Image.open(directory / f"page-{number:03d}.png") as image:
            width, height = image.size
        images.append(
            {
                "page": number,
                "width": width,
                "height": height,
                "segments": 1,
                "segment_overlap": 0,
            }
        )
    return {
        "page_count": metrics["pageCount"],
        "page_width": _shared([image["width"] for image in images]),
        "page_height": _shared([image["height"] for image in images]),
        "segmented": False,
        "page_images": images,
        "device_pixel_ratio": ratio,
        "viewer_zoom_for_capture": ZOOM_FIT_SLIDE,
    }


async def _capture_portrait_viewer(
    page: Page, directory: Path, metrics: dict[str, Any]
) -> dict[str, Any]:
    """Capture every portrait page, stitching a tall one from two overlapping shots.

    Four things here are measured rather than assumed, and each was assumed:

    * **Each page's own rectangle.** Every page was shot where page 1's
      position and height, and the step from page 1 to page 2, said it was,
      at page 1's width. In a document whose pages differ in size (a
      landscape page among portrait ones, a Legal page, an appended form, a
      shorter last page) every page after the first that differed came out
      as a slice of the document page 1's size, holding parts of its
      neighbours, and the scroll to it still took, so nothing noticed
      (docs/09 P46). Each page is now found by its own `Page N.` element,
      measured once it is in view, and shot and sized as itself.

    * **The page's absolute offset inside the scroller.** A rectangle's `y`
      is viewport-relative, so its difference from the viewer's is the page's
      offset *in the current scroll state*. It was written straight to
      `scrollTop`, a document coordinate, which is correct only when the
      viewer happened to be at the top. A viewer restored to a nonzero
      `scrollTop`, or scrolled by the user during interactive authentication,
      shifted every page by that amount (docs/09 P8-45). `_page_box` adds
      `scrollTop` back.

    * **The device pixel ratio.** The context is created with
      `device_scale_factor=2` and `page.screenshot` defaults to device-scale
      output, so each segment PNG is twice as tall as the CSS rectangle it was
      clipped from — while `page_height` came from that CSS rectangle. For an
      ordinary letter page inside a ~900px viewer, `stitch_segmented_pages`
      compared 1100 against 1800 and refused the capture outright with
      "stitching is unnecessary". Every multi-page portrait protected capture,
      at the scale factor this code sets itself (docs/09 P8-46). The stitch
      is a pixel operation, so it is given pixels.

    * **Where the viewer actually is.** Each shot is clipped from where the
      page is measured after the scroll, not from where the scroll was asked
      to put it (docs/09 P42), and a shot whose part of the page is not in
      view stops the capture.
    """
    ratio = float(await page.evaluate("() => window.devicePixelRatio")) or 1.0
    raw = directory / "segments"
    raw.mkdir(parents=True)

    async def settle(number: int, target: float) -> dict[str, Any]:
        """Scroll to `target` and measure page `number` where the viewer is."""
        await _scroll_viewer(page, target)
        box = await _page_box(page, number)
        if box is None:
            raise RuntimeError(
                f"The viewer dropped page {number}'s element when it was "
                f"scrolled to {target:g}px, inside that page"
            )
        return box

    async def locate(number: int, begins_after: float) -> dict[str, Any]:
        """Find page `number`, scrolling to where it begins if it is not drawn.

        `begins_after` is the bottom of the page before it, so a page the
        viewer only draws near the view is in view from there whatever the
        gap between pages.
        """
        box = await _page_box(page, number)
        if box is None:
            await _scroll_viewer(page, begins_after)
            box = await _page_box(page, number)
        if box is None:
            raise RuntimeError(
                f"The viewer has no `Page {number}.` element, even scrolled to "
                f"{begins_after:g}px, where page {number} begins"
            )
        return box

    async def shoot(
        number: int,
        now: dict[str, Any],
        target: float,
        offset: float,
        length: float,
        path: Path,
    ) -> None:
        """Shoot rows `offset` to `offset + length` of page `number`.

        The clip is where `now` measured the page after the scroll to
        `target`, not where that scroll was asked to put it, so a short last
        page the viewer cannot scroll to its top is shot lower in the view.
        Rows the viewer does not show cannot be shot from anywhere, and a
        viewer that clamps the scroll, snaps it, or moves the page while it
        draws leaves some out: that stops the capture. Chromium keeps the
        scroll offset in whole CSS pixels while a page's offset need not be
        one (1104.5 reads back 1105, docs/09 P42-3), hence half a pixel of
        slack, and the clip is kept inside the viewer.
        """
        visible = now["viewer"]
        y = now["y"] + offset
        if (
            y < visible["y"] - 0.5
            or y + length > visible["y"] + visible["height"] + 0.5
        ):
            raise RuntimeError(
                f"The viewer was scrolled to {target:g}px for a segment of page "
                f"{number} but is at {now['scrollTop']:g}px, so the segment "
                "would not show the part of the page its clip assumes"
            )
        if (
            now["x"] < visible["x"] - 0.5
            or now["x"] + now["width"] > visible["x"] + visible["width"] + 0.5
        ):
            raise RuntimeError(
                f"Page {number} is {now['width']:g}px wide and the viewer shows "
                f"{visible['width']:g}px, so the page cannot be shot whole"
            )
        clip: FloatRect = {
            "x": float(now["x"]),
            "y": float(
                min(max(y, visible["y"]), visible["y"] + visible["height"] - length)
            ),
            "width": float(now["width"]),
            "height": float(length),
        }
        await page.screenshot(path=path, clip=clip, animations="disabled", caret="hide")

    directory.mkdir(parents=True, exist_ok=True)
    stitch: dict[int, int] = {}
    segment_heights: dict[int, int] = {}
    begins_after = 0.0
    for number in range(1, metrics["pageCount"] + 1):
        found = await locate(number, begins_after)
        # Measured again in view, after the wait, so a viewer that sizes a
        # page it has not drawn yet from another page is read once it has.
        now = await settle(number, found["top"])
        top, height = now["top"], now["height"]
        visible = now["viewer"]["height"]
        page_height = round(height * ratio)
        segment_height = round(visible * ratio)
        segment_heights[number] = segment_height
        begins_after = top + height
        # A page that fits inside the viewer needs one shot, not two
        # overlapping ones — and `stitch_segmented_pages` refuses to stitch
        # what does not overlap, so segmenting it would fail rather than
        # degrade. The commonest case is a single-page letter or form, which
        # could not be captured at all once (docs/09 P8-47), and now any page
        # of a document that fits: a landscape page among portrait ones.
        if page_height <= segment_height:
            await shoot(
                number,
                now,
                found["top"],
                0,
                height,
                directory / f"page-{number:03d}.png",
            )
            continue
        await shoot(
            number, now, found["top"], 0, visible, raw / f"page-{number:03d}-top.png"
        )
        target = top + height - visible
        bottom = await settle(number, target)
        if (bottom["width"], bottom["height"]) != (now["width"], now["height"]):
            raise RuntimeError(
                f"Page {number} measured {now['width']:g} x {height:g}px for its "
                f"top segment and {bottom['width']:g} x {bottom['height']:g}px "
                "for its bottom one, so the two would not join"
            )
        await shoot(
            number,
            bottom,
            target,
            height - visible,
            visible,
            raw / f"page-{number:03d}-bottom.png",
        )
        stitch[number] = page_height
    await _scroll_viewer(page, 0)
    stitch_segmented_pages(raw, directory, page_heights=stitch)

    # Device pixels, read from the images written, so each number describes a
    # file that exists.
    images: list[dict[str, Any]] = []
    for number in range(1, metrics["pageCount"] + 1):
        with Image.open(directory / f"page-{number:03d}.png") as image:
            width, height = image.size
        overlap = segment_heights[number] * 2 - height if number in stitch else 0
        images.append(
            {
                "page": number,
                "width": width,
                "height": height,
                "segments": 2 if number in stitch else 1,
                "segment_overlap": overlap,
            }
        )

    return {
        "page_count": metrics["pageCount"],
        "page_width": _shared([image["width"] for image in images]),
        "page_height": _shared([image["height"] for image in images]),
        "segment_height": _shared(list(segment_heights.values())),
        "segmented": bool(stitch),
        "segment_overlap": _shared([image["segment_overlap"] for image in images]),
        "page_images": images,
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
    # Page 1 decides how the whole document is captured: the toolbar at 75%
    # for a wide one, the scroll at the viewer's own zoom for any other. One
    # path draws every page at one zoom, which the packed PDF's scale
    # assumes (docs/09 P46-5), and the choice does not depend on where the
    # reader left the viewer. Each path shoots each page as itself, and one
    # it cannot shoot whole stops the capture (docs/09 P51).
    first = await _first_page_box(page)
    if first["width"] > first["height"]:
        result = await _capture_landscape_viewer(page, destination_directory, metrics)
    else:
        result = await _capture_portrait_viewer(page, destination_directory, metrics)
    return {"viewer_title": metrics["title"], **result}


def stitch_segmented_pages(
    raw_directory: Path,
    destination_directory: Path,
    *,
    page_heights: Mapping[int, int],
) -> list[Path]:
    """Stitch overlapping top/bottom viewer screenshots without a visible seam.

    `page_heights` names each page to stitch and its own height in the
    segments' pixels. A document's pages need not share a size, so no page's
    height or width is taken from another's (docs/09 P46).
    """
    destination_directory.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for page_number, page_height in sorted(page_heights.items()):
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
