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
import re
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
from .headings import SEEN_HEADING_SCRIPT

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
    # A newsletter's sign-up box and a subscribe button are matched by the
    # words of a class name (`NOISE_CLASS_WORDS`), not by a selector here.
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
    # Its anchor ad, fixed to the window's bottom edge: its collapse tab
    # printed over a line of every page of a news site (measured, docs/09
    # P123-2), where the tag had written it as `ins#gpt_unit_/<network>/anchor_0`.
    "ins[id^='gpt_unit_'][id*='anchor']",
]

#: Words that make an element page chrome when one of its class names holds
#: the word whole: the name itself, a part of it set off by a hyphen or an
#: underscore, or a capitalised part of a camel-case name
#: (`newsletter-signup`, `c-newsletter__form`, `NewsletterSignup`,
#: `subscribeBox`). They were `[class*='newsletter' i]` and
#: `[class*='subscribe' i]`, which match anywhere in the attribute: a
#: compliance news site writes a post's tags as classes on the section
#: holding the post, one tag was `tag-cw-subscriber-only`, and clean mode
#: hid the section, its `<h1>` and the article with it, printed only a
#: popup, and said nothing (docs/09 P132-2). "subscriber" is not
#: "subscribe": a box for subscribers holds what they subscribed to.
#:
#: A class name that begins `tag-`, `category-` or `term-` is passed over
#: whatever it holds: WordPress writes the post's tags, categories and
#: terms with those prefixes, and they say what the post is about, not
#: what the element is. A post filed under "Newsletter" is a post.
NOISE_CLASS_WORDS: tuple[str, ...] = ("newsletter", "subscribe")

#: The share of the page's text that an element clean mode's rules match
#: must hold, with a visible `<h1>` that would go with it, for the rule to be
#: taken as wrong about it and the element kept (`hide_noise`). Half: a site
#: header holding the site's name as its `<h1>`, or a blog's sidebar holding
#: the blog's, is a small part of the page, and a theme's wrapper around the
#: article and its sidebar, matched by `.sidebar`, held 95% of the text a
#: think tank's blog post prints in clean mode (docs/09 P132-1). A comment
#: feed can hold more than half of a page's text and is still removed: it
#: holds no `<h1>`.
NOISE_CONTENT_SHARE = 0.5

#: What every element clean mode's rules hid is marked with, as a selector.
NOISE_MARK = "[data-webshot-hidden-by='noise']"

#: `classWord(element, wanted)`: the first word of `wanted` (a Set of
#: lower-case words) that one of the element's class names holds whole, as
#: `NOISE_CLASS_WORDS` says, or null.
CLASS_WORD_JS = r"""
    const classWord = (element, wanted) => {
        for (const name of (element.getAttribute('class') || '').split(/\s+/)) {
            if (!name || /^(?:tag|category|term)-/i.test(name)) continue;
            for (const part of name.split(/[-_]+/)) {
                const words = part
                    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
                    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
                    .split(' ');
                for (const word of words) {
                    if (wanted.has(word.toLowerCase())) return word.toLowerCase();
                }
            }
        }
        return null;
    };
"""

#: `noiseRules(selectors, words)`: every element clean mode's rules match, in
#: the document's own tree, mapped to the first rule that matched it, named
#: as the warning names it. Shared by the lazy-load pass's measurement and
#: by `hide_noise`, so the two match alike (docs/09 P23-8).
NOISE_RULES_JS = (
    CLASS_WORD_JS
    + r"""
    const noiseRules = (selectors, words) => {
        const matched = new Map();
        for (const selector of selectors) {
            let elements;
            try { elements = document.querySelectorAll(selector); }
            catch (error) { throw new Error(`Invalid CSS selector '${selector}': ${error.message}`); }
            for (const element of elements) {
                if (!matched.has(element)) matched.set(element, selector);
            }
        }
        if (words.length) {
            const wanted = new Set(words.map(word => word.toLowerCase()));
            for (const element of document.querySelectorAll('[class]')) {
                if (matched.has(element)) continue;
                const word = classWord(element, wanted);
                if (word) matched.set(element, `a class name holding "${word}"`);
            }
        }
        return matched;
    };
"""
)

