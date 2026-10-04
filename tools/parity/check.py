#!/usr/bin/env python3
"""Measure v3-candidate bundles against the frozen v2 baselines.

    uv run python tools/parity/check.py                       # full corpus
    uv run python tools/parity/check.py --case article-clean
    uv run python tools/parity/check.py --report parity-report.md

For every corpus case with a frozen baseline this re-captures the fixture with
the current code, reads both bundles into the format-independent view, and
scores the metrics defined in docs/06 §Parity against `parity.toml`.  Exit 0
means every case met every threshold.  The markdown report is written for the
pull request and uploaded by the CI job on every run.
"""

from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.corpus import GoldenCase  # noqa: E402
from tools.golden.harness import (  # noqa: E402
    default_entrypoint,
    read_snapshot_directory,
    run_case,
    selected_cases,
    snapshot,
)
from tools.parity.engine import (  # noqa: E402
    CaseResult,
    compare,
    load_thresholds,
    render_report,
)
from tools.parity.views import read_bundle  # noqa: E402

BASELINE_ROOT = REPO_ROOT / "tests" / "parity" / "v2-baseline"


def read_baseline(case_id: str) -> dict[str, str]:
    return read_snapshot_directory(BASELINE_ROOT / case_id)


def skip_reason(case: GoldenCase) -> str | None:
    """Why a corpus case is not parity-measured, derived from the case itself.

    Every skip is reported in the run output and the report — a silently
    narrowed corpus would read as "covered everything".
    """
    if case.kind == "protected":
        return (
            "the protected path is untouched by the extraction swap; its "
            "invariance is enforced byte-for-byte by the golden corpus job"
        )
    if "--no-ai-bundle" in case.args:
        return "produces no bundle (--no-ai-bundle); nothing to compare"
    if not (BASELINE_ROOT / case.id / "bundle").is_dir():
        return "no frozen v2 baseline recorded"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases", metavar="ID")
    parser.add_argument("--report", type=Path, help="write the markdown report here")
    parser.add_argument(
        "--entrypoint",
        default=shlex.join(default_entrypoint()),
        help="command used to run WebShot (default: this interpreter, -m webshot)",
    )
    arguments = parser.parse_args(argv)
    entrypoint = shlex.split(arguments.entrypoint)
    thresholds = load_thresholds()

    results: list[CaseResult] = []
    skipped: dict[str, str] = {}
    failed_captures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="webshot-parity-") as directory:
        for case in selected_cases(arguments.cases):
            reason = skip_reason(case)
            if reason:
                skipped[case.id] = reason
                continue
            run = run_case(case, Path(directory) / case.id, entrypoint)
            if run.exit_code != 0:
                failed_captures.append(case.id)
                print(f"FAIL {case.id}: capture exited {run.exit_code}\n{run.stderr}")
                continue
            result = compare(
                case.id,
                read_bundle(read_baseline(case.id)),
                read_bundle(snapshot(run)),
                thresholds,
            )
            results.append(result)
            failing = [metric.name for metric in result.metrics if not metric.passed]
            print(
                f"{'FAIL' if failing else 'ok  '} {case.id}"
                + (f"  ({', '.join(failing)})" if failing else "")
            )

    report = render_report(
        results,
        skipped,
        thresholds,
        profile_note=f"{len(results)} case(s) measured, {len(skipped)} skipped, "
        f"{len(failed_captures)} failed to capture",
        failed_captures=failed_captures,
    )
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(report, encoding="utf-8")
        print(f"report written to {arguments.report}")

    failing_cases = [result.case_id for result in results if not result.passed]
    if failed_captures or failing_cases:
        print(
            f"\n{len(failed_captures) + len(failing_cases)} case(s) below parity "
            "thresholds or failed to capture."
        )
        return 1
    print(f"\nAll {len(results)} measured case(s) meet the parity thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
