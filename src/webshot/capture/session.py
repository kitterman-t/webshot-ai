"""Browser lifecycle and authentication.

Two authentication shapes, both of which keep credentials out of the output
(docs/04-spec.md §6.1, §6.3): a Playwright storage-state file the caller
already owns, or a persistent browser profile made owner-only — `0700`, or on
Windows an ACL admitting only the user — that never leaves the machine.
Neither is copied into a bundle.

Service workers are blocked and the viewport is fixed, because a capture that
depends on cached workers or on the operator's window size is not reproducible.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    Request,
    Route,
    WebSocketRoute,
)
from playwright.async_api import Error as PlaywrightError

from .. import netpolicy, winacl
from ..config import DEFAULT_VIEWPORT, CaptureOptions
from ..errors import EnvironmentFailure

LOGGER = logging.getLogger("webshot")


async def launch_context(
    playwright: Playwright, options: CaptureOptions
) -> tuple[BrowserContext, Browser | None]:
    """Open the browser context a capture runs in.

    Returns the context and, when one was launched, the browser that owns it —
    a persistent profile *is* its own context, so there is nothing to return.
    """
    browser: Browser | None = None
    try:
        if options.auth_profile:
            prepare_profile(options.auth_profile)
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(options.auth_profile),
                headless=not options.interactive_auth,
                user_agent=options.user_agent,
                viewport=DEFAULT_VIEWPORT,
                device_scale_factor=2,
                service_workers="block",
            )
        else:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=options.user_agent,
                viewport=DEFAULT_VIEWPORT,
                device_scale_factor=2,
                service_workers="block",
                storage_state=str(options.storage_state)
                if options.storage_state
                else None,
            )
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            raise EnvironmentFailure(
                "The Chromium browser is not installed. Run: python -m playwright install chromium"
            ) from exc
        raise
    except Exception:
        if browser:
            await browser.close()
        raise
    context.set_default_timeout(options.operation_timeout_ms)
    context.set_default_navigation_timeout(options.navigation_timeout_ms)
    if options.block_private_requests or options.filesystem_roots:
        await _install_request_policy(context, options)
    return context, browser


def prepare_profile(directory: Path) -> None:
    """Make an authentication profile owner-only before the browser opens it.

    On POSIX that is `0700` on the directory, which also keeps everyone else
    out of what is inside. On Windows `chmod` sets no ACL at all, so the profile
    was as private as its parent directory happened to be (docs/09 P10-24);
    there the ACL is written and every entry checked (docs/09 P10-25).
    """
    if sys.platform == "win32":
        winacl.make_owner_only(directory)
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)


#: One DevTools session per page, opened on first use and never detached.
_DEVTOOLS: WeakKeyDictionary[Page, CDPSession] = WeakKeyDictionary()


async def devtools(page: Page) -> CDPSession:
    """The page's DevTools session, for the questions Playwright cannot ask.

    **Never detached, and that is the point.** Detaching any DevTools session
    resets the page's emulated media type: measured on this build,
    `matchMedia('print')` went from true to false, while the viewport, colour
    scheme and user agent were untouched. Playwright still believes it set
    print, so nothing noticed. The print-edge check after the render then
    measured the screen layout and found nothing cut, and a `--media screen`
    capture would have printed with print media (docs/09 P16-6). The session
    ends with its page. One per page, so a journey's captures share it.
    """
    session = _DEVTOOLS.get(page)
    if session is None:
        session = await page.context.new_cdp_session(page)
        _DEVTOOLS[page] = session
    return session


async def _install_request_policy(
    context: BrowserContext, options: CaptureOptions
) -> None:
    """Refuse every request the page makes that the caller's own source could not.

    Checking only the URL the caller asked for guards the front door and leaves
    the windows open: a page — local or remote — can `fetch()` or iframe
    `http://169.254.169.254/`, and its response lands in the capture the caller
    then reads back. A redirect does the same to the top-level document.
    Filtering at the request level covers the document, its subresources, and
    every navigation it makes (docs/09 P4-13).

    Two rules, because the front door has two locks and the windows only ever
    had one:

    * **Private networks**, under `--block-private-requests`.
    * **Filesystem roots**, whenever the caller set any. The MCP boundary
      confines the *source* to its configured roots and stopped there, so an
      allowed local document could reference `file:///anything` and Chromium
      loaded it — into the PDF and into the asset bundle — because the
      classifier called every non-HTTP scheme safe. Measured on this Playwright
      build: `file:` subresources *are* routed, and an out-of-root image
      rendered (docs/09 P8-11).

    Answers are cached per host for the context's lifetime: a page pulls dozens
    of subresources from a handful of hosts, and each miss is a DNS lookup.
    `file:` has no host to cache on and resolves a path instead, which is a
    stat rather than a lookup.
    """
    verdicts: dict[str, bool] = {}
    roots = options.filesystem_roots

    async def gate(route: Route, request: Request) -> None:
        if roots and netpolicy.is_outside_roots(request.url, roots):
            LOGGER.warning(
                "Blocked a request for a file outside the configured roots: %s",
                request.url,
            )
            await route.abort("blockedbyclient")
            return
        if not options.block_private_requests:
            await route.continue_()
            return
        host = urlparse(request.url).hostname or ""
        if host not in verdicts:
            verdicts[host] = await asyncio.to_thread(
                netpolicy.is_internal_url, request.url
            )
        if verdicts[host]:
            LOGGER.warning(
                "Blocked a request to an internal address during capture: %s",
                request.url,
            )
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    await context.route("**/*", gate)
    if options.block_private_requests:
        await _block_private_websockets(context)


async def _block_private_websockets(context: BrowserContext) -> None:
    """The same rule for the traffic `context.route` does not carry.

    Page routing governs HTTP; a WebSocket handshake is routed separately and
    was therefore ungoverned. A captured public page opening
    `ws://127.0.0.1:PORT` could exchange data with an internal service and copy
    the replies into the DOM, while `final_url` stayed public and the capture
    published — the private-network guardrail intact on paper and absent in
    fact (docs/09 P8-10).

    A handler that never calls `connect_to_server()` is one Playwright does not
    connect for us, so refusing is simply not connecting.
    """

    async def gate(ws: WebSocketRoute) -> None:
        if await asyncio.to_thread(netpolicy.is_internal_url, ws.url):
            LOGGER.warning(
                "Blocked a WebSocket to an internal address during capture: %s",
                ws.url,
            )
            await ws.close(code=1008, reason="blocked by client")
            return
        # Synchronous in the Python API, unlike every other route method: it
        # returns the server-side handle rather than awaiting a round trip.
        ws.connect_to_server()

    await context.route_web_socket("**/*", gate)
