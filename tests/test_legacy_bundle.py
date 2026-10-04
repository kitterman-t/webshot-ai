"""`--legacy-bundle` round-trips against the frozen v2 schemas (task 2.5).

The legacy emitters are v2.2's own code (extract/legacy.py), so this is not a
similarity check: given block records shaped like v2's, the emitted
legacy/content.json and legacy/chunks.jsonl must validate against the pydantic
models Phase 0 froze — the same models `schemas/legacy-*.schema.json` are
generated from.  The `article-legacy` golden case holds the end-to-end output
still through v3.0; these tests hold the emitters without a browser.
"""

from __future__ import annotations

import json
from typing import Any

from webshot.bundle.manifest import WebChunk, WebContent
from webshot.capture.visuals import VisualAsset
from webshot.extract.legacy import legacy_chunks_jsonl, legacy_content_json
from webshot.ocr.tesseract import OCRResult

BLOCKS: list[dict[str, Any]] = [
    {
        "id": "block-00001",
        "locator": "body > main > h1",
        "text": "Report",
        "type": "heading",
        "level": 1,
    },
    {
        "id": "block-00002",
        "locator": "body > main > p",
        "text": "A paragraph of real prose.",
        "type": "paragraph",
    },
    {
        "id": "block-00003",
        "locator": "body > main > form > input:nth-of-type(1)",
        "text": "",
        "type": "form-value",
        "name": "account_password",
        "value": "[REDACTED PASSWORD]",
    },
    {
        "id": "block-00004",
        "locator": "body > main > video",
        "text": "",
        "type": "embedded-media",
        "mediaType": "video",
        "sourceUrl": "https://example.com/v.mp4",
        "title": "Training video",
        "tracks": [
            {"kind": "captions", "language": "en", "label": "English", "url": "c.vtt"}
        ],
    },
    {
        "id": "block-00005",
        "locator": "body > main > dl",
        "text": "Term Meaning",
        "type": "definitions",
        "entries": [{"term": "Term", "definition": "Meaning"}],
    },
]

METADATA = {
    "title": "Report",
    "language": "en",
    "description": "",
    "author": "",
    "keywords": "",
    "canonicalUrl": "",
    "publishedTime": "",
}

ASSETS = [
    VisualAsset(
        id="asset-001",
        file="assets/asset-001.png",
        kind="canvas",
        width=640,
        height=240,
        caption="A chart.",
        nearby_heading="Report",
        ocr=OCRResult(
            text="OCR WORDS", confidence=90.0, language="eng", engine="Tesseract"
        ),
        sha256="cd" * 32,
    )
]


def test_legacy_content_validates_against_the_frozen_v2_schema() -> None:
    payload = legacy_content_json(
        "https://example.com/page",
        METADATA,
        BLOCKS,
        [{"text": "a link", "url": "https://example.com/next"}],
        ASSETS,
    )
    content = WebContent.model_validate_json(payload)
    assert content.schema_version == "1.0"
    kinds = [block.type for block in content.blocks]
    assert kinds == [
        "heading",
        "paragraph",
        "form-value",
        "embedded-media",
        "definitions",
    ]


def test_legacy_chunks_validate_against_the_frozen_v2_schema() -> None:
    lines = legacy_chunks_jsonl(BLOCKS, ASSETS, "https://example.com/page").splitlines()
    assert lines
    for line in lines:
        WebChunk.model_validate_json(line)
    parsed = [json.loads(line) for line in lines]
    assert parsed[-1].get("type") == "visual"  # v2's visual chunks survive
    assert any("[REDACTED PASSWORD]" in record["text"] for record in parsed)
