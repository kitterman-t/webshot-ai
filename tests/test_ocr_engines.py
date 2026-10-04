"""The `OcrEngine` seam and its two implementations (docs/05 task 3.5).

The protocol only earns its place if a second engine really does drop in, so
the RapidOCR case is a real recognition against a real fixture rather than a
mock — and it runs offline, because RapidOCR ships its ONNX models inside the
wheel (docs/09 P3-4). It skips when the extra is not installed, which is what
a default install looks like.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import webshot.ocr.tesseract as tesseract_module
from webshot.bundle.manifest import WebChunkV3, WebManifestV3
from webshot.cli import DEFAULT_OCR_PSM, build_parser, options_from_args
from webshot.errors import UsageError
from webshot.ocr.engine import OcrEngine, OCRResult, resolve_engine
from webshot.ocr.tesseract import TesseractEngine, tesseract_executable

REPO_ROOT = Path(__file__).resolve().parents[1]
#: A page image with known words in it — the same one the protected-viewer
#: golden case proves recognition against.
OCR_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "viewer" / "page-001.png"
SENTINEL = "SENTINEL PAGE ONE ALPHA"

RAPID_INSTALLED = importlib.util.find_spec("rapidocr") is not None
needs_rapid = pytest.mark.skipif(
    not RAPID_INSTALLED, reason="install webshot[ocr-rapid] to run this"
)


def _options(*argv: str) -> object:
    return options_from_args(
        build_parser().parse_args(["tests/fixtures/sample_notes.txt", *argv])
    )


# --------------------------------------------------------------------------- #
# The seam
# --------------------------------------------------------------------------- #


def test_tesseract_is_the_default_engine() -> None:
    engine = resolve_engine("tesseract")
    assert isinstance(engine, TesseractEngine)
    assert (engine.name, engine.label) == ("tesseract", "Tesseract")
    assert _options().ocr_engine == "tesseract"


def test_an_unknown_engine_is_a_usage_error() -> None:
    with pytest.raises(UsageError, match="Unknown OCR engine"):
        resolve_engine("ocrmagic")


@pytest.mark.parametrize("name", ("tesseract", "rapid"))
def test_every_engine_satisfies_the_protocol(name: str) -> None:
    if name == "rapid" and not RAPID_INSTALLED:
        pytest.skip("install webshot[ocr-rapid] to run this")
    engine: OcrEngine = resolve_engine(name)
    assert isinstance(engine.name, str) and isinstance(engine.label, str)
    reason = engine.unavailable_reason()
    assert reason is None or isinstance(reason, str)


def test_an_absent_engine_reports_a_reason_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing recognition degrades to a warning (docs/04-spec.md §5.7)."""
    from webshot.ocr import tesseract

    monkeypatch.setattr(tesseract, "tesseract_executable", lambda: None)
    reason = TesseractEngine().unavailable_reason()
    assert reason and "not installed" in reason


# --------------------------------------------------------------------------- #
# `--ocr-psm` under `--ocr-engine rapid`
# --------------------------------------------------------------------------- #


def test_an_unrequested_psm_is_not_reported_as_ignored() -> None:
    assert _options("--ocr-engine", "rapid").ocr_psm_explicit is False


@pytest.mark.parametrize("psm", ("6", "11"))
def test_a_requested_psm_is_recorded_as_a_request(psm: str) -> None:
    """Including the value that happens to equal the default: it was asked for."""
    options = _options("--ocr-engine", "rapid", "--ocr-psm", psm)
    assert options.ocr_psm_explicit is True
    assert options.ocr_psm == int(psm)


def test_the_default_psm_reaches_the_options_as_a_plain_int() -> None:
    """The sentinel is a CLI detail and must not travel into the bundle."""
    options = _options()
    assert options.ocr_psm == DEFAULT_OCR_PSM
    assert type(options.ocr_psm) is int


# --------------------------------------------------------------------------- #
# Recognition
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(tesseract_executable() is None, reason="tesseract is not installed")
def test_tesseract_recognizes_the_fixture() -> None:
    result = asyncio.run(
        TesseractEngine().recognize(OCR_FIXTURE, language="eng", psm=11)
    )
    assert SENTINEL in result.text
    assert result.engine == "Tesseract"
    assert result.language == "eng"


