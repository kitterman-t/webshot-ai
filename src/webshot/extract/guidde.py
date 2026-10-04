"""The Guidde bridge: playbook JSON in, WebShot's own dataclasses out.

This is the ONLY module allowed to know Guidde's API shape (CONTRIBUTING rule
1).  Everything downstream — the bundle writer, the PDF appendix, the chunk
records — receives `Walkthrough`, `WalkthroughStep`, and `TranscriptCue`, never a
raw response dict.

Rule 1 holds, but the stronger claim this docstring used to make does not, and
it is worth being accurate: **a second provider is not yet one file's work.**
The dataclasses are named for Guidde, `"provider": "guidde"` is written by
`bundle/videos.py` and pinned by `Literal["guidde"]` in two contract models,
and two raw upstream values reach `steps.json` unmapped (`WalkthroughStep.kind`, and
`audioNote.type` through `unverified_narration`).  Generalizing these into
provider-neutral `Walkthrough` types is the first task of the transcription
work, precisely because it is mechanical now and expensive after a second
producer exists — docs/13 records it as such.

Five upstream facts shape it.  The first four were measured against the live
service on 2026-08-22 rather than assumed (docs/09 P7-1..P7-4); the fifth was
measured against a real training journey and is the reason this module now
rewrites narration rather than only bounding it:

- **The endpoint needs the app's own bearer token.**  `GET
  app.guidde.com/c/v1/quickguidde?id=<id>` answers 401 to anything that is not
  the Guidde SPA, including a `fetch` issued from the share page's own context.
  So WebShot does not *make* the call: it navigates a page to the share URL,
  lets the app make its own authenticated call, and reads the response body off
  the wire.  There is deliberately no HTTP-client path and no token replay.

- **The intercepted response is wrapped.**  The call the page issues carries
  `&forEmbed=true` and answers `{"playbook": {…}, "data": {…}}` — `data` being
  branding configuration.  A bare `{…}` playbook is accepted too, so a change
  of wrapper on Guidde's side degrades to nothing.

- **Subtitle timings are per-step relative.**  Each step's `subtitles[]` starts
  again near zero; the document-level time of a cue is the sum of every prior
  step's `duration` plus the cue's own start.  Verified by reconstruction: the
  offsets computed here reproduce Guidde's own published `.vtt` cue for cue,
  and the step durations sum to the top-level `duration` exactly.  That is why
  one intercepted response is enough and the transcript is never fetched twice.

- **Narration carries correction markup.**  `{written||||alt=spoken}` is a
  pronunciation hint for the synthesizer and `{written||||spelling=Correct}` is
  the author fixing a written word, so the two publish *opposite* halves of the
  same shape.  Both were reaching `content.md` and `chunks.jsonl` verbatim
  because nothing looked for them; they are resolved here, at the bridge, so
  every downstream surface gets the resolved text from one place.

- **Steps without text are normal.**  On the sample playbook 4 of 11 steps
  carry no `audioNote` and 3 carry no subtitles at all: they are silent screen
  action, not missing data.  They still carry screenshots.  Nothing here
  requires `description` or `subtitles` on a step, and the walkthrough says
  "silent step" rather than pretending.

What this produces is **the complete narration plus the per-step written
instructions** — not a transcript in the lecture-recording sense.  For a
playbook whose `audioNote.type` is `textToSpeech` the audio is generated *from*
the authored text, so the text is the source and there is nothing an ASR pass
could recover.  A playbook narrated any other way is unverified territory:
`unverified_narration` says so, and the caller turns it into a warning rather
than assuming the reading is complete.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from math import isfinite
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..capture.snapshot import HEADING_JS, LOCATOR_JS, VISIBLE_JS

#: Where a playbook can be watched.  Overridable only so the test suite can
#: point the fetch at a local stand-in — no test may touch live Guidde
#: (docs/06).  Detection is *not* parameterized by it: a page names Guidde by
#: its real host whatever origin WebShot later reads the playbook from.
DEFAULT_ORIGIN = "https://app.guidde.com"

#: The hosts a Guidde URL can carry.  Matched as the registrable suffix, so
#: `app.guidde.com` and `embed.app.guidde.com` both qualify and
#: `guidde.com.example.invalid` does not.
GUIDDE_HOSTS = ("guidde.com",)

#: The id as a path segment, on both surfaces that carry one:
#:   share  app.guidde.com/share/playbooks/<id>
#:   embed  embed.app.guidde.com/playbooks/<id>
#: Firestore ids are 20-22 characters of the URL-safe alphabet; the bounds are
#: wide enough to survive a change of generator and tight enough that a slug
#: like `/playbooks/getting-started` cannot pass for one.
PLAYBOOK_PATH = re.compile(r"/(?:share/)?playbooks/([A-Za-z0-9_-]{16,64})(?:$|[/?#])")

#: The call the share page makes to hydrate itself.  Matched as a substring of
#: the request URL, because the app appends parameters of its own.
API_MARKER = "/c/v1/quickguidde"

#: Attributes that can carry the player's URL.  An embed reaches a page as an
#: iframe `src`, a lazily-hydrated `data-src`, a link the reader can follow, or
#: an `og:video`/`twitter:player` `content` — the id is a path segment in every
#: one of them, which is what makes scanning attributes sufficient and clicking
#: the player's link control unnecessary (docs/09 P7-2).
URL_ATTRIBUTES = (
    "src",
    "href",
    "data-src",
    "data-href",
    "data-url",
    "data-video-url",
    "content",
)

#: Guidde's text-to-speech scripts carry timing directives (`/pause-2.2/`) that
#: are instructions to the synthesizer, not words anyone said.
PAUSE_DIRECTIVE = re.compile(r"/pause-\d+(?:\.\d+)?/")

#: The other thing those scripts carry: inline correction markup, written
#: `{written||||directive=replacement}`.  Measured in a live journey, 95
#: occurrences reached `content.md` and 48 reached `chunks.jsonl` across 11 of
#: 129 modules, because nothing here had ever looked for it.
CORRECTION_MARKUP = re.compile(r"\{([^{}|]*)\|{4}([A-Za-z][A-Za-z0-9_-]*)=([^{}|]*)\}")

#: **Which half is the text a reader should see depends on the directive, and
#: getting it backwards is the whole risk.**
#:
#: `alt` is a pronunciation hint aimed at the synthesizer, so the *written*
#: half is the display text: `{does||||alt=duzz}` and
#: `{attribute||||alt=At-tribute}` are spellings for a voice, and
#: `{attribute||||alt=attribute}` — both halves identical — is the proof that
#: this is a speech hint rather than a correction.
CORRECTION_KEEPS_WRITTEN = frozenset({"alt"})

#: `spelling` is the opposite: the author is correcting the written word, so the
#: *replacement* is what must be published.  93 of the 95 occurrences measured
#: were `alt` and 2 were `spelling`, which is exactly why "keep the first half"
#: is the tempting wrong answer — it is right 93 times and publishes the
#: misspelling the author corrected, a product name, in the place a reader
#: notices.
CORRECTION_KEEPS_REPLACEMENT = frozenset({"spelling"})

#: The narration style whose fidelity this bridge has been verified against.
VERIFIED_NARRATION = "textToSpeech"

#: Screenshot fields in the order of preference, with what each one is.  The
#: annotated still is first because it carries the arrows and highlights that
#: make a step readable without the video; the cover and closing steps have no
#: `docScreenshot` at all, which is why this is a chain rather than a field.
SCREENSHOT_FIELDS: tuple[tuple[str, str], ...] = (
    ("drawnScreenshot", "annotated"),
    ("docScreenshot", "document"),
    ("previewScreenshot", "preview"),
    ("staticScreenshot", "static"),
    ("screenshot", "raw"),
)


#: The most steps one playbook may contribute. The count comes straight out of
#: third-party JSON and drives a request, a chunk record, and a block of
#: appendix HTML each — so it is page data choosing how much work a capture
#: does. Far above any real walkthrough (the sample has eleven).
MAX_STEPS = 500

#: And the most players one page may contribute. Each costs two navigations
#: with their own timeouts, so an unbounded count is an unbounded capture.
#: Applied by the caller rather than here: detection reports what the page has,
#: and how much of it a capture is willing to read is policy, which has to be
#: able to say out loud that it stopped (CONTRIBUTING rule 5).
MAX_EMBEDS = 20


class GuiddeUnavailable(Exception):
    """The playbook could not be read.

    Never fatal to a capture: the page is the deliverable and the walkthrough
    is enrichment, so `pipeline` turns this into a manifest warning
    (CONTRIBUTING rule 5).
    """


# --------------------------------------------------------------------------- #
# What crosses the boundary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    """One narration segment, timed against the **whole** playbook."""

    text: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class WalkthroughStep:
    """One step of a walkthrough, in WebShot's terms rather than Guidde's."""

    number: int
    step_id: str
    #: "cover", "step", or "end" — Guidde's own division, kept because a reader
    #: should be able to tell the title card from the procedure.
    kind: str
    title: str
    #: The written instruction, from the first of `description`, the narration
    #: script, the narration itself, and the step title that has any text.
    #: Empty only for a step that carries none of them.
    instruction: str
    #: The narration script with the synthesizer's timing directives removed.
    narration: str
    #: Seconds from the start of the playbook to the start of this step.
    start: float
    duration: float
    cues: tuple[TranscriptCue, ...] = ()
    screenshot_url: str = ""
    #: Which of the stills above `screenshot_url` came from.
    screenshot_kind: str = ""
    clip_url: str = ""

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def silent(self) -> bool:
        """Whether this step has no narration — normal, not a defect."""
        return not self.cues and not self.narration


@dataclass(frozen=True, slots=True)
class Walkthrough:
    """A whole walkthrough: what the bundle, the chunks, and the PDF read.

    Provider-neutral by name and by field, ahead of a second producer rather
    than after one (docs/13, *One refactor comes first*). `id` is whatever the
    producing service calls this walkthrough — a Guidde playbook id today — and
    `provider` says which service that was, so a reader of a bundle holding two
    kinds can tell them apart.

    The published vocabulary does not move with it. `manifest.videos[]` and
    `assets.json` still say `playbook_id`, because those are the contract and a
    rename there would be a format change wearing a refactor's clothes.
    """

    #: The producing service's own identifier for this walkthrough.
    id: str
    title: str
    duration: float
    steps: tuple[WalkthroughStep, ...]
    source_url: str
    #: Which service produced it. One value today. The field exists so that a
    #: second producer would not mean touching every construction site at the
    #: moment there are two; docs/13 sketches one (an ASR path, L3) and leaves
    #: it out of scope for this repository.
    provider: str = "guidde"
    organization: str = ""
    language: str = ""
    last_updated_by: str = ""
    source_recording_url: str = ""
    #: Narration styles seen that this bridge has *not* been verified against.
    #: Non-empty means the caller should say so rather than assume the reading
    #: is complete (docs/09 P7-4).
    unverified_narration: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def narrated_steps(self) -> int:
        return sum(1 for step in self.steps if not step.silent)


@dataclass(frozen=True, slots=True)
class GuiddeEmbed:
    """A player found on a captured page, and where on the page it was."""

    playbook_id: str
    #: The URL the id was read out of, as the page wrote it.
    url: str
    #: A CSS locator for the element carrying it, in `capture/snapshot.py`'s
    #: convention, so a video's position can be compared with the page's own
    #: block records.
    locator: str = ""
    #: The heading the player sits under.  This is what places the walkthrough
    #: in `content.md`: the player element itself does not survive
    #: sanitization (an `<iframe>` is not in the snapshot allowlist), so the
    #: section it was in is the finest anchor the page's own document model
    #: actually has.
    heading: str = ""


# --------------------------------------------------------------------------- #
# Resolving an id
# --------------------------------------------------------------------------- #


def _is_guidde_host(host: str) -> bool:
    host = host.lower()
    return any(host == known or host.endswith(f".{known}") for known in GUIDDE_HOSTS)


def playbook_id(url: str) -> str | None:
    """The playbook a Guidde URL names, or None if it names none.

    Reads the path first and the `id` query parameter second, which between
    them cover every surface Guidde publishes: the share page, the embeddable
    player, and the hydration call itself.  A non-Guidde host is refused before
    either, so a look-alike domain carrying a plausible path resolves to
    nothing.
    """
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https", ""}:
        return None
    if not _is_guidde_host(parsed.hostname or ""):
        return None
    found = PLAYBOOK_PATH.search(parsed.path)
    if found:
        return found.group(1)
    for part in parsed.query.split("&"):
        name, _, value = part.partition("=")
        if name == "id" and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", value):
            return value
    return None


def share_url(identifier: str, origin: str = DEFAULT_ORIGIN) -> str:
    """Where to send a browser to make the app hydrate this playbook."""
    return f"{origin.rstrip('/')}/share/playbooks/{identifier}"


class _EmbedScanner(HTMLParser):
    """Every Guidde URL in a document, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value or name.lower() not in URL_ATTRIBUTES:
                continue
            identifier = playbook_id(value)
            if identifier:
                self.found.append((identifier, value))


