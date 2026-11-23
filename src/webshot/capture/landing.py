"""Whether the browser landed on the page that was asked for.

Three ways a capture can run to the end and still not be of the page, and
each used to exit 0 with no warning (docs/09 P14-1, P22, P78):

- **a sign-in wall.** The site sent the browser to a sign-in page on another
  origin, because it had no session. docs/04-spec.md §5 item 4 makes this
  exit 4, and before P22 only a 401 or 403 did.
- **an empty capture.** The page rendered nothing a reader could use: an app
  shell whose script never filled it, a document served to a browser with no
  session, a `--selector` that matched an empty element. §5 item 14 makes this
  a manifest warning, and `--require-content` a failure.
- **a bot check's page.** The site answered 200 and a bot check drew its own
  page in place of the page asked for, which §5 item 4 makes exit 4, as it
  does a 403 that names its bot check.

And three that say less than they should: a 403 whose page is the site's own
"not found" page, which is a dead link and not a refusal (P132-5); a page
that declares an article its capture does not hold (`capture/headings`,
P132-4); and a page that hides its article after its wall's marker (P132-7).

Detection only. Nothing here signs in, retries, waits longer or changes what
the page is sent; a wall is reported to the person who can sign in, which is
the line docs/11 draws around access controls.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from .prepare import CLASS_WORD_JS

LOGGER = logging.getLogger("webshot")

#: Fewer characters of text than this, with no image and no video, is a page
#: that did not render its content. The figure is the one Crawl4AI's structural
#: check uses for "minimal text" (`crawl4ai/antibot_detector.py`); here it reads
#: the text of the bundle's own `content.txt`, so the warning describes the
#: file a reader has. A one-sentence notice clears it, while "Loading…",
#: "Redirecting…" and "Your session has expired. Please sign in again." do not
#: (docs/09 P22-2).
NEAR_EMPTY_CHARACTERS = 50

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: A password field a person could type into: in the top document or any open
#: shadow root, laid out with a size, not hidden by CSS, and not parked off the
#: page. Hidden ones are excluded because an ordinary article can carry a
#: collapsed sign-in dropdown, and that is not a wall. Frames are not read: the
#: spec's rule is about the page the redirect landed on, and a frame's own
#: visibility would need its own rule.
_VISIBLE_PASSWORD_FIELD = """() => {
    const roots = [document];
    for (let index = 0; index < roots.length; index++) {
        const root = roots[index];
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) roots.push(element.shadowRoot);
        }
        for (const field of root.querySelectorAll('input[type="password"]')) {
            const box = field.getBoundingClientRect();
            if (box.width <= 0 || box.height <= 0) continue;
            if (box.right + window.scrollX <= 0 || box.bottom + window.scrollY <= 0) continue;
            if (!field.checkVisibility({
                checkOpacity: true, checkVisibilityCSS: true,
                opacityProperty: true, visibilityProperty: true,
            })) continue;
            return true;
        }
    }
    return false;
}"""


def _origin(url: str) -> tuple[str, str, int] | None:
    """`(scheme, host, port)` for an http(s) URL, or None for anything else."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[scheme]


def crossed_origin(requested: str, landed: str) -> bool:
    """Whether the browser ended up on a different origin from the one asked for.

    False when either side is not an http(s) URL: a local file has no origin to
    leave, and "could not tell" must not read as "redirected".
    """
    asked, reached = _origin(requested), _origin(landed)
    return asked is not None and reached is not None and asked != reached


#: A path that names a site's home page itself: asking for it is asking for
#: the home page.
_INDEX_FILE = re.compile(r"/(?:index|default)\.(?:html?|php|aspx?)$", re.IGNORECASE)


def landed_on_home_page(requested: str, landed: str) -> bool:
    """Whether the browser went from a page of a site to that site's home page.

    The same origin only: another origin is the sign-in wall's rule (P22-1).
    A signed-out browser sent from a page that needs a session to the home
    page was captured as that page, with only the generic "nearly empty"
    warning to say anything was wrong (docs/09 P23-5). A page asked for as the
    home page, or as its index file, is not one.
    """
    if _origin(requested) is None or _origin(requested) != _origin(landed):
        return False
    asked = _INDEX_FILE.sub("/", urlsplit(requested).path).rstrip("/")
    return bool(asked) and urlsplit(landed).path in ("", "/")


