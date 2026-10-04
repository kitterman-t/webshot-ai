#!/usr/bin/env python3
"""Audit every version uv.lock pins against OSV, whichever platform it is pinned for.

    uv run python tools/audit/check.py                     # export uv.lock, audit it
    uv run python tools/audit/check.py --requirement FILE  # audit an existing export

`uv export` writes a universal requirements file: one lock for every platform,
with an environment marker on each line that applies to only some of them
(`transformers==5.8.1 ; sys_platform == 'darwin'`). pip-audit evaluates those
markers against the machine it runs on and drops every line that does not
match: no skip reason, no count, and `--strict` does not object, because to
pip-audit that line was never a requirement. So an audit covers the share of
the lock that its runner's platform installs and reports on the whole of it.
Run on macOS, the audit never checked the pins held only for Linux or Windows;
run on Linux, it never checked the macOS-only transformers 5.8.1
(PYSEC-2026-3929). The CI job has run on both. See docs/09 P10-16.

So the markers are read here, kept for the report, and never handed to
pip-audit. Every pin goes in bare, which runs into pip-audit's other rule: two
versions of one package are "duplicate requirements", a hard error across every
`-r` a single run is given. A forked package therefore puts its second version
in a second run; a lock with no fork is one run.

Bare pins also mean pip-audit must not install anything — `pywin32` has no
wheel for Linux — so `--disable-pip` has it read the file as the resolved set
it is. Without that flag, `--no-deps` still has pip resolve the file in a
scratch environment, which evaluates the markers a second time.

Nothing here trusts pip-audit to have looked. Every pin handed to it must come
back in its JSON report, or the audit fails as incomplete rather than clean.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What CI has always audited: every extra, the locked versions only, and not
#: WebShot itself — an editable checkout pip-audit cannot resolve and
#: `--strict` refuses to skip.
EXPORT = (
    "uv",
    "export",
    "--frozen",
    "--all-extras",
    "--no-emit-project",
    "--no-hashes",
    "--format",
    "requirements-txt",
)

#: Exit statuses. An audit that could not finish is not a clean one, and it
#: says so with a status of its own rather than borrowing either of the others.
CLEAN = 0
VULNERABLE = 1
INCOMPLETE = 2


class ExportError(ValueError):
    """The lock could not be exported, or not as exact pins."""


@dataclass(frozen=True, order=True)
class Pin:
    name: NormalizedName
    version: Version

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class Advisory:
    id: str
    aliases: tuple[str, ...]
    fixed_in: tuple[str, ...]


def parse_pins(text: str) -> dict[Pin, list[str]]:
    """Every exact pin in a `uv export` requirements file, with its markers.

    Each value lists the marker of every line that pinned that version, `""`
    for a line with none. A line that is not one exact pin raises instead of
    being passed over: a URL, a range, or an option line would otherwise fall
    out of the audit exactly the way a marker did.
    """
    pins: dict[Pin, list[str]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as error:
            raise ExportError(f"line {number}: {line!r}: {error}") from None
        specifiers = list(requirement.specifier)
        exact = (
            requirement.url is None
            and len(specifiers) == 1
            and specifiers[0].operator == "=="
            and not specifiers[0].version.endswith(".*")
        )
        if not exact:
            raise ExportError(
                f"line {number}: {line!r} is not pinned to one exact version"
            )
        try:
            version = Version(specifiers[0].version)
        except InvalidVersion as error:
            raise ExportError(f"line {number}: {line!r}: {error}") from None
        marker = "" if requirement.marker is None else str(requirement.marker)
        pin = Pin(canonicalize_name(requirement.name), version)
        pins.setdefault(pin, []).append(marker)
    if not pins:
        # An empty export audits to "no known vulnerabilities", which is true
        # of nothing and would read as true of the lock.
        raise ExportError("the export pins nothing")
    return pins


def runs(pins: Iterable[Pin]) -> list[list[Pin]]:
    """Group pins so that no pip-audit run names a package twice.

    The lowest version of every package goes in the first run, the next
    version of a forked one in the second, and so on — so there are as many
    runs as the widest fork in the lock has versions.
    """
    versions: dict[NormalizedName, set[Version]] = {}
    for pin in pins:
        versions.setdefault(pin.name, set()).add(pin.version)
    grouped: list[list[Pin]] = []
    for name in sorted(versions):
        for depth, version in enumerate(sorted(versions[name])):
            if depth == len(grouped):
                grouped.append([])
            grouped[depth].append(Pin(name, version))
    return grouped


def read_report(
    document: Mapping[str, Any],
) -> tuple[dict[Pin, list[Advisory]], list[str]]:
    """The pins a pip-audit JSON report says it audited, and what it skipped."""
    audited: dict[Pin, list[Advisory]] = {}
    skipped: list[str] = []
    for entry in document["dependencies"]:
        if "skip_reason" in entry:
            skipped.append(f"{entry['name']}: {entry['skip_reason']}")
            continue
        pin = Pin(canonicalize_name(entry["name"]), Version(entry["version"]))
        # OSV has returned the same advisory twice for one version
        # (PYSEC-2026-3929 on transformers 5.8.1); it is one finding.
        advisories: dict[str, Advisory] = {}
        for vuln in entry["vulns"]:
            advisories.setdefault(
                vuln["id"],
                Advisory(
                    vuln["id"],
                    tuple(sorted(vuln.get("aliases", []))),
                    tuple(vuln.get("fix_versions", [])),
                ),
            )
        audited[pin] = list(advisories.values())
    return audited, skipped


def unaccounted(handed: Iterable[Pin], reported: Iterable[Pin]) -> list[str]:
    """Pins handed to pip-audit and absent from its report, and the reverse.

    A set comparison, not a count: a report that lost one pin and gained
    another has the right length and the wrong contents.
    """
    handed_set, reported_set = set(handed), set(reported)
    problems = [
        f"not in pip-audit's report: {pin}" for pin in sorted(handed_set - reported_set)
    ]
    problems += [
        f"in pip-audit's report but never handed to it: {pin}"
        for pin in sorted(reported_set - handed_set)
    ]
    return problems


def _tail(completed: subprocess.CompletedProcess[str]) -> str:
    lines = (completed.stdout + completed.stderr).strip().splitlines()
    return "\n".join(f"    {line}" for line in lines[-25:]) or "    (no output)"


def audit_run(
    pins: Sequence[Pin], work: Path, index: int, osv_url: str | None
) -> tuple[dict[Pin, list[Advisory]], list[str]]:
    """One pip-audit run over bare pins; returns its findings and its problems."""
    requirement = work / f"run-{index + 1}.txt"
    report = work / f"run-{index + 1}.json"
    requirement.write_text(
        "".join(f"{pin.name}=={pin.version}\n" for pin in pins), encoding="utf-8"
    )
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--strict",
        "--no-deps",
        "--disable-pip",
        # Named rather than defaulted: pip-audit's default is PyPI's JSON API,
        # while the CI job, the security guide, and docs/06 all say OSV.
        "--vulnerability-service",
        "osv",
        # OSV queries are POSTs, which the HTTP cache never stores, so a
        # scratch cache costs nothing and keeps the run out of the user's.
        "--cache-dir",
        str(work / "cache"),
        "--progress-spinner",
        "off",
        "--format",
        "json",
        "--output",
        str(report),
        "--requirement",
        str(requirement),
    ]
    if osv_url is not None:
        command += ["--osv-url", osv_url]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    label = f"pip-audit run {index + 1} ({len(pins)} pins)"

    if not report.is_file():
        return {}, [
            f"{label} wrote no report and exited {completed.returncode}:\n"
            + _tail(completed)
        ]
    text = report.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
        audited, skipped = read_report(document)
    except (ValueError, KeyError, TypeError) as error:
        return {}, [
            f"{label} wrote a report this cannot read ({error!r}):\n{text[:500]}"
        ]

    problems = [f"{label} skipped {entry}" for entry in skipped]
    problems += unaccounted(pins, audited)
    if completed.returncode != 0 and not any(audited.values()):
        problems.append(
            f"{label} exited {completed.returncode} without reporting a "
            "vulnerability:\n" + _tail(completed)
        )
    return audited, problems


def _applies_here(markers: Sequence[str]) -> bool:
    return any(not marker or Marker(marker).evaluate() for marker in markers)


def audit(pins: Mapping[Pin, Sequence[str]], source: str, osv_url: str | None) -> int:
    grouped = runs(pins)
    audited: dict[Pin, list[Advisory]] = {}
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="webshot-audit-") as scratch:
        for index, run in enumerate(grouped):
            found, trouble = audit_run(run, Path(scratch), index, osv_url)
            audited.update(found)
            problems += trouble
    # Each run was held to the pins it was handed; this asks the same of the
    # whole, so a run that came back empty without saying why cannot pass as
    # one that found nothing.
    problems += [p for p in unaccounted(pins, audited) if p not in problems]

    packages = len({pin.name for pin in pins})
    covered = len(pins.keys() & audited.keys())
    print(
        f"Audited {covered} of {len(pins)} pins ({packages} packages) "
        f"from {source} against OSV, in {len(grouped)} pip-audit run(s)."
    )
    # The pins a host-evaluated audit would have dropped here, printed so that
    # every run shows them in the count above rather than asserting they are.
    elsewhere = sorted(
        pin for pin, markers in pins.items() if not _applies_here(markers)
    )
    if elsewhere:
        print(
            f"{len(elsewhere)} of them apply only to another platform or "
            "interpreter than this one, which pip-audit alone would skip:"
        )
        print("  " + ", ".join(map(str, elsewhere)))

    vulnerable = {pin: found for pin, found in sorted(audited.items()) if found}
    for pin, advisories in vulnerable.items():
        markers = pins.get(pin, ())
        where = "; ".join(dict.fromkeys(m or "every platform" for m in markers))
        where = where or "not in the export"
        print(f"\nVULNERABLE  {pin}  (pinned for: {where})")
        for advisory in advisories:
            aliases = f" ({', '.join(advisory.aliases)})" if advisory.aliases else ""
            fixed = (
                f"fixed in {', '.join(advisory.fixed_in)}"
                if advisory.fixed_in
                else "no fixed version"
            )
            print(f"  {advisory.id}{aliases}, {fixed}")

    if problems:
        print("\nThe audit is incomplete, so it is not clean:")
        for problem in problems:
            print(f"  {problem}")
        return INCOMPLETE
    if vulnerable:
        print(f"\n{len(vulnerable)} locked pin(s) carry a known vulnerability.")
        return VULNERABLE
    print("No known vulnerability in any locked pin.")
    return CLEAN


def export_lock() -> str:
    """uv.lock as `uv export` writes it to a file.

    To a file, not stdout: CI sets `FORCE_COLOR`, and uv then colours the
    comment lines it prints (`\x1b[32m# via typer`), which stop reading as
    comments. A file is data, and uv writes it plain.
    """
    with tempfile.TemporaryDirectory(prefix="webshot-export-") as scratch:
        exported = Path(scratch) / "requirements.txt"
        command = [*EXPORT, "--output-file", str(exported)]
        try:
            completed = subprocess.run(
                command, cwd=REPO_ROOT, capture_output=True, text=True, check=False
            )
        except FileNotFoundError:
            raise ExportError("uv is not installed, so nothing was exported") from None
        if completed.returncode != 0:
            raise ExportError(
                f"`{' '.join(command)}` exited {completed.returncode}:\n"
                + (completed.stderr.strip() or "(no output)")
            )
        return exported.read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit every version uv.lock pins against OSV, on every "
        "platform it is pinned for."
    )
    parser.add_argument(
        "--requirement",
        type=Path,
        metavar="FILE",
        help="audit this `uv export` requirements file instead of exporting uv.lock",
    )
    parser.add_argument(
        "--osv-url",
        metavar="URL",
        help="query this OSV endpoint instead of api.osv.dev "
        "(the tests point it at a loopback fake)",
    )
    args = parser.parse_args(argv)

    source = "uv.lock" if args.requirement is None else str(args.requirement)
    try:
        if args.requirement is None:
            text = export_lock()
        else:
            text = args.requirement.read_text(encoding="utf-8")
        pins = parse_pins(text)
    except ExportError as error:
        print(f"{source} cannot be audited: {error}")
        return INCOMPLETE
    return audit(pins, source, args.osv_url)


if __name__ == "__main__":
    raise SystemExit(main())
