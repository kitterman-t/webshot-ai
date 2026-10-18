"""One image an engine cannot read is one warning, not the end of the capture.

The visual-asset stage recognizes each asset in turn and records a failure as
that asset's warning (docs/09 P8-44). It caught `RuntimeError`, `OSError` and
`TimeoutExpired`, which is what running Tesseract raises. RapidOCR raises its
own `ONNXRuntimeError` when onnxruntime fails during recognition, wrapping
onnxruntime's error, and both derive from `Exception` alone, so one image that
could not be read ended the capture (docs/09 P10-33, P90). Tesseract's path
had one such error too: its output decoded under a locale that is not UTF-8.

Each bridge now turns its engine's failure into `OcrRecognitionError`. These
tests make recognition fail on the second of three charts, after the engine
has started and read the first, and require the warning to name that asset
and the error while the other two are still read.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import async_playwright

import webshot.ocr.tesseract as tesseract_module
from webshot.capture.visuals import capture_visual_assets
from webshot.errors import OcrUnavailableError
from webshot.ocr.engine import OcrEngine, OcrRecognitionError
from webshot.ocr.rapid import RapidOcrEngine
from webshot.ocr.tesseract import OCRPage, TesseractEngine

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ocr_recognition_failure.html"
OCR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "viewer" / "page-001.png"
#: The image recognition fails on: the second of the fixture's three charts.
FAILING = "asset-002.png"
READ = {"asset-001.png": "FIRST CHART", "asset-003.png": "THIRD CHART"}


class OnnxFailure(Exception):
    """Shaped like onnxruntime's errors: `Exception` is the only base."""


class EngineFailure(Exception):
    """Shaped like RapidOCR's ONNXRuntimeError: `Exception` is the only base,
    so no handler for `RuntimeError` or `OSError` sees it, and its message is
    the traceback of the onnxruntime error it wraps."""


class Recognizer:
    """Stands in for `RapidOCR()`, built and ready, which fails on one image."""

    def __call__(self, image: str) -> SimpleNamespace:
        name = Path(image).name
        if name == FAILING:
            try:
                raise OnnxFailure(
                    "Non-zero status code returned while running Conv node"
                )
            except OnnxFailure as onnx:
                raise EngineFailure(
                    "Traceback (most recent call last):\n"
                    '  File "/somewhere/rapidocr/inference_engine/onnxruntime/main.py"\n'
                    "OnnxFailure: Non-zero status code returned while running Conv node"
                ) from onnx
        return SimpleNamespace(txts=(READ[name],), scores=(0.9,))


def _fail_rapid(monkeypatch: pytest.MonkeyPatch) -> OcrEngine:
    from webshot.ocr import rapid

    monkeypatch.setattr(rapid, "_recognizer", Recognizer)
    return RapidOcrEngine()


def _fail_tesseract(monkeypatch: pytest.MonkeyPatch) -> OcrEngine:
    """Tesseract printed bytes that are not UTF-8.

    Measured with Python's UTF-8 mode off under `LC_ALL=C`: subprocess then
    decoded the output as ASCII, and a recognized "Café" raised this, past the
    stage's handler (docs/09 P90-2). The bridge has read Tesseract's output as
    UTF-8 since P91, so what still raises it is bytes that are not UTF-8,
    which `tests/test_tesseract_encoding.py` feeds a stand-in Tesseract.
    """

    def recognize_page(
        number: int, image: Path, language: str, psm: int, **_: Any
    ) -> OCRPage:
        if image.name == FAILING:
            raise UnicodeDecodeError("utf-8", b"1\tCaf\xff", 5, 6, "invalid start byte")
        return OCRPage(
            page=number, text=READ[image.name], confidence=90.0, words=[], tsv=""
        )

    monkeypatch.setattr(tesseract_module, "tesseract_executable", lambda: "tesseract")
    monkeypatch.setattr(tesseract_module, "installed_languages", lambda: {"eng"})
    monkeypatch.setattr(tesseract_module, "recognize_page", recognize_page)
    return TesseractEngine()


