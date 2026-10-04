"""`webshot doctor` — diagnose the environment before a capture, not during one.

Every check reports one of four states, and each one that is not PASS
carries a one-line fix:

    PASS      the capability is present and usable
    OPTIONAL  a tool only an opt-in flag uses is absent; nothing degrades
              unless that flag is given, and the line names the flag
    WARN      something is missing or questionable; captures still run,
              but a feature degrades (and says so in the bundle's warnings)
    FAIL      a capture cannot succeed until it is fixed

The probes (subprocess calls, browser launch) are deliberately separated from
the judgments (what a version number means), so the interesting half is
testable without the tools installed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

#: `optional` is not a softer `warn`. A WARN on a clean machine for a tool
#: only `--validate-pdf` uses read as a degraded install to someone who will
#: never pass that flag. Like `warn`, it never makes `doctor` exit non-zero:
#: only `fail` does.
Status = Literal["pass", "optional", "warn", "fail"]

# The release WebShot asks for. 10.06 was measured with JPEG-corrupting bugs
# that OCRmyPDF flags (docs/09-spike-report.md, environment findings), and
# only `--pdfa` reaches Ghostscript: OCRmyPDF needs it for the PDF/A
# conversion and not for the plain searchable PDF.
MINIMUM_GHOSTSCRIPT = (10, 7)

#: When Ghostscript matters at all, said on every line that mentions it.
GHOSTSCRIPT_SCOPE = "only --pdfa uses it, for PDF/A output on the protected-viewer path"

INSTALL_HINTS = {
    "tesseract": "install Tesseract 5 (macOS: brew install tesseract; "
    "Debian/Ubuntu: sudo apt-get install tesseract-ocr)",
    "tesseract-language": "install the language pack (macOS: brew install "
    "tesseract-lang; Debian/Ubuntu: sudo apt-get install tesseract-ocr-{code})",
    # Not `apt-get install ghostscript`: Ubuntu 24.04's package is 10.02, which
    # the version check below then flags, so the fix led straight to a warning.
    "ghostscript": "if you use --pdfa, install Ghostscript 10.7 or later (macOS: "
    "brew install ghostscript; Linux distribution packages are often older, "
    "Ubuntu 24.04's is 10.02, so take a release from ghostscript.com)",
    "browser": "run: python -m playwright install chromium",
    "verapdf": "install veraPDF (macOS: brew install verapdf) or start Docker so "
    "the verapdf/cli image can run",
}

#: The repair for a Python stack that is installed but will not import. From the
#: checkout and forced, because a damaged package is still "installed" to a
#: plain sync. Never `pip install webshot`: WebShot is not on PyPI, and that
#: name there belongs to an unrelated project.
REINSTALL_HINT = (
    "reinstall from your checkout: uv sync --reinstall (name any extras you use too)"
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    fix: str = ""


# --------------------------------------------------------------------------- #
# Judgments — pure functions over what the probes found
# --------------------------------------------------------------------------- #


def parse_version(text: str) -> tuple[int, ...] | None:
    """Read a dotted version out of a tool's `--version` output.

    Ghostscript reports `10.07.1`, where `07` is a minor version and not an
    octal literal — comparing the strings would call 10.07 newer than 10.7.
    """
    for token in text.replace(",", " ").split():
        parts = token.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return tuple(int(part) for part in parts)
    return None


def judge_ghostscript(version: tuple[int, ...] | None) -> CheckResult:
    """Absent is `optional`; present but old is still `warn`.

    Absence costs nothing until `--pdfa` is given. An old release is a
    different finding: it is installed, so `--pdfa` will use it, and the
    detail says which bug the floor is there for rather than claiming every
    release before it has that bug.
    """
    if version is None:
        return CheckResult(
            "Ghostscript",
            "optional",
            f"not installed; {GHOSTSCRIPT_SCOPE}",
            INSTALL_HINTS["ghostscript"],
        )
    printable = ".".join(str(part) for part in version)
    if version[:2] < MINIMUM_GHOSTSCRIPT:
        return CheckResult(
            "Ghostscript",
            "warn",
            f"{printable} is older than 10.7, the release WebShot asks for "
            "(OCRmyPDF reports JPEG-corrupting bugs in 10.6); "
            f"{GHOSTSCRIPT_SCOPE}",
            INSTALL_HINTS["ghostscript"],
        )
    return CheckResult("Ghostscript", "pass", printable)


def judge_verapdf(version: str | None, docker_daemon: bool) -> CheckResult:
    """veraPDF is only needed by `--validate-pdf`, so absence is `optional`.

    Docker counts, but only a *running* daemon does: `docker` on PATH with a
    stopped daemon is the common local state, and reporting it as available
    would move the failure to the middle of a capture (docs/09 environment
    findings, still true a phase later).
    """
    if version:
        return CheckResult("veraPDF", "pass", version)
    if docker_daemon:
        return CheckResult(
            "veraPDF", "pass", "not installed; the Docker daemon can run verapdf/cli"
        )
    return CheckResult(
        "veraPDF",
        "optional",
        "not installed and no Docker daemon; only --validate-pdf uses it, and "
        "it will report that conformance was not checked",
        INSTALL_HINTS["verapdf"],
    )


def parse_tesseract_languages(listing: str) -> set[str]:
    """Read `tesseract --list-langs` output, minus its header line."""
    lines = [line.strip() for line in listing.splitlines() if line.strip()]
    return {line for line in lines if not line.endswith(":") and " " not in line}


def requested_languages(specification: str) -> list[str]:
    """`eng+spa` is how Tesseract spells "these languages together"."""
    return [part for part in specification.split("+") if part]


def judge_tesseract(
    version: str | None, available: set[str], requested: Sequence[str]
) -> list[CheckResult]:
    if version is None:
        return [
            CheckResult(
                "Tesseract",
                "warn",
                "not installed; visual assets will be saved without OCR text",
                INSTALL_HINTS["tesseract"],
            )
        ]
    results = [CheckResult("Tesseract", "pass", version)]
    missing = [code for code in requested if code not in available]
    if missing:
        results.append(
            CheckResult(
                "Tesseract languages",
                "warn",
                f"missing {', '.join(missing)} (installed: {len(available)} packs)",
                INSTALL_HINTS["tesseract-language"].format(code=missing[0]),
            )
        )
    else:
        results.append(
            CheckResult(
                "Tesseract languages",
                "pass",
                f"{'+'.join(requested)} available",
            )
        )
    return results


def summarize(results: Sequence[CheckResult]) -> int:
    """Exit 9 — environment failure — if any required check failed.

    `warn` and `optional` both leave it at 0: neither stops a capture.
    """
    return 9 if any(result.status == "fail" for result in results) else 0


# --------------------------------------------------------------------------- #
# Probes — the parts that touch the machine
# --------------------------------------------------------------------------- #


def tool_output(executable: str, *arguments: str) -> str:
    """Run a tool for its version/listing output, tolerating a broken one.

    `doctor` exists to describe a damaged environment, so a tool that hangs,
    dies, or prints nothing has to come back as an empty string rather than as
    a traceback.
    """
    try:
        completed = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def check_browser() -> CheckResult:
    """Launching is the only honest test that the browser install works."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency is mandatory
        return CheckResult("Chromium", "fail", f"Playwright missing: {exc}", "uv sync")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                return CheckResult("Chromium", "pass", browser.version)
            finally:
                browser.close()
    except Exception as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
        return CheckResult("Chromium", "fail", message, INSTALL_HINTS["browser"])


