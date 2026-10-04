"""Write the walkthrough artifacts for the videos embedded in a captured page.

The point of this module is stated once and everything in it follows from it:
**an agent cannot watch a video.** So a capture that leaves a page's procedural
knowledge inside a player has captured the page and lost the point of it. What
is written here is the walkthrough as text — the instruction per step, the
narration on the playbook's own clock, and the annotated still that shows where
each instruction happens — so an agent handed the bundle can learn the
procedure without playing anything.

Four artifacts per video, each answering a different question:

| File | For |
|---|---|
| `walkthrough.md` | reading it as prose, and inlining it into `content.md` and `content.txt` |
| `steps.json` | addressing one step, with its timings and asset references |
| `transcript.txt` | full-text search over what was said |
| `transcript.vtt` | citing a moment — the cues carry the playbook's own clock |

Nothing here parses Guidde's API: it reads the bridge's dataclasses and writes
files (CONTRIBUTING rule 1). It does still *name* Guidde — `provider: "guidde"`
in the records it writes — which is the seam a second provider will move; see
`extract/guidde.py` and docs/13. What it produces is **the complete narration plus the
per-step written instructions**, and it says exactly that rather than calling
itself a transcript of a recording — a silent step is silent because there was
nothing said, and the walkthrough marks it instead of implying text is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .. import netpolicy
from ..capture.snapshot import HEADING_JS, LOCATOR_JS
from ..capture.visuals import OCR_PDF_PREFIX
from ..extract.guidde import (
    MAX_EMBEDS,
    GuiddeEmbed,
    GuiddeUnavailable,
    TranscriptCue,
    Walkthrough,
    WalkthroughStep,
    asset_urls,
    detect_embeds,
    fetch_playbook,
)
from .captions import (
    CaptionTrack,
    media_directory,
    read_caption_tracks,
    track_filename,
    track_text,
    track_vtt,
    transcript_of,
)
from .publish import write_json

#: Where a video's own artifacts live inside the bundle.
VIDEOS_DIRECTORY = "videos"

#: Chunk kinds this module produces. `video_step` is one step; the chunker's
#: own kinds describe the page's prose.
VIDEO_CHUNK_KIND = "video_step"

#: The chunk kind for a video the bundle could NOT document. It carries no
#: content of the video — that is the point: retrieval should surface the gap
#: rather than silently return nothing.
UNTRANSCRIBED_CHUNK_KIND = "untranscribed_media"
#: A media element's own caption track, read and chunked for retrieval.
TRANSCRIPT_CHUNK_KIND = "media_transcript"

#: What a silent step's walkthrough entry says, in place of instruction text.
#: Named rather than inlined so the docs, the tests, and the file cannot drift.
SILENT_STEP_NOTE = (
    "No narration in this step — on-screen action only. The screenshot is the "
    "record of what happens here."
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")
#: Markdown characters that mean something structural at the start of a line,
#: and the bracket pair that delimits alt text.
#: Two shapes, because they are neutralised differently. A line opening with
#: `#`, `>`, `-` and so on takes a backslash in front of the marker. A numbered
#: list opens with digits, and `\1` is not a CommonMark escape — a backslash
#: there would ship a visible stray character — so the *separator* is escaped
#: instead, which is what CommonMark actually defines.
_BLOCK_OPENER = re.compile(r"^(\s*)(?=[#>\-+*=])")
_NUMBERED_OPENER = re.compile(r"^(\s*\d+)([.)])")
_BRACKETS = re.compile(r"[\[\]]")


def _slug(value: str) -> str:
    """A playbook id as a path segment, refusing anything that is not one.

    The id reaches this module from a page's own markup, so it is treated as
    untrusted: a value carrying `..` or a separator would otherwise choose
    where in the bundle these files land.
    """
    return _SAFE_ID.sub("-", value)[:64] or "unknown"


def _finite(seconds: float) -> float:
    """A number these helpers can format, or 0.0.

    The bridge already refuses non-finite values, so this is the second line
    rather than the first — but `int(nan)` raises and these helpers are called
    from every artifact writer, so a future caller must not be able to fail a
    capture by handing one in.
    """
    value = float(seconds)
    return value if isfinite(value) else 0.0


def timestamp(seconds: float) -> str:
    """A moment as a reader cites it: `0:42`, `1:20`, `1:02:03`."""
    total = max(0, int(_finite(seconds)))
    minutes, second = divmod(total, 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def vtt_timestamp(seconds: float) -> str:
    """A moment as WebVTT spells it: `00:01:20.400`.

    Rounded to milliseconds *before* the split, not after: `59.9996` formatted
    with `%06.3f` renders as `60.000`, which is not a legal WebVTT timestamp
    and which float accumulation across step offsets reaches on its own.
    """
    total = round(max(0.0, _finite(seconds)) * 1000)
    hour, remainder = divmod(total, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    return f"{hour:02d}:{minute:02d}:{remainder / 1000:06.3f}"


# --------------------------------------------------------------------------- #
# Downloaded files
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VideoAsset:
    """One file fetched from the video's own host, as the bundle records it."""

    id: str
    file: str
    kind: str
    playbook_id: str
    source_url: str
    bytes: int
    sha256: str
    step: int | None = None
    width: int | None = None
    height: int | None = None
    #: The playbook whose download produced this file, when it is not this
    #: record's own — one file referenced from two videos is stored once, and
    #: this is what stops the shared `id` reading as a mis-attribution.
    shared_with: str | None = None

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "kind": self.kind,
            "playbook_id": self.playbook_id,
            "step": self.step,
            "source_url": self.source_url,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "shared_with": self.shared_with,
        }


def describe_asset(
    path: Path,
    body: bytes,
    *,
    asset_id: str,
    kind: str,
    playbook_id: str,
    source_url: str,
    step: int | None,
    relative_to: Path,
) -> VideoAsset:
    """Record a downloaded file, reading its dimensions if it is an image.

    Dimensions are read rather than trusted: the record is what a consumer
    sizes a layout from, and Guidde's own metadata describes the video frame
    rather than the still that was actually served.

    Everything is measured from `body`, which the download is still holding —
    re-reading the file to size it, hash it, and probe it was three passes over
    bytes already in memory, and PIL pointlessly probed every downloaded clip.
    """
    width: int | None = None
    height: int | None = None
    try:
        from PIL import Image

        with Image.open(BytesIO(body)) as image:
            width, height = image.size
    except Exception:
        pass
    return VideoAsset(
        id=asset_id,
        file=path.relative_to(relative_to).as_posix(),
        kind=kind,
        playbook_id=playbook_id,
        source_url=source_url,
        bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        step=step,
        width=width,
        height=height,
    )


# --------------------------------------------------------------------------- #
# The walkthrough
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class VideoBundle:
    """One video's contribution to the bundle, ready to be written and counted."""

    playbook: Walkthrough
    #: The standalone `videos/<id>/walkthrough.md`, with image refs relative to
    #: that directory.
    walkthrough: str
    #: The same walkthrough with refs relative to the bundle root, for splicing
    #: into `content.md`.
    inlined: str
    #: And the same walkthrough with no markdown marks, for appending to
    #: `content.txt`. Rendered rather than derived from `inlined` by stripping:
    #: a stripper cannot tell an image reference it should drop from a bracket
    #: the playbook's author wrote.
    plain_text: str
    steps_payload: dict[str, Any]
    transcript_text: str
    transcript_vtt: str
    assets: list[VideoAsset] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Where on the page the player was, so `content.md` can inline the
    #: walkthrough at the position the video appeared.
    locator: str = ""
    #: The section the player sat in — the anchor `inline_into_content` places
    #: the walkthrough against.
    heading: str = ""

    @property
    def clip_urls(self) -> list[str]:
        """Every media URL this walkthrough already accounts for.

        The source recording as well as the per-step clips. A page that embeds
        a Guidde playbook *and* renders that playbook's own source recording in
        a `<video>` listed the same file twice — once as a documented
        walkthrough and once as `NOT TRANSCRIBED` — which double-counted
        `video_tally` and put a contradictory note in `content.md`
        (docs/09 P8-26).
        """
        urls = [step.clip_url for step in self.playbook.steps if step.clip_url]
        if self.playbook.source_recording_url:
            urls.append(self.playbook.source_recording_url)
        return urls

    @property
    def directory(self) -> str:
        """Where this video's own artifacts live — derived, never stored.

        One expression rather than a field means it cannot come to disagree
        with the playbook id it is built from.
        """
        return video_directory(self.playbook.id)

    @property
    def manifest_record(self) -> dict[str, Any]:
        playbook = self.playbook
        return {
            "provider": "guidde",
            "playbook_id": playbook.id,
            "title": playbook.title,
            "source_url": playbook.source_url,
            "duration_seconds": round(playbook.duration, 2),
            "steps": len(playbook.steps),
            "narrated_steps": playbook.narrated_steps,
            # What the transcript carries, which is not always what the
            # playbook shipped: a step with authored narration and no
            # `subtitles` contributes one cue at the step's own timing
            # (`narration_cues`), and a count taken from `step.cues` would
            # describe a transcript.vtt that has more entries than it says.
            "transcript_cues": sum(
                len(narration_cues(step)) for step in playbook.steps
            ),
            "organization": playbook.organization,
            "language": playbook.language,
            "last_updated_by": playbook.last_updated_by,
            "directory": self.directory,
            # Distinct files, not records: two steps referencing one
            # deduplicated still are one asset in the bundle, and a count that
            # said two would contradict the directory it describes.
            "assets": len({asset.file for asset in self.assets}),
            "chunks": len(self.chunks),
            "locator": self.locator or None,
            "transcribed": True,
            # How the text was produced, in the record rather than only in
            # prose. `authored` means a person wrote it and the audio was
            # generated from it — the opposite of a machine transcript, and a
            # consumer deciding how far to trust a quote needs to know which
            # of the two it is holding.
            #
            # Which is exactly why it cannot be a constant. `extract/guidde.py`
            # calls any narration type other than `textToSpeech` "unverified
            # territory" and sets `unverified_narration` to say so — and this
            # field went on claiming `authored` anyway, with only the sibling
            # prose disagreeing. A consumer filtering structured fields (the
            # reason the structured field exists) was told the subtitles were
            # authoritative precisely when the parser had said they might not
            # be. The value is now derived from the same fact the prose is
            # (docs/09 P8-18).
            "transcript_provenance": (
                "unverified" if playbook.unverified_narration else "authored"
            ),
            "narration": "text-to-speech from the authored script"
            if not playbook.unverified_narration
            else "unverified: " + ", ".join(playbook.unverified_narration),
        }


