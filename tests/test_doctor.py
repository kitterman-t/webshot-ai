"""`webshot doctor`'s judgments, tested without needing the tools installed."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from webshot.doctor import (
    CheckResult,
    check_extraction,
    check_local_formats,
    check_output_directory,
    judge_ghostscript,
    judge_tesseract,
    judge_verapdf,
    parse_tesseract_languages,
    parse_version,
    render,
    requested_languages,
    summarize,
)


def test_ghostscript_minor_versions_are_numbers_not_strings() -> None:
    """10.07.1 is 10.7.1: a string comparison would call it newer than 10.7."""
    assert parse_version("10.07.1") == (10, 7, 1)
    assert parse_version("GPL Ghostscript 9.56.1 (2022-04-04)") == (9, 56, 1)
    assert parse_version("no version here") is None


def test_ghostscript_below_10_7_warns_about_the_jpeg_bug() -> None:
    result = judge_ghostscript((10, 6, 0))
    assert result.status == "warn"
    assert "JPEG" in result.detail
    assert result.fix


def test_an_old_ghostscript_says_when_its_version_matters() -> None:
    """Ubuntu 24.04 ships 10.02: the warning must say only --pdfa reaches it.

    And it must not claim 10.02 has the bug: the bug that was measured is in
    10.6, and 10.7 is the release WebShot asks for.
    """
    result = judge_ghostscript((10, 2, 1))
    assert result.status == "warn"
    assert "--pdfa" in result.detail
    assert "10.2.1 is older than 10.7" in result.detail
    assert "bugs in 10.6" in result.detail
    assert "--pdfa" in result.fix


def test_the_ghostscript_fix_does_not_point_at_a_package_that_is_too_old() -> None:
    """`apt-get install ghostscript` gives 10.02 on Ubuntu 24.04, which this
    same check then flags, so following the fix led straight to a warning."""
    for version in (None, (10, 2, 1)):
        fix = judge_ghostscript(version).fix
        assert "apt-get" not in fix, fix
        assert "10.7 or later" in fix, fix


def test_current_ghostscript_passes() -> None:
    assert judge_ghostscript((10, 7, 1)).status == "pass"
    assert judge_ghostscript((11, 0)).status == "pass"


def test_missing_ghostscript_is_optional() -> None:
    """Only --pdfa reaches Ghostscript, so its absence degrades nothing else."""
    result = judge_ghostscript(None)
    assert result.status == "optional"
    assert "--pdfa" in result.detail
    assert summarize([result]) == 0


def test_language_listing_drops_the_header_line() -> None:
    listing = "List of available languages in ...:\neng\nosd\nspa\n"
    assert parse_tesseract_languages(listing) == {"eng", "osd", "spa"}


def test_language_specification_is_split_on_plus() -> None:
    assert requested_languages("eng+spa") == ["eng", "spa"]
    assert requested_languages("eng") == ["eng"]


def test_missing_language_pack_warns_and_names_it() -> None:
    results = judge_tesseract("tesseract 5.5.3", {"eng"}, ["eng", "spa"])
    assert [result.status for result in results] == ["pass", "warn"]
    assert "spa" in results[1].detail
    assert "tesseract-ocr-spa" in results[1].fix


def test_absent_tesseract_degrades_rather_than_blocks() -> None:
    results = judge_tesseract(None, set(), ["eng"])
    assert [result.status for result in results] == ["warn"]


def test_writable_output_directory_passes(tmp_path: Path) -> None:
    assert check_output_directory(tmp_path).status == "pass"


def test_output_directory_that_does_not_exist_yet_is_judged_by_its_parent(
    tmp_path: Path,
) -> None:
    result = check_output_directory(tmp_path / "output" / "pdf")
    assert result.status == "pass"
    assert "would create" in result.detail


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions",
)
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only setup: Windows ignores a directory's mode bits, so "
    "mkdir(mode=0o500) leaves it writable. The check itself writes a probe "
    "file, which answers truthfully there too.",
)
def test_unwritable_output_directory_fails(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        result = check_output_directory(locked / "pdf")
        assert result.status == "fail"
        assert result.fix
    finally:
        locked.chmod(0o700)


def test_file_where_a_directory_is_expected_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    assert check_output_directory(blocker / "pdf").status == "fail"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["pass", "pass"], 0),
        (["pass", "warn"], 0),
        (["pass", "optional"], 0),
        (["optional", "warn"], 0),
        (["warn", "fail"], 9),
        (["optional", "fail"], 9),
    ],
)
def test_only_failures_set_the_environment_exit_code(
    statuses: list[str], expected: int
) -> None:
    results = [
        CheckResult(f"check {index}", status, "")  # type: ignore[arg-type]
        for index, status in enumerate(statuses)
    ]
    assert summarize(results) == expected


def test_report_shows_fixes_for_problems_only() -> None:
    text = render(
        [
            CheckResult("Chromium", "pass", "151.0", "should not appear"),
            CheckResult("Tesseract", "warn", "not installed", "brew install tesseract"),
        ]
    )
    assert "should not appear" not in text
    assert "fix: brew install tesseract" in text
    assert "1 degraded capability" in text


def test_report_counts_read_as_english() -> None:
    """Two warnings used to read "with 2 degraded capability"."""
    warn = CheckResult("Tesseract", "warn", "not installed", "install it")
    fail = CheckResult("Chromium", "fail", "missing", "install it")
    assert "with 2 degraded capabilities." in render([warn, warn])
    assert "1 blocking problem, 2 warnings." in render([fail, warn, warn])
    assert "2 blocking problems, 1 warning." in render([fail, fail, warn])


def test_an_optional_line_is_not_presented_as_a_problem() -> None:
    """No WARN, no "fix:", and the summary still says everything needed is there."""
    text = render(
        [
            CheckResult("Chromium", "pass", "151.0"),
            judge_verapdf(None, docker_daemon=False),
        ]
    )
    assert "WARN" not in text
    assert "OPTIONAL  veraPDF" in text
    assert "fix:" not in text
    assert "to add it: install veraPDF" in text
    assert "Everything WebShot needs is installed." in text
    assert "needed only for the flag on its line: veraPDF." in text


def test_a_report_without_an_optional_line_keeps_its_column() -> None:
    """The status column widens only when an OPTIONAL line needs the room."""
    text = render([CheckResult("Chromium", "pass", "151.0")])
    assert text.startswith("PASS  Chromium  151.0")


def test_installed_verapdf_passes() -> None:
    assert judge_verapdf("veraPDF 1.30.0", docker_daemon=False).status == "pass"


def test_docker_daemon_counts_as_a_validator() -> None:
    result = judge_verapdf(None, docker_daemon=True)
    assert result.status == "pass"
    assert "Docker" in result.detail


def test_docker_binary_without_a_daemon_is_not_a_validator() -> None:
    """The exact state this machine is usually in; it must not read as ready.

    Not ready, and not a warning either: only `--validate-pdf` uses veraPDF, so
    on a clean machine it is optional, and it still never fails `doctor`.
    """
    result = judge_verapdf(None, docker_daemon=False)
    assert result.status == "optional"
    assert "--validate-pdf" in result.detail
    assert result.fix
    assert summarize([result]) == 0


# --------------------------------------------------------------------------- #
# The command has to work in the environment it exists to diagnose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "failure",
    [
        OSError("dlopen failed: incompatible architecture"),
        RuntimeError("upstream initialization failed"),
        PackageNotFoundError("docling-slim"),
    ],
    ids=["extension-load", "upstream-init", "missing-metadata"],
)
def test_a_broken_extraction_stack_is_a_failed_check_not_a_traceback(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except ImportError` caught one of the several ways an install breaks.

    A compiled extension that will not load raises `OSError`; an upstream
    initializer raises `RuntimeError`; and the distribution-version lookup —
    which sat *outside* the guard entirely — raises `PackageNotFoundError`.
    Each propagated out of `doctor` as a traceback and Python's exit 1, from
    the one command whose job is to explain a broken environment and exit 9
    (docs/09 P8-43).
    """

    def explode(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr("importlib.metadata.version", explode)
    result = check_extraction()
    assert result.status == "fail"
    assert type(failure).__name__ in result.detail
    assert result.fix


def test_a_broken_local_format_stack_is_a_failed_check_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter imports were above the `try`, so importing MarkItDown,
    Markdown or defusedxml raised before the guard began."""

    def explode(*_args: object, **_kwargs: object) -> object:
        raise PackageNotFoundError("markitdown")

    monkeypatch.setattr("importlib.metadata.version", explode)
    result = check_local_formats()
    assert result.status == "fail"
    assert "PackageNotFoundError" in result.detail


def test_no_message_tells_a_user_to_pip_install_webshot() -> None:
    """WebShot is not on PyPI, and `webshot` there is an unrelated project.

    Four messages said `pip install 'webshot[...]'`, and two repair hints said
    `pip install --force-reinstall webshot`: followed as written, each would
    install that other project, and one named a `webshot[all]` extra that does
    not exist. Every string in the package is checked rather than the sites
    that were fixed, so a new message cannot bring the advice back. Comments
    are not strings and may still name the command to warn against it.
    """
    import ast
    import re

    package = Path(__file__).resolve().parents[1] / "src" / "webshot"
    advice = re.compile(r"pip install[^\n]*\bwebshot\b")
    found = [
        f"{path.relative_to(package)}:{node.lineno}: {node.value!r}"
        for path in sorted(package.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and advice.search(node.value)
    ]
    assert not found, found
