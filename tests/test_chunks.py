"""Chunk records from seeds: splitting, contextualization, and the schema."""

from __future__ import annotations

import hashlib

from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import ChunkSeed


def _seed(text: str, **overrides: object) -> ChunkSeed:
    values: dict = {
        "text": text,
        "headings": ("Report", "Details"),
        "doc_items": ("#/texts/4",),
        "kind": "text",
    }
    values.update(overrides)
    return ChunkSeed(**values)


def test_records_follow_the_spec_schema() -> None:
    (record,) = build_chunk_records([_seed("Hello world.")])
    assert record["id"] == "ch_000001"
    assert record["text"] == "Report\nDetails\nHello world."
    meta = record["meta"]
    assert meta["doc_items"] == ["#/texts/4"]
    assert meta["headings"] == ["Report", "Details"]
    assert meta["kind"] == "text"
    assert meta["page"] is None  # web path: no print pagination (docs/02)
    assert meta["locator"] == "#/texts/4"
    assert meta["sha256"] == hashlib.sha256(record["text"].encode()).hexdigest()


def test_oversized_seeds_split_and_keep_their_heading_context() -> None:
    long_text = "A sentence that repeats for splitting. " * 120  # ~4700 chars
    records = build_chunk_records([_seed(long_text)])
    assert len(records) > 1
    assert all(record["text"].startswith("Report\nDetails\n") for record in records)
    assert all(
        len(record["text"]) <= 1800 + len("Report\nDetails\n") for record in records
    )
    assert all(record["meta"]["doc_items"] == ["#/texts/4"] for record in records)
    # nothing is lost by the split
    rebuilt = " ".join(
        record["text"].removeprefix("Report\nDetails\n") for record in records
    )
    assert "repeats for splitting" in rebuilt


def test_ids_are_sequential_across_seeds() -> None:
    records = build_chunk_records(
        [
            _seed("one"),
            _seed("two", kind="table", doc_items=("#/tables/0",)),
            _seed("three", kind="figure", doc_items=("#/pictures/0",), headings=()),
        ]
    )
    assert [record["id"] for record in records] == [
        "ch_000001",
        "ch_000002",
        "ch_000003",
    ]
    assert records[1]["meta"]["kind"] == "table"
    assert records[2]["meta"]["kind"] == "figure"
    assert records[2]["text"] == "three"  # no headings, no prefix


def test_a_heading_only_seed_is_its_heading_path_with_no_trailing_newline() -> None:
    """The seed docling_bridge makes for a heading whose section has no body
    (P14-45) has empty text; its record says the heading path and nothing
    after it."""
    (record,) = build_chunk_records([_seed("", doc_items=("#/texts/2",))])
    assert record["text"] == "Report\nDetails"
    assert record["meta"]["headings"] == ["Report", "Details"]
    assert record["meta"]["locator"] == "#/texts/2"