def home_page_message(requested: str, landed: str) -> str:
    """The manifest warning for `landed_on_home_page`."""
    return (
        f"The browser was sent from {requested} to the site's home page "
        f"({landed}), so this is a capture of the home page, not of the page "
        "asked for. A site does this with a page that needs a sign-in or no "
        "longer exists. If the home page is what you meant, give its address "
        "directly."
    )


def requested_url(response: Any, fallback: str) -> str:
    """The first URL of the navigation's redirect chain, as Chromium spelled it.

    Chromium's own spelling rather than the command line's, so a host given in
    Unicode or capitals compares equal to the punycode, lower-case host the
    browser reports in `page.url`.
    """
    if response is None:
        return fallback
    request = response.request
    while request.redirected_from is not None:
        request = request.redirected_from
    return str(request.url)


@dataclass(frozen=True, slots=True)
class WallCheck:
    """What the sign-in check found. Neither field set means no wall.

    Two fields rather than an optional URL, because "the page could not be
    read" and "the page has no password field" are different answers, and
    folding the first into the second let a capture that was never checked
    pass as one that was (docs/09 P22-1).
    """

    #: The URL of the sign-in wall the browser landed on.
    landed: str | None = None
    #: Why the landed page could not be read, when it could not.
    unread: str | None = None


async def sign_in_wall(page: Any, requested: str) -> WallCheck:
    """Whether the page is a sign-in wall on another origin.

    Both signals of docs/04-spec.md §5 item 4, and only both: a sign-in form on
    the page asked for is a page someone may mean to capture, and a redirect to
    another origin is how many sites route an ordinary link. The page is read
    only when the origin changed, so a capture that was not redirected pays
    nothing.

    A read that fails is tried once more after the document loads, because the
    usual cause is a navigation that began after the waits and destroyed the
    document mid-read. The URL is taken again then, since it is the new
    document that is read. A second failure is `unread`, never "no wall".
    """
    problem = ""
    for _ in range(2):
        landed = str(page.url)
        if not crossed_origin(requested, landed):
            return WallCheck()
        try:
            visible = bool(await page.evaluate(_VISIBLE_PASSWORD_FIELD))
        except PlaywrightError as exc:
            problem = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            with contextlib.suppress(PlaywrightError):
                await page.wait_for_load_state("domcontentloaded")
            continue
        return WallCheck(landed=landed if visible else None)
    return WallCheck(
        unread=(
            f"The sign-in check could not read the page the browser landed on "
            f"({landed}), which is on another origin from the one asked for: "
            f"{problem}. Whether it is a sign-in page was not established, so "
            "check the PDF before relying on it."
        )
    )


def sign_in_wall_message(source: str, landed: str, *, interactive: bool) -> str:
    """What exit 4 says about a wall, and what to do about it."""
    if interactive:
        return (
            f"The browser is still at a sign-in page ({landed}) after you pressed "
            f"Enter, so sign-in did not finish and {source} was never shown. Run "
            "again and press Enter only once the page itself is visible."
        )
    return (
        f"{source} sent the browser to a sign-in page on another site ({landed}), "
        "which shows a password field: the browser has no session there, so "
        "nothing of the page could be captured. Pass a signed-in session with "
        "--storage-state, or use --auth-profile (add --interactive-auth to sign "
        "in once). If the page it landed on is what you meant to capture, give "
        "that address directly."
    )


#: What a person is told to do about a bot check: the one path that works,
#: because the check refuses this browser and not the person (docs/09 P23-3).
SAVE_IT_YOURSELF = (
    "To capture the page, open it in your own browser, save it there, and "
    "capture the saved file."
)

