"""A capture of a page the browser was not shown says so (docs/09 P22).

Two cases, both of which exited 0 with no warning before P22:

- **a sign-in wall** (docs/04-spec.md §5 item 4). The site sends a browser
  with no session to a sign-in page on another origin. That is exit 4, and
  only when both signals hold: the origin changed *and* a password field is
  visible. Each half has a fixture that holds the other half alone.
- **an empty capture** (§5 item 14, docs/09 P14-1). A page that rendered no
  text, no image and no video is a manifest warning, and under
  `--require-content` exit 5 with nothing published. A thin page with one
  sentence, and a page that is only a picture, are not empty.

Two loopback servers stand in for a site and its identity provider: the
different ports make them different origins, and nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

import webshot.capture.landing as landing
from conftest import serve
from webshot.capture.landing import (
    NEAR_EMPTY_CHARACTERS,
    WallCheck,
    crossed_origin,
    empty_capture_message,
    sign_in_wall,
)
from webshot.cli import apply_settings, build_parser, main, options_from_args
from webshot.config import CaptureOptions
from webshot.errors import AuthenticationError, UsageError
from webshot.pipeline import convert_url_to_pdf, ignored_option_warnings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "landing"


class _Files(http.server.SimpleHTTPRequestHandler):
    """The identity provider: serves the fixtures as they are."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def other_origin() -> Iterator[str]:
    with serve(_Files) as origin:
        yield origin


@pytest.fixture(scope="module")
def site(other_origin: str) -> Iterator[str]:
    """A site that redirects every page it is asked for to the other origin.

    `/private` lands on the sign-in page; `/article` lands on an article whose
    header holds a collapsed sign-in form, the way `example.com` moves to
    `www.example.com` for an ordinary link.
    """
    targets = {"/private": "sign_in_wall.html", "/article": "sign_in_hidden.html"}

    class _Redirects(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target = targets.get(self.path)
            if target is None:
                self.send_error(404)
                return
            self.send_response(302)
            self.send_header("Location", f"{other_origin}/{target}")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Redirects) as origin:
        yield origin


def _run(tmp_path: Path, source: str, *argv: str) -> tuple[int, dict[str, Any]]:
    """The real CLI with `--report`, and the report it wrote."""
    report = tmp_path / "report.json"
    code = main(
        [
            source,
            "--output",
            str(tmp_path / "capture.pdf"),
            "--no-ocr",
            *argv,
            "--report",
            str(report),
        ]
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


def _published(tmp_path: Path) -> list[str]:
    """What the run left at its destinations. The report is not a deliverable."""
    return sorted(
        path.name
        for path in tmp_path.iterdir()
        if path.name in ("capture.pdf", "capture.ai")
    )


# --------------------------------------------------------------------------- #
# The rules, without a browser
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("requested", "landed", "crossed"),
    [
        ("https://site.test/a", "https://login.site.test/sso", True),
        ("http://site.test/a", "https://site.test/a", True),
        ("http://127.0.0.1:8000/a", "http://127.0.0.1:8001/a", True),
        ("https://site.test/a", "https://site.test/login?next=/a", False),
        # Spelled differently, the same origin: the default port, and the
        # host's case, which Chromium lowers in `page.url`.
        ("https://Site.TEST:443/a", "https://site.test/b", False),
        # Not an origin a redirect can leave: "could not tell" is not "crossed".
        ("file:///tmp/page.html", "https://site.test/", False),
        ("https://site.test:99999/a", "https://other.test/", False),
    ],
)
def test_an_origin_is_scheme_host_and_port(
    requested: str, landed: str, crossed: bool
) -> None:
    assert crossed_origin(requested, landed) is crossed


def test_the_threshold_is_where_the_warning_stops() -> None:
    below = empty_capture_message(
        page_text_characters=NEAR_EMPTY_CHARACTERS - 1,
        visuals=0,
        videos=0,
        required=False,
    )
    assert below is not None and "nearly empty" in below
    assert f"only {NEAR_EMPTY_CHARACTERS - 1} characters of text" in below
    at = empty_capture_message(
        page_text_characters=NEAR_EMPTY_CHARACTERS,
        visuals=0,
        videos=0,
        required=False,
    )
    assert at is None


def test_a_picture_or_a_video_is_content_without_any_text() -> None:
    for counts in (
        {"visuals": 1, "videos": 0},
        {"visuals": 0, "videos": 1},
    ):
        assert (
            empty_capture_message(page_text_characters=0, required=False, **counts)
            is None
        )


def test_the_failure_says_nothing_was_published_and_the_warning_does_not() -> None:
    warning = empty_capture_message(
        page_text_characters=0, visuals=0, videos=0, required=False
    )
    failure = empty_capture_message(
        page_text_characters=0, visuals=0, videos=0, required=True
    )
    assert warning is not None and failure is not None
    assert "The capture is empty: content.txt holds no text" in warning
    assert "makes this a failure (exit 5)" in warning
    assert "nothing was published" in failure
    assert "nothing was published" not in warning


