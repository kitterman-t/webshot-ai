"""Path 1: reading a media element's `<track>`, and the three states it has.

The rule under test is an **allowlist**: only `kind="captions"` becomes a
transcript. Everything else — `subtitles`, `descriptions`, a `<track>` with no
`kind` at all — is recorded as what it is. The naive implementation below is a
denylist, which is what someone writes first, and it is wrong in the direction
that matters: HTML defaults a missing `kind` to `subtitles`, so a denylist lets
exactly the ambiguous case through wearing the word transcript.

Scheme matters here and is the reason for two fixtures rather than one. Over
`file://` every document is an opaque origin, so the browser fetches the caption
file and discards the cues. That is a real state with its own note, and it is
also why the `embedded-media` golden cannot exercise this feature and
`captions-read` serves the same page over loopback.
"""

from __future__ import annotations

import asyncio
import http.server
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from conftest import serve
from webshot.bundle import videos as video_bundle
from webshot.bundle.captions import (
    CaptionTrack,
    read_caption_tracks,
    track_filename,
    track_vtt,
    transcript_of,
)
from webshot.bundle.videos import MediaVideo
from webshot.extract.guidde import TranscriptCue
from webshot.render.embed import _video_section

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _static() -> type[http.server.BaseHTTPRequestHandler]:
    directory = str(FIXTURES)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, *args: Any) -> None:
            """Quiet."""

    return Handler


def read(url: str, **kwargs: Any) -> dict[str, tuple[CaptionTrack, ...]]:
    """Open `url` in a real browser and read its tracks.

    `kwargs` reach `read_caption_tracks`, so a test can drive the read budget
    down to where it bites on a two-cue fixture. A bound proven only at its
    shipping value is a bound proven never to fire.
    """

    async def run() -> dict[str, tuple[CaptionTrack, ...]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url)
                return await read_caption_tracks(page, **kwargs)
            finally:
                await browser.close()

    return asyncio.run(run())


def over_http(page: str = "captions_page.html") -> dict[str, tuple[CaptionTrack, ...]]:
    """Read one fixture's tracks over loopback, where cues actually load.

    `captions_page.html` by default: it carries a `captions` track and a
    `subtitles` track pointing at *different* files, which is what makes the
    transcript and the translation distinguishable at the level of the text.
    `media_page.html` is the input a frozen parity baseline was recorded from
    and cannot gain a second file (docs/09 P10-8), so it is named explicitly
    by the one test that needs that exact page on both schemes.
    """
    with serve(_static()) as origin:
        return read(f"{origin}/{page}")


@pytest.mark.browser
def test_a_captions_track_is_read_and_becomes_the_transcript() -> None:
    tracks = over_http()
    video = next(t for source, t in tracks.items() if "sample-video" in source)
    assert len(video) == 1
    track = video[0]
    assert track.kind == "captions"
    assert track.language == "en"
    assert track.readable is True
    assert track.is_transcript is True
    assert [cue.text for cue in track.cues] == [
        "Welcome to the quarterly capture workflow review.",
        "Every page element you see becomes part of the record.",
    ]
    assert transcript_of(video) is track


@pytest.mark.browser
def test_a_subtitles_track_is_a_translation_and_never_the_transcript() -> None:
    """A German subtitle track is not a transcript of English speech."""
    tracks = over_http()
    audio = next(t for source, t in tracks.items() if "sample-audio" in source)
    track = audio[0]
    assert track.kind == "subtitles"
    assert track.readable is True
    assert track.cues, "the track was not read, so this proves nothing about kind"
    assert track.is_transcript is False
    assert track.record["role"] == "translation"
    assert transcript_of(audio) is None
    # Read, and demonstrably a different file from the captions — with one
    # shared source an implementation that ignored `kind` would produce
    # identical text either way and no test could tell them apart.
    assert "Willkommen" in track.cues[0].text


@pytest.mark.browser
def test_the_naive_denylist_would_call_an_untyped_track_a_transcript() -> None:
    """Why this is an allowlist, in the one case that decides it.

    HTML defaults a `<track>` with no `kind` to `subtitles`. The denylist
    somebody writes first — "anything that is not subtitles is a transcript" —
    reads the *attribute*, finds nothing, and publishes a translation as the
    video's transcript. The allowlist asks whether it is `captions` and gets no
    for the same input.
    """
    untyped = CaptionTrack(
        kind="subtitles",  # what the browser reports for a missing kind
        language="de",
        label="",
        source_url="subtitles-de.vtt",
        readable=True,
        cues=(),
    )
    # The naive test, against the raw attribute rather than the parsed kind.
    raw_attribute = ""
    assert raw_attribute != "subtitles", "the denylist's own premise"
    # The allowlist's answer, on the same track.
    assert untyped.is_transcript is False
    assert untyped.record["role"] == "translation"