def check_tesseract(language: str) -> list[CheckResult]:
    executable = shutil.which("tesseract")
    if not executable:
        return judge_tesseract(None, set(), requested_languages(language))
    lines = tool_output(executable, "--version").splitlines()
    version = lines[0].strip() if lines else f"present at {executable}, version unknown"
    available = parse_tesseract_languages(tool_output(executable, "--list-langs"))
    return judge_tesseract(version, available, requested_languages(language))


def check_ghostscript() -> CheckResult:
    executable = shutil.which("gs")
    if not executable:
        return judge_ghostscript(None)
    return judge_ghostscript(parse_version(tool_output(executable, "--version")))


def check_verapdf() -> CheckResult:
    """Ask the bridge what it can run, rather than probing for it a second time.

    "Is the veraPDF gate runnable here" has one definition, in
    `webshot/validate/verapdf.py`; two would be free to drift, and `doctor`
    reporting available while a capture finds nothing is the exact failure this
    subcommand exists to prevent.
    """
    from .validate.verapdf import docker_daemon_available, verapdf_executable

    executable = verapdf_executable()
    version = None
    if executable:
        for line in tool_output(executable, "--version").splitlines():
            if line.lower().startswith("verapdf"):
                version = line.strip()
                break
        version = version or f"present at {executable}, version unknown"
        return judge_verapdf(version, docker_daemon=False)
    return judge_verapdf(None, docker_daemon_available())