def embeds_in_html(html: str) -> list[GuiddeEmbed]:
    """Guidde players in saved HTML, deduplicated, first occurrence winning.

    The offline half of detection: the pipeline scans the *live* prepared DOM
    (which also sees attributes a framework set after load), and this scans a
    document that was already saved.  One rule for reading a URL serves both,
    so a shape that works in a test works in a capture.
    """
    scanner = _EmbedScanner()
    scanner.feed(html)
    return _deduplicated(scanner.found)


def _deduplicated(found: Sequence[tuple[str, str]]) -> list[GuiddeEmbed]:
    """First mention of each playbook wins; the list's order is document order."""
    embeds: list[GuiddeEmbed] = []
    seen: set[str] = set()
    for identifier, url in found:
        if identifier in seen:
            continue
        seen.add(identifier)
        embeds.append(GuiddeEmbed(identifier, url))
    return embeds


#: Collect every URL-bearing attribute in document order, with the locator and
#: the enclosing heading for the element carrying it.  Hidden elements are
#: skipped for the same reason the snapshot skips them: the bundle describes
#: what was captured, and a walkthrough for a player `--exclude` removed would
#: be a claim about a different page.  The walk covers `<head>` too, where an
#: `og:video` can name the embed — which is why the locator has to survive an
#: element with no parent.
_SCAN_SCRIPT = (
    """attributes => {
    const locator = __LOCATOR__;
    const heading = __HEADING__;
    const visible = __VISIBLE__;
    const found = [];
    for (const element of document.querySelectorAll('*')) {
        if (element.closest('[data-webshot-hidden="true"]')) continue;
        // `data-webshot-hidden` is what `--exclude` sets; it is not what a
        // responsive layout or a preloaded second player uses. Those are
        // `display:none`, `visibility:hidden` or zero geometry, and treating
        // them as captured content appended walkthroughs the reader never saw
        // in the PDF and inflated `video_tally` with duplicates of the one
        // they did (docs/09 P8-25). Same predicate the semantic extraction
        // uses, and applied only to elements in the body: a `<meta>` in the
        // head has no box and is not hidden, it is metadata.
        if (element.ownerDocument.body?.contains(element) && !visible(element)) continue;
        for (const attribute of attributes) {
            const value = element.getAttribute(attribute);
            // The cheapest possible gate, first. The locator walk and the
            // heading lookup both cost real work — the latter forces layout
            // through innerText — and without this every `href` on an ordinary
            // page would pay for both before Python threw the result away.
            // Lowercased on both sides. `getAttribute` returns the author's
            // casing, and `https://APP.GUIDDE.COM/...` is a valid spelling of
            // the same host — dropped here, before `playbook_id`'s own
            // case-insensitive hostname check could see it, so the walkthrough
            // was silently absent from the bundle, the appendix and the tally
            // (docs/09 P8-24).
            if (!value || !value.toLowerCase().includes(__HOST__)) continue;
            found.push({
                url: value,
                locator: document.body.contains(element) ? locator(element) : '',
                heading: heading(element).text,
            });
        }
    }
    return found;
}""".replace("__LOCATOR__", LOCATOR_JS)
    .replace("__HEADING__", HEADING_JS)
    .replace("__VISIBLE__", VISIBLE_JS)
    .replace("__HOST__", json.dumps(GUIDDE_HOSTS[0].lower()))
)


