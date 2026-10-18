"""The `OcrEngine` seam: what WebShot needs from a recognizer, and nothing else.

docs/03-components.md §5 keeps Tesseract as the default and names RapidOCR as
the pip-only alternative for environments where a system binary cannot be
installed.  This protocol is what makes that a choice rather than a rewrite:
`--ocr-engine` selects an implementation, the harvested-visual path calls it,
and every per-asset record says which engine produced it.

The seam sits at *asset recognition*, which is the level `--ocr-engine`
actually switches.  The protected-viewer path is deliberately not on it: its
searchable PDF is built by OCRmyPDF, which is Tesseract, and its
`ocr/page-NNN.tsv` records are Tesseract's own word-box format.  Asking that
path to honour a different engine would mean either fabricating TSV or
publishing a text layer from one engine and coordinates from another, so
`--ocr-engine rapid` is warned and ignored there — the same treatment
`--legacy-bundle` gets on the same path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import UsageError


class OcrRecognitionError(RuntimeError):
    """An engine that could run did not read one image.

    Each bridge raises this in place of whatever its engine raised for that
    image, which stops at the bridge with the engine's other types
    (CONTRIBUTING rule 1). The visual-asset stage cannot name an engine's own
    exceptions without importing it, and RapidOCR's, and the onnxruntime
    errors they wrap, derive from `Exception` alone: before this, one image
    RapidOCR failed on ended the capture (docs/09 P10-33, P90). The stage
    records it as that asset's warning and goes on to the next asset, or
    under `--require-ocr` stops with exit 6. The message is one line saying
    what failed, because it becomes the warning.
    """


@dataclass(slots=True)
class OCRResult:
    """One recognized visual asset.

    `engine` is set by whichever engine produced the text and is empty when
    nothing recognized it, which is what the manifest reads to report the
    engine that actually ran rather than the one that was requested.
    """

    text: str = ""
    confidence: float | None = None
    language: str | None = None
    engine: str | None = None


class OcrEngine(Protocol):
    """Recognize the text inside one harvested image."""

    #: The `--ocr-engine` value that selects this engine.
    name: str
    #: How the engine names itself in per-asset records and the manifest.
    label: str

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        """The settings this engine actually used, for the manifest.

        Not the settings that were *requested*: `--ocr-language` is a
        Tesseract language string and `--ocr-psm` is Tesseract's model of a
        page, and an engine that honours neither must not let the bundle claim
        the text was recognized under them (docs/09 P3-7).  The manifest's
        `ocr.engine` is already read back off the per-asset records for the
        same reason.
        """

    def unavailable_reason(self, *, language: str = "") -> str | None:
        """`None` when the engine can run here, else why it cannot.

        A reason is not a failure: on the web path, missing recognition is a
        manifest warning (docs/04-spec.md §5.7), so the caller reports this
        and carries on without OCR.

        `language` is part of "can run here". Checking only whether the binary
        exists made `--require-ocr --ocr-language spa` pass a preflight on an
        English-only installation and then recognize nothing: the per-asset
        failures became warnings and the command exited 0 with no text, where
        the taxonomy says an unavailable required engine is an early exit 6
        (docs/09 P8-44).
        """

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        """The text in *image*.

        Raises `OcrRecognitionError` when the engine ran and could not read
        this image, so that one image is one warning (docs/09 P90).
        """


def resolve_engine(name: str) -> OcrEngine:
    """The engine `--ocr-engine` names, imported only when it is asked for."""
    if name == "tesseract":
        from .tesseract import TesseractEngine

        return TesseractEngine()
    if name == "rapid":
        from .rapid import RapidOcrEngine

        return RapidOcrEngine()
    raise UsageError(f"Unknown OCR engine '{name}'. Choose tesseract or rapid.")