#: A bot check's own page, shown in place of the page with HTTP 200, in the
#: top document or any open shadow root. Each signature is the vendor naming
#: itself, never a phrase a page could quote (docs/09 P22, P78-1):
#:
#: - DataDome draws its captcha and its block page in a frame from its own
#:   host, `geo.captcha-delivery.com`, over the page it refuses. Only a frame
#:   that is shown counts; one the page laid out invisibly blocks nothing.
#: - HUMAN (PerimeterX) writes its press-and-hold challenge into an element
#:   with the id `px-captcha`. Its height is that of a frame its script loads,
#:   so a rendered box of any size counts.
#: - Akamai's refusal is titled "Access Denied" and carries a reference in its
#:   own dotted format. Both are required: the title alone is an ordinary
#:   phrase, and an article about Akamai could quote a reference.
#: - F5 BIG-IP's application firewall answers a request it blocks with its
#:   stock page, titled "Request Rejected", whose whole text is "The requested
#:   URL was rejected. Please consult with your administrator." and "Your
#:   support ID is: <digits>". A government policy page was captured as that
#:   page, 126 characters with exit 0, by two captures in one run (docs/09
#:   P120-1). As with Akamai, the title and the support ID are both required,
#:   and the sentence too: each alone is a phrase a page could quote.
#: - Imperva's Incapsula answers a browser it has not yet cleared with a page
#:   whose whole content is a script from its own path, `/_Incapsula_Resource`,
#:   and blocks one with a page whose whole content is a frame from that path
#:   (its query naming the incident). The script is on every page Incapsula
#:   serves, the article as well (docs/09 P132-3), so only a page that shows
#:   nothing else counts: no text, and no box but the frame's.
#:
#: The answer is `[vendor, detail]`, or null when no signature is present.
_BOT_CHECK_PAGE = r"""() => {
    const roots = [document];
    for (let index = 0; index < roots.length; index++) {
        for (const element of roots[index].querySelectorAll('*')) {
            if (element.shadowRoot) roots.push(element.shadowRoot);
        }
    }
    const shown = element => element.checkVisibility({
        checkOpacity: true, checkVisibilityCSS: true,
        opacityProperty: true, visibilityProperty: true,
    });
    for (const root of roots) {
        for (const frame of root.querySelectorAll('iframe[src]')) {
            let host;
            try { host = new URL(frame.getAttribute('src'), document.baseURI).hostname; }
            catch (error) { continue; }
            const dataDome = host === 'captcha-delivery.com' || host.endsWith('.captcha-delivery.com');
            const box = frame.getBoundingClientRect();
            if (dataDome && box.width > 0 && box.height > 0 && shown(frame)) {
                return ['datadome', host];
            }
        }
        const challenge = root.querySelector('#px-captcha');
        if (challenge && shown(challenge)) return ['perimeterx', '#px-captcha'];
    }
    if (document.title.trim().toLowerCase() === 'access denied' && document.body) {
        const reference = /Reference\s*#\s*(\d+\.[0-9a-f]+\.\d+\.[0-9a-f]+)/i.exec(
            document.body.innerText
        );
        if (reference) return ['akamai', reference[1]];
    }
    if (document.body && !document.body.innerText.trim()) {
        const own = element => {
            try {
                return new URL(element.getAttribute('src'), document.baseURI).pathname
                    === '/_Incapsula_Resource';
            } catch (error) { return false; }
        };
        const drawn = [...document.body.querySelectorAll('*')].filter(element =>
            !['script', 'noscript', 'style', 'template'].includes(element.localName)
            && element.getClientRects().length > 0 && shown(element));
        const frame = drawn.find(element => element.localName === 'iframe' && own(element));
        if (frame && drawn.every(element => element === frame || frame.contains(element))) {
            const incident = new URL(frame.getAttribute('src'), document.baseURI)
                .searchParams.get('incident_id');
            return ['incapsula-block', incident || ''];
        }
        if (!drawn.length && [...document.querySelectorAll('script[src]')].some(own)) {
            return ['incapsula', '/_Incapsula_Resource'];
        }
    }
    if (document.title.trim().toLowerCase() === 'request rejected' && document.body) {
        const said = document.body.innerText.replace(/\s+/g, ' ');
        const support = /Your support ID is:?\s*<?\s*(\d{6,})\s*>?/i.exec(said);
        if (support && /The requested URL was rejected\./i.test(said)) {
            return ['f5', support[1]];
        }
    }
    return null;
}"""


