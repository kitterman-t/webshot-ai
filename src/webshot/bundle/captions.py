"""Reading a media element's `<track>` — preference-order path 1 (docs/13).

A `<video>` that declares a caption track has a transcript the publisher wrote.
Until now WebShot recorded that the track existed and said plainly it had not
read it, which was honest and is not the same as reading it.

**Read through the page, never out of band.** P7-1 set this shape for Guidde:
WebShot does not issue the request, it lets the page make it and reads the
result. A `<track>` is the same case and the reason is P7-6 — `context.route`
filters page-initiated traffic only, so an `APIRequestContext` fetch would sail
straight past `--block-private-requests`. Setting `track.mode = 'hidden'` makes
the browser fetch and parse the VTT itself; measured, that request crosses a
`context.route("**/*")` gate like any other subresource, so it inherits the
capture's network policy, its cookies and its CORS rules with no new allowlist.
It is also what docs/11 principle 2 already promises: "the page the user asked
for **plus that page's own subresources, as any browser does**."

`hidden` rather than `showing`: cues become active and parsed without being
painted, so a page prepared for print looks the same as it would have.

**Only `kind="captions"` is a transcript.** `subtitles` are a *translation* for
viewers who do not speak the language — a German subtitle track is not a
transcript of English speech, and letting one wear that word would put a claim
in the bundle the data does not support. `descriptions`, `chapters` and
`metadata` are not transcripts either, and a `<track>` with no `kind` **defaults
to `subtitles`** in HTML, which is exactly why this is an allowlist: a denylist
of "not subtitles" would let the ambiguous case through in the wrong direction.
That is the assessment-refusal lesson applied to a second place.

**A declared track that cannot be read is its own state**, distinct from a media
element with no track at all. Over `file://` every document is an opaque origin,
so the track load is cross-origin without CORS and Chromium discards the cues —
the fetch happens, `readyState` becomes 3 and an `error` event fires. Recording
that as "no caption track" would be silent degradation; recording it as an
unread transcript would be the note that was true before this module existed and
is not now.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..extract.guidde import TranscriptCue

LOGGER = logging.getLogger("webshot.videos")

#: The one `kind` whose text is a transcription of the audio. Everything else a
#: `<track>` can declare is something other than what was said.
TRANSCRIPT_KIND = "captions"


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    """One `<track>` on a media element, and what came of reading it."""

    kind: str
    language: str
    label: str
    source_url: str
    #: False when the browser reported `readyState === 3` or fired `error` —
    #: the track was declared and its cues could not be obtained.
    readable: bool
    cues: tuple[TranscriptCue, ...] = ()
    #: How many cues the track actually had, before the read budget.
    total_cues: int = 0
    #: Whether the read budget cut this track short of its own end. Carried
    #: from the read rather than derived from `total_cues > len(cues)`, because
    #: blank-text cues are dropped on this side too and that comparison is
    #: therefore also true for a track nothing truncated. A transcript cut at a
    #: budget and one that simply ended are otherwise the same shape, and only
    #: one of them is complete.
    truncated: bool = False

    @property
    def is_transcript(self) -> bool:
        """Whether this track's text may be called a transcript of the audio."""
        return self.kind == TRANSCRIPT_KIND and self.readable and bool(self.cues)

    @property
    def descriptor(self) -> str:
        """`captions:en` — the shape the manifest has always recorded."""
        return f"{self.kind or 'track'}:{self.language}"

    @property
    def record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "srclang": self.language or None,
            "label": self.label or None,
            "source_url": self.source_url or None,
            "readable": self.readable,
            "cues": len(self.cues),
            #: Stated, never left to be inferred by comparing `cues` against a
            #: constant the reader would have to know. A partial transcript
            #: that publishes as complete is worse than one that is absent:
            #: absent, nobody quotes it as the whole of what was said.
            "truncated": self.truncated,
            "total_cues": self.total_cues,
            # Said per track rather than inferred from `kind` by a reader: a
            # translation kept in the corpus must never be mistaken for the
            # thing it translates.
            "role": "transcript" if self.is_transcript else _role(self),
        }


def _role(track: CaptionTrack) -> str:
    if not track.readable:
        return "unreadable"
    if track.kind == TRANSCRIPT_KIND:
        return "empty"
    return "translation" if track.kind in {"subtitles", ""} else track.kind


