#!/usr/bin/env python3
"""How much of a capture's text its PDF prints, measured as the capture measures it.

    uv run python tools/coverage/printed_share.py page.pdf [more.pdf ...]
    uv run python tools/coverage/printed_share.py --json page.pdf

Reviews of finished captures measured this by hand, as the share of
`content.md`'s word 5-grams found in `pdftotext`'s output (docs/09 P16, P99).
That measure counts two things a PDF never prints: a link's target, which
Markdown writes beside its text, and the text recognized in a picture, which
the bundle labels as OCR. A lesson page and an interactive site that printed
all their text scored 0.49 and 0.30 by it, and a library record that printed
86% of its text scored 0.22 (docs/09 P113-6).

The capture already measures this after every render, and correctly
(`webshot.render.text_layer`, docs/09 P97): runs of letters and digits from
`content.txt`, which carries no link targets, with the OCR blocks dropped,
against the text PDFium reads off each page. It warns in the manifest below
half. This tool runs that same measure on a finished PDF, so a review reads
the number the capture judged by rather than a second, different one.

The bundle's text is the `content.txt` the PDF carries, or, for a capture
made with `--no-embed-bundle`, the one in the `.ai` directory beside it. A PDF
with neither is not measured, and says so: absence is not a pass. Both are the
finished files, where the capture measured before its video records were
added: a video appendix's pages can raise the share a little, and a video's
text added to `content.txt` and not printed can lower it a little (a watch
page measured 350 of 4,792 runs here and 350 of 4,501 in its own manifest).
That watch page is low by either count: its related videos' titles print as
blank boxes (P97-2), and its manifest says so.

Exit 0 when every PDF was measured at or above `LOW_SHARE` (or was too short
to judge), 1 when one measured below it, 2 when one could not be measured.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from webshot.render.pdfium_bridge import PdfInkError, page_texts
from webshot.render.text_layer import LOW_SHARE, MIN_RUNS, printed_share

#: The bundle file the measure reads, as `create_ai_bundle` names it.
BUNDLE_TEXT = "content.txt"


@dataclass(frozen=True)
class Measured:
    """One PDF's measure, or why there is none."""

    pdf: str
    #: Where the bundle's text came from: "embedded", the `.ai` directory's
    #: path, or "" when it was not found.
    source: str
    runs: int | None = None
    printed: int | None = None
    share: float | None = None
    #: "ok", "low", "too short" or "not measured".
    verdict: str = "not measured"
    reason: str = ""


def bundle_text(pdf: Path) -> tuple[str, str] | None:
    """The bundle's `content.txt` for `pdf`, and where it was found."""
    try:
        attachments = PdfReader(str(pdf)).attachments
    except (PyPdfError, OSError, ValueError) as exc:
        raise PdfInkError(f"pypdf could not read {pdf.name}: {exc}") from exc
    embedded = attachments.get(BUNDLE_TEXT)
    if embedded:
        # One file of a name in a WebShot PDF; the first is the one written.
        return embedded[0].decode("utf-8"), "embedded"
    beside = pdf.with_suffix(".ai") / BUNDLE_TEXT
    if beside.is_file():
        return beside.read_text(encoding="utf-8"), str(beside.parent)
    return None


def measure(pdf: Path) -> Measured:
    """`printed_share` for one finished capture."""
    try:
        found = bundle_text(pdf)
        if found is None:
            return Measured(
                pdf=str(pdf),
                source="",
                reason=f"no {BUNDLE_TEXT} embedded in it, and no "
                f"{pdf.with_suffix('.ai').name}/{BUNDLE_TEXT} beside it",
            )
        text, source = found
        share = printed_share(text, page_texts(pdf))
    except (PdfInkError, OSError, UnicodeDecodeError) as exc:
        return Measured(pdf=str(pdf), source="", reason=str(exc))
    verdict = (
        "too short"
        if share.runs < MIN_RUNS
        else "low"
        if share.share < LOW_SHARE
        else "ok"
    )
    return Measured(
        pdf=str(pdf),
        source=source,
        runs=share.runs,
        printed=share.printed,
        share=round(share.share, 4),
        verdict=verdict,
    )


def line(result: Measured) -> str:
    """One PDF's result, as the terminal prints it."""
    name = Path(result.pdf).name
    if result.verdict == "not measured":
        return f"{name}: not measured: {result.reason}"
    counted = f"{result.printed:,} of {result.runs:,} runs printed ({result.share:.0%})"
    note = {
        "ok": "",
        "low": f"; under {LOW_SHARE:.0%}: the PDF shows much less than its bundle",
        "too short": f"; under {MIN_RUNS} runs, too short for a share to mean much",
    }[result.verdict]
    where = "" if result.source == "embedded" else f" (text from {result.source})"
    return f"{name}: {counted}{note}{where}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdfs", nargs="+", type=Path, metavar="PDF")
    parser.add_argument(
        "--json", action="store_true", help="one JSON object per PDF, one per line"
    )
    args = parser.parse_args(argv)
    results = [measure(pdf) for pdf in args.pdfs]
    for result in results:
        print(json.dumps(asdict(result)) if args.json else line(result))
    if any(r.verdict == "not measured" for r in results):
        return 2
    return 1 if any(r.verdict == "low" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
