"""The perf smoke budget: catch an order-of-magnitude regression, nothing finer.

docs/04-spec.md §4 sets no performance SLO on purpose — a capture's cost is
mostly Chromium's, and Chromium is not WebShot's to promise.  What is worth
catching is the change that makes a capture ten times slower, and
docs/06-quality-and-testing.md §CI says how: one fixture article, the median of
three runs on the standard runner, and a budget of three times a recorded
baseline median.

Three properties this file exists to keep true, each of which is a way the
measurement could quietly stop meaning anything:

- **The baseline belongs to the machine that recorded it.** A median recorded on
  a maintainer's laptop and compared against a shared CI runner is a budget
  calibrated to the wrong hardware — it would be either unfailable or
  permanently red. The baseline therefore records where it was taken, and a run
  on a machine that does not match reports its number without judging it.
- **The first run is not a measurement.** It pays for Chromium's first launch,
  the font cache and the import graph. It is run and discarded, and the median
  is of the three that follow, which is what docs/06 asks for.
- **A missing baseline is loud.** Silently passing when there is nothing to
  compare against is how a budget stops existing without anyone deciding to
  remove it.

Usage:

    python tools/perf/smoke.py                 # measure and compare
    python tools/perf/smoke.py --record        # write tools/perf/baseline.json
    python tools/perf/smoke.py --json out.json # also emit the measurement
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).resolve().parent / "baseline.json"

#: docs/06's "fixture article" — the same page the golden corpus captures, so a
#: perf regression and an output regression are measured against one document.
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "professional_page.html"

#: OCR is excluded deliberately. spec §4's own wording is "excluding OCR", and
#: including it would make the number a function of the runner's Tesseract
#: build rather than of WebShot — the same reason the golden corpus stops
#: comparing recognized text once Tesseract differs.
CAPTURE_ARGS = ("--no-ocr",)

MEASURED_RUNS = 3
BUDGET_MULTIPLIER = 3.0


@dataclass(frozen=True)
class Environment:
    """Where a measurement was taken, in the terms that change its size."""

    system: str
    machine: str
    python: str
    cpu_count: int
    #: `RUNNER_OS` when GitHub Actions sets it, else "local". It is what
    #: separates a shared runner from a developer's machine, which is the
    #: distinction the budget turns on.
    runner: str

    @classmethod
    def here(cls) -> Environment:
        return cls(
            system=platform.system(),
            machine=platform.machine(),
            python=".".join(platform.python_version_tuple()[:2]),
            cpu_count=os.cpu_count() or 0,
            runner=os.environ.get("RUNNER_OS", "local"),
        )

    def comparable_to(self, other: Environment) -> bool:
        """Whether a budget from `other` means anything about a run here.

        Deliberately coarse: the CPU count and the exact Python patch level
        move for reasons that are not regressions, while the operating system
        and the architecture are what make one median three times another.
        """
        return (self.system, self.machine, self.runner) == (
            other.system,
            other.machine,
            other.runner,
        )


def _clean_environment() -> dict[str, str]:
    """The environment a measurement runs in, with WebShot's settings removed.

    `WEBSHOT_*` and `WEBSHOT_CONFIG` change what a capture *does*, so a
    maintainer with either exported would measure — or worse, `--record` — a
    baseline under personal settings, and nothing in the recorded environment
    would say so. The golden harness scrubs the same variables for the same
    reason (docs/09 P4-9, P5-10).
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WEBSHOT_")
    }


