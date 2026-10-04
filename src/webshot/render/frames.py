"""Fit each frame to its document before the print, and say what still does not fit.

A frame prints its box, not its document: Chromium draws what the frame shows,
and the rest of a taller or wider document is missing from the PDF. The
snapshot reads a frame the page can script whole, so the bundle has that text
and the PDF did not, and nothing said so (docs/09 P16-6, P16-16).

So before the bundle is built, each such frame is grown to its document's
height, at the width the page prints at. It is grown on trial: the frame
must then hold its document, stay in the flow, and push what follows down
rather than cover it. A frame the reader cannot scroll (`scrolling="no"`, or
a document whose overflow is hidden) shows what its author chose, and is not
grown. What still does not fit is a manifest warning naming the frame: a
trial that failed, text that runs off to the side, and text a frame will not
scroll to, which the bundle holds and neither the page nor the PDF shows. A
frame whose document the page cannot read is already named by the snapshot's
own warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as AsyncPage

from ..capture.snapshot import FLAT_TREE_JS, LOCATOR_JS, VISIBLE_JS
from ..config import CaptureOptions
from .clipping import EDGE_SLACK_PX, printed_width

LOGGER = logging.getLogger("webshot")

#: Every frame the page can script and shows, anywhere: in the top document, in
#: an open shadow root, or in another such frame. Innermost first, because an
#: inner frame's growth is part of the height its outer frame must reach.
_FRAMES_JS = """
    const flat = __FLAT_TREE__;
    const visible = __VISIBLE__;
    const frames = [];
    const collect = (root, depth) => {
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) collect(element.shadowRoot, depth);
            if (!flat.isFrame(element) || !visible(element)) continue;
            const body = flat.frameBody(element);
            if (!body) continue;
            frames.push({frame: element, document: body.ownerDocument, depth});
            collect(body.ownerDocument, depth + 1);
        }
    };
    collect(document, 0);
    frames.sort((a, b) => b.depth - a.depth);
""".replace("__FLAT_TREE__", FLAT_TREE_JS).replace("__VISIBLE__", VISIBLE_JS)

#: Whether any frame would need fitting at all: one the page can script, shown,
#: whose document holds anything. Asked first, so a page without one pays
#: nothing, not even the print-width probe.
HAS_FRAME_CONTENT_JS = (
    "() => {"
    + _FRAMES_JS
    + """
    return frames.some(({document}) => (document.body.textContent || '').trim() !== ''
        || document.body.querySelector('*') !== null);
}"""
)

#: Grow each frame on trial, then list the text that still sits outside a frame
#: a reader could scroll, by frame, below its box and beside it.
FIT_FRAMES_JS = (
    "(slack) => {"
    + _FRAMES_JS
    + """
    const locator = __LOCATOR__;
    const skipped = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    // The axes a reader can scroll: what the author hid stays hidden.
    const scrollable = ({frame, document}) => {
        if ((frame.getAttribute('scrolling') || '').toLowerCase() === 'no') {
            return {x: false, y: false};
        }
        const view = document.defaultView;
        const hidden = axis => [document.documentElement, document.body].some(
            element => element && ['hidden', 'clip'].includes(view.getComputedStyle(element)[axis]));
        return {x: !hidden('overflowX'), y: !hidden('overflowY')};
    };
    const outerOf = node => node.parentElement || node.getRootNode().host || null;
    const bottom = node => node.getBoundingClientRect().bottom;
    const restore = (element, saved) => {
        // Read first. Chromium writes a changed inline style back to the
        // attribute lazily, so removing it straight after the trial left
        // `style=""` behind; the page gets back exactly what it had.
        void element.getAttribute('style');
        if (saved === null) element.removeAttribute('style');
        else element.setAttribute('style', saved);
    };
    let released = 0;
    for (const entry of frames) {
        const {frame, document} = entry;
        const style = frame.ownerDocument.defaultView.getComputedStyle(frame);
        if (!scrollable(entry).y || style.position === 'absolute' || style.position === 'fixed') continue;
        const scroller = document.scrollingElement || document.documentElement;
        const overflow = scroller.scrollHeight - scroller.clientHeight;
        if (overflow <= 1) continue;
        const saved = frame.getAttribute('style');
        const parent = outerOf(frame);
        const before = {self: frame.getBoundingClientRect().height,
                        parent: parent ? parent.getBoundingClientRect().height : 0};
        frame.style.setProperty('height', `${parseFloat(style.height) + overflow}px`, 'important');
        const grown = frame.getBoundingClientRect().height - before.self;
        // It holds its document now: a document sized to its viewport (a
        // `100vh` shell) grows with the frame and never does.
        let safe = scroller.scrollHeight <= scroller.clientHeight + 1;
        // And what it grew by is room the page made, not text it now covers.
        if (safe && parent) safe = parent.getBoundingClientRect().height - before.parent >= grown - 1;
        for (let node = parent; safe && node && node !== frame.ownerDocument.body; node = outerOf(node)) {
            if (node.getClientRects().length && bottom(node) < bottom(frame) - 1) safe = false;
        }
        if (safe) {
            frame.setAttribute('data-webshot-clamped', 'frame');
            released += 1;
        } else {
            restore(frame, saved);
        }
    }
    // What is left: each line of text a frame shows none of, or only part of,
    // at the size it now prints at, sorted by whether a reader could scroll to
    // it. Text a frame will not scroll to is in the bundle, and neither the
    // page nor the PDF shows it, which is worth saying too. Shadow roots inside
    // the frame count; a frame inside it is measured as its own.
    const clipped = [];
    for (const entry of frames) {
        const axes = scrollable(entry);
        const {frame, document} = entry;
        const view = document.defaultView;
        const [width, height] = [view.innerWidth, view.innerHeight];
        const trees = [document];
        for (let index = 0; index < trees.length; index += 1) {
            for (const element of trees[index].querySelectorAll('*')) {
                if (element.shadowRoot) trees.push(element.shadowRoot);
            }
        }
        const counts = {below: 0, beside: 0, hiddenBelow: 0, hiddenBeside: 0};
        const range = document.createRange();
        for (const tree of trees) {
            const walker = document.createTreeWalker(tree, NodeFilter.SHOW_TEXT);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                const owner = node.parentElement;
                if (!node.data.trim() || !owner || skipped.has(owner.tagName)) continue;
                if (view.getComputedStyle(owner).visibility === 'hidden') continue;
                range.selectNodeContents(node);
                for (const rect of range.getClientRects()) {
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (rect.bottom > height + slack || rect.top < -slack) {
                        counts[axes.y ? 'below' : 'hiddenBelow'] += 1;
                    } else if (rect.right > width + slack || rect.left < -slack) {
                        counts[axes.x ? 'beside' : 'hiddenBeside'] += 1;
                    }
                }
            }
        }
        if (Object.values(counts).some(Boolean)) {
            const unscrolled = (frame.getAttribute('scrolling') || '').toLowerCase() === 'no'
                ? 'its `scrolling` is `no`' : 'its document hides its overflow';
            clipped.push({locator: locator(frame), unscrolled, ...counts});
        }
    }
    return {released, clipped};
}""".replace("__LOCATOR__", LOCATOR_JS)
)


@dataclass(slots=True)
class FrameFit:
    """What fitting the frames did, and what the manifest must say about it."""

    released: int = 0
    warnings: list[str] = field(default_factory=list)


def _lines(count: int) -> str:
    return f"{count} line{'' if count == 1 else 's'}"


def _outside(below: int, beside: int) -> str:
    """How much of a frame's text its box leaves out, and where, or nothing."""
    total = below + beside
    if not total:
        return ""
    sides = ", ".join(
        f"{count} {label}"
        for count, label in ((below, "above or below it"), (beside, "beside it"))
        if count
    )
    verb = "falls" if total == 1 else "fall"
    return f"{_lines(total)} of its text {verb} outside its box ({sides})"


