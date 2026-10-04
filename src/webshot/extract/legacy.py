"""The v2.2 extraction serializers, kept alive for `--legacy-bundle` only.

Phase 2 replaced these with docling (extract/docling_bridge.py); this module is
the escape hatch docs/05 task 2.5 promises for exactly one release: it emits
the v2-format `content.json` and `chunks.jsonl` under `legacy/` inside a v3
bundle, validated against the frozen v2 schemas.  **Ships in v3.0, removed in
v3.1** together with the frozen v2 contract models in bundle/manifest.py and
the parity baselines.

The code is v2.2's, moved verbatim (minus the markdown/table exports the
legacy flag does not emit) so the legacy output is the real v2 output, not a
reconstruction.  Do not improve it; delete it at v3.1.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from ..capture.visuals import VisualAsset

LEGACY_SCHEMA_VERSION = "1.0"


def _markdown_for_block(block: dict[str, Any]) -> str:
    kind = block["type"]
    if kind == "heading":
        return f"{'#' * block['level']} {block['text']}"
    if kind == "paragraph":
        return str(block["text"])
    if kind == "blockquote":
        return "\n".join(f"> {line}" for line in block["text"].splitlines())
    if kind == "code":
        return f"```text\n{block['text']}\n```"
    if kind == "list":
        return "\n".join(
            f"{number + 1}. {item}" if block["ordered"] else f"- {item}"
            for number, item in enumerate(block["items"])
        )
    if kind == "definitions":
        return "\n\n".join(
            f"**{entry['term']}**\n\n{entry['definition']}"
            for entry in block["entries"]
        )
    if kind == "table":
        rows = [
            [cell["text"].replace("|", "\\|") for cell in row] for row in block["rows"]
        ]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
        return (
            f"**{block['caption']}**\n\n" if block.get("caption") else ""
        ) + "\n".join(lines)
    if kind == "figure":
        references = "\n".join(
            f"![{block.get('caption') or asset_id}](assets/{asset_id}.png)"
            for asset_id in block.get("assetIds", [])
        )
        return "\n\n".join(
            part for part in (references, block.get("caption", "")) if part
        )
    if kind == "form-value":
        return f"**{block['name']}:** {block['value']}"
    if kind == "embedded-media":
        lines = [
            f"**Embedded {block['mediaType']}:** {block.get('title') or 'Untitled'}",
            f"Source: {block.get('sourceUrl') or 'not exposed'}",
        ]
        lines.extend(
            f"Track ({track.get('kind') or 'unknown'}, {track.get('language') or 'unspecified'}): {track.get('url') or 'not exposed'}"
            for track in block.get("tracks", [])
        )
        return "\n\n".join(lines)
    return str(block.get("text", ""))


def build_chunks(
    blocks: list[dict[str, Any]], source: str, target_characters: int = 1800
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    headings: list[str] = []
    current: list[str] = []
    block_ids: list[str] = []
    chunk_heading_path: list[str] = []

    def split_text(text: str) -> list[str]:
        parts: list[str] = []
        remaining = text.strip()
        while len(remaining) > target_characters:
            cut = remaining.rfind("\n", 0, target_characters + 1)
            if cut < target_characters // 2:
                cut = remaining.rfind(" ", 0, target_characters + 1)
            if cut < target_characters // 2:
                cut = target_characters
            parts.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            parts.append(remaining)
        return parts

    def flush() -> None:
        if not current:
            return
        text = "\n\n".join(current).strip()
        chunks.append(
            {
                "id": f"chunk-{len(chunks) + 1:05d}",
                "source": source,
                "heading_path": list(chunk_heading_path),
                "block_ids": list(block_ids),
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        current.clear()
        block_ids.clear()
        chunk_heading_path.clear()

    for block in blocks:
        if block["type"] == "heading":
            level = block["level"]
            headings[:] = headings[: level - 1]
            headings.append(block["text"])
        rendered_parts = split_text(_markdown_for_block(block))
        for rendered in rendered_parts:
            if (
                current
                and sum(len(part) for part in current) + len(rendered)
                > target_characters
            ):
                flush()
            if not current:
                chunk_heading_path.extend(headings)
            current.append(rendered)
            block_ids.append(block["id"])
            if len(rendered) >= target_characters:
                flush()
    flush()
    return chunks


def append_visual_chunks(
    chunks: list[dict[str, Any]], assets: list[VisualAsset], source: str
) -> None:
    for asset in assets:
        parts = [
            f"Visual asset {asset.id}",
            asset.caption,
            asset.alt,
            asset.aria_label,
            asset.title,
            asset.ocr.text,
        ]
        text = "\n\n".join(dict.fromkeys(part for part in parts if part)).strip()
        if not text:
            text = f"Visual asset {asset.id} ({asset.kind})"
        chunks.append(
            {
                "id": f"chunk-{len(chunks) + 1:05d}",
                "type": "visual",
                "source": source,
                "heading_path": [asset.nearby_heading] if asset.nearby_heading else [],
                "asset_ids": [asset.id],
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )


def _v2_asset(asset: VisualAsset) -> dict[str, Any]:
    """One asset as v2.2 recorded it. `frame_content` is v3's (docs/09 P16-7)."""
    record = asdict(asset)
    del record["frame_content"]
    return record


def legacy_content_json(
    source: str,
    metadata: dict[str, Any],
    blocks: list[dict[str, Any]],
    links: list[dict[str, Any]],
    assets: list[VisualAsset],
) -> str:
    """`legacy/content.json`, exactly as v2.2 wrote `content.json`."""
    return (
        json.dumps(
            {
                "schema_version": LEGACY_SCHEMA_VERSION,
                "source": source,
                "metadata": metadata,
                "blocks": blocks,
                "links": links,
                "visual_assets": [_v2_asset(asset) for asset in assets],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def legacy_chunks_jsonl(
    blocks: list[dict[str, Any]], assets: list[VisualAsset], source: str
) -> str:
    """`legacy/chunks.jsonl`, exactly as v2.2 wrote `chunks.jsonl`."""
    chunks = build_chunks(blocks, source)
    append_visual_chunks(chunks, assets, source)
    return "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