@dataclass(frozen=True, slots=True)
class BotCheckPage:
    """What the bot-check test found. Neither field set means no bot check.

    Two fields for the reason `WallCheck` has two: a page that could not be
    read has not been shown to be the page.
    """

    #: Which bot check's page the browser was shown, in words.
    check: str | None = None
    #: Why the page could not be read, when it could not.
    unread: str | None = None
    #: Whether the page is a firewall's refusal of the request rather than a
    #: check that names automation, which decides what the message may claim.
    firewall: bool = False


def _bot_check_name(vendor: str, detail: str) -> str:
    if vendor == "datadome":
        return f"DataDome's bot protection (its challenge frame from {detail})"
    if vendor == "perimeterx":
        return f"HUMAN's bot protection, formerly PerimeterX ({detail})"
    if vendor == "f5":
        return (
            f'the "Request Rejected" page of F5 BIG-IP\'s application firewall '
            f"(support ID {detail})"
        )
    if vendor == "incapsula":
        return (
            "the challenge page of Imperva's bot protection, Incapsula (a page "
            f"holding nothing but its script from {detail})"
        )
    if vendor == "incapsula-block":
        incident = f", incident ID {detail}" if detail else ""
        return (
            "the block page of Imperva's application firewall, Incapsula (a "
            f"frame from /_Incapsula_Resource{incident})"
        )
    return f'Akamai\'s "Access Denied" page (Reference #{detail})'


async def bot_check_page(page: Any) -> BotCheckPage:
    """Whether the page on screen is a bot check's page rather than the page.

    A site behind DataDome answered with 200 and the article, and the check's
    frame was drawn over it; the capture printed the frame's "We detected
    unusual activity" over the article's title and exited 0 (docs/09 P78-1).
    The response says nothing, so the page is read. A read that fails is tried
    once more after the document loads, as `sign_in_wall` does, and a second
    failure is `unread`, never "no bot check".
    """
    problem = ""
    for _ in range(2):
        try:
            found = await page.evaluate(_BOT_CHECK_PAGE)
        except PlaywrightError as exc:
            problem = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            with contextlib.suppress(PlaywrightError):
                await page.wait_for_load_state("domcontentloaded")
            continue
        if found is None:
            return BotCheckPage()
        vendor, detail = found
        return BotCheckPage(
            check=_bot_check_name(str(vendor), str(detail)),
            firewall=vendor in ("f5", "incapsula-block"),
        )
    return BotCheckPage(
        unread=(
            f"The bot-check test could not read the page ({page.url}): "
            f"{problem}. Whether a bot check was shown in place of the page was "
            "not established, so check the PDF before relying on it."
        )
    )


#: Access walls (a site asking the reader to sign in, register or subscribe
#: before it shows the article), by vendor, as each vendor's script writes its
#: box. Mg2's ConneXt was measured: a news site's registration box, printed on
#: ten pages over an article whose text the PDF lost (docs/09 P97-1). Piano's
#: follows its documented markup and was not met in a capture. A wall is only
#: named, never a reason to fail: whether it hid the article is what the
#: printed-text share measures (`render/text_layer.py`).
ACCESS_WALLS: dict[str, tuple[str, ...]] = {
    "Mg2 ConneXt": (".Mg2-connext",),
    "Piano": (".tp-modal", ".tp-backdrop"),
}

#: The vendors in `ACCESS_WALLS` with a box shown on the page.
_ACCESS_WALLS = r"""walls => walls.filter(([vendor, selectors]) =>
    selectors.some(selector => [...document.querySelectorAll(selector)].some(element => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && element.checkVisibility({
            checkOpacity: true, checkVisibilityCSS: true,
            opacityProperty: true, visibilityProperty: true,
        });
    }))).map(([vendor]) => vendor)"""