@pytest.mark.browser
def test_a_declared_track_that_cannot_be_read_is_its_own_state() -> None:
    """Three states, not two — and the third is why `embedded-media` stays file://.

    Over `file://` the browser fetches the caption file and discards the cues
    because the document is an opaque origin. Recording that as "no caption
    track" would send a reader away from a transcript that exists; recording it
    as unread would repeat a note that stopped being true when path 1 shipped.
    """
    tracks = read((FIXTURES / "media_page.html").as_uri())
    assert tracks, "no media elements were seen at all"
    every = [track for group in tracks.values() for track in group]
    assert every, "no tracks were declared, so this proves nothing"
    assert all(not track.readable for track in every)
    assert all(track.record["role"] == "unreadable" for track in every)
    assert all(not track.cues for track in every)
    # And over http the same page reads them — the scheme is the only difference.
    assert any(
        track.readable
        for group in over_http("media_page.html").values()
        for track in group
    )


def test_a_translation_names_itself_inside_its_own_file() -> None:
    """A `.vtt` gets opened on its own, so the role travels with the bytes."""
    track = CaptionTrack(
        kind="subtitles",
        language="de",
        label="German subtitles",
        source_url="subtitles-de.vtt",
        readable=True,
        cues=(),
    )
    text = track_vtt(track, title="Operations audio briefing", source_url="a.mp3")
    assert "role: translation" in text
    assert "kind: subtitles" in text
    assert track_filename(track) == "translation-de"


def test_a_transcript_is_named_transcript_and_nothing_else_is() -> None:
    from webshot.extract.guidde import TranscriptCue

    captions = CaptionTrack(
        kind="captions",
        language="en",
        label="",
        source_url="c.vtt",
        readable=True,
        cues=(TranscriptCue(start=0.0, end=1.0, text="Spoken words."),),
    )
    assert track_filename(captions) == "transcript"
    # A captions track that loaded and holds nothing is not a transcript
    # either — there is no text to be one.
    empty = CaptionTrack(
        kind="captions", language="en", label="", source_url="c.vtt", readable=True
    )
    assert empty.is_transcript is False
    assert empty.record["role"] == "empty"
    for kind in ("subtitles", "descriptions", "chapters", "metadata", ""):
        other = CaptionTrack(
            kind=kind, language="en", label="", source_url="x.vtt", readable=True
        )
        assert other.is_transcript is False, kind
        assert track_filename(other) != "transcript", kind


def test_a_read_transcript_reaches_every_surface_and_a_translation_none() -> None:
    """The words go where an agent looks; the translation goes only to a file.

    Held as one test because the two halves are one decision. A transcript
    readable only in `videos/media-NN/transcript.txt` is absent from every
    surface a search touches, which is the same silence the untranscribed note
    exists to prevent. A translation in those surfaces is worse than absent:
    retrieved, it is indistinguishable from a record of what was said.
    """
    video = MediaVideo(
        source_url="https://example.invalid/a.mp4",
        duration_seconds=60.0,
        poster_url="",
        title="Captioned",
        heading="Captioned",
        tracks=(
            CaptionTrack(
                kind="captions",
                language="en",
                label="English",
                source_url="captions.vtt",
                readable=True,
                cues=(TranscriptCue(start=0.0, end=2.0, text="SPOKEN WORDS HERE"),),
            ),
        ),
    )
    audio = MediaVideo(
        source_url="https://example.invalid/b.mp3",
        duration_seconds=30.0,
        poster_url="",
        title="Translated",
        heading="Translated",
        media_kind="audio",
        tracks=(
            CaptionTrack(
                kind="subtitles",
                language="de",
                label="Deutsch",
                source_url="subtitles-de.vtt",
                readable=True,
                cues=(TranscriptCue(start=0.0, end=2.0, text="UEBERSETZTE WORTE"),),
            ),
        ),
    )
    chunks = video_bundle.media_chunks([video, audio], start_index=0)
    text = "\n".join(chunk["text"] for chunk in chunks)
    assert "SPOKEN WORDS HERE" in text
    assert "UEBERSETZTE WORTE" not in text
    kinds = [chunk["meta"]["kind"] for chunk in chunks]
    assert kinds == ["media_transcript", "untranscribed_media"]
    # The gap chunk is the audio's, and it is numbered by the element's place
    # among *all* the page's media — the same 2 as its `videos/media-02`.
    assert chunks[1]["meta"]["doc_items"] == ["#/media/2"]
    assert chunks[0]["meta"]["media"]["transcript_provenance"] == "authored"

    # And the note that goes into content.md says which of the two it is.
    transcribed_note = "\n".join(
        video_bundle._media_note(video, position=1, plain=True)
    )
    assert "SPOKEN WORDS HERE" in transcribed_note
    assert "not transcribed" not in transcribed_note.lower()
    assert "videos/media-01/transcript.txt" in transcribed_note
    translation_note = "\n".join(
        video_bundle._media_note(audio, position=2, plain=True)
    )
    assert "UEBERSETZTE WORTE" not in translation_note
    assert "not transcribed" in translation_note.lower()


