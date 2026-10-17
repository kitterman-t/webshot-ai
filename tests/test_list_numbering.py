"""A list keeps the number it starts at, as the number Chromium shows (docs/09 P30).

`MARKDOWN_POLICY` did not allow `ol[start]`, so a Markdown list that began `3.`
(Python-Markdown's `<ol start="3">`, and markdownify's rendering of an EPUB's
`<ol start="5">`) was numbered from 1 on the page. The snapshot allowed it
through nh3's defaults, holding whatever text the page wrote, and docling reads
that text with `int()`: `start="²"` failed the capture, and `start="٣"` read as
3 where Chromium numbered the list from 1. Both allowlists now keep the
attribute, and only as the integer Chromium reads from it.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from playwright.async_api import async_playwright

from webshot.acquire.adapters import materialize_source, sanitize_markdown_html
from webshot.capture.snapshot import (
    extract_semantic_content,
    sanitize_body_html,
    standalone_document,
)
from webshot.extract.docling_bridge import extract
from webshot.netpolicy import file_url_path
from webshot.sanitize import html_integer

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
Sanitizer = Callable[[str], str]
SANITIZERS: tuple[Sanitizer, ...] = (sanitize_body_html, sanitize_markdown_html)
OL_RE = re.compile(r"<ol\b[^>]*>")
START_RE = re.compile(r'<ol start="([^"]*)">')


# --------------------------------------------------------------------------- #
# The number, as Chromium reads it
# --------------------------------------------------------------------------- #

#: Measured with `ol.start` in Chromium 153 (docs/09 P30). None is a value
#: Chromium reads no number from, so the list starts at its default, 1.
CHROMIUM_READS = [
    ("3", 3),
    ("03", 3),
    ("00000000000000000003", 3),
    ("0", 0),
    ("-0", 0),
    ("-2", -2),
    (" 3", 3),
    ("\t\n3", 3),
    ("3 ", 3),
    ("+3", 3),
    ("3abc", 3),
    ("3.5", 3),
    ("0x10", 0),
    ("2147483647", 2147483647),
    ("2147483648", None),
    ("99999999999", None),
    ("-2147483648", -2147483648),
    ("-2147483649", None),
    ("\u00a03", None),  # a no-break space is not ASCII whitespace
    ("\u20073", None),  # nor is a figure space
    ("- 3", None),
    ("--3", None),
    ("+-3", None),
    ("\u0663", None),  # ARABIC-INDIC DIGIT THREE: `int()` reads 3
    ("\u00b2", None),  # SUPERSCRIPT TWO: `isnumeric()`, but `int()` raises
    ("", None),
    ("x", None),
    # Past Python's 4,300-digit `int()` limit. nh3 swallowed the exception it
    # raised and kept the value as written, for docling to fail on.
    pytest.param("1" * 5000, None, id="5000 ones"),
    pytest.param("0" * 5000 + "7", 7, id="5000 zeros and 7"),
    pytest.param("-" + "0" * 5000 + "2", -2, id="minus, 5000 zeros and 2"),
    pytest.param("0" * 30 + "2147483648", None, id="30 zeros and 2^31"),
]


@pytest.mark.parametrize(("value", "number"), CHROMIUM_READS)
def test_the_number_read_is_chromiums(value: str, number: int | None) -> None:
    assert html_integer(value) == number


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@pytest.mark.parametrize(("value", "number"), CHROMIUM_READS)
def test_both_allowlists_keep_the_start_as_that_number(
    sanitize: Sanitizer, value: str, number: int | None
) -> None:
    cleaned = sanitize(f'<ol start="{html.escape(value)}"><li>x</li></ol>')
    expected = "<ol>" if number is None else f'<ol start="{number}">'
    assert OL_RE.findall(cleaned) == [expected]


#: Any short text, and runs of digits long enough to reach `int()`'s limit.
_start_values = st.one_of(
    st.text(max_size=24),
    st.builds(
        lambda sign, zeros, digits: sign + "0" * zeros + digits * 9,
        st.sampled_from(["", "-", "+"]),
        st.integers(0, 6000),
        st.from_regex(r"[1-9][0-9]{0,700}", fullmatch=True),
    ),
)


@given(_start_values)
@settings(max_examples=200, deadline=None)
def test_the_number_read_is_always_none_or_in_range(value: str) -> None:
    """nh3 keeps an attribute as written when its filter raises, so it must not."""
    number = html_integer(value)
    assert number is None or -(2**31) <= number < 2**31


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@given(_start_values)
@settings(max_examples=200, deadline=None)
def test_a_kept_start_is_only_ever_a_plain_integer(
    sanitize: Sanitizer, value: str
) -> None:
    cleaned = sanitize(f'<ol start="{html.escape(value)}"><li>x</li></ol>')
    kept = START_RE.findall(cleaned)
    for number in kept:
        assert re.fullmatch(r"-?(0|[1-9][0-9]{0,9})", number), number[:40]
        assert -(2**31) <= int(number) < 2**31
    assert sanitize(cleaned) == cleaned


@pytest.mark.parametrize(
    "value", ["²", "1" * 5000], ids=["superscript two", "5000 digits"]
)
def test_a_start_docling_cannot_read_no_longer_fails_extraction(value: str) -> None:
    """The pages that failed: docling's `int()` raised, and the bundle with it."""
    snapshot = standalone_document(
        "Steps",
        "en",
        sanitize_body_html(f'<ol start="{value}"><li>Seal the jars</li></ol>'),
    )
    assert "1. Seal the jars" in extract(snapshot, []).markdown


