"""The enumeration pass: open every expander, read the tree, open no module.

Stage 1 of the journey walker (docs/13, *The journey walker — the
specification*). What it produces is the contract the capture pass is checked
against, which is why it is built and verified first.

Everything here is written against measurements, and the ones that shaped the
code most are the ones nobody asked for:

- **A completion modal blocks the page on every visit** (docs/13 P11-1).
  Completion is the walker's precondition, so this is not an edge case — it is
  the first thing every run meets. It is dismissed with `View Journey Details`,
  which stays on the journey; `Return to Dashboard` navigates away and is never
  clicked.
- **A track view is not ready when the click returns** (P11-2). The title has
  already moved while the body is still a shell. The walk polls for the group
  accordions and fails loudly on timeout, because a fixed wait records an empty
  track as a complete one.
- **Chakra does not unmount a collapsed panel** (P11-4). Collapsed content
  stays in `innerText`, so "the rows are there" cannot witness an expansion.
  `aria-expanded` can, and does.
- **No reference is carried across a click** (P9-4). Row nodes are reused, so a
  handle held across a state change stays valid while meaning something else.
- **Titles come from the DOM heading, never `document.title`** (P9-4).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from ..errors import (
    AuthenticationError,
    CaptureIntegrityError,
    NavigationError,
    certificate_hint,
)
from .model import JourneyNode, JourneyOutline, renumber

LOGGER = logging.getLogger("webshot.journey")


class EnumerationFailure(CaptureIntegrityError):
    """The walk ran and what it produced cannot be trusted (spec §3, exit 5).

    Deliberately not exit 3: the journey *was* reachable. docs/04 §3 draws the
    line as "the page never became available" against "it ran and the result
    cannot be relied on", and an enumeration is the one artifact where a wrong
    answer is worse than no answer — everything downstream is checked against
    it.
    """


@dataclass(frozen=True, slots=True)
class Accordion:
    """A Chakra accordion button — a section in the journey, a group in a track.

    The same component at both levels (docs/13 P11-3), which is why one reader
    serves both. Which level it is is decided by which view is open, and the
    two views are exclusive.
    """

    index: int
    expanded: bool
    panel: str
    label: str
    #: The completion word rendered beside the tick ("Completed"). Empty when
    #: the application renders none.
    status: str = ""


@dataclass(frozen=True, slots=True)
class DomContract:
    """How Continu's DOM is read.

    Measured (docs/13 P11-3), not assumed, which is the difference between this
    and the version that shipped first. What is safe to key on:
    `button.chakra-accordion__button`, `aria-expanded`, `data-index`,
    `aria-controls`, and `aria-label` on track rows.

    What is deliberately *not* keyed on, and why: every `css-*` class is an
    Emotion hash that changes with any style edit, and the `id` values
    (`accordion-button-:rq:`) are React `useId` output that is not stable
    across renders. The stand-in re-hashes both on every render so a walker
    that reaches for either breaks immediately.

    Module rows remain the one shape still taken from P9-4's role rather than
    from measured markup.
    """

    heading: str = "h1"
    #: Read by matching the label's own text rather than by selector. `#progress`
    #: was a fixture-ism and does not exist on the real LMS; worse, the label and
    #: its value are separate elements whose arrangement differs between the
    #: journey and track views, so a structural selector finds it in one and not
    #: the other. Matching "Your Progress <n>%" also avoids P11-6's trap: the
    #: completion dialog says "successfully completed" but never this.
    progress_js: str = r"""
        () => {
            const match = (document.body.innerText || '')
                .match(/Your Progress\s*([0-9]+\s*%)/);
            return match ? match[1].replace(/\s+/g, '') : null;
        }
    """
    #: Matched as a substring of a button's text: the real control reads
    #: "← Back to Journey" and an exact match on the arrow is brittle.
    back_to_journey: str = "Back to Journey"
    #: The dismissal that stays on the journey. The other one navigates away.
    dismiss_modal: str = "View Journey Details"
    #: Module → track. `Next in Track` sits beside it on a module page (P12) and
    #: is a traversal control, so it is named nowhere and pressed never — the
    #: same standing as `Next in Journey`.
    back_to_track: str = "Back to Track"
    #: How long a track view may take to fill in (P11-2 measured a shell at
    #: 1.5 s), and how often to look.
    readiness_timeout_ms: int = 10_000
    poll_interval_ms: int = 100

    accordions_js: str = """
        () => Array.from(document.querySelectorAll('button.chakra-accordion__button'))
            .filter(node => node.getClientRects().length > 0)
            .map(node => ({
                index: Number(node.dataset.index),
                expanded: node.getAttribute('aria-expanded') === 'true',
                panel: node.getAttribute('aria-controls') || '',
                // The FIRST <p> is the title. `textContent` also swallows the
                // status label rendered beside the tick, which made every
                // section come back as "GroundworkCompleted".
                label: (node.querySelector('p')?.textContent
                        || node.textContent || '').trim(),
                // And that status label is the completion state, in plain text
                // — the thing P9-6 recorded as unreadable. Carried so the
                // capture stage need not re-derive it.
                status: (node.querySelectorAll('p')[1]?.textContent || '').trim(),
            }))
    """
    click_accordion_js: str = """
        (index) => {
            const node = Array.from(
                document.querySelectorAll('button.chakra-accordion__button')
            ).filter(candidate => candidate.getClientRects().length > 0)
             .find(candidate => Number(candidate.dataset.index) === index);
            if (!node) return false;
            node.click();
            return true;
        }
    """
    #: `null` distinguishes "this panel has never been mounted" from "it is
    #: mounted and holds nothing" — a distinction P11-4 makes load-bearing.
    panel_tracks_js: str = """
        (panel) => {
            const root = document.getElementById(panel);
            if (!root) return null;
            return Array.from(root.querySelectorAll('button[aria-label]'))
                .map(node => node.getAttribute('aria-label'));
        }
    """
    #: Measured, and it is not what P9-4 inferred. There is no `role="article"`
    #: anywhere on this LMS — zero matches on a real group panel. A module row
    #: is a plain `div` carrying three `<p>`s (title, duration, type) and a
    #: React `onClick`; it has no role, no `data-testid`, no inner button, and
    #: `cursor: auto`, so nothing about it is findable by affordance. The only
    #: stable handle is the shape, since every `css-*` class is an Emotion hash.
    panel_modules_js: str = """
        (panel) => {
            const root = document.getElementById(panel);
            if (!root) return null;
            const stack = root.firstElementChild || root;
            return Array.from(stack.children)
                .map(row => Array.from(row.querySelectorAll('p'))
                    .map(node => (node.textContent || '').trim()))
                .filter(parts => parts.length >= 3)
                // The type is the LAST `<p>`, not the third, and that is
                // deliberate rather than an accident of a three-cell row.
                // They are identical on the markup P12-1 measured. If a fourth
                // cell ever appears, `parts[2]` would keep reading `article`
                // and the module would open on an assumption; the last cell
                // reads the badge instead, which the capture pass does not
                // recognise and therefore refuses — loudly, naming what it
                // read. Failing safe is worth more here than being right about
                // an arrangement nobody has measured.
                .map(parts => ({
                    title: parts[0],
                    duration: parts[1],
                    type: parts[parts.length - 1].toLowerCase(),
                }));
        }
    """
    #: The group's description is a Froala block (`.fr-view`) — the same class
    #: P9-7 named. Taking "the first <p> in the panel" instead returned the
    #: first *module's title* as the group's prose, because the description
    #: block can be empty and often is.
    panel_prose_js: str = """
        (panel) => {
            const root = document.getElementById(panel);
            if (!root) return null;
            const view = root.querySelector('.fr-view');
            const text = view ? (view.innerText || '').trim() : '';
            return text || null;
        }
    """
    #: Scoped to the section's own panel, exactly as `panel_tracks_js` is. A
    #: document-wide `find` takes the first match in DOM order, and P11-5 keeps
    #: every section expanded — so once the walk is past section one, an
    #: earlier section's track row is still in the document and wins. Two
    #: sections sharing a track title is ordinary courseware naming, and the
    #: result was section two recorded holding section one's content under
    #: section two's ids. Reading was already scoped; only the click was not.
    #: Scoped to the owning group's panel for the same reason `click_track_js`
    #: is scoped to its section's: a document-wide match takes the first row in
    #: DOM order, and two groups can hold modules with the same title.
    #: Matched on the row's FIRST `<p>`, which is the title. P12-1 measured
    #: three: title, duration, type.
    click_module_js: str = """
        ([panel, title]) => {
            const root = document.getElementById(panel);
            if (!root) return false;
            const stack = root.firstElementChild || root;
            const row = Array.from(stack.children).find(candidate => {
                const parts = candidate.querySelectorAll('p');
                return parts.length >= 3
                    && (parts[0].textContent || '').trim() === title;
            });
            if (!row) return false;
            row.click();
            return true;
        }
    """
    click_track_js: str = """
        ([panel, label]) => {
            const root = document.getElementById(panel);
            if (!root) return false;
            const node = Array.from(root.querySelectorAll('button[aria-label]'))
                .find(candidate => candidate.getAttribute('aria-label') === label);
            if (!node) return false;
            node.click();
            return true;
        }
    """
    #: Document-wide by design, and safe *here* because its only caller is the
    #: return to the journey, where every match does the same thing. The
    #: dialog's control is a different question — pressing the wrong "View
    #: Journey Details" could navigate — so it has its own scoped resolver
    #: below. Asking what else sits on a boundary being fixed is P7-12's rule.
    #: Leaf buttons only, matched on normalised *equality* rather than
    #: `includes`. A substring match takes the first button in document order
    #: whose text merely contains the name — a "Back to Journey Overview"
    #: elsewhere on the page wins over the real control — and matching any
    #: button rather than a leaf one lets a wrapper match through its
    #: descendants. Normalising strips the leading arrow, since the real
    #: control reads "← Back to Journey".
    click_named_button_js: str = r"""
        (name) => {
            const norm = s => (s || '').replace(/^[^\p{L}\p{N}]+/u, '').trim();
            const node = Array.from(document.querySelectorAll('button'))
                .filter(candidate => !candidate.querySelector('button'))
                .find(candidate => norm(candidate.textContent) === name);
            if (!node) return false;
            node.click();
            return true;
        }
    """
    #: Visibility is tested with `getClientRects()`, not `offsetParent`. A
    #: `position: fixed` element always reports a null `offsetParent`, so the
    #: obvious check reports the blocking dialog as absent — the guard defeated
    #: by the very property that makes the thing block (docs/09 P10-3).
    click_dialog_button_js: str = """
        (name) => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return false;
            const node = Array.from(dialog.querySelectorAll('button'))
                .find(candidate => (candidate.textContent || '').trim() === name);
            if (!node) return false;
            node.click();
            return true;
        }
    """
    modal_js: str = """
        () => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog || dialog.hidden || dialog.getClientRects().length === 0) {
                return null;
            }
            return {
                text: (dialog.textContent || '').trim().slice(0, 200),
                buttons: Array.from(dialog.querySelectorAll('button'))
                    .map(node => (node.textContent || '').trim()),
            };
        }
    """


async def _accordions(page: Any, contract: DomContract) -> list[Accordion]:
    """Re-read every accordion. Never cached — that is the point (P9-4)."""
    raw = await page.evaluate(contract.accordions_js)
    return [
        Accordion(
            index=int(item["index"]),
            expanded=bool(item["expanded"]),
            panel=str(item["panel"]),
            label=str(item["label"]),
            status=str(item.get("status") or ""),
        )
        for item in raw
    ]


async def _dismiss_completion_modal(page: Any, contract: DomContract) -> bool:
    """Clear the "You Did It!" dialog, which blocks a completed journey (P11-1).

    Returns whether one was there. Not a warning when it is: it fires on every
    visit to a completed journey, and completion is the precondition, so its
    presence is the normal case and its *absence* is the surprise.
    """
    modal = await page.evaluate(contract.modal_js)
    if modal is None:
        return False
    clicked = await page.evaluate(
        contract.click_dialog_button_js, contract.dismiss_modal
    )
    if not clicked:
        raise EnumerationFailure(
            f"a dialog is blocking the journey and it has no {contract.dismiss_modal!r} "
            f"control to clear it. It offers {modal['buttons']!r}. Refusing rather than "
            "pressing something that might navigate away: the walk cannot read a page "
            "it cannot reach, and a click that lands on the overlay returns cleanly "
            "while doing nothing."
        )
    if await page.evaluate(contract.modal_js) is not None:
        raise EnumerationFailure(
            f"the completion dialog survived {contract.dismiss_modal!r}. Every click "
            "from here would be intercepted and would report success, so the walk "
            "stops instead of enumerating an empty journey."
        )
    return True


async def _refuse_blocking_dialog(page: Any, contract: DomContract, where: str) -> None:
    """Fail loudly if a dialog appears anywhere the walk did not expect one.

    P11-1 measured the modal on a page visit. Whether one can appear mid-walk
    is unmeasured, so this refuses rather than dismissing something nobody has
    seen: an unexpected dialog silently swallows every subsequent click.
    """
    modal = await page.evaluate(contract.modal_js)
    if modal is not None:
        raise EnumerationFailure(
            f"a dialog appeared {where}: {modal['text']!r}. Clicks from here are "
            "intercepted and return cleanly, so the walk stops rather than recording "
            "whatever it can still see."
        )


async def _ensure_expanded(
    page: Any, contract: DomContract, index: int, what: str
) -> Accordion:
    """Expand one accordion and *witness* it with `aria-expanded`.

    P11-4 is why the witness is the attribute and not the content: Chakra
    collapses by animating height, the panel is not unmounted, and the text
    stays in `innerText`. A walker that asks "are the rows there?" is answered
    yes by a group it never opened.
    """
    current = next(
        (node for node in await _accordions(page, contract) if node.index == index),
        None,
    )
    if current is None:
        raise EnumerationFailure(
            f"{what} was not present when the walk tried to open it"
        )
    if current.expanded:
        return current
    if not await page.evaluate(contract.click_accordion_js, index):
        raise EnumerationFailure(
            f"{what} vanished between being read and being clicked"
        )
    after = next(
        (node for node in await _accordions(page, contract) if node.index == index),
        None,
    )
    if after is None or not after.expanded:
        raise EnumerationFailure(
            f"{what} did not expand: aria-expanded is still false after the click. "
            "Either the click did not reach it — a dialog intercepting, or the "
            "stale-reference hazard of docs/13 P9-4 — or the control is not an "
            "accordion. Reporting it rather than recording an empty node."
        )
    return after


async def _wait_for_accordions(
    page: Any, contract: DomContract, what: str
) -> list[Accordion]:
    """Poll until the view has filled in, and fail loudly if it never does.

    P11-2: after a track click the title has already changed while the body is
    a 141-character shell with no accordions in it. A fixed interval is the
    wrong instrument — it either wastes the whole walk's time or records an
    empty track as a complete one.
    """
    deadline = contract.readiness_timeout_ms / 1000
    waited = 0.0
    step = contract.poll_interval_ms / 1000
    while True:
        found = await _accordions(page, contract)
        if found:
            return found
        if waited >= deadline:
            raise EnumerationFailure(
                f"{what} never rendered an accordion within "
                f"{contract.readiness_timeout_ms} ms. docs/13 P11-2 records this view "
                "arriving after the click returns, so this is a timeout rather than an "
                "empty track — recording it as empty would be the silent loss the "
                "enumeration exists to prevent."
            )
        await asyncio.sleep(step)
        waited += step


def _require_unique(labels: list[str], level: str, where: str) -> None:
    """Refuse a level whose labels repeat, where the walk addresses by label.

    Only tracks need this now. Sections and groups are reached by `data-index`,
    which is the component's own ordering (P11-3) and cannot collide — one of
    the places the measured markup removed an assumption rather than adding a
    guard.
    """
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        raise EnumerationFailure(
            f"{where} lists more than one {level} called {repeated!r}. A track row "
            "carries its title in aria-label and is reached by it, so identical "
            "siblings cannot be told apart and would be recorded twice."
        )


async def _text(page: Any, selector: str, what: str) -> str:
    try:
        value = await page.inner_text(selector)
    except Exception as exc:
        raise EnumerationFailure(f"could not read {what} ({selector}): {exc}") from exc
    return str(value).strip()


#: Whether the page we landed on is asking for credentials rather than showing
#: a journey. Two independent signals because either alone is guessable: a
#: password field is what a sign-in page *has*, and these path fragments are
#: what this LMS's sign-in URLs *are* — `Login.aspx` is the sign-in page of the
#: documentation portal the measured journey links to, and it is the form
#: docs/13 already requires outbound links to keep.
_LOOKS_LIKE_SIGN_IN = """() => {
    const password = !!document.querySelector('input[type="password"]');
    const path = (location.pathname + location.search).toLowerCase();
    const named = ['login.aspx', '/login', '/signin', '/sign-in', '/sso/']
        .some(fragment => path.includes(fragment));
    return {password, named, url: location.href};
}"""


async def _sign_in_page(page: Any) -> str:
    """The landed URL if this looks like a sign-in page, else an empty string."""
    try:
        seen = await page.evaluate(_LOOKS_LIKE_SIGN_IN)
    except Exception:  # pragma: no cover - a page that closed under us
        return ""
    if seen.get("password") or seen.get("named"):
        return str(seen.get("url") or "")
    return ""


async def _progress(page: Any, contract: DomContract) -> str:
    """The journey's progress figure, or a refusal if it cannot be read.

    Not optional and not defaulted: this value is the whole of the walk's
    safety argument, and a walk that could not read it has not established the
    thing it needs to establish.

    An unauthenticated run fails *here*, because a sign-in page has no progress
    element either — and "could not read the journey's progress" is a true
    sentence that sends the reader to the DOM contract when the actual problem
    is that they are not signed in. The check runs only on the failure path, so
    a healthy walk pays nothing for it.
    """
    value = await page.evaluate(contract.progress_js)
    if not value:
        landed = await _sign_in_page(page)
        if landed:
            # `AuthenticationError`, not `EnumerationFailure`: spec §3 reserves
            # exit 4 for "authentication required, or the auth material was
            # rejected", and this is exactly that. Raising the integrity error
            # would fix the sentence a person reads and leave the number a
            # script reads misclassified — the same defect this check exists to
            # remove, surviving in the machine-readable half.
            raise AuthenticationError(
                f"this looks like a sign-in page, not a journey: the browser is at "
                f"{landed}. The session is not established, so there is nothing to "
                "walk. Sign in first with --auth-profile (add --interactive-auth to "
                "be prompted), or pass --storage-state."
            )
        raise EnumerationFailure(
            "could not read the journey's progress. The walk compares it before and "
            "after as its only evidence that a read-only pass stayed read-only, so it "
            "stops rather than proceeding without that evidence."
        )
    return str(value)


async def open_module(page: Any, contract: DomContract, panel: str, title: str) -> None:
    """Open one module from its group's panel, and wait for its body to arrive.

    The enumeration deliberately never calls this — it opens no module at all.
    The capture pass does, for every module the outline says is an `article`,
    and for none that it says is an `assessment` (docs/13; P12-2 found two in
    the first group opened live).

    Readiness is polled rather than waited out, for P11-2's reason: the heading
    moves while the body is still a shell, so a fixed interval either wastes a
    low-hundreds journey's time or captures an empty page.
    """
    if not await page.evaluate(contract.click_module_js, [panel, title]):
        raise EnumerationFailure(
            f"the module row {title!r} was not present in its group's panel ({panel})"
        )
    deadline = contract.readiness_timeout_ms / 1000
    waited = 0.0
    step = contract.poll_interval_ms / 1000
    while True:
        heading = (await page.inner_text(contract.heading)).strip()
        body = await page.evaluate(
            "() => (document.querySelector('.fr-view') || {}).innerText || ''"
        )
        if heading == title and str(body).strip():
            return
        if waited >= deadline:
            raise EnumerationFailure(
                f"the module {title!r} never rendered a body within "
                f"{contract.readiness_timeout_ms} ms. Recording it as captured would "
                "publish an empty page for a module that has content."
            )
        await asyncio.sleep(step)
        waited += step


async def return_to_track(page: Any, contract: DomContract, what: str) -> None:
    """Leave a module the way P12 measured, and never by a traversal control."""
    if not await page.evaluate(contract.click_named_button_js, contract.back_to_track):
        raise EnumerationFailure(f"no {contract.back_to_track!r} control on {what}")


async def _read_group(
    page: Any, contract: DomContract, accordion: Accordion, warnings: list[str]
) -> JourneyNode:
    """Expand one group and read its modules. Opens no module."""
    opened = await _ensure_expanded(
        page, contract, accordion.index, f"the group {accordion.label!r}"
    )
    modules = await page.evaluate(contract.panel_modules_js, opened.panel)
    if not modules:
        # `None` is an unmounted panel; `[]` is a mounted one holding nothing.
        # Both stop the walk, because the likeliest cause of the second is the
        # least-verified assumption in this file: module rows are still matched
        # by P9-4's inferred `[role="article"]`, the one shape P11-3 did not
        # measure. If that selector is wrong, EVERY group reads empty and every
        # self-check passes on `0 == 0` — silence that looks like success.
        raise EnumerationFailure(
            f"the group {accordion.label!r} reports aria-expanded=true and yielded no "
            f"module from its panel ({opened.panel}). Either the panel is not in the "
            "document, or the module-row selector does not match this LMS. Both are "
            "reported rather than recorded as an empty group."
        )
    prose = await page.evaluate(contract.panel_prose_js, opened.panel)
    if not prose:
        warnings.append(
            f"the group {accordion.label!r} revealed no description; recorded without "
            "the prose that docs/13 P9-1 expects at this level"
        )
    return JourneyNode(
        node_id="",
        level="group",
        ordinal=accordion.index + 1,
        title=accordion.label,
        prose=prose or None,
        children=tuple(
            JourneyNode(
                node_id="",
                level="module",
                ordinal=0,
                title=str(record.get("title", "")),
                # Recorded, never branched on here — but the capture stage MUST
                # branch on it, because `assessment` is the one type docs/13
                # forbids opening. An enumeration that published only titles
                # gave it nothing to refuse with.
                module_type=str(record.get("type") or "") or None,
                duration=str(record.get("duration") or "") or None,
            )
            for record in modules
        ),
    )


async def _read_track(
    page: Any, contract: DomContract, panel: str, track_label: str, warnings: list[str]
) -> JourneyNode:
    """Open one track, read every group inside it, and return to the journey.

    `panel` is the owning section's panel id, and it is required rather than
    optional: resolving a track row anywhere else in the document is the defect
    this signature exists to make impossible.
    """
    if not await page.evaluate(contract.click_track_js, [panel, track_label]):
        raise EnumerationFailure(
            f"the track row {track_label!r} was not present in its section's panel "
            f"({panel})"
        )
    await _refuse_blocking_dialog(
        page, contract, f"on opening the track {track_label!r}"
    )
    accordions = await _wait_for_accordions(
        page, contract, f"the track {track_label!r}"
    )

    groups = [await _read_group(page, contract, node, warnings) for node in accordions]

    # One re-read, after every group in this track has been opened. That makes
    # each group's count a genuine second look across the renders in between —
    # not a value compared with itself one statement later (docs/09 P10-2).
    settled = {node.index: node for node in await _accordions(page, contract)}
    rechecked: list[JourneyNode] = []
    for group in groups:
        node = settled.get(group.ordinal - 1)
        if node is None:
            rechecked.append(replace(group, listed=0))
            continue
        again = await page.evaluate(contract.panel_modules_js, node.panel)
        rechecked.append(replace(group, listed=0 if again is None else len(again)))

    if not await page.evaluate(
        contract.click_named_button_js, contract.back_to_journey
    ):
        raise EnumerationFailure(
            f"no {contract.back_to_journey!r} control on the track {track_label!r}"
        )
    return JourneyNode(
        node_id="",
        level="track",
        ordinal=0,
        title=track_label,
        listed=len(settled),
        children=tuple(rechecked),
    )


async def _read_section(
    page: Any, contract: DomContract, accordion: Accordion, warnings: list[str]
) -> JourneyNode:
    """Expand one section and walk each of its tracks."""
    opened = await _ensure_expanded(
        page, contract, accordion.index, f"the section {accordion.label!r}"
    )
    track_labels = await page.evaluate(contract.panel_tracks_js, opened.panel)
    if not track_labels:
        raise EnumerationFailure(
            f"the section {accordion.label!r} reports aria-expanded=true and lists no "
            "track. A section with no tracks is more likely a walk that opened the "
            "wrong thing than a real shape."
        )
    _require_unique(list(track_labels), "track", f"the section {accordion.label!r}")

    tracks: list[JourneyNode] = []
    for label in track_labels:
        # Re-resolved before every click, never carried. `aria-controls` is
        # React `useId` output, and this file's own rule is that nothing may
        # key on one across a render — while each track visit is a full
        # navigation into another view and back. Reusing the id captured before
        # the loop was a cross-navigation dependency on exactly the kind of
        # value the contract says is unstable.
        current = next(
            (
                node
                for node in await _accordions(page, contract)
                if node.index == accordion.index
            ),
            None,
        )
        if current is None:
            raise EnumerationFailure(
                f"the section {accordion.label!r} was no longer present when the walk "
                f"returned to open the track {label!r}"
            )
        tracks.append(
            await _read_track(page, contract, current.panel, str(label), warnings)
        )
    # Expansion survives the return trip (P9-4), so the second look at this
    # section costs one evaluate rather than a second walk.
    settled = next(
        (
            node
            for node in await _accordions(page, contract)
            if node.index == accordion.index
        ),
        None,
    )
    again = (
        await page.evaluate(contract.panel_tracks_js, settled.panel)
        if settled
        else None
    )
    return JourneyNode(
        node_id="",
        level="section",
        ordinal=accordion.index + 1,
        title=accordion.label,
        listed=0 if again is None else len(again),
        children=tuple(tracks),
    )


async def enumerate_journey(
    page: Any, *, source: str, contract: DomContract | None = None
) -> JourneyOutline:
    """Walk one journey and return its outline. Opens no module, downloads nothing.

    Progress is read before and after and compared here rather than by the
    caller: docs/13 makes the completed journey a precondition, and a walk that
    moved it is the one failure that reaches outside the machine.
    """
    contract = contract or DomContract()
    warnings: list[str] = []
    try:
        await page.goto(source, wait_until="domcontentloaded")
    except Exception as exc:
        raise NavigationError(
            f"could not open the journey at {source}: {exc}{certificate_hint(str(exc))}"
        ) from exc

    # Its absence is deliberately not a signal. P11-1 measured this dialog on
    # every visit; P12 could not reproduce it on two fresh loads of the same
    # completed journey. Progress is what establishes completion, and it is read
    # immediately below — treating a missing dialog as evidence would have
    # warned on every real run and been wrong.
    await _dismiss_completion_modal(page, contract)

    title = await _text(page, contract.heading, "the journey heading")
    progress_before = await _progress(page, contract)

    sections = await _accordions(page, contract)
    if not sections:
        raise NavigationError(
            f"{source} rendered no sections. The journey may not have finished loading, "
            "or the DOM contract in this build does not match this LMS."
        )
    read = [await _read_section(page, contract, node, warnings) for node in sections]
    listed_sections = len(await _accordions(page, contract))

    progress_after = await _progress(page, contract)
    if progress_before != progress_after:
        raise EnumerationFailure(
            f"the journey's progress moved during a read-only walk: "
            f"{progress_before!r} -> {progress_after!r}. This is a training record; "
            "the walk is stopped rather than reported as a footnote."
        )

    journey_id = _journey_id(source)
    root = JourneyNode(
        node_id="",
        level="journey",
        ordinal=1,
        title=title,
        listed=listed_sections,
        children=tuple(read),
    )
    root = renumber(root, f"journey/{journey_id}", 1)
    return JourneyOutline(
        journey_id=journey_id,
        source=source,
        title=title,
        progress_before=progress_before,
        progress_after=progress_after,
        root=root,
        warnings=tuple(warnings),
    )


def _journey_id(source: str) -> str:
    """The journey's own id, which is the one address this application has.

    The last non-empty path segment, query dropped: `contextId` names no module
    (docs/13) and including it would make an id that changes with how the
    operator happened to arrive.
    """
    from urllib.parse import urlsplit

    path = urlsplit(source).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1] if path else ""
    return segment or "journey"