def test_the_readme_never_sends_a_reader_to_a_file_a_caption_track_has_not() -> None:
    """A captioned `<video>` has no walkthrough.md, no steps.json, no appendix.

    Splitting the README's groups on `transcribed` put a media element in the
    walkthrough branch the moment path 1 made one transcribable, and that
    branch names three files it does not have and claims "0 steps, documented
    in full" (docs/09 P10-10).
    """
    record = MediaVideo(
        source_url="https://example.invalid/a.mp4",
        duration_seconds=60.0,
        poster_url="",
        title="Captioned",
        tracks=(
            CaptionTrack(
                kind="captions",
                language="en",
                label="English",
                source_url="captions.vtt",
                readable=True,
                cues=(TranscriptCue(start=0.0, end=2.0, text="Spoken words."),),
            ),
        ),
    ).manifest_record
    record["directory"] = "videos/media-01"
    section = _video_section([record])
    assert "videos/media-01/transcript.txt" in section
    for absent in (
        "walkthrough.md",
        "steps.json",
        "in the appendix at the end of this PDF",
        "0 steps",
    ):
        assert absent not in section, f"the README names {absent}, which is absent"
    # Denying the appendix is not the same as naming one, and is the point:
    # absence stated beats absence implied.
    assert "no appendix pages and no steps" in section


@pytest.mark.browser
def test_the_read_budget_bites_inside_the_page_and_says_that_it_did() -> None:
    """A budget must bound the work, and a cut transcript must not read whole.

    Two failures in one line before this. The cap was `[:max_cues]` on the
    Python side, so every cue had already been parsed, serialized, carried
    across CDP and deserialized before the first was dropped — a limit applied
    after the cost it exists to refuse (P7-9). And it dropped the remainder in
    silence: `cues` read 5000 whether the track had 5000 or 8000, so a partial
    transcript published as a complete one.

    Driven with a budget small enough to bite on the two-cue fixture, because a
    guard proven only at its real setting is a guard proven never to fire.
    """
    with serve(_static()) as origin:
        tracks = read(f"{origin}/captions_page.html", max_chars=1)
    video = next(t for source, t in tracks.items() if "sample-video" in source)
    track = video[0]
    assert track.truncated is True
    assert len(track.cues) == 1, "the budget stopped after the first cue"
    assert track.total_cues == 2, "and the track's real length is still known"
    assert track.record["truncated"] is True
    assert track.record["total_cues"] == 2
    # The fact travels with the bytes, for the same reason `role` does: a .vtt
    # gets opened on its own, and a file that ends early looks exactly like a
    # file that ended.
    vtt = track_vtt(track, title="Quarterly training video", source_url="x")
    assert "PARTIAL" in vtt and "1 of 2 cues" in vtt

    # And the same page read without a biting budget is not marked truncated —
    # otherwise this test would pass on a flag that is simply always true.
    with serve(_static()) as origin:
        whole = read(f"{origin}/captions_page.html")
    intact = next(t for source, t in whole.items() if "sample-video" in source)[0]
    assert intact.truncated is False
    assert len(intact.cues) == intact.total_cues == 2
    assert "PARTIAL" not in track_vtt(intact, title="x", source_url="x")


def test_a_truncated_transcript_says_so_where_a_reader_would_quote_it() -> None:
    """`content.md` is where the words are read, so that is where it must say."""
    cut = CaptionTrack(
        kind="captions",
        language="en",
        label="English",
        source_url="captions.vtt",
        readable=True,
        cues=(TranscriptCue(start=0.0, end=2.0, text="Only the beginning."),),
        total_cues=900,
        truncated=True,
    )
    video = MediaVideo(
        source_url="https://example.invalid/a.mp4",
        duration_seconds=3600.0,
        poster_url="",
        title="Long",
        tracks=(cut,),
    )
    note = "\n".join(video_bundle._media_note(video, position=1, plain=True))
    assert "PARTIAL" in note
    assert "1 of 900 cues" in note
    # Still a transcript — incomplete is not the same as absent, and calling it
    # absent would send a reader looking for words the bundle does have.
    assert cut.is_transcript is True
