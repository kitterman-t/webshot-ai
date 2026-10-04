"""Chunk records: docling's hierarchical seeds, size-bounded by chonkie.

The chunker gate was resolved empirically (docs/09 S4, ADR-0008): the default
is docling's HierarchicalChunker — no tokenizer, no network — whose output
arrives here as `ChunkSeed`s through the bridge, split to a retrieval-sized
character budget by chonkie's RecursiveChunker with its offline character
tokenizer.  HybridChunker stays behind an extra for users who accept a
tokenizer download.

This module is chonkie's bridge (the only file importing it).  Records follow
spec §2: `page` is null on the web path, `locator` is the leading DocItem
`self_ref` — a stable anchor into `content.json` — and `sha256` is the digest
of the UTF-8 bytes of the `text` field.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .docling_bridge import ChunkSeed

TARGET_CHARACTERS = 1800


def _split(text: str, target_characters: int) -> list[str]:
    if len(text) <= target_characters:
        return [text]
    from chonkie import RecursiveChunker

    chunker = RecursiveChunker(tokenizer="character", chunk_size=target_characters)
    return [part.text.strip() for part in chunker.chunk(text) if part.text.strip()]


def build_chunk_records(
    seeds: list[ChunkSeed], *, target_characters: int = TARGET_CHARACTERS
) -> list[dict[str, Any]]:
    """Spec §2 chunk records, one per (possibly split) seed.

    The text is contextualized the way docling's own `contextualize` spells it
    — the heading path, then the content — with the headings prepended to
    *each* split part, so no part loses its place in the document.
    """
    records: list[dict[str, Any]] = []
    for seed in seeds:
        for part in _split(seed.text, target_characters):
            # A heading whose section has no body is a seed with empty text
            # (P14-45): its record is the heading path alone, not the path
            # plus the trailing newline an empty part would join on.
            text = "\n".join([*seed.headings, part] if part else seed.headings)
            records.append(
                {
                    "id": f"ch_{len(records) + 1:06d}",
                    "text": text,
                    "meta": {
                        "doc_items": list(seed.doc_items),
                        "headings": list(seed.headings),
                        "kind": seed.kind,
                        "page": None,
                        "locator": seed.doc_items[0],
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    },
                }
            )
    return records