def video_directory(playbook_id: str) -> str:
    """One video's directory inside the bundle, from its playbook id.

    A free function because two callers need the same answer and one of them
    is not a `VideoBundle`: the chunk writer has to name the artifact its
    `doc_items` pointer resolves in, and a second spelling of this path is a
    second thing that can drift from where the files are actually written.
    """
    return f"{VIDEOS_DIRECTORY}/{_slug(playbook_id)}"


def asset_for_step(
    assets: Sequence[VideoAsset], step: WalkthroughStep
) -> VideoAsset | None:
    for asset in assets:
        if asset.kind == "step-screenshot" and asset.step == step.number:
            return asset
    return None


#: How far `videos/<id>/walkthrough.md` is from the bundle root.
FROM_VIDEO_DIRECTORY = "../../"


def _as_alt_text(value: str) -> str:
    """Playbook prose reduced to something that cannot leave an `![…](…)`.

    A bracket in an instruction — `Click [Save]` is ordinary phrasing — ends
    the alt text early and turns the rest into a stray link; `](http://…)`
    would make it a reference of the author's choosing. This is third-party
    text, so it is neutralised rather than trusted.
    """
    return _BRACKETS.sub(" ", " ".join(value.split()))[:200]


def _as_inline(value: str) -> str:
    """Playbook text going onto a line WebShot is composing.

    Interior newlines are the danger: a title of `X\n\n## Injected` writes a
    real heading into `content.md`, which `_section_end` then treats as a
    section boundary — the injected-anchor problem `_as_paragraph` guards
    against, arriving through a field that is not a paragraph.
    """
    return " ".join(value.split())


def _as_paragraph(value: str) -> str:
    """Playbook prose that cannot open a markdown block of its own.

    A leading `#`, `>` or `-` on a line of an instruction would inject a
    heading, quote or list into `content.md` — and an injected heading is also
    an anchor `_section_end` can later mistake for the page's own.
    """
    return "\n".join(
        _NUMBERED_OPENER.sub(r"\1\\\2", _BLOCK_OPENER.sub(r"\1\\", line))
        for line in value.splitlines()
    ).strip()


def _asset_reference(file: str, prefix: str) -> str:
    """One `assets/…` bundle path, as seen from wherever the markdown lives.

    The same walkthrough text is written twice — once as
    `videos/<id>/walkthrough.md`, two directories down, and once inlined into
    `content.md` at the bundle root — so a single relative form is wrong in one
    of them. It used to be wrong in `content.md`, which is the file this
    feature exists to improve: every screenshot resolved two levels *above*
    the bundle (docs/09 P7-10).
    """
    return f"{prefix}{file}"


def _as_prose(value: str, *, plain: bool) -> str:
    """Instruction text for whichever file it is going into.

    `content.md` needs `_as_paragraph`'s escaping so third-party prose cannot
    open a markdown block of its own. `content.txt` must NOT have it: that file
    has no markdown semantics to protect, so a backslash in it is not an escape
    but a visible stray character the author never wrote.
    """
    return value.strip() if plain else _as_paragraph(value)


def _walkthrough(
    playbook: Walkthrough,
    assets: Sequence[VideoAsset],
    *,
    asset_prefix: str,
    plain: bool,
) -> str:
    """The video as a document, one section per step, in one of two renderings.

    One function with a `plain` flag rather than two, for the reason
    `_media_note` gives for the same choice: the markdown and the plain text
    are the *same* claims about the same video, and two implementations of that
    would eventually make two different claims. Everything the markdown carries
    the plain text carries — the step numbers, the timestamps, the
    instructions, the narration, and the sentence saying what this text is —
    minus only the marks: no heading hashes, no image references, no
    backticks, no blockquote.
    """
    playbook_duration = timestamp(playbook.duration)
    title = _as_inline(playbook.title)
    summary = (
        f"Embedded walkthrough — {len(playbook.steps)} steps, {playbook_duration}."
    )
    lines = [
        title if plain else f"## {title}",
        "",
        summary if plain else f"*{summary}*",
        "",
    ]
    source = (
        f"Source: {playbook.source_url}"
        if plain
        else f"Source: <{playbook.source_url}>"
    )
    provenance = [source]
    if playbook.organization:
        provenance.append(f"Publisher: {playbook.organization}")
    if playbook.last_updated_by:
        provenance.append(f"Last updated by: {playbook.last_updated_by}")
    if playbook.language:
        provenance.append(f"Language: {playbook.language}")
    joined = " · ".join(provenance)
    lines += [joined, ""] if plain else ["> " + joined, ">", ""]
    lines += [
        "This is the walkthrough as text: the written instruction for each "
        "step, the narration on the video's own clock, and the annotated "
        "screenshot for that moment. It is the complete narration plus the "
        "per-step instructions, not a transcript of a recording — steps with "
        "no narration are marked rather than omitted.",
        "",
    ]

    for step in playbook.steps:
        heading = _as_inline(step.title) or f"Step {step.number}"
        opener = f"Step {step.number} — {heading}  ·  {timestamp(step.start)}"
        lines.append(opener if plain else f"### {opener}")
        lines.append("")
        if step.silent:
            lines += [SILENT_STEP_NOTE if plain else f"*{SILENT_STEP_NOTE}*", ""]
        elif step.instruction:
            lines += [_as_prose(step.instruction, plain=plain), ""]
        asset = asset_for_step(assets, step)
        if asset:
            reference = _asset_reference(asset.file, asset_prefix)
            if plain:
                # Named rather than linked. The still is a real artifact of
                # this step and an agent reading `content.txt` should be able
                # to find it; the alt text is dropped with the link because it
                # is the instruction again, one line above.
                lines += [f"Screenshot: {reference}", ""]
            else:
                lines += [
                    f"![{_as_alt_text(step.instruction or heading)}]({reference})",
                    "",
                ]
        if narration_cues(step):
            lines.append("Narration:")
            lines.append("")
            for cue in narration_cues(step):
                stamp = timestamp(cue.start)
                spoken = _as_inline(cue.text)
                # `[0:42] …` is how `transcript.txt` already places a cue in
                # plain text; the markdown list item would ship its bullet and
                # its backticks as literal characters here.
                lines.append(
                    f"[{stamp}] {spoken}" if plain else f"- `{stamp}` {spoken}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def walkthrough_markdown(
    playbook: Walkthrough,
    assets: Sequence[VideoAsset],
    *,
    asset_prefix: str = FROM_VIDEO_DIRECTORY,
) -> str:
    """The walkthrough for `videos/<id>/walkthrough.md` and for `content.md`.

    `asset_prefix` is how far this copy of the text sits from the bundle root,
    because the same walkthrough is written both as `videos/<id>/walkthrough.md`
    and inlined into `content.md` — and a relative image path correct in one is
    broken in the other.
    """
    return _walkthrough(playbook, assets, asset_prefix=asset_prefix, plain=False)


def walkthrough_plain_text(
    playbook: Walkthrough,
    assets: Sequence[VideoAsset],
    *,
    asset_prefix: str = "",
) -> str:
    """The same walkthrough for `content.txt`, with no markdown marks.

    `content.txt` is the plain-text rendering of the page everywhere else, and
    heading marks, image references and backticks in it would be the only ones
    there — the argument `_note_media` already makes for writing its own note
    twice.
    """
    return _walkthrough(playbook, assets, asset_prefix=asset_prefix, plain=True)


def steps_payload(
    playbook: Walkthrough, assets: Sequence[VideoAsset]
) -> dict[str, Any]:
    """`steps.json` — one addressable record per step."""
    return {
        "provider": "guidde",
        "playbook_id": playbook.id,
        "title": playbook.title,
        "source_url": playbook.source_url,
        "duration_seconds": round(playbook.duration, 3),
        "organization": playbook.organization,
        "language": playbook.language,
        "last_updated_by": playbook.last_updated_by,
        "source_recording_url": playbook.source_recording_url or None,
        "narration": {
            "kind": "text-to-speech from the authored script"
            if not playbook.unverified_narration
            else "unverified",
            "unverified_styles": list(playbook.unverified_narration),
            "note": (
                "The audio is generated from this text, so the text is the "
                "source rather than a transcription of it. Steps with no "
                "narration are silent screen action, not missing data."
            ),
        },
        "steps": [
            {
                "number": step.number,
                "id": step.step_id,
                "kind": step.kind,
                "title": step.title,
                "instruction": step.instruction,
                "narration": step.narration,
                "silent": step.silent,
                "start_seconds": round(step.start, 3),
                "end_seconds": round(step.end, 3),
                "duration_seconds": round(step.duration, 3),
                "timestamp": timestamp(step.start),
                "screenshot": (
                    asset.file if (asset := asset_for_step(assets, step)) else None
                ),
                "screenshot_kind": step.screenshot_kind or None,
                "source_screenshot_url": step.screenshot_url or None,
                "source_clip_url": step.clip_url or None,
                "transcript": [
                    {
                        "text": cue.text,
                        "start_seconds": round(cue.start, 3),
                        "end_seconds": round(cue.end, 3),
                        "timestamp": timestamp(cue.start),
                    }
                    for cue in narration_cues(step)
                ],
            }
            for step in playbook.steps
        ],
    }


