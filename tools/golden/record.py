#!/usr/bin/env python3
"""Record golden bundles for the fixture corpus.

    uv run python tools/golden/record.py            # every case
    uv run python tools/golden/record.py --case csv-data

Re-recording is never routine: it means WebShot's output changed.  Do it in a
pull request that explains the diff (or, for a pinned-browser or Tesseract
upgrade, in a dedicated PR that contains the upgrade and the re-recording and
nothing else) — see CONTRIBUTING.md.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.golden.harness import (
    GOLDEN_ROOT,
    _dump,
    chunk_digest_failures,
    default_entrypoint,
    environment,
    profile_for,
    run_case,
    selected_cases,
    sentinel_failures,
    snapshot,
    structure_failures,
    write_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases", metavar="ID")
    parser.add_argument(
        "--entrypoint",
        default=shlex.join(default_entrypoint()),
        help="command used to run WebShot (default: this interpreter, -m webshot)",
    )
    arguments = parser.parse_args(argv)
    cases = selected_cases(arguments.cases)
    entrypoint = shlex.split(arguments.entrypoint)

    failures = 0
    with tempfile.TemporaryDirectory(prefix="webshot-golden-record-") as directory:
        root = Path(directory)
        for case in cases:
            run = run_case(case, root / case.id, entrypoint)
            files = snapshot(run)
            if run.exit_code != 0:
                print(f"FAIL {case.id}: exit code {run.exit_code}\n{run.stderr}")
                failures += 1
                continue
            problems = [
                *sentinel_failures(case, files),
                *structure_failures(case, run),
                *chunk_digest_failures(run),
            ]
            if problems:
                print(f"FAIL {case.id}: the produced artifacts are not recordable")
                for problem in problems:
                    print(f"  - {problem}")
                failures += 1
                continue
            write_snapshot(case, files)
            print(f"recorded {case.id} ({len(files)} artifacts)")

    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    current = environment()
    if not arguments.cases:
        (GOLDEN_ROOT / "environment.json").write_text(_dump(current), "utf-8")
        print("recorded environment.json:", json.dumps(current))
    else:
        # environment.json describes the whole corpus, so a subset re-recorded
        # elsewhere would leave the other cases claiming an environment they
        # were not recorded in — and the next strict check would blame them.
        environment_path = GOLDEN_ROOT / "environment.json"
        recorded = (
            json.loads(environment_path.read_text("utf-8"))
            if environment_path.is_file()
            else {}
        )
        profile, reason = profile_for(recorded, current)
        if profile != "strict":
            print(
                f"WARNING: this machine differs from the recorded environment "
                f"({reason}). The re-recorded case(s) now hold this machine's "
                f"output while the rest hold another's; re-record the whole "
                f"corpus instead.",
                file=sys.stderr,
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
