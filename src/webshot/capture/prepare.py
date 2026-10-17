"""Prepare a live page for capture: load it fully, then remove what is not the document.

Everything here is deliberately done by *hiding* elements and adding print CSS
rather than rebuilding the DOM, so linked assets, inherited styles, and the
page's own structure survive into the PDF.

Both modes hide the consent managers' dialogs, and the manifest names each one
that was on screen; `clean` mode also hides page chrome and large fixed
overlays, and `faithful` mode nothing else (docs/04-spec.md §5.5, docs/09
P79). Capture is an act of record, so what was hidden is reported.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..errors import UsageError
from ..render.clipping import RELEASED_BOXES_PAST_FRAME_SCRIPT, printed_frame

LOGGER = logging.getLogger("webshot")

#: The share of Trafilatura's article vocabulary an element must contain
#: before it is accepted as the region holding that article.  High enough that
#: a sidebar quoting the lede cannot win; below 1.0 because Trafilatura
#: normalizes text as it reads (entities, whitespace, dropped inline markup),
#: so an exact vocabulary match is not something a real page produces.
MAIN_CONTENT_COVERAGE = 0.9

CONTENT_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".entry-content",
    ".post-content",
    ".article-content",
    ".content",
    "#content",
    "#main",
]

NOISE_SELECTORS = [
    "nav",
    "body > header",
    "body > footer",
    "body > aside",
    "[role='navigation']",
    "[role='search']",
    "[aria-label*='cookie' i]",
    "[class*='cookie-consent' i]",
    "[id*='cookie-consent' i]",
    "[class*='newsletter' i]",
    "[class*='subscribe' i]",
    ".sidebar",
    "#sidebar",
    ".comments",
    "#comments",
    ".related-posts",
    # Advertising. An ad network writes its units into the article itself, so
    # selecting the content region keeps them: on a statistics page, Mediavine's
    # video player ("My Latest Videos") sat between a calculator's result and
    # its verdict, and the text recognized in its ad frame's screenshot went
    # into the text layer there (docs/09 P23-9). Each selector names a unit by
    # what its vendor or the ad itself writes, never by an "ad" substring, which
    # matches "header", "download" and "loading".
    "#mv-first-video",  # Mediavine's in-article video slot (measured)
    "[id^='mv-player-']",  # its player, wherever the slot is not used
    "[id^='mv-ad-']",  # its ad container and its ad-report footer
    "iframe[src*='imasdk.googleapis.com']",  # Google's video-ad SDK frame (measured)
    "iframe[title='Advertisement' i]",  # how that frame names itself (measured)
    "video[title='Advertisement' i]",  # and the ad videos beside it (measured)
    "ins.adsbygoogle",  # an AdSense unit, as AdSense's tag writes it
    "[id^='div-gpt-ad']",  # a Google Publisher Tag slot, by its default id
    "[id^='google_ads_iframe']",  # the frame that slot fills
]

#: Consent managers' dialogs, by vendor, as each vendor's script writes them.
#: Hidden in both modes (docs/04-spec.md §5 item 5): the dialog is the
#: browser's session asking a question, not the page, and it is fixed to the
#: viewport, so Chromium prints it on every page over the text beneath it, and
#: its words are read into the bundle between the article's. Cookiebot's was
#: measured, printed on five of six pages of one article in clean mode as well
#: as faithful (docs/09 P79); the others follow their vendors' documented
#: markup and were not met in a capture. Each selector is a name the vendor
#: gave its own element, so none matches a page's own box the way the noise
#: list's `[class*='cookie-consent' i]` can, which is why that list stays
#: clean mode's alone.
CONSENT_MANAGERS: dict[str, tuple[str, ...]] = {
    "Cookiebot": ("#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay"),
    "OneTrust": ("#onetrust-consent-sdk",),
    "Didomi": ("#didomi-host",),
    "Quantcast Choice": (".qc-cmp2-container",),
    "TrustArc": ("#truste-consent-track",),
    "Usercentrics": ("#usercentrics-root", "#usercentrics-cmp-ui"),
    "Sourcepoint": ("[id^='sp_message_container']",),
}

#: The rules that act on `release_clamped_text`'s marks. Part of the page's
#: print CSS, and on their own the whole of what a frame is given: a frame
#: gets its marks released and nothing else of WebShot's (docs/09 P16-17).
RELEASE_PRINT_CSS = """/* Release text the page truncated with CSS. Only elements `release_clamped_text`
   measured and marked: a blanket `* { max-height: none }` would un-collapse
   blocks a page keeps collapsed on purpose, and would move the recorded output
   of every existing case (docs/13 P13-3). The two shapes are marked apart
   because only the line-clamp one needs its `display` restored — forcing
   `display: block` on a container that was merely too short would relayout it
   for no reason.

   The `:not([data-webshot-hidden])` is belt and braces. `release_clamped_text`
   already skips hidden subtrees, but without this guard the rule above sits at
   the same specificity as the hide rule and later in source order, so a noise
   element that is also clamped would be un-hidden and printed — and a future
   reordering of this block would be enough to reintroduce that. */
[data-webshot-clamped='line']:not([data-webshot-hidden='true']) {
    display: block !important;
    -webkit-line-clamp: unset !important;
    max-height: none !important;
    overflow: visible !important;
}
[data-webshot-clamped='height']:not([data-webshot-hidden='true']) {
    max-height: none !important;
    overflow: visible !important;
}
/* A box that scrolls on screen, capped by its own height or max-height, and
   measured to take its full height without covering anything (docs/09 P16-10).
   Only the vertical axis: a wide table released sideways runs off the page
   instead (P16-8), so its wrapper keeps scrolling and is reported. */
[data-webshot-clamped='scroll']:not([data-webshot-hidden='true']),
[data-webshot-clamped='shell']:not([data-webshot-hidden='true']) {
    height: auto !important;
    max-height: none !important;
    overflow-y: visible !important;
}
/* A box released as part of an app shell whose parent is a flex column: `flex:
   1` would otherwise go on sizing it to what is left of the shell (docs/09
   P16-13). Only a column's children, because in a row `flex` sets widths. */
[data-webshot-unflexed='true']:not([data-webshot-hidden='true']) {
    flex: 0 0 auto !important;
}
/* A grid whose fixed row sets a released pane's height (docs/09 P16-14). Its
   rows are left to their content; the columns, and where each item sits, are
   the grid's own. */
[data-webshot-ungridded='true']:not([data-webshot-hidden='true']) {
    grid-template-rows: none !important;
    grid-auto-rows: auto !important;
}
/* A panel positioned over the page, put into the flow where it sits in the
   document so it can grow without covering anything (docs/09 P16-15). Fixed,
   it printed its first rows again on every page. */
[data-webshot-unpositioned='true']:not([data-webshot-hidden='true']) {
    position: static !important;
    inset: auto !important;
    transform: none !important;
    float: none !important;
}
/* A table's sideways-scrolling wrapper, measured at the printed width to fit
   the page once its cells may wrap (docs/09 P16-11). An attribute of its own,
   not a `clamped` value: a wrapper can also be a released vertical box. */
