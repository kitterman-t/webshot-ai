"""Turn a pytest JUnit report into a job summary: what failed, what was skipped.

The weekly Windows run was green for six weeks. Four of those runs failed 107
or 150 tests and two never started, because a job-level `continue-on-error`
turned the job's failure into the run's success (docs/09 P10-24). Removing it
makes the run red; this makes the run page say why, without anyone reading a
7,000-line log.

It prints what it saw. A report that is missing, unreadable, or holds no test
at all is this step's failure: pytest did not finish, and an empty table would
read as a clean run. Skips are listed by reason because a skip is a test this
platform did not measure, and the summary should not claim it did.

    python tools/ci/junit_summary.py out/pytest.xml --title "Unit tests (Windows)"
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree

#: Rows past this are counted, not listed: a summary page has a size limit.
MAX_ROWS = 200


@dataclass(slots=True)
class Tally:
    passed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    errored: list[tuple[str, str]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return (
            self.passed
            + len(self.failed)
            + len(self.errored)
            + sum(self.skipped.values())
        )


def _first_line(text: str | None) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = line if len(line) <= 200 else line[:199] + "…"
    return line.replace("|", "\\|")


def tally(report: Path) -> Tally:
    """Every `<testcase>` in the report, sorted by outcome.

    Counted from the cases rather than from the suite's attributes, so the
    totals and the listed rows cannot disagree.
    """
    root = ElementTree.parse(report).getroot()
    result = Tally()
    for case in root.iter("testcase"):
        test = f"{case.get('classname', '')}::{case.get('name', '')}"
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if error is not None:
            # A setup error on a test that also failed is still one test.
            result.errored.append((test, _first_line(error.get("message"))))
        elif failure is not None:
            result.failed.append((test, _first_line(failure.get("message"))))
        elif skipped is not None:
            result.skipped[_first_line(skipped.get("message")) or "(no reason)"] += 1
        else:
            result.passed += 1
    return result


def render(result: Tally, *, title: str, source: Path) -> str:
    lines = [
        f"## {title}",
        "",
        f"**{len(result.failed)} failed, {len(result.errored)} errors, "
        f"{result.passed} passed, {sum(result.skipped.values())} skipped** "
        f"— {result.total} test cases in `{source.as_posix()}`",
    ]
    broken = [(test, "error", why) for test, why in result.errored] + [
        (test, "failed", why) for test, why in result.failed
    ]
    if broken:
        lines += [
            "",
            f"### Failed and errored ({len(broken)})",
            "",
            "| Test | Outcome | First line |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{test}` | {outcome} | {why} |"
            for test, outcome, why in sorted(broken)[:MAX_ROWS]
        ]
        if len(broken) > MAX_ROWS:
            lines += ["", f"…and {len(broken) - MAX_ROWS} more; see the log."]
    if result.skipped:
        lines += [
            "",
            f"### Not measured here: skipped, by reason ({sum(result.skipped.values())})",
            "",
            "| Reason | Tests |",
            "|---|---|",
        ]
        lines += [
            f"| {reason} | {count} |"
            for reason, count in sorted(result.skipped.items(), key=lambda kv: -kv[1])
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize a pytest JUnit report as GitHub job-summary markdown."
    )
    parser.add_argument("report", type=Path, help="pytest --junitxml output")
    parser.add_argument("--title", default="Unit tests")
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(
            f"## {args.title}\n\n**No report at `{args.report.as_posix()}`**: pytest "
            "did not finish, so nothing here was measured.\n"
        )
        return 1
    try:
        result = tally(args.report)
    except ElementTree.ParseError as exc:
        print(f"## {args.title}\n\n**Unreadable report** `{args.report}`: {exc}\n")
        return 1
    if result.total == 0:
        print(f"## {args.title}\n\n**The report holds no test cases**: nothing ran.\n")
        return 1
    print(render(result, title=args.title, source=args.report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