#: Sets every track to `hidden`, waits for each to load or fail, and reads what
#: the browser parsed. The wait is bounded per track by `readyState` reaching a
#: terminal value rather than by a fixed sleep, because a fixed sleep is either
#: a wasted second on every capture or a silent truncation on a slow one.
#: `HTMLTrackElement.readyState` is 0 NONE, 1 LOADING, 2 LOADED, 3 ERROR; the
#: script compares against the numbers, so 2 and 3 are the terminal values.
_READ_TRACKS = """async ([timeoutMs, maxChars]) => {
    // The budget is spent HERE, cue by cue, and not on the Python side of the
    // bridge. A slice taken after this function returns bounds nothing it was
    // written to bound: every cue would already have been parsed, serialized,
    // carried across CDP and deserialized before the first one was dropped —
    // P7-9's shape exactly, a limit applied once the cost it exists to refuse
    // has been paid.
    //
    // `total` is the count BEFORE the budget, so a caller can tell a track
    // that ended from one that was cut. Reporting only what survived would
    // make a truncated transcript indistinguishable from a complete one.
    const readCues = (track, readable) => {
        if (!readable || !track || !track.cues) {
            return {cues: [], total: 0, truncated: false};
        }
        const all = [...track.cues];
        const cues = [];
        let chars = 0;
        for (const cue of all) {
            if (chars >= maxChars) break;
            cues.push({start: cue.startTime, end: cue.endTime, text: cue.text});
            chars += (cue.text || '').length;
        }
        // Reported, not left to be derived by comparing lengths: the Python
        // side drops blank-text cues, so `kept < total` is also true for a
        // track that was never cut. Only this loop knows which happened.
        return {cues, total: all.length, truncated: cues.length < all.length};
    };
    const elements = [...document.querySelectorAll('video, audio')];
    const errored = new WeakSet();
    for (const element of elements) {
        for (const node of element.querySelectorAll('track')) {
            node.addEventListener('error', () => errored.add(node));
        }
    }
    for (const element of elements) {
        for (const track of element.textTracks) {
            // `hidden`, not `showing`: the cues are parsed and never painted.
            track.mode = 'hidden';
        }
    }
    const deadline = Date.now() + timeoutMs;
    const settled = () => [...document.querySelectorAll('track')]
        .every(node => node.readyState === 2 || node.readyState === 3);
    while (Date.now() < deadline && !settled()) {
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    const out = [];
    for (const element of elements) {
        const source = element.currentSrc || element.src
            || element.querySelector('source')?.src || '';
        if (!source) continue;
        const tracks = [];
        for (const node of element.querySelectorAll('track')) {
            const track = node.track;
            const readable = node.readyState === 2 && !errored.has(node);
            tracks.push({
                kind: (track && track.kind) || node.getAttribute('kind') || '',
                language: (track && track.language) || node.getAttribute('srclang') || '',
                label: (track && track.label) || node.getAttribute('label') || '',
                sourceUrl: node.src || node.getAttribute('src') || '',
                readable,
                readyState: node.readyState,
                ...readCues(track, readable),
            });
        }
        if (tracks.length) out.push({source, tracks});
    }
    return out;
}"""


#: A ceiling on the text one `<track>` may contribute, spent inside the page.
#:
#: Characters rather than a cue count, for the reason `MAX_VIDEO_BYTES` gives
#: twenty lines from where the old cue cap sat: a walkthrough's steps are *the
#: document*, and cutting a legitimately long one at an arbitrary count
#: silently drops its ending. Caption cues are the document in the same sense,
#: and a count is arbitrary with respect to how much text a cue holds — five
#: thousand two-word cues are trivial and five thousand paragraphs are not.
#:
#: Two megabytes is around forty hours of speech, so no real transcript reaches
#: it. That is deliberate: this bound exists to stop a hostile page choosing
#: how much work a capture does (P7-10), not to trim honest material, and a
#: guard set where legitimate content lands would be trimming.
MAX_TRANSCRIPT_CHARS = 2_000_000


