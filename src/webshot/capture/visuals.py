"""Harvest the visuals a page renders, and read the text inside them.

This is one of the modules docs/01-scope.md calls audit-confirmed unique
value: screenshotting each visible image, SVG, canvas, video frame, iframe and
CSS background, rasterizing canvases so pagination cannot drop them, and
feeding every asset through OCR so text that exists only as pixels still
reaches the bundle and the PDF's text layer.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FrameLocator, Locator, Page

from ..bundle.publish import sha256_file
from ..errors import OcrUnavailableError
from ..ocr.engine import OcrEngine, OCRResult
from .prepare import OCR_TEXT_DECLARATIONS
from .snapshot import FLAT_TREE_JS, HARVEST_MARKERS, READING_ORDER_JS

VISUAL_SELECTOR = "img, svg, canvas, video, [role='img'], iframe, [data-webshot-background-asset='true']"
OCR_PDF_PREFIX = "Text recognized within visual asset"

#: `BASE_PRINT_CSS`'s rule for recognized text, for an element it cannot reach.
OCR_TEXT_STYLE = "; ".join(OCR_TEXT_DECLARATIONS)

#: Clean every tree the page can reach, then list its visuals in reading order.
#:
#: The trees are the snapshot's: the top document, each open shadow root, and
#: the document of each frame the page can script, recursively. Visuals were
#: looked for in the top document only, so an image, chart or canvas in any of
#: the others had no asset, no OCR and no warning, while the snapshot carried
#: its alt text and the PDF printed it (docs/09 P16-9).
#:
#: The page's own copies of `HARVEST_MARKERS` go from every tree first, hidden
#: frames included, because the snapshot trusts ours wherever it walks. The
#: list then follows `READING_ORDER_JS`, the walk `HEADING_JS` makes, so a
#: visual's `nearby_heading` is the heading every other stage would give it.
#: On a page with no shadow root or frame, that order and those headings are
#: what `document.querySelectorAll` gave before. Each frame entered gets a
#: `data-webshot-frame-index`, which is how Python reaches into it.
HARVEST_JS = """({selector, owned}) => {
    const flat = __FLAT_TREE__;
    const inOrder = __READING_ORDER__;
    const trees = flat.trees(document);
    for (const tree of trees) {
        for (const attribute of owned) {
            for (const element of tree.querySelectorAll(`[${attribute}]`)) {
                if (attribute === 'data-webshot-ocr-text') element.remove();
                else element.removeAttribute(attribute);
            }
        }
    }
    for (const tree of trees) {
        const scope = tree.nodeType === Node.DOCUMENT_NODE ? tree.body : tree;
        if (!scope) continue;
        for (const element of scope.querySelectorAll('*')) {
            const style = element.ownerDocument.defaultView.getComputedStyle(element);
            if (style.backgroundImage && style.backgroundImage !== 'none') {
                const rect = element.getBoundingClientRect();
                if (rect.width >= 32 && rect.height >= 24) {
                    element.setAttribute('data-webshot-background-asset', 'true');
                }
            }
        }
    }
    const visuals = [];
    let frames = 0;
    inOrder((node, heading, chain) => {
        if (node.matches(selector)) visuals.push({element: node, frames: chain, heading: heading.text});
        return false;
    }, (frame, body, chain) => {
        const key = String(frames);
        frames += 1;
        frame.setAttribute('data-webshot-frame-index', key);
        return [...chain, key];
    });
    visuals.forEach((visual, index) => {
        visual.element.setAttribute('data-webshot-visual-index', String(index));
    });
    return visuals.map(({frames, heading}) => ({frames, heading}));
}""".replace("__FLAT_TREE__", FLAT_TREE_JS).replace(
    "__READING_ORDER__", READING_ORDER_JS
)

#: Every scroll position a screenshot could move, in every tree: each
#: document's window, and each element with more content than box. Playwright
#: scrolls whatever it photographs into view, and Chromium prints a scrolling
#: box, and a frame, as they stand. So a chart below a box's fold left the box
#: printing its bottom, and a visual in a frame left the frame printing
#: wherever that visual was (docs/09 P16-9, P16-12). Kept as live references,
#: not markers, for `RESTORE_SCROLL_JS` to put back.
SCROLL_POSITIONS_JS = """() => {
    const flat = __FLAT_TREE__;
    const saved = [];
    for (const tree of flat.trees(document)) {
        const view = tree.nodeType === Node.DOCUMENT_NODE ? tree.defaultView : null;
        if (view) saved.push({view, x: view.scrollX, y: view.scrollY});
        for (const element of tree.querySelectorAll('*')) {
            if (element.scrollHeight > element.clientHeight
                    || element.scrollWidth > element.clientWidth) {
                saved.push({element, left: element.scrollLeft, top: element.scrollTop});
            }
        }
    }
    return saved;
}""".replace("__FLAT_TREE__", FLAT_TREE_JS)

#: Put back what `SCROLL_POSITIONS_JS` saved, and count what would not go back.
RESTORE_SCROLL_JS = """saved => {
    let failed = 0;
    for (const entry of saved) {
        try {
            if (entry.view) {
                if (entry.view.scrollX !== entry.x || entry.view.scrollY !== entry.y) {
                    entry.view.scrollTo(entry.x, entry.y);
                }
            } else if (entry.element.scrollLeft !== entry.left
                    || entry.element.scrollTop !== entry.top) {
                entry.element.scrollLeft = entry.left;
                entry.element.scrollTop = entry.top;
            }
        } catch {
            failed += 1;
        }
    }
    return failed;
}"""


def _scope(page: Page, frames: Sequence[str]) -> Page | FrameLocator:
    """The page, or the frame the harvest numbered, one frame at a time."""
    scope: Page | FrameLocator = page
    for key in frames:
        scope = scope.frame_locator(f"[data-webshot-frame-index='{key}']")
    return scope


#: The harvest's own test, for the candidates past `--max-assets`, which are
#: only counted: what Playwright's `is_visible` checks (`checkVisibility()`,
#: `visibility: visible`, a box) and the 32 by 24 floor. One call per frame,
#: because checking them one at a time, two round trips each, made a page of
#: 1,000 images take 13 seconds to harvest where it took 5 (docs/09 P20-3).
COUNT_SHOWN_FROM_JS = """(elements, first) => elements.filter(element => {
    if (Number(element.getAttribute('data-webshot-visual-index')) < first) return false;
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    // No box of its own, so `bounding_box` would have none either.
    if (style.display === 'contents') return false;
    if (!element.checkVisibility() || style.visibility !== 'visible') return false;
    const rect = element.getBoundingClientRect();
    return rect.width >= 32 && rect.height >= 24;
}).length"""


async def _count_shown_from(
    page: Page, found: Sequence[dict[str, Any]], first: int
) -> tuple[int, list[str]]:
    """How many candidates from `first` on are visual assets, and any failure."""
    chains = list(dict.fromkeys(tuple(visual["frames"]) for visual in found[first:]))
    shown, problems = 0, []
    for chain in chains:
        try:
            shown += await (
                _scope(page, chain)
                .locator("[data-webshot-visual-index]")
                .evaluate_all(COUNT_SHOWN_FROM_JS, first)
            )
        except PlaywrightError as exc:
            problems.append(str(exc).splitlines()[0])
    return shown, problems


def _locate(page: Page, frames: Sequence[str], index: int) -> Locator:
    """The visual the harvest numbered `index`, inside the frames it named.

    A CSS locator already crosses open shadow roots, and a frame locator is the
    only way into a frame's document.
    """
    return _scope(page, frames).locator(f"[data-webshot-visual-index='{index}']")


@dataclass(slots=True)
class VisualAsset:
    id: str
    file: str
    kind: str
    width: float
    height: float
    alt: str = ""
    aria_label: str = ""
    title: str = ""
    caption: str = ""
    source_url: str = ""
    nearby_heading: str = ""
    ocr: OCRResult = field(default_factory=OCRResult)
    sha256: str = ""
    #: For an iframe, which path its content took (docs/04-spec.md §5.6):
    #: `dom` when the snapshot read its document into content.html, so OCR here
    #: repeats text the bundle already has, and `screenshot` when this asset is
    #: all the bundle holds of it. None on every other kind. The harvest runs
    #: before the snapshot and records `screenshot`, which is what it took;
    #: `snapshot.with_frame_content` sets `dom` for a frame that was read.
    frame_content: Literal["dom", "screenshot"] | None = None


async def capture_visual_assets(
    page: Page,
    assets_directory: Path,
    *,
    max_assets: int,
    ocr: bool,
    ocr_language: str,
    ocr_psm: int,
    engine: OcrEngine,
    #: When true, a recognizer that cannot run — or runs and fails — ends the
    #: capture rather than adding a warning. Passed in rather than inferred:
    #: `--require-ocr` is the caller's promise about what the deliverable must
    #: contain, and this function is where the promise is either kept or
    #: quietly broken (docs/09 P8-44).
    require_ocr: bool = False,
) -> tuple[list[VisualAsset], list[str], int]:
    """Save the visuals the page shows, and say what was left out.

    Returns the saved assets, the warnings, and how many visuals the bundle
    does not hold although the page may show them: those past `--max-assets`,
    and those whose capture failed. A failure can come before the element was
    judged visible, so it is counted rather than assumed hidden. The
    empty-capture check reads this, because "no image" is false of a page
    whose pictures were capped away (docs/09 P22-2).
    """
    assets_directory.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    assets: list[VisualAsset] = []

    found: list[dict[str, Any]] = await page.evaluate(
        HARVEST_JS, {"selector": VISUAL_SELECTOR, "owned": list(HARVEST_MARKERS)}
    )
    positions = await page.evaluate_handle(SCROLL_POSITIONS_JS)
    # Asked once rather than once per asset (whether the engine can run here is
    # a property of the environment), and only once there is something to
    # recognize: starting RapidOCR means loading three ONNX models, which a
    # page with no visual assets should not pay for. Off the event loop for the
    # same reason, since the browser connection is live around it.
    unavailable: str | None = None
    asked = False
    #: Visuals the page shows that the cap left out. Counted, not just noticed:
    #: the warning says how many (docs/09 P20-3).
    over_cap = 0
    #: Why the candidates past the cap in a frame could not be counted.
    uncounted: list[str] = []
    #: Candidates whose capture raised, shown or not.
    capture_failures = 0

    for index in range(len(found)):
        element = _locate(page, found[index]["frames"], index)
        try:
            # The cap counts visuals the page shows, so a candidate is judged
            # before it takes a place. Capping the candidate list instead let
            # hidden and icon-sized elements fill every place and dropped the
            # visuals after them (docs/09 P20-3).
            if not await element.is_visible():
                continue
            box = await element.bounding_box()
            if not box or box["width"] < 32 or box["height"] < 24:
                continue
            if len(assets) >= max_assets:
                past, uncounted = await _count_shown_from(page, found, index + 1)
                over_cap = 1 + past
                break
            if ocr and not asked:
                asked = True
                unavailable = await asyncio.to_thread(
                    functools.partial(engine.unavailable_reason, language=ocr_language)
                )
                if unavailable:
                    # Missing recognition degrades the capture to a manifest
                    # warning; it never fails it (docs/04-spec.md §5.7),
                    # unless the caller said it must not, in which case this
                    # is the failure `--require-ocr` names.
                    if require_ocr:
                        raise OcrUnavailableError(
                            f"{unavailable} --require-ocr was given, so missing "
                            "recognition is a failure rather than a warning."
                        )
                    warnings.append(unavailable)
            recognize = ocr and unavailable is None
            metadata = await element.evaluate(
                """element => {
                    const figure = element.closest('figure');
                    return {
                        kind: element.tagName.toLowerCase(),
                        alt: element.getAttribute('alt') || '',
                        ariaLabel: element.getAttribute('aria-label') || '',
                        title: element.getAttribute('title') || '',
                        caption: figure?.querySelector('figcaption')?.innerText?.trim() || '',
                        sourceUrl: element.currentSrc || element.src || element.getAttribute('src') || (getComputedStyle(element).backgroundImage !== 'none' ? getComputedStyle(element).backgroundImage : '')
                    };
                }"""
            )
            asset_id = f"asset-{len(assets) + 1:03d}"
            image_path = assets_directory / f"{asset_id}.png"
            rendered_canvas = ""
            if metadata["kind"] == "canvas":
                rendered_canvas = await element.evaluate(
                    "canvas => canvas.toDataURL('image/png')"
                )
                _, encoded = rendered_canvas.split(",", 1)
                image_path.write_bytes(base64.b64decode(encoded))
            else:
                await element.screenshot(
                    path=image_path,
                    animations="disabled",
                    caret="hide",
                    scale="device",
                    timeout=30_000,
                )
            ocr_result = OCRResult()
            if recognize:
                try:
                    ocr_result = await engine.recognize(
                        image_path, language=ocr_language, psm=ocr_psm
                    )
                except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                    # Same rule one level down. The preflight asks whether the
                    # engine *can* run; this is what happens when it could and
                    # then did not, and under `--require-ocr` swallowing it
                    # produced a successful capture with no recognized text —
                    # which is the outcome the flag exists to prevent
                    # (docs/09 P8-44).
                    if require_ocr:
                        raise OcrUnavailableError(
                            f"OCR failed for {asset_id}: {exc} --require-ocr "
                            "was given, so this is a failure rather than a "
                            "warning."
                        ) from exc
                    warnings.append(f"OCR failed for {asset_id}: {exc}")

            asset = VisualAsset(
                id=asset_id,
                file=f"assets/{image_path.name}",
                kind=metadata["kind"],
                width=round(box["width"], 2),
                height=round(box["height"], 2),
                alt=metadata["alt"],
                aria_label=metadata["ariaLabel"],
                title=metadata["title"],
                caption=metadata["caption"],
                source_url=metadata["sourceUrl"],
                nearby_heading=found[index]["heading"],
                ocr=ocr_result,
                sha256=sha256_file(image_path),
                frame_content="screenshot" if metadata["kind"] == "iframe" else None,
            )
            assets.append(asset)
            if asset.kind == "video":
                warnings.append(
                    f"{asset_id} is a video. Its current visual frame was preserved; temporal and audio content require a source transcript or separate media processing."
                )
            # No warning for an iframe here. Every iframe had one, saying its
            # content "may only be available through the screenshot", which a
            # frame the snapshot reads is not. Only the snapshot, which runs
            # after this, knows which frames it could not read, and
            # `unread_text_warnings` names each of those with this asset's id
            # (docs/09 P16-7).

            description_parts = [
                part
                for part in (asset.alt, asset.aria_label, asset.caption, asset.title)
                if part
            ]
            if ocr_result.text:
                description_parts.append(f"{OCR_PDF_PREFIX}: {ocr_result.text}")
            description = " | ".join(dict.fromkeys(description_parts))[:4000]
            await element.evaluate(
                """(element, data) => {
                    element.setAttribute('data-webshot-asset-id', data.id);
                    if (data.description) {
                        if (element.tagName === 'IMG' && !element.getAttribute('alt')) {
                            element.setAttribute('alt', data.description);
                        }
                        if (!element.getAttribute('aria-label')) element.setAttribute('aria-label', data.description);
                        else if (!element.getAttribute('aria-description')) element.setAttribute('aria-description', data.description);
                    }
                    if (data.ocrText) {
                        const span = document.createElement('span');
                        span.setAttribute('data-webshot-ocr-text', 'true');
                        span.textContent = data.ocrText;
                        // The print stylesheet that keeps this in the text
                        // layer and off the page is the top document's, and
                        // reaches no shadow root and no other document.
                        let top = null;
                        try { top = window.top.document; } catch {}
                        if (element.getRootNode() !== top) span.setAttribute('style', data.ocrStyle);
                        element.insertAdjacentElement('afterend', span);
                    }
                    if (data.renderedCanvas && element.tagName === 'CANVAS') {
                        const image = document.createElement('img');
                        image.src = data.renderedCanvas;
                        image.width = element.width;
                        image.height = element.height;
                        image.className = element.className;
                        // The placeholder alt is for the PDF's structure
                        // tree. The marker says it is WebShot's, so the
                        // snapshot does not carry it as page text (P20-1).
                        image.setAttribute('data-webshot-rendered-canvas', data.description ? 'true' : 'placeholder');
                        image.setAttribute('data-webshot-asset-id', data.id);
                        image.setAttribute('alt', data.description || 'Rendered canvas visual');
                        if (data.description) image.setAttribute('aria-label', data.description);
                        if (data.description) image.setAttribute('aria-description', data.description);
                        element.replaceWith(image);
                    }
                }""",
                {
                    "id": asset_id,
                    "description": description,
                    "ocrText": f"{OCR_PDF_PREFIX} {asset_id}: {ocr_result.text}"
                    if ocr_result.text
                    else "",
                    "renderedCanvas": rendered_canvas,
                    "ocrStyle": OCR_TEXT_STYLE,
                },
            )
        except PlaywrightError as exc:
            capture_failures += 1
            warnings.append(f"Could not capture visual element {index + 1}: {exc}")
    if over_cap:
        # "At least" when a frame could not be counted: the number is then a
        # floor, and saying it plainly would make it read as the total.
        floor = "at least " if uncounted else ""
        warnings.append(
            f"The page shows {floor}{over_cap} more visual "
            f"asset{'' if over_cap == 1 else 's'} than the limit of {max_assets} "
            f"(--max-assets); {'it was' if over_cap == 1 else 'they were'} not "
            "captured."
        )
    if uncounted:
        warnings.append(
            f"The visuals past --max-assets in {len(uncounted)} frame"
            f"{'' if len(uncounted) == 1 else 's'} could not be counted: "
            f"{uncounted[0]}"
        )
    try:
        failed = await positions.evaluate(RESTORE_SCROLL_JS)
        problem = f"{failed} would not move" if failed else ""
    except PlaywrightError as exc:
        problem = f"the restore failed: {exc}"
    finally:
        await positions.dispose()
    if problem:
        warnings.append(
            "Scroll positions moved while capturing visual assets were not all "
            f"put back ({problem}), so a scrolling box or a frame may print "
            "scrolled to a visual rather than where the page had it."
        )
    return assets, warnings, over_cap + capture_failures