#: Consent managers' dialogs, by vendor, as each vendor's script writes them.
#: Hidden in both modes (docs/04-spec.md §5 item 5): the dialog is the
#: browser's session asking a question, not the page, and it is fixed to the
#: viewport, so Chromium prints it on every page over the text beneath it, and
#: its words are read into the bundle between the article's. Cookiebot's was
#: measured, printed on five of six pages of one article in clean mode as well
#: as faithful (docs/09 P79), Osano's on two of three (P97-4), and Complianz's
#: and CookieYes' on every page of two more (P124-1); the others follow their
#: vendors' documented markup and were not met in a capture. Each selector is
#: a name the vendor gave its own element, so none matches a page's own box the
#: way the noise list's `[class*='cookie-consent' i]` can, which is why that
#: list stays clean mode's alone.
CONSENT_MANAGERS: dict[str, tuple[str, ...]] = {
    "Cookiebot": ("#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay"),
    "OneTrust": ("#onetrust-consent-sdk",),
    "Didomi": ("#didomi-host",),
    "Quantcast Choice": (".qc-cmp2-container",),
    "TrustArc": ("#truste-consent-track",),
    "Usercentrics": ("#usercentrics-root", "#usercentrics-cmp-ui"),
    "Sourcepoint": ("[id^='sp_message_container']",),
    # Measured: its banner shown on load and hidden by its own script five
    # seconds later, so a quick capture printed it mid-fade (docs/09 P97-4).
    "Osano": (".osano-cm-window", ".osano-cm-dialog"),
    # Measured: a WordPress plugin's banner, fixed to the window, printed on
    # all 13 pages of a legal summary and over its text on 11, inside the
    # container named first, which holds a banner per consent type with all
    # but one kept hidden. `#cmplz-manage-consent`, the button that reopens
    # the banner, follows the plugin's markup and was not met (docs/09
    # P124-1).
    "Complianz": (
        "#cmplz-cookiebanner-container",
        ".cmplz-cookiebanner",
        "#cmplz-manage-consent",
    ),
    # Measured: its centred popup printed over 726 characters on all 5 pages
    # of a news article. These four are the classes of the stylesheet that
    # printed the article clean: the popup, the overlay behind it, its
    # preferences dialog and the button that reopens it (docs/09 P124-1).
    "CookieYes": (
        ".cky-consent-container",
        ".cky-overlay",
        ".cky-modal",
        ".cky-btn-revisit-wrapper",
    ),
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
/* A box wider than the printed page, capped at its container's width and let
   shrink below its content's widest line, in clean mode, where
   `render/clipping.release_wide_boxes` measured that it keeps more text on the
   page (docs/09 P123-3). */
[data-webshot-reflowed='true']:not([data-webshot-hidden='true']) {
    min-width: 0 !important;
    max-width: 100% !important;
}
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
#: does not fit. Text 0.1px tall that close to a page's top edge lost every
#: glyph that does not dip below the baseline: "Revenue 2026" printed as
#: "eeue 06" (docs/09 P16-20).
#:
#: The spacing the page sets is put back to the font's own. The layer inherits
#: the asset's surroundings, and a page that spaced its links 2px apart set
#: every letter of the layer's text many letters' width from the next: a text reader
#: took each letter for a word ("v i s u a l a s s e t"). A long word still
#: breaks where it would run past the asset's box, as it may have before, so
#: the layer never reaches past the page (docs/09 P135-4).
#:
#: The text is 0.5px tall, in the platform's regular sans-serif. At 0.1px,
#: PDFium, which reads the text layer for every check WebShot runs on the
#: print and underlies Chrome's own PDF viewer, left out each glyph whose ink is a
#: thin stroke: "Tide tables" read "Tde tab es" in the Linux CI's fonts,
#: "illicit" read "i c t" in Verdana, and "|" went in Courier even at 0.2px.
#: Its limit is on the glyph as set, not as scaled: 0.3px kept every glyph in
#: every font measured, and 0.5px leaves room for a thin face, whose weight
#: and family the layer no longer takes from the page (docs/09 P135-4).
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
    "font-family: sans-serif !important",
    "font-size: 0.5px !important",
    "font-style: normal !important",
    "font-weight: 400 !important",
    "line-height: 0.5px !important",
    "color: rgba(0, 0, 0, 0.01) !important",
    "white-space: pre-wrap !important",
    "border: 0 !important",
    "pointer-events: none !important",
    "letter-spacing: normal !important",
    "word-spacing: normal !important",
    "text-indent: 0 !important",
    "text-transform: none !important",
    "word-break: normal !important",
    "overflow-wrap: anywhere !important",
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

#: What a link's `::before` and `::after` draw may break anywhere in clean
#: mode. A print stylesheet that writes each link's address after it,
#: `a[href]::after { content: " (" attr(href) ")" }`, set an archive's long
#: addresses as words with no break in them, and 35 of them ran past the
#: page's edge (docs/09 P135-6). Leaving the addresses out was built and taken
#: back: it dropped the citations Wikipedia's and HTML5 Boilerplate's print
#: stylesheets write, which P70-16 keeps. Wrapping keeps every address. In a
#: paragraph it moves only one that cannot fit. `anywhere`, unlike
#: `break-word`, also takes the address out of its box's narrowest width, so a
#: table cell or a flex item holding one can shrink, and the address breaks
#: there too, where unbroken it widened its column and pushed the column beside
#: it off the page. `text-wrap-mode` lets the address wrap after a link the page sets
#: `nowrap`, and leaves the page's handling of spaces alone, so an address a
#: stylesheet puts on its own line with `"\A"` under `white-space: pre` stays
#: there. Faithful mode prints what the stylesheet sets, and the clipping
#: check names what the edge cuts (docs/09 P123-10, P139).
CLEAN_PRINT_CSS = """
a[href]::before, a[href]::after {
    overflow-wrap: anywhere !important;
    text-wrap-mode: wrap !important;
}
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

#: The running header and footer print in the page's top and bottom margins,
#: and Chromium honours a page's own `@page` margin over the one `page.pdf` is
#: given. pdf.js's `viewer.css`, loaded by a catalogue page for an embedded
#: viewer, carries `@page { margin: 0 }`, so the page's fixed bar printed
#: over the header's title and URL, and the text layer interleaved the two
#: (docs/09 P113-5, P115).
#:
#: `revert`, not `--margin`'s own value: `revert` hands the margin back to the
#: one `page.pdf` was given, and a page with no `@page` rule prints with this
#: sheet byte for byte as it did without it. The same length written into the
#: CSS rounds differently: `0.6in` there put the text 0.75 pt lower than
#: `0.6in` passed to `page.pdf` (P115).
#:
#: Adopted, like the clipping probe's sheet: a `style-src` policy refuses a
#: `<style>` element and not a constructed sheet, and the bundle's snapshot
#: reads no adopted sheet. `!important` beats every `@page` margin a page sets
#: without it, wherever the rule sits. A page's own `!important` margin still
#: wins: Chromium lets the *earliest* important `@page` declaration win, and an
#: adopted sheet always comes last (P115).
HEADER_FOOTER_MARGINS_CSS = "@page { margin: revert !important; }"


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

#: How long past `FRAME_TIMEOUT_S` the pass waits for the page to answer at
#: all. The page's own timer answers when the wait runs out, but only while its
#: main thread runs. In Chromium 156 a GPU process that stops drawing can hold
#: the page's main thread as well; then nothing answered, and the pass waited
#: until the GPU process drew again, or forever (docs/09 P100). Five times
#: `LATE_TIMER_S`: a timer later than that is reported as no answer, and the
#: warning says the same of both, that the page cannot say which side stopped.
ANSWER_GRACE_S = 5.0


def _answer_bound_s() -> float:
    """How long the pass waits for any answer from the page.

    The frame wait's whole bound, and the bound on a step's measurement and
    its scroll too. Neither of those waits on a frame, so a page answers them
    at once while its main thread runs; but a stopped GPU process can hold
    that thread before the pass has scrolled at all, and the first
    measurement then never answered (docs/09 P100-5). No longer than the
    frame wait, so nothing is cut short here that it would have waited for.
    """
    return FRAME_TIMEOUT_S + ANSWER_GRACE_S


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
#: `hide_noise` and `hide_elements` match them, in the document's own tree,
#: and counted into open shadow roots, where a component's content lives.
#: An element clean mode's rules match that holds a visible `<h1>` is counted
#: as kept: `hide_noise` keeps such an element when it holds most of the
#: page's text (docs/09 P132-1), which takes the content region and the
#: printed text to decide, and neither is known while the page is scrolled.
#: Counting a site header or a blog's sidebar kept is the safe side: the pass
#: watches more of the page, and only scrolls longer. `keptBottom` is the
#: lowest box among kept elements with no element children: a container's box
#: stretches with whatever its column holds, the removed feed included, and a
#: leaf's does not. `regions` sizes each outermost removed element, so the
#: warning can name the one that grew rather than guess.
LAZY_LOAD_STATE_JS = (
    """({noise, words, excluded}) => {"""
    + NOISE_RULES_JS
    + """
    const state = {
        y: window.scrollY,
        viewport: window.innerHeight,
        height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
        href: location.href, title: document.title,
        kept: null, keptBottom: 0, regions: {},
    };
    if (!noise.length && !words.length && !excluded.length) return state;
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
    const chrome = new Set(noiseRules(noise, words).keys());
    for (const heading of document.querySelectorAll('h1')) {
        if (!heading.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
        for (let node = heading; node; node = node.parentElement) chrome.delete(node);
    }
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
)


#: How many steps in a row the window may stay where it was, short of the
#: page's end, with the page not growing, before the pass stops: the window
#: can scroll no further. A policy page's window stopped short of the height
#: the pass measured, and the pass ran to its limit, 100 steps and then 300,
#: on a page that was complete (docs/09 P120-5).
UNMOVED_STEPS = 3

#: Remembered at the pass's first step: the articles the page had as it loaded.
KEEP_LOADED_ARTICLES_JS = """() => {
    window.__webshotLoadedArticles = new WeakSet(document.querySelectorAll('article'));
}"""

#: Once the page has changed its address while scrolled, hides each article it
#: added since the first step that is not inside another, and says how many.
HIDE_LATER_ARTICLES_JS = """() => {
    const loaded = window.__webshotLoadedArticles;
    if (!loaded) return 0;
    let hidden = 0;
    for (const article of document.querySelectorAll('article')) {
        if (loaded.has(article)) continue;
        if (article.parentElement && article.parentElement.closest('article')) continue;
        article.setAttribute('data-webshot-hidden', 'true');
        article.setAttribute('data-webshot-hidden-by', 'appended');
        hidden += 1;
    }
    return hidden;
}"""


#: Puts back the title and address the page loaded with, once it has changed
#: them while scrolled, so every stage that reads them from the page (the
#: snapshot's `page_metadata.title`, `content.html`'s `<title>`, discovery)
#: names the article the capture keeps, as the report and the header do: the
#: first fix set only those two (review of #367). The address is put back only
#: where the History API allows, on the same origin, as the page changed it.
RESTORE_LOADED_PAGE_JS = """([title, url]) => {
    if (typeof title === 'string') document.title = title;
    try {
        if (url && location.href !== url) history.replaceState(history.state, '', url);
    } catch (error) {}
    return [document.title, location.href];
}"""


async def restore_loaded_page(page: Page, result: LazyLoadPass) -> None:
    """After a `navigated` pass, give the page back its first title and address."""
    if result.ended != "navigated":
        return
    await page.evaluate(
        RESTORE_LOADED_PAGE_JS, [result.loaded_title, result.loaded_url]
    )


def _without_fragment(url: str) -> str:
    """The address a page is at, less the fragment a scroll may change."""
    return url.split("#", 1)[0]


@dataclass(frozen=True, slots=True)
class FrameStall:
    """What the page could see when no frame came within `FRAME_TIMEOUT_S` of a scroll.

    The page cannot see why the browser drew nothing, but it can rule out
    itself: whether it was hidden, whether it had been running, and whether
    it had been drawn before (docs/09 P76). A page that did not answer within
    `ANSWER_GRACE_S` of the wait running out rules out nothing, and every
    field is None (docs/09 P100).
    """

    #: `document.visibilityState`. A browser does not render a hidden page.
    visibility: str | None
    #: Whether the page had a first paint on record: whether a frame of it
    #: had ever been drawn.
    painted: bool | None
    #: How much later than asked the page's own timer fired, in seconds. A
    #: page whose main thread was busy, or not run at all, fires it late.
    late_s: float | None

    @property
    def answered(self) -> bool:
        """Whether the page said anything at all when the wait ran out."""
        return self.visibility is not None


@dataclass(frozen=True, slots=True)
class LazyLoadPass:
    """What the lazy-load pass did, for the log, the manifest and a crash report."""

    steps: int
    #: `bottom`: the window reached the end of a page that stopped growing.
    #: `removed-growth`: the page was still growing, only inside regions the
    #: capture removes. `limit`: `--max-scrolls` ran out first. `unrendered`:
    #: the page did not render within `FRAME_TIMEOUT_S` of a scroll.
    #: `unanswered`: the page did not answer a step's measurement or its
    #: scroll within the pass's bound (docs/09 P100-5). `navigated`: the page
    #: changed its address while it was scrolled (P120-4). `unmoved`: the
    #: window could scroll no further, short of the page's end (P120-5).
    ended: Literal[
        "bottom",
        "removed-growth",
        "limit",
        "unrendered",
        "unanswered",
        "navigated",
        "unmoved",
    ]
    #: The document's height at the last step, in CSS pixels.
    height: int
    #: Its height at the first step, so a warning can say how much the page
    #: grew while it was scrolled (docs/09 P105-1).
    first_height: int = 0
    #: How many times the page grew right after the window had come within
    #: a window's height of its end: what an endless feed does, which loads
    #: more before the window reaches the last line, and what a long page the
    #: window is still far from the end of does not.
    end_growths: int = 0
    #: For `removed-growth`: each removed region that grew while the kept
    #: content stood still, as (name, what removes it: `clean` or `exclude`).
    growing: tuple[tuple[str, str], ...] = ()
    #: For `removed-growth`: the steps the kept content held still for.
    steady_steps: int = 0
    #: For `unrendered`: what the page could see when the wait ran out.
    stall: FrameStall | None = None
    #: The page's address and title at the first step, before anything was
    #: scrolled; None when the pass measured nothing.
    loaded_url: str | None = None
    loaded_title: str | None = None
    #: For `navigated`: the address and title the page changed to, and how
    #: many articles it had appended that the capture hides.
    navigated_url: str | None = None
    navigated_title: str | None = None
    hidden_articles: int = 0
    #: For `unmoved`: how far down the page the window's bottom edge got.
    window_bottom: int = 0


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
    noise_words: Sequence[str] = (),
    excluded: Sequence[str] = (),
) -> LazyLoadPass:
    """Incrementally scroll through the page to activate viewport-based lazy loading.

    `noise` and `excluded` are the selectors the capture will hide (clean
    mode's page chrome, and `--exclude`), and `noise_words` the class-name
    words clean mode hides by (`NOISE_CLASS_WORDS`). Growth inside what they
    match does not keep the pass going once the rest of the page has stopped
    changing.
    """
    LOGGER.info("Loading lazy content (up to %d scroll steps)...", max_scrolls)
    stable_steps = 0
    previous_height = 0
    previous_y: float | None = None
    unmoved_steps = 0
    window_bottom = 0
    loaded_url: str | None = None
    loaded_title: str | None = None
    navigated_url: str | None = None
    navigated_title: str | None = None
    hidden_articles = 0
    ended: Literal[
        "bottom",
        "removed-growth",
        "limit",
        "unrendered",
        "unanswered",
        "navigated",
        "unmoved",
    ] = "limit"
    growing: tuple[tuple[str, str], ...] = ()
    steady: _Steady | None = None
    steady_steps = 0
    stall: FrameStall | None = None
    steps = 0
    height = 0
    first_height = 0
    end_growths = 0
    was_at_end = False
    selectors = {
        "noise": list(noise),
        "words": list(noise_words),
        "excluded": list(excluded),
    }

    for steps in range(1, max_scrolls + 1):
        try:
            state = await asyncio.wait_for(
                page.evaluate(LAZY_LOAD_STATE_JS, selectors), _answer_bound_s()
            )
        except TimeoutError:
            ended = "unanswered"
            break
        height = state["height"]
        href = state.get("href")
        if steps == 1:
            first_height = height
            loaded_url = href
            loaded_title = state.get("title")
            try:
                await asyncio.wait_for(
                    page.evaluate(KEEP_LOADED_ARTICLES_JS), _answer_bound_s()
                )
            except TimeoutError:
                ended = "unanswered"
                break
        elif (
            href is not None
            and loaded_url is not None
            and _without_fragment(href) != _without_fragment(loaded_url)
        ):
            # A news site appends its next article as the window nears the end
            # of one and puts that article's address and title on the page:
            # the capture printed ten articles under the tenth one's title and
            # address (docs/09 P120-4). A change of fragment alone is a page
            # marking where the reader is, and scrolling goes on.
            ended = "navigated"
            navigated_url = href
            navigated_title = state.get("title")
            try:
                hidden_articles = int(
                    await asyncio.wait_for(
                        page.evaluate(HIDE_LATER_ARTICLES_JS), _answer_bound_s()
                    )
                )
            except TimeoutError:
                hidden_articles = 0
            break
        bottom = state["y"] + state["viewport"]
        if was_at_end and height > previous_height:
            end_growths += 1
        was_at_end = bottom >= height - state["viewport"]
        if bottom >= height - 4:
            stable_steps = stable_steps + 1 if height == previous_height else 0
            unmoved_steps = 0
            if stable_steps >= 3:
                ended = "bottom"
                break
        else:
            stable_steps = 0
            # Short of the end, where the last scroll left the window, and
            # the page no taller: the window can go no further (P120-5).
            if state["y"] == previous_y and height == previous_height:
                unmoved_steps += 1
                if unmoved_steps >= UNMOVED_STEPS:
                    ended = "unmoved"
                    window_bottom = int(bottom)
                    break
            else:
                unmoved_steps = 0
        previous_height = height
        previous_y = state["y"]
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
        try:
            await asyncio.wait_for(page.evaluate(SCROLL_STEP_JS), _answer_bound_s())
        except TimeoutError:
            ended = "unanswered"
            break
        # The delay starts once the page has seen the scroll, not when it was
        # asked for: until it renders, the page has not seen it (docs/09 P34).
        # Bounded here too: the page's own bound needs its main thread to run.
        try:
            seen = await asyncio.wait_for(
                page.evaluate(NEXT_FRAME_JS, FRAME_TIMEOUT_S * 1000),
                _answer_bound_s(),
            )
        except TimeoutError:
            seen = None
        if seen is not True:
            ended = "unrendered"
            stall = (
                FrameStall(visibility=None, painted=None, late_s=None)
                if seen is None
                else FrameStall(
                    visibility=seen["visibility"],
                    painted=seen["painted"],
                    late_s=max(0.0, seen["lateMs"] / 1000),
                )
            )
            break
        await asyncio.sleep(delay_s)

    if ended == "unanswered" or (stall is not None and not stall.answered):
        # A page that did not answer would not answer this either. Sent, so it
        # runs before anything later once the page runs again; not waited on
        # past the grace, so the pass ends and says why.
        try:
            await asyncio.wait_for(page.evaluate(SCROLL_TO_TOP_JS), ANSWER_GRACE_S)
        except TimeoutError:
            LOGGER.info("The page has not yet answered the scroll back to the top.")
    else:
        await page.evaluate(SCROLL_TO_TOP_JS)
    LOGGER.info("Lazy-load pass finished after %d step(s).", steps)
    return LazyLoadPass(
        steps=steps,
        first_height=first_height,
        end_growths=end_growths,
        ended=ended,
        height=height,
        growing=growing,
        steady_steps=steady_steps,
        stall=stall,
        loaded_url=loaded_url,
        loaded_title=loaded_title,
        navigated_url=navigated_url,
        navigated_title=navigated_title,
        hidden_articles=hidden_articles,
        window_bottom=window_bottom,
    )


def lazy_load_warning(result: LazyLoadPass, max_scrolls: int) -> str | None:
    """The manifest warning for a lazy-load pass that did not reach the page's end."""
    if result.ended == "limit":
        # Said with what the pass saw: a news page's endless feed ran the pass
        # to its limit and printed 66 pages of other stories after a two-page
        # article, and the warning said only that a limit was reached
        # (docs/09 P105-1). A feed grows as the window nears its end; a
        # long article that grew earlier, from its own lazy pictures, and is
        # longer than the steps reach needs more steps, not fewer (review of
        # #316).
        if result.end_growths:
            times = "once" if result.end_growths == 1 else f"{result.end_growths} times"
            return (
                f"Lazy-load scrolling reached the {max_scrolls}-step safety limit "
                f"with the page still loading more at its end: {times} the window "
                "came near the end of the page and the page grew, from "
                f"{result.first_height:,} to {result.height:,} pixels tall over "
                "the pass, as an endless feed of other stories does. Everything "
                "it loaded is in the capture, so the PDF may run on long past the "
                "page's own content. Pass --no-scroll, or a lower --max-scrolls, "
                "to stop sooner."
            )
        return (
            f"Lazy-load scrolling reached the {max_scrolls}-step safety limit "
            f"before the window settled at the end of the page ({result.height:,} "
            "pixels tall), so content further down that loads only when it is "
            "scrolled to may not have loaded. Pass a higher --max-scrolls to go "
            "further."
        )
    if result.ended == "bottom":
        return None
    if result.ended == "navigated":
        named = f' ("{result.navigated_title}")' if result.navigated_title else ""
        if result.hidden_articles:
            count = result.hidden_articles
            kept = (
                f", and leaves out the {count} article"
                f"{'' if count == 1 else 's'} the page appended while it was "
                "scrolled"
            )
        else:
            kept = (
                ". What the page appended before the pass stopped is in it; "
                "--no-scroll captures the page as it loaded"
            )
        return (
            f"The page changed its address while it was scrolled, from "
            f"{result.loaded_url} to {result.navigated_url}{named}, as a site "
            "that appends its next article does, so the lazy-load pass stopped "
            f"at step {result.steps}. The capture keeps the first address and "
            f"title{kept}."
        )
    if result.ended == "unmoved":
        return (
            f"The lazy-load pass stopped at step {result.steps}: the window "
            f"could scroll no further than {result.window_bottom:,} pixels down a "
            f"page measured at {result.height:,}, and the page did not grow for "
            f"{UNMOVED_STEPS} steps. Content below that the page loads only as it "
            "is scrolled, or that a box of the page's own scrolls into view, may "
            "not have loaded."
        )
    if result.ended == "unanswered":
        # Nothing of the page is claimed: it said nothing, not even what a
        # stalled frame wait's timer reports (docs/09 P100-5).
        return (
            f"The lazy-load pass stopped at step {result.steps}: the page did "
            "not answer when asked to measure itself or to scroll, within "
            f"{_answer_bound_s():g} seconds. Its main thread was busy or not "
            "run, or its answer could not get out of the browser, so content "
            "it loads as it is scrolled may not have been loaded."
        )
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

    The first that holds is the one said: a page that said nothing rules
    nothing out, a hidden page explains the rest, and a page that was not
    running cannot say what the browser was doing.
    """
    if not stall.answered:
        return (
            "The page did not answer at all: its own timer, due when the wait "
            f"ran out, had not reported {ANSWER_GRACE_S:g} seconds later. Its "
            "main thread was busy or not run, or its answer could not get out "
            "of the browser, so it cannot say whether the browser or the page "
            "stopped."
        )
    if stall.visibility != "visible":
        return (
            f'The page was hidden (its visibilityState was "{stall.visibility}"), '
            "and a browser does not render a hidden page."
        )
    if stall.late_s is not None and stall.late_s >= LATE_TIMER_S:
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
                    if (sibling !== node) {
                        sibling.setAttribute('data-webshot-hidden', 'true');
                        sibling.setAttribute('data-webshot-hidden-by', 'selector');
                    }
                }
                if (parent !== document.body) parent.setAttribute('data-webshot-ancestor', 'true');
                node = parent;
            }
        }"""
    )


