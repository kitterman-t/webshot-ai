"""Tesseract via subprocess — v2's proven OCR path, and the default engine.

docs/03-components.md §5: pytesseract was never a dependency and does not
become one; the binary is invoked directly and its TSV output parsed here.
`TesseractEngine` at the foot of this file is the `OcrEngine` implementation
`--ocr-engine tesseract` selects (docs/05 task 3.5); it adds no behavior, it
gives the existing invocation the shape the protocol asks for.

Two shapes of result, because two callers want different things.  A harvested
visual asset only needs its text and a confidence (`OCRResult`).  A captured
document page also needs **where every word is** (`OCRPage`): the protected
path publishes those coordinates as `ocr/page-NNN.tsv`, which is why it keeps
running Tesseract itself even though OCRmyPDF recognizes the same pages while
building the PDF text layer (docs/03 §4, docs/07 R9).

Only the *shape* differs, so there is one invocation and one TSV parser, and
`OCRResult` is a projection of `OCRPage`.  What the two callers genuinely
disagree about — how long to wait, and whether a missing binary degrades or
stops the run — are parameters.
"""

from __future__ import annotations

import asyncio
import csv
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ..errors import EnvironmentFailure
from .engine import OCRResult

ASSET_TIMEOUT_S = 45
PAGE_TIMEOUT_S = 90

#: How Tesseract names itself in per-asset records and the manifest.
TESSERACT_LABEL = "Tesseract"


def _run_tesseract_sync(image_path: Path, language: str, psm: int) -> OCRResult:
    """Recognize one harvested asset, degrading to empty when Tesseract is absent.

    Missing OCR is a manifest warning on the web path, not a failure
    (docs/04-spec.md §5.7), which is the one thing this does differently from
    page recognition.
    """
    if tesseract_executable() is None:
        return OCRResult()
    page = recognize_page(1, image_path, language, psm, timeout=ASSET_TIMEOUT_S)
    return OCRResult(
        text=page.text,
        confidence=page.confidence,
        language=language,
        engine=TESSERACT_LABEL,
    )


async def run_tesseract(image_path: Path, language: str, psm: int) -> OCRResult:
    return await asyncio.to_thread(_run_tesseract_sync, image_path, language, psm)


# --------------------------------------------------------------------------- #
# Page recognition with word coordinates (the protected-viewer path)
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OCRWord:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int


@dataclass(slots=True)
class OCRPage:
    page: int
    text: str
    confidence: float | None
    words: list[OCRWord]
    tsv: str
    alternate_text: str | None = None
    alternate_confidence: float | None = None
    alternate_psm: int | None = None


_TESSERACT: str | None = None


def tesseract_environment() -> dict[str, str]:
    """The environment every Tesseract run gets: one OpenMP thread per process.

    WebShot already runs pages and assets in parallel, one process each. An
    OpenMP build of Tesseract (the Debian and Ubuntu packages) also starts a
    thread per core in every process, and with several processes at once they
    can stall: on a 4-core Linux machine, `tests/fixtures/viewer/page-001.png`
    took half a second alone, but three of four simultaneous runs were still
    going after 200 seconds, and CI's Linux jobs timed out on that fixture.
    With one thread each, the four finished together in under a second.
    OCRmyPDF sets the same limit for the same reason. A limit the user set
    themselves is kept.
    """
    environment = dict(os.environ)
    environment.setdefault("OMP_THREAD_LIMIT", "1")
    return environment


def tesseract_executable() -> str | None:
    """The Tesseract binary, resolved once rather than once per page.

    Only a *found* path is remembered.  Caching "absent" would outlive the
    problem in a long-running process — a server that was started before
    Tesseract was installed would keep reporting degraded OCR forever.
    """
    global _TESSERACT
    if _TESSERACT is None:
        _TESSERACT = shutil.which("tesseract")
    return _TESSERACT