class _Page:
    """A page whose reads fail a set number of times, and that may move.

    Only what `sign_in_wall` touches: `url`, `evaluate` and
    `wait_for_load_state`. `moves_to` is where a navigation in flight takes
    the page once its document loads.
    """

    def __init__(self, url: str, failures: int, moves_to: str | None = None) -> None:
        self.url = url
        self.failures = failures
        self.moves_to = moves_to
        self.reads = 0

    async def evaluate(self, _script: str) -> bool:
        self.reads += 1
        if self.reads <= self.failures:
            raise PlaywrightError("Execution context was destroyed")
        return True

    async def wait_for_load_state(self, _state: str) -> None:
        if self.moves_to:
            self.url = self.moves_to


def test_a_page_that_cannot_be_read_is_not_reported_as_no_wall() -> None:
    """A read that failed twice is `unread`, never the same answer as "no wall"."""
    page = _Page("https://login.site.test/sso", failures=2)
    check = asyncio.run(sign_in_wall(page, "https://site.test/a"))
    assert check.landed is None
    assert check.unread is not None
    assert "https://login.site.test/sso" in check.unread
    assert "Execution context was destroyed" in check.unread
    assert page.reads == 2


def test_a_read_that_failed_once_is_tried_again_on_the_new_document() -> None:
    page = _Page("https://site.test/a", failures=1)
    # The same origin at first; it is the navigation that moves it away.
    page.url = "https://login.site.test/sso"
    check = asyncio.run(sign_in_wall(page, "https://site.test/a"))
    assert check == WallCheck(landed="https://login.site.test/sso")


def test_a_navigation_back_to_the_origin_ends_the_check_clean() -> None:
    page = _Page(
        "https://login.site.test/sso", failures=1, moves_to="https://site.test/a"
    )
    assert asyncio.run(sign_in_wall(page, "https://site.test/a")) == WallCheck()


def test_require_content_without_a_bundle_is_refused() -> None:
    """The check reads the bundle's counts; without one it could check nothing."""
    with pytest.raises(UsageError, match="contradict"):
        CaptureOptions(
            source="x", output=Path("x.pdf"), require_content=True, ai_bundle=False
        )


def _options(argv: list[str], environ: dict[str, str]) -> CaptureOptions:
    args = build_parser().parse_args(argv)
    apply_settings(args, argv, environ)
    return options_from_args(args)


def test_the_flag_wins_over_a_setting_it_contradicts() -> None:
    """flags > env > file, in both directions of the pair (docs/09 P8-66).

    A setting that contradicts a flag would otherwise make the command line
    unusable, which inverts the precedence rather than following it.
    """
    flag_requires = _options(
        ["https://site.test/", "--require-content"],
        {"WEBSHOT_CAPTURE_AI_BUNDLE": "false"},
    )
    assert flag_requires.require_content and flag_requires.ai_bundle
    flag_declines = _options(
        ["https://site.test/", "--no-ai-bundle"],
        {"WEBSHOT_CAPTURE_REQUIRE_CONTENT": "true"},
    )
    assert not flag_declines.require_content and not flag_declines.ai_bundle
    from_settings = _options(
        ["https://site.test/"], {"WEBSHOT_CAPTURE_REQUIRE_CONTENT": "true"}
    )
    assert from_settings.require_content


def test_the_protected_viewer_says_it_ignores_require_content() -> None:
    warnings = ignored_option_warnings(
        CaptureOptions(
            source="https://tenant.test/doc",
            output=Path("x.pdf"),
            protected_viewer=True,
            require_content=True,
        )
    )
    assert any(w.startswith("--require-content was ignored") for w in warnings)


# --------------------------------------------------------------------------- #
# The sign-in wall
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_a_redirect_to_a_sign_in_page_on_another_origin_exits_four(
    tmp_path: Path, site: str, other_origin: str
) -> None:
    code, report = _run(tmp_path, f"{site}/private")
    assert code == 4
    assert report["exit_code"] == 4
    error = str(report["error"])
    # The guidance names both ways to supply a session, and where it landed.
    assert "--storage-state" in error and "--auth-profile" in error
    assert f"{other_origin}/sign_in_wall.html" in error
    assert _published(tmp_path) == []


@pytest.mark.browser
def test_the_same_sign_in_page_asked_for_directly_is_captured(
    tmp_path: Path, other_origin: str
) -> None:
    """A password field alone is not a wall: someone may mean to capture it."""
    code, report = _run(tmp_path, f"{other_origin}/sign_in_wall.html")
    assert code == 0
    assert report["warnings"] == []
    assert _published(tmp_path) == ["capture.ai", "capture.pdf"]