#: `--exclude` as a stylesheet as well as marks, so an element the page adds
#: after preparation is hidden too. A news site's slide-out arrived on a timer
#: once the marks were made, matched the `--exclude` given for it, and printed
#: over 532 characters on every page (docs/09 P101-1). Adopted, so the sheet
#: is in no document a snapshot reads; one rule per selector, each inserted
#: alone, so a selector can only ever be a selector. Answers the selectors
#: whose rule did not take.
EXCLUDE_SHEET_JS = """selectors => {
    const sheet = new CSSStyleSheet();
    const refused = [];
    for (const selector of selectors) {
        try { sheet.insertRule(`${selector} { display: none !important; }`, sheet.cssRules.length); }
        catch (error) { refused.push(selector); }
    }
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    return refused;
}"""


async def keep_excluded_hidden(page: Page, selectors: Sequence[str]) -> str | None:
    """Hide what `selectors` match from now on, and say if one could not be.

    `hide_elements` has already marked what they match now; this covers what
    the page adds before the print. The warning is for a selector that
    `querySelectorAll` accepts and a stylesheet does not.
    """
    if not selectors:
        return None
    refused: list[str] = await page.evaluate(EXCLUDE_SHEET_JS, list(selectors))
    if not refused:
        return None
    named = ", ".join(f'"{selector}"' for selector in refused)
    return (
        f"--exclude {named} hid what it matched when the page was prepared, but "
        "could not be applied as a stylesheet, so an element it matches that the "
        "page adds later may still print."
    )


