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


def _bot_check_name(vendor: str, detail: str) -> str:
    if vendor == "datadome":
        return f"DataDome's bot protection (its challenge frame from {detail})"
    if vendor == "perimeterx":
        return f"HUMAN's bot protection, formerly PerimeterX ({detail})"
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
        return BotCheckPage(check=_bot_check_name(str(vendor), str(detail)))
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


def bot_check_page_message(source: str, check: str) -> str:
    """What exit 4 says when a bot check's page was shown in place of the page."""
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


def empty_capture_message(
    *, page_text_characters: int, visuals: int, videos: int, required: bool
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
    content is a video.
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
    return (
        f"The capture is {'empty' if page_text_characters == 0 else 'nearly empty'}: "
        f"content.txt holds {held}, and there is no image and no video. "
        "A page shows this when the browser had no session, when its script had "
        "not rendered the content yet, or when --selector matched an empty "
        "element. --storage-state or --auth-profile supplies a session, and "
        f"--wait-for waits for an element. {consequence}"
    )