[data-webshot-wrapped='true']:not([data-webshot-hidden='true']) {
    overflow-x: visible !important;
}
[data-webshot-wrapped='true'] :is(th, td) { white-space: normal !important; }
"""

#: How recognized text rides in the print: in the text layer, and not on the
#: page. The stylesheet below carries it in the top document. No stylesheet
#: reaches into a shadow root or a frame's document, so the visuals harvest
#: writes these same declarations inline on the text it adds there (P16-9).
#:
#: The layer must not change the layout it describes. It spans its containing
#: block, a box the page already lays out, and keeps its static `top`, beside
#: the asset. It used to be 600px wide from its static position, which is right
#: after the asset: after an image at the right edge it widened the document,
#: Chromium's shrink-to-fit printed the whole page at 1168px rather than 779px,
#: and the layer was itself cut off the page, text and all. A zero-size or
#: clipped wrapper keeps the layout, but Chromium then prints none of the text,
#: which is the layer's one purpose.
#:
#: The 2px top margin keeps the text off the top edge of a page. Its static
#: `top` is the top of the asset's line, which is the top of a page whenever
#: the asset was pushed there, as `break-inside: avoid` does to an image that
#: does not fit. Text 0.1px tall that close to a page's top edge loses every
#: glyph that does not dip below the baseline: "Revenue 2026" printed as
#: "eeue 06" (docs/09 P16-20).
OCR_TEXT_DECLARATIONS = (
    "position: absolute !important",
    "left: 0 !important",
    "right: 0 !important",
    "width: auto !important",
    "min-width: 0 !important",
    "max-width: none !important",
    "height: auto !important",
    "padding: 0 !important",
    "margin: 2px 0 0 !important",
    "overflow: visible !important",
    "font-size: 0.1px !important",
    "line-height: 0.1px !important",
    "color: rgba(0, 0, 0, 0.01) !important",
    "white-space: pre-wrap !important",
    "border: 0 !important",
    "pointer-events: none !important",
)

BASE_PRINT_CSS = (
    """