async def adopt_stylesheet(page: Page, css: str) -> None:
    """Give the page's document `css` as a constructed, adopted stylesheet.

    How WebShot's print CSS, the clean mode's and `--css` reach the page. They
    were `<style>` elements, which a page served with a `style-src` policy
    that allows no inline style refuses; Playwright's `add_style_tag` reports
    the refusal as an error, and the capture ended with exit 5 before anything
    printed (docs/09 P115-4, P116). The policy does not govern a constructed
    sheet, which the clipping probe, `--exclude` and the ligature sheet
    already rely on.

    Adopted sheets come after every sheet of the document's own in the
    cascade, so these rules now follow a page's `<style>` or `<link>` in its
    body, and any sheet the page had adopted, where a `<style>` appended to
    the head came before them. A constructed sheet cannot hold `@import`:
    `css_imports` finds what one drops (P116).
    """
    await page.evaluate(ADOPT_STYLESHEET_SCRIPT, css)


_AT_KEYWORD = re.compile(r"@(-?[A-Za-z_][\w-]*)")

#: The statements that may come before an `@import` and leave it in force.
_SHEET_HEAD_RULES = frozenset({"charset", "import", "layer"})


def css_imports(css: str) -> list[str]:
    """The `@import` rules in a stylesheet's text, where CSS would honour them.

    That is before every rule but `@charset` and `@layer` statements. An
    `@import` anywhere else is dropped by a `<style>` element too, so it never
    applied and dropping it costs nothing. Each is answered as written after
    `@import`, its whitespace collapsed, for a warning to name: a constructed
    sheet drops them all with only a console message (docs/09 P116).
    """
    imports: list[str] = []
    position = 0
    while True:
        position = _skip_space_and_comments(css, position)
        keyword = _AT_KEYWORD.match(css, position)
        if keyword is None or keyword.group(1).lower() not in _SHEET_HEAD_RULES:
            return imports
        end = _statement_end(css, keyword.end())
        if end is None:
            # A block, as `@layer base { … }` has, is a rule: no `@import`
            # after it counts.
            return imports
        if keyword.group(1).lower() == "import":
            imports.append(" ".join(css[keyword.end() : end].split()))
        position = end + 1


