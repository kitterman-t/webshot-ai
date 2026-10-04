#!/usr/bin/env python3
"""Run what CI runs, here, before pushing.

Every check in this repository already exists as its own command, and
CONTRIBUTING lists them — which is exactly how the gap opened: `pytest` alone
reports "662 passed" while comparing no goldens at all, because the golden
tests are deselected by default (docs/09 P6-2). A change that altered captured
output therefore looked fine locally and failed on CI, costing a round-trip
for something a local check finds in under a minute.

So this is one command that runs the CI gauntlet in CI's order and stops
being polite about what it skipped.

    uv run python tools/verify.py            # everything
    uv run python tools/verify.py --fast     # skip the browser-driven checks
    uv run python tools/verify.py --list     # show the steps and exit

`--fast` is for the edit loop; the full run is what "ready to push" means.

Two rules this script exists to obey, both learned from it breaking them:

**Run what CI runs, in the environment CI runs it in.** `pytest` under the
documented `uv sync --extra dev` deselects the entire MCP module and skips the
office-format and RapidOCR cases — and then reported "everything passed",
because a skip is not a failure (docs/09 P7-14). Each step below names the
extras its CI job installs, so the local run cannot be greener than the remote
one for want of a package.

**Never print success for a check that did not run.** A step that is skipped is
named in the exit line and, unless it is skipped for a reason CI shares, it
fails the run. `parity` is the one legitimate skip: CI pins it to macOS because
`tools/parity/check.py` has no portable profile, so on Linux there is nothing
to compare against and CI is not running it either.

The corpus is checked **twice**, from two different absolute paths, and the
second one is not redundant: a local golden run compares this checkout against
goldens recorded from this checkout, so a value that depends on where the
repository lives agrees with itself by construction and is invisible until CI
checks out somewhere else. That has cost a round-trip twice — docs/09 P6-3 and
P7-13 — which is the exact failure this script exists to prevent. It costs
about ninety seconds; `--fast` skips it with the rest of the heavy steps.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The extras each CI job installs, named once so a step can say which job it
#: is standing in for rather than repeating the list. `uv run --extra` is
#: additive against the project environment, so naming them per step costs
#: nothing after the first install.
LINT_EXTRAS = ("dev", "mcp")
#: `.github/workflows/ci.yml`, the `tests` job: "Every optional extra is
#: installed here on purpose: the office-format, RapidOCR, and MCP tests skip
#: without them, and a test that skips everywhere is how 'it works behind an
#: extra' quietly stops being true."  Locally it was worse than that — the MCP
#: module was deselected outright and the run still said everything passed.
TEST_EXTRAS = ("dev", "office", "ocr-rapid", "mcp")
DOCS_EXTRAS = ("dev", "docs", "mcp")
BASE_EXTRAS = ("dev",)


def uv(*args: str, extras: Sequence[str] = BASE_EXTRAS) -> list[str]:
    """A `uv run` invocation with the extras its CI job installs."""
    command = ["uv", "run", "--frozen"]
    for extra in extras:
        command += ["--extra", extra]
    return command + list(args)


@dataclass(frozen=True)
class Step:
    name: str
    #: Run in order; the first failure is the step's failure.
    commands: tuple[list[str], ...]
    #: Drives a browser or an OCR engine, so it is slow and needs the tools
    #: `webshot doctor` checks for. These are the ones `--fast` leaves out —
    #: and the ones most likely to catch a change to captured output.
    heavy: bool = False
    #: Platforms this step can run on at all, empty meaning every platform.
    #: A step skipped for a platform CI also excludes is an honest skip; there
    #: is no other kind here, and a skip is still named in the exit line.
    platforms: tuple[str, ...] = ()
    #: Why the platform restriction exists, printed with the skip.
    platform_reason: str = ""


STEPS: tuple[Step, ...] = (
    Step("ruff (lint)", (uv("ruff", "check", ".", extras=LINT_EXTRAS),)),
    Step(
        "ruff (format)",
        (uv("ruff", "format", "--check", ".", extras=LINT_EXTRAS),),
    ),
    # mypy checks `files = ["src", "tools"]` and the MCP bridge imports the
    # SDK, so the `mcp` extra is what lets it check the bridge against the
    # real API rather than against `Any` (docs/09 P4-14).
    Step("mypy", (uv("mypy", extras=LINT_EXTRAS),)),
    # The `sys.platform == "win32"` blocks, which the run above skips on this
    # platform and which otherwise execute only in the weekly Windows run.
    Step(
        "mypy (Windows branches)",
        (uv("mypy", "--platform", "win32", extras=LINT_EXTRAS),),
    ),
    Step("pytest", (uv("pytest", "-q", "-n", "4", extras=TEST_EXTRAS),)),
    Step("license gate", (uv("python", "tools/licenses/check.py"),)),
    # The `audit` job, which is blocking in CI and was absent here entirely.
    # One command in both places, so the export flags and the pip-audit flags
    # cannot drift apart: it audits every version uv.lock pins, including the
    # ones pinned only for a platform this machine is not (docs/09 P10-16).
    Step("vulnerability audit", (uv("python", "tools/audit/check.py"),)),
    Step(
        "golden corpus",
        (uv("python", "tools/golden/check.py"),),
        heavy=True,
    ),
    Step(
        "golden corpus (second path)",
        (uv("python", "tools/golden/second_path.py"),),
        heavy=True,
    ),
    # macOS only, exactly as the `parity` CI job is: the frozen v2 baselines
    # were recorded on macOS and Chromium's text layout is platform-dependent
    # (docs/09 P0-6). Unlike the golden harness, `tools/parity/check.py` has
    # no portable profile, so on Linux this compares a Linux rendering against
    # a macOS recording and fails for a reason that is not a regression.
    Step(
        "parity (v2 baseline vs v3)",
        (uv("python", "tools/parity/check.py"),),
        heavy=True,
        platforms=("Darwin",),
        platform_reason="macOS-only in CI; no portable baseline exists",
    ),
    # No pre-flight probe. The previous one asked whether mkdocs could be
    # resolved *without installing it* and skipped the step when it could not
    # — which, after the documented `uv sync --extra dev`, is always. The
    # blocking docs job was therefore skipped by default and a broken
    # cross-reference still ended in "Everything passed". `--extra docs`
    # installs the locked extra, which is what the CI step does.
    Step(
        "docs site (strict)",
        (uv("mkdocs", "build", "--strict", "-q", extras=DOCS_EXTRAS),),
    ),
)


def run(step: Step) -> tuple[bool, float, str]:
    started = time.monotonic()
    for command in step.commands:
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            elapsed = time.monotonic() - started
            return False, elapsed, f"{command[0]} is not installed"
        if completed.returncode != 0:
            elapsed = time.monotonic() - started
            # The tail is what a reader needs; the head is setup noise.
            output = (completed.stdout + completed.stderr).strip().splitlines()
            return False, elapsed, "\n".join(output[-25:])
    return True, time.monotonic() - started, ""


def _skip_reason(step: Step) -> str | None:
    if step.platforms and platform.system() not in step.platforms:
        return step.platform_reason or f"{'/'.join(step.platforms)} only"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the CI checks locally, in CI's order."
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the browser-driven checks (golden corpus, parity)",
    )
    parser.add_argument("--list", action="store_true", help="list the steps and exit")
    args = parser.parse_args(argv)

    steps = [s for s in STEPS if not (args.fast and s.heavy)]

    if args.list:
        for step in STEPS:
            mark = "fast" if not step.heavy else "full only"
            note = _skip_reason(step)
            suffix = f"  — skipped here: {note}" if note else ""
            print(f"  {step.name:<28} [{mark}]{suffix}")
        return 0

    if args.fast:
        print("--fast: skipping the browser-driven checks. They are the ones")
        print("that notice a change to captured output — run the full pass")
        print("before pushing.\n")

    live = sys.stdout.isatty()
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []
    started = time.monotonic()

    for step in steps:
        reason = _skip_reason(step)
        if reason is not None:
            skipped.append(f"{step.name} ({reason})")
            print(f"  SKIP  {step.name:<28} {reason}")
            continue
        if live:
            print(f"  ....  {step.name}", end="", flush=True)
        ok, elapsed, detail = run(step)
        status = "ok  " if ok else "FAIL"
        # Erase the in-progress line, but only on a terminal: piped into a
        # file or a log, the escape would be printed literally.
        erase = "\r\033[K" if live else ""
        print(f"{erase}  {status}  {step.name:<28} {elapsed:5.1f}s")
        if not ok:
            failures.append((step.name, detail))

    total = time.monotonic() - started
    print()

    for name, detail in failures:
        print(f"--- {name} ---")
        print(detail or "(no output)")
        print()

    if failures:
        print(f"{len(failures)} check(s) failed in {total:.0f}s.")
        return 1

    note = ""
    if args.fast:
        note = " (--fast: the golden corpus and parity were not run)"
    if skipped:
        note += f" Skipped: {'; '.join(skipped)}."
    print(f"Everything passed in {total:.0f}s.{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
