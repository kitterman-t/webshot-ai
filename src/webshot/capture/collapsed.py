"""Find text the page keeps behind a closed disclosure control, and say so.

A legal case page showed an 80-word teaser and a "show full summary" button;
the full summary, about 1,020 words and the page's main content, was in the
served HTML as a Bootstrap collapse, `display: none` until the button is
pressed. The PDF and `content.md` both held only the teaser, and nothing said
so (docs/09 P102-1). A video page's description stops at its clamp with
"…more", and the rest is not in the document at all until the button is
pressed (P102-2).

The check runs once the page is prepared, before the bundle, and reports each
control whose closed region holds text, in three shapes the page states
itself:

- a control the page marks `aria-expanded="false"` whose `aria-controls`
  names an element with no box;
- a Bootstrap collapse toggle (`data-bs-toggle` or `data-toggle` of
  `collapse`) whose target has no box;
- a closed `<details>`.

And two more: a button whose label says the article goes on ("Story
continues", "Read more") followed by a region with no box, as a finance news
page kept the rest of its article (docs/09 P120-2); and one known by its
vendor's markup, YouTube's description expander, whose text is fetched only
when opened.

In clean mode, and under `--collapsed open`, `open_collapsed` opens them
before the page is printed and read: a `<details>` gets its `open`
attribute, a region with no box is given its box back, and the expander's
button is pressed. The check then names only what stayed closed. In faithful
mode, or under `--collapsed closed`, nothing is opened: what the page showed
is what is recorded, and the warning says where the rest is. Where it is, in
the bundle or not, is read from the bundle once it is built, never assumed:
a policy page kept every section in a closed `<details>` whose contents its
own stylesheet hid, and the warning said "in the bundle" of text the bundle
did not hold (P120-3).

A region is reported when it holds at least `MIN_CHARACTERS` of text, and is
not page chrome: neither it nor its control sits in a header, navigation,
footer or aside, and at most half its text is link text, which is how a
collapsed menu reads. Nor is anything the capture hid itself, a consent
dialog, clean mode's noise or an `--exclude`: the page did not close that.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError

LOGGER = logging.getLogger("webshot")

#: Text a closed region must hold to be reported: about thirty words. The
#: case page's hidden summary held over 6,000 characters; a footer's folded
#: "Legal" notice, 25.
MIN_CHARACTERS = 200

#: How many regions one warning names.
NAMED = 5

#: How many words each of a region's samples holds. A region's text is in
#: the bundle only when its start, its middle and its end all are: a teaser
#: the page shows often repeats the hidden text's opening sentence, so the
#: opening alone said "in the bundle" of text that was not (review of #367).
SAMPLE_WORDS = 8

#: How long an opened expander whose page fetches its text is waited for.
FETCH_WAIT_MS = 2000

#: How many times opening looks again: a region opened can hold another.
OPEN_ROUNDS = 3

#: A label that says the article goes on behind the control. Whole labels
#: only: "Read more" alone, not "Read more about our funds", which links to
#: another page. A finance news page's button read "Story Continues"
#: (docs/09 P120-2).
CONTINUES = (
    r"^(?:story continues|continue reading|keep reading|read more|read the rest"
    r"|read (?:the )?(?:full|whole|entire|rest of the) (?:story|article|post|text)"
    r"|show (?:the )?(?:full|whole|entire) (?:story|article|post|text)"
    r"|show more|see more|view (?:the )?full (?:story|article|post))"
    # Trailing dots, an ellipsis, and the arrows and chevrons a label ends in.
    "[\\s.\u2026\u203a\u00bb\u2192>]*$"
)

#: Finds the closed regions and, when `open` is true, opens them. One script
#: for both, so what is opened is exactly what the warning would have named.
COLLAPSED_SCRIPT = r"""async ({minimum, open, sampleWords, fetchWaitMs, rounds, continues}) => {
    const chrome = 'header, nav, footer, aside, [role="banner"], [role="navigation"], '
        + '[role="contentinfo"], [role="complementary"], [role="menu"], [role="menubar"], '
        + '[role="dialog"], [role="listbox"], [role="tree"]';
    const text = node => (node.textContent || '').replace(/\s+/g, ' ').trim();
    // Text as a reader would read it: each text node a word apart from the
    // next, so minified `<h3>Fees</h3><p>Every` reads "Fees Every" and not
    // "FeesEvery", which no word of the bundle matched (review of #367).
    // What never renders (scripts, styles, templates) is not text.
    const unrendered = 'script, style, noscript, template';
    const spaced = (root, skip) => {
        const parts = [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const parent = node.parentElement;
            if (parent && (parent.closest(unrendered) || (skip && skip.contains(node)))) continue;
            parts.push(node.data);
        }
        return parts.join(' ').replace(/\s+/g, ' ').trim();
    };
    const step = element => {
        const id = element.id && !/^\d|^[0-9a-f]{16,}$/i.test(element.id) ? `#${CSS.escape(element.id)}` : '';
        const classes = [...element.classList].slice(0, 2).map(name => `.${CSS.escape(name)}`);
        return element.localName + id + classes.join('');
    };
    const unboxed = element => !element.getClientRects().length;
    const prose = element => {
        const all = text(element).length;
        const links = [...element.querySelectorAll('a')].reduce((sum, a) => sum + text(a).length, 0);
        return all >= minimum && links * 2 <= all;
    };
    // What the capture hid itself (the consent dialogs, clean mode's noise,
    // `--exclude`) is not the page keeping it closed (review of #313).
    const inChrome = element => element.closest(chrome) !== null
        || element.closest('[data-webshot-hidden="true"]') !== null;
    const label = control => (control.getAttribute('aria-label') || text(control)
        || control.getAttribute('title') || '').trim();
    const goesOn = new RegExp(continues, 'i');
    // The region's own text: a <details> without its summary.
    const own = region => spaced(region, region.localName === 'details'
        ? region.querySelector(':scope > summary') : null);
    const find = () => {
        const found = [];
        const seen = new Set();
        const report = (kind, control, region, characters) => {
            if (seen.has(region)) return;
            seen.add(region);
            found.push({kind, control, region, characters});
        };
        for (const control of document.querySelectorAll('[aria-expanded="false"][aria-controls]')) {
            if (inChrome(control) || unboxed(control)) continue;
            for (const id of control.getAttribute('aria-controls').split(/\s+/)) {
                const region = id && document.getElementById(id);
                if (region && unboxed(region) && !inChrome(region) && prose(region)) {
                    report('hidden', control, region, text(region).length);
                }
            }
        }
        for (const control of document.querySelectorAll('[data-bs-toggle="collapse"], [data-toggle="collapse"]')) {
            if (inChrome(control) || unboxed(control)) continue;
            const target = control.getAttribute('data-bs-target') || control.getAttribute('data-target')
                || control.getAttribute('href') || '';
            if (!target.startsWith('#') && !target.startsWith('.')) continue;
            let regions;
            try { regions = document.querySelectorAll(target); } catch (error) { continue; }
            for (const region of regions) {
                if (unboxed(region) && !inChrome(region) && prose(region)) {
                    report('hidden', control, region, text(region).length);
                }
            }
        }
        // A button that says the article goes on, and the first region with
        // no box after it or after one of its three nearest ancestors: the
        // finance page's button sat in a wrapper of its own, and the rest of
        // the article in the wrapper's next sibling (docs/09 P120-2). A link
        // to another page is a link, not a control.
        for (const control of document.querySelectorAll('button, [role="button"], a')) {
            if (inChrome(control) || unboxed(control) || !goesOn.test(label(control))) continue;
            const href = control.localName === 'a' ? control.getAttribute('href') : null;
            if (href && !href.startsWith('#') && !/^javascript:/i.test(href)) continue;
            let region = null;
            for (let node = control, depth = 0; node && node !== document.body && depth < 4 && !region;
                 node = node.parentElement, depth++) {
                for (let next = node.nextElementSibling, n = 0; next && n < 2; next = next.nextElementSibling, n++) {
                    // A script or a style has no box and its source reads as
                    // text, so an ad's inline script after a "Read more"
                    // button was a closed region (review of #367).
                    if (next.matches(unrendered)) continue;
                    if (unboxed(next) && !inChrome(next) && prose(next)) { region = next; break; }
                }
            }
            if (region) report('hidden', control, region, text(region).length);
        }
        // A closed <details> lays its contents out and skips painting them,
        // so they have boxes either way; `checkVisibility` tells whether they
        // are drawn. A stylesheet that shows them (a `--css` rule on
        // `::details-content`) leaves the element closed and its text
        // printed, and such a <details> was named all the same (P120-7).
        const drawn = details => [...details.children].some(child =>
            child.localName !== 'summary' && child.getClientRects().length > 0
            && child.checkVisibility({checkVisibilityCSS: true}));
        for (const details of document.querySelectorAll('details:not([open])')) {
            const summary = details.querySelector(':scope > summary');
            if (inChrome(details) || unboxed(details) || drawn(details)) continue;
            const characters = own(details).length;
            if (characters >= minimum && prose(details)) {
                report('details', summary || details, details, characters);
            }
        }
        for (const expander of document.querySelectorAll('ytd-text-inline-expander:not([is-expanded])')) {
            const button = expander.querySelector('#expand');
            if (button && !unboxed(button) && button.checkVisibility()) {
                report('fetched', button, expander, null);
            }
        }
        return found;
    };
    // Its start, its middle and its end, `sampleWords` words each.
    const samples = region => {
        const words = own(region).split(' ').filter(Boolean);
        if (!words.length) return [];
        const middle = Math.max(0, Math.floor((words.length - sampleWords) / 2));
        const at = [0, middle, Math.max(0, words.length - sampleWords)];
        return [...new Set(at)].map(start => words.slice(start, start + sampleWords).join(' '));
    };
    const described = item => ({
        kind: item.kind, control: step(item.control), label: text(item.control).slice(0, 60),
        region: step(item.region), characters: item.characters,
        samples: item.kind === 'fetched' ? [] : samples(item.region),
    });
    if (!open) return {found: find().map(described), opened: 0};
    // Opening. A region given its box back is marked, and one rule at a
    // specificity no page rule reaches gives it the display its element has
    // by default: `revert`, not `block`, so a <span> stays inline and a <li>
    // a list item. `hidden` is the UA's own `display: none`, which `revert`
    // would keep, so the attribute goes.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync("[data-webshot-opened='true']:not([data-webshot-hidden='true']) {"
        + ' display: revert !important; visibility: visible !important;'
        + ' content-visibility: visible !important; height: auto !important;'
        + ' max-height: none !important; overflow: visible !important; opacity: 1 !important; }');
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    const tried = new Set();
    let opened = 0;
    const isOpen = item => item.kind === 'details' ? item.region.open && !unboxed(item.region)
        : item.kind === 'fetched' ? item.region.hasAttribute('is-expanded')
        : !unboxed(item.region);
    for (let round = 0; round < rounds; round++) {
        const found = find().filter(item => !tried.has(item.region));
        if (!found.length) break;
        for (const item of found) {
            tried.add(item.region);
            if (item.kind === 'details') item.region.open = true;
            else if (item.kind === 'fetched') item.control.click();
            else {
                item.region.removeAttribute('hidden');
                item.region.setAttribute('data-webshot-opened', 'true');
            }
        }
        const deadline = performance.now() + fetchWaitMs;
        while (found.some(item => item.kind === 'fetched' && !isOpen(item))
               && performance.now() < deadline) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        opened += found.filter(isOpen).length;
    }
    return {found: [], opened};
}"""


@dataclass(frozen=True, slots=True)
class CollapsedRegion:
    """A closed region, and the control that opens it."""

    #: `hidden` (the region has no box), `details` (a closed `<details>`), or
    #: `fetched` (the page fetches the text only when opened).
    kind: str
    control: str
    label: str
    region: str
    #: Characters of text the region holds, or `None` where the page has not
    #: fetched it.
    characters: int | None
    #: Its text's start, middle and end, `SAMPLE_WORDS` words each, which the
    #: warning looks for in the bundle's; empty where the page has not
    #: fetched it.
    samples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Collapsed:
    regions: tuple[CollapsedRegion, ...] = ()
    #: Why the page could not be read, when it could not.
    unread: str | None = None


@dataclass(frozen=True, slots=True)
class Opened:
    """What `open_collapsed` did."""

    #: Regions it opened that now have a box (or, for an expander, opened).
    opened: int = 0
    #: Why the page could not be read, when it could not.
    unread: str | None = None


def _arguments(open_them: bool) -> dict[str, Any]:
    return {
        "minimum": MIN_CHARACTERS,
        "open": open_them,
        "sampleWords": SAMPLE_WORDS,
        "fetchWaitMs": FETCH_WAIT_MS,
        "rounds": OPEN_ROUNDS,
        "continues": CONTINUES,
    }


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


async def collapsed_content(page: Any) -> Collapsed:
    """The closed regions holding text on the page as prepared."""
    try:
        answer = await page.evaluate(COLLAPSED_SCRIPT, _arguments(False))
    except PlaywrightError as exc:
        return Collapsed(unread=_first_line(exc))
    return Collapsed(
        regions=tuple(
            CollapsedRegion(
                kind=str(item["kind"]),
                control=str(item["control"]),
                label=str(item["label"]),
                region=str(item["region"]),
                characters=None
                if item["characters"] is None
                else int(item["characters"]),
                samples=tuple(str(sample) for sample in item["samples"]),
            )
            for item in answer["found"]
        )
    )


async def open_collapsed(page: Any) -> Opened:
    """Open the closed regions `collapsed_content` would name (docs/09 P120-3).

    Run once the capture has hidden what it hides and before anything is
    measured, released, snapshotted or printed, so every later stage sees
    the regions open. A region that stays closed (one inside an ancestor
    the page hides, an expander whose page never answers the press) is left
    for the check to name.
    """
    try:
        answer = await page.evaluate(COLLAPSED_SCRIPT, _arguments(True))
    except PlaywrightError as exc:
        return Opened(unread=_first_line(exc))
    return Opened(opened=int(answer["opened"]))


def open_collapsed_failure(opened: Opened) -> str | None:
    """The manifest warning for an opening that could not run, or `None`."""
    if opened.unread is None:
        return None
    return (
        f"WebShot could not open the page's closed controls ({opened.unread}), so "
        "text behind them may be missing from the PDF and the bundle."
    )


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"\w+", text.lower())) + " "


def _one(region: CollapsedRegion, bundle: str | None) -> str:
    label = f' ("{region.label}")' if region.label else ""
    if region.kind == "fetched":
        return (
            f"{region.control}{label} in {region.region}, whose text the page fetches "
            "only when it is opened"
        )
    held = f"{region.characters:,} characters"
    # Where the text is is read from the bundle, never assumed from the kind:
    # with --no-ai-bundle there is no bundle (review of #313), and a closed
    # <details> whose page hides its contents is in neither (docs/09 P120-3).
    if bundle is None:
        where = "not in the PDF"
    elif region.samples and all(_words(sample) in bundle for sample in region.samples):
        where = "in the bundle but not in the PDF"
    else:
        where = "in neither the PDF nor the bundle"
    if region.kind == "details":
        return f"{region.region}{label}, {held}, {where}"
    return f"{region.control}{label} opens {region.region}, {held}, {where}"


#: What the capture did about the closed regions before the check: nothing
#: (`off`), opened them (`done`), or tried and could not read the page
#: (`failed`).
Opening = Literal["off", "done", "failed"]


def opening_state(asked: bool, opened: Opened | None) -> Opening:
    """What `collapsed_content_warning` may say the capture did."""
    if not asked:
        return "off"
    return "failed" if opened is None or opened.unread is not None else "done"


def collapsed_content_warning(
    found: Collapsed, *, bundle_text: str | None, opening: Opening = "off"
) -> str | None:
    """The manifest warning for the closed regions, or `None`.

    `bundle_text` is the bundle's `content.txt`, or `None` when the capture
    builds no bundle; it decides what the warning may say of the bundle.
    `opening` is what the capture did first, which decides the remedy: an
    opening that failed is not "opened what it could" (review of #367).
    """
    if found.unread is not None:
        return (
            "WebShot could not read the page for text it keeps collapsed "
            f"({found.unread}), so whether some is missing from the PDF was not "
            "checked."
        )
    if not found.regions:
        return None
    bundle = None if bundle_text is None else _words(bundle_text)
    named = "; ".join(_one(region, bundle) for region in found.regions[:NAMED])
    more = (
        f"; and {len(found.regions) - NAMED} more" if len(found.regions) > NAMED else ""
    )
    count = len(found.regions)
    if opening == "off":
        remedy = (
            "WebShot printed the page as it was shown; --collapsed open opens them "
            "before printing."
        )
    else:
        # Text the page has not fetched is beyond any stylesheet. One that
        # shows a region can work where opening did not, though on one page
        # a rule on `::details-content` reserved the space and printed none
        # of the text (docs/09 P120-3), so it is offered second.
        css = (
            ", or pass a --css stylesheet that shows the region"
            if any(region.kind != "fetched" for region in found.regions)
            else ""
        )
        done = (
            f"WebShot opened what it could, and {'this' if count == 1 else 'these'} "
            "stayed closed."
            if opening == "done"
            else "WebShot could not open them, as the warning before this one says."
        )
        remedy = (
            f"{done} To capture the text, open the control in your own "
            f"browser and save the page there{css}."
        )
    return (
        f"The page keeps text behind {count} closed control{'' if count == 1 else 's'}, "
        f"so the PDF does not show it: {named}{more}. {remedy}"
    )