def narration_cues(step: WalkthroughStep) -> tuple[TranscriptCue, ...]:
    """A step's transcript cues, or its authored narration standing in for them.

    A step can carry `audioNote.markdown` and no `subtitles` — narration Guidde
    never split into timed lines. Parsing sets `step.narration`, which makes the
    step non-silent, and the transcript writers then emitted only `step.cues` —
    so `transcript.txt` held the heading and nothing under it and `transcript.vtt`
    held nothing at all, for a step the bundle counted as narrated. The bundle
    claimed the complete narration and did not carry it (docs/09 P8-28).

    One cue spanning the step, because the step's own timing is the only timing
    there is: it places the text on the video's clock correctly, to the
    resolution the source actually provides.
    """
    if step.cues:
        return step.cues
    if not step.narration:
        return ()
    return (TranscriptCue(text=step.narration, start=step.start, end=step.end),)


def transcript_text(playbook: Walkthrough) -> str:
    """The narration as plain text, timestamped so a quote can be placed."""
    lines = [
        playbook.title,
        f"Source: {playbook.source_url}",
        f"Duration: {timestamp(playbook.duration)}",
        "",
        "Narration with the video's own timings. Steps with no narration are "
        "silent screen action and are listed with their instruction instead.",
        "",
    ]
    for step in playbook.steps:
        lines.append(f"[{timestamp(step.start)}] Step {step.number}: {step.title}")
        if step.silent:
            lines.append(f"    ({SILENT_STEP_NOTE})")
            if step.instruction:
                lines.append(f"    {step.instruction}")
        for cue in narration_cues(step):
            lines.append(f"    [{timestamp(cue.start)}] {cue.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def transcript_vtt(playbook: Walkthrough) -> str:
    """The narration as WebVTT, on the playbook's own clock.

    The cues are the per-step subtitles moved onto the document clock, which
    reproduces Guidde's own published `.vtt` cue for cue — so a timestamp cited
    from this file matches the player a reader would open to check it.
    """
    parts = [
        "WEBVTT",
        "",
        "NOTE",
        f"playbook: {playbook.id}",
        f"title: {playbook.title}",
        f"source: {playbook.source_url}",
        "",
    ]
    number = 0
    for step in playbook.steps:
        for cue in narration_cues(step):
            number += 1
            parts += [
                str(number),
                f"{vtt_timestamp(cue.start)} --> {vtt_timestamp(cue.end)}",
                cue.text,
                "",
            ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Chunks
# --------------------------------------------------------------------------- #


def video_chunks(
    playbook: Walkthrough,
    assets: Sequence[VideoAsset],
    *,
    start_index: int,
) -> list[dict[str, Any]]:
    """One retrieval chunk per step, each carrying its full provenance.

    Every chunk names the playbook, the video's title, the step number, the
    moment it happens, and the screenshot for it — so an agent citing "video X,
    step 3, at 0:42" is quoting the record rather than reconstructing it.

    **The anchor is a real JSON Pointer into a named file.** It used to read
    `#/videos/<id>/steps/<n>` — 1-based, and rooted at a `videos` key that
    `steps.json` does not have, so it resolved to nothing at either end. Page
    chunks emit `#/texts/N`, which does resolve, so `doc_items` meant one thing
    on one chunk kind and nothing on another; a consumer following it back to
    the source record found no record (docs/09 P8-23).

    A pointer needs a document, and a chunk that anchors outside content.json
    has to say which one — so `meta.video.steps_document` carries the
    bundle-relative path the pointer resolves in. That pairing is the contract:
    `steps_document` + `doc_items[0]` addresses exactly one step, and it stays
    unambiguous on a page carrying several walkthroughs, where the pointer
    alone would not.
    """
    records: list[dict[str, Any]] = []
    for step in playbook.steps:
        asset = asset_for_step(assets, step)
        narration = " ".join(cue.text for cue in narration_cues(step)).strip()
        body = step.instruction
        if narration and narration != body:
            body = f"{body}\n\n{narration}" if body else narration
        if not body:
            body = SILENT_STEP_NOTE
        text = "\n".join(
            [
                f"{playbook.title} — step {step.number} of {len(playbook.steps)} "
                f"at {timestamp(step.start)}",
                step.title,
                body,
            ]
        ).strip()
        # RFC 6901 into `steps.json`, whose `steps` array is zero-based and at
        # the root. `step.number` is 1-based because that is how a reader cites
        # a step; the pointer is not, because that is how the file is indexed.
        anchor = f"#/steps/{step.number - 1}"
        steps_document = f"{video_directory(playbook.id)}/steps.json"
        records.append(
            {
                "id": f"ch_{start_index + len(records) + 1:06d}",
                "text": text,
                "meta": {
                    "doc_items": [anchor],
                    "headings": [playbook.title],
                    "kind": VIDEO_CHUNK_KIND,
                    "page": None,
                    "locator": anchor,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "video": {
                        "provider": "guidde",
                        "playbook_id": playbook.id,
                        "title": playbook.title,
                        "step": step.number,
                        "steps": len(playbook.steps),
                        "start_seconds": round(step.start, 3),
                        "end_seconds": round(step.end, 3),
                        "timestamp": timestamp(step.start),
                        "screenshot": asset.file if asset else None,
                        "source_url": playbook.source_url,
                        #: The file `doc_items` points into. A pointer without
                        #: its document is not a reference.
                        "steps_document": steps_document,
                    },
                },
            }
        )
    return records


# --------------------------------------------------------------------------- #
# Writing, and inlining into the page's own content
# --------------------------------------------------------------------------- #


def build_video_bundle(
    playbook: Walkthrough,
    assets: Sequence[VideoAsset],
    *,
    locator: str = "",
    heading: str = "",
    start_index: int = 0,
) -> VideoBundle:
    """Everything one video contributes, composed but not yet written."""
    warnings = list(playbook.warnings)
    if playbook.unverified_narration:
        warnings.append(
            f"Guidde playbook {playbook.id} narrates with "
            f"{', '.join(playbook.unverified_narration)} rather than "
            "text-to-speech. WebShot's reading has only been verified against "
            "generated narration, where the text is the source of the audio; "
            "check this walkthrough against the video before relying on it."
        )
    return VideoBundle(
        playbook=playbook,
        walkthrough=walkthrough_markdown(playbook, assets),
        inlined=walkthrough_markdown(playbook, assets, asset_prefix=""),
        plain_text=walkthrough_plain_text(playbook, assets),
        steps_payload=steps_payload(playbook, assets),
        transcript_text=transcript_text(playbook),
        transcript_vtt=transcript_vtt(playbook),
        assets=list(assets),
        chunks=video_chunks(playbook, assets, start_index=start_index),
        warnings=warnings,
        locator=locator,
        heading=heading,
    )


def write_video_bundles(staging: Path, bundles: Iterable[VideoBundle]) -> None:
    """Write each video's four artifacts under `videos/<playbookId>/`."""
    for bundle in bundles:
        directory = staging / bundle.directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "walkthrough.md").write_text(
            bundle.walkthrough, encoding="utf-8", newline="\n"
        )
        write_json(directory / "steps.json", bundle.steps_payload)
        (directory / "transcript.txt").write_text(
            bundle.transcript_text, encoding="utf-8", newline="\n"
        )
        (directory / "transcript.vtt").write_text(
            bundle.transcript_vtt, encoding="utf-8", newline="\n"
        )


def append_chunk_records(staging: Path, records: Sequence[dict[str, Any]]) -> int:
    """Add records to `chunks.jsonl`; returns how many.

    Appended rather than merged in memory so the page's own chunk records keep
    the ids and the order `extract/chunks.py` gave them: a video is additional
    provenance, not a renumbering of the page.
    """
    if not records:
        return 0
    with (staging / "chunks.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def append_video_chunks(staging: Path, bundles: Sequence[VideoBundle]) -> int:
    """Add the per-step chunks to `chunks.jsonl`; returns how many.

    Appended rather than merged in memory so the page's own chunk records keep
    the ids and the order `extract/chunks.py` gave them: a video is additional
    provenance, not a renumbering of the page.
    """
    return append_chunk_records(
        staging, [record for bundle in bundles for record in bundle.chunks]
    )


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _placed(lines: list[str], blocks: Sequence[tuple[str, str]]) -> str:
    """Splice each `(heading, block)` at the end of that heading's section.

    One helper for both callers — walkthroughs and untranscribed-media notes —
    so `content.md` is read, rewritten and written once, and the two cannot
    develop different ideas of where a section ends.  Offsets are all computed
    against the original lines before anything is inserted, so blocks cannot
    move each other's anchors.
    """
    placed: dict[int, list[str]] = {}
    trailing: list[str] = []
    # Which occurrence of each heading text the next block naming it should
    # take. `blocks` arrives in document order and `_headings` yields in
    # document order, so the Nth video under a repeated heading takes the Nth
    # heading of that text — where before, every one of them took the first,
    # and a walkthrough for a later section was spliced under an earlier,
    # unrelated one (docs/09 P8-36).
    #
    # Not a complete answer, and the residual is worth naming: two videos both
    # under the *second* of two identically-titled sections still put one under
    # the first, because the only positional signal available here is order.
    # The DOM locator is not usable — the player element is not in the snapshot
    # at all (see `inline_into_content`), so there is no line in `content.md`
    # to map it to. Strictly better than always-first, and honest about it.
    taken: dict[str, int] = {}
    for heading, block in blocks:
        key = _normalized(heading)
        occurrence = taken.get(key, 0)
        index = _section_end(lines, heading, occurrence=occurrence)
        if index is None and occurrence:
            # Fewer headings than blocks naming them: the rest belong to the
            # last one, which is where two videos under one heading go.
            index = _section_end(lines, heading, occurrence=occurrence - 1)
        else:
            taken[key] = occurrence + 1
        if index is None:
            trailing.append(block)
        else:
            placed.setdefault(index, []).append(block)
    rebuilt: list[str] = []
    for number, line in enumerate(lines):
        rebuilt.append(line)
        for block in placed.get(number, ()):
            rebuilt += ["", block]
    for block in trailing:
        rebuilt += ["", block]
    return "\n".join(rebuilt).rstrip() + "\n"


def inline_into_content(markdown: str, bundles: Sequence[VideoBundle]) -> str:
    """Put each walkthrough where its video was, not in an appendix.

    A page whose videos are relegated to the end reads in an order that is not
    the page's — an agent following the procedure would meet the checklist
    before the walkthrough it depends on.

    The anchor is the *section* the player was in, because that is the finest
    position the page's own document model still has: an `<iframe>` is not in
    the snapshot's tag allowlist, so the player element itself is gone from
    `content.html` and docling never sees a picture for it (measured, not
    assumed).  Each walkthrough is therefore placed at the end of its heading's
    section, which puts it after the prose that introduced the video and before
    whatever came next.  A video whose heading cannot be found is appended —
    a worse reading order, never a lost one.
    """
    if not bundles:
        return markdown
    return _placed(
        markdown.rstrip().split("\n"),
        [(bundle.heading, bundle.inlined.rstrip()) for bundle in bundles],
    )


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _headings(lines: Sequence[str]) -> Iterator[tuple[int, int, str]]:
    """Every real markdown heading, as (line index, level, normalized text).

    Fenced code is skipped. A shell snippet's `# comment` is not a heading, and
    treating one as a section boundary spliced whole walkthroughs *inside* a
    code fence — turning the walkthrough into code and the snippet into prose
    (docs/09 P7-10).
    """
    # The marker that opened the fence, or None. Tracking the *character*
    # matters: a `~~~` line inside a ``` block is content, and toggling on
    # either one let it close the block and expose the `#` comments inside.
    opener: str | None = None
    for number, line in enumerate(lines):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if opener is None:
                opener = marker
            elif marker == opener:
                opener = None
            continue
        if opener is not None:
            continue
        match = _HEADING.match(line)
        if match:
            yield number, len(match.group(1)), _normalized(match.group(2))


def _section_end(
    lines: Sequence[str], heading: str, *, occurrence: int = 0
) -> int | None:
    """The last line of the section opened by `heading`, or None if absent.

    Matched on normalized text so a heading docling re-wrapped still resolves,
    and the level comes from the *document* rather than from the DOM — the two
    can legitimately differ, and it is the markdown's own nesting that decides
    where a section stops.  Two videos under one heading both land at its end,
    in the order the page showed them, which is the reading order that matters.

    `occurrence` picks *which* heading of that text, zero-based. A document may
    repeat one — "Overview" under two modules is ordinary — and matching only
    the first sent every later section's walkthrough to the first section
    (docs/09 P8-36).

    Headings come from `_headings`, which skips fenced code — a shell snippet's
    `# comment` is not a section boundary, and treating one as the end of a
    section spliced whole walkthroughs *inside* a code block.
    """
    if not heading:
        return None
    wanted = _normalized(heading)
    found = list(_headings(lines))
    matches = [(number, level) for number, level, text in found if text == wanted]
    if occurrence >= len(matches):
        return None
    start, level = matches[occurrence]
    end = len(lines) - 1
    for number, other, _text in found:
        if number > start and other <= level:
            end = number - 1
            break
    while end > start and not lines[end].strip():
        end -= 1
    return end


# --------------------------------------------------------------------------- #
# Enrichment: detect, read, download, write
# --------------------------------------------------------------------------- #

#: Refused rather than downloaded. A still is tens of kilobytes and a step clip
#: is under a megabyte; anything at this size is not what it claims to be, and
#: a capture must not be turned into an unbounded download by a page's markup.
MAX_ASSET_BYTES = 32 * 1024 * 1024

#: And a ceiling across the whole capture, because the per-file cap alone
#: bounds nothing: the step *count* comes out of the intercepted response, so a
#: playbook claiming ten thousand steps would pay the per-file limit ten
#: thousand times over.
#:
#: A budget in bytes rather than a cap on steps, deliberately. `--max-assets`
#: counts elements because a page's visuals are things a reader can see and
#: fifty is a sane number of them; a walkthrough's steps are *the document*,
#: and truncating a legitimately long one at an arbitrary count would silently
#: drop the procedure's ending. Bytes is what actually bounds the work, and it
#: is where `--video-assets` puts its weight. A normal playbook is nowhere
#: near this: eleven annotated stills measured about a megabyte.
MAX_VIDEO_BYTES = 512 * 1024 * 1024

#: `asset_urls` names each file `<prefix>-<step>` or `source-recording`; this is
#: what each prefix means in the bundle's own vocabulary.
ASSET_KINDS = {"step": "step-screenshot", "clip": "step-clip"}

#: What a downloaded file is called, by its content type where the URL's own
#: extension is unhelpful (Firebase storage URLs carry one, but not always).
_MIME_SUFFIX = {
    "image/webp": ".webp",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


#: What a bundle says about a video it could not document. Named so the
#: manifest, the bundle note, and the embedded README cannot say it three
#: slightly different ways — and so a test can hold the wording.
#:
#: Two notes rather than one, because the two situations are not the same and
#: saying so wrongly is worse than saying nothing: a page whose video declares
#: a caption track has a transcript WebShot simply has not read yet, and
#: telling a reader there is none would send them looking for something that is
#: right there.
UNTRANSCRIBED_NOTE = (
    "This media is NOT transcribed. It is a plain media file with no caption "
    "track and no authored walkthrough behind it, so WebShot has its source "
    "URL, its duration and its poster frame, and nothing of what is said or "
    "shown in it. Anything an agent needs from it is missing from this bundle "
    "and has to come from playing it."
)

#: A track was declared and could not be read. Distinct from "no track", and
#: the distinction is load-bearing: over `file://` every document is an opaque
#: origin, so the browser fetches the VTT and discards the cues. Reporting that
#: as "no caption track" would send a reader away from a transcript that is
#: right there; reporting it as unread would repeat a note that stopped being
#: true when path 1 shipped.
UNREADABLE_CAPTIONS_NOTE = (
    "This media is NOT transcribed. It declares caption track(s) and the "
    "browser could not read them — the track loaded with an error, which a "
    "cross-origin caption file does when the page itself was opened from a "
    "file:// URL. A transcript for this media exists and is absent from this "
    "bundle for that reason, not because none was written."
)

#: Tracks exist, none of them is a transcript. A `subtitles` track is a
#: translation for viewers who do not speak the language, so calling its text
#: the transcript would put a claim in the bundle the data does not support.
TRANSLATION_ONLY_NOTE = (
    "This media is NOT transcribed. It declares track(s), but none of them is "
    'kind="captions" — what it has is a translation rather than a '
    "transcription of what is said. The text is kept and named in the manifest "
    "as a translation, and is deliberately not inlined into content.md, so "
    "nothing downstream can retrieve it as though it were the source."
)


def untranscribed_note(tracks: Sequence[CaptionTrack]) -> str:
    """Which of the three honest things to say about an undocumented video.

    Three rather than two since path 1: a media element can now have no track,
    a track that could not be read, or a track that is not a transcription.
    They are different facts and a reader acts differently on each.
    """
    if not tracks:
        return UNTRANSCRIBED_NOTE
    if not any(track.readable for track in tracks):
        return UNREADABLE_CAPTIONS_NOTE
    return TRANSLATION_ONLY_NOTE


#: Why a media element's own text was not usable as its title. Constants
#: rather than an excerpt of the value: these travel into `manifest.warnings`,
#: and a message quoting recognized text would record a fact about the
#: recording machine's OCR rather than about WebShot (docs/09 P7-13).
TITLE_WAS_RECOGNIZED_TEXT = (
    "the element carried no title of its own, so what reached this field was "
    "the text OCR read inside the frame — the player's own chrome, not a name"
)
TITLE_SPANNED_LINES = (
    "the value runs across more than one line, and a title does not; "
    "interpolated into a heading it would have broken the document around it"
)


def media_title(raw: str) -> tuple[str, str]:
    """A media element's title, or nothing and the reason there is none.

    The DOM scan reads `title` and then `aria-label`, and `aria-label` is
    exactly where the visual harvest writes what OCR recognized inside a
    visual that had no label of its own. So a `<video>` the author never named
    hands this the recognized text of its *player chrome*. One live capture
    recorded `Text recognized within visual asset: +e]\\ni\\na\\n> 0:00/4:11…`
    as a title: interpolated into a single-line markdown heading it broke
    `content.md` across six lines, rendered `> 0:00/4:11` as a blockquote, and
    propagated to the corpus index.

    This is docs/09 P12-4's shape — a field filled from text that happened to
    be nearby, with no check that it is the kind of thing the field is for — so
    the check is here, at the one place the field is derived.

    **The OCR segment is cut, not the whole value**, and cut at the *first*
    marker part: `capture/visuals.py` appends recognition last, joined with
    ` | `, so what precedes the marker is the author's own text and is worth
    keeping. It is the same cut `capture/snapshot.py` makes on the same value,
    for the same reason it gives — keeping the non-marker parts instead would
    let recognized text containing ` | ` leak back in as fragments.
    """
    value = raw.strip()
    if not value:
        return "", ""
    parts = value.split(" | ")
    marker = next(
        (n for n, part in enumerate(parts) if part.startswith(OCR_PDF_PREFIX)), None
    )
    if marker is not None:
        value = " | ".join(parts[:marker]).strip()
        if not value:
            return "", TITLE_WAS_RECOGNIZED_TEXT
    if "\n" in value or "\r" in value:
        return "", TITLE_SPANNED_LINES
    return value, ""


@dataclass(frozen=True, slots=True)
class MediaVideo:
    """A plain `<video>` or `<audio>` element: recorded, openly not transcribed.

    The alternative to recording it is silence, and a video an agent cannot see
    is far worse unlabelled than labelled: unlabelled, the bundle reads as
    complete when it is not.  So this carries everything that *is* knowable
    without watching — where it came from, how long it is, what frame the page
    showed — and says plainly that the rest is absent.
    """

    source_url: str
    duration_seconds: float | None
    poster_url: str
    title: str
    #: "video" or "audio". An audio briefing is *entirely* spoken content, so
    #: leaving it out of this list would have made `video_tally.total` a number
    #: that is wrong rather than merely incomplete — and a tally exists to be
    #: summed across a journey.
    media_kind: str = "video"
    locator: str = ""
    heading: str = ""
    #: The harvested frame already in the bundle's assets, if there was one.
    poster_asset: str = ""
    #: Caption tracks the element declared, as read through the page. Empty is
    #: the common case and the reason there is nothing to transcribe from.
    tracks: tuple[CaptionTrack, ...] = ()
    #: Overrides the note derived from `tracks`. Set for the one case that is
    #: neither of the usual two: a walkthrough WebShot *found* and could not
    #: fetch. Saying "no authored walkthrough behind it" there would be false —
    #: there is one, and it is the reason this record exists.
    note_override: str = ""
    #: Why `title` is empty, when the element had text that was not a title.
    #: Empty when the title stands *and* when the element simply had none: only
    #: the refusal is something a reader has to be told about, and an element
    #: silently renamed reads exactly like one that was never named.
    refused_title: str = ""

    @property
    def name(self) -> str:
        """What the content files and the chunks call this element.

        One expression rather than the same `title or heading or …` written in
        two places, and collapsed to a single line where it is written: `title`
        is already refused by `media_title` when it spans lines, but the
        `heading` fallback is an `<h2>`'s `innerText`, and a heading carrying a
        `<br>` would break the `### …` line it is interpolated into exactly the
        way the recognized text did. Same boundary, the other side of it
        (docs/09 P7-12).
        """
        return _as_inline(self.title or self.heading) or "Untitled media"

    @property
    def transcript(self) -> CaptionTrack | None:
        """The track whose text may be published as this media's transcript."""
        return transcript_of(self.tracks)

    @property
    def note(self) -> str:
        if self.note_override:
            return self.note_override
        return untranscribed_note(self.tracks)

    @property
    def manifest_record(self) -> dict[str, Any]:
        transcribed = self.transcript is not None
        return {
            "provider": "media-element",
            "media_kind": self.media_kind,
            "title": self.title,
            "source_url": self.source_url,
            "duration_seconds": (
                round(self.duration_seconds, 2)
                if self.duration_seconds is not None
                else None
            ),
            "poster_url": self.poster_url or None,
            "poster_asset": self.poster_asset or None,
            "caption_tracks": [track.descriptor for track in self.tracks],
            # Per track, so a reader can tell a transcript from a translation
            # from a track that would not load without re-deriving it.
            "tracks": [track.record for track in self.tracks],
            "locator": self.locator or None,
            "transcribed": transcribed,
            # Two axes that compose: `provider` says which service produced the
            # words, this says how far they can be trusted. A caption file the
            # publisher shipped is `authored`, the same value Guidde's own
            # steps carry, and both stay distinguishable from a machine
            # transcript (`asr`), which is the whole point of keeping them
            # separate. This repository produces none (docs/13, L3 is out of
            # scope), so `authored` is the only value written here.
            "transcript_provenance": "authored" if transcribed else None,
            "note": None if transcribed else self.note,
        }


@dataclass(slots=True)
class VideoEnrichment:
    """Everything the videos on one page contributed — of both kinds.

    Two kinds, because a page has two: a Guidde playbook, whose walkthrough is
    authored text WebShot can read in full, and a plain media file, which it
    cannot.  They are counted separately and reported separately, so a reader
    of the manifest can tell how much of the page's video content actually
    reached the bundle — and so the tally of which kind is where can be summed
    across captures rather than measured again.
    """

    walkthroughs: list[VideoBundle] = field(default_factory=list)
    media: list[MediaVideo] = field(default_factory=list)
    #: One chunk per video the bundle could not document, so retrieval surfaces
    #: the gap instead of returning nothing and reading as complete.
    media_chunks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def chunks(self) -> int:
        return sum(len(bundle.chunks) for bundle in self.walkthroughs) + len(
            self.media_chunks
        )

    @property
    def assets(self) -> list[VideoAsset]:
        return [asset for bundle in self.walkthroughs for asset in bundle.assets]

    @property
    def records(self) -> list[dict[str, Any]]:
        """Every video's manifest record, each able to name its own files.

        `directory` is injected here rather than computed by whoever reads the
        manifest: a media element has no id, so its directory is its position
        among the page's media, and a consumer re-deriving that from list
        order is a second implementation of the same rule waiting to disagree
        with `write_caption_artifacts`.
        """
        records = [bundle.manifest_record for bundle in self.walkthroughs]
        for position, video in enumerate(self.media, start=1):
            record = video.manifest_record
            if any(track.readable and track.cues for track in video.tracks):
                record["directory"] = media_directory(position)
            records.append(record)
        return records

    @property
    def tally(self) -> dict[str, int]:
        """How many videos of each kind, and how many are documented.

        The per-page half of "count modules by video type": summing this across
        a journey's manifests answers the question without measuring it again.

        Three parts rather than two since path 1. `untranscribed_media` was
        `len(self.media)` while every media element was untranscribed by
        construction, and it kept returning that after a readable `captions`
        track started producing one — so a manifest could say a video was
        transcribed and, four lines down, count it as not. Adding
        `transcribed_media` rather than only narrowing the old field is the
        P7-8 half: a tally whose parts no longer reach its total omits a case
        silently, which is worse than not counting at all.
        """
        transcribed = sum(1 for video in self.media if video.transcript is not None)
        return {
            "total": len(self.walkthroughs) + len(self.media),
            "documented_walkthroughs": len(self.walkthroughs),
            "transcribed_media": transcribed,
            "untranscribed_media": len(self.media) - transcribed,
        }

    @property
    def empty(self) -> bool:
        return not self.walkthroughs and not self.media


def _suffix(url: str, content_type: str) -> str:
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix.lower()
    if suffix and len(suffix) <= 6 and suffix[1:].isalnum():
        return suffix
    return _MIME_SUFFIX.get(content_type.split(";")[0].strip().lower(), ".bin")


def _undeclared_length(asset_id: str) -> str:
    return (
        f"{asset_id} was not downloaded: the server declared no Content-Length, "
        "and an undeclared body cannot be bounded before it is read. The step "
        "keeps its instructions and timings and loses only this asset."
    )


def _too_large(asset_id: str, size: int) -> str:
    return (
        f"{asset_id} was not downloaded: it is {size / 1e6:.1f} MB, past the "
        f"{MAX_ASSET_BYTES / 1e6:.0f} MB per-file limit."
    )


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _same_service(target: str, home: str) -> bool:
    """Whether `target` is `home` or sits beneath it.

    Deliberately *not* "shares a registrable domain": that needs a public
    suffix list this project will not carry, and guessing it as the last two
    labels makes `evil.co.uk` and `victim.co.uk` the same service — and makes
    two unrelated addresses `1.2.3.4` and `9.8.3.4` the same service too, since
    an IP's labels are no hierarchy at all.

    A suffix rule has neither failure. It is what Guidde needs —
    `storage.app.guidde.com` is beneath `app.guidde.com` — and it is strict, so
    a deployment serving assets from a *sibling* host is refused rather than
    silently allowed. That refusal is a per-asset warning, not a failed
    capture, so the cost of being wrong this way is a named gap in the bundle
    rather than a credentialed fetch nobody asked for.
    """
    target, home = target.lower(), home.lower()
    return target == home or (
        not _is_address(target)
        and not _is_address(home)
        and target.endswith(f".{home}")
    )


def permitted_asset_host(url: str, origin: str) -> bool:
    """Whether the walkthrough's own service is what this URL names.

    The URLs come out of the intercepted playbook, so third-party data chooses
    the fetch target — and the fetch carries the capture context's cookies
    (measured), lands in the bundle, and is embedded in the PDF the user
    shares. Unrestricted, that is a credentialed read of any host the signed-in
    user is authenticated to, written into a document they hand to someone
    else.

    So a playbook may name assets on the service it was read from and nowhere
    else — the same subtree, so `storage.app.guidde.com` is permitted for a
    playbook read from `app.guidde.com`, and a playbook pointing outside it is
    not describing its own video (docs/09 P7-10).

    **What the rule refuses, measured rather than assumed.** Against the
    committed fixture
    `tests/fixtures/guidde/quickguidde-sample-playbook.json`, every URL
    WebShot actually fetches — `drawnScreenshot`, `docScreenshot`,
    `previewScreenshot`, `videoUrl`, `sourceCaptureVideo.url` — is on
    `storage.app.guidde.com` and passes. But the same response names two hosts
    this rule refuses:

    * `static.guidde.com` — `steps[].docPublicScreenshot` (9x) and
      `docCoverPublicScreenshot` (1x). A *sibling* domain, outside
      `app.guidde.com`'s subtree, so "the walkthrough's own service" is
      narrower than "Guidde".
    * `storage.googleapis.com` — `magicCapture.captureType.imageUrl` (1x).

    Nothing breaks today, because nothing reads those fields. The boundary is
    recorded because of where it bites next: `docPublicScreenshot` on the
    public CDN is the obvious fallback when `drawnScreenshot` returns 403 on a
    non-shared playbook, and adding that fallback without widening this rule
    would lose every screenshot to a per-asset warning. Widening it is a real
    decision — a second permitted host is a second host a hostile playbook can
    aim a credentialed fetch at — so it belongs in a change that wants it, not
    here (docs/09 P8-35).
    """
    target = urlparse(url).hostname or ""
    home = urlparse(origin).hostname or ""
    return bool(target) and bool(home) and _same_service(target, home)


def _refused_foreign(asset_id: str, url: str, origin: str) -> str:
    return (
        f"{asset_id} was not downloaded: {url} is not on the service this "
        f"walkthrough was read from ({urlparse(origin).hostname}). A playbook "
        "may only name assets on its own host."
    )


def _refused_unparseable(asset_id: str, url: str, exc: Exception) -> str:
    return (
        f"{asset_id} was not downloaded: {url!r} is not a URL this capture can "
        f"classify ({exc}), so it was refused rather than fetched."
    )


def _refused_internal(asset_id: str, url: str) -> str:
    return (
        f"{asset_id} was not downloaded: {url} names an internal address and "
        "--block-private-requests is in force."
    )


async def _internal(url: str) -> bool:
    """Whether this URL names the host or a network behind it.

    Off the event loop: the classifier resolves the name, and the capture's
    browser connection is live above every caller.
    """
    return await asyncio.to_thread(netpolicy.is_internal_url, url)


async def _download(
    request: Any,
    url: str,
    destination_directory: Path,
    *,
    asset_id: str,
    block_private: bool,
    origin: str,
) -> tuple[Path | None, bytes, int, str]:
    """Fetch one file into the bundle, or say why it was not fetched.

    Through the capture's own browser context rather than a second HTTP stack:
    the same session, the same proxy, the same cookies as everything else the
    capture did.

    **Not the same network policy, though — which is why the check is here.**
    `--block-private-requests` is enforced by `context.route`, and Playwright
    applies route handlers to page-initiated traffic only: an
    `APIRequestContext` request goes straight past them. Measured rather than
    inferred — with an aborting gate installed on a context, `page.goto` is
    blocked and `context.request.get` returns 200 with the body.

    That matters more here than it would elsewhere, because these URLs are not
    the caller's: they come out of the intercepted Guidde response, so
    third-party data names the fetch target, and the bytes are written into the
    bundle and embedded in the PDF the user shares. It is the shape docs/09
    P4-13 was written about, and `capture/session.py` calls the
    caller-URL-only check "guards the front door and leaves the windows open".

    Gated on the flag rather than unconditional: an LMS hosted on an intranet
    and serving its media from a private address is a real deployment — and
    plausibly this project's own — so refusing it by default would break a
    legitimate capture silently. Same rule as the page gate, same default.

    Returns the written path, the body itself (so the caller measures and
    hashes what it already holds rather than re-reading the file), the bytes
    this attempt cost the capture — charged whether or not the body was kept —
    and a problem string.
    """
    # Both classifiers parse the URL, and `urlparse` raises `ValueError` on a
    # malformed one — `http://[` is enough. These calls sit outside the request
    # `try` below, so the exception escaped `_download` entirely and failed a
    # capture that had already rendered, where the documented behaviour is a
    # per-asset warning and a step that keeps everything but its screenshot.
    # A URL this code cannot parse is a URL it cannot vouch for, so it is
    # refused rather than fetched (docs/09 P8-37).
    try:
        permitted = permitted_asset_host(url, origin)
    except ValueError as exc:
        return None, b"", 0, _refused_unparseable(asset_id, url, exc)
    if not permitted:
        return None, b"", 0, _refused_foreign(asset_id, url, origin)
    if block_private:
        try:
            internal = await _internal(url)
        except ValueError as exc:
            return None, b"", 0, _refused_unparseable(asset_id, url, exc)
        if internal:
            return None, b"", 0, _refused_internal(asset_id, url)
    try:
        response = await request.get(url, timeout=60_000)
    except Exception as exc:
        return None, b"", 0, f"{asset_id} could not be downloaded: {exc}"
    # The URL actually reached, before a byte of it is read: a public URL that
    # redirects to an internal one would otherwise walk straight through the
    # pre-check. The page gate gets this for free — a redirect is a new request
    # and is routed again — so this is the same guarantee restated where the
    # route handler does not reach (docs/04-spec.md §6.8's `final_url`
    # re-check, applied to an asset).
    try:
        if response.url != url:
            try:
                redirected_ok = permitted_asset_host(response.url, origin)
                redirected_internal = block_private and await _internal(response.url)
            except ValueError as exc:
                return None, b"", 0, _refused_unparseable(asset_id, response.url, exc)
            if not redirected_ok:
                return None, b"", 0, _refused_foreign(asset_id, response.url, origin)
            if redirected_internal:
                return None, b"", 0, _refused_internal(asset_id, response.url)
        if not response.ok:
            return (
                None,
                b"",
                0,
                f"{asset_id} could not be downloaded: HTTP {response.status}",
            )
        return await _store(response, url, destination_directory, asset_id=asset_id)
    except Exception as exc:
        # Reading or writing a body is as much a per-asset problem as a refused
        # one, and this function's whole contract is that it reports rather
        # than raises — `enrich_with_videos` promises nothing in it can fail a
        # capture, and an unguarded write would have broken that promise.
        return None, b"", 0, f"{asset_id} could not be stored: {exc}"
    finally:
        # Playwright keeps a response body in the driver process until the
        # context closes unless it is disposed. A capture fetches one per step,
        # so without this the whole set stays resident for the rest of the run.
        await _dispose(response)


async def _dispose(response: Any) -> None:
    try:
        await response.dispose()
    except Exception:
        return


async def _store(
    response: Any, url: str, destination_directory: Path, *, asset_id: str
) -> tuple[Path | None, bytes, int, str]:
    """Write one fetched body, or say why it was refused, with its cost.

    **The length has to be declared, and it has to fit, before anything is
    read.** `APIResponse.body()` buffers the whole response in the driver
    process; there is no streaming read on Playwright's request context, which
    is the API this download uses precisely *because* it carries the capture
    session's cookies. So a cap applied after the read has already paid the
    cost it exists to refuse: a server that omits `Content-Length` and answers
    with an endless chunked body exhausts memory before `MAX_ASSET_BYTES` is
    ever consulted, and these URLs come out of the intercepted playbook rather
    than from the caller (docs/09 P8-17).

    An absent or unparseable length is therefore refused rather than read.
    That is a real narrowing — a chunked asset server becomes a per-asset
    warning — and it is the documented degradation this module already has:
    the step keeps its instructions and timings and loses only its screenshot
    (docs/09 P7-9). Every asset host reached here serves stored objects, which
    declare their length.

    **What this does not close**, written down rather than implied: a server
    that declares a small length and sends a large body is still read whole,
    because the buffering happens inside the driver. What bounds that case is
    the 60-second request timeout, the per-capture byte budget, and
    `permitted_asset_host` — the asset must be on the walkthrough's own
    service. Closing it properly means not using `APIRequestContext`, which
    means reproducing the session cookies on a streaming client; that is a
    larger change than this review, and the residual is recorded so the next
    person meets it as a decision rather than as a surprise.
    """
    declared = response.headers.get("content-length", "")
    if not declared.isdigit():
        return None, b"", 0, _undeclared_length(asset_id)
    if int(declared) > MAX_ASSET_BYTES:
        # Charged even though nothing was read: a playbook of oversized steps
        # must not be able to name unlimited work for free (docs/09 P7-10).
        return None, b"", int(declared), _too_large(asset_id, int(declared))
    body = await response.body()
    # Re-measured against what actually arrived, and still charged when it is
    # refused: the declared length is the server's claim, not a fact.
    if len(body) > MAX_ASSET_BYTES:
        return None, b"", len(body), _too_large(asset_id, len(body))
    path = destination_directory / (
        asset_id + _suffix(url, response.headers.get("content-type", ""))
    )
    path.write_bytes(body)
    return path, body, len(body), ""


#: Every `<video>` and `<audio>` a reader could reach, with what is knowable
#: about it without playing it.  `duration` is read from the element rather
#: than from a header: the browser has already parsed the metadata, and that is
#: the only place a length is reliably available.
#:
#: Audio is in scope because the question this list answers is "what spoken
#: content did the bundle not capture" — and an audio briefing is nothing but
#: spoken content. `visible()` is deliberately not applied: an `<audio>` with
#: default controls is small and a `<video>` can be off-screen until scrolled
#: to, and neither makes what it says less absent from the bundle.
_MEDIA_SCAN = """() => {
    const locator = __LOCATOR__;
    const heading = __HEADING__;
    const found = [];
    for (const element of document.querySelectorAll('video, audio')) {
        if (element.closest('[data-webshot-hidden="true"]')) continue;
        const source = element.currentSrc || element.src
            || element.querySelector('source')?.src || '';
        if (!source) continue;
        found.push({
            source,
            mediaKind: element.tagName.toLowerCase(),
            poster: element.poster || element.getAttribute('poster') || '',
            duration: Number.isFinite(element.duration) ? element.duration : null,
            title: element.title || element.getAttribute('aria-label') || '',
            locator: locator(element),
            heading: heading(element).text,
            posterAsset: element.getAttribute('data-webshot-asset-id') || '',
            tracks: [...element.querySelectorAll('track')].map(
                t => `${t.kind || 'track'}:${t.srclang || ''}`),
        });
    }
    return found;
}""".replace("__LOCATOR__", LOCATOR_JS).replace("__HEADING__", HEADING_JS)


async def detect_media_videos(
    page: Any, *, exclude: Sequence[str] = ()
) -> list[MediaVideo]:
    """Plain `<video>` elements on the page, minus any already documented.

    `exclude` carries the source URLs a walkthrough already accounts for, so a
    Guidde player that renders its own `<video>` is not also reported as an
    undocumented one.

    Each element's `<track>`s are read here rather than merely counted — path 1
    of docs/13's preference order — through the page, so the request crosses
    the capture's own network policy (`bundle/captions.py`).
    """
    captions = await read_caption_tracks(page)
    found: list[dict[str, Any]] = await page.evaluate(_MEDIA_SCAN)
    skip = {url for url in exclude if url}
    videos: list[MediaVideo] = []
    seen: set[str] = set()
    for entry in found:
        source = str(entry.get("source") or "")
        if not source or source in skip or source in seen:
            continue
        seen.add(source)
        duration = entry.get("duration")
        title, refused = media_title(str(entry.get("title") or ""))
        videos.append(
            MediaVideo(
                source_url=source,
                duration_seconds=float(duration) if duration is not None else None,
                poster_url=str(entry.get("poster") or ""),
                title=title,
                media_kind=str(entry.get("mediaKind") or "video"),
                refused_title=refused,
                locator=str(entry.get("locator") or ""),
                heading=str(entry.get("heading") or ""),
                poster_asset=str(entry.get("posterAsset") or ""),
                tracks=captions.get(source, ()),
            )
        )
    return videos


@dataclass(slots=True)
class _Budget:
    """Bytes already fetched for this capture's videos."""

    spent: int = 0


async def _playbook_assets(
    request: Any,
    playbook: Walkthrough,
    assets_directory: Path,
    *,
    staging: Path,
    clips: bool,
    block_private: bool,
    origin: str,
    downloaded: dict[str, VideoAsset],
    budget: _Budget,
) -> tuple[list[VideoAsset], list[str]]:
    """Fetch one playbook's files, reusing anything already downloaded.

    `downloaded` is keyed by source URL and shared across every video on the
    page, so a still two playbooks both reference is one request and one copy
    in the bundle — recorded twice, under each step that uses it.
    """
    assets: list[VideoAsset] = []
    problems: list[str] = []
    for name, url in asset_urls(playbook, clips=clips):
        if budget.spent >= MAX_VIDEO_BYTES:
            problems.append(
                f"{playbook.id} stopped downloading at "
                f"{budget.spent / 1e6:.0f} MB, the per-capture ceiling. The "
                "steps past this point keep their instructions and timings and "
                "lose only their screenshots."
            )
            break
        prefix, _, tail = name.rpartition("-")
        step = int(tail) if tail.isdigit() else None
        kind = ASSET_KINDS.get(prefix, "source-recording")
        existing = downloaded.get(url)
        if existing is not None:
            # The file keeps the id it was stored under — two records pointing
            # at one file is the point of the dedupe — while this record says
            # which playbook and step *this* reference is. `shared_with` names
            # the *other* playbook whose download produced it, and is left
            # unset when a playbook simply reuses one of its own stills, which
            # is not sharing between videos and must not read as if it were.
            assets.append(
                replace(
                    existing,
                    kind=kind,
                    playbook_id=playbook.id,
                    step=step,
                    shared_with=(
                        existing.playbook_id
                        if existing.playbook_id != playbook.id
                        else None
                    ),
                )
            )
            continue
        asset_id = f"video-{_slug(playbook.id)}-{name}"
        path, body, cost, problem = await _download(
            request,
            url,
            assets_directory,
            asset_id=asset_id,
            block_private=block_private,
            origin=origin,
        )
        budget.spent += cost
        if problem:
            problems.append(problem)
            continue
        assert path is not None
        asset = describe_asset(
            path,
            body,
            asset_id=asset_id,
            kind=kind,
            playbook_id=playbook.id,
            source_url=url,
            step=step,
            relative_to=staging,
        )
        downloaded[url] = asset
        assets.append(asset)
    return assets, problems


async def enrich_with_videos(
    page: Any,
    *,
    staging: Path,
    origin: str,
    download_clips: bool,
    block_private_requests: bool,
    chunk_start: int,
) -> VideoEnrichment:
    """Find every embedded walkthrough on the page and write it into the bundle.

    Ordered so a failure costs only what it has to: detection is a DOM read,
    each playbook is read independently, and a video that cannot be reached
    leaves the others — and the page itself — untouched.  Nothing here can fail
    a capture; every problem becomes a warning the manifest carries
    (CONTRIBUTING rule 5).
    """
    enrichment = VideoEnrichment()
    chunk_index = chunk_start
    embeds: list[GuiddeEmbed] = []
    try:
        embeds = await detect_embeds(page)
    except GuiddeUnavailable as exc:
        # Said out loud rather than read as "this page had no video": the two
        # are indistinguishable in the output, and only one of them is true.
        # The plain-video scan still runs — one detector failing is no reason
        # to also lose the videos the other one would have found.
        enrichment.warnings.append(f"Embedded videos were not detected: {exc}")
    if len(embeds) > MAX_EMBEDS:
        enrichment.warnings.append(
            f"The page carries {len(embeds)} embedded walkthroughs; only the "
            f"first {MAX_EMBEDS} were read. Each one costs two navigations, so "
            "an unbounded count would be an unbounded capture."
        )
        embeds = embeds[:MAX_EMBEDS]
    if not embeds:
        enrichment.media = await _media_or_warn(page, enrichment)
        _record_media(staging, enrichment, chunk_start=chunk_index)
        return enrichment

    assets_directory = staging / "assets"
    assets_directory.mkdir(parents=True, exist_ok=True)
    context = page.context
    # One file per source URL, however many steps or videos reference it: a
    # still reused across two playbooks on one page is one download and one
    # copy in the bundle. The cross-*module* case is the one that matters for a
    # whole journey, and it needs the same rule keyed by content hash — which
    # is why the reference is recorded per step rather than the file being
    # named after the step that happened to fetch it first.
    downloaded: dict[str, VideoAsset] = {}
    # Shared across every video on the page: the ceiling is what this capture
    # is willing to fetch in total, not what each playbook may fetch each.
    budget = _Budget()
    for embed in embeds:
        try:
            playbook = await fetch_playbook(context, embed.playbook_id, origin=origin)
        except GuiddeUnavailable as exc:
            message = (
                f"The walkthrough for the video embedded at {embed.url} was not "
                f"captured: {exc} The page itself is unaffected."
            )
            enrichment.warnings.append(message)
            # The embed is still recorded. Dropping it left `videos`,
            # `video_tally`, `content.md` and the embedded README describing a
            # two-player page as a one-video page — detection had succeeded, so
            # the bundle knew the video was there and published a count that
            # said otherwise. A PDF-only consumer saw no trace of it at all.
            # An unreachable walkthrough is exactly the gap `MediaVideo`
            # already exists to record (CONTRIBUTING rule 5, docs/09 P8-27).
            enrichment.media.append(
                MediaVideo(
                    source_url=embed.url,
                    duration_seconds=None,
                    poster_url="",
                    title=_as_inline(embed.heading) or "Embedded walkthrough",
                    media_kind="video",
                    locator=embed.locator,
                    heading=embed.heading,
                    note_override=(
                        "This walkthrough is NOT transcribed. WebShot detected "
                        "the embedded player and could not read its playbook: "
                        f"{exc} What it demonstrates is absent from this bundle."
                    ),
                )
            )
            continue
        assets, problems = await _playbook_assets(
            context.request,
            playbook,
            assets_directory,
            staging=staging,
            clips=download_clips,
            block_private=block_private_requests,
            origin=origin,
            downloaded=downloaded,
            budget=budget,
        )
        enrichment.warnings.extend(problems)
        bundle = build_video_bundle(
            playbook,
            assets,
            locator=embed.locator,
            heading=embed.heading,
            start_index=chunk_index,
        )
        chunk_index += len(bundle.chunks)
        enrichment.walkthroughs.append(bundle)
        enrichment.warnings.extend(bundle.warnings)

    enrichment.media = await _media_or_warn(
        page,
        enrichment,
        exclude=[url for bundle in enrichment.walkthroughs for url in bundle.clip_urls],
    )
    if not enrichment.walkthroughs:
        _record_media(staging, enrichment, chunk_start=chunk_index)
        return enrichment

    write_video_bundles(staging, enrichment.walkthroughs)
    append_video_chunks(staging, enrichment.walkthroughs)
    content = staging / "content.md"
    if content.is_file():
        content.write_text(
            inline_into_content(
                content.read_text(encoding="utf-8"), enrichment.walkthroughs
            ),
            encoding="utf-8",
            newline="\n",
        )
    _note_walkthroughs(staging, enrichment)
    _record_assets(staging, enrichment.assets)
    _record_media(staging, enrichment, chunk_start=chunk_index)
    return enrichment


async def _media_or_warn(
    page: Any, enrichment: VideoEnrichment, *, exclude: Sequence[str] = ()
) -> list[MediaVideo]:
    """Plain media elements, or a warning saying they could not be looked for."""
    try:
        media = await detect_media_videos(page, exclude=exclude)
    except Exception as exc:
        enrichment.warnings.append(
            f"The page could not be scanned for plain video elements: {exc}"
        )
        return []
    # A log line is not the manifest. A transcript cut at the read budget is a
    # stage doing less than the caller asked for, and CONTRIBUTING rule 4 puts
    # that in `warnings` where it travels with the bundle — a reader holding
    # only the artifact never saw stderr.
    for video in media:
        if video.refused_title:
            # Same rule applied to a title: the bundle names this element by
            # its heading or as untitled, and a reader comparing it against the
            # page would otherwise have no way to tell a video the author never
            # named from one whose name WebShot declined to publish.
            enrichment.warnings.append(
                f"The text offered as a title for {video.source_url} was "
                f"refused: {video.refused_title}. The {video.media_kind} is "
                "recorded without a title rather than under a corrupt one."
            )
        for track in video.tracks:
            if track.truncated:
                enrichment.warnings.append(
                    f"The {track.kind or 'untyped'} track on "
                    f"{video.source_url} was cut at the read budget: "
                    f"{len(track.cues)} of {track.total_cues} cues were read. "
                    "Any transcript or translation from it in this bundle is "
                    "PARTIAL and ends before the media does."
                )
    return media


def write_caption_artifacts(staging: Path, media: Sequence[MediaVideo]) -> None:
    """Write every readable track's text into the bundle, saying what each is.

    A transcript nobody can read is not a transcript, so the cues reach disk
    rather than only a count in the manifest.

    A translation is written too — the corpus keeps what the page offered —
    but it is written *only* here. It is never inlined into `content.md` and
    never chunked, because those are the retrieval surfaces: text that enters
    them is indistinguishable from source at the point it is retrieved, which
    is the failure docs/13 names for machine transcripts. The index names the
    file so a reader can still find it.
    """
    for index, video in enumerate(media, start=1):
        readable = [track for track in video.tracks if track.readable and track.cues]
        if not readable:
            continue
        directory = staging / media_directory(index)
        directory.mkdir(parents=True, exist_ok=True)
        for track in readable:
            stem = track_filename(track)
            (directory / f"{stem}.vtt").write_text(
                track_vtt(track, title=video.title, source_url=video.source_url),
                encoding="utf-8",
                newline="\n",
            )
            if track.is_transcript:
                (directory / f"{stem}.txt").write_text(
                    track_text(track), encoding="utf-8", newline="\n"
                )


def _record_media(
    staging: Path, enrichment: VideoEnrichment, *, chunk_start: int
) -> None:
    """Write what every media element contributed into the files agents read.

    Both outcomes reach every surface. A gap is recorded so retrieval can
    surface it; a transcript is recorded so retrieval can *find* it. Writing
    only one of the two was the bug this function used to have twice over: the
    filtering was applied to the guard and not to the loops beneath it, so a
    video whose captions had just been read was announced as not transcribed —
    in `content.md`, beside a manifest that said the opposite (docs/09 P8-21's
    shape, P10-10 for this instance).
    """
    write_caption_artifacts(staging, enrichment.media)
    _note_media(staging, enrichment)
    enrichment.media_chunks = media_chunks(enrichment.media, start_index=chunk_start)
    append_chunk_records(staging, enrichment.media_chunks)


def _note_walkthroughs(staging: Path, enrichment: VideoEnrichment) -> None:
    """Put the walkthroughs into `content.txt`, not only into `content.md`.

    `_note_media` states the argument for a media element and it applies
    unchanged to the walkthrough beside it: an agent that searches
    `content.txt` and finds no mention of the procedure would reasonably
    conclude the page had none. It went unapplied here, and a walkthrough is
    the largest body of text this tool produces — in the first live journey,
    78 of 78 modules carrying one had a `content.txt` under 60% of their
    `content.md`, the worst 379 bytes against 10388.

    Appended rather than spliced by heading, for the reason
    `inline_into_content`'s own docstring already accepts: `content.txt` has no
    heading marks to anchor against, and "a video whose heading cannot be found
    is appended — a worse reading order, never a lost one."
    """
    if not enrichment.walkthroughs:
        return
    text = staging / "content.txt"
    if not text.is_file():
        # Absence is not success. If this file was never written the
        # walkthrough survives in `content.md` and `videos/<id>/`, and a reader
        # of the manifest is told which surfaces are missing it.
        enrichment.warnings.append(
            "The page's content.txt was not written, so the walkthrough text "
            "reached content.md and the video's own artifacts only."
        )
        return
    with text.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n\n"
            + "\n\n".join(
                bundle.plain_text.rstrip() for bundle in enrichment.walkthroughs
            )
            + "\n"
        )


def _note_media(staging: Path, enrichment: VideoEnrichment) -> None:
    """Record, in every file an agent reads, what each media element gave.

    `content.md` is where a reader meets it, but an agent that searches
    `content.txt` or retrieves over `chunks.jsonl` and finds no mention would
    reasonably conclude the page had no video — which is the whole failure this
    note exists to prevent, so all three say it. That argument does not stop
    applying when the media element *was* transcribed: words readable only in
    `videos/media-NN/transcript.txt` are absent from every surface an agent
    searches, which is the same silence wearing a better outcome.
    """
    if not enrichment.media:
        return
    content = staging / "content.md"
    if content.is_file():
        content.write_text(
            _placed(
                content.read_text(encoding="utf-8").rstrip().split("\n"),
                [
                    (video.heading, "\n".join(_media_note(video, position=position)))
                    for position, video in enumerate(enrichment.media, start=1)
                ],
            ),
            encoding="utf-8",
            newline="\n",
        )
    else:
        enrichment.warnings.append(
            "The page's content.md was not written, so what this capture read "
            "from the page's media is recorded only in the manifest."
        )
    text = staging / "content.txt"
    if text.is_file():
        # Plain prose, not the markdown `content.md` gets: this file is the
        # plain-text rendering everywhere else, and heading marks in it would
        # be the only ones there.
        with text.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "\n\n"
                + "\n\n".join(
                    "\n".join(_media_note(video, plain=True, position=position))
                    for position, video in enumerate(enrichment.media, start=1)
                )
                + "\n"
            )


#: How much transcript goes in one chunk before the next one starts. A whole
#: transcript in a single chunk retrieves as one undifferentiated blob — a
#: twenty-minute video would return twenty minutes of speech to answer a
#: question about ten seconds of it — so the cues are grouped up to this many
#: characters and no further. The split is only ever *between* cues, so no
#: chunk holds half a sentence and every chunk keeps a start and end time.
TRANSCRIPT_CHUNK_CHARS = 1500


def _cue_groups(cues: Sequence[Any]) -> list[list[Any]]:
    """Consecutive cues, grouped up to `TRANSCRIPT_CHUNK_CHARS`."""
    groups: list[list[Any]] = []
    current: list[Any] = []
    size = 0
    for cue in cues:
        if current and size + len(cue.text) > TRANSCRIPT_CHUNK_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(cue)
        size += len(cue.text) + 1
    if current:
        groups.append(current)
    return groups


def media_chunks(
    media: Sequence[MediaVideo], *, start_index: int
) -> list[dict[str, Any]]:
    """What each media element contributes to retrieval — words, or the gap.

    A corpus that cannot describe its own gaps invites confident answers built
    on absent material: an agent searching for what a video covers should find
    a record saying the video exists and its content is not here, rather than
    finding nothing and concluding there was nothing. The same reasoning is why
    a transcript is chunked rather than left as a file: retrieval that cannot
    reach the words is indistinguishable from not having them.

    Numbered by each element's position among *all* the page's media, so
    `#/media/N` and the `videos/media-NN` directory holding its files are the
    same N. They were briefly not, when this function began skipping the
    transcribed elements and kept numbering from its own output.
    """
    records: list[dict[str, Any]] = []

    def provenance(video: MediaVideo, name: str, transcribed: bool) -> dict[str, Any]:
        return {
            "provider": "media-element",
            "media_kind": video.media_kind,
            "title": name,
            "source_url": video.source_url,
            "transcribed": transcribed,
            "duration_seconds": (
                round(video.duration_seconds, 3)
                if video.duration_seconds is not None
                else None
            ),
        }

    def add(video: MediaVideo, anchor: str, text: str, meta: dict[str, Any]) -> None:
        records.append(
            {
                "id": f"ch_{start_index + len(records) + 1:06d}",
                "text": text,
                "meta": {
                    "doc_items": [anchor],
                    "headings": [video.heading] if video.heading else [],
                    "page": None,
                    "locator": video.locator or anchor,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    **meta,
                },
            }
        )

    for position, video in enumerate(media, start=1):
        name = video.name
        anchor = f"#/media/{position}"
        transcript = video.transcript
        if transcript is None:
            text = "\n".join(
                [
                    f"{name} — {video.media_kind} on this page, NOT transcribed",
                    video.note,
                    f"Source: {video.source_url}",
                ]
            )
            add(
                video,
                anchor,
                text,
                {
                    "kind": UNTRANSCRIBED_CHUNK_KIND,
                    "media": provenance(video, name, False),
                },
            )
            continue
        groups = _cue_groups(transcript.cues)
        for number, group in enumerate(groups, start=1):
            body = "\n".join(cue.text for cue in group)
            text = (
                f"{name} — {video.media_kind} transcript, part {number} of "
                f"{len(groups)}\n{body}"
            )
            add(
                video,
                f"{anchor}/transcript/{number}",
                text,
                {
                    "kind": TRANSCRIPT_CHUNK_KIND,
                    "media": {
                        **provenance(video, name, True),
                        # Said on the chunk, not only on the manifest record:
                        # a chunk retrieved on its own is the whole context a
                        # consumer has, and "who wrote these words" is the
                        # question it must not have to leave the chunk to ask.
                        "transcript_provenance": "authored",
                        "track_kind": transcript.kind,
                        "language": transcript.language or None,
                        "start_seconds": round(group[0].start, 3),
                        "end_seconds": round(group[-1].end, 3),
                    },
                },
            )
    return records


#: Kept as a name because tests and the corpus tooling refer to the gap chunks
#: specifically; `media_chunks` is what the pipeline calls.
def untranscribed_chunks(
    media: Sequence[MediaVideo], *, start_index: int
) -> list[dict[str, Any]]:
    """Only the gap chunks — `media_chunks` restricted to media with no words."""
    return media_chunks(
        [video for video in media if video.transcript is None],
        start_index=start_index,
    )


def _media_note(video: MediaVideo, *, plain: bool = False, position: int) -> list[str]:
    """What the content files say where a media element appeared.

    Two shapes, because there are two things to say. A gap says what is missing
    and why. A transcript says what was read, where it came from, and then
    *is the words* — inlined for the same reason a walkthrough's prose is:
    `content.md` and `content.txt` are where an agent looks, and a transcript
    they do not contain is one it will not find.
    """
    name = video.name
    length = (
        f" ({timestamp(video.duration_seconds)})"
        if video.duration_seconds is not None
        else ""
    )
    transcript = video.transcript
    if transcript is None:
        headline = f"{video.media_kind.capitalize()} not transcribed: {name}{length}"
        return [
            headline if plain else f"### {headline}",
            "",
            video.note,
            "",
            f"Source: {video.source_url}" if plain else f"Source: <{video.source_url}>",
        ]
    directory = media_directory(position)
    headline = f"{video.media_kind.capitalize()} transcript: {name}{length}"
    provenance = (
        f"Transcribed from the {transcript.kind} track the page shipped "
        f"({transcript.language or 'language unstated'}); the publisher wrote "
        f"these words. The same text is at {directory}/transcript.txt, and "
        f"{directory}/transcript.vtt carries it on the media's own clock so a "
        f"timestamp can be cited."
    )
    if transcript.truncated:
        # The reader of this section is the one most likely to quote it as the
        # whole of what was said, so the incompleteness goes here rather than
        # only in the manifest they may never open.
        provenance += (
            f" **This transcript is PARTIAL**: the read budget cut it after "
            f"{len(transcript.cues)} of {transcript.total_cues} cues, so it "
            f"ends before the {video.media_kind} does."
        )
    return [
        headline if plain else f"### {headline}",
        "",
        provenance,
        "",
        f"Source: {video.source_url}" if plain else f"Source: <{video.source_url}>",
        "",
        track_text(transcript).rstrip(),
    ]


def _record_assets(staging: Path, assets: Sequence[VideoAsset]) -> None:
    """Add the downloaded files to `assets.json` as their own typed list.

    Separate from `visual_assets` on purpose: those are visuals harvested from
    the page and matched to the document model by position, and appending to
    that list would break the count check the extraction bridge relies on.
    These came from the video's own host and are recorded as what they are.
    """
    if not assets:
        return
    path = staging / "assets.json"
    if not path.is_file():
        return
    document = json.loads(path.read_text(encoding="utf-8"))
    document["video_assets"] = [asset.record() for asset in assets]
    write_json(path, document)
