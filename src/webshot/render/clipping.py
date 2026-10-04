"""Find text the PDF loses at the page edge or in a scrolling box, and say so.

Chromium lays a page out at the width of the paper it prints on, and whatever
a print stylesheet places past that width is clipped: gone from the render and
from the text layer alike. The bundle is read from the DOM and keeps every
word. So a capture can publish a PDF that says less than the bundle beside it,
and before this check nothing in the manifest or the exit code said so. A
university's Bootstrap print stylesheet did exactly that to a quarter of a
page's words.

A box that scrolls on screen loses text the same way. A wide table in an
`overflow-x: auto` wrapper prints its first ten columns of thirty, and a log in
a `max-height` box prints three rows of thirty. The PDF cannot be scrolled, and
the bundle has every cell and row (docs/09 P16-8). That is reported separately,
because the fix is different: the box has to wrap or grow, not the page.

This module measures it *after* the render and reports it as a warning. Two
decisions in that are deliberate:

- *It warns and does not correct.* The layouts that clip are not one property
  on one element. A pushed column, a pulled sidebar beside it, a centred
  container that moves right as the page widens: a generic neutraliser has to
  guess which of these to undo, and a wrong guess (a pull left in place after
  its push was undone) stacks two columns on each other. Overlapped text is
  inside the page, so no check here could see it — it would trade a defect
  this module reports for one nothing reports. `--css` is the remedy, written
  by someone who can see the page (docs/09 P16-4).
- *It asks Chromium for the width rather than computing it.* The paper size,
  margins and scale give a first answer, and it is wrong in two common cases:
  a page's own `@page` margin changes the width it is laid out at even when
  `--prefer-css-page-size` is off, and a page wider than the paper is laid out
  again up to half as wide once more, then scaled down to fit. So one page of
  the capture is printed a second time with a probe that writes its own width
  into the text layer, and that number is the edge (docs/09 P16-4).

Running after the render is what keeps the check from changing what it checks:
the probe prints the page again and the measurement resizes the viewport, and
neither can reach a PDF that has already been written.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame
from playwright.async_api import Page as AsyncPage
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from ..config import CaptureOptions
from .pdf import print_geometry

LOGGER = logging.getLogger("webshot")

#: How far past an edge, in CSS pixels, a line must reach before it counts as
#: cut. A line set flush to the edge measures a fraction past it, and a finding
#: that fires on rounding would record a loss the render does not have.
EDGE_SLACK_PX = 1.0

PROBE_ID = "webshot-print-width-probe"
PROBE_MARK = "WEBSHOTPRINTWIDTH"
PROBE_RE = re.compile(rf"{PROBE_MARK}\s*(\d+)\s*{PROBE_MARK}")

#: A fixed element spans the page area in print, and `100cqw / 1px` is that
#: span as a number a counter can print. Everything is `!important` and set
#: through the CSSOM or an adopted sheet: a page's print CSS must not be able to
#: hide or resize the probe, and a `style-src` policy blocks a style attribute
#: or a `<style>` element but not these. Returns false if a probe could not be
#: installed, which the caller reports as a check that did not run.
INSTALL_PROBE_SCRIPT = r"""([id, mark]) => {
    if (!document.documentElement || !window.CSSStyleSheet || !document.adoptedStyleSheets) return false;
    const probe = document.createElement('div');
    probe.id = id;
    for (const [name, value] of [
        ['display', 'block'], ['position', 'fixed'], ['left', '0'], ['right', '0'],
        ['top', '0'], ['width', 'auto'], ['height', 'auto'], ['margin', '0'],
        ['padding', '0'], ['border', '0'], ['transform', 'none'],
        ['visibility', 'visible'], ['container-type', 'inline-size'],
    ]) probe.style.setProperty(name, value, 'important');
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`#${id}::before {
        display: inline !important; visibility: visible !important;
        counter-reset: webshot-width calc(100cqw / 1px) !important;
        content: "${mark}" counter(webshot-width) "${mark}" !important;
        color: #000 !important; font: 8px/1 monospace !important;
        letter-spacing: 0 !important; word-spacing: 0 !important;
    }`);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    document.documentElement.appendChild(probe);
    window.__webshotPrintWidthSheet = sheet;
    return true;
}"""

REMOVE_PROBE_SCRIPT = r"""(id) => {
    document.getElementById(id)?.remove();
    const sheet = window.__webshotPrintWidthSheet;
    if (sheet) {
        document.adoptedStyleSheets = document.adoptedStyleSheets.filter(s => s !== sheet);
        delete window.__webshotPrintWidthSheet;
    }
}"""

#: Two tallies from one pass over the text, each counted once per element, with
#: a selector for the box to fix around the first of them.
#:
#: *Hidden by design.* A line wholly outside the box of an `overflow: hidden` or
#: `clip` ancestor — a carousel's other slides, a collapsed panel — is hidden
#: on screen as well, and counts in neither tally. Wholly, because a line that
#: only crosses such a box is the layout running over it, not the box choosing
#: what to show (docs/09 P16-8).
#:
#: The page is given as bounds in the document's own coordinates: the edges
#: that cut (`null` where nothing does) and the span that is visible. On the
#: page itself they are [0, width] and [0, width]. In a frame they are the
#: printed page's edges moved into the frame's coordinates, and an edge cuts
#: only where it falls inside the frame's own box (docs/09 P16-19).
#:
#: *Page edge.* A line that *crosses* the left or right edge of the page is cut
#: mid-line, and counts. A line wholly past an edge counts
#: only when it belongs to something the page shows — an ancestor that crosses
#: the edge — and no container of its own clips it first. Without that a closed
#: off-canvas menu is a finding. It is layout the page intends at any width.
#:
#: *Scrolling box.* A line past the visible area of an `overflow: auto` or
#: `scroll` box that actually overflows — a wide table's right-hand columns, a
#: log's later rows — is something a reader reaches by scrolling and a PDF
#: cannot. It counts on either axis, and is not also counted at the page edge:
#: the box hid it first.
CLIPPED_TEXT_SCRIPT = r"""([cutLeft, cutRight, viewLeft, viewRight, slack]) => {
    const scrollX = window.scrollX;
    const lo = cutLeft ?? -Infinity;
    const hi = cutRight ?? Infinity;
    const body = document.body || document.documentElement;
    const top = node => node === document.body || node === document.documentElement;
    // The composed tree: a slotted node is laid out where its slot is.
    const up = node => node.assignedSlot || node.parentElement
        || (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);
    const boxes = new Map();
    const box = element => {
        let found = boxes.get(element);
        if (!found) {
            const rect = element.getBoundingClientRect();
            found = {left: rect.left + scrollX, right: rect.right + scrollX,
                     width: rect.width, clips: getComputedStyle(element).overflowX !== 'visible'};
            boxes.set(element, found);
        }
        return found;
    };
    const crosses = b => b.width > 0 && (
        (b.left < hi - slack && b.right > hi + slack) || (b.left < lo - slack && b.right > lo + slack));
    const past = b => b.right > hi + slack || b.left < lo - slack;
    const attached = (start, rightSide) => {
        let crossing = false;
        for (let node = start; node && !top(node); node = up(node)) {
            const b = box(node);
            if (crosses(b)) crossing = true;
            const inside = rightSide ? b.right <= hi + slack : b.left >= lo - slack;
            if (b.clips && b.width > 0 && inside) return false;
        }
        return crossing;
    };
    // How an element clips each axis: `hide` if it hides overflow from the
    // screen too, `scroll` if it overflows and a reader can scroll to the rest,
    // or null. With its padding box, in viewport coordinates like the line
    // rectangles it is compared with. A scrolling box parked off the page
    // (`left: -9999px`) is hidden from the screen whole, so what it holds is
    // hidden by design, like `overflow: hidden` (docs/09 P16-15).
    const clippers = new Map();
    const clipper = element => {
        if (clippers.has(element)) return clippers.get(element);
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const onPage = rect.right + scrollX > viewLeft + slack && rect.left + scrollX < viewRight - slack
            && rect.bottom + window.scrollY > slack;
        const kind = (value, overflowing) =>
            value === 'hidden' || value === 'clip' ? 'hide'
            : (value === 'auto' || value === 'scroll') && overflowing ? (onPage ? 'scroll' : 'hide') : null;
        const x = kind(style.overflowX, element.scrollWidth > element.clientWidth + 1);
        const y = kind(style.overflowY, element.scrollHeight > element.clientHeight + 1);
        let found = null;
        if (x || y) {
            const left = rect.left + element.clientLeft;
            const upper = rect.top + element.clientTop;
            found = {element, x, y, left, right: left + element.clientWidth,
                     top: upper, bottom: upper + element.clientHeight};
        }
        clippers.set(element, found);
        return found;
    };
    // `hidden` when a box hides the line from the screen too, else the
    // innermost scrolling box it overflows, else null.
    const hiddenBy = (owner, rect) => {
        let scrolledIn = null;
        for (let node = owner; node && !top(node); node = up(node)) {
            const c = clipper(node);
            if (!c) continue;
            const outsideX = rect.right <= c.left + slack || rect.left >= c.right - slack;
            const outsideY = rect.bottom <= c.top + slack || rect.top >= c.bottom - slack;
            if ((c.x === 'hide' && outsideX) || (c.y === 'hide' && outsideY)) return 'hidden';
            if (scrolledIn) continue;
            const pastX = rect.right > c.right + slack || rect.left < c.left - slack;
            const pastY = rect.bottom > c.bottom + slack || rect.top < c.top - slack;
            if ((c.x === 'scroll' && pastX) || (c.y === 'scroll' && pastY)) scrolledIn = c;
        }
        return scrolledIn;
    };
    const range = document.createRange();
    const cut = [];
    const scrolled = [];
    const cutOwners = new Set();
    const scrolledOwners = new Set();
    const visit = root => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            if (node.nodeType === Node.ELEMENT_NODE) {
                if (node.shadowRoot) visit(node.shadowRoot);
                continue;
            }
            const owner = up(node);
            if (!owner || !node.data.trim()) continue;
            let edgeDone = cutOwners.has(owner);
            let scrollDone = scrolledOwners.has(owner);
            if (edgeDone && scrollDone) continue;
            // WebShot's own recognized-text layer, sized for search rather than
            // for the page.
            if (owner.closest('[data-webshot-ocr-text="true"]')) continue;
            let visible = null;
            range.selectNodeContents(node);
            for (const rect of range.getClientRects()) {
                if (rect.width <= 0 || rect.height <= 0) continue;
                const hider = hiddenBy(owner, rect);
                if (hider === 'hidden') continue;
                if (!hider) {
                    if (edgeDone) continue;
                    const line = {left: rect.left + scrollX, right: rect.right + scrollX, width: rect.width};
                    if (!past(line)) continue;
                    if (!crosses(line) && !attached(owner, line.right > hi + slack)) continue;
                } else if (scrollDone) continue;
                if (visible === null) visible = getComputedStyle(owner).visibility === 'visible';
                if (!visible) break;
                if (hider) { scrolledOwners.add(owner); scrolled.push(hider.element); scrollDone = true; }
                else { cutOwners.add(owner); cut.push(owner); edgeDone = true; }
                if (edgeDone && scrollDone) break;
            }
        }
    };
    visit(body);
    // Classes even beside an id: on a grid layout they name the rule to undo
    // (`col-sm-push-4`), which the id does not.
    const step = element => {
        const id = element.id ? `#${CSS.escape(element.id)}` : '';
        const classes = [...element.classList].slice(0, 3).map(name => `.${CSS.escape(name)}`);
        return element.localName + id + classes.join('');
    };
    const describe = element => {
        const parent = up(element);
        const selector = element.id || !parent || top(parent) ? step(element) : `${step(parent)} > ${step(element)}`;
        return selector.length > 160 ? `${selector.slice(0, 159)}…` : selector;
    };
    let sample = null;
    if (cut.length) {
        let outer = cut[0];
        let running = crosses(box(outer));
        for (let node = up(outer); node && !top(node); node = up(node)) {
            if (crosses(box(node))) { outer = node; running = true; }
            else if (running) break;
        }
        sample = describe(outer);
    }
    return {count: cut.length, sample, scrolled: scrolled.length,
            scrolledSample: scrolled.length ? describe(scrolled[0]) : null};
}"""


@dataclass(frozen=True, slots=True)
class PrintClipping:
    """What the check found, or that it could not look (docs/09 P16-1, P16-8).

    `width` is `None` when the printed layout could not be measured. That is
    a check that did not run, and it is reported as one — never as a page with
    nothing cut.
    """

    #: The width Chromium laid the page out at, in CSS pixels.
    width: int | None
    #: Elements with at least one line of their own text cut off at the edge.
    elements: int = 0
    #: The outermost box running off the page around the first of them — the
    #: thing a `--css` fix has to reflow.
    sample: str | None = None
    #: Elements with text that a scrolling box shows only when scrolled.
    scrolled: int = 0
    #: The scrolling box around the first of them.
    scrolled_sample: str | None = None
    #: Frames from another origin whose release, checked against the print,
    #: did not hold, each with what went wrong (docs/09 P16-21).
    unheld: tuple[str, ...] = ()


def clipping_warnings(clipping: PrintClipping) -> list[str]:
    """The manifest warnings for a measurement: one per kind of loss found."""
    if clipping.width is None:
        return [
            "WebShot could not measure the layout the page was printed at, so "
            "it did not check whether the PDF cuts text off at the page edge "
            "or inside a scrolling box."
        ]
    warnings = []
    if clipping.elements:
        warnings.append(
            f"Text runs off the printed page: {clipping.elements} element(s) "
            f"have lines past the page edge (in {clipping.sample}), and the PDF "
            "cuts them off, so its render and text layer hold less than the "
            "page does. A --css stylesheet that reflows that layout for print "
            "keeps them on the page."
        )
    if clipping.scrolled:
        warnings.append(
            f"Text is hidden in a scrolling box: {clipping.scrolled} element(s) "
            f"have text that {clipping.scrolled_sample} shows only when "
            "scrolled, and a PDF cannot scroll, so its render and text layer "
            "hold less than the page does. A --css stylesheet that lets that "
            "box wrap or grow for print keeps the text on the page."
        )
    for failure in clipping.unheld:
        warnings.append(
            f"WebShot released scrolling boxes inside {failure}, and checking the "
            "print showed the release did not hold. The frame is from another "
            "origin, so --css cannot reach it."
        )
    return warnings


async def printed_width(page: AsyncPage, options: CaptureOptions) -> int | None:
    """The width, in CSS pixels, that Chromium lays this page out at to print it.

    One page is printed with a probe that writes its own width into the text
    layer. Only the first page, because the width is the document's, and
    untagged, because nothing reads this file but the line below.
    """
    try:
        if not await page.evaluate(INSTALL_PROBE_SCRIPT, [PROBE_ID, PROBE_MARK]):
            return None
        try:
            pdf = await page.pdf(
                **print_geometry(options),
                page_ranges="1",
                tagged=False,
                outline=False,
            )
        finally:
            await page.evaluate(REMOVE_PROBE_SCRIPT, PROBE_ID)
        text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    except (PlaywrightError, PyPdfError, IndexError) as exc:
        LOGGER.debug("The print-width probe failed: %s", exc)
        return None
    match = PROBE_RE.search(text)
    # A counter that could not be set prints 0, so zero is no answer either.
    if not match or int(match.group(1)) <= 0:
        return None
    return int(match.group(1))


async def find_clipped_text(page: AsyncPage, options: CaptureOptions) -> PrintClipping:
    """Measure which elements the printed page cuts off, at its edge or in a scrolling box.

    The page is laid out at the printed width by narrowing the viewport, with
    the capture's media emulation still on, and put back afterwards: the journey
    walker goes on using this page. The one difference from the print itself is
    that media queries see the width the page is laid out at, where Chromium's
    print evaluates them at the paper's width before any `@page` margin or
    shrink-to-fit. The two agree unless a breakpoint lies between them.
    """
    viewport = page.viewport_size
    if viewport is None:
        # A context without a fixed viewport cannot be narrowed and restored.
        return PrintClipping(width=None)
    width = await printed_width(page, options)
    if width is None:
        return PrintClipping(width=None)
    try:
        await page.set_viewport_size({"width": width, "height": viewport["height"]})
        found = await page.evaluate(
            CLIPPED_TEXT_SCRIPT, [0, width, 0, width, EDGE_SLACK_PX]
        )
        framed = await _measure_frames(page, width)
    except PlaywrightError as exc:
        LOGGER.debug("Measuring the printed layout failed: %s", exc)
        return PrintClipping(width=None)
    finally:
        await page.set_viewport_size(viewport)
    return PrintClipping(
        width=width,
        elements=int(found["count"]) + framed.elements,
        sample=found["sample"] or framed.sample,
        scrolled=int(found["scrolled"]) + framed.scrolled,
        scrolled_sample=found["scrolledSample"] or framed.scrolled_sample,
        unheld=framed.unheld,
    )


#: The frame element as a selector, or null when the frame does not print: it
#: sits inside what the capture removes, or has no box at all.
FRAME_ELEMENT_SCRIPT = r"""element => {
    if (element.closest('[data-webshot-hidden="true"]') || !element.getClientRects().length) return null;
    const id = element.id ? `#${CSS.escape(element.id)}` : '';
    const classes = [...element.classList].slice(0, 3).map(name => `.${CSS.escape(name)}`).join('');
    const title = element.getAttribute('title');
    const named = !id && !classes && title ? `[title=${JSON.stringify(title.slice(0, 60))}]` : '';
    return element.localName + id + classes + named;
}"""


#: How many boxes the release marked in this frame now run past the frame's own
#: bottom edge, or null when this is not a frame from another origin that was
#: released. `release_clamped_text` runs the same test to decide; this runs it
#: again on the layout that printed, to confirm (docs/09 P16-21).
RELEASED_BOXES_PAST_FRAME_SCRIPT = """() => {
    const root = document.documentElement;
    if (!root.hasAttribute('data-webshot-frame-released')) return null;
    const limit = scrollY + root.clientHeight + 1;
    return [...document.querySelectorAll('[data-webshot-clamped]')]
        .filter(element => element.getBoundingClientRect().bottom + scrollY > limit).length;
}"""


#: How far the frame's content box starts inside its element's border box: the
#: border and the padding, which `bounding_box` does not include.
FRAME_OFFSET_SCRIPT = """element => {
    const style = getComputedStyle(element);
    return element.clientLeft + parseFloat(style.paddingLeft || '0');
}"""


async def _measure_frames(page: AsyncPage, width: int) -> PrintClipping:
    """Both tallies inside the page's frames, as one `PrintClipping` to add in.

    Every frame that prints is measured, those from another origin included:
    measuring is read-only, and the release cannot touch those frames, so this
    is the only place their loss can show (docs/09 P16-18). A frame that cuts
    off its own document is the frame-fitting work's to report (P16-16), so
    each frame is measured against the printed page's edges only where they
    fall inside its own box, and past its own box nothing counts (P16-19).
    """
    elements = scrolled = 0
    sample: str | None = None
    scrolled_sample: str | None = None
    unheld: list[str] = []
    page_scroll = float(await page.evaluate("() => scrollX"))
    for frame in page.frames[1:]:
        placed = await printed_frame(frame)
        if placed is None:
            continue
        label, left_on_page = placed
        try:
            view = await frame.evaluate(
                "() => ({x: scrollX, width: document.documentElement.clientWidth})"
            )
            # The printed page's edges, in this frame's document coordinates.
            offset = left_on_page + page_scroll
            page_left = -offset + view["x"]
            page_right = width - offset + view["x"]
            frame_left, frame_right = view["x"], view["x"] + view["width"]
            visible = [max(page_left, frame_left), min(page_right, frame_right)]
            if visible[1] <= visible[0]:
                continue
            cut_left = page_left if page_left > frame_left + EDGE_SLACK_PX else None
            cut_right = page_right if page_right < frame_right - EDGE_SLACK_PX else None
            found = await frame.evaluate(
                CLIPPED_TEXT_SCRIPT, [cut_left, cut_right, *visible, EDGE_SLACK_PX]
            )
        except PlaywrightError as exc:
            # Detached between being listed and being measured: not printed.
            LOGGER.debug("A frame could not be measured: %s", exc)
            continue
        elements += int(found["count"])
        scrolled += int(found["scrolled"])
        # A frame from another origin that `release_clamped_text` chose to
        # release is checked here against the layout that printed: its released
        # boxes must sit inside the frame's own box, and no scrolling box there
        # may still hide text (docs/09 P16-21).
        try:
            past = await frame.evaluate(RELEASED_BOXES_PAST_FRAME_SCRIPT)
        except PlaywrightError:
            past = None
        if past is not None:
            reasons = []
            if past:
                reasons.append(
                    f"{past} released box(es) run past the frame's own bottom edge"
                )
            if found["scrolled"]:
                reasons.append(
                    f"{found['scrolled']} element(s) still hide text in a scrolling box"
                )
            if reasons:
                unheld.append(f"{label} ({'; '.join(reasons)})")
        if sample is None and found["sample"]:
            sample = f"{found['sample']} in {label}"
        if scrolled_sample is None and found["scrolledSample"]:
            scrolled_sample = f"{found['scrolledSample']} in {label}"
    return PrintClipping(
        width=width,
        elements=elements,
        sample=sample,
        scrolled=scrolled,
        scrolled_sample=scrolled_sample,
        unheld=tuple(unheld),
    )


async def printed_frame(frame: Frame) -> tuple[str, float] | None:
    """The frame's label and its content box's left edge on the page.

    `None` if it, or a frame above it, does not print: inside what the capture
    removes, or without a box.
    """
    label: str | None = None
    left: float | None = None
    node = frame
    try:
        while node.parent_frame is not None:
            handle = await node.frame_element()
            try:
                step = await handle.evaluate(FRAME_ELEMENT_SCRIPT)
                if step is not None and left is None:
                    box = await handle.bounding_box()
                    if box is None:
                        return None
                    left = box["x"] + float(await handle.evaluate(FRAME_OFFSET_SCRIPT))
            finally:
                await handle.dispose()
            if step is None:
                return None
            label = label or step
            node = node.parent_frame
    except PlaywrightError:
        return None
    if label is None or left is None:
        return None
    return label, left
