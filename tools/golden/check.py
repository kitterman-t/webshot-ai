#!/usr/bin/env python3
"""Re-capture the fixture corpus and diff it against the recorded goldens.

    uv run python tools/golden/check.py                     # every case
    uv run python tools/golden/check.py --case article-clean
    uv run python tools/golden/check.py --profile portable  # force the profile

Exit code 0 means WebShot still produces the recorded artifacts.  Any other
value means output changed: read the per-file report, and only re-record once
you can explain every line of the diff.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.golden.harness import (
    GOLDEN_ROOT,
    asset_digest_failures,
    chunk_digest_failures,
    chunk_pointer_failures,
    comparable,
    compare,
    default_entrypoint,
    environment,
    environment_drift,
    environment_summary,
    manifest_digest_failures,
    profile_for,
    read_snapshot,
    run_case,
    selected_cases,
    sentinel_failures,
    snapshot,
    structure_failures,
)


def drift_annotation(drift: list[str]) -> str | None:
    """A GitHub Actions warning naming what differs from the recording.

    Only real differences: a dimension the recording never wrote is printed
    but not annotated, or every run would warn until the next recording.
    """
    real = [line for line in drift if ": not recorded;" not in line]
    if not real:
        return None
    message = " | ".join(real).replace("%", "%25").replace("\r", "").replace("\n", " ")
    return f"::warning title=Golden corpus environment::{message}"


def report_environment(recorded: dict[str, Any], current: dict[str, Any]) -> None:
    """Print what this run used and every way it differs from the recording.

    A golden failure that arrives with the fonts, the OS or a tool changed
    names that change in its first lines (docs/09 P85); P7-14's twelve
    failures read as a mystery for want of exactly this.
    """
    print(f"environment: {environment_summary(current)}")
    drift = environment_drift(recorded, current)
    for line in drift:
        print(f"environment differs: {line}")
    if not drift:
        print("environment differs: in no recorded dimension")
    annotation = drift_annotation(drift)
    if annotation and os.environ.get("GITHUB_ACTIONS") == "true":
        print(annotation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases", metavar="ID")
    parser.add_argument(
        "--profile", choices=("auto", "strict", "portable"), default="auto"
    )
    parser.add_argument(
        "--entrypoint",
        default=shlex.join(default_entrypoint()),
        help="command used to run WebShot (default: this interpreter, -m webshot)",
    )
    arguments = parser.parse_args(argv)
    cases = selected_cases(arguments.cases)
    entrypoint = shlex.split(arguments.entrypoint)

    recorded_environment: dict[str, str] = {}
    environment_path = GOLDEN_ROOT / "environment.json"
    if environment_path.is_file():
        recorded_environment = json.loads(environment_path.read_text("utf-8"))
    current = environment()
    profile, reason = profile_for(recorded_environment, current)
    if arguments.profile != "auto":
        profile, reason = arguments.profile, "requested on the command line"
    print(f"golden profile: {profile} ({reason})")
    report_environment(recorded_environment, current)

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="webshot-golden-check-") as directory:
        root = Path(directory)
        for case in cases:
            expected = read_snapshot(case)
            if not expected:
                failures.append(f"{case.id}: no golden recorded")
                print(f"FAIL {case.id}: no golden recorded")
                continue
            run = run_case(case, root / case.id, entrypoint)
            actual = snapshot(run)
            problems = [
                f"exit code {run.exit_code} (expected 0)\n{run.stderr}"
                if run.exit_code
                else "",
                *sentinel_failures(case, actual),
                *structure_failures(case, run),
                *chunk_digest_failures(run),
                *chunk_pointer_failures(run),
                *manifest_digest_failures(run),
                *asset_digest_failures(run),
            ]
            problems = [problem for problem in problems if problem]
            expected = comparable(expected, profile, ocr_pdf_text=case.ocr_pdf_text)
            actual = comparable(actual, profile, ocr_pdf_text=case.ocr_pdf_text)
            problems.extend(compare(expected, actual))
            if problems:
                failures.append(case.id)
                print(f"FAIL {case.id}")
                for problem in problems:
                    print("  " + problem.replace("\n", "\n  "))
            else:
                print(f"ok   {case.id}")

    if failures:
        print(f"\n{len(failures)} of {len(cases)} case(s) differ from the goldens.")
        if profile == "portable":
            print(
                "Profile was portable: OCR text and browser versions were already "
                "excluded, so these differences are real output changes."
            )
        return 1
    print(f"\nAll {len(cases)} case(s) match the recorded goldens ({profile} profile).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