async def fit_frames(page: AsyncPage, options: CaptureOptions) -> FrameFit:
    """Grow each frame the page can script to its document, at the printed width.

    Measured with the viewport narrowed to the width Chromium prints at, read
    back from Chromium as the clipping check does, and put back afterwards. A
    frame sized at the capture's width would be too short wherever the print is
    narrower, because the frame's text wraps into more lines there.
    """
    try:
        if not await page.evaluate(HAS_FRAME_CONTENT_JS):
            return FrameFit()
    except PlaywrightError as exc:
        return FrameFit(
            warnings=[_unmeasured(f"the frames could not be listed: {exc}")]
        )
    viewport = page.viewport_size
    if viewport is None:
        return FrameFit(
            warnings=[_unmeasured("the page has no fixed viewport to narrow")]
        )
    width = await printed_width(page, options)
    if width is None:
        return FrameFit(warnings=[_unmeasured("the printed width could not be read")])
    try:
        await page.set_viewport_size({"width": width, "height": viewport["height"]})
        found = await page.evaluate(FIT_FRAMES_JS, EDGE_SLACK_PX)
    except PlaywrightError as exc:
        return FrameFit(warnings=[_unmeasured(f"measuring failed: {exc}")])
    finally:
        await page.set_viewport_size(viewport)
    warnings = []
    for frame in found["clipped"]:
        where = f"The frame at `{frame['locator']}`"
        scrolled = _outside(frame["below"], frame["beside"])
        if scrolled:
            warnings.append(
                f"{where} scrolls, and at the printed width {scrolled}, so the "
                "PDF does not print that text. content.html and everything built from it "
                "hold the frame's whole text."
            )
        unscrolled = _outside(frame["hiddenBelow"], frame["hiddenBeside"])
        if unscrolled:
            warnings.append(
                f"{where} cannot be scrolled ({frame['unscrolled']}), and at the "
                f"printed width {unscrolled}, so neither the page nor the PDF "
                "shows that text. content.html and everything built from it hold the "
                "frame's whole text."
            )
    return FrameFit(released=int(found["released"]), warnings=warnings)


def _unmeasured(reason: str) -> str:
    return (
        f"Frames on this page could not be fitted for print ({reason}). A frame "
        "whose document is taller or wider than its box prints only its box, so "
        "text the bundle holds may be missing from the PDF."
    )