async def access_walls(page: Any) -> tuple[str, ...]:
    """The access walls shown on the page, by vendor.

    Read for its wording only: a page that cannot be read names no wall, and
    the share of the text the PDF holds still says what was lost.
    """
    try:
        found = await page.evaluate(
            _ACCESS_WALLS,
            [[vendor, list(selectors)] for vendor, selectors in ACCESS_WALLS.items()],
        )
    except PlaywrightError as exc:
        LOGGER.debug("Reading the page for access walls failed: %s", exc)
        return ()
    return tuple(str(vendor) for vendor in found)


#: The words a page names its wall's place with, in a class name, matched as
#: whole words of it as clean mode's `NOISE_CLASS_WORDS` are. A business
#: magazine marks where its "halfway hardwall" goes with an empty element,
#: `halfway-hardwall-insertion-point`, and its own stylesheet hides every
#: section after the one holding it; an archived copy printed the opening,
#: four pages that ended at the site's footer, with no warning (docs/09
#: P132-7). The others are the same thing's other names.
WALL_MARKER_WORDS: tuple[str, ...] = ("hardwall", "paywall", "regwall")

#: The least text the page must hide after its wall marker to be reported:
#: `collapsed.MIN_CHARACTERS`, about thirty words.
WALLED_CHARACTERS = 200

#: The first element named as a wall that the page hides more text after,
#: in the article or main region around it, than it shows before it: no box,
#: outside the page's header, navigation, footer, asides and dialogs, and
#: outside what the capture hid itself. `{marker, hidden, shown}`, counted in letters and
#: digits, or null.
_HIDDEN_AFTER_WALL = (
    r"""({words, minimum}) => {"""
    + CLASS_WORD_JS
    + r"""
    const wanted = new Set(words);
    const unrendered = 'script, style, noscript, template';
    // A dialog the page keeps closed (a sign-up form, a cookie policy) is not
    // the article either.
    const chrome = 'header, nav, footer, aside, dialog, [role="banner"], [role="navigation"], '
        + '[role="contentinfo"], [role="complementary"], [role="dialog"], '
        + '[role="alertdialog"], [aria-modal="true"]';
    const marked = element => element.closest('[data-webshot-hidden="true"]') !== null;
    const letters = (root, visible) => {
        let count = 0;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const parent = node.parentElement;
            if (!parent || parent.closest(unrendered)) continue;
            if (visible && !visible(node, parent)) continue;
            count += node.data.replace(/[^\p{L}\p{N}]/gu, '').length;
        }
        return count;
    };
    const named = element => {
        const cls = (element.getAttribute('class') || '').trim().split(/\s+/)[0];
        return element.localName + (element.id ? `#${element.id}` : cls ? `.${cls}` : '');
    };
    for (const marker of document.querySelectorAll('[class]')) {
        if (!classWord(marker, wanted) || marked(marker) || marker.closest(chrome)) continue;
        const region = marker.closest('article, main, [role="main"], [data-webshot-root="true"]')
            || document.body;
        let hidden = 0;
        for (let node = marker; node && node !== region; node = node.parentElement) {
            for (let next = node.nextElementSibling; next; next = next.nextElementSibling) {
                if (next.matches(unrendered) || next.matches(chrome) || marked(next)) continue;
                if (next.getClientRects().length > 0) continue;
                hidden += letters(next, null);
            }
        }
        if (hidden < minimum) continue;
        const shown = letters(region, (node, parent) =>
            (marker.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) !== 0
            && !parent.closest(chrome)
            && parent.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
        if (hidden > shown) return {marker: named(marker), hidden, shown};
    }
    return null;
}"""
)


@dataclass(frozen=True, slots=True)
class HiddenAfterWall:
    """Text the page hides after an element it names as its wall."""

    #: The element, as `element#id` or `element.class`.
    marker: str
    #: Letters and digits hidden after it, and shown before it.
    hidden: int
    shown: int