@pytest.mark.browser
def test_a_redirect_to_a_page_whose_password_fields_are_hidden_is_captured(
    tmp_path: Path, site: str, other_origin: str
) -> None:
    """A changed origin alone is not a wall either, nor is a field nobody sees."""
    code, report = _run(tmp_path, f"{site}/article")
    assert code == 0
    assert report["final_url"] == f"{other_origin}/sign_in_hidden.html"
    assert report["warnings"] == []


@pytest.mark.browser
def test_a_sign_in_check_that_could_not_run_is_a_manifest_warning(
    tmp_path: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page read fails in the browser, and the capture says so."""
    monkeypatch.setattr(
        landing,
        "_VISIBLE_PASSWORD_FIELD",
        "() => { throw new Error('the probe was refused'); }",
    )
    code, report = _run(tmp_path, f"{site}/article")
    assert code == 0
    for warnings in (_manifest(tmp_path)["warnings"], report["warnings"]):
        unread = [w for w in warnings if w.startswith("The sign-in check could not")]
        assert len(unread) == 1, warnings
        assert "the probe was refused" in unread[0]


@pytest.mark.browser
def test_an_expired_session_under_wait_for_is_an_authentication_failure(
    tmp_path: Path, site: str
) -> None:
    """The wait times out on the sign-in page; the code says why, not where.

    Through `convert_url_to_pdf` because the wait's limit is the operation
    timeout, a build constant the command line cannot shorten.
    """
    options = CaptureOptions(
        source=f"{site}/private",
        output=tmp_path / "capture.pdf",
        wait_for_selector="article",
        operation_timeout_ms=2_000,
        ocr=False,
    )
    with pytest.raises(AuthenticationError, match="sign-in page on another site"):
        asyncio.run(convert_url_to_pdf(options))


# --------------------------------------------------------------------------- #
# The empty capture
# --------------------------------------------------------------------------- #


def _manifest(tmp_path: Path) -> dict[str, Any]:
    return dict(
        json.loads((tmp_path / "capture.ai" / "manifest.json").read_text("utf-8"))
    )


@pytest.mark.browser
@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        # An app shell whose script never rendered: what P14-1 captured.
        ("empty_shell.html", "The capture is empty: content.txt holds no text,"),
        # "Loading…", which docling writes with three full stops.
        (
            "loading_shell.html",
            "The capture is nearly empty: content.txt holds only 10 characters",
        ),
    ],
)
def test_an_empty_capture_is_a_warning_in_the_manifest_and_the_report(
    tmp_path: Path, fixture: str, expected: str
) -> None:
    code, report = _run(tmp_path, str(FIXTURES / fixture))
    assert code == 0
    manifest = _manifest(tmp_path)
    for warnings in (manifest["warnings"], report["warnings"]):
        assert [w for w in warnings if w.startswith(expected)], warnings
    assert manifest["content"]["visual_assets"] == 0


@pytest.mark.browser
@pytest.mark.parametrize("fixture", ["thin_notice.html", "image_only.html"])
def test_a_thin_page_and_a_picture_are_not_empty(tmp_path: Path, fixture: str) -> None:
    """One sentence clears the threshold, and a picture is content without text."""
    code, report = _run(tmp_path, str(FIXTURES / fixture), "--require-content")
    assert code == 0
    assert report["warnings"] == []
    assert _manifest(tmp_path)["warnings"] == []
    # The gate was on, and the report says so; a run without it omits the key.
    assert report["options"]["require_content"] is True


@pytest.mark.browser
def test_a_picture_past_max_assets_is_still_content(tmp_path: Path) -> None:
    """A picture the cap left out is still a picture the page shows.

    The bundle holds no asset, so counting saved assets alone said "no image"
    of this page, and `--require-content` refused it.
    """
    code, report = _run(
        tmp_path,
        str(FIXTURES / "image_only.html"),
        "--max-assets",
        "0",
        "--require-content",
    )
    assert code == 0
    manifest = _manifest(tmp_path)
    assert manifest["content"]["visual_assets"] == 0
    assert manifest["warnings"] == [
        "The page shows 1 more visual asset than the limit of 0 (--max-assets); "
        "it was not captured."
    ]
    assert report["warnings"] == manifest["warnings"]


@pytest.mark.browser
def test_require_content_refuses_an_empty_capture_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    code, report = _run(
        tmp_path, str(FIXTURES / "empty_shell.html"), "--require-content"
    )
    assert code == 5
    assert report["exit_code"] == 5
    assert "nothing was published" in str(report["error"])
    assert _published(tmp_path) == []
    # No staging directory or temporary PDF left behind either. The output
    # lock's own files stay by design (`outputlock.py`).
    leftovers = [
        path.name
        for path in tmp_path.iterdir()
        if path.name.startswith(".capture") and not path.name.endswith(".lock")
    ]
    assert leftovers == []
