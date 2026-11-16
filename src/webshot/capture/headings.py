"""Find headings the page showed that the PDF does not print, and say so.

Two captures lost a heading and said nothing that named it (docs/09 P123-5):

- A consulting firm's print stylesheet hid its article's `<h1>`, so the PDF
  began at the first section, with no title, in clean and faithful mode
  alike. The bundle, taken under the same print stylesheet, lacked it too, so
  the printed-text share (P97) had nothing to miss.
- A code host's repository page cut its README at the right edge and lost its
  last two sections, headings and all, which the bundle kept. The only
  warning counted elements with lines past the edge.

So the page's title, its first `<h1>` a reader could see, is read before the
capture isolates its content or hides anything, and the other headings are
read once it has, while the page still shows what a reader saw: before any
print stylesheet or the print media applies. Each is looked for in the PDF's
text once it is printed, and in the bundle's. A heading in neither was hidden
for print; a heading in the bundle and not the PDF was lost by the layout. A
title the capture itself left out is said only where WebShot chose the
region that leaves it out and that region printed no `<h1>` of its own: a
`--selector` the caller wrote, and page chrome that clean mode or `--exclude`
removed, are what was asked for. So is a box clean mode hid after the
headings were read, once it was measured over the text (P123-6): the
headings it held are left out of the check (`hidden_since`). A box the
capture marked before they were read, that the page's print stylesheet shows
anyway, is not left out (P128): once its mark comes off, the headings it held
are checked too (`unmarked_since`).

A heading is matched as words, in order, ignoring case and punctuation, so
a print stylesheet's `text-transform` and a line break inside it do not
count as missing. Words may be up to `GAP_WORDS` apart, for the running
header and footer printed between two pages a heading can span.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Error as PlaywrightError

#: How many other words may fall between two of a heading's words in the
#: printed text: a page's running header and footer, about twenty.
GAP_WORDS = 40

#: How many missing headings a warning names.
NAMED = 5

#: Whether a heading is one a reader can see: a box more than a pixel or two
#: across (a heading kept for screen readers is clipped to nothing), visible,
#: and not wholly outside a box that clips it, such as another slide of a
#: carousel. The page keeps the others out of sight itself, in print as on
#: screen. Shared by both scripts below.
_SEEN = r"""
    const seen = heading => {
        const rect = heading.getBoundingClientRect();
        if (rect.width < 3 || rect.height < 3) return false;
        if (!heading.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
        for (let node = heading.parentElement; node && node !== document.documentElement; node = node.parentElement) {
            const style = getComputedStyle(node);
            const clipsX = style.overflowX !== 'visible';
            const clipsY = style.overflowY !== 'visible';
            if (!clipsX && !clipsY) continue;
            const box = node.getBoundingClientRect();
            if ((clipsX && (rect.right <= box.left || rect.left >= box.right))
                || (clipsY && (rect.bottom <= box.top || rect.top >= box.bottom))) return false;
        }
        return true;
    };
    const textOf = heading => (heading.innerText || heading.textContent || '').replace(/\s+/g, ' ').trim();
"""

#: The page's title: its first `<h1>` a reader can see and that has text,
#: kept on the window for `SHOWN_HEADINGS_SCRIPT`. Run before the capture
#: isolates or hides anything.
TITLE_HEADING_SCRIPT = (
    r"""() => {"""
    + _SEEN
    + r"""
    delete window.__webshotTitleHeading;
    for (const heading of document.querySelectorAll('h1')) {
        if (!seen(heading) || !textOf(heading)) continue;
        window.__webshotTitleHeading = heading;
        return textOf(heading);
    }
    return null;
}"""
)

#: Every heading the page shows, in document order, with its level and text,
#: inside the content the capture isolated if it isolated any, leaving out
#: what it hid; and what became of the title `TITLE_HEADING_SCRIPT` kept:
#: `shown`, `isolated` (outside the content the capture isolated) or
#: `removed` (hidden by the capture, or gone from the page). Given the
#: selectors of the page chrome the capture hid (`chrome`): a title it hid
#: inside one was removed with the chrome whatever region was chosen, and
#: isolation marks what it leaves out just as the chrome's removal does, so
#: only the selectors tell the two apart. Keeps the elements it read on the
#: window, for `HIDDEN_SINCE_SCRIPT`, and those it left out because a box
#: the capture marked held them, for `UNMARKED_SINCE_SCRIPT`.
SHOWN_HEADINGS_SCRIPT = (
    r"""(chrome) => {"""
    + _SEEN
    + r"""
    const hidden = '[data-webshot-hidden="true"]';
    const root = document.querySelector('[data-webshot-root="true"]') || document.body;
    const found = [];
    const elements = [];
    const marked = [];
    for (const heading of root ? root.querySelectorAll('h1, h2, h3, h4, h5, h6') : []) {
        if (heading.closest('[data-webshot-ocr-text="true"]')) continue;
        if (!seen(heading)) continue;
        const text = textOf(heading);
        if (!text) continue;
        const item = {level: Number(heading.localName.slice(1)), text};
        if (heading.closest(hidden)) {
            marked.push({element: heading, item});
            continue;
        }
        found.push(item);
        elements.push(heading);
    }
    const title = window.__webshotTitleHeading || null;
    delete window.__webshotTitleHeading;
    const inChrome = element => element.closest(hidden) !== null && chrome.some(selector => {
        try { return element.closest(selector) !== null; } catch (error) { return false; }
    });
    let fate = null;
    if (title) {
        const isolated = document.querySelector('[data-webshot-root="true"]');
        if (!title.isConnected || inChrome(title)) fate = 'removed';
        else if (isolated && !isolated.contains(title) && !title.contains(isolated)) fate = 'isolated';
        else if (title.closest(hidden) || !seen(title)) fate = 'removed';
        else fate = 'shown';
    }
    window.__webshotShownHeadings = {
        headings: elements, found, marked, title,
        titleMarked: Boolean(title && title.isConnected && seen(title) && title.closest(hidden)),
    };
    return {found, fate};
}"""
)

#: The headings `SHOWN_HEADINGS_SCRIPT` read, in document order, with those
#: it left out in a box the capture marked whose mark has come off since,
#: and the title's fate if it was in such a box: `{found: [...], fate}`,
#: where `fate` is `null` for a title whose fate stands. `null` when the
#: page no longer holds what that script kept. Keeps the new list for
#: `HIDDEN_SINCE_SCRIPT`.
UNMARKED_SINCE_SCRIPT = r"""() => {
    const kept = window.__webshotShownHeadings;
    if (!kept) return null;
    const hidden = element => element.closest('[data-webshot-hidden="true"]') !== null;
    const pairs = kept.headings.map((element, index) => ({element, item: kept.found[index]}));
    for (const entry of kept.marked) {
        if (entry.element.isConnected && !hidden(entry.element)) pairs.push(entry);
    }
    pairs.sort((a, b) => a.element === b.element ? 0
        : a.element.compareDocumentPosition(b.element) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    kept.headings = pairs.map(pair => pair.element);
    kept.found = pairs.map(pair => pair.item);
    kept.marked = kept.marked.filter(entry => !kept.headings.includes(entry.element));
    let fate = null;
    const title = kept.title;
    if (kept.titleMarked && title.isConnected && !hidden(title)) {
        const isolated = document.querySelector('[data-webshot-root="true"]');
        fate = isolated && !isolated.contains(title) && !title.contains(isolated)
            ? 'isolated' : 'shown';
        kept.titleMarked = false;
    }
    return {found: kept.found, fate};
}"""

#: Which of the headings `SHOWN_HEADINGS_SCRIPT` read the capture has hidden
#: since, and whether it hid the title: `{found: [bool, ...], title: bool}`,
#: or `null` when the page no longer holds what that script kept.
HIDDEN_SINCE_SCRIPT = r"""() => {
    const kept = window.__webshotShownHeadings;
    if (!kept) return null;
    delete window.__webshotShownHeadings;
    const hidden = element => element.closest('[data-webshot-hidden="true"]') !== null;
    return {found: kept.headings.map(hidden), title: kept.title ? hidden(kept.title) : false};
}"""

#: Why `without_hidden` could not say which headings a box hidden since held.
UNMATCHED = (
    "the page no longer held the headings it showed once clean mode hid a box over "
    "its text"
)

#: Why `with_unmarked` could not say which headings a box shown since held.
UNKEPT = (
    "the page no longer held the headings it showed once WebShot took its mark off "
    "a box the page's print stylesheet shows"
)


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class ShownHeadings:
    """The headings the page showed before it was printed."""

    headings: tuple[Heading, ...] = ()
    #: The page's title, read before the capture isolated or hid anything.
    title: Heading | None = None
    #: What became of it by the time the page was printed: `shown`,
    #: `isolated` or `removed` (`SHOWN_HEADINGS_SCRIPT`).
    title_fate: str | None = None
    #: Why the page could not be read, when it could not.
    unread: str | None = None


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


async def title_heading(page: Any) -> str | None:
    """Keep the page's title on the page, for `shown_headings`, and return its
    text. A page that cannot be read keeps none, and `shown_headings` says
    so when it cannot read the page either."""
    try:
        found = await page.evaluate(TITLE_HEADING_SCRIPT)
    except PlaywrightError:
        return None
    return str(found) if found else None


async def shown_headings(
    page: Any, title: str | None = None, chrome: Sequence[str] = ()
) -> ShownHeadings:
    """The headings the page shows, read before anything prints it.

    `title` is what `title_heading` returned, earlier in the capture.
    `chrome` is the selectors of the page chrome the capture hid by name:
    clean mode's, the consent managers' and `--exclude`'s.
    """
    try:
        found = await page.evaluate(SHOWN_HEADINGS_SCRIPT, list(chrome))
    except PlaywrightError as exc:
        return ShownHeadings(unread=_first_line(exc))
    return ShownHeadings(
        headings=tuple(
            Heading(int(item["level"]), str(item["text"])) for item in found["found"]
        ),
        title=Heading(1, title) if title and found["fate"] else None,
        title_fate=str(found["fate"]) if title and found["fate"] else None,
    )


async def unmarked_since(page: Any, shown: ShownHeadings) -> ShownHeadings:
    """`shown` with the headings of each box whose mark has come off since it
    was read.

    The headings are read before the print stylesheets, and a heading in a
    box the capture had marked to leave out is not read. Once the print
    stylesheets apply, `unmark_shown` takes the mark off a box the page's
    print CSS shows anyway, and the PDF and the bundle hold it (docs/09
    P128). Left out, its headings went unchecked: a title the print
    stylesheet hid inside such a box, which faithful mode names, went unnamed
    in clean mode, where the same box printed. Each is checked as the
    page's other headings are; a title in one is `shown` or `isolated` as if
    it had never been marked.
    """
    if shown.unread is not None:
        return shown
    try:
        answer = await page.evaluate(UNMARKED_SINCE_SCRIPT)
    except PlaywrightError as exc:
        return ShownHeadings(unread=_first_line(exc))
    return with_unmarked(shown, answer)


def with_unmarked(shown: ShownHeadings, answer: Any) -> ShownHeadings:
    """`shown` with the headings `UNMARKED_SINCE_SCRIPT`'s `answer` lists,
    and the title's fate it gives, if it gives one. An answer that does not
    list at least the headings `shown` has leaves them unchecked: which ones
    the page holds cannot be told, and `hidden_since` matches its own answer
    to this list heading for heading."""
    if shown.unread is not None:
        return shown
    found = answer.get("found") if isinstance(answer, dict) else None
    if (
        not isinstance(found, list)
        or len(found) < len(shown.headings)
        or not all(
            isinstance(item, dict) and "level" in item and "text" in item
            for item in found
        )
    ):
        return ShownHeadings(unread=UNKEPT)
    fate = answer.get("fate")
    return ShownHeadings(
        headings=tuple(
            Heading(int(item["level"]), str(item["text"])) for item in found
        ),
        title=shown.title,
        title_fate=str(fate)
        if shown.title is not None and fate in ("shown", "isolated")
        else shown.title_fate,
    )


async def hidden_since(page: Any, shown: ShownHeadings) -> ShownHeadings:
    """`shown` less the headings the capture has hidden since it was read.

    Clean mode hides a fixed box once the render shows it over the page's
    text, and prints the page again (docs/09 P123-6). The headings were read
    before, while the box showed, so one inside it, a site's name set in an
    `<h1>` in a fixed masthead, is missing from the PDF that is delivered and
    present in the bundle: what the check would call lost by the layout. Clean
    mode removed it as page chrome, and the warning for the box says so.
    """
    if shown.unread is not None:
        return shown
    try:
        answer = await page.evaluate(HIDDEN_SINCE_SCRIPT)
    except PlaywrightError as exc:
        return ShownHeadings(unread=_first_line(exc))
    return without_hidden(shown, answer)


def without_hidden(shown: ShownHeadings, answer: Any) -> ShownHeadings:
    """`shown` less the headings `HIDDEN_SINCE_SCRIPT`'s `answer` says the
    capture hid since; a title it hid was `removed`. An answer that does not
    match `shown` heading for heading leaves the headings unchecked: which of
    them a hidden box held cannot be told."""
    if shown.unread is not None:
        return shown
    flags = answer.get("found") if isinstance(answer, dict) else None
    if not isinstance(flags, list) or len(flags) != len(shown.headings):
        return ShownHeadings(unread=UNMATCHED)
    title_hidden = shown.title is not None and bool(answer.get("title"))
    return ShownHeadings(
        headings=tuple(
            heading
            for heading, gone in zip(shown.headings, flags, strict=True)
            if not gone
        ),
        title=shown.title,
        title_fate="removed" if title_hidden else shown.title_fate,
    )


def words(text: str) -> list[str]:
    """`text` as the words it is matched by: letters and digits, case folded."""
    return re.findall(r"\w+", text.casefold())


def holds(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """Whether `needle`'s words appear in `haystack` in order, each within
    `GAP_WORDS` of the one before."""
    if not needle:
        return True
    first = needle[0]
    for start, word in enumerate(haystack):
        if word != first:
            continue
        at = start
        for following in needle[1:]:
            limit = min(len(haystack), at + 1 + GAP_WORDS + 1)
            try:
                at = haystack.index(following, at + 1, limit)
            except ValueError:
                break
        else:
            return True
    return False


@dataclass(frozen=True, slots=True)
class MissingHeadings:
    #: Headings in neither the PDF's text nor the bundle's.
    hidden: tuple[Heading, ...] = ()
    #: Headings in the bundle's text and not the PDF's.
    lost: tuple[Heading, ...] = ()
    #: Headings not in the PDF's text, with no bundle to say more.
    unprinted: tuple[Heading, ...] = ()
    #: The title, when the PDF's text lacks it, and how: `hidden`, `lost`,
    #: `unprinted` as above, or `isolated` when the capture left it out.
    title: Heading | None = None
    title_kind: str | None = None
    #: Why the page's headings could not be read, when they could not.
    unread: str | None = None


def missing_headings(
    shown: ShownHeadings, printed: str, bundle: str | None
) -> MissingHeadings:
    """Which of the headings `shown` the printed text lacks, and whether the
    bundle has them."""
    if shown.unread is not None:
        return MissingHeadings(unread=shown.unread)
    printed_words = words(printed)
    bundle_words = words(bundle) if bundle is not None else None

    def kind(heading: Heading) -> str | None:
        needle = words(heading.text)
        if holds(printed_words, needle):
            return None
        if bundle_words is None:
            return "unprinted"
        return "lost" if holds(bundle_words, needle) else "hidden"

    title: Heading | None = None
    title_kind: str | None = None
    seen: set[str] = set()
    if shown.title is not None and shown.title_fate in ("shown", "isolated"):
        seen.add(" ".join(words(shown.title.text)))
        # The region WebShot chose has a title of its own when one of its
        # `<h1>`s printed, so the PDF has one, and "the PDF has no title"
        # would be false: the page's first `<h1>`, which the region leaves
        # out, is then taken for a site's name in its header (P123-5).
        titled = shown.title_fate == "isolated" and any(
            heading.level == 1 and holds(printed_words, words(heading.text))
            for heading in shown.headings
        )
        if not titled and not holds(printed_words, words(shown.title.text)):
            title = shown.title
            title_kind = (
                "isolated" if shown.title_fate == "isolated" else kind(shown.title)
            )
    groups: dict[str, list[Heading]] = {"hidden": [], "lost": [], "unprinted": []}
    for heading in shown.headings:
        key = " ".join(words(heading.text))
        if not key or key in seen:
            continue
        seen.add(key)
        found = kind(heading)
        if found is not None:
            groups[found].append(heading)
    return MissingHeadings(
        hidden=tuple(groups["hidden"]),
        lost=tuple(groups["lost"]),
        unprinted=tuple(groups["unprinted"]),
        title=title,
        title_kind=title_kind,
    )


def _named(headings: Sequence[Heading]) -> str:
    named = ", ".join(f'"{_short(heading.text)}"' for heading in headings[:NAMED])
    rest = len(headings) - NAMED
    return f"{named} and {rest} more" if rest > 0 else named


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def missing_headings_warnings(
    found: MissingHeadings, *, media: str, explicit_selector: bool = False
) -> list[str]:
    """The manifest warnings for headings the PDF does not print.

    The title is said on its own (docs/09 P123-5). `media` is the media the
    page was printed with: under `print` a heading in neither the PDF nor the
    bundle was hidden by the page's print stylesheet, and `--media screen` is
    the remedy. A title outside the region the caller's own `--selector`
    names (`explicit_selector`) is what was asked for, and is not said.
    """
    if found.unread is not None:
        return [
            f"WebShot could not read the page's headings ({found.unread}), so "
            "whether the PDF lacks one the page showed was not checked."
        ]
    remedy = (
        " --media screen captures the page as the screen shows it."
        if media == "print"
        else ""
    )
    warnings = []
    if found.title is not None and not (
        found.title_kind == "isolated" and explicit_selector
    ):
        warnings.append(_title_warning(found.title, str(found.title_kind), remedy))
    for kind, group in (
        ("hidden", found.hidden),
        ("lost", found.lost),
        ("unprinted", found.unprinted),
    ):
        if group:
            warnings.append(_rest_warning(group, kind, remedy))
    return warnings


def _title_warning(title: Heading, kind: str, remedy: str) -> str:
    start = (
        f"The page's main heading, \"{_short(title.text)}\", is not in the PDF's text"
    )
    if kind == "isolated":
        return (
            f"{start}: it lies outside the region WebShot chose as the page's main "
            "content, so the PDF has no title. --selector with a region that holds "
            "it keeps it."
        )
    if kind == "hidden":
        return (
            f"{start}, nor in the bundle's, though the page showed it on screen: "
            "its print stylesheet, or the print layout, hides it, so the PDF has "
            f"no title.{remedy}"
        )
    if kind == "lost":
        return (
            f"{start}, though the bundle has it: the printed layout lost it, so "
            "the PDF has no title."
        )
    return f"{start}, though the page showed it on screen, so the PDF has no title."


def _rest_warning(headings: Sequence[Heading], kind: str, remedy: str) -> str:
    count = len(headings)
    one = count == 1
    noun = f"{count} heading{'' if one else 's'} the page showed"
    are = "is" if one else "are"
    it = "it" if one else "them"
    if kind == "hidden":
        return (
            f"{noun} {are} in neither the PDF's text nor the bundle's: "
            f"{_named(headings)}. The page's print stylesheet, or its print "
            f"layout, hides {it}, and the sections under {it} may be gone "
            f"too.{remedy}"
        )
    if kind == "lost":
        return (
            f"{noun} {are} in the bundle but not in the PDF's text: "
            f"{_named(headings)}. The printed layout lost {it}, and with {it} "
            f"likely the section{'' if one else 's'} under {it}: text cut off at "
            "the page edge or inside a box that does not grow for print goes "
            "this way."
        )
    return f"{noun} {are} not in the PDF's text: {_named(headings)}."