async def detect_embeds(page: Any) -> list[GuiddeEmbed]:
    """Guidde players in the prepared DOM, in document order.

    Run against the page as capture left it and *after* the visual harvest, so
    what it reports is the set of players a reader of the PDF actually saw.

    A failure here is raised rather than swallowed.  Returning an empty list on
    a broken scan is indistinguishable from a page with no video, and the whole
    point of the manifest's warnings is that a capture never quietly does less
    than it claims (CONTRIBUTING rule 5) — the caller turns this into a warning
    that says so.
    """
    try:
        candidates: list[dict[str, Any]] = await page.evaluate(
            _SCAN_SCRIPT, list(URL_ATTRIBUTES)
        )
    except PlaywrightError as exc:
        raise GuiddeUnavailable(
            f"the page could not be scanned for embedded videos: {exc}"
        ) from exc
    hits: list[tuple[str, str]] = []
    context: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        identifier = playbook_id(str(candidate.get("url") or ""))
        if not identifier:
            continue
        hits.append((identifier, str(candidate["url"])))
        context.setdefault(identifier, candidate)
    embeds = []
    for embed in _deduplicated(hits):
        where = context.get(embed.playbook_id, {})
        embeds.append(
            GuiddeEmbed(
                embed.playbook_id,
                embed.url,
                locator=str(where.get("locator") or ""),
                heading=str(where.get("heading") or ""),
            )
        )
    return embeds