async def hidden_after_wall(page: Any) -> HiddenAfterWall | None:
    """Whether the page hides most of its article after its wall's marker.

    Detection only: the text is in the document and the page's own CSS hides
    it, and nothing is shown that the page does not show (docs/11). A page
    that cannot be read names nothing; the printed share and the wall's words
    still say what they measure.
    """
    try:
        found = await page.evaluate(
            _HIDDEN_AFTER_WALL,
            {"words": list(WALL_MARKER_WORDS), "minimum": WALLED_CHARACTERS},
        )
    except PlaywrightError as exc:
        LOGGER.debug("Reading the page for text hidden after a wall failed: %s", exc)
        return None
    if not found:
        return None
    return HiddenAfterWall(
        marker=str(found["marker"]),
        hidden=int(found["hidden"]),
        shown=int(found["shown"]),
    )


def _archived(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "web.archive.org"


def hidden_after_wall_message(found: HiddenAfterWall, source: str) -> str:
    """The manifest warning for `hidden_after_wall` (docs/09 P132-7)."""
    archived = (
        " This is a web archive's copy: a snapshot saved before the site added "
        "its wall may hold the whole article."
        if _archived(source)
        else ""
    )
    return (
        f"The page hides {found.hidden:,} letters and digits of text after an "
        f"element it names as a wall ({found.marker}), more than the "
        f"{found.shown:,} it shows before it, so the PDF and the bundle hold only "
        "what comes before the wall: the article's opening. WebShot does not "
        "show what the page hides. The site may show the rest only to readers "
        "who sign in or subscribe: open the page in your own browser, signed in "
        f"if you have an account, and save it from there.{archived}"
    )


def bot_check_page_message(source: str, check: str, *, firewall: bool = False) -> str:
    """What exit 4 says when a bot check's page was shown in place of the page.

    A firewall's page says only that the request was rejected, not why, so
    the message claims no more than that: a rule may refuse the request for
    something other than the browser (docs/09 P120-1).
    """
    if firewall:
        return (
            f"{source} showed {check} in place of the page: the site's firewall "
            "rejected the request. This is not a sign-in page. If the page opens "
            "in your own browser, the firewall refused this automated one. "
            f"{SAVE_IT_YOURSELF}"
        )
    return (
        f"{source} showed {check} in place of the page: a bot check refused "
        f"an automated browser. This is not a sign-in page. {SAVE_IT_YOURSELF}"
    )


def automation_refusal(headers: Mapping[str, str]) -> str | None:
    """Which bot check answered instead of the site, read off the response.

    Each marker was read off a live 403 that an ordinary browser, signed in to
    nothing, was not shown (docs/09 P23-3): Cloudflare's challenge says so in
    `cf-mitigated`, and DataDome answers as the `server`. Only a marker that
    names the check counts. A bare 403 from a CDN can be anything, and a
    keyword in the page's text would refuse a real page that mentions one
    (docs/09 P22). None when no marker is present.
    """
    lowered = {name.lower(): value.strip().lower() for name, value in headers.items()}
    if lowered.get("cf-mitigated") == "challenge":
        return "Cloudflare's bot challenge (cf-mitigated: challenge)"
    if lowered.get("server") == "datadome":
        return "DataDome's bot protection (server: DataDome)"
    return None


def refused_status_message(source: str, status: int, headers: Mapping[str, str]) -> str:
    """What exit 4 says about a 401 or 403, and what to do about it.

    A 403 was always explained as a missing sign-in, and four public pages
    that open in any browser without one were refused by a bot check (docs/09
    P23-3), so the advice sent the person the wrong way.
    """
    refused = f"The server returned HTTP {status} for {source}."
    check = automation_refusal(headers)
    if check:
        return (
            f"{refused} The response came from {check}, which refused an "
            "automated browser: this is a bot check, not a sign-in page. "
            f"{SAVE_IT_YOURSELF}"
        )
    hint = "Use --allow-http-errors only if that page is intentional."
    if status == 401:
        return (
            f"{refused} {hint} A capture of a signed-in page needs "
            "--storage-state or --auth-profile."
        )
    return (
        f"{refused} {hint} If the page needs a sign-in, a capture of it needs "
        "--storage-state or --auth-profile. If it opens without one in your own "
        "browser, the site refused an automated browser instead: save the page "
        "from that browser and capture the saved file."
    )


#: What a page's title or first `<h1>` says when the page asked for does not
#: exist. A news site's missing page came back as a 403, because a storage
#: bucket behind its CDN refuses a key it does not hold, and its body was the
#: site's own "404 / Page Not Found" page; WebShot said a sign-in or a bot
#: check refused the browser (docs/09 P132-5). Read only on that path, a
#: refused status, so a page about a 404 is never asked.
_NOT_FOUND = re.compile(
    r"\b404\b|\bnot found\b"
    r"|\b(?:can['\u2019]?t|cannot|could(?: not|n['\u2019]?t)|unable to) find\b"
    r"|\b(?:does(?: not|n['\u2019]?t)|no longer) exists?\b",
    re.IGNORECASE,
)

#: The page's title and the text of its first `<h1>` that has any.
_TITLE_AND_HEADING = r"""() => {
    const tidy = text => (text || '').replace(/\s+/g, ' ').trim();
    const heading = [...document.querySelectorAll('h1')]
        .map(element => tidy(element.innerText || element.textContent)).find(Boolean) || '';
    return [tidy(document.title), heading];
}"""


async def not_found_page(page: Any) -> str | None:
    """What the page on screen says that tells the page asked for is not
    there, its title or its first `<h1>`, or None when neither says so or the
    page could not be read."""
    try:
        said = await page.evaluate(_TITLE_AND_HEADING)
    except PlaywrightError as exc:
        LOGGER.debug("Reading a refused page's title failed: %s", exc)
        return None
    for text in said:
        if isinstance(text, str) and text and _NOT_FOUND.search(text):
            return text[:120]
    return None


def not_found_status_message(source: str, status: int, said: str) -> str:
    """What exit 3 says about a refused status whose page says the page is
    not there (`not_found_page`)."""
    return (
        f"The server returned HTTP {status} for {source}, and the page it sent "
        f'reads "{said}": the site says the page is not there, so the link is '
        "most likely dead or the page has moved, rather than one that needs a "
        "sign-in. Use --allow-http-errors only if that page is intentional; a "
        "web archive may hold a copy of the page."
    )


def empty_capture_message(
    *,
    page_text_characters: int,
    visuals: int,
    videos: int,
    required: bool,
    waited_s: float | None = None,
    cleaned: str | None = None,
) -> str | None:
    """What an empty capture says: a warning, or under `required` the failure.

    None when the capture has content. `page_text_characters` is the text of the
    bundle's `content.txt` without the newline the file ends with. It is not
    the manifest's `content.text_characters`, which counts that newline and so
    is 1 for a page with no text. `visuals` counts every visual the page may
    show: the saved assets (`content.visual_assets`) and those past
    `--max-assets` or whose capture failed, so "no image" is said only of a
    page that showed none. `videos` is the manifest's video records. A page of
    pictures and no text is not empty, and neither is a page whose only
    content is a video. `waited_s` is the time the capture gave a page that
    showed no text for its script to render some (`wait_for_rendered_text`)
    when that wait ran out with none, or None; it is said so the reader does
    not wait as long again by hand. `cleaned` is a sentence saying what
    clean mode hid that showed text, or None: a rule that took the page's
    content for chrome leaves the capture empty too (docs/09 P132-1).
    """
    if visuals or videos or page_text_characters >= NEAR_EMPTY_CHARACTERS:
        return None
    held = (
        "no text"
        if page_text_characters == 0
        else f"only {page_text_characters} character"
        f"{'s' if page_text_characters != 1 else ''} of text"
    )
    consequence = (
        "--require-content was given, so nothing was published."
        if required
        else "--require-content makes this a failure (exit 5)."
    )
    waited = (
        f" WebShot gave its script {waited_s:g} s more to render text after the "
        "page loaded."
        if waited_s is not None
        else ""
    )
    hid = f" {cleaned}" if cleaned else ""
    return (
        f"The capture is {'empty' if page_text_characters == 0 else 'nearly empty'}: "
        f"content.txt holds {held}, and there is no image and no video.{waited}{hid} "
        "A page shows this when the browser had no session, when its script had "
        "not rendered the content yet, or when --selector matched an empty "
        "element. --storage-state or --auth-profile supplies a session, and "
        f"--wait-for waits for an element. {consequence}"
    )