* {
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }
img, svg, video, canvas { max-width: 100% !important; height: auto; }
iframe { max-width: 100% !important; }
table { max-width: 100% !important; border-collapse: collapse; }
thead { display: table-header-group; }
tr, img, svg, figure, pre, blockquote { break-inside: avoid; }
h1, h2, h3, h4, h5, h6 { break-after: avoid; }
p, li { orphans: 3; widows: 3; }
pre, code { white-space: pre-wrap !important; overflow-wrap: anywhere; }
[data-webshot-hidden='true'] { display: none !important; }
"""
    + RELEASE_PRINT_CSS
    + "[data-webshot-ocr-text='true'] {\n"
    + "".join(f"    {declaration};\n" for declaration in OCR_TEXT_DECLARATIONS)
    + "}\n"
)

CLEAN_PRINT_CSS = """
html { background: #fff !important; }
body { margin: 0 !important; background: transparent !important; }
[data-webshot-root='true'] {
    background: transparent !important;
    box-sizing: border-box !important;
    box-shadow: none !important;
    float: none !important;
    position: static !important;
    width: 100% !important;
    max-width: none !important;
    margin: 0 auto !important;
    overflow: visible !important;
}
[data-webshot-ancestor='true'] {
    background: transparent !important;
    box-shadow: none !important;
}
a[href] { text-decoration-thickness: 0.06em; text-underline-offset: 0.12em; }
"""


#: How many of the images still loading a warning names; the rest it counts.
NAMED_PENDING_IMAGES = 5

#: Wait, within one deadline, for web fonts and then for every image that can
#: print, and report what was still loading when the deadline passed.
#:
#: Only an image with a box is waited for. One without a box (`display: none`,
#: a closed `<details>`) is never printed, and a lazy one never starts loading,
#: so it fires neither `load` nor `error` and every capture of a page holding
#: one waited out the whole deadline and then warned that images were still
#: loading (docs/09 P20-2).
#:
#: An image with a box that is still lazy is switched to eager loading. The
#: scroll pass moves the window only, so a lazy image inside a scrolling box or
#: a carousel, which the print may release (P16-10), or anywhere under
#: `--no-scroll`, never comes near the viewport: it never loads, the wait for
#: it can only time out, and the PDF prints its box empty.
WAIT_FOR_FONTS_AND_IMAGES_JS = """async timeoutMs => {
    const timedOut = new Promise(resolve => setTimeout(resolve, timeoutMs));
    if (document.fonts?.ready) await Promise.race([document.fonts.ready, timedOut]);
    const images = [...document.images].filter(
        img => !img.complete && img.getClientRects().length > 0);
    let eager = 0;
    for (const img of images) {
        if (img.loading === 'lazy') { img.loading = 'eager'; eager += 1; }
    }
    await Promise.race([timedOut, Promise.all(images.map(img => new Promise(resolve => {
        if (img.complete) return resolve();
        img.addEventListener('load', resolve, {once: true});
        img.addEventListener('error', resolve, {once: true});
    })))]);
    return {
        fontsLoading: document.fonts?.status === 'loading',
        images: images.filter(img => !img.complete).map(img => img.currentSrc || img.src),
        eager,
    };
}"""


def image_name(url: str) -> str:
    """An image as a warning names it: its address without query or fragment.

    The manifest carries the warning, and a query string is where a signed
    image URL keeps its token (docs/guide/bundle-format.md).
    """
    parts = urlsplit(url)
    if parts.scheme in ("http", "https", "file"):
        named = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    elif parts.scheme:
        named = f"a {parts.scheme}: URL"
    else:
        named = url
    return named if len(named) <= 160 else named[:157] + "..."


def pending_images_warning(urls: Sequence[str], timeout_s: float) -> str:
    """The warning for images still loading at the deadline, naming a few.

    Each address is named once, and "and N more" counts the images whose
    address is not named, so every image is either named or counted, even
    where several share an address.
    """
    names = [image_name(url) for url in urls]
    shown = list(dict.fromkeys(names))[:NAMED_PENDING_IMAGES]
    rest = sum(1 for name in names if name not in shown)
    listed = ", ".join(shown) + (f", and {rest} more" if rest else "")
    return (
        f"{len(urls)} image{'' if len(urls) == 1 else 's'} had not finished "
        f"loading after a {timeout_s:g}-second wait, so the PDF may show "
        f"{'it' if len(urls) == 1 else 'them'} blank or incomplete: {listed}."
    )


async def _wait_for_fonts_and_images(page: Page, timeout_s: float = 30.0) -> list[str]:
    try:
        # The deadline is the script's own, so it can say what was still
        # loading when it passed. This one is for a page that never answers.
        state = await asyncio.wait_for(
            page.evaluate(WAIT_FOR_FONTS_AND_IMAGES_JS, timeout_s * 1000),
            timeout=timeout_s + 10,
        )
    except (TimeoutError, PlaywrightTimeoutError):
        return [
            "Fonts and images could not be checked: the page did not answer "
            f"within {timeout_s + 10:g} seconds, so the PDF may have been "
            "created while some were still loading."
        ]
    if state["eager"]:
        LOGGER.info(
            "Started loading %d lazy image(s) the scroll pass had not reached.",
            state["eager"],
        )
    warnings: list[str] = []
    if state["fontsLoading"]:
        warnings.append(
            f"Web fonts were still loading after a {timeout_s:g}-second wait, so "
            "the PDF may use fallback fonts."
        )
    if state["images"]:
        warnings.append(pending_images_warning(state["images"], timeout_s))
    return warnings


#: How long the content a capture keeps must stand still, while only regions
#: it removes go on growing, before the lazy-load pass stops short of a page
#: that never ends (docs/09 P23-8). Steps and seconds both: a step is a
#: rendered frame, `--scroll-delay` and a measurement, and what the pass waits
#: for is a network fetch. On a video site's watch page each continuation of
#: the kept list of recommendations arrived within three steps (0.99 s) of the
#: window passing the end of the list.
REMOVED_GROWTH_STEPS = 5
REMOVED_GROWTH_SECONDS = 2.0

#: How long a step waits for the page to render after its scroll before the
#: pass gives up on a page that is not rendering (docs/09 P34). A scroll
#: reaches the page only in a rendering update: that is where Chromium fires
#: `scroll` events and works out what an IntersectionObserver sees, and the
#: two are how pages load lazy content. A step that counted before the page
#: had rendered measured a page that could not yet have answered, and three
#: such steps at the bottom ended the pass as though the page had ended. With
#: sixteen captures sharing a loaded Mac, a fresh renderer's first frame came
#: up to 3.4 s after `load`; later frames came every 12 ms or so.
FRAME_TIMEOUT_S = 30.0

#: Resolves true at the page's next rendering update, which handles every
#: scroll made before it. After `timeout` ms without one it resolves to what
#: the page could see of why: a timer, because a page that is not rendering
#: still runs them. A wait that ran out used to say only "the page did not
#: render", which blames the page; the same result came from stopping the
#: browser's GPU process, which draws every frame, while the page ran on
#: (docs/09 P76).
NEXT_FRAME_JS = """timeout => new Promise(resolve => {
    const start = performance.now();
    const timer = setTimeout(() => resolve({
        visibility: document.visibilityState,
        painted: performance.getEntriesByType('paint').length > 0,
        lateMs: performance.now() - start - timeout,
    }), timeout);
    requestAnimationFrame(() => { clearTimeout(timer); resolve(true); });
})"""

#: How late the page's own timer must fire before the warning stops saying
#: the page was running. Under every load docs/09 P76 measured, up to 39
#: runnable threads a core and a swap storm, no frame wait, which needs a task
#: on the page's main thread, was longer than 0.19 s; a second is not a busy
#: machine's ordinary jitter.
LATE_TIMER_S = 1.0

#: One step down, and back to the top when the pass ends. Chromium 153's
#: scroll methods return a promise that settles when the scroll has finished,
#: and `page.evaluate` waits for a promise it is handed. On a page that sets
#: `scroll-behavior: smooth` that took 0.49 s a step, and on one that was not
#: rendering it never settled, so the pass hung before its frame wait could
#: time out (docs/09 P34). `instant` puts the window where the next
#: measurement expects it, and the function hands nothing back, so the only
#: wait is `NEXT_FRAME_JS`, which has a bound.
SCROLL_STEP_JS = """() => {
    window.scrollBy({top: Math.max(320, window.innerHeight * 0.8), behavior: 'instant'});
}"""
SCROLL_TO_TOP_JS = """() => {
    window.scrollTo({top: 0, behavior: 'instant'});
}"""

#: One step's measurement. The document's height drives the original rule: the
#: pass ends when the window has reached the bottom and the height has held
#: still. The rest is only measured when the capture will remove something
#: (clean mode's page chrome, `--exclude`), and says whether the page is still
#: growing anywhere the capture keeps.
#:
#: A comment feed under a video never ends: each time the window nears the
#: bottom the page appends more, so the height never holds and the pass ran to
#: `--max-scrolls` every time. Clean mode then removed the feed, which by step
#: 100 held 89% of the page's elements, and every later stage carried them:
#: the renderer grew from 0.4 to 1.0 GB over the pass and peaked at 2.5 GB
#: while the visuals were harvested (docs/09 P23-8).
#:
#: Kept means outside every element the capture will hide, matched as
#: `hide_elements` matches them, in the document's own tree, and counted into
#: open shadow roots, where a component's content lives. `keptBottom` is the
#: lowest box among kept elements with no element children: a container's box
#: stretches with whatever its column holds, the removed feed included, and a
#: leaf's does not. `regions` sizes each outermost removed element, so the
#: warning can name the one that grew rather than guess.
LAZY_LOAD_STATE_JS = """({noise, excluded}) => {
    const state = {
        y: window.scrollY,
        viewport: window.innerHeight,
        height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
        kept: null, keptBottom: 0, regions: {},
    };
    if (!noise.length && !excluded.length) return state;
    const matched = selectors => {
        if (!selectors.length) return new Set();
        try { return new Set(document.querySelectorAll(selectors.join(','))); } catch {}
        const found = new Set();
        for (const selector of selectors) {
            try { for (const element of document.querySelectorAll(selector)) found.add(element); }
            catch {}  // hide_elements names an invalid selector; this only measures
        }
        return found;
    };
    const chrome = matched(noise);
    const cut = matched(excluded);
    const removed = new Set([...chrome, ...cut]);
    const filter = {acceptNode: node =>
        removed.has(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT};
    let kept = 0;
    let bottom = 0;
    const walk = root => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, filter);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            kept += 1;
            if (node.shadowRoot) walk(node.shadowRoot);
            if (node.firstElementChild) continue;
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) bottom = Math.max(bottom, rect.bottom);
        }
    };
    walk(document.body);
    state.kept = kept;
    state.keptBottom = bottom + window.scrollY;
    for (const element of removed) {
        let above = element.parentElement;
        while (above && !removed.has(above)) above = above.parentElement;
        if (above) continue;
        const name = element.localName + (element.id ? `#${element.id}` : '');
        const by = cut.has(element) ? 'exclude' : 'clean';
        const key = `${by} ${name}`;
        const size = element.getElementsByTagName('*').length + 1;
        state.regions[key] = {name, by, size: (state.regions[key]?.size || 0) + size};
    }
    return state;
}"""


@dataclass(frozen=True, slots=True)
class FrameStall:
    """What the page could see when no frame came within `FRAME_TIMEOUT_S` of a scroll.

    The page cannot see why the browser drew nothing, but it can rule out
    itself: whether it was hidden, whether it had been running, and whether
    it had been drawn before (docs/09 P76).
    """

    #: `document.visibilityState`. A browser does not render a hidden page.
    visibility: str
    #: Whether the page had a first paint on record: whether a frame of it
    #: had ever been drawn.
    painted: bool
    #: How much later than asked the page's own timer fired, in seconds. A
    #: page whose main thread was busy, or not run at all, fires it late.
    late_s: float


@dataclass(frozen=True, slots=True)
class LazyLoadPass:
    """What the lazy-load pass did, for the log, the manifest and a crash report."""

    steps: int
    #: `bottom`: the window reached the end of a page that stopped growing.
    #: `removed-growth`: the page was still growing, only inside regions the
    #: capture removes. `limit`: `--max-scrolls` ran out first. `unrendered`:
    #: the page did not render within `FRAME_TIMEOUT_S` of a scroll.
    ended: Literal["bottom", "removed-growth", "limit", "unrendered"]
    #: The document's height at the last step, in CSS pixels.
    height: int
    #: For `removed-growth`: each removed region that grew while the kept
    #: content stood still, as (name, what removes it: `clean` or `exclude`).
    growing: tuple[tuple[str, str], ...] = ()
    #: For `removed-growth`: the steps the kept content held still for.
    steady_steps: int = 0
    #: For `unrendered`: what the page could see when the wait ran out.
    stall: FrameStall | None = None


@dataclass(slots=True)
class _Steady:
    """Since when the kept content has held still with the window past its end."""

    step: int
    since: float
    kept: int
    regions: dict[str, Any]


async def scroll_to_load(
    page: Page,
    max_scrolls: int,
    delay_s: float,
    *,
    noise: Sequence[str] = (),
    excluded: Sequence[str] = (),
) -> LazyLoadPass:
    """Incrementally scroll through the page to activate viewport-based lazy loading.

    `noise` and `excluded` are the selectors the capture will hide (clean
    mode's page chrome, and `--exclude`). Growth inside what they match does
    not keep the pass going once the rest of the page has stopped changing.
    """
    LOGGER.info("Loading lazy content (up to %d scroll steps)...", max_scrolls)
    stable_steps = 0
    previous_height = 0
    ended: Literal["bottom", "removed-growth", "limit", "unrendered"] = "limit"
    growing: tuple[tuple[str, str], ...] = ()
    steady: _Steady | None = None
    steady_steps = 0
    stall: FrameStall | None = None
    steps = 0
    height = 0
    selectors = {"noise": list(noise), "excluded": list(excluded)}

    for steps in range(1, max_scrolls + 1):
        state = await page.evaluate(LAZY_LOAD_STATE_JS, selectors)
        height = state["height"]
        bottom = state["y"] + state["viewport"]
        if bottom >= height - 4:
            stable_steps = stable_steps + 1 if height == previous_height else 0
            if stable_steps >= 3:
                ended = "bottom"
                break
        else:
            stable_steps = 0
        previous_height = height
        # `kept` is None when the capture removes nothing: every part of the
        # page is kept, and only the rule above can end the pass.
        if state["kept"] is not None:
            now = time.monotonic()
            passed = bottom >= state["keptBottom"] - 4
            if not passed:
                steady = None
            elif steady is None or state["kept"] != steady.kept:
                steady = _Steady(steps, now, state["kept"], state["regions"])
            elif (
                steps - steady.step >= REMOVED_GROWTH_STEPS
                and now - steady.since >= REMOVED_GROWTH_SECONDS
            ):
                # Growth the pass has seen, not inferred from the height: a
                # page whose kept content grows somewhere this count cannot
                # see (a closed shadow root) has no removed region growing,
                # and keeps scrolling as before.
                growing = tuple(
                    (region["name"], region["by"])
                    for key, region in state["regions"].items()
                    if region["size"] > steady.regions.get(key, {"size": 0})["size"]
                )
                if growing:
                    ended = "removed-growth"
                    steady_steps = steps - steady.step
                    break
        await page.evaluate(SCROLL_STEP_JS)
        # The delay starts once the page has seen the scroll, not when it was
        # asked for: until it renders, the page has not seen it (docs/09 P34).
        seen = await page.evaluate(NEXT_FRAME_JS, FRAME_TIMEOUT_S * 1000)
        if seen is not True:
            ended = "unrendered"
            stall = FrameStall(
                visibility=seen["visibility"],
                painted=seen["painted"],
                late_s=max(0.0, seen["lateMs"] / 1000),
            )
            break
        await asyncio.sleep(delay_s)

    await page.evaluate(SCROLL_TO_TOP_JS)
    LOGGER.info("Lazy-load pass finished after %d step(s).", steps)
    return LazyLoadPass(
        steps=steps,
        ended=ended,
        height=height,
        growing=growing,
        steady_steps=steady_steps,
        stall=stall,
    )


def lazy_load_warning(result: LazyLoadPass, max_scrolls: int) -> str | None:
    """The manifest warning for a lazy-load pass that did not reach the page's end."""
    if result.ended == "limit":
        return f"Lazy-load scrolling reached the {max_scrolls}-step safety limit."
    if result.ended == "bottom":
        return None
    if result.ended == "unrendered":
        message = (
            f"The lazy-load pass stopped after {result.steps} step(s): the page "
            f"did not render within {FRAME_TIMEOUT_S:g} seconds of a scroll, and "
            "a page sees a scroll only when it renders, so content it loads as "
            "it is scrolled may not have been loaded."
        )
        if result.stall is not None:
            message += " " + _stall_seen(result.stall)
        return message
    chrome = [name for name, by in result.growing if by == "clean"]
    cut = [name for name, by in result.growing if by == "exclude"]
    where = []
    if chrome:
        where.append(f"{_listed(chrome)}, which clean mode removes as page chrome")
    if cut:
        where.append(f"{_listed(cut)}, which --exclude removes")
    message = (
        f"The lazy-load pass stopped after {result.steps} step(s) with the page "
        f"still growing: it grew only inside {' and '.join(where)}, and the "
        "content the capture keeps did not change over the last "
        f"{result.steady_steps} steps. Whatever more the page would have added "
        "there was not loaded."
    )
    if chrome:
        message += (
            " --mode faithful keeps that part of the page and scrolls on, up to "
            "--max-scrolls."
        )
    return message


def _stall_seen(stall: FrameStall) -> str:
    """What the page saw while no frame came, and so which side stopped (docs/09 P76).

    The first that holds is the one said: a hidden page explains the rest, and
    a page that was not running cannot say what the browser was doing.
    """
    if stall.visibility != "visible":
        return (
            f'The page was hidden (its visibilityState was "{stall.visibility}"), '
            "and a browser does not render a hidden page."
        )
    if stall.late_s >= LATE_TIMER_S:
        return (
            f"The page's own timer fired {stall.late_s:.1f} seconds late, so for "
            "part of the wait the page could not run anything: its main thread "
            "was busy, or the machine did not run it."
        )
    if stall.painted:
        return (
            "The page had been drawn before, was visible, and its own timers ran "
            "on time: the page was running, and the browser drew no frame for "
            "it. Capturing again may get further."
        )
    return (
        "The page was visible and its own timers ran on time, but it had never "
        "been drawn: something held back its first frame, such as a "
        "render-blocking resource that had not arrived, or a browser that had "
        "not yet drawn one."
    )


def _listed(names: Sequence[str], shown: int = 3) -> str:
    """Names joined for a sentence, with the rest counted."""
    rest = len(names) - shown
    head = ", ".join(names[:shown])
    return f"{head} and {rest} more" if rest > 0 else head


@dataclass(frozen=True, slots=True)
class ContentSelection:
    """Which content region was chosen, and what chose it (docs/05 task 3.4)."""

    selector: str | None
    strategy: Literal["explicit", "trafilatura", "static-selectors", "full-page"]


#: Vocabulary coverage, not substring matching: Trafilatura rewrites the text
#: it reports (entities resolved, inline markup dropped, blocks rejoined), so
#: the words are what survive the trip and the element boundaries are not.
LOCATOR_SCRIPT = r"""([article, coverage]) => {
    const words = value => (value || '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
    const articleWords = words(article);
    const wanted = new Set(articleWords);
    if (wanted.size < 20) return null;
    // How often each word appears, not just that it does. Distinct-vocabulary
    // coverage alone let one paragraph of a repetitive article hold 90% of the
    // article's *words* while holding a fraction of its *text* — and the
    // shortest-wins rule below then picked it, after which `isolate_content`
    // hid its siblings and the capture silently lost most of the article
    // (docs/09 P8-67). An element now has to carry the article's bulk as well
    // as its vocabulary.
    const wantedCounts = new Map();
    for (const word of articleWords) wantedCounts.set(word, (wantedCounts.get(word) || 0) + 1);
    // An element has to hold `need` DISTINCT article words to clear the
    // coverage bar, and the shortest text that can hold `need` distinct words
    // is one character each plus one separator between them. Everything below
    // that length is provably not the article, so it is rejected before
    // anything expensive touches it — on a large page this is the difference
    // between tokenizing the whole DOM a dozen times over and tokenizing the
    // handful of elements that could actually win.
    const need = Math.ceil(coverage * wanted.size);
    const minLength = Math.max(200, 2 * need - 1);
    let best = null;
    for (const element of document.body.querySelectorAll('*')) {
        const text = element.innerText || '';
        if (text.length < minLength) continue;
        if (element.closest('[data-webshot-hidden="true"]')) continue;
        // Reading layout is the other cost worth deferring past the gate.
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden' || rect.width < 250) continue;
        const found = new Set();
        const remaining = new Map(wantedCounts);
        let occurrences = 0;
        for (const word of words(text)) {
            if (!wanted.has(word)) continue;
            found.add(word);
            // Capped at what the article actually contains, so an element that
            // repeats one word a thousand times cannot buy coverage with it.
            const left = remaining.get(word) || 0;
            if (left > 0) { remaining.set(word, left - 1); occurrences += 1; }
        }
        if (found.size / wanted.size < coverage) continue;
        if (occurrences / articleWords.length < coverage) continue;
        // The smallest element that still holds the article is its container;
        // anything larger has wrapped it in page chrome. `<=` breaks the tie
        // toward the deeper element: a wrapper whose only child is the
        // article has exactly the article's text, and querySelectorAll walks
        // ancestors first, so a strict `<` would isolate the wrapper.
        if (!best || text.length <= best.length) best = {element, length: text.length};
    }
    if (!best) return null;
    best.element.setAttribute('data-webshot-auto-root', 'true');
    return "[data-webshot-auto-root='true']";
}"""


async def locate_main_content(page: Page, article: str) -> str | None:
    """Find the text Trafilatura called the article, in the live DOM.

    Trafilatura reports text, not a selector, so the element holding that text
    is what gets isolated.  The text arrives from the caller rather than being
    read here: the DOM is serialized once per capture, so the region and the
    metadata recorded beside it are observations of the same page.
    """
    selector: str | None = await page.evaluate(
        LOCATOR_SCRIPT, [article, MAIN_CONTENT_COVERAGE]
    )
    return selector


async def select_main_content(
    page: Page, explicit: str | None, article: str | None, *, auto: bool = True
) -> ContentSelection:
    """Resolve `--selector` / `--auto-selector` into one recorded decision.

    Every outcome is named, including the two that involve no searching at
    all, so the manifest can say how a capture's content region was chosen
    without the caller re-deriving it.
    """
    if explicit:
        return ContentSelection(explicit, "explicit")
    if not auto:
        return ContentSelection(None, "full-page")
    selector = await locate_main_content(page, article) if article else None
    if selector:
        return ContentSelection(selector, "trafilatura")
    selector = await find_content_selector(page)
    if selector:
        return ContentSelection(selector, "static-selectors")
    return ContentSelection(None, "full-page")


async def find_content_selector(page: Page) -> str | None:
    """Choose the semantic container with the most substantial visible text."""
    selector: str | None = await page.evaluate(
        """selectors => {
            let best = null;
            for (const selector of selectors) {
                for (const element of document.querySelectorAll(selector)) {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const textLength = (element.innerText || '').trim().length;
                    if (style.display === 'none' || style.visibility === 'hidden' || rect.width < 250 || textLength < 300) continue;
                    const linkLength = [...element.querySelectorAll('a')].reduce((n, a) => n + (a.innerText || '').length, 0);
                    const score = textLength - Math.min(linkLength * 0.35, textLength * 0.5);
                    if (!best || score > best.score) best = {element, score, selector};
                }
            }
            if (!best) return null;
            best.element.setAttribute('data-webshot-auto-root', 'true');
            return "[data-webshot-auto-root='true']";
        }""",
        CONTENT_SELECTORS,
    )
    return selector


async def isolate_content(page: Page, selector: str) -> None:
    """Hide siblings around a content root without rebuilding the DOM."""
    count = await page.locator(selector).count()
    if count == 0:
        raise UsageError(f"Content selector did not match anything: {selector}")
    if count > 1:
        LOGGER.warning("Content selector matched %d elements; using the first.", count)
    await page.locator(selector).first.evaluate(
        """root => {
            root.setAttribute('data-webshot-root', 'true');
            let node = root;
            while (node && node !== document.body) {
                const parent = node.parentElement;
                if (!parent) break;
                for (const sibling of parent.children) {
                    if (sibling !== node) sibling.setAttribute('data-webshot-hidden', 'true');
                }
                if (parent !== document.body) parent.setAttribute('data-webshot-ancestor', 'true');
                node = parent;
            }
        }"""
    )


async def hide_elements(page: Page, selectors: Sequence[str]) -> int:
    if not selectors:
        return 0
    hidden: int = await page.evaluate(
        """selectors => {
            let count = 0;
            for (const selector of selectors) {
                let elements;
                try { elements = document.querySelectorAll(selector); }
                catch (error) { throw new Error(`Invalid CSS selector '${selector}': ${error.message}`); }
                for (const element of elements) {
                    if (!element.hasAttribute('data-webshot-hidden')) count++;
                    element.setAttribute('data-webshot-hidden', 'true');
                }
            }
            return count;
        }""",
        list(selectors),
    )
    return hidden


#: The release, run in one document at a time (see `release_clamped_text`).
RELEASE_SCRIPT = """() => {
            const TRIAL = [['height', 'auto'], ['max-height', 'none'], ['overflow-y', 'visible']];
            const kept = [];
            const bottom = node => node.getBoundingClientRect().bottom;
            // The attribute itself, not its properties: a property round trip
            // re-serializes it, and the page should get back what it had.
            // Read before writing: Chromium writes a changed inline style back
            // to the attribute lazily, so removing it straight after
            // `setProperty` left `style=""` on a box that had no attribute.
            const restore = (element, saved) => {
                void element.getAttribute('style');
                if (saved === null) element.removeAttribute('style');
                else element.setAttribute('style', saved);
            };
            const releasesSafely = element => {
                const saved = element.getAttribute('style');
                const parent = element.parentElement;
                const before = {self: element.getBoundingClientRect().height,
                                parent: parent.getBoundingClientRect().height};
                for (const [name, value] of TRIAL) element.style.setProperty(name, value, 'important');
                const grown = element.getBoundingClientRect().height - before.self;
                let safe = element.scrollHeight <= element.clientHeight + 1
                    && parent.getBoundingClientRect().height - before.parent >= grown - 1;
                for (let node = parent; safe && node && node !== document.body; node = node.parentElement) {
                    // A box with no layout of its own (`display: contents`)
                    // contains nothing to compare against.
                    if (node.getClientRects().length && bottom(node) < bottom(element) - 1) safe = false;
                }
                if (safe) { kept.push([element, saved]); return true; }
                restore(element, saved);
                return false;
            };
            const flexColumnChild = node => {
                const parent = node.parentElement;
                if (!parent) return false;
                const style = getComputedStyle(parent);
                return style.display.includes('flex') && style.flexDirection.startsWith('column');
            };
            const inFlow = node => {
                const position = getComputedStyle(node).position;
                return position !== 'absolute' && position !== 'fixed';
            };
            const overlaps = (a, b) => {
                const r = a.getBoundingClientRect();
                const q = b.getBoundingClientRect();
                return Math.min(r.right, q.right) - Math.max(r.left, q.left) > 1
                    && Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top) > 1;
            };
            const chains = [];
            const releasesShell = pane => {
                const changed = [];
                const grow = node => {
                    const unflex = flexColumnChild(node);
                    changed.push({node, saved: node.getAttribute('style'), unflex, kind: 'grow'});
                    for (const [name, value] of TRIAL) node.style.setProperty(name, value, 'important');
                    if (unflex) node.style.setProperty('flex', '0 0 auto', 'important');
                };
                const ungrid = node => {
                    changed.push({node, saved: node.getAttribute('style'), unflex: false, kind: 'ungrid'});
                    node.style.setProperty('grid-template-rows', 'none', 'important');
                    node.style.setProperty('grid-auto-rows', 'auto', 'important');
                };
                const grown = node => changed.some(entry => entry.node === node && entry.kind === 'grow');
                grow(pane);
                // Only the boxes that still stop it: one that ends above the
                // pane, or one its content overflows. Overflowing, not only
                // clipping: in a row the pane stretches to its shell's height,
                // so the shell holds it without clipping and still sets it.
                // Not the root's overflow: its `clientHeight` is the viewport,
                // so every page taller than the screen would look held.
                const walk = () => {
                    for (let node = pane.parentElement; node; node = node.parentElement) {
                        if (grown(node)) continue;
                        const overflowing = node !== document.documentElement
                            && node.scrollHeight > node.clientHeight + 1;
                        if (bottom(node) < bottom(pane) - 1 || overflowing) {
                            // A box already as tall as its content, that clips
                            // nothing, was not what held the pane: the pane's
                            // own overflow ran past it. Growing it changes
                            // nothing, so it is not marked.
                            const before = node.getBoundingClientRect().height;
                            const clips = getComputedStyle(node).overflowY !== 'visible';
                            grow(node);
                            if (!clips && Math.abs(node.getBoundingClientRect().height - before) <= 1) {
                                const entry = changed.pop();
                                restore(entry.node, entry.saved);
                            }
                        }
                    }
                };
                walk();
                if (pane.scrollHeight > pane.clientHeight + 1) {
                    for (let node = pane; node.parentElement; node = node.parentElement) {
                        if (getComputedStyle(node.parentElement).display.includes('grid')) {
                            ungrid(node.parentElement);
                            walk();
                            break;
                        }
                    }
                }
                let safe = pane.scrollHeight <= pane.clientHeight + 1;
                for (let node = pane.parentElement; safe && node; node = node.parentElement) {
                    if (node.getClientRects().length && bottom(node) < bottom(pane) - 1) safe = false;
                }
                for (const {node} of changed) {
                    if (!safe || !node.parentElement) continue;
                    for (const sibling of node.parentElement.children) {
                        if (sibling === node || !sibling.getClientRects().length || !inFlow(sibling)) continue;
                        if (overlaps(node, sibling)) { safe = false; break; }
                    }
                }
                if (safe) {
                    for (const {node, saved} of changed) kept.push([node, saved]);
                    chains.push(changed);
                    return true;
                }
                for (const {node, saved} of changed.reverse()) restore(node, saved);
                return false;
            };
            const UNPOSITION = [['position', 'static'], ['inset', 'auto'],
                                ['transform', 'none'], ['float', 'none']];
            // On the page and seen: a panel parked off the page or made
            // invisible is hidden from the screen as well.
            const shown = (element, style) => {
                const rect = element.getBoundingClientRect();
                const page = document.documentElement;
                return style.visibility === 'visible' && Number(style.opacity) > 0
                    && rect.width > 0 && rect.height > 0
                    && rect.right + scrollX > 0 && rect.bottom + scrollY > 0
                    && rect.left + scrollX < page.scrollWidth && rect.top + scrollY < page.scrollHeight;
            };
            const moved = [];
            const releasesPositioned = element => {
                const saved = element.getAttribute('style');
                for (const [name, value] of UNPOSITION) element.style.setProperty(name, value, 'important');
                if (releasesSafely(element) || releasesShell(element)) {
                    // The trials saved the style they found, which by then
                    // had the move in it; the page gets back what it had.
                    for (const entry of kept) if (entry[0] === element) entry[1] = saved;
                    moved.push(element);
                    return true;
                }
                restore(element, saved);
                return false;
            };
            let count = 0;
            for (const element of document.querySelectorAll('body *')) {
                // Never release something the capture is removing. `closest`
                // matches the element itself as well as its ancestors, which is
                // the case that matters: a noise selector and a clamp on the
                // SAME element put `display: block` and `display: none` at equal
                // specificity, and the clamp rule wins on source order.
                if (element.closest('[data-webshot-hidden="true"]')) continue;
                const style = getComputedStyle(element);
                const clipped = style.overflow === 'hidden' || style.overflowY === 'hidden';
                // Truncating, not merely capable of it. A `-webkit-line-clamp`
                // roomier than its content clips nothing, and marking it would
                // relayout an element for no reason and overstate the count.
                const truncating = element.scrollHeight > element.clientHeight + 1;
                const lineClamped =
                    style.webkitLineClamp && style.webkitLineClamp !== 'none';
                const capped = style.maxHeight && style.maxHeight !== 'none';
                if (lineClamped && truncating) {
                    element.setAttribute('data-webshot-clamped', 'line');
                    count++;
                } else if (capped && clipped && truncating) {
                    element.setAttribute('data-webshot-clamped', 'height');
                    count++;
                } else if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && truncating) {
                    const positioned = style.position === 'absolute' || style.position === 'fixed';
                    if (positioned) {
                        if (shown(element, style) && releasesPositioned(element)) count++;
                    } else if (releasesSafely(element)) {
                        element.setAttribute('data-webshot-clamped', 'scroll');
                        count++;
                    } else if (releasesShell(element)) {
                        count++;
                    }
                }
            }
            // A positioned panel kept by its own trial is marked here; one kept
            // as a chain is marked with the chain below.
            for (const element of moved) {
                element.setAttribute('data-webshot-unpositioned', 'true');
                if (!element.hasAttribute('data-webshot-clamped') && !chains.some(c => c[0].node === element)) {
                    element.setAttribute('data-webshot-clamped', 'scroll');
                }
            }
            for (const changed of chains) {
                for (const {node, unflex, kind} of changed) {
                    if (kind === 'ungrid') {
                        node.setAttribute('data-webshot-ungridded', 'true');
                        continue;
                    }
                    // A box already marked keeps its mark; its own rule covers it.
                    if (!node.hasAttribute('data-webshot-clamped')) {
                        node.setAttribute('data-webshot-clamped', node === changed[0].node ? 'scroll' : 'shell');
                    }
                    if (unflex) node.setAttribute('data-webshot-unflexed', 'true');
                }
            }
            // The mark and the print rule take over from here; the page keeps
            // no inline style of WebShot's.
            for (const [element, saved] of kept) restore(element, saved);
            return [count, moved.length];
        }"""


async def release_clamped_text(page: Page) -> int:
    """Mark every element whose own CSS is truncating its content, and count them.

    The measurement has to happen in the page because CSS cannot select on
    computed style: `-webkit-line-clamp` and a too-short `max-height` are both
    invisible to a selector. So this follows the file's established idiom —
    measure, mark, and let the print rule target the mark, the same shape
    `hide_obstructive_overlays` uses.

    Why it matters at all: `innerText` returns the whole string either way, so
    the *bundle* is complete and every text assertion passes while the **render
    silently drops the clipped part** (docs/13 P9-7). On the LMS this was
    measured against there is no `Read more` control to click (P12-6), which
    makes this rule the entire fix rather than a fallback for what a click
    leaves behind.

    The `max-height` arm deliberately requires an actual cap rather than just
    `overflow: hidden` and an overflowing box. Without that, an ordinary
    clipping container — a rounded-corner media frame, say — matches, and
    releasing it moves output on cases that have nothing to do with this.

    The `scroll` arm is the same defect behind `overflow: auto` or `scroll`: a
    capped log or panel that the reader scrolls on screen and a PDF cannot, so
    it printed three of thirty rows (docs/09 P16-8). Such a box is not always
    safe to grow, and a release that makes text overlap what follows would hide
    it where no check looks (P16-4). So each one is released on trial and kept
    only if it measurably fits: it is in the flow, it then holds all its
    content, its parent grew by as much as it did, and every ancestor still
    contains it. A pane sized by a flex or grid layout, a panel positioned over
    the page, and a row of a fixed grid all fail that trial. Whatever the chain
    below does not release either stays capped, and `render/clipping.py`
    reports it. Kept trials stay applied until the
    walk ends, so a box nested in a released one is measured in the page it
    will print in.

    A box that fails that trial gets a second one, as a chain (docs/09
    P16-13). The case it exists for is the app shell: a pane with `flex: 1` in
    a column fixed at the viewport's height, which prints one page of two
    hundred rows. Its own cap is not what holds it, so the pane is released,
    and then each box above it that still stops it growing, up to the root.
    The chain is kept on the same terms plus one: the pane holds all its
    content, every ancestor contains it, and no released box overlaps a
    neighbour in the flow. A child of a flex column is also marked to stop
    `flex` sizing it. Anything else is undone, and the pane stays reported.

    A pane in a grid with fixed rows still overflows after all that, because
    the row track sets its height whatever the boxes above do (docs/09
    P16-14). So when the chain leaves the pane overflowing, the nearest grid
    it sits in has its rows left to their content, and the walk runs again.
    The same terms decide whether any of it is kept.

    A panel positioned over the page, `absolute` or `fixed`, cannot grow where
    it is without covering what lies under it. Fixed, it printed its first
    rows again on every page (docs/09 P16-15). So a visible one is put into the
    flow, where it sits in the document, and then tried like any other box:
    on its own, then as a chain. Moving it is a change to where it appears, so
    the capture log says how many were moved. A panel placed off the page
    (`left: -9999px`) is hidden from the screen too and is never a candidate.

    A scrolling box inside a frame is the same box, and printed 2 of 30 rows
    (docs/09 P16-17). So the same script runs in every frame the page can
    script, and a frame that got marks is given the rules that act on them,
    as an adopted stylesheet, which its `style-src` policy cannot refuse. A
    frame from another origin, or one inside what the capture is removing, is
    left alone here. A released box can still be cut off by its frame's own
    box, so the frame must be tall enough to show its document.

    A frame from another origin is decided frame by frame, because no page
    script could make this change and nothing will grow the frame afterwards
    (docs/09 P16-21). It is left alone when it holds a sign-in or payment
    field. Otherwise it is released on trial, and the release is kept only if
    every released box still sits inside the frame's own box: past it, the
    text would only move from a loss the clipping check reports to one it
    cannot see. Each decision is logged with its reason. A kept one is marked,
    so the clipping check can confirm it against the layout that printed.
    """
    released, moved = await _release_in(page)
    scriptable = await _scriptable_frames(page)
    for frame in scriptable:
        try:
            in_frame, moved_in_frame = await _release_in(frame)
            if in_frame:
                await frame.evaluate(ADOPT_STYLESHEET_SCRIPT, RELEASE_PRINT_CSS)
        except PlaywrightError:
            if not frame.is_detached():
                raise
            # Removed by the page between being listed and being released, as
            # ad slots are: it prints nothing, so it needs nothing. Before
            # this, one such frame ended the capture with exit 5 (docs/09
            # P23-2). Any other failure here is still one.
            LOGGER.debug("A frame went away before it could be released: %s", frame.url)
            continue
        released += in_frame
        moved += moved_in_frame
    for frame in page.frames[1:]:
        if frame in scriptable:
            continue
        placed = await printed_frame(frame)
        if placed is None:
            continue
        in_frame, moved_in_frame = await _release_cross_origin(frame, placed[0])
        released += in_frame
        moved += moved_in_frame
    if moved:
        LOGGER.info(
            "Moved %d positioned panel(s) into the page flow, so each prints whole "
            "where it sits in the document.",
            moved,
        )
    return released


async def _release_in(target: Page | Frame) -> tuple[int, int]:
    """Run the release in one document: how many were released, and how many moved."""
    marked: list[int] = await target.evaluate(RELEASE_SCRIPT)
    return int(marked[0]), int(marked[1])


#: Whether this frame is one the page could script: every frame between it and
#: the top reachable through `frameElement`, which is null across an origin,
#: and none of them inside what the capture is removing.
SCRIPTABLE_FRAME_SCRIPT = """() => {
    try {
        for (let view = window; view !== view.top; view = view.parent) {
            const host = view.frameElement;
            if (!host || host.closest('[data-webshot-hidden="true"]')) return false;
        }
        return true;
    } catch (error) {
        return false;
    }
}"""

#: A constructed sheet rather than a `<style>` element: a frame's `style-src`
#: policy refuses the one and not the other.
ADOPT_STYLESHEET_SCRIPT = """css => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}"""


#: A frame holding a sign-in or payment field is never changed, whatever the
#: gain: those are the widgets least safe to rearrange (docs/09 P16-21).
SENSITIVE_FRAME_SCRIPT = """() => !!document.querySelector(
    'input[type="password"], input[autocomplete^="cc-"], input[autocomplete="one-time-code"]'
)"""

#: The adopted sheet kept on the window, so a release that does not hold can be
#: taken back with its marks.
ADOPT_UNDOABLE_STYLESHEET_SCRIPT = """css => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    window.__webshotReleaseSheet = sheet;
}"""

UNDO_FRAME_RELEASE_SCRIPT = """() => {
    const sheet = window.__webshotReleaseSheet;
    if (sheet) {
        document.adoptedStyleSheets = document.adoptedStyleSheets.filter(s => s !== sheet);
        delete window.__webshotReleaseSheet;
    }
    for (const name of ['data-webshot-clamped', 'data-webshot-unflexed',
                        'data-webshot-ungridded', 'data-webshot-unpositioned']) {
        for (const element of document.querySelectorAll(`[${name}]`)) element.removeAttribute(name);
    }
}"""

MARK_FRAME_RELEASED_SCRIPT = """count => {
    document.documentElement.setAttribute('data-webshot-frame-released', String(count));
}"""


async def _release_cross_origin(frame: Frame, label: str) -> tuple[int, int]:
    """Decide, try and keep or undo the release in one frame from another origin."""
    try:
        if await frame.evaluate(SENSITIVE_FRAME_SCRIPT):
            if await frame.evaluate(HIDES_TEXT_SCRIPT):
                LOGGER.info(
                    "Left %s, a frame from another origin, as it was: it holds a "
                    "sign-in or payment field.",
                    label,
                )
            return 0, 0
        released, moved = await _release_in(frame)
        if not released:
            return 0, 0
        await frame.evaluate(ADOPT_UNDOABLE_STYLESHEET_SCRIPT, RELEASE_PRINT_CSS)
        # The check the clipping stage repeats on the printed layout.
        await frame.evaluate(MARK_FRAME_RELEASED_SCRIPT, released)
        past = await frame.evaluate(RELEASED_BOXES_PAST_FRAME_SCRIPT)
        if past:
            await frame.evaluate(UNDO_FRAME_RELEASE_SCRIPT)
            await frame.evaluate(
                "() => document.documentElement.removeAttribute('data-webshot-frame-released')"
            )
            LOGGER.info(
                "Left %s, a frame from another origin, as it was: released, %d "
                "box(es) would run past the frame's own bottom edge, where nothing "
                "reports the loss. It stays reported as a scrolling box instead.",
                label,
                past,
            )
            return 0, 0
    except PlaywrightError as exc:
        LOGGER.debug("A frame from another origin could not be released: %s", exc)
        return 0, 0
    LOGGER.info(
        "Released %d scrolling box(es) inside %s, a frame from another origin: it "
        "holds no sign-in or payment field, and every released box still fits "
        "inside the frame.",
        released,
        label,
    )
    return released, moved


#: Whether any scrolling box in this document overflows: the only frames whose
#: sign-in or payment field is worth a line in the log.
HIDES_TEXT_SCRIPT = """() => [...document.querySelectorAll('body *')].some(element => {
    const style = getComputedStyle(element);
    return (style.overflowY === 'auto' || style.overflowY === 'scroll')
        && element.scrollHeight > element.clientHeight + 1;
})"""


async def _scriptable_frames(page: Page) -> list[Frame]:
    frames = []
    for frame in page.frames[1:]:
        try:
            if await frame.evaluate(SCRIPTABLE_FRAME_SCRIPT):
                frames.append(frame)
        except PlaywrightError:
            # Detached while being asked, which leaves nothing to release.
            continue
    return frames


#: A wrapper that scrolls sideways around a table: the only content WebShot
#: can make narrower, by letting the cells wrap. Anything else in such a box —
#: an image, a fixed-width block — is as wide as it is.
SCROLLING_TABLE_TEST = """(element => {
    const style = getComputedStyle(element);
    return (style.overflowX === 'auto' || style.overflowX === 'scroll')
        && (element.localName === 'table' || element.querySelector('table') !== null)
        && !element.closest('[data-webshot-hidden="true"]');
})"""


async def has_scrolling_tables(page: Page) -> bool:
    """Whether the page has a table in a sideways scroller, before paying for a probe."""
    found: bool = await page.evaluate(
        f"() => [...document.querySelectorAll('body *')].some({SCROLLING_TABLE_TEST})"
    )
    return found


async def release_wide_tables(page: Page, width: int) -> int:
    """Let a scrolling table wrap for print where it then fits the page, and count them.

    A table in an `overflow-x: auto` wrapper prints the columns scrolled into
    view and loses the rest: three of six in the measured case (docs/09
    P16-8). Letting its cells wrap fits many tables on the page. It does not
    fit all of them, and a table released anyway runs past the page edge,
    where Chromium shrinks the whole page to fit. That is a page-wide change,
    made on WebShot's guess.

    So each wrapper is tried at the width the page prints at, which the caller
    reads back from Chromium (`render/clipping.printed_width`), not at the
    capture's viewport. It is kept only if its content then fits its own box
    and the box sits inside the page. A table too wide even wrapped keeps
    scrolling, and the clipping check reports it. The viewport is put back
    afterwards, because the bundle is extracted from this page next.
    """
    viewport = page.viewport_size
    if viewport is None:
        return 0
    try:
        await page.set_viewport_size({"width": width, "height": viewport["height"]})
        released: int = await page.evaluate(
            f"""([edge, slack]) => {{
                const scrolling = {SCROLLING_TABLE_TEST};
                let count = 0;
                for (const element of document.querySelectorAll('body *')) {{
                    if (!scrolling(element)) continue;
                    // Nothing hidden, nothing to release.
                    if (element.scrollWidth <= element.clientWidth + 1) continue;
                    element.setAttribute('data-webshot-wrapped', 'true');
                    const rect = element.getBoundingClientRect();
                    const fits = element.scrollWidth <= element.clientWidth + 1
                        && rect.left >= -slack && rect.right <= edge + slack;
                    if (fits) count++;
                    else element.removeAttribute('data-webshot-wrapped');
                }}
                return count;
            }}""",
            [width, 1.0],
        )
    finally:
        await page.set_viewport_size(viewport)
    return released


@dataclass(frozen=True, slots=True)
class ConsentDialogs:
    """What `hide_consent_dialogs` hid."""

    #: Every element hidden, shown or not.
    hidden: int
    #: The vendors whose dialog was on screen, each with the selector that
    #: found it, in `CONSENT_MANAGERS` order. A manager whose container is on
    #: every page and whose dialog was already closed is hidden and not listed:
    #: hiding it changed nothing a reader would see.
    shown: tuple[tuple[str, str], ...]


#: Hides every match of each vendor's selectors, and answers which vendors had
#: something on screen. A container is "on screen" when it or anything inside
#: it, in its light tree or its open shadow root (Usercentrics draws its banner
#: in one), has a box and is visible: a manager's root is often a zero-sized
#: host for a fixed banner.
HIDE_CONSENT_DIALOGS_JS = """managers => {
    const shown = element => {
        const inside = [element, ...element.querySelectorAll('*')];
        if (element.shadowRoot) inside.push(...element.shadowRoot.querySelectorAll('*'));
        return inside.some(node => {
            const box = node.getBoundingClientRect();
            return box.width > 0 && box.height > 0 && node.checkVisibility({
                checkOpacity: true, checkVisibilityCSS: true,
                opacityProperty: true, visibilityProperty: true,
            });
        });
    };
    let hidden = 0;
    const seen = [];
    for (const [vendor, selectors] of managers) {
        let found = null;
        for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
                if (element.hasAttribute('data-webshot-hidden')) continue;
                if (found === null && shown(element)) found = selector;
                element.setAttribute('data-webshot-hidden', 'true');
                hidden++;
            }
        }
        if (found !== null) seen.push([vendor, found]);
    }
    return {hidden, seen};
}"""


async def hide_consent_dialogs(page: Page) -> ConsentDialogs:
    """Hide the consent managers' dialogs, and say which were on screen."""
    answer: dict[str, Any] = await page.evaluate(
        HIDE_CONSENT_DIALOGS_JS,
        [[vendor, list(selectors)] for vendor, selectors in CONSENT_MANAGERS.items()],
    )
    return ConsentDialogs(
        hidden=int(answer["hidden"]),
        shown=tuple(
            (str(vendor), str(selector)) for vendor, selector in answer["seen"]
        ),
    )


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def consent_dialogs_message(shown: Sequence[tuple[str, str]]) -> str:
    """The manifest's record of the consent dialogs a capture hid."""
    named = ", ".join(
        f"{_possessive(vendor)} ({selector})" for vendor, selector in shown
    )
    dialogs = "dialog" if len(shown) == 1 else "dialogs"
    return (
        f"Hid the consent manager {dialogs} on screen: {named}. A consent dialog "
        "is fixed to the window, so it would have printed over the page's text "
        "on every page, and its words would have been read into the bundle "
        "between the page's. It is the browser's session asking, not the page, "
        "so both modes hide it. Pass --exclude for any other box that covers the "
        "page."
    )


async def hide_obstructive_overlays(page: Page) -> int:
    """Hide large fixed overlays while leaving ordinary positioned content alone."""
    hidden: int = await page.evaluate(
        """() => {
            let count = 0;
            for (const element of document.querySelectorAll('body *')) {
                const style = getComputedStyle(element);
                if (style.position !== 'fixed') continue;
                const rect = element.getBoundingClientRect();
                const large = rect.width >= innerWidth * 0.45 && rect.height >= innerHeight * 0.08;
                if (large && Number(style.zIndex || 0) >= 10) {
                    element.setAttribute('data-webshot-hidden', 'true');
                    count++;
                }
            }
            return count;
        }"""
    )
    return hidden