# --------------------------------------------------------------------------- #
# Local documents
# --------------------------------------------------------------------------- #


def _page(source: Path) -> str:
    materialized = materialize_source(source.resolve().as_uri())
    try:
        return file_url_path(materialized.browser_url).read_text(encoding="utf-8")
    finally:
        materialized.cleanup()


def test_a_markdown_list_keeps_the_number_it_starts_at() -> None:
    assert OL_RE.findall(_page(FIXTURES / "numbered_lists.md")) == [
        '<ol start="3">',
        '<ol start="0">',
        "<ol>",
        '<ol start="7">',  # inside the list above
        '<ol start="12">',  # its author's HTML, `start=" +12th"`
        "<ol>",  # `start="²"`, from which Chromium reads no number
    ]


def test_an_epub_list_keeps_the_number_it_starts_at() -> None:
    assert OL_RE.findall(_page(FIXTURES / "office" / "numbered_book.epub")) == [
        '<ol start="5">',
        '<ol start="0">',
        '<ol start="12">',
        '<ol start="4">',  # inside the list above
        "<ol>",
    ]


# --------------------------------------------------------------------------- #
# Through a browser
# --------------------------------------------------------------------------- #


def _first_markers(markdown: str) -> dict[str, int]:
    """Each `item NN` in content.md, and the number it is listed under."""
    return {
        label: int(number)
        for number, label in re.findall(r"^\s*(-?\d+)\. (item \d+)$", markdown, re.M)
    }


@pytest.mark.browser
def test_the_snapshot_and_content_md_keep_the_number_chromium_shows() -> None:
    fixture = FIXTURES / "list_numbering.html"

    async def run() -> tuple[list[int], str]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture.as_uri())
                shown = await page.evaluate(
                    "() => [...document.querySelectorAll('ol')].map(o => o.start)"
                )
                extracted = await extract_semantic_content(page)
                return list(shown), str(extracted["html"])
            finally:
                await browser.close()

    shown, snapshot = asyncio.run(run())
    kept = [
        1 if match.group(1) is None else int(match.group(1))
        for match in re.finditer(r'<ol(?: start="([^"]*)")?>', snapshot)
    ]
    assert len(shown) == 20
    assert kept == shown

    markers = _first_markers(extract(snapshot, []).markdown)
    # docling reads a start only where `str.isnumeric()` holds, so a negative
    # one is numbered from 1 in content.md though content.html keeps it
    # (docs/09 P30, not changed).
    assert markers == {
        f"item {index:02d}": number if number >= 0 else 1
        for index, number in enumerate(shown, 1)
    }


@pytest.mark.browser
@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            FIXTURES / "numbered_lists.md",
            [
                "3. Count the marsh plots",
                "0. Calibrate the scale",
                "7. Note the wind",
                "12. Seal the jars",
                "1. File the notebook",
            ],
        ),
        (
            FIXTURES / "office" / "numbered_book.epub",
            [
                "5. Count the marsh plots",
                "0. Calibrate the scale",
                "12. Log the tide",
                "4. Note the wind",
                "1. File the notebook",
            ],
        ),
    ],
    ids=["markdown", "epub"],
)
def test_a_captured_document_lists_the_numbers_it_starts_at(
    tmp_path: Path, fixture: Path, expected: list[str]
) -> None:
    pdf = tmp_path / f"{fixture.stem}.pdf"
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            str(fixture),
            "--output",
            str(pdf),
            "--report",
            str(report),
            "--no-ocr",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report.read_text("utf-8"))["failed_requests"] == []
    content = pdf.with_suffix(".ai").joinpath("content.md").read_text("utf-8")
    listed = [line.strip() for line in content.splitlines()]
    for line in expected:
        assert line in listed, content
