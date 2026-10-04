"""The line a capture ends on counts one page in the singular (docs/09 P20-5).

Before the fix this capture logged "Created .../notes.pdf (1 pages, 29.8 KB)", and
twelve of the golden cases end on that line. `sample_notes.txt` is the
`text-notes` case's input, which prints on one page.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

from webshot.acquire.router import normalize_source
from webshot.config import CaptureOptions
from webshot.pipeline import convert_url_to_pdf, page_count

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_notes.txt"


def test_one_page_is_counted_in_the_singular() -> None:
    assert page_count(1) == "1 page"
    assert page_count(0) == "0 pages"
    assert page_count(2) == "2 pages"


@pytest.mark.browser
def test_a_one_page_capture_says_1_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = tmp_path / "notes.pdf"
    options = CaptureOptions(
        source=normalize_source(str(FIXTURE)),
        output=output,
        ai_bundle=False,
        ocr=False,
    )
    with caplog.at_level(logging.INFO, logger="webshot"):
        asyncio.run(convert_url_to_pdf(options))
    created = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Created ")
    ]
    assert len(created) == 1, created
    assert re.fullmatch(
        rf"Created {re.escape(str(output))} \(1 page, \d+\.\d KB\)", created[0]
    ), created