def _skip_space_and_comments(css: str, position: int) -> int:
    """Past whitespace, comments and the `<!--` and `-->` a sheet may hold."""
    while position < len(css):
        if css[position].isspace():
            position += 1
        elif css.startswith("/*", position):
            close = css.find("*/", position + 2)
            position = len(css) if close < 0 else close + 2
        elif css.startswith("<!--", position):
            position += 4
        elif css.startswith("-->", position):
            position += 3
        else:
            break
    return position


def _statement_end(css: str, position: int) -> int | None:
    """Where the at-rule from `position` ends: its `;`, or the sheet's end.

    `None` when a `{` comes first, which makes it a block. Strings, comments,
    escapes and parentheses are stepped over, so a `;` in a URL is not an end.
    """
    depth = 0
    while position < len(css):
        character = css[position]
        if character == "\\":
            position += 2
            continue
        if css.startswith("/*", position):
            close = css.find("*/", position + 2)
            position = len(css) if close < 0 else close + 2
            continue
        if character in "\"'":
            position += 1
            while position < len(css) and css[position] not in (character, "\n"):
                position += 2 if css[position] == "\\" else 1
        elif character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and character == "{":
            return None
        elif depth == 0 and character == ";":
            return position
        position += 1
    return len(css)


def dropped_imports_message(imports: Sequence[str]) -> str:
    """The warning for `--css` `@import` rules a constructed sheet dropped."""
    named = "; ".join(f"@import {rule}" for rule in imports)
    return (
        f"--css: {len(imports)} @import rule(s) were not applied ({named}). "
        "WebShot installs --css as an adopted stylesheet, which a page's "
        "style-src policy cannot refuse and which cannot hold @import; copy "
        "the imported rules into the file instead."
    )


async def hide_elements(page: Page, selectors: Sequence[str], reason: str) -> int:
    """Mark what `selectors` match to leave out, and record `reason` on it.

    The reason is what `unmark_shown` names when the page's print CSS shows
    a marked box anyway (docs/09 P128): `noise` for clean mode's selectors,
    `exclude` for `--exclude`. The last rule to mark a box sets its reason,
    and `--exclude` runs last, so the user's choice wins.
    """
    if not selectors:
        return 0
    hidden: int = await page.evaluate(
        """([selectors, reason]) => {
            let count = 0;
            for (const selector of selectors) {
                let elements;
                try { elements = document.querySelectorAll(selector); }
                catch (error) { throw new Error(`Invalid CSS selector '${selector}': ${error.message}`); }
                for (const element of elements) {
                    if (!element.hasAttribute('data-webshot-hidden')) count++;
                    element.setAttribute('data-webshot-hidden', 'true');
                    element.setAttribute('data-webshot-hidden-by', reason);
                }
            }
            return count;
        }""",
        [list(selectors), reason],
    )
    return hidden