def check_extraction() -> CheckResult:
    """The docling-backed extraction stack (arrives in Phase 2 — 04 §1.2).

    Importing is the honest test: docs/09 S3's trap was a converter that
    *installed* fine and crashed on first use, and the Phase 2 corrections
    add a second one (docling-core's chunker package needs tree-sitter and
    huggingface-hub importable even though nothing is ever downloaded).
    """
    try:
        from importlib.metadata import version

        import chonkie  # noqa: F401
        from docling.backend.html_backend import (  # noqa: F401
            HTMLDocumentBackend,
        )
        from docling_core.transforms.chunker.hierarchical_chunker import (  # noqa: F401
            HierarchicalChunker,
        )

        # Through its owner, not directly: `webshot/sanitize.py` is the only
        # module that imports nh3 (CONTRIBUTING rule 1), and importing it
        # proves nh3 loads just as well as importing nh3 would.
        from . import sanitize  # noqa: F401
        from .extract import EXTRACTION_DISTRIBUTIONS

        detail = ", ".join(
            f"{distribution} {version(distribution)}"
            for distribution in EXTRACTION_DISTRIBUTIONS
        )
    except Exception as exc:
        # `Exception`, not `ImportError`, and everything inside the guard.
        #
        # A damaged installation does not fail politely: a compiled extension
        # that will not load raises `OSError`, an upstream initializer raises
        # `RuntimeError`, and `version()` raises `PackageNotFoundError` for a
        # distribution whose metadata was removed. Each of those propagated
        # out of `doctor` as a traceback and Python's exit 1 — from the one
        # command whose entire purpose is to explain a broken environment and
        # exit 9 (docs/09 P8-43). The type is named because "which import
        # failed" is the finding.
        return CheckResult(
            "Extraction",
            "fail",
            f"docling extraction stack is not usable: {type(exc).__name__}: {exc}",
            REINSTALL_HINT,
        )
    return CheckResult("Extraction", "pass", detail)


def check_local_formats() -> CheckResult:
    """MarkItDown and Trafilatura — the Phase 3 readers (04 §1.2).

    Importing rather than asking the metadata, for the reason the extraction
    check gives: a distribution that installs and fails on first import is
    exactly what this command exists to find.
    """
    try:
        from importlib.metadata import version

        import trafilatura  # noqa: F401
        from markitdown import MarkItDown  # noqa: F401

        from .acquire import ADAPTER_DISTRIBUTIONS

        # Inside the guard, not above it. Importing `acquire.adapters` loads
        # MarkItDown, Markdown and defusedxml at module import — so a missing
        # or broken one of those raised *before* the `try` began, and `doctor`
        # crashed in exactly the incomplete environment it exists to diagnose
        # (docs/09 P8-43).
        from .acquire.adapters import OFFICE_CONVERTER_MODULES
        from .capture import DISCOVERY_DISTRIBUTIONS

        detail = ", ".join(
            f"{distribution} {version(distribution)}"
            for distribution in (*ADAPTER_DISTRIBUTIONS, *DISCOVERY_DISTRIBUTIONS)
        )
        office = [
            suffix.removeprefix(".")
            for suffix, module in sorted(OFFICE_CONVERTER_MODULES.items())
            if importlib.util.find_spec(module) is not None
        ]
    except Exception as exc:
        return CheckResult(
            "Local formats",
            "fail",
            f"the local-format and discovery stack is not usable: "
            f"{type(exc).__name__}: {exc}",
            REINSTALL_HINT,
        )
    extra = (
        f"; office formats: {', '.join(office)}"
        if office
        else "; office formats need webshot[office]: install it from your "
        "checkout with uv sync --extra office (name any other extras you use too)"
    )
    return CheckResult("Local formats", "pass", detail + extra)


