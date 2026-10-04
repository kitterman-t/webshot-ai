"""Filename, source-normalization, and option-validation behavior."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

from webshot import (
    WebShotError,
    normalize_source,
    resolve_output,
    sanitize_filename,
    validate_css_length,
)
from webshot.errors import UsageError


def test_web_filename_is_readable_and_safe() -> None:
    name = sanitize_filename("https://www.example.com/reports/Q2 results?draft=true")
    assert name == "www.example.com-reports-Q2-results.pdf"


def test_file_filename_uses_stem() -> None:
    assert (
        sanitize_filename("file:///tmp/Quarterly%20Report.html")
        == "Quarterly-Report.pdf"
    )


def test_output_gets_pdf_suffix() -> None:
    output = resolve_output("https://example.com/a", "deliverables/report")
    assert output.name == "report.pdf"


def test_https_is_preserved() -> None:
    assert normalize_source("https://example.com") == "https://example.com"


def test_bare_hostname_gets_https() -> None:
    assert normalize_source("example.com/path") == "https://example.com/path"


def test_localhost_with_port_gets_http() -> None:
    assert normalize_source("localhost:8000/report") == "http://localhost:8000/report"


def test_host_with_port_gets_https() -> None:
    assert (
        normalize_source("example.com:8443/report") == "https://example.com:8443/report"
    )


def test_local_file_becomes_file_url(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text("<h1>Hello</h1>", encoding="utf-8")
    assert normalize_source(str(path)) == path.resolve().as_uri()


def test_unknown_value_is_rejected() -> None:
    with pytest.raises(WebShotError):
        normalize_source("not a valid source")


@pytest.mark.parametrize(
    "value",
    [
        "./typo.csv",
        "../missing.md",
        ".\\typo.csv",
        "~/webshot-no-such-file.csv",
        "/webshot-no-such-directory/report.html",
        "C:\\reports\\q2.docx",
    ],
)
def test_a_missing_path_is_a_usage_error_not_a_web_address(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`./typo.csv` became `https://./typo.csv` and failed as a network error.

    That is exit 3 for a file that is not there, which spec §3 gives code 2.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageError) as raised:
        normalize_source(value)
    assert str(raised.value) == f"No such file: {value}."
    assert raised.value.exit_code == 2


@pytest.mark.parametrize("value", ["missing.csv", "notes.md", "Scan.PNG", "a.zip"])
def test_a_missing_bare_file_name_says_how_to_ask_for_the_web_address(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare name is a file when WebShot reads its suffix; `.md` is also a TLD."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageError) as raised:
        normalize_source(value)
    assert str(raised.value) == (
        f"No such file: {value}. For a web address, write https://{value}"
    )


def test_a_directory_is_not_taken_for_a_web_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pages").mkdir()
    monkeypatch.chdir(tmp_path)
    for value in ("./pages", "."):
        with pytest.raises(UsageError, match="is a directory"):
            normalize_source(value)


def test_a_bare_file_name_that_exists_is_still_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "notes.md").write_text("# Notes\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert normalize_source("notes.md") == (tmp_path / "notes.md").resolve().as_uri()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("example.com", "https://example.com"),
        ("en.wikipedia.org/wiki/Foo", "https://en.wikipedia.org/wiki/Foo"),
        (
            "www.example.com/files/report.csv",
            "https://www.example.com/files/report.csv",
        ),
        ("example.co.uk", "https://example.co.uk"),
        ("127.0.0.1:8000/page.html", "http://127.0.0.1:8000/page.html"),
    ],
)
def test_scheme_less_web_addresses_still_become_urls(
    value: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path check must not take a host for a file, even one ending in a path."""
    monkeypatch.chdir(tmp_path)
    assert normalize_source(value) == expected


def test_css_units() -> None:
    for value in ("0", "12mm", "0.5in", "24px", "1cm"):
        assert validate_css_length(value) == value


def test_invalid_css_unit() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        validate_css_length("1rem")


def test_a_replaced_deliverable_is_announced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Only what is already there, and nothing for a path that is absent."""
    from webshot.bundle.publish import announce_replacements

    pdf, bundle = tmp_path / "report.pdf", tmp_path / "report.ai"
    pdf.write_bytes(b"%PDF-")
    with caplog.at_level(logging.INFO, logger="webshot"):
        announce_replacements(pdf, bundle, None)
    assert caplog.messages == [f"Replacing existing {pdf}"]


@pytest.mark.browser
def test_two_local_files_with_one_stem_say_the_second_replaces_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`report.csv` then `report.json` share `output/pdf/report.pdf`.

    The default name keeps only the stem (the golden corpus pins that slug), so
    the second capture replaced the first one's PDF and bundle without a word.
    """
    from webshot.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.csv").write_text("region,total\nnorth,4\n", encoding="utf-8")
    (tmp_path / "report.json").write_text('{"region": "north"}\n', encoding="utf-8")
    pdf = (tmp_path / "output" / "pdf" / "report.pdf").resolve()
    with caplog.at_level(logging.INFO, logger="webshot"):
        assert main(["report.csv", "--no-ocr"]) == 0
        first = caplog.text
        caplog.clear()
        assert main(["report.json", "--no-ocr"]) == 0
    assert "Replacing existing" not in first, first
    assert f"Replacing existing {pdf}" in caplog.messages, caplog.messages
    assert f"Replacing existing {pdf.with_suffix('.ai')}" in caplog.messages, (
        caplog.messages
    )