async def read_caption_tracks(
    page: Any, *, timeout_ms: int = 5000, max_chars: int = MAX_TRANSCRIPT_CHARS
) -> dict[str, tuple[CaptionTrack, ...]]:
    """Every media element's tracks, keyed by the element's source URL.

    Nothing here fails a capture. A page with no media, a browser that refuses,
    a track that never loads — each yields fewer tracks, and the caller says so
    in the record rather than pretending the element had none.

    A track cut by `max_chars` says so: on the track, in its manifest record,
    and in a warning. It used to be cut by `[:max_cues]` on this side of the
    bridge, which both bounded nothing — the cues were already parsed,
    serialized and transferred by then (P7-9) — and dropped the remainder in
    silence, publishing a partial transcript as a whole one.
    """
    try:
        found: list[dict[str, Any]] = await page.evaluate(
            _READ_TRACKS, [timeout_ms, max_chars]
        )
    except Exception as exc:  # pragma: no cover - a page that closed under us
        LOGGER.warning("Could not read caption tracks: %s", exc)
        return {}

    tracks: dict[str, tuple[CaptionTrack, ...]] = {}
    for entry in found:
        source = str(entry.get("source") or "")
        if not source:
            continue
        read: list[CaptionTrack] = []
        for item in entry.get("tracks") or []:
            cues = tuple(
                TranscriptCue(
                    start=float(cue.get("start") or 0.0),
                    end=float(cue.get("end") or 0.0),
                    text=str(cue.get("text") or "").strip(),
                )
                for cue in item.get("cues") or []
                if str(cue.get("text") or "").strip()
            )
            truncated = bool(item.get("truncated"))
            if truncated:
                LOGGER.warning(
                    "The %s caption track for %s was cut at %d characters: "
                    "%d of %d cues were read. Its transcript in this bundle is "
                    "PARTIAL and ends early.",
                    str(item.get("kind") or "untyped"),
                    source,
                    max_chars,
                    len(cues),
                    int(item.get("total") or 0),
                )
            read.append(
                CaptionTrack(
                    kind=str(item.get("kind") or "").lower(),
                    language=str(item.get("language") or ""),
                    label=str(item.get("label") or ""),
                    source_url=str(item.get("sourceUrl") or ""),
                    readable=bool(item.get("readable")),
                    cues=cues,
                    total_cues=int(item.get("total") or 0),
                    truncated=truncated,
                )
            )
        if read:
            tracks[source] = tuple(read)
    return tracks


def transcript_of(tracks: tuple[CaptionTrack, ...]) -> CaptionTrack | None:
    """The one track whose text may be published as the transcript, if any."""
    return next((track for track in tracks if track.is_transcript), None)


def media_directory(index: int) -> str:
    """`videos/media-01` — where one media element's caption artifacts land.

    Positional because a media element has no id of its own: a `<video>` is not
    a Guidde playbook and the page gives it nothing stable to name it by. The
    index is its order on the page, which is what `assets.json` already uses for
    the same reason.
    """
    return f"videos/media-{index:02d}"


def track_filename(track: CaptionTrack) -> str:
    """`transcript` for the one that is one; the rest say what they are."""
    if track.is_transcript:
        return "transcript"
    suffix = f"-{track.language}" if track.language else ""
    return f"{_role(track)}{suffix}"


def track_text(track: CaptionTrack) -> str:
    """The cues as plain text, one line each, for full-text search."""
    return "\n".join(cue.text for cue in track.cues).rstrip() + "\n"


def track_vtt(track: CaptionTrack, *, title: str, source_url: str) -> str:
    """The cues back out as WebVTT, on the media's own clock.

    Re-emitted rather than the fetched bytes copied: what the browser parsed is
    what the bundle should carry, and a file copied past the parser could
    disagree with the cues every other artifact was built from.
    """
    parts = [
        "WEBVTT",
        "",
        "NOTE",
        f"kind: {track.kind}",
        f"language: {track.language or '(unstated)'}",
        f"label: {track.label or '(none)'}",
        f"media: {source_url}",
        f"title: {title}" if title else "title: (none)",
        # Said in the file itself, because a `.vtt` gets opened on its own and
        # a translation must not be mistaken for a transcription anywhere.
        f"role: {track.record['role']}",
    ]
    if track.truncated:
        # Same reason as `role`, and a stronger one: a file that ends early
        # looks exactly like a file that ended.
        parts += [
            "",
            "NOTE",
            f"PARTIAL: cut at the read budget after {len(track.cues)} of "
            f"{track.total_cues} cues. This file ends before the media does.",
        ]
    parts.append("")
    for number, cue in enumerate(track.cues, start=1):
        parts += [
            str(number),
            f"{_vtt_timestamp(cue.start)} --> {_vtt_timestamp(cue.end)}",
            cue.text,
            "",
        ]
    return "\n".join(parts).rstrip() + "\n"


def _vtt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"