def check_output_directory(path: Path) -> CheckResult:
    """Writability of the directory a capture would publish into."""
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir():
        return CheckResult(
            "Output directory",
            "fail",
            f"{existing} exists but is not a directory",
            f"choose another --output path (wanted {path})",
        )
    if not os.access(existing, os.W_OK):
        return CheckResult(
            "Output directory",
            "fail",
            f"{existing} is not writable",
            "choose a writable --output path or fix the directory's permissions",
        )
    try:
        with tempfile.NamedTemporaryFile(dir=existing, prefix=".webshot-doctor-"):
            pass
    except OSError as exc:
        return CheckResult(
            "Output directory",
            "fail",
            f"cannot create files in {existing}: {exc}",
            "choose a writable --output path",
        )
    note = f"{existing} is writable"
    if existing != path:
        note += f" (would create {path})"
    return CheckResult("Output directory", "pass", note)


def run_checks(*, language: str, output: Path) -> list[CheckResult]:
    return [
        check_browser(),
        *check_tesseract(language),
        check_ghostscript(),
        check_verapdf(),
        check_extraction(),
        check_local_formats(),
        check_output_directory(output),
    ]


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def render(results: Sequence[CheckResult]) -> str:
    width = max(len(result.name) for result in results)
    # As wide as the widest status present, so a report with no OPTIONAL line
    # keeps the four-character column it always had.
    status_width = max(len(result.status) for result in results)
    lines = []
    for result in results:
        status = f"{result.status.upper():<{status_width}}"
        lines.append(f"{status}  {result.name:<{width}}  {result.detail}")
        if result.fix and result.status != "pass":
            # Nothing is broken on an `optional` line, so it is not a fix.
            label = "to add it" if result.status == "optional" else "fix"
            lines.append(f"{' ' * status_width}  {' ' * width}  {label}: {result.fix}")
    failures = sum(result.status == "fail" for result in results)
    warnings = sum(result.status == "warn" for result in results)
    optional = sum(result.status == "optional" for result in results)
    lines.append("")
    if failures:
        lines.append(
            f"{_count(failures, 'blocking problem', 'blocking problems')}, "
            f"{_count(warnings, 'warning', 'warnings')}."
        )
    elif warnings:
        lines.append(
            "Ready to capture, with "
            f"{_count(warnings, 'degraded capability', 'degraded capabilities')}."
        )
    else:
        lines.append("Everything WebShot needs is installed.")
    if optional:
        names = ", ".join(r.name for r in results if r.status == "optional")
        whose = "its line" if optional == 1 else "their lines"
        lines.append(
            f"Not installed, and needed only for the flag on {whose}: {names}."
        )
    return "\n".join(lines)


def build_doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webshot doctor",
        description="Check that this machine can capture, recognize, and publish.",
    )
    parser.add_argument(
        "--ocr-language",
        default="eng",
        help="language pack(s) a capture would ask Tesseract for, such as eng+spa",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/pdf"),
        help="directory a capture would publish into",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_doctor_parser().parse_args(argv)
    results = run_checks(
        language=arguments.ocr_language, output=arguments.output.expanduser()
    )
    if arguments.json:
        print(
            json.dumps(
                {
                    "checks": [asdict(result) for result in results],
                    "exit_code": summarize(results),
                },
                indent=2,
            )
        )
    else:
        print(render(results))
    return summarize(results)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