#: Clean mode's removal of page chrome, and its two exceptions (docs/09
#: P132-1). An element its rules match is kept, with every element the rules
#: match around it, when it is the content region or holds it
#: (`data-webshot-root`, which `--selector` and the auto-selector set): a
#: think tank's theme classes the wrapper around its article and the
#: article's sidebar `sidebar`, `--selector` named the article inside it,
#: and the capture printed an empty page. And when it holds a visible `<h1>`
#: and more than `share` of the text the page would print with it kept: what
#: holds the page's heading and most of its words is the page, whatever its
#: class says. Both are measured before anything is printed, on the page as
#: a reader saw it, and text the capture has already marked to leave out (the
#: content selector's siblings, a consent dialog) is not counted.
#:
#: Answers what it hid, what it kept and why, and the outermost elements it
#: hid that held the most text, for the empty-capture warning to name.
HIDE_NOISE_JS = (
    r"""({selectors, words, share}) => {"""
    + SEEN_HEADING_SCRIPT
    + NOISE_RULES_JS
    + r"""
    const matched = noiseRules(selectors, words);
    const marked = element => element.closest('[data-webshot-hidden="true"]') !== null;
    const named = element => {
        const cls = (element.getAttribute('class') || '').trim().split(/\s+/)[0];
        return element.localName + (element.id ? `#${element.id}` : cls ? `.${cls}` : '');
    };
    const unrendered = new Set(['script', 'style', 'noscript', 'template']);
    // The letters and digits a reader sees in `scope`, outside `skip` and
    // outside what the capture already marked to leave out.
    const characters = (scope, skip) => {
        const shown = new Map();
        let count = 0;
        const walker = document.createTreeWalker(scope, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
            acceptNode: node => {
                if (node.nodeType === Node.TEXT_NODE) return NodeFilter.FILTER_ACCEPT;
                if (skip.has(node) || unrendered.has(node.localName)
                    || node.getAttribute('data-webshot-hidden') === 'true') return NodeFilter.FILTER_REJECT;
                return NodeFilter.FILTER_SKIP;
            },
        });
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const parent = node.parentElement;
            if (!parent) continue;
            if (!shown.has(parent)) {
                shown.set(parent, parent.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
            }
            if (shown.get(parent)) count += node.data.replace(/[^\p{L}\p{N}]/gu, '').length;
        }
        return count;
    };
    const kept = new Map();
    const keep = (element, why, held) => {
        for (let node = element; node; node = node.parentElement) {
            if (matched.has(node) && !kept.has(node)) kept.set(node, {why, held});
        }
    };
    const root = document.querySelector('[data-webshot-root="true"]');
    if (root) keep(root, 'root', null);
    const headings = [...document.querySelectorAll('h1')].filter(
        heading => seen(heading) && textOf(heading) && !marked(heading));
    // Each heading's chain: the elements the rules match around it, the
    // outermost last. The text is weighed against what the page would print
    // with every chain kept and the rest of the chrome hidden, so a site
    // header holding the site's name is weighed against the article beside
    // it, not against itself.
    const chains = [];
    const around = new Set();
    for (const heading of headings) {
        const chain = [];
        for (let node = heading; node; node = node.parentElement) {
            if (matched.has(node) && !kept.has(node) && !marked(node)) chain.push(node);
        }
        if (chain.length) chains.push(chain);
        for (const node of chain) around.add(node);
    }
    if (chains.length) {
        const skip = new Set([...matched.keys()].filter(other => !kept.has(other) && !around.has(other)));
        const total = characters(document.body, skip);
        const weighed = new Map();
        for (const chain of chains) {
            const outermost = chain[chain.length - 1];
            if (!weighed.has(outermost)) weighed.set(outermost, characters(outermost, skip));
            const inside = weighed.get(outermost);
            if (total > 0 && inside > share * total) keep(chain[0], 'heading', [inside, total]);
        }
    }
    const hidden = [];
    let count = 0;
    for (const [element, rule] of matched) {
        if (kept.has(element)) continue;
        if (!element.hasAttribute('data-webshot-hidden')) count++;
        hidden.push([element, rule]);
    }
    // Measured before anything is marked, which the count would then skip.
    const outermost = hidden.filter(([element]) =>
        !hidden.some(([other]) => other !== element && other.contains(element)));
    const removed = outermost
        .filter(([element]) => !marked(element))
        .map(([element, rule]) => ({name: named(element), rule, characters: characters(element, new Set())}))
        .filter(box => box.characters > 0)
        .sort((a, b) => b.characters - a.characters);
    for (const [element] of hidden) {
        element.setAttribute('data-webshot-hidden', 'true');
        element.setAttribute('data-webshot-hidden-by', 'noise');
    }
    const keptBoxes = [...kept]
        .filter(([element]) => ![...kept.keys()].some(
            other => other !== element && other.contains(element)))
        .map(([element, {why, held}]) => ({
            name: named(element), rule: matched.get(element), why,
            characters: held ? held[0] : null, total: held ? held[1] : null,
        }));
    return {count, kept: keptBoxes, removed};
}"""
)


@dataclass(frozen=True, slots=True)
class NoiseBox:
    """An element clean mode's rules matched, named as the warnings name it."""

    #: `element#id` or `element.class`.
    name: str
    #: The rule that matched it: a selector, or the class-name word.
    rule: str
    #: The letters and digits of text it showed, where measured.
    characters: int | None = None
    #: For one kept: `root` (it holds the content region) or `heading` (it
    #: holds a visible `<h1>` and most of the text), and the text the page
    #: would print with it kept, when that was measured.
    why: str = ""
    total: int | None = None


@dataclass(frozen=True, slots=True)
class NoiseRemoval:
    """What `hide_noise` did."""

    #: Elements it marked that were not marked before.
    hidden: int = 0
    #: The outermost elements its rules matched and it kept.
    kept: tuple[NoiseBox, ...] = ()
    #: The outermost elements it hid that showed text, the most first.
    removed: tuple[NoiseBox, ...] = ()

    @property
    def removed_characters(self) -> int:
        return sum(box.characters or 0 for box in self.removed)


