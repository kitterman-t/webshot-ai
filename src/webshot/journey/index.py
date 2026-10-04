"""The corpus index: the entry point an agent is pointed at.

docs/13's requirement is the sharp one — **it must be readable alone and still
be useful**. A file listing is not that. This is the full five-level outline
with every module's synthetic id, title, type, both durations (P9-8 measured
"6 Mins" against a 251.6 s video, so neither is trusted alone), what kind of
video it holds, and the file that holds the module.

And it records every refusal with its reason. An index that lists only what was
captured describes a corpus that does not exist: a reader would conclude the
journey has no assessments, when it has two and the walker refused them. That is
*absence implied*, which is the trap this project keeps catching — so a reader
must be able to tell "there is no assessment here" from "there is one and it was
refused" **from the index alone, without opening anything**.

Nothing here re-walks the journey. Everything comes from what the capture stage
already wrote: the outline, the outcome records, and each module's own manifest.
Re-opening a module to build the index would mean the capture failed to record
something, and that would be the bug to fix instead.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..version import VERSION
from .model import JourneyNode, JourneyOutline

LOGGER = logging.getLogger("webshot.journey")

INDEX_FORMAT = 1


def _module_facts(bundle: Path) -> dict[str, Any]:
    """What a captured module's own manifest already knows.

    Read rather than recomputed. A module that was refused has no bundle and
    therefore no facts — which is not a gap to be filled in, it is the accurate
    record of a module nobody opened.
    """
    manifest = bundle / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Reported by the caller as a warning: a module whose manifest cannot be
        # read is a real defect in the corpus, and an index that silently
        # omitted its facts would describe it as having none.
        return {"unreadable": True}
    videos = data.get("videos") or []
    return {
        "video_tally": data.get("video_tally"),
        "videos": [
            {
                "provider": video.get("provider"),
                "title": video.get("title"),
                "duration_seconds": video.get("duration_seconds"),
                # The provenance marker a reader must meet before acting on the
                # text, not after: an untranscribed video says so here as well
                # as in the README and content.md.
                "transcribed": video.get("transcribed"),
                "transcript_provenance": video.get("transcript_provenance"),
            }
            for video in videos
        ],
        "pdf_pages": (data.get("pdf") or {}).get("pages"),
    }


def _measured_total(videos: list[dict[str, Any]]) -> float | None:
    """The module's measured seconds, or `None` when that cannot be said.

    This field sits beside `duration_authored` and exists to invite P9-8's
    comparison — the authored "6 Mins" against a video measured at 251.6 s. So
    a number here has to describe the module, not one of its parts.

    It used to take the **first** video's duration, which this repository's own
    `lms-module-videos` golden already breaks: two videos, one reporting 81.4 s
    and one reporting nothing, and the index would have published 81.4 as the
    module's measured duration.

    A partial sum was the obvious repair, with the shortfall left to
    `video_tally.untranscribed_media` to imply. It does not work: that counts
    videos with no *transcript*, which is not the same set as videos with no
    *duration* — P9-8's own measurement is an untranscribed Continu video
    reporting 251.6 s. A sibling that happens to correlate on one sample is not
    a statement of incompleteness (P7-8: a count whose job is to be summed must
    be complete).

    So the total is published only when every video contributed to it, and is
    `None` otherwise. `videos[]` sits in the same entry carrying the per-video
    figures, which is where an incomplete answer belongs.
    """
    if not videos:
        return None
    durations = [video.get("duration_seconds") for video in videos]
    if any(duration is None for duration in durations):
        return None
    return sum(float(duration) for duration in durations if duration is not None)


def _node_entry(
    node: JourneyNode,
    *,
    outcomes: dict[str, Any],
    root: Path,
    directory: Path,
    warnings: list[str],
    references: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    from .capture import slug

    entry: dict[str, Any] = {
        "node_id": node.node_id,
        "level": node.level,
        "ordinal": node.ordinal,
        "title": node.title,
    }
    if node.level != "journey":
        entry["path"] = str(directory.relative_to(root))
    if node.level == "group":
        # The prose that exists at no other level (P9-1), named here so the
        # index can point at it rather than a reader having to find it.
        entry["prose"] = node.prose
        entry["prose_file"] = (
            str((directory / "group.md").relative_to(root))
            if (directory / "group.md").is_file()
            else None
        )
    if node.level == "module":
        outcome = outcomes.get(node.node_id)
        status = getattr(outcome, "status", None) or "not-reached"
        entry["module_type"] = node.module_type
        entry["references"] = (references or {}).get(node.node_id, [])
        # Both, per P9-8: the label is what the LMS claims and the measured
        # figure is what the file is, and a corpus that silently picked one
        # would be asserting something nobody checked.
        entry["duration_authored"] = node.duration
        entry["status"] = status
        entry["reason"] = getattr(outcome, "reason", None)
        if status == "captured":
            facts = _module_facts(directory / "bundle")
            if facts.get("unreadable"):
                warnings.append(
                    f"{node.node_id}: the module's manifest at "
                    f"{directory / 'bundle' / 'manifest.json'} could not be read, so "
                    "the index carries no video or duration facts for it"
                )
                facts = {}
            entry["pdf"] = str((directory / "capture.pdf").relative_to(root))
            entry["bundle"] = str((directory / "bundle").relative_to(root))
            entry["video_tally"] = facts.get("video_tally")
            entry["videos"] = facts.get("videos", [])
            entry["duration_measured_seconds"] = _measured_total(
                facts.get("videos", [])
            )
        else:
            # Stated, not omitted. There is no measured duration because
            # nothing was measured, and saying so is different from leaving the
            # field out and letting a reader assume it is zero or unknown.
            entry["pdf"] = None
            entry["bundle"] = None
            entry["video_tally"] = None
            entry["videos"] = []
            entry["duration_measured_seconds"] = None
        return entry

    children = []
    for child in node.children:
        child_directory = (
            directory / slug(child.title, child.ordinal)
            if child.level != "journey"
            else directory
        )
        children.append(
            _node_entry(
                child,
                outcomes=outcomes,
                root=root,
                directory=child_directory,
                warnings=warnings,
                references=references,
            )
        )
    entry["children"] = children
    return entry


def _captured_directories(
    outline: JourneyOutline, outcomes: dict[str, Any], root: Path
) -> dict[str, Path]:
    """`node_id -> module directory`, for the modules that produced a bundle."""
    from .capture import slug

    found: dict[str, Path] = {}

    def walk(node: JourneyNode, directory: Path) -> None:
        for child in node.children:
            child_directory = directory / slug(child.title, child.ordinal)
            if child.level == "module":
                outcome = outcomes.get(child.node_id)
                if getattr(outcome, "status", None) == "captured":
                    found[child.node_id] = child_directory.relative_to(root)
            else:
                walk(child, child_directory)

    walk(outline.root, root)
    return found


def resolve_cross_references(
    outline: JourneyOutline, root: Path, captured: dict[str, Path]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Which other modules each module names, resolved to synthetic ids.

    docs/13: "a module that references another module by title carries that
    module's synthetic id". Titles are the only handle the LMS gives — a module
    row has no address (P12-1) — and **titles are ambiguous by construction**:
    two groups can hold modules with the same title, which is why
    `click_module_js` is panel-scoped.

    So the rule is: an unambiguous title resolves to its `node_id`; an ambiguous
    one resolves to **every** candidate and is marked `ambiguous`. It is never
    narrowed by guessing. A cross-reference that silently picked one of two
    candidates would be a wrong answer wearing the shape of a right one, and
    nothing downstream could tell — the same failure the panel-scoped click
    exists to prevent, one layer up.
    """
    by_title: dict[str, list[str]] = {}
    node_titles: dict[str, str] = {}
    for module in outline.modules():
        by_title.setdefault(module.title.strip(), []).append(module.node_id)
        node_titles[module.node_id] = module.title.strip()

    references: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    for node_id, directory in sorted(captured.items()):
        content = directory / "bundle" / "content.md"
        if not content.is_file():
            continue
        try:
            text = content.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"{node_id}: {content} could not be read ({exc})")
            continue
        found: list[dict[str, Any]] = []
        for title, candidates in sorted(by_title.items()):
            if not title or title not in text:
                continue
            targets = [target for target in candidates if target != node_id]
            if not targets:
                continue  # a module naming itself is not a cross-reference
            entry: dict[str, Any] = {"title": title, "candidates": targets}
            # A title equal to the referring module's OWN title is never
            # resolved. `content.md` opens with the module's heading, so the
            # occurrence is almost certainly that — and it cannot be told apart
            # from a mention of the other module sharing the title. Dropping
            # self and pointing confidently at the twin is precisely the wrong
            # answer in the shape of a right one this rule exists to refuse.
            if title == node_titles.get(node_id):
                entry["node_id"] = None
                entry["ambiguous"] = True
                warnings.append(
                    f"{node_id}: {title!r} is this module's own title, so an "
                    "occurrence of it cannot be told apart from its heading; "
                    f"recorded as ambiguous against {targets}"
                )
                found.append(entry)
                continue
            if len(targets) == 1:
                entry["node_id"] = targets[0]
                entry["ambiguous"] = False
            else:
                entry["node_id"] = None
                entry["ambiguous"] = True
                warnings.append(
                    f"{node_id}: the reference to {title!r} matches "
                    f"{len(targets)} modules ({targets}); recorded as ambiguous "
                    "rather than resolved to one of them"
                )
            found.append(entry)
        if found:
            references[node_id] = found
    return references, warnings


