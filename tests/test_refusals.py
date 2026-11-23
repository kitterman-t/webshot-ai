"""A refused status says who refused, and the report records it (docs/09 P23-3).

Four public pages that open in any browser without a sign-in answered a
headless one with 403, and WebShot said a capture of a signed-in page needs a
session. Two bot checks name themselves in the response, and those headers are
what the loopback server sends here, as read off the live 403s: Cloudflare's
challenge in `cf-mitigated`, DataDome as the `server`. A bare 403 cannot be
told from a sign-in refusal, so its message gives both readings.

The failure report also said `http_status: null` and `final_url: null` for a
status it had read.

A 403 that names nothing in its headers can still say what it is in its page:
a news site's missing page came back as a 403 whose body was the site's own
"404" page, and was called a sign-in or a bot check (docs/09 P132-5); a
firewall's block page served with 403 is named as the same page served with
200 is (P132-3).
"""

from __future__ import annotations

import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import serve
from webshot.capture.landing import automation_refusal, refused_status_message
from webshot.cli import main

#: path -> (status, headers, body)
ROUTES: dict[str, tuple[int, dict[str, str], str]] = {
    "/challenge": (
        403,
        {"cf-mitigated": "challenge", "cf-ray": "0000000000000000-LHR"},
        "<title>Just a moment...</title><p>One more step</p>",
    ),
    "/datadome": (403, {"Server": "DataDome", "x-datadome": "protected"}, ""),
    "/forbidden": (403, {}, "<title>403 Forbidden</title><h1>Forbidden</h1>"),
    "/gone": (404, {}, "<title>Not found</title>"),
    # As a storage bucket behind a CDN answers a key it does not hold, with
    # the site's own error page as the body: no title, an <h1> of "404".
    "/missing": (
        403,
        {"x-cache": "Error from cloudfront"},
        "<header><p>Harbor Weekly</p></header><main><h1>404</h1>"
        "<h2>Page Not Found</h2><p>We can't find the page you're looking for.</p>"
        "</main>",
    ),
    "/missing-titled": (
        403,
        {},
        "<title>Page not found | Harbor Weekly</title><p>Sorry.</p>",
    ),
    "/incapsula-block": (
        403,
        {"x-iinfo": "0-00000000-0 0NNN RT(0 0) q(0 -1 -1 -1) r(0 -1)"},
        '<html style="height:100%"><body style="margin:0;height:100%"><iframe '
        'src="/_Incapsula_Resource?CWUDNSAI=9&amp;incident_id=0000-1111" '
        'width="100%" height="100%">Request unsuccessful. Incapsula incident ID: '
        "0000-1111</iframe></body></html>",
    ),
}


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class _Refuses(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = ROUTES.get(self.path, (404, {}, ""))
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in headers.items():
                if name != "Server":
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def version_string(self) -> str:
            # `send_response` writes `Server` itself; a route that names one
            # answers as that server, and no other route names any.
            return ROUTES.get(self.path, (404, {}, ""))[1].get("Server", "loopback")

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Refuses) as origin:
        yield origin


def _run(tmp_path: Path, source: str) -> tuple[int, dict[str, Any]]:
    report = tmp_path / "report.json"
    code = main(
        [
            source,
            "--output",
            str(tmp_path / "capture.pdf"),
            "--no-ocr",
            "--report",
            str(report),
        ]
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_only_a_marker_that_names_the_check_counts() -> None:
    assert automation_refusal({"cf-mitigated": "challenge"}) is not None
    assert automation_refusal({"CF-Mitigated": " Challenge "}) is not None
    assert automation_refusal({"server": "DataDome"}) is not None
    # Only DataDome answering as the server is matched; `x-datadome` was never
    # seen without it, and is not evidence on its own.
    assert automation_refusal({"server": "nginx", "x-datadome": "protected"}) is None
    assert automation_refusal({"server": "cloudflare", "cf-ray": "1-LHR"}) is None
    assert automation_refusal({}) is None


def test_a_401_still_means_a_sign_in() -> None:
    message = refused_status_message("https://site.test/a", 401, {})
    assert "--storage-state or --auth-profile" in message
    assert "automated browser" not in message


@pytest.mark.browser
@pytest.mark.parametrize(
    ("path", "names"),
    [
        ("/challenge", "Cloudflare's bot challenge (cf-mitigated: challenge)"),
        ("/datadome", "DataDome's bot protection (server: DataDome)"),
    ],
)
def test_a_bot_check_is_named_and_not_called_a_sign_in(
    tmp_path: Path, site: str, path: str, names: str
) -> None:
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 4 and report["exit_code"] == 4
    error = str(report["error"])
    assert names in error and "not a sign-in page" in error
    assert "--storage-state" not in error and "--auth-profile" not in error
    assert report["http_status"] == 403
    assert report["final_url"] == f"{site}{path}"


@pytest.mark.browser
def test_a_bare_403_gives_both_readings(tmp_path: Path, site: str) -> None:
    code, report = _run(tmp_path, f"{site}/forbidden")
    assert code == 4
    error = str(report["error"])
    assert "--storage-state or --auth-profile" in error
    assert "refused an automated browser" in error
    assert report["http_status"] == 403 and report["final_url"] == f"{site}/forbidden"


@pytest.mark.browser
def test_any_refused_status_is_in_the_report(tmp_path: Path, site: str) -> None:
    code, report = _run(tmp_path, f"{site}/gone")
    assert code == 3
    assert report["http_status"] == 404 and report["final_url"] == f"{site}/gone"


@pytest.mark.browser
@pytest.mark.parametrize(
    ("path", "said"),
    [("/missing", "404"), ("/missing-titled", "Page not found | Harbor Weekly")],
)
def test_a_403_whose_page_says_not_found_is_a_dead_link(
    tmp_path: Path, site: str, path: str, said: str
) -> None:
    """Exit 3, as a 404 is: the page never became available to capture, and
    no sign-in would make it (docs/04-spec.md §3)."""
    code, report = _run(tmp_path, f"{site}{path}")
    assert code == 3 and report["exit_code"] == 3
    error = str(report["error"])
    assert f'reads "{said}": the site says the page is not there' in error
    assert "--storage-state" not in error and "bot check" not in error
    assert report["http_status"] == 403 and report["final_url"] == f"{site}{path}"


@pytest.mark.browser
def test_a_403_whose_page_is_a_firewall_block_page_names_it(
    tmp_path: Path, site: str
) -> None:
    code, report = _run(tmp_path, f"{site}/incapsula-block")
    assert code == 4
    error = str(report["error"])
    assert "the block page of Imperva's application firewall, Incapsula" in error
    assert "incident ID 0000-1111" in error and "not a sign-in page" in error
    assert "--storage-state" not in error