async def hide_noise(
    page: Page,
    selectors: Sequence[str] = tuple(NOISE_SELECTORS),
    words: Sequence[str] = NOISE_CLASS_WORDS,
) -> NoiseRemoval:
    """Mark the page chrome clean mode removes, keeping what is the page.

    `selectors` and `words` are clean mode's rules: `NOISE_SELECTORS` (and a
    watch page's own), and `NOISE_CLASS_WORDS`. Each element marked records
    `noise` as its reason, as `hide_elements` records one. Run after the
    content region is isolated, whose root it keeps (`HIDE_NOISE_JS`).
    """
    if not selectors and not words:
        return NoiseRemoval()
    answer: dict[str, Any] = await page.evaluate(
        HIDE_NOISE_JS,
        {
            "selectors": list(selectors),
            "words": list(words),
            "share": NOISE_CONTENT_SHARE,
        },
    )
    return NoiseRemoval(
        hidden=int(answer["count"]),
        kept=tuple(
            NoiseBox(
                name=str(box["name"]),
                rule=str(box["rule"]),
                characters=None
                if box["characters"] is None
                else int(box["characters"]),
                why=str(box["why"]),
                total=None if box["total"] is None else int(box["total"]),
            )
            for box in answer["kept"]
        ),
        removed=tuple(
            NoiseBox(
                name=str(box["name"]),
                rule=str(box["rule"]),
                characters=int(box["characters"]),
            )
            for box in answer["removed"]
        ),
    )


def kept_noise_message(removal: NoiseRemoval) -> str | None:
    """The log line for the elements clean mode's rules matched and it kept."""
    if not removal.kept:
        return None
    parts = []
    for box in removal.kept:
        if box.why == "root":
            reason = "it holds the content region"
        elif box.characters is not None and box.total:
            reason = (
                f"it holds a visible <h1> and {box.characters / box.total:.0%} "
                "of the page's text"
            )
        else:
            reason = "it holds a visible <h1> and most of the page's text"
        parts.append(f"{box.name} (matched by {box.rule}: {reason})")
    return (
        f"Kept {len(removal.kept)} element(s) clean mode would have removed as "
        f"page chrome, because each is the page's content: {'; '.join(parts)}."
    )


def noise_removal_note(removal: NoiseRemoval) -> str | None:
    """A sentence for the empty-capture warning: what clean mode hid that had
    text, or None when it hid none."""
    if not removal.removed:
        return None
    largest = removal.removed[0]
    count = len(removal.removed)
    held = removal.removed_characters
    return (
        f"Clean mode hid {count} element{'' if count == 1 else 's'} as page chrome "
        f"that showed {held:,} letters and digits of text, the most in "
        f"{largest.name} ({largest.characters:,}, matched by {largest.rule}): if "
        "that is the page's content, --mode faithful keeps it."
    )


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


async def keep_header_footer_margins(page: Page) -> None:
    """Print in `--margin`'s margins, whatever `@page` margin the page sets.

    Only for a capture with the running header and footer, which need the
    room: without them a page's own margin takes nothing from WebShot, and it
    stays in force as before (docs/09 P115). The top document only, because a
    frame's `@page` rules never reach the print.
    """
    await adopt_stylesheet(page, HEADER_FOOTER_MARGINS_CSS)


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
                element.setAttribute('data-webshot-hidden-by', 'consent');
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


#: How many of the nine points of a 3x3 grid over the window a fixed box must
#: be on top at before clean mode takes it for an interstitial: more than half
#: of the window. A header, a footer bar or a side panel is on top at three at
#: most; a dialog with a backdrop behind it, at all nine.
INTERSTITIAL_POINTS = 5

#: The most of the page's text a box taken for an interstitial may hold, as a
#: share of the body's. A page that keeps its whole document in a fixed,
#: scrolling shell is on top everywhere too, and it is the page; an ad, a
#: newsletter wall or a "welcome" splash holds a few words beside it. Half,
#: not a tenth: a short article under a sign-up box with a paragraph of terms
#: is still the larger part, and a box that holds more than the page is left
#: for the covering check to report rather than guessed at.
INTERSTITIAL_TEXT_SHARE = 0.5

#: Clean mode's two overlay rules, in one pass. The first is the size rule it
#: has always had. The second finds what is on top across the window, which
#: the first misses in two ways, both measured on one news site's ad
#: (docs/09 P113-4): its dialog, 640 px wide, fell under 45% of the 1440 px
#: window, and its backdrop, the full window at 80% black, had `z-index: -1`,
#: inside the dialog's own stacking context, so it printed over the text but
#: read as behind it. So the second rule asks the page which element is drawn
#: on top at each point of a grid, climbs from it to the outermost box fixed to
#: the window, and hides that box whole, its backdrop with it. Neither rule
#: hides a box that holds most of the page's text (`INTERSTITIAL_TEXT_SHARE`):
#: the size rule used to, and a page kept in a fixed scrolling shell printed
#: empty.
#: `elementsFromPoint` skips what is already marked, so a consent dialog hidden
#: a moment before does not stand in front of an interstitial beneath it.
HIDE_OBSTRUCTIVE_OVERLAYS_JS = """([points, textShare]) => {
    let count = 0;
    const pageText = (document.body.innerText || '').length;
    // Both rules: a box holding most of the page's text is the page. The size
    // rule hid such a shell whole before, and the capture came out empty.
    const isPage = node => (node.innerText || '').length > pageText * textShare;
    for (const element of document.querySelectorAll('body *')) {
        const style = getComputedStyle(element);
        if (style.position !== 'fixed') continue;
        const rect = element.getBoundingClientRect();
        const large = rect.width >= innerWidth * 0.45 && rect.height >= innerHeight * 0.08;
        if (large && Number(style.zIndex || 0) >= 10 && !isPage(element)) {
            element.setAttribute('data-webshot-hidden', 'true');
            element.setAttribute('data-webshot-hidden-by', 'overlay');
            count++;
        }
    }
    const marked = node => node.closest('[data-webshot-hidden="true"]') !== null;
    const up = node => node.parentElement
        || (node.getRootNode() instanceof ShadowRoot ? node.getRootNode().host : null);
    const top = (x, y) => {
        let found = document.elementsFromPoint(x, y).find(node => !marked(node)) || null;
        // Into open shadow roots, where a consent banner or an ad draws.
        while (found && found.shadowRoot) {
            const inner = found.shadowRoot.elementsFromPoint(x, y)
                .find(node => node !== found && !marked(node));
            if (!inner || inner === found) break;
            found = inner;
        }
        return found;
    };
    const fixedRoot = node => {
        let root = null;
        for (let at = node; at && at !== document.body && at !== document.documentElement; at = up(at)) {
            if (getComputedStyle(at).position === 'fixed') root = at;
        }
        // A root with a negative z-index paints beneath the page's own
        // content. Below the end of a short page's body there is nothing
        // else at a point, so a decorative background at -1 was "on top" at
        // six points of nine and hid as an interstitial (P113-4, review).
        // The root's own stacking decides, not the hit's: the measured ad's
        // backdrop is at -1 inside a dialog at 2147483644, which is on top.
        if (root && Number.parseInt(getComputedStyle(root).zIndex, 10) < 0) return null;
        // A mark inside a shadow root is read by no stylesheet of the
        // document's, so the box hidden is the host its tree hangs from.
        while (root && root.getRootNode() instanceof ShadowRoot) root = root.getRootNode().host;
        return root;
    };
    const tally = new Map();
    for (const fx of [1 / 6, 1 / 2, 5 / 6]) {
        for (const fy of [1 / 6, 1 / 2, 5 / 6]) {
            const hit = top(innerWidth * fx, innerHeight * fy);
            const root = hit && fixedRoot(hit);
            if (root && !marked(root)) tally.set(root, (tally.get(root) || 0) + 1);
        }
    }
    const covering = [];
    for (const [root, hits] of tally) {
        if (hits < points || isPage(root)) continue;
        root.setAttribute('data-webshot-hidden', 'true');
        root.setAttribute('data-webshot-hidden-by', 'overlay');
        count++;
        const cls = typeof root.className === 'string' ? root.className.trim().split(/\\s+/)[0] : '';
        covering.push(root.localName + (root.id ? `#${root.id}` : cls ? `.${cls}` : ''));
    }
    return {count, covering};
}"""


