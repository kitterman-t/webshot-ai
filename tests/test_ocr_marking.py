"""Recognized text is marked as OCR wherever a reader meets it (docs/09 P20-4).

docs/11 principle 7 says machine-recognized text is labeled as OCR in the
artifacts, with its confidence. The picture annotation in `content.json` said
so, but `content.md`, `content.txt` and the chunks printed the recognized words
bare, as if the page had written them. A new user's captures of mirrored
Wikipedia pages printed text recognized at 20% to 39% confidence that way.

`recognized_text_snapshot.html` is a snapshot as the capture writes it: one
picture of a harvested asset between two paragraphs the page wrote. Run
through the bridge with low-confidence noise for that asset, every surface
must carry the noise only inside its marker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from webshot.capture.visuals import VisualAsset
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import extract, ocr_reading
from webshot.ocr.engine import OCRResult

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.harness import portable  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recognized_text_snapshot.html"

NOISE = "lI1 ~~ Ww\nsettngs | | dashbrd\n= 0 o"

MARKED = (
    "[OCR text from asset-001, machine-recognized by Tesseract, mean "
    f"confidence 27%:]\n{NOISE}\n[End of OCR text from asset-001]"
)


def _extracted() -> tuple[str, str, list[str]]:
    asset = VisualAsset(
        id="asset-001",
        file="assets/asset-001.png",
        kind="img",
        width=800,
        height=450,
        ocr=OCRResult(text=NOISE, confidence=27.4, language="eng", engine="Tesseract"),
        sha256="cd" * 32,
    )
    result = extract(FIXTURE.read_text("utf-8"), [asset])
    chunks = [record["text"] for record in build_chunk_records(result.chunk_seeds)]
    return result.markdown, result.text, chunks


def test_the_marker_names_the_asset_engine_and_confidence() -> None:
    assert (
        ocr_reading(NOISE, asset_id="asset-001", engine="Tesseract", confidence=27.4)
        == MARKED
    )
    assert ocr_reading("x", asset_id="asset-002", engine=None, confidence=None) == (
        "[OCR text from asset-002, machine-recognized, no confidence reported:]\n"
        "x\n[End of OCR text from asset-002]"
    )


def test_recognized_text_is_marked_in_content_md_and_content_txt() -> None:
    markdown, text, _ = _extracted()
    for surface in (markdown, text):
        assert MARKED in surface
        # Nowhere outside its marker.
        assert NOISE.splitlines()[1] not in surface.replace(MARKED, "")
        # The page's own words stay unmarked.
        assert "Text the page wrote after the picture." in surface


def test_recognized_text_is_marked_in_every_chunk_that_holds_it() -> None:
    _, _, chunks = _extracted()
    holding = [chunk for chunk in chunks if NOISE.splitlines()[1] in chunk]
    # The text chunk the picture sits in, and the asset's own figure chunk.
    assert len(holding) == 2, chunks
    for chunk in holding:
        assert MARKED in chunk, chunk


def test_the_golden_harness_masks_the_confidence_it_cannot_compare() -> None:
    """The figure moves with the Tesseract build, as the words do (P7-13)."""
    markdown, _, chunks = _extracted()
    masked = portable(
        {
            "bundle/content.md": markdown,
            "bundle/chunks.jsonl": "\n".join(
                json.dumps({"text": chunk}) for chunk in chunks
            ),
        }
    )
    for name in ("bundle/content.md", "bundle/chunks.jsonl"):
        assert "mean confidence <OCR-CONFIDENCE>%:]" in masked[name], name
        assert "confidence 27%" not in masked[name], name