def build_index(
    outline: JourneyOutline,
    outcomes: dict[str, Any],
    root: Path,
    *,
    progress_before: str,
    progress_after: str,
    media: dict[str, Any] | None = None,
    references: dict[str, list[dict[str, Any]]] | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """The published `index.json`, built from what capture already recorded."""
    warnings: list[str] = list(extra_warnings or [])
    tree = _node_entry(
        outline.root,
        outcomes=outcomes,
        root=root,
        directory=root,
        warnings=warnings,
        references=references or {},
    )
    modules = list(outline.modules())
    by_status: dict[str, int] = {}
    for module in modules:
        status = getattr(outcomes.get(module.node_id), "status", None) or "not-reached"
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "index_format": INDEX_FORMAT,
        "generator": "webshot",
        "generator_version": VERSION,
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "journey_id": outline.journey_id,
        "title": outline.title,
        "source": outline.source,
        "progress_before": progress_before,
        "progress_after": progress_after,
        "counts": {
            "modules": len(modules),
            **{key: by_status[key] for key in sorted(by_status)},
        },
        "media": media,
        "root": tree,
        "warnings": warnings,
    }


_LEVEL_DEPTH = {"journey": 0, "section": 1, "track": 2, "group": 3, "module": 4}


def render_index(index: dict[str, Any]) -> str:
    """`index.md` — the same facts, for a reader rather than a parser.

    The refusals get their own section as well as their line in the outline.
    Someone skimming this file has to be able to answer "what is not in this
    corpus" without reading every entry, or the absence is only nominally
    stated.
    """
    lines = [
        f"# {index['title']}",
        "",
        f"`{index['journey_id']}` · captured from {index['source']}",
        "",
        f"Progress {index['progress_before']} before the walk, "
        f"{index['progress_after']} after.",
        "",
    ]
    counts = index["counts"]
    summary = ", ".join(
        f"{value} {key.replace('_', ' ')}" for key, value in counts.items()
    )
    lines += [f"**{summary}.**", "", "## Outline", ""]

    def walk(entry: dict[str, Any]) -> None:
        depth = _LEVEL_DEPTH[entry["level"]]
        indent = "  " * depth
        if entry["level"] == "module":
            mark = {"captured": "", "refused": " — **REFUSED**"}.get(
                entry["status"], f" — **{entry['status'].upper()}**"
            )
            duration = entry.get("duration_authored") or "?"
            measured = entry.get("duration_measured_seconds")
            if measured is not None:
                duration += f" (measured {measured:g}s)"
            lines.append(
                f"{indent}- **{entry['title']}** · {entry.get('module_type') or 'untyped'}"
                f" · {duration}{mark}"
            )
            if entry.get("reason"):
                lines.append(f"{indent}  - {entry['reason']}")
            if entry.get("pdf"):
                lines.append(f"{indent}  - `{entry['pdf']}`")
            for video in entry.get("videos") or []:
                state = (
                    "walkthrough documented"
                    if video.get("transcribed")
                    else "**not transcribed**"
                )
                lines.append(
                    f"{indent}  - video ({video.get('provider') or 'unknown'}): {state}"
                )
            return
        heading = "#" * (depth + 2)
        lines.append(f"{heading} {entry['title']}" if depth else "")
        if entry.get("prose"):
            lines.extend(["", entry["prose"], ""])
        for child in entry.get("children", []):
            walk(child)

    walk(index["root"])

    refused = _collect_refused(index["root"])
    lines += ["", "## What this corpus does not hold", ""]
    if refused:
        lines.append(
            "These modules are part of the journey and were **not** captured. "
            "They are listed so their absence is a recorded fact rather than a "
            "gap a reader has to notice."
        )
        lines.append("")
        for entry in refused:
            lines.append(
                f"- **{entry['title']}** (`{entry['node_id']}`, "
                f"{entry.get('module_type') or 'untyped'}) — {entry.get('reason')}"
            )
    else:
        lines.append(
            "Nothing. Every module the enumeration listed was captured — which is "
            "a different statement from this section being empty because nobody "
            "checked."
        )
    for warning in index.get("warnings", []):
        lines += ["", f"> **Warning:** {warning}"]
    return "\n".join(lines).rstrip() + "\n"