# --------------------------------------------------------------------------- #
# Reading a playbook
# --------------------------------------------------------------------------- #


#: How much of a playbook string is kept. These land in `manifest.json`, in
#: `steps.json`, in every chunk record, and in the README embedded in the PDF,
#: so an unbounded one is an unbounded artifact — a 10 MB title would be
#: written a dozen times over. The prose limit is generous enough that no real
#: instruction reaches it; the label limit is what a heading can sensibly hold.
MAX_LABEL_CHARACTERS = 300
MAX_PROSE_CHARACTERS = 20_000

#: Marks a value this bridge shortened, so a reader can tell a truncation from
#: an author who simply stopped there.
TRUNCATION_MARK = "… [truncated by WebShot]"


def _text(value: Any, limit: int = MAX_PROSE_CHARACTERS) -> str:
    """One playbook string, stripped and bounded."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARK)] + TRUNCATION_MARK


#: What `_resolve_corrections` reports when correction markup reached it in a
#: shape `CORRECTION_MARKUP` does not match — an unclosed brace, a nested one,
#: a directive name outside the pattern.  Such text ships to the deliverable
#: exactly as written, which is the defect this resolution exists to stop, so
#: it is named rather than left to be noticed in a published bundle.
UNPARSED_CORRECTION = "(markup this bridge could not parse)"


def _resolve_corrections(value: str, unresolved: set[str]) -> str:
    """One playbook string with its correction markup resolved to plain words.

    `unresolved` accumulates every directive that could not be resolved, so the
    caller can say so in the manifest.  An unknown directive keeps the *written*
    half — the literal word the author typed — because guessing which half an
    unrecognised directive publishes would silently rewrite what was said, and a
    wrong word is worse than a reported gap (CONTRIBUTING rule 5).
    """

    def resolve(match: re.Match[str]) -> str:
        written, directive, replacement = match.groups()
        name = directive.lower()
        if name in CORRECTION_KEEPS_REPLACEMENT:
            return replacement
        if name not in CORRECTION_KEEPS_WRITTEN:
            unresolved.add(directive)
        return written

    resolved = CORRECTION_MARKUP.sub(resolve, value)
    if "||||" in resolved:
        unresolved.add(UNPARSED_CORRECTION)
    return resolved


def _prose(value: Any, unresolved: set[str], limit: int = MAX_PROSE_CHARACTERS) -> str:
    """One playbook string of human-readable text, bounded and resolved.

    Separate from `_text` rather than folded into it: `_text` also carries ids,
    URLs and Guidde's own enum-ish values, and none of those is prose whose
    braces mean anything.

    Resolved *before* the length bound, not after, so the limit applies to what
    is actually published and a truncation cannot cut a directive in half and
    leave the remains of one in a deliverable.
    """
    if not isinstance(value, str):
        return ""
    return _text(_resolve_corrections(value, unresolved), limit)


def _number(value: Any) -> float:
    """A duration or offset, or 0.0 for anything that is not a real number.

    `json.loads` accepts bare `NaN` and `Infinity`, and `1e400` is ordinary
    valid JSON that parses to `inf` — so a playbook can hand us a float that
    `int()` refuses, and every timestamp helper downstream calls `int()`.
    Non-finite values are therefore stopped at the bridge rather than left to
    crash a capture that had already rendered (docs/09 P7-10).

    `bool` is excluded explicitly: it passes `isinstance(x, int)`, so
    `"duration": true` would otherwise silently become a one-second step.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    return number if isfinite(number) else 0.0


