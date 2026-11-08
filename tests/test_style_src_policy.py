"""A page whose policy refuses inline style still prints with WebShot's CSS (docs/09 P116).

`Content-Security-Policy: style-src 'self'`, with no `'unsafe-inline'`,
refuses every `<style>` element. WebShot gave the page its print CSS, the
clean mode's and `--css` as `<style>` elements through Playwright's
`add_style_tag`, which raised on the refusal, so a capture of such a page
exited 5 before anything printed (P115-4). They are adopted constructed
sheets now, which that policy does not govern. The loopback server sends the
policy as a header, the way a site does, on the page and on its own sheet.

The fixture shows each part working: the page's inline `<style>` is refused
(its line prints), its own linked sheet applies (its line does not), the
print CSS applies (a `<pre>` line far wider than the paper wraps, so its end
prints rather than being cut off at the edge) and so does `--css` (its
line does not print).
"""

from __future__ import annotations

import asyncio
import http.server
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from conftest import serve
from webshot.capture.prepare import BASE_PRINT_CSS, adopt_stylesheet, css_imports
from webshot.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
POLICY = "style-src 'self'"
PAGE = FIXTURES / "style_src_self.html"
SHEET = FIXTURES / "style_src_self.css"
EXTRA_CSS = ".extra-css-rule { display: none; }\n"


@pytest.fixture(scope="module")
def origin() -> Iterator[str]:
    files = {
        "/page.html": (PAGE.read_bytes(), "text/html; charset=utf-8"),
        "/style_src_self.css": (SHEET.read_bytes(), "text/css; charset=utf-8"),
    }

    class _Policy(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            found = files.get(self.path)
            if found is None:
                self.send_error(404)
                return
            body, kind = found
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    with serve(_Policy) as served:
        yield served


def _capture(
    tmp_path: Path, source: str, *args: str
) -> tuple[int, dict[str, Any], str]:
    report = tmp_path / "report.json"
    output = tmp_path / "capture.pdf"
    code = main(
        [source, "--output", str(output), "--no-ocr", "--report", str(report), *args]
    )
    reader = PdfReader(io.BytesIO(output.read_bytes())) if output.exists() else None
    text = "\n".join(page.extract_text() for page in reader.pages) if reader else ""
    return code, json.loads(report.read_text(encoding="utf-8")), text


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_a_page_that_refuses_inline_style_prints_with_the_print_css(
    tmp_path: Path, origin: str, mode: str
) -> None:
    css = tmp_path / "extra.css"
    css.write_text(EXTRA_CSS, encoding="utf-8")
    code, report, text = _capture(
        tmp_path, f"{origin}/page.html", "--mode", mode, "--css", str(css)
    )
    assert code == 0, report["error"]
    assert report["warnings"] == []
    # The policy is in force, and the page's own sheet is not.
    assert "The inline style was refused, so this line prints." in text
    assert "The linked sheet applied" not in text
    # The print CSS wraps the long line, breaking it anywhere; without it the
    # line ran past the paper's edge and its end was cut off.
    assert "/1.76/seasonend" in "".join(text.split())
    assert "The command line's stylesheet hides this line." not in text
    bundle = (tmp_path / "capture.ai" / "content.md").read_text(encoding="utf-8")
    assert "The gauge board was repainted in the spring." in bundle


@pytest.mark.browser
def test_an_import_in_css_that_was_not_applied_is_a_warning(
    tmp_path: Path, origin: str
) -> None:
    css = tmp_path / "extra.css"
    css.write_text('@import url("print-extra.css");\n' + EXTRA_CSS, encoding="utf-8")
    code, report, text = _capture(tmp_path, f"{origin}/page.html", "--css", str(css))
    assert code == 0, report["error"]
    assert report["warnings"] == [
        '--css: 1 @import rule(s) were not applied (@import url("print-extra.css")). '
        "WebShot installs --css as an adopted stylesheet, which a page's style-src "
        "policy cannot refuse and which cannot hold @import; copy the imported "
        "rules into the file instead."
    ]
    # The rest of the sheet still applies.
    assert "The command line's stylesheet hides this line." not in text


@pytest.mark.browser
def test_the_print_css_comes_after_every_sheet_of_the_page() -> None:
    """The cascade cost of adopting: a rule in a `<style>` in the page's body,
    which came after the print CSS's `<style>` in the head and won a tie, now
    comes before it. `--css`, adopted last, still has the last word."""

    page_rule = "<style>pre { white-space: pre !important; }</style>"
    html = f"<!doctype html><title>t</title><body>{page_rule}<pre>a b</pre></body>"

    async def run() -> list[str]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html)
                read = (
                    "() => getComputedStyle(document.querySelector('pre')).whiteSpace"
                )
                seen = [await page.evaluate(read)]
                await adopt_stylesheet(page, BASE_PRINT_CSS)
                seen.append(await page.evaluate(read))
                await adopt_stylesheet(page, "pre { white-space: pre !important; }")
                seen.append(await page.evaluate(read))
                return seen
            finally:
                await browser.close()

    assert asyncio.run(run()) == ["pre", "pre-wrap", "pre"]


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        ("", []),
        ('@import url("a.css");', ['url("a.css")']),
        ('@IMPORT "a.css" print;', ['"a.css" print']),
        ('@import "a.css"', ['"a.css"']),
        (
            '@charset "utf-8";\n@import "a.css" screen;\n@import url(b.css) layer(base);',
            ['"a.css" screen', "url(b.css) layer(base)"],
        ),
        ('/* a note */ <!-- @import "a.css"; -->', ['"a.css"']),
        ('@layer base, print; @import "a.css";', ['"a.css"']),
        (
            '@import url("a;b.css"); @import url(c;d.css);',
            ['url("a;b.css")', "url(c;d.css)"],
        ),
        ('@import "a\\";b.css";', ['"a\\";b.css"']),
        ('@import\n  url("a.css")\n  print;', ['url("a.css") print']),
        # After a rule, a block or anything else, an import is invalid CSS,
        # which a `<style>` element drops too.
        ('p { color: red } @import "a.css";', []),
        ('@layer base { p { color: red } } @import "a.css";', []),
        ('@media print { @import "a.css"; }', []),
        ('@importer "a.css";', []),
        ('@font-face { font-family: x; } @import "a.css";', []),
    ],
)
def test_css_imports_finds_the_imports_css_honours(
    css: str, expected: list[str]
) -> None:
    assert css_imports(css) == expected