def _collect_refused(entry: dict[str, Any]) -> list[dict[str, Any]]:
    if entry["level"] == "module":
        return [entry] if entry.get("status") != "captured" else []
    found: list[dict[str, Any]] = []
    for child in entry.get("children", []):
        found += _collect_refused(child)
    return found


def write_index(
    outline: JourneyOutline,
    outcomes: dict[str, Any],
    root: Path,
    *,
    progress_before: str,
    progress_after: str,
) -> dict[str, Any]:
    """Write `index.json` and `index.md`, plus the corpus media store.

    Every part of this is derived from what capture already wrote — the
    outline, the outcome records, each module's own bundle. Nothing re-opens a
    module: if it had to, the capture stage failed to record something and that
    would be the bug.
    """
    from .media import build_media_store

    captured = {
        node_id: root / entry
        for node_id, entry in _captured_directories(outline, outcomes, root).items()
    }
    media = build_media_store(root, sorted(captured.items()))
    references, reference_warnings = resolve_cross_references(outline, root, captured)
    index = build_index(
        outline,
        outcomes,
        root,
        progress_before=progress_before,
        progress_after=progress_after,
        media={
            key: media[key] for key in ("distinct", "references", "shared", "directory")
        },
        references=references,
        extra_warnings=list(media["warnings"]) + reference_warnings,
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "index.md").write_text(render_index(index), encoding="utf-8", newline="\n")
    return index
