"""A page that enforces Trusted Types is captured (docs/09 P23-1).

`require-trusted-types-for 'script'` makes every string handed to an HTML
parser throw unless a policy made it. The snapshot built its inert working
document with `DOMParser.parseFromString`, so on a page with that policy the
bundle stage threw and the whole capture exited 5. The fixture holds the
policy in a `<meta>`; the loopback server sends it as a header, the way a
site does, with the `<meta>` taken out so the header alone is the policy.
"""

from __future__ import annotations

import http.server
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import serve
from webshot.cli import main

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trusted_types_page.html"
POLICY = "require-trusted-types-for 'script'"


@pytest.fixture(scope="module")
def header_policy() -> Iterator[str]:
    body = FIXTURE.read_text(encoding="utf-8")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">\n'
    assert meta in body
    page = body.replace(meta, "").encode("utf-8")

    class _Policy(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Policy) as origin:
        yield f"{origin}/watch"


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


@pytest.mark.browser
@pytest.mark.parametrize("where", ["meta", "header"])
def test_a_page_that_enforces_trusted_types_is_captured(
    tmp_path: Path, header_policy: str, where: str
) -> None:
    source = str(FIXTURE) if where == "meta" else header_policy
    code, report = _run(tmp_path, source)
    assert code == 0, report["error"]
    assert report["warnings"] == []
    text = (tmp_path / "capture.ai" / "content.md").read_text(encoding="utf-8")
    for line in (
        "# Measuring river flow",
        "The rating curve turns each level into a discharge.",
        "Readings after a flood are checked against a current meter.",
    ):
        assert line in text
