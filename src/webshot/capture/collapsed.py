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

And one known by its vendor's markup: YouTube's description expander, whose
text is fetched only when opened. It never opens anything: what the page
showed is what is recorded, and the warning says where the rest is.

A region is reported when it holds at least `MIN_CHARACTERS` of text, and is
not page chrome: neither it nor its control sits in a header, navigation,
footer or aside, and at most half its text is link text, which is how a
collapsed menu reads. Nor is anything the capture hid itself, a consent
dialog, clean mode's noise or an `--exclude`: the page did not close that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError

LOGGER = logging.getLogger("webshot")

#: Text a closed region must hold to be reported: about thirty words. The
#: case page's hidden summary held over 6,000 characters; a footer's folded
#: "Legal" notice, 25.
MIN_CHARACTERS = 200

#: How many regions one warning names.
NAMED = 5

COLLAPSED_SCRIPT = r"""({minimum}) => {
    const chrome = 'header, nav, footer, aside, [role="banner"], [role="navigation"], '
        + '[role="contentinfo"], [role="complementary"], [role="menu"], [role="menubar"], '
        + '[role="dialog"], [role="listbox"], [role="tree"]';
    const text = node => (node.textContent || '').replace(/\s+/g, ' ').trim();
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
    const found = [];
    const seen = new Set();
    const report = (kind, control, region, characters) => {
        if (seen.has(region)) return;
        seen.add(region);
        found.push({kind, control: step(control), label: text(control).slice(0, 60),
                    region: step(region), characters});
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
    for (const details of document.querySelectorAll('details:not([open])')) {
        const summary = details.querySelector(':scope > summary');
        if (inChrome(details) || unboxed(details)) continue;
        const characters = text(details).length - (summary ? text(summary).length : 0);
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
}"""


@dataclass(frozen=True, slots=True)
class CollapsedRegion:
    """A closed region, and the control that opens it."""

    #: `hidden` (no box: in neither the PDF nor the bundle), `details` (a
    #: closed `<details>`: in the bundle, not in the PDF), or `fetched` (the
    #: page fetches the text only when opened).
    kind: str
    control: str
    label: str
    region: str
    #: Characters of text the region holds, or `None` where the page has not
    #: fetched it.
    characters: int | None


@dataclass(frozen=True, slots=True)
class Collapsed:
    regions: tuple[CollapsedRegion, ...] = ()
    #: Why the page could not be read, when it could not.
    unread: str | None = None


async def collapsed_content(page: Any) -> Collapsed:
    """The closed regions holding text on the page as prepared."""
    try:
        found = await page.evaluate(COLLAPSED_SCRIPT, {"minimum": MIN_CHARACTERS})
    except PlaywrightError as exc:
        first = (
            str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        )
        return Collapsed(unread=first)
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
            )
            for item in found
        )
    )


def _one(region: CollapsedRegion, bundle: bool) -> str:
    label = f' ("{region.label}")' if region.label else ""
    if region.kind == "fetched":
        return (
            f"{region.control}{label} in {region.region}, whose text the page fetches "
            "only when it is opened"
        )
    held = f"{region.characters:,} characters"
    # Where the text is depends on whether a bundle is built at all: with
    # --no-ai-bundle a closed <details> is in no deliverable (review of #313).
    if region.kind == "details":
        where = "in the bundle but not in the PDF" if bundle else "not in the PDF"
        return f"{region.region}{label}, {held}, {where}"
    where = "in neither the PDF nor the bundle" if bundle else "not in the PDF"
    return f"{region.control}{label} opens {region.region}, {held}, {where}"


def collapsed_content_warning(found: Collapsed, *, bundle: bool) -> str | None:
    """The manifest warning for the closed regions, or `None`.

    `bundle` says whether the capture builds a bundle, which decides what the
    warning may say of it.
    """
    if found.unread is not None:
        return (
            "WebShot could not read the page for text it keeps collapsed "
            f"({found.unread}), so whether some is missing from the PDF was not "
            "checked."
        )
    if not found.regions:
        return None
    named = "; ".join(_one(region, bundle) for region in found.regions[:NAMED])
    more = (
        f"; and {len(found.regions) - NAMED} more" if len(found.regions) > NAMED else ""
    )
    count = len(found.regions)
    # Text the page has not fetched is beyond any stylesheet.
    css = (
        ", or pass a --css stylesheet that shows the region"
        if any(region.kind != "fetched" for region in found.regions)
        else ""
    )
    return (
        f"The page keeps text behind {count} closed control{'' if count == 1 else 's'}, "
        f"so the PDF does not show it: {named}{more}. WebShot records the page as it "
        "was shown and does not open them. To capture the text, open the control in "
        f"your own browser and save the page there{css}."
    )