@needs_rapid
def test_rapidocr_recognizes_the_same_fixture_with_no_system_binary() -> None:
    from webshot.ocr.rapid import RapidOcrEngine

    engine = RapidOcrEngine()
    assert engine.unavailable_reason() is None
    result = asyncio.run(engine.recognize(OCR_FIXTURE, language="eng", psm=11))
    assert isinstance(result, OCRResult)
    assert SENTINEL in result.text
    assert result.engine == "RapidOCR"
    # Confidence is republished on Tesseract's percentage scale, not RapidOCR's
    # 0-1 one, because the bundle format documents exactly one scale.
    assert result.confidence is not None and 0 <= result.confidence <= 100
    # The bundled models are not told which language to expect, so claiming
    # the requested one would be a setting that was never applied.
    assert result.language is None


@needs_rapid
@pytest.mark.browser
def test_a_capture_under_rapid_publishes_a_valid_bundle(tmp_path: Path) -> None:
    """docs/05 Phase 3 acceptance: the engine has to work as a whole capture.

    The fixture's chart is a `<canvas>`, so its words exist only as pixels —
    the same reason the golden corpus uses it as an OCR sentinel — which makes
    this a real end-to-end proof that a second engine reaches the bundle.
    """
    pdf = tmp_path / "rapid.pdf"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            "tests/fixtures/professional_page.html",
            "--output",
            str(pdf),
            "--ocr-engine",
            "rapid",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    bundle = pdf.with_suffix(".ai")
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    WebManifestV3.model_validate(manifest)
    for line in (bundle / "chunks.jsonl").read_text("utf-8").splitlines():
        WebChunkV3.model_validate_json(line)

    assert manifest["ocr"]["engine"] == "RapidOCR"
    # Neither setting reaches RapidOCR, so the manifest claims neither.
    assert manifest["ocr"]["language"] is None
    assert manifest["ocr"]["page_segmentation_mode"] is None

    assets = json.loads((bundle / "assets.json").read_text("utf-8"))
    recognized = [a for a in assets["visual_assets"] if a["ocr"]["text"]]
    assert recognized, "nothing was recognized, so nothing proves the engine ran"
    assert all(a["ocr"]["engine"] == "RapidOCR" for a in recognized)


@needs_rapid
def test_rapidocr_models_ship_in_the_wheel_so_recognition_needs_no_network() -> None:
    import rapidocr

    models = Path(rapidocr.__file__).parent / "models"
    assert sorted(path.name for path in models.glob("*.onnx")), (
        "RapidOCR would have to download models, which no test may rely on"
    )


# --------------------------------------------------------------------------- #
# "Can this engine run here" includes the language it was asked for
# --------------------------------------------------------------------------- #


def test_a_missing_language_pack_makes_tesseract_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`unavailable_reason` checked only whether the binary exists.

    So `--require-ocr --ocr-language spa` passed the preflight on an
    English-only installation, every per-asset recognition then failed, each
    failure became a warning, and the command exited 0 having recognized
    nothing — where the taxonomy says an unavailable required engine is an
    early exit 6 (docs/09 P8-44).
    """
    # The binary is stubbed as well as its language list: the question is the
    # language check, and a host without Tesseract — the weekly Windows run —
    # otherwise answers "not installed" before reaching it (docs/09 P10-24).
    monkeypatch.setattr(tesseract_module, "tesseract_executable", lambda: "tesseract")
    monkeypatch.setattr(tesseract_module, "installed_languages", lambda: {"eng"})
    engine = TesseractEngine()
    reason = engine.unavailable_reason(language="spa")
    assert reason and "spa" in reason
    assert "eng" in reason, "the message must say what IS installed"
    assert engine.unavailable_reason(language="eng") is None
    # A multi-language request needs all of its parts.
    assert engine.unavailable_reason(language="eng+spa")
    assert engine.unavailable_reason(language="") is None


def test_an_unlistable_tesseract_is_not_read_as_having_no_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing a capture because `--list-langs` did not answer would be worse
    than the gap it closes."""
    monkeypatch.setattr(tesseract_module, "tesseract_executable", lambda: "tesseract")
    monkeypatch.setattr(tesseract_module, "installed_languages", lambda: set())
    assert TesseractEngine().unavailable_reason(language="spa") is None


@needs_rapid
def test_rapid_accepts_a_language_and_has_no_packs_to_miss() -> None:
    """One multilingual model, so there is no second question to ask."""
    from webshot.ocr.rapid import RapidOcrEngine

    engine = RapidOcrEngine()
    assert engine.unavailable_reason(language="spa") == engine.unavailable_reason()