ENGINES = {
    "rapid": (
        _fail_rapid,
        "OCR failed for asset-002: RapidOCR could not read the image: "
        "EngineFailure, from OnnxFailure: Non-zero status code returned while "
        "running Conv node",
    ),
    "tesseract": (
        _fail_tesseract,
        "OCR failed for asset-002: Tesseract's output could not be decoded: "
        "'utf-8' codec can't decode byte 0xff in position 5: invalid start byte",
    ),
}


def _harvest(directory: Path, engine: OcrEngine, require_ocr: bool) -> Any:
    async def run() -> Any:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                return await capture_visual_assets(
                    page,
                    directory / "assets",
                    max_assets=10,
                    ocr=True,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=engine,
                    require_ocr=require_ocr,
                )
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
@pytest.mark.parametrize("name", sorted(ENGINES))
def test_an_image_the_engine_cannot_read_is_one_warning(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail, warning = ENGINES[name]
    engine = fail(monkeypatch)
    assets, warnings, unsaved = _harvest(tmp_path, engine, require_ocr=False)

    assert warnings == [warning]
    assert [asset.id for asset in assets] == ["asset-001", "asset-002", "asset-003"]
    assert unsaved == 0, "an unread image is still a saved asset"
    # The images either side of it were read, by the engine that was asked.
    assert [asset.ocr.text for asset in assets] == ["FIRST CHART", "", "THIRD CHART"]
    assert [asset.ocr.engine for asset in assets] == [engine.label, None, engine.label]
    assert (tmp_path / "assets" / FAILING).is_file()


@pytest.mark.browser
@pytest.mark.parametrize("name", sorted(ENGINES))
def test_under_require_ocr_an_image_the_engine_cannot_read_is_exit_6(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--require-ocr promises recognized text, so the same failure ends the
    capture with the code that names it, not with the engine's exception."""
    fail, warning = ENGINES[name]
    with pytest.raises(OcrUnavailableError) as raised:
        _harvest(tmp_path, fail(monkeypatch), require_ocr=True)
    assert str(raised.value) == (
        f"{warning} --require-ocr was given, so this is a failure rather than "
        "a warning."
    )
    assert isinstance(raised.value.__cause__, OcrRecognitionError)


@pytest.mark.skipif(
    tesseract_module.tesseract_executable() is None,
    reason="tesseract is not installed",
)
def test_tesseracts_own_failure_reaches_the_stage_as_written(tmp_path: Path) -> None:
    """A Tesseract that ran and exited non-zero: the stage already caught
    this, and the warning keeps Tesseract's own words for it."""
    truncated = tmp_path / FAILING
    truncated.write_bytes(OCR_FIXTURE.read_bytes()[:300])
    with pytest.raises(OcrRecognitionError) as raised:
        asyncio.run(TesseractEngine().recognize(truncated, language="eng", psm=11))
    assert type(raised.value.__cause__) is RuntimeError
    assert str(raised.value) == str(raised.value.__cause__) != ""


needs_rapid = pytest.mark.skipif(
    importlib.util.find_spec("rapidocr") is None,
    reason="install webshot[ocr-rapid] to run this",
)


@needs_rapid
def test_rapidocrs_own_recognition_error_stops_at_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real engine, with onnxruntime failing while it recognizes.

    RapidOCR wraps onnxruntime's error in its `ONNXRuntimeError`, whose
    message is the whole traceback, file paths on this machine included. The
    bridge's error names both and keeps one line of the cause, because the
    message becomes a manifest warning.
    """
    from onnxruntime.capi.onnxruntime_pybind11_state import Fail

    from webshot.ocr import rapid

    session = rapid._recognizer().text_rec.session.session

    def run(*args: object, **kwargs: object) -> object:
        raise Fail(
            "[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while "
            "running Conv node. Name:'Conv.0' Status Message: injected"
        )

    monkeypatch.setattr(session, "run", run)
    with pytest.raises(OcrRecognitionError) as raised:
        asyncio.run(RapidOcrEngine().recognize(OCR_FIXTURE, language="eng", psm=11))
    assert type(raised.value.__cause__).__name__ == "ONNXRuntimeError"
    assert str(raised.value) == (
        "RapidOCR could not read the image: ONNXRuntimeError, from Fail: "
        "[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while "
        "running Conv node. Name:'Conv.0' Status Message: injected"
    )