@dataclass(frozen=True)
class Overlays:
    """What `hide_obstructive_overlays` hid."""

    #: Every element hidden, by either rule.
    hidden: int
    #: The boxes hidden because they were on top across most of the window,
    #: named as `element#id` or `element.class`, in document order of the
    #: grid's first hit. The size rule's are counted, not named, as before.
    covering: tuple[str, ...]


async def hide_obstructive_overlays(page: Page) -> Overlays:
    """Hide large fixed overlays while leaving ordinary positioned content alone."""
    answer: dict[str, Any] = await page.evaluate(
        HIDE_OBSTRUCTIVE_OVERLAYS_JS, [INTERSTITIAL_POINTS, INTERSTITIAL_TEXT_SHARE]
    )
    return Overlays(
        hidden=int(answer["count"]),
        covering=tuple(str(name) for name in answer["covering"]),
    )


def interstitials_message(covering: Sequence[str]) -> str:
    """The manifest warning for the boxes clean mode hid as interstitials."""
    boxes = "box" if len(covering) == 1 else "boxes"
    named = _listed(list(covering))
    return (
        f"Clean mode hid the fixed {boxes} drawn on top across most of the window: "
        f"{named}. A box fixed to the window prints on every page of the PDF, over "
        "the text beneath it, and one this size was an interstitial (an ad, a "
        "sign-up or a welcome screen) with any backdrop it drew. --mode faithful "
        "keeps it."
    )


#: How long a page with no text when the post-load wait ends is given for its
#: script to render some. A library catalogue's record page, a single-page app,
#: showed none for 4.5 s after DOMContentLoaded and its whole record 5 s after
#: it; WebShot's one second after DOMContentLoaded printed a blank page
#: (docs/09 P113-5). Bounded, because an app shell whose script never renders
#: (a browser with no session) would otherwise hold the capture for good.
RENDERED_TEXT_WAIT_S = 10.0

#: Once text has come, how long it and the element count must stand still
#: before the capture goes on. The same record page drew its header and then
#: its record within half a second; a second is past that.
RENDERED_TEXT_SETTLE_S = 1.0

#: The page's rendered text, as a count: `innerText` of the body, then of each
#: open shadow root and same-origin frame when the body has none. `innerText`,
#: not `textContent`, because an app shell carries template text and hidden
#: "Loading" markup that a reader never sees. With the element count, which
#: says whether the page is still building, and whether it has a script at
#: all: a page with none cannot fill itself.
RENDERED_TEXT_JS = """() => {
    const rendered = node => ((node && node.innerText) || '').trim().length;
    let text = rendered(document.body);
    if (!text) {
        const roots = [document];
        for (let index = 0; index < roots.length && !text; index++) {
            for (const element of roots[index].querySelectorAll('*')) {
                if (element.shadowRoot) {
                    roots.push(element.shadowRoot);
                    for (const child of element.shadowRoot.children) text += rendered(child);
                }
                if (element.localName === 'iframe') {
                    try { text += rendered(element.contentDocument && element.contentDocument.body); }
                    catch (error) { /* cross-origin: not this page's text */ }
                }
            }
        }
    }
    return {text, nodes: document.getElementsByTagName('*').length, scripts: document.scripts.length};
}"""


@dataclass(frozen=True)
class TextWait:
    """What `wait_for_rendered_text` waited for, when it waited at all."""

    #: Seconds past the post-load wait.
    waited_s: float
    #: The rendered text when it stopped, as `RENDERED_TEXT_JS` counts it: 0
    #: when none came before the wait ended.
    characters: int
    #: The bound the wait was given.
    limit_s: float = RENDERED_TEXT_WAIT_S
    #: Whether it ended because `limit_s` passed. False when text came and
    #: stood still, and when the page navigated or closed under it, which a
    #: zero `characters` alone does not tell apart from a wait that ran out.
    ran_out: bool = False


def text_wait_bound(wait: TextWait | None) -> float | None:
    """The seconds the empty-capture warning may say the script was given:
    the bound, when the wait ran it out, or None. The bound rather than the
    time measured, which runs a poll past it and would make the warning
    differ run to run; and only a wait that ran out, because one the page
    cut short by navigating gave the script a fraction of it (P113-5)."""
    return wait.limit_s if wait is not None and wait.ran_out else None


async def wait_for_rendered_text(
    page: Page,
    limit_s: float = RENDERED_TEXT_WAIT_S,
    settle_s: float = RENDERED_TEXT_SETTLE_S,
) -> TextWait | None:
    """Give a page that shows no text yet time for its script to render some.

    None when there was nothing to wait for: the page already shows text, or
    has no script that could add any. Otherwise polls until text has come and
    stood still for `settle_s`, or `limit_s` has passed, and says how long it
    took and how much text there was. The page's network is not a signal: the
    record page above had nothing in flight for 2.5 s before its text came.
    """
    try:
        last: dict[str, int] = await page.evaluate(RENDERED_TEXT_JS)
    except PlaywrightError as exc:
        LOGGER.debug("Reading the page's rendered text failed: %s", exc)
        return None
    if last["text"] or not last["scripts"]:
        return None
    started = time.monotonic()
    still_since: float | None = None
    ran_out = False
    while True:
        if time.monotonic() - started >= limit_s:
            ran_out = True
            break
        await asyncio.sleep(0.25)
        try:
            now: dict[str, int] = await page.evaluate(RENDERED_TEXT_JS)
        except PlaywrightError as exc:
            # The page navigated or closed under the wait. What it is now is
            # for the checks after this one (a sign-in wall, an empty page).
            LOGGER.debug("Reading the page's rendered text failed: %s", exc)
            break
        if now["text"]:
            if still_since is None or (now["text"], now["nodes"]) != (
                last["text"],
                last["nodes"],
            ):
                still_since = time.monotonic()
            elif time.monotonic() - still_since >= settle_s:
                last = now
                break
        last = now
    return TextWait(
        waited_s=round(time.monotonic() - started, 1),
        characters=int(last["text"]),
        limit_s=limit_s,
        ran_out=ran_out,
    )


def rendered_text_message(wait: TextWait) -> str:
    """What the log says of a wait that brought text."""
    return (
        f"The page showed no text when it had loaded; its script rendered "
        f"{wait.characters:,} characters in {wait.waited_s:g} s more."
    )