def _clean_narration(markdown: str) -> str:
    """The narration script as words, without the synthesizer's directives."""
    return " ".join(PAUSE_DIRECTIVE.sub(" ", markdown).split())


def _screenshot(step: dict[str, Any]) -> tuple[str, str]:
    for field_name, kind in SCREENSHOT_FIELDS:
        url = _text(step.get(field_name))
        if url:
            return url, kind
    return "", ""


def _cues(
    step: dict[str, Any], offset: float, unresolved: set[str]
) -> tuple[TranscriptCue, ...]:
    """This step's subtitles, moved onto the playbook's own clock."""
    cues = []
    for raw in step.get("subtitles") or []:
        if not isinstance(raw, dict):
            continue
        text = _prose(raw.get("text"), unresolved)
        if not text:
            continue
        start = offset + _number(raw.get("start"))
        end = offset + _number(raw.get("end"))
        cues.append(TranscriptCue(text, start, max(start, end)))
    return tuple(cues)


def _instruction(
    description: str, narration: str, cues: Sequence[TranscriptCue], title: str
) -> str:
    """The written instruction for a step, from whichever field has one.

    Deliberately a chain rather than a required field: a silent transition
    carries no `description` and no narration, and demanding either would fail
    on an ordinary playbook (docs/09 P7-3).  The title is last because Guidde
    fills it with "Segment" when there is nothing else to say.
    """
    if description:
        return description
    if narration:
        return narration
    joined = " ".join(cue.text for cue in cues).strip()
    if joined:
        return joined
    return title


