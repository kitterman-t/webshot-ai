"""A refused status says who refused, and the report records it (docs/09 P23-3).

Four public pages that open in any browser without a sign-in answered a
headless one with 403, and WebShot said a capture of a signed-in page needs a
session. Two bot checks name themselves in the response, and those headers are
what the loopback server sends here, as read off the live 403s: Cloudflare's
challenge in `cf-mitigated`, DataDome as the `server`. A bare 403 cannot be
told from a sign-in refusal, so its message gives both readings.

The failure report also said `http_status: null` and `final_url: null` for a
status it had read.
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
}


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class _Refuses(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = ROUTES[self.path]
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
            return ROUTES[self.path][1].get("Server", "loopback")

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