def capture_once(index: int) -> float:
    """One end-to-end capture, timed the way a user experiences it.

    A subprocess rather than an in-process call: process start, the import
    graph and Playwright's own launch are part of what a capture costs, and a
    regression in any of them is one worth catching.
    """
    with tempfile.TemporaryDirectory(prefix="webshot-perf-") as workdir:
        output = Path(workdir) / f"run-{index}.pdf"
        started = time.perf_counter()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "webshot",
                str(FIXTURE),
                "--output",
                str(output),
                *CAPTURE_ARGS,
            ],
            cwd=REPO_ROOT,
            env=_clean_environment(),
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode != 0:
            raise SystemExit(
                f"the perf fixture failed to capture (exit {completed.returncode}):\n"
                f"{completed.stderr[-2000:]}"
            )
        if not output.is_file():
            raise SystemExit("the perf fixture produced no PDF")
    return elapsed


def measure() -> dict[str, Any]:
    print(f"warm-up run (discarded): {capture_once(0):.2f}s", flush=True)
    runs = []
    for index in range(1, MEASURED_RUNS + 1):
        seconds = capture_once(index)
        print(f"run {index}/{MEASURED_RUNS}: {seconds:.2f}s", flush=True)
        runs.append(round(seconds, 3))
    return {
        "schema": 1,
        "fixture": str(FIXTURE.relative_to(REPO_ROOT)),
        "args": list(CAPTURE_ARGS),
        "runs": runs,
        "median_seconds": round(statistics.median(runs), 3),
        "environment": asdict(Environment.here()),
    }


def _load_baseline() -> dict[str, Any] | None:
    if not BASELINE.is_file():
        return None
    return dict(json.loads(BASELINE.read_text(encoding="utf-8")))


def record(measurement: dict[str, Any]) -> None:
    BASELINE.write_text(
        json.dumps(
            {
                # The measurement plus its provenance, rather than a hand-copied
                # subset: listing the keys again silently dropped `fixture` and
                # `args`, which are the two that say *what* was measured, and
                # would have gone stale again the next time `measure()` grew a
                # field.
                **measurement,
                "budget_multiplier": BUDGET_MULTIPLIER,
                "note": (
                    "Recorded on the CI runner named in `environment`. Re-record "
                    "only with a pinned-browser or Playwright upgrade, in the "
                    "pull request that makes it (docs/06 §CI)."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"recorded baseline: {BASELINE.relative_to(REPO_ROOT)}")


def compare(measurement: dict[str, Any], baseline: dict[str, Any] | None) -> int:
    median = measurement["median_seconds"]
    here = Environment(**measurement["environment"])
    if baseline is None:
        print(
            "\nNO BASELINE. Nothing to compare against, so this measurement is "
            "a number and not a verdict.\n"
            f"Median: {median:.2f}s. Record it from a CI run with "
            "`python tools/perf/smoke.py --record`."
        )
        return 1
    there = Environment(**baseline["environment"])
    budget = baseline["median_seconds"] * baseline.get(
        "budget_multiplier", BUDGET_MULTIPLIER
    )
    ratio = median / baseline["median_seconds"] if baseline["median_seconds"] else 0.0
    print(
        f"\nmedian {median:.2f}s against a {there.runner}/{there.system} baseline of "
        f"{baseline['median_seconds']:.2f}s ({ratio:.2f}x), budget {budget:.2f}s"
    )
    if not here.comparable_to(there):
        print(
            f"REPORTED ONLY: this run is {here.runner}/{here.system}/{here.machine} "
            f"and the baseline is {there.runner}/{there.system}/{there.machine}. A "
            "budget calibrated on other hardware is not a budget, so the number "
            "is published and not judged."
        )
        return 0
    if median > budget:
        print(f"OVER BUDGET by {median - budget:.2f}s.")
        return 1
    print("within budget.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--record",
        action="store_true",
        help="write this measurement to tools/perf/baseline.json",
    )
    parser.add_argument(
        "--json", type=Path, metavar="FILE", help="also write the measurement as JSON"
    )
    arguments = parser.parse_args(argv)

    measurement = measure()
    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(measurement, indent=2) + "\n", encoding="utf-8"
        )
    if arguments.record:
        record(measurement)
        return 0
    return compare(measurement, _load_baseline())


if __name__ == "__main__":
    raise SystemExit(main())
