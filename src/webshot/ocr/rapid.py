"""The one module that imports RapidOCR (CONTRIBUTING rule 1).

`webshot[ocr-rapid]` exists for machines where Tesseract cannot be installed:
RapidOCR is pip-only, runs its detection and recognition models on ONNX
Runtime, and — unlike most of its neighbours — ships those models inside the
wheel, so it recognizes text with no download and no network (docs/09 P3-4).

Two differences from Tesseract are visible in the records this produces, and
both are deliberate:

- **`--ocr-psm` has no counterpart.**  Page segmentation is Tesseract's model
  of a page; RapidOCR detects text regions instead.  The CLI warns and ignores
  the flag rather than pretending to apply it.
- **`language` is left unset.**  The bundled models read Latin and Chinese
  script without being told which to expect, so recording the requested
  Tesseract language string against RapidOCR's output would claim a setting
  that was never applied.
"""

from __future__ import annotations

import asyncio
import logging
from functools import cache
from pathlib import Path
from typing import Any

from .engine import OCRResult

LOGGER = logging.getLogger("webshot")

_RAPIDOCR_LOGGER = "RapidOCR"


class _ModelChatter(logging.Filter):
    """Keep RapidOCR's model-loading narration out of an ordinary capture.

    RapidOCR attaches its own stderr handler at INFO and announces every ONNX
    model it loads, which would put coloured lines in front of each run.
    Setting the logger's level does not hold: each of its modules builds a
    `Logger("RapidOCR")` on first use, and that constructor calls `setLevel`
    again — so a level chosen here is raised back by the next component to
    start. A filter on the logger survives, because nothing upstream removes
    one. `--verbose` still shows the narration.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return (
            record.levelno >= logging.WARNING
            or LOGGER.getEffectiveLevel() <= logging.DEBUG
        )


@cache
def _recognizer() -> Any:
    """Build the recognizer once: loading three ONNX models is not free."""
    from rapidocr import RapidOCR

    logging.getLogger(_RAPIDOCR_LOGGER).addFilter(_ModelChatter())
    return RapidOCR()


def _recognize_sync(image: Path) -> OCRResult:
    output = _recognizer()(str(image))
    lines: tuple[str, ...] = tuple(getattr(output, "txts", None) or ())
    scores: tuple[float, ...] = tuple(getattr(output, "scores", None) or ())
    if not lines:
        return OCRResult(engine=RapidOcrEngine.label)
    return OCRResult(
        text="\n".join(line.strip() for line in lines if line.strip()).strip(),
        # Tesseract reports confidence as a percentage and this bundle format
        # publishes one scale, so RapidOCR's 0-1 scores are converted, not
        # republished on a second scale nobody documented.
        confidence=round(sum(scores) / len(scores) * 100, 2) if scores else None,
        language=None,
        engine=RapidOcrEngine.label,
    )


class RapidOcrEngine:
    """`--ocr-engine rapid`."""

    name = "rapid"
    label = "RapidOCR"

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        """Neither setting reaches this engine, so the manifest claims neither."""
        return {"language": None, "page_segmentation_mode": None}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        # `language` is accepted and ignored: RapidOCR ships one multilingual
        # model and has no per-language packs to be missing, so there is no
        # second question to ask (docs/04-spec.md §1.1).
        try:
            _recognizer()
        except ImportError:
            return (
                "RapidOCR is not installed. Install webshot[ocr-rapid] from "
                "your checkout: uv sync --extra ocr-rapid (name any other "
                "extras you use too)"
            )
        except Exception as exc:  # a model that will not load is not a crash
            return f"RapidOCR could not start: {exc}"
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return await asyncio.to_thread(_recognize_sync, image)
