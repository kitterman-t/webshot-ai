"""Planted defects for the parity harness's own calibration.

A harness that has never caught a planted bug proves nothing — the same
vacuous-pass trap the red team found in the §6.2 redaction test.  Each mutation
takes a bundle's snapshot files, breaks exactly one thing a real extraction
regression could break, and declares which metrics must catch it.  The
self-test (tests/test_parity_selftest.py) runs every mutation against every
applicable baseline case and fails if a defect goes unreported — or if it is
reported under the wrong metric.

The primary set is written against the v2 format because that is what the
frozen baselines are; the engine it calibrates is format-agnostic (it sees
only `BundleView`s).  `V3_MUTATIONS` plants the same defects into real v3
candidates — the recorded format-3 goldens — to prove the v3 reader feeds the
engine correctly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.parity.views import chunk_rows


@dataclass(frozen=True, slots=True)
class Mutation:
    name: str
    #: Metric names that MUST fail when this defect is planted.
    must_fail: frozenset[str]
    #: Metrics allowed to move as a side effect (e.g. removing a block also
    #: removes its tokens).  Everything else MUST keep passing — that is the
    #: "right severity" half of the calibration.
    may_fail: frozenset[str]
    apply: Callable[[dict[str, str]], dict[str, str]]
    #: A mutation only applies where there is something to break.
    applies: Callable[[dict[str, str]], bool]


def _content(files: dict[str, str]) -> dict[str, Any]:
    content: dict[str, Any] = json.loads(files["bundle/content.json"])
    return content


def _write_content(files: dict[str, str], content: dict[str, Any]) -> dict[str, str]:
    result = dict(files)
    result["bundle/content.json"] = json.dumps(content, ensure_ascii=False, indent=2)
    return result


def _blocks_of(files: dict[str, str], kind: str) -> list[dict[str, Any]]:
    return [b for b in _content(files)["blocks"] if b["type"] == kind]


def _scrub_refs(files: dict[str, str], removed_ids: set[str]) -> dict[str, str]:
    """Keep chunks consistent with a document that lost items.

    A real extraction regression produces its chunks from the already-broken
    document, so every chunk still resolves — the loss shows up in the content
    metrics, not as dangling provenance.  Post-hoc mutations have to imitate
    that: dropped ids leave the chunks' reference lists, and a chunk whose
    references are all gone disappears with them.
    """
    result = dict(files)
    lines = []
    for line in chunk_rows(result):
        chunk = json.loads(line)
        for key in ("block_ids", "asset_ids"):
            if key in chunk:
                kept = [ref for ref in chunk[key] if ref not in removed_ids]
                if chunk[key] and not kept:
                    chunk = None  # every source item is gone; so is the chunk
                    break
                chunk[key] = kept
        if chunk is not None:
            lines.append(json.dumps(chunk, ensure_ascii=False))
    result["bundle/chunks.jsonl"] = "\n".join(lines) + "\n"
    return result


def _drop_block(files: dict[str, str], kind: str, position: int = 0) -> dict[str, str]:
    content = _content(files)
    seen = -1
    removed: set[str] = set()
    for index, block in enumerate(content["blocks"]):
        if block["type"] == kind:
            seen += 1
            if seen == position:
                removed.add(block["id"])
                del content["blocks"][index]
                break
    return _scrub_refs(_write_content(files, content), removed)


def _drop_heading(files: dict[str, str]) -> dict[str, str]:
    # The second heading, not the first: dropping h1 would also be a title
    # change, and the point of each mutation is one defect, cleanly attributed.
    return _drop_block(files, "heading", position=1)


def _drop_table_row(files: dict[str, str]) -> dict[str, str]:
    # The published table artifact is the CSV; drop one data row from it.
    result = dict(files)
    name = next(n for n in sorted(result) if n.startswith("bundle/tables/"))
    lines = result[name].splitlines()
    del lines[-1]
    result[name] = "\n".join(lines) + "\n"
    return result


def _drop_password_record(files: dict[str, str]) -> dict[str, str]:
    """The exact vacuous-pass scenario: the redacted record silently vanishes."""
    content = _content(files)
    removed = {
        block["id"]
        for block in content["blocks"]
        if block["type"] == "form-value" and block.get("value") == "[REDACTED PASSWORD]"
    }
    content["blocks"] = [b for b in content["blocks"] if b["id"] not in removed]
    return _scrub_refs(_write_content(files, content), removed)


def _strip_asset(files: dict[str, str]) -> dict[str, str]:
    content = _content(files)
    removed = {content["visual_assets"][0]["id"]}
    del content["visual_assets"][0]
    return _scrub_refs(_write_content(files, content), removed)


def _drop_longest_paragraph(files: dict[str, str]) -> dict[str, str]:
    from tools.parity.views import _without_injected_ocr

    content = _content(files)
    paragraphs = [b for b in content["blocks"] if b["type"] == "paragraph"]
    # Longest by *basis* text: a paragraph that is mostly injected OCR scrubs
    # to nothing, and dropping nothing must not be the planted defect.
    longest = max(
        paragraphs, key=lambda block: len(_without_injected_ocr(block["text"]))
    )
    content["blocks"].remove(longest)
    return _scrub_refs(_write_content(files, content), {longest["id"]})


def _drop_definition_entry(files: dict[str, str]) -> dict[str, str]:
    content = _content(files)
    for block in content["blocks"]:
        if block["type"] == "definitions" and block["entries"]:
            del block["entries"][0]
            break
    return _write_content(files, content)


def _drop_media_track(files: dict[str, str]) -> dict[str, str]:
    content = _content(files)
    for block in content["blocks"]:
        if block["type"] == "embedded-media" and block.get("tracks"):
            block["tracks"] = []
            break
    return _write_content(files, content)


def _drop_metadata_description(files: dict[str, str]) -> dict[str, str]:
    content = _content(files)
    content["metadata"]["description"] = ""
    return _write_content(files, content)


def _rewrite_link(files: dict[str, str]) -> dict[str, str]:
    result = dict(files)
    links = json.loads(result["bundle/links.json"])
    links[0]["url"] = "https://wrong.invalid/swapped"
    result["bundle/links.json"] = json.dumps(links, ensure_ascii=False, indent=2)
    return result


def _break_chunk_provenance(files: dict[str, str]) -> dict[str, str]:
    result = dict(files)
    lines = chunk_rows(result)
    chunk = json.loads(lines[0])
    for key in ("block_ids", "asset_ids", "doc_items"):
        if key in chunk:
            chunk[key] = []
    if "meta" in chunk:
        chunk["meta"]["doc_items"] = []
    lines[0] = json.dumps(chunk, ensure_ascii=False)
    result["bundle/chunks.jsonl"] = "\n".join(lines) + "\n"
    return result


def _drop_first_chunk(files: dict[str, str]) -> dict[str, str]:
    # The first chunk, because it carries document body text; the last is often
    # the visual/OCR chunk, whose tokens are deliberately outside the document
    # text basis and whose loss this metric therefore should not claim to see.
    result = dict(files)
    lines = chunk_rows(result)
    result["bundle/chunks.jsonl"] = "\n".join(lines[1:]) + "\n"
    return result


def _has(name_prefix: str) -> Callable[[dict[str, str]], bool]:
    return lambda files: any(name.startswith(name_prefix) for name in files)


# --------------------------------------------------------------------------- #
# v3-format mutations — planted after the swap, against real v3 candidates,
# to prove the v3 *reader* feeds the engine correctly (the v2 set proves the
# engine itself; a defect the reader never surfaces would pass both).
# --------------------------------------------------------------------------- #


def _assets(files: dict[str, str]) -> dict[str, Any]:
    assets: dict[str, Any] = json.loads(files["bundle/assets.json"])
    return assets


def _write_assets(files: dict[str, str], assets: dict[str, Any]) -> dict[str, str]:
    result = dict(files)
    result["bundle/assets.json"] = json.dumps(assets, ensure_ascii=False, indent=2)
    return result


def _v3_scrub_refs(files: dict[str, str], removed: set[str]) -> dict[str, str]:
    """The v3 spelling of `_scrub_refs`: meta.doc_items instead of block ids."""
    result = dict(files)
    lines = []
    for line in chunk_rows(result):
        chunk = json.loads(line)
        refs = chunk.get("meta", {}).get("doc_items", [])
        kept = [ref for ref in refs if ref not in removed]
        if refs and not kept:
            continue
        if "meta" in chunk:
            chunk["meta"]["doc_items"] = kept
            if kept:
                chunk["meta"]["locator"] = kept[0]
        lines.append(json.dumps(chunk, ensure_ascii=False))
    result["bundle/chunks.jsonl"] = "\n".join(lines) + "\n"
    return result


def _v3_drop_heading(files: dict[str, str]) -> dict[str, str]:
    content = _content(files)
    for index, item in enumerate(content["texts"]):
        if item.get("label") == "section_header":
            removed = item["self_ref"]
            del content["texts"][index]
            return _v3_scrub_refs(_write_content(files, content), {removed})
    return files


def _v3_strip_asset(files: dict[str, str]) -> dict[str, str]:
    assets = _assets(files)
    del assets["visual_assets"][0]
    return _write_assets(files, assets)


def _v3_drop_password_record(files: dict[str, str]) -> dict[str, str]:
    assets = _assets(files)
    assets["form_fields"] = [
        record
        for record in assets["form_fields"]
        if record.get("value") != "[REDACTED PASSWORD]"
    ]
    return _write_assets(files, assets)


def _v3_drop_media_track(files: dict[str, str]) -> dict[str, str]:
    assets = _assets(files)
    for record in assets["embedded_media"]:
        if record.get("tracks"):
            record["tracks"] = []
            break
    return _write_assets(files, assets)


def _v3_drop_page_metadata(files: dict[str, str]) -> dict[str, str]:
    result = dict(files)
    manifest = json.loads(result["bundle/manifest.json"])
    manifest["page_metadata"]["description"] = ""
    result["bundle/manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)
    return result


def _is_v3(files: dict[str, str]) -> bool:
    return "bundle/assets.json" in files


V3_MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "v3-drop-heading",
        must_fail=frozenset({"headings"}),
        may_fail=frozenset({"text_coverage", "chunk_text_coverage"}),
        apply=_v3_drop_heading,
        applies=lambda files: (
            _is_v3(files)
            and any(
                item.get("label") == "section_header"
                for item in _content(files).get("texts", [])
            )
        ),
    ),
    Mutation(
        "v3-strip-asset",
        must_fail=frozenset({"assets"}),
        may_fail=frozenset(),
        apply=_v3_strip_asset,
        applies=lambda files: _is_v3(files) and bool(_assets(files)["visual_assets"]),
    ),
    Mutation(
        "v3-remove-redacted-password-record",
        must_fail=frozenset({"form_fields"}),
        may_fail=frozenset(),
        apply=_v3_drop_password_record,
        applies=lambda files: (
            _is_v3(files) and "[REDACTED PASSWORD]" in files["bundle/assets.json"]
        ),
    ),
    Mutation(
        "v3-drop-media-track",
        must_fail=frozenset({"embedded_media"}),
        may_fail=frozenset(),
        apply=_v3_drop_media_track,
        applies=lambda files: (
            _is_v3(files)
            and any(record.get("tracks") for record in _assets(files)["embedded_media"])
        ),
    ),
    Mutation(
        "v3-drop-page-metadata",
        must_fail=frozenset({"page_metadata"}),
        may_fail=frozenset(),
        apply=_v3_drop_page_metadata,
        applies=lambda files: (
            _is_v3(files)
            and bool(
                json.loads(files["bundle/manifest.json"])
                .get("page_metadata", {})
                .get("description")
            )
        ),
    ),
    # The CSV, link, and chunk-provenance mutations above are format-agnostic
    # and are run against v3 candidates by the self-test as well.
)


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "drop-heading",
        must_fail=frozenset({"headings"}),
        may_fail=frozenset({"text_coverage", "chunk_text_coverage"}),
        apply=_drop_heading,
        applies=lambda files: len(_blocks_of(files, "heading")) >= 2,
    ),
    Mutation(
        "delete-table-row",
        must_fail=frozenset({"tables"}),
        may_fail=frozenset(),
        apply=_drop_table_row,
        applies=_has("bundle/tables/"),
    ),
    Mutation(
        "remove-redacted-password-record",
        must_fail=frozenset({"form_fields"}),
        may_fail=frozenset(),
        apply=_drop_password_record,
        applies=lambda files: "[REDACTED PASSWORD]" in files["bundle/content.json"],
    ),
    Mutation(
        "strip-asset",
        must_fail=frozenset({"assets"}),
        may_fail=frozenset(),
        apply=_strip_asset,
        applies=lambda files: bool(_content(files).get("visual_assets")),
    ),
    Mutation(
        "drop-longest-paragraph",
        must_fail=frozenset({"text_coverage"}),
        may_fail=frozenset({"chunk_text_coverage"}),
        apply=_drop_longest_paragraph,
        applies=lambda files: bool(_blocks_of(files, "paragraph")),
    ),
    Mutation(
        "drop-definition-entry",
        must_fail=frozenset({"definitions"}),
        may_fail=frozenset({"text_coverage", "chunk_text_coverage"}),
        apply=_drop_definition_entry,
        applies=lambda files: bool(_blocks_of(files, "definitions")),
    ),
    Mutation(
        "drop-media-track",
        must_fail=frozenset({"embedded_media"}),
        may_fail=frozenset(),
        apply=_drop_media_track,
        applies=lambda files: any(
            block.get("tracks") for block in _blocks_of(files, "embedded-media")
        ),
    ),
    Mutation(
        "drop-metadata-description",
        must_fail=frozenset({"page_metadata"}),
        may_fail=frozenset(),
        apply=_drop_metadata_description,
        applies=lambda files: bool(
            _content(files).get("metadata", {}).get("description")
        ),
    ),
    Mutation(
        "rewrite-link-url",
        must_fail=frozenset({"links"}),
        may_fail=frozenset(),
        apply=_rewrite_link,
        applies=lambda files: json.loads(files.get("bundle/links.json", "[]")) != [],
    ),
    Mutation(
        "break-chunk-provenance",
        must_fail=frozenset({"chunk_doc_items"}),
        may_fail=frozenset(),
        apply=_break_chunk_provenance,
        applies=lambda files: bool(files.get("bundle/chunks.jsonl", "").strip()),
    ),
    Mutation(
        "drop-first-chunk",
        must_fail=frozenset({"chunk_text_coverage"}),
        may_fail=frozenset(),
        apply=_drop_first_chunk,
        applies=lambda files: len(chunk_rows(files)) >= 2,
    ),
)
