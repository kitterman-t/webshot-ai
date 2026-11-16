"""Boxes WebShot marked to leave out that the page's own print CSS shows anyway.

Five rules mark what they leave out with `data-webshot-hidden`: the content
selector (`--selector` or the auto-selector) marks the siblings along the
path to its root, the consent rule marks consent dialogs in either mode, clean
mode marks page furniture and what it takes for an overlay, the lazy-load
pass marks the articles a page appended once it changed its address
(docs/09 P120-4), and `--exclude` marks what the user named. Each records which it was in
`data-webshot-hidden-by`. The print stylesheet hides a marked box with
`display: none !important`, and a page's own `!important` rule of higher
specificity wins that tie, so the box prints. Every later stage takes the mark
as the answer: the snapshot drops a marked box from every text surface, and
the clipping, covering and video checks skip it. So a box the PDF printed was
missing from the bundle, with nothing saying so.

A blog in Blogger's Dynamic Views theme showed one: it draws a post in a panel
fixed over a grid of every other post, which clean mode took for an
interstitial and marked, and its print stylesheet makes the panel the page
(`display: block !important`) and hides the grid. The PDF printed the post;
content.txt held one byte, and the manifest no warning (docs/09 P128).

Once the print stylesheets and the media switch are in place, a marked box
that still lays out a box is not left out, and the mark that says it is comes
off, so the bundle and the checks read what the PDF prints, and the manifest
names the box and the rule that marked it. A box the user named with
`--exclude` keeps its mark: that is the user's choice, not WebShot's guess.
"""

from __future__ import annotations

from collections.abc import Sequence

from playwright.async_api import Page

#: Unmarks each marked box that still lays out, and names it with the rule
#: that marked it. Only the outermost of nested marks is named: the inner
#: ones go with it.
UNMARK_SHOWN_JS = """excluded => {
    const named = element => {
        const cls = typeof element.className === 'string'
            ? element.className.trim().split(/\\s+/)[0] : '';
        return element.localName + (element.id ? `#${element.id}` : cls ? `.${cls}` : '');
    };
    const userExcluded = element => element.getAttribute('data-webshot-hidden-by') === 'exclude'
        || excluded.some(selector => {
            try { return element.matches(selector); } catch (error) { return false; }
        });
    const marked = [...document.querySelectorAll('[data-webshot-hidden="true"]')];
    const shown = marked.filter(element => !userExcluded(element)
        && element.getClientRects().length > 0);
    const names = shown
        .filter(element => !shown.some(other => other !== element && other.contains(element)))
        .map(element => [named(element), element.getAttribute('data-webshot-hidden-by') || '']);
    for (const element of shown) {
        element.removeAttribute('data-webshot-hidden');
        element.removeAttribute('data-webshot-hidden-by');
    }
    return names;
}"""

#: What each marking rule took a box for, as the warning says it.
REASONS = {
    "overlay": "taken in clean mode for an overlay",
    "noise": "taken in clean mode for page furniture",
    "consent": "taken for a consent dialog",
    "appended": "an article the page appended after the one captured",
}


async def unmark_shown(
    page: Page, exclude_selectors: Sequence[str]
) -> list[tuple[str, str]]:
    """Take the mark off each marked box the print still shows.

    Returns each box's name with the rule that marked it, `""` where no rule
    recorded one.
    """
    names: list[list[str]] = await page.evaluate(
        UNMARK_SHOWN_JS, list(exclude_selectors)
    )
    return [(str(name), str(reason)) for name, reason in names]


def _why(reason: str, selector: str | None) -> str:
    if reason == "selector":
        return (
            f"outside the content region {selector}"
            if selector
            else ("outside the content region")
        )
    return REASONS.get(reason, "marked by a rule that recorded no reason")


def shown_despite_marking_message(
    shown: Sequence[tuple[str, str]], selector: str | None = None
) -> str:
    """The manifest warning for boxes the page's print CSS kept on the page.

    `selector` is the content region the capture isolated, named for a box
    the content selector marked.
    """
    one = len(shown) == 1
    boxes = ", ".join(f"{name} ({_why(reason, selector)})" for name, reason in shown)
    return (
        f"WebShot marked {len(shown)} {'box' if one else 'boxes'} to leave out that "
        f"the page's own print stylesheet shows anyway: {boxes}. The PDF prints "
        f"{'it' if one else 'them'}, so the bundle holds "
        f"{'its' if one else 'their'} text too."
    )