def _steps(
    raw_steps: Sequence[Any], unresolved: set[str]
) -> tuple[tuple[WalkthroughStep, ...], set[str]]:
    steps: list[WalkthroughStep] = []
    narration_styles: set[str] = set()
    offset = 0.0
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            continue
        duration = _number(raw.get("duration"))
        audio_note = raw.get("audioNote")
        audio_note = audio_note if isinstance(audio_note, dict) else {}
        narration_source = _text(audio_note.get("type"))
        if narration_source:
            narration_styles.add(narration_source)
        narration = _clean_narration(_prose(audio_note.get("markdown"), unresolved))
        cues = _cues(raw, offset, unresolved)
        title = _prose(raw.get("title"), unresolved, MAX_LABEL_CHARACTERS)
        screenshot_url, screenshot_kind = _screenshot(raw)
        # Clamped as it accumulates, not only as it arrives: three steps of
        # `1e308` sum to `inf` from three individually finite values, and the
        # result reaches `steps.json` and the manifest (docs/09 P7-12).
        if not isfinite(offset + duration):
            duration = 0.0
        steps.append(
            WalkthroughStep(
                number=index,
                step_id=_text(raw.get("id")),
                kind=_text(raw.get("kind")) or "step",
                title=title,
                instruction=_instruction(
                    _prose(raw.get("description"), unresolved), narration, cues, title
                ),
                narration=narration,
                start=offset,
                duration=duration,
                cues=cues,
                screenshot_url=screenshot_url,
                screenshot_kind=screenshot_kind,
                clip_url=_text(raw.get("videoUrl")),
            )
        )
        offset += duration
    return tuple(steps), narration_styles


