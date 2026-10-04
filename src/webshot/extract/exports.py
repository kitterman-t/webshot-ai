"""Write the extraction stage's files: docling's serializations, plus CSVs.

Nothing here serializes a document itself — the strings and grids come out of
the bridge already rendered by docling's serializers (docs/05 task 2.2); this
module only decides file names and encodings.  `content.html` is deliberately
absent: the sanitized snapshot has a single producer, the capture stage
(docs/02 §Bundle table).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .docling_bridge import ExtractionResult


def write_exports(result: ExtractionResult, staging: Path) -> int:
    """Write content.{json,md,txt,doctags} and tables/*.csv; returns table count."""
    (staging / "content.json").write_text(
        result.document_json, encoding="utf-8", newline="\n"
    )
    (staging / "content.md").write_text(result.markdown, encoding="utf-8", newline="\n")
    (staging / "content.txt").write_text(result.text, encoding="utf-8", newline="\n")
    (staging / "content.doctags").write_text(
        result.doctags, encoding="utf-8", newline="\n"
    )
    if result.tables:
        tables_directory = staging / "tables"
        tables_directory.mkdir(parents=True, exist_ok=True)
        for index, grid in enumerate(result.tables, 1):
            path = tables_directory / f"table-{index:03d}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(grid)
    return len(result.tables)


def write_chunks(records: list[dict[str, Any]], staging: Path) -> None:
    with (staging / "chunks.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
