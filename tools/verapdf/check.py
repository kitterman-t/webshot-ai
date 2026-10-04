#!/usr/bin/env python3
"""Assemble the protected fixture with --pdfa and validate the composed file.

    uv run python tools/verapdf/check.py                    # report, exit 0
    uv run python tools/verapdf/check.py --strict           # non-conformance fails
    uv run python tools/verapdf/check.py --out reports/     # keep the JSON report

The point of this script is *what* it validates.  OCRmyPDF produces a
conformant PDF/A-3 and WebShot then appends an appendix, rewrites link
destinations, adds bookmarks, and embeds five associated files — all after the
conversion.  Only the final composed file says whether the deliverable a user
actually receives is conformant, so that is the file this checks (ADR-0010,
red-team finding C2).

Non-blocking in CI until the baseline has been clean for a while; `--strict` is
what flips it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.harness import assemble_viewer_fixture  # noqa: E402
from webshot.validate.verapdf import validate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when the composed file is not conformant",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="directory to write verapdf.json (and the PDF) into",
    )
    arguments = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="webshot-verapdf-") as directory:
        workdir = Path(directory)
        pdf = (arguments.out or workdir) / "protected-viewer-pdfa.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        # The same assembly the golden corpus records, with --pdfa on, so what
        # is validated cannot drift from what is frozen.
        manifest = assemble_viewer_fixture(
            workdir, pdf, workdir / "bundle.ai", pdfa=True
        )
        composition = manifest["pdf"]
        print(
            f"composed {composition['pages']} pages "
            f"({composition['source_pages']} source + "
            f"{composition['transcript_appendix_pages']} appendix), "
            f"{len(composition['embedded_machine_readable_files'])} embedded files, "
            f"declared {composition['pdfa']}"
        )

        report_path = (arguments.out / "verapdf.json") if arguments.out else None
        report = validate(pdf, report_path=report_path)

    print(f"validator: {report.tool or 'unavailable'} · flavour: {report.flavour}")
    if report.compliant is None:
        print(f"INCONCLUSIVE: {report.detail}")
        return 0
    if report.compliant:
        print(
            f"PASS: the final composed file is {report.flavour} conformant "
            f"({report.passed_rules} rules passed)"
        )
        return 0
    print(
        f"FAIL: {report.failed_rules} rule(s), {report.failed_checks} check(s) failed"
    )
    for failure in report.failures:
        print(f"  - {failure}")
    if report_path:
        print(f"full report: {report_path}")
    return 1 if arguments.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