def parse_playbook(
    payload: bytes | str, *, expected_id: str, source_url: str
) -> Walkthrough:
    """One intercepted response into one `Walkthrough`.

    Pure and synchronous on purpose: it is the whole of what the offline tests
    exercise, against a recorded response rather than a live service.
    """
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GuiddeUnavailable(
            f"Guidde returned something that is not JSON for playbook "
            f"{expected_id}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise GuiddeUnavailable(
            f"Guidde returned a {type(document).__name__} rather than a "
            f"playbook for {expected_id}."
        )
    # The embed call wraps the playbook beside its branding configuration; a
    # bare playbook is accepted so a change of wrapper costs nothing.
    raw = document.get("playbook")
    playbook = raw if isinstance(raw, dict) else document

    raw_steps = playbook.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise GuiddeUnavailable(
            f"Guidde's response for playbook {expected_id} carries no steps, so "
            "there is no walkthrough to record."
        )
    over_limit = max(0, len(raw_steps) - MAX_STEPS)
    # Every directive the resolution below could not resolve, from the steps and
    # from the playbook's own title, so one warning names all of them.
    unresolved: set[str] = set()
    steps, narration_styles = _steps(raw_steps[:MAX_STEPS], unresolved)
    if not steps:
        raise GuiddeUnavailable(
            f"None of the {len(raw_steps)} entries in playbook {expected_id} is "
            "a readable step."
        )

    found_id = _text(playbook.get("id"))
    if found_id and found_id != expected_id:
        raise GuiddeUnavailable(
            f"Guidde answered with playbook {found_id} for a request for "
            f"{expected_id}; the walkthrough is not recorded rather than "
            "recorded against the wrong video."
        )
    duration = _number(playbook.get("duration")) or steps[-1].end
    if not isfinite(duration):  # pragma: no cover - the clamp above prevents it
        duration = 0.0
    # Resolved here rather than in the constructor below, because `unresolved`
    # has to be complete before the warning that reports it is composed — a
    # field resolved after the warning is a field whose unknown directive is
    # silently dropped by the check written to report it.
    #
    # `organization` and `last_updated_by` are in this set for a reason worth
    # stating: both are published in the walkthrough's provenance line, and
    # "correction markup does not reach a deliverable" is only true if the
    # resolution covers every field that reaches one.
    title = _prose(playbook.get("title"), unresolved, MAX_LABEL_CHARACTERS)
    organization = _prose(playbook.get("orgName"), unresolved, MAX_LABEL_CHARACTERS)
    last_updated_by = _prose(
        playbook.get("lastUpdatedBy"), unresolved, MAX_LABEL_CHARACTERS
    )
    warnings: list[str] = []
    if over_limit:
        warnings.append(
            f"Guidde playbook {expected_id} declares "
            f"{len(raw_steps)} steps; only the first {MAX_STEPS} were "
            f"documented and {over_limit} were skipped."
        )
    if unresolved:
        warnings.append(
            f"Guidde playbook {expected_id} carries narration correction markup "
            f"WebShot does not resolve: {', '.join(sorted(unresolved))}. Where "
            "the directive was named but unknown the written word was kept — "
            "guessing which half of an unrecognised directive a reader should "
            "see would silently rewrite the author's words — and where the "
            "markup itself did not parse it reached the text as written."
        )
    overrun = max((cue.end for step in steps for cue in step.cues), default=0.0)
    if overrun > duration + 0.05:
        # Guidde's own published .vtt has the same overrun on the sample
        # playbook, so this is upstream data rather than a reconstruction
        # error — recorded, not corrected (docs/09 P7-3).
        warnings.append(
            f"Guidde playbook {expected_id} times its last narration cue at "
            f"{overrun:.1f}s against a stated duration of {duration:.1f}s; the "
            "timings are reported as Guidde published them."
        )

    language = playbook.get("language")
    recording = playbook.get("sourceCaptureVideo")
    recording = recording if isinstance(recording, dict) else {}
    return Walkthrough(
        id=found_id or expected_id,
        title=title or f"Guidde playbook {expected_id}",
        duration=duration,
        steps=steps,
        source_url=source_url,
        organization=organization,
        # A language code is not prose; it is left as `_text` reads it.
        language=_text(language.get("langCode")) if isinstance(language, dict) else "",
        last_updated_by=last_updated_by,
        source_recording_url=_text(recording.get("url")),
        unverified_narration=tuple(sorted(narration_styles - {VERIFIED_NARRATION})),
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------- #
# Interception
# --------------------------------------------------------------------------- #


async def fetch_playbook(
    context: BrowserContext,
    identifier: str,
    *,
    origin: str = DEFAULT_ORIGIN,
    timeout_ms: int = 45_000,
) -> Walkthrough:
    """Read a playbook by letting Guidde's own page fetch it.

    The hydration call is authenticated with a token only the app has, so
    WebShot never issues it: a page is navigated to the share URL and the
    response the app makes for itself is read off the wire.  That is the same
    thing a person opening the link sees, which is the whole of what
    docs/11 permits.

    The page is opened in the capture's own context so an operator who signed
    in with `--auth-profile` gets whatever that session grants, and closed
    before returning whatever happened.
    """
    target = share_url(identifier, origin)
    page = await context.new_page()
    captured: list[bytes] = []
    statuses: list[int] = []
    # Set by the handler once a body is in hand.  A plain `wait_for_event`
    # would race it: its predicate runs when the event fires, which is before
    # the async handler has finished reading the body.
    arrived = asyncio.Event()

    async def on_response(response: Response) -> None:
        # The id as well as the endpoint: a share page may hydrate more than
        # one playbook (a playlist names its members), and taking whichever
        # response arrived first would file another video's walkthrough under
        # this one's id — a wrong answer that looks entirely right.
        if API_MARKER not in response.url or identifier not in response.url:
            return
        statuses.append(response.status)
        if response.status != 200 or captured:
            return
        try:
            captured.append(await response.body())
        except PlaywrightError:
            # A body already discarded by the browser is a race, not a
            # refusal: the wait below simply times out and says so.
            return
        arrived.set()

    page.on("response", on_response)
    try:
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise GuiddeUnavailable(f"{target} did not load in time: {exc}") from exc
        except PlaywrightError as exc:
            raise GuiddeUnavailable(f"{target} could not be reached: {exc}") from exc
        try:
            await asyncio.wait_for(arrived.wait(), timeout=timeout_ms / 1000)
        except TimeoutError:
            pass
        if not captured:
            if statuses and all(status != 200 for status in statuses):
                raise GuiddeUnavailable(
                    f"Guidde answered HTTP {statuses[0]} for playbook "
                    f"{identifier}. A playbook that is not shared publicly is "
                    "only readable by a browser signed in to that workspace."
                )
            raise GuiddeUnavailable(
                f"{target} did not hydrate: no {API_MARKER} response for "
                f"{identifier} was seen within {timeout_ms / 1000:g}s"
                + ("" if statuses else " (the page made no such call)")
                + "."
            )
    finally:
        page.remove_listener("response", on_response)
        await page.close()
    return parse_playbook(captured[0], expected_id=identifier, source_url=target)


def asset_urls(playbook: Walkthrough, *, clips: bool) -> Iterator[tuple[str, str]]:
    """Every file worth downloading for `playbook`, as (kind, url).

    Screenshots always; the step clips and the source recording only when the
    caller asked, because an agent cannot watch a video and a bundle whose bulk
    is unplayable media is worse for the stated purpose, not better.
    """
    for step in playbook.steps:
        if step.screenshot_url:
            yield f"step-{step.number:03d}", step.screenshot_url
    if not clips:
        return
    for step in playbook.steps:
        if step.clip_url:
            yield f"clip-{step.number:03d}", step.clip_url
    if playbook.source_recording_url:
        yield "source-recording", playbook.source_recording_url
