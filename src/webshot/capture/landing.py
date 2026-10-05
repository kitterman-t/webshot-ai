"""Whether the browser landed on the page that was asked for.

Two ways a capture can run to the end and still not be of the page, and both
used to exit 0 with no warning (docs/09 P14-1, P22):

- **a sign-in wall.** The site sent the browser to a sign-in page on another
  origin, because it had no session. docs/04-spec.md §5 item 4 makes this
  exit 4, and before P22 only a 401 or 403 did.
- **an empty capture.** The page rendered nothing a reader could use: an app
  shell whose script never filled it, a document served to a browser with no
  session, a `--selector` that matched an empty element. §5 item 14 makes this
  a manifest warning, and `--require-content` a failure.

Detection only. Nothing here signs in, retries, waits longer or changes what
the page is sent; a wall is reported to the person who can sign in, which is
the line docs/11 draws around access controls.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

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
