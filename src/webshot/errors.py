"""WebShot's error taxonomy.

docs/04-spec.md §3 gives every failure its own exit code so a script can tell a
navigation timeout from a bad flag.  v2.2 exited 1 for everything; the phases
migrated paths onto their real codes one at a time, and Phase 5.1 finished the
job — **v3 never exits 1**.  Every class below carries the code the spec table
assigns it, and `classify` covers the exceptions WebShot did not raise itself.

The two boundaries worth stating, because every reclassification decision came
down to one of them (docs/09 P5-1):

- **2 against 4.** 2 is "the arguments could not be used as given" — a missing
  file, an impossible combination, a value out of range, all decided before
  anything runs.  4 is "the site refused, or the auth material was rejected" —
  a decision only the far end can make.
- **3 against 5.** 3 is "the page never became available to capture".  5 is
  "the capture ran and what came out cannot be trusted".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; config imports this module
    from .config import CaptureResult


class WebShotError(RuntimeError):
    """A user-actionable capture failure.

    Never raised directly — every failure names its kind, which is what
    `tests/test_exit_codes.py::test_no_bare_webshot_error_is_raised` enforces.
    The default is the honest catch-all for "the capture ran and its result
    cannot be trusted" rather than v2.2's 1, so a path someone forgets to
    classify still lands inside the taxonomy instead of outside it.
    """

    exit_code = 5

    def __init__(self, *args: object, result: CaptureResult | None = None) -> None:
        super().__init__(*args)
        #: The partial capture this failure interrupted, when there is one.
        #: `--validate-pdf=strict` is the case that matters: the file was
        #: published and veraPDF did run, so its QA report can carry the
        #: findings that explain the exit code rather than only the message.
        self.result = result


class UsageError(WebShotError):
    """The request itself is impossible: a bad flag, or an unsupported combination."""

    exit_code = 2


class NavigationError(WebShotError):
    """The page never became available: a timeout, a dead host, a refused status."""

    exit_code = 3


class AuthenticationError(WebShotError):
    """The far end wants credentials, or the ones supplied were not accepted."""

    exit_code = 4


class CaptureIntegrityError(WebShotError):
    """The capture completed and the result does not add up (spec §2.2)."""

    exit_code = 5


class OcrUnavailableError(WebShotError):
    """`--require-ocr` was given and no recognizer can run here."""

    exit_code = 6


class PdfValidationError(WebShotError):
    """A PDF could not be produced, or failed a gate the caller made strict."""

    exit_code = 7


class BundleBuildError(WebShotError):
    """The AI bundle could not be built or published (made real in Phase 2)."""

    exit_code = 8


class EnvironmentFailure(WebShotError):
    """A tool WebShot needs is missing or unusable; `webshot doctor` explains it."""

    exit_code = 9


#: The published taxonomy, spelled once. `docs/04-spec.md` §3, the CLI's
#: epilogue, the docs site's exit-code table and `tests/test_exit_codes.py` all
#: read this, so a code cannot be documented in one place and meant in another.
EXIT_CODES: dict[int, str] = {
    0: "success",
    2: "usage or configuration error",
    3: "navigation or readiness failure",
    4: "authentication required, or the auth material was rejected",
    5: "capture integrity failure",
    6: "OCR required but unavailable",
    7: "PDF render or validation failure",
    8: "bundle build or publication failure",
    9: "environment failure (webshot doctor explains it)",
}


def is_unreachable(exc: BaseException) -> bool:
    """Whether a Playwright error means the page was never reached.

    Chromium's own network failures all carry this prefix —
    `ERR_NAME_NOT_RESOLVED`, `ERR_CONNECTION_REFUSED`, `ERR_CERT_*`,
    `ERR_UNSAFE_PORT`. Two callers need the same answer for different reasons:
    the pipeline, to decide whether it can say something better than the raw
    message, and `classify`, to decide the code. One test, in one place,
    because two copies of a string comparison are two things to get wrong.
    """
    from playwright.async_api import Error as PlaywrightError

    return isinstance(exc, PlaywrightError) and "net::" in str(exc)


def certificate_hint(detail: str) -> str:
    """One line for a certificate the browser refused, or nothing.

    `net::ERR_CERT_*` on a site that opens fine in curl is the usual sign of a
    network that inspects TLS through a proxy whose certificate authority was
    installed where curl looks and not where Chromium does. The raw Playwright
    error names only the code, which sends a user to look at the site rather
    than at the machine. It stays a "may": WebShot cannot see the proxy, and an
    expired or misissued certificate on the site raises the same family.
    """
    if "net::ERR_CERT_" not in detail:
        return ""
    return (
        "\nThis network may inspect TLS through a proxy whose certificate "
        "authority the browser does not trust (Chromium reads the NSS store on "
        "Linux, and the system store on macOS and Windows), so curl can succeed "
        "where the browser fails."
    )


def classify(exc: BaseException) -> int:
    """The spec §3 code for an exception WebShot did not raise itself.

    The pipeline translates what it can at the point where the meaning is
    known — a `goto` that times out is a navigation failure and nothing else.
    This is the backstop for everything that escapes anyway, and its whole
    contract is that it never returns 1: an unclassified failure is still a
    failed capture, so it lands on 5 with the rest of them.
    """
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    if isinstance(exc, WebShotError):
        return exc.exit_code
    if isinstance(exc, PlaywrightTimeoutError) or is_unreachable(exc):
        return NavigationError.exit_code
    if isinstance(exc, PlaywrightError):
        # A Playwright error that is not a network one is a capture that went
        # wrong rather than a page that was never reached.
        return CaptureIntegrityError.exit_code
    if isinstance(exc, ImportError):
        # Reachable only since the CLI began deferring its heavy imports
        # (docs/09 P10-6): a dependency that is missing, or installed and
        # unloadable, now surfaces when the stage that needs it runs rather
        # than when `webshot.cli` is imported. That is the better moment — but
        # before this branch it landed on 5, and before the deferral it was not
        # a WebShot exit code at all, just a traceback and Python's own 1,
        # which spec §3 forbids. 9 is the code that names the machine as the
        # problem and points at the command that explains it.
        return EnvironmentFailure.exit_code
    if isinstance(exc, TimeoutError | ConnectionError):
        # Both are `OSError` subclasses, so they have to be answered before it:
        # in 3.11+ `TimeoutError` *is* `asyncio.TimeoutError` and `socket.timeout`,
        # and a timed-out wait or a reset connection is the page failing to
        # arrive — not something `webshot doctor` can explain (docs/09 P5-10).
        return NavigationError.exit_code
    if isinstance(exc, OSError):
        # What is left is the filesystem: a path that cannot be written, a
        # device that is gone. Writing where the caller pointed is one of the
        # things `webshot doctor` checks, which is what makes 9 the actionable
        # answer rather than a generic failure.
        return EnvironmentFailure.exit_code
    return CaptureIntegrityError.exit_code