def recognize_page(
    page_number: int,
    image_path: Path,
    language: str,
    psm: int,
    *,
    timeout: float = PAGE_TIMEOUT_S,
) -> OCRPage:
    """Recognize one image, keeping the raw TSV and every word box."""
    executable = tesseract_executable()
    if not executable:
        raise EnvironmentFailure(
            "Tesseract is required to recognize document pages. Run `webshot "
            "doctor` for how to install it."
        )
    completed = subprocess.run(
        [
            executable,
            str(image_path),
            "stdout",
            "-l",
            language,
            "--psm",
            str(psm),
            "tsv",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=tesseract_environment(),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or f"Tesseract failed on page {page_number}"
        )

    words: list[OCRWord] = []
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    confidences: list[float] = []
    for row in csv.DictReader(completed.stdout.splitlines(), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf", "-1"))
            left = int(row.get("left", "0"))
            top = int(row.get("top", "0"))
            width = int(row.get("width", "0"))
            height = int(row.get("height", "0"))
            tsv_page = int(row.get("page_num", "0"))
            block = int(row.get("block_num", "0"))
            paragraph = int(row.get("par_num", "0"))
            line = int(row.get("line_num", "0"))
        except ValueError:
            continue
        if confidence >= 0:
            confidences.append(confidence)
        lines.setdefault((tsv_page, block, paragraph, line), []).append(text)
        words.append(
            OCRWord(
                text=text,
                confidence=confidence,
                left=left,
                top=top,
                width=width,
                height=height,
                block=block,
                paragraph=paragraph,
                line=line,
            )
        )
    return OCRPage(
        page=page_number,
        text="\n".join(" ".join(parts) for parts in lines.values()).strip(),
        confidence=round(sum(confidences) / len(confidences), 2)
        if confidences
        else None,
        words=words,
        tsv=completed.stdout,
    )


def recognize_pages(
    pages: Sequence[Path], language: str, psm: int, workers: int
) -> list[OCRPage]:
    """Recognize every page, retrying weak pages under a second segmentation mode.

    A page the primary mode read poorly is read again with a different layout
    assumption; when the two readings disagree substantially and the second one
    is plausible, it is carried alongside rather than replacing the first.  The
    consumer decides which to trust — the original image is in the PDF either
    way.
    """
    tasks = [(index, path, language, psm) for index, path in enumerate(pages, 1)]
    worker_count = max(1, min(workers, 8))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda args: recognize_page(*args), tasks))
    results = sorted(results, key=lambda page: page.page)

    alternate_psm = 6 if psm != 6 else 11
    alternate_tasks = [
        (result.page, pages[result.page - 1], language, alternate_psm)
        for result in results
        if result.confidence is None or result.confidence < 80
    ]
    if alternate_tasks:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            alternates = list(
                executor.map(lambda args: recognize_page(*args), alternate_tasks)
            )
        by_page = {result.page: result for result in results}
        for alternate in alternates:
            primary = by_page[alternate.page]
            primary_normalized = re.sub(r"\W+", "", primary.text).lower()
            alternate_normalized = re.sub(r"\W+", "", alternate.text).lower()
            similarity = SequenceMatcher(
                None, primary_normalized, alternate_normalized
            ).ratio()
            average_token_length = len(alternate.text) / max(1, len(alternate.words))
            plausible_length = len(alternate.text) <= max(
                500, int(len(primary.text) * 2.5)
            )
            if (
                alternate.text
                and similarity < 0.92
                and average_token_length <= 40
                and plausible_length
            ):
                primary.alternate_text = alternate.text
                primary.alternate_confidence = alternate.confidence
                primary.alternate_psm = alternate_psm
    return results


def complete_text(page: OCRPage) -> str:
    """The full human reading of a page: primary text, plus any alternate."""
    text = page.text.strip() or (
        "[No reliable text was detected on this page. Analyze the original "
        "source-page image visually.]"
    )
    if page.alternate_text:
        text += (
            "\n\n[Alternate OCR reading for low-confidence or complex layout; "
            "compare both readings with the original visual page.]\n"
            + page.alternate_text
        )
    return text


# --------------------------------------------------------------------------- #
# The OcrEngine implementation (docs/05 task 3.5)
# --------------------------------------------------------------------------- #


def installed_languages() -> set[str]:
    """Every language pack this Tesseract can load, or empty if it cannot say.

    Empty means "could not ask" — a Tesseract that does not answer
    `--list-langs` is not a Tesseract with no languages, and refusing a
    capture on that reading would be worse than the gap it closes.
    """
    executable = tesseract_executable()
    if executable is None:
        return set()
    try:
        completed = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {line for line in lines if not line.endswith(":") and " " not in line}


def missing_languages(specification: str) -> list[str]:
    """Which of `eng+spa`'s parts this installation does not have."""
    available = installed_languages()
    if not available:
        return []
    return [part for part in specification.split("+") if part and part not in available]


class TesseractEngine:
    """`--ocr-engine tesseract`, the default."""

    name = "tesseract"
    label = TESSERACT_LABEL

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        return {"language": language, "page_segmentation_mode": psm}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        if tesseract_executable() is None:
            return (
                "Tesseract is not installed; visual assets were saved without OCR text."
            )
        if not language:
            return None
        # A language pack is as much "can this engine run" as the binary is:
        # Tesseract exits non-zero per image for a traineddata file it cannot
        # open, which `capture_visual_assets` turns into a per-asset warning —
        # so the run finished successfully having recognized nothing at all
        # (docs/09 P8-44). Asked once here rather than discovered N times.
        missing = missing_languages(language)
        if missing:
            return (
                f"Tesseract has no language data for {', '.join(missing)}. "
                f"Installed: {', '.join(sorted(installed_languages())) or 'none'}. "
                "Install the pack (macOS: brew install tesseract-lang; "
                "Debian/Ubuntu: apt install tesseract-ocr-<lang>)."
            )
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return await run_tesseract(image, language, psm)
