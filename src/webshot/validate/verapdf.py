"""veraPDF: the PDF/A conformance gate, when the machine can run it.

veraPDF is the industry validator and it is GPL, so WebShot never links it — it
is invoked as an external tool the user installs, or as the `verapdf/cli`
container (docs/adr/0006-license-policy.md).  Neither is guaranteed to exist,
and docs/04-spec.md §5 is explicit about what happens then: an absent validator
is a warning, not a failure.  `--validate-pdf=strict` is how a caller says the
absence of proof is unacceptable.

What gets validated is always the **final composed file** — after the OCR
layer, the appendix merge, and the embedded files — because every one of those
steps happens after OCRmyPDF's conversion and any of them could break
conformance (ADR-0010, red-team finding C2).  Validating the intermediate would
prove nothing about what WebShot actually published.

This module owns the tool: `webshot doctor` asks it what is available rather
than probing again, so there is one definition of "the gate can run here".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader
from pypdf.generic import StreamObject

DOCKER_IMAGE = "verapdf/cli"
VALIDATION_TIMEOUT_S = 300
PROBE_TIMEOUT_S = 30


def _xmp_property(name: str, characters: str) -> re.Pattern[bytes]:
    """Match one XMP property in either RDF spelling.

    RDF lets the same property be an attribute or an element, and pikepdf emits
    either depending on what else is in the packet — reading only the attribute
    form made a conformant file look like a plain PDF.  Matching by pattern
    rather than by parsing also means no XML parser is ever pointed at a packet
    that came out of a file (docs/04-spec.md §6.6).
    """
    return re.compile(
        rf'{name}\s*=\s*"([{characters}])"'.encode()
        + rf"|<{name}[^>]*>\s*([{characters}])\s*</{name}>".encode()
    )


PDFA_PART = _xmp_property("pdfaid:part", r"\d")
PDFA_CONFORMANCE = _xmp_property("pdfaid:conformance", "A-Za-z")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """What a validation attempt established, including "nothing".

    `compliant is None` is the single way of saying "not established" — whether
    because no validator was found, or because the file declares no conformance
    level to be measured against.  `tool` says which of those it was.
    """

    tool: str | None
    flavour: str
    compliant: bool | None
    passed_rules: int | None = None
    failed_rules: int | None = None
    failed_checks: int | None = None
    failures: tuple[str, ...] = ()
    report_file: str | None = None
    detail: str = ""


def verapdf_executable() -> str | None:
    """The veraPDF CLI, if this machine has one."""
    return shutil.which("verapdf")


def docker_daemon_available() -> bool:
    """Whether a Docker daemon will actually answer.

    `docker` on PATH says nothing about whether the daemon is running — a very
    common local state, and the reason both this module and `webshot doctor`
    report the daemon rather than the binary.
    """
    executable = shutil.which("docker")
    if not executable:
        return False
    try:
        probe = subprocess.run(
            [executable, "info"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _local_command(pdf: Path, flavour: str) -> tuple[str, list[str]] | None:
    executable = verapdf_executable()
    if not executable:
        return None
    return "verapdf", [executable, "--format", "json", "--flavour", flavour, str(pdf)]


def _docker_command(pdf: Path, flavour: str) -> tuple[str, list[str]] | None:
    if not docker_daemon_available():
        return None
    return f"docker {DOCKER_IMAGE}", [
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{pdf.parent.resolve()}:/data:ro",
        DOCKER_IMAGE,
        "--format",
        "json",
        "--flavour",
        flavour,
        f"/data/{pdf.name}",
    ]


def declared_flavour(pdf: Path) -> str | None:
    """The PDF/A flavour the file claims in its own XMP, e.g. `3b`.

    A file is validated against what it says it is.  A capture that never
    claimed PDF/A — every web-path capture, since ADR-0010 rejects that
    conversion — has nothing to be conformant *to*, and saying so is more
    useful than reporting it as a failure of a standard it never invoked.
    """
    try:
        metadata = PdfReader(str(pdf)).root_object.get("/Metadata")
        raw = cast(StreamObject, metadata.get_object()).get_data() if metadata else b""
    except Exception:  # a file we cannot parse is the caller's problem, not ours
        return None
    part = _matched(PDFA_PART.search(raw))
    if part is None:
        return None
    return f"{part}{(_matched(PDFA_CONFORMANCE.search(raw)) or 'b').lower()}"


def _matched(match: re.Match[bytes] | None) -> str | None:
    """The one group that actually matched, whichever spelling it was."""
    if match is None:
        return None
    value = next((group for group in match.groups() if group), None)
    return value.decode() if value else None


def _summarize(
    payload: dict[str, Any], flavour: str, tool: str, report_file: str | None
) -> ValidationReport:
    jobs = payload.get("report", {}).get("jobs") or []
    results = (jobs[0].get("validationResult") if jobs else None) or []
    if not results:
        return ValidationReport(
            tool=tool,
            flavour=flavour,
            compliant=None,
            report_file=report_file,
            detail="veraPDF produced no validation result for this file",
        )
    result = results[0]
    details = result.get("details", {})
    return ValidationReport(
        tool=tool,
        flavour=flavour,
        compliant=bool(result.get("compliant")),
        passed_rules=details.get("passedRules"),
        failed_rules=details.get("failedRules"),
        failed_checks=details.get("failedChecks"),
        failures=tuple(
            f"{summary.get('clause')}: {summary.get('description')}"
            for summary in details.get("ruleSummaries", [])
        ),
        report_file=report_file,
        detail=str(result.get("profileName") or ""),
    )


def validate(pdf: Path, *, report_path: Path | None = None) -> ValidationReport:
    """Validate `pdf` against the flavour it declares, or explain why we cannot."""
    flavour = declared_flavour(pdf)
    if flavour is None:
        return ValidationReport(
            tool=None,
            flavour="none",
            compliant=None,
            detail=(
                "the file declares no PDF/A conformance level, so there is nothing "
                "to validate — PDF/A is available on protected-viewer captures via "
                "--pdfa (docs/adr/0010)"
            ),
        )
    # A stale report beside a newly published PDF is worse than none: it looks
    # like this file's verdict and describes the previous one. Cleared before
    # validation rather than only on success, because every early return below
    # is a path that used to leave it in place (docs/09 P8-57).
    if report_path is not None:
        report_path.unlink(missing_ok=True)
    # Lazily, so a machine with the CLI installed never pays for the daemon probe.
    attempts: list[str] = []
    for build in (_local_command, _docker_command):
        found = build(pdf, flavour)
        if found is None:
            continue
        tool, command = found
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=VALIDATION_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # Continue to the next candidate rather than returning. A `verapdf`
            # on PATH that is broken, half-installed, or hangs used to end the
            # loop before Docker was tried — so a machine with a working
            # container backend reported conformance as unavailable because of
            # a binary it did not need to use (docs/09 P8-58). The detail is
            # kept and reported if nothing else works.
            attempts.append(f"{tool} could not be run: {exc}")
            continue
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            attempts.append(
                f"{tool} exited {completed.returncode} without a JSON report: "
                + (completed.stderr.strip().splitlines() or [""])[-1]
            )
            continue
        written: str | None = None
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(completed.stdout, encoding="utf-8", newline="\n")
            written = str(report_path)
        return _summarize(payload, flavour, tool, written)

    return ValidationReport(
        tool=None,
        flavour=flavour,
        compliant=None,
        detail=(
            "; ".join(attempts)
            if attempts
            else (
                "veraPDF is not available: install the CLI (brew install verapdf) "
                "or start Docker so the verapdf/cli image can run. PDF/A "
                "conformance was not checked."
            )
        ),
    )
