"""Embedded-video capture: the bridge offline, then one capture end to end.

Two halves, deliberately.  Most of this file never opens a browser: the Guidde
bridge is a pure function from a recorded response to WebShot's dataclasses, so
the properties that matter — every step yields instruction text, timings are
monotonic, chunks carry full provenance, the VTT parses — are checked against a
fixture in milliseconds.

The last test runs a real capture, and it reaches a **local stand-in** that
behaves the way the live share page does: a client-rendered page that fetches
its own playbook.  No test may touch live Guidde (docs/06), and one that did
would be measuring Guidde's uptime rather than WebShot's behaviour.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import math
import re
import sys
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # so this module can be selected on its own
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.guidde_standin import (  # noqa: E402
    PIXEL,
    rewritten_payload,
    standin,
)

from conftest import serve  # noqa: E402
from webshot.acquire.router import normalize_source  # noqa: E402
from webshot.bundle import videos as video_bundle  # noqa: E402
from webshot.bundle.captions import CaptionTrack  # noqa: E402
from webshot.config import CaptureOptions  # noqa: E402
from webshot.extract import guidde  # noqa: E402
from webshot.extract.guidde import TranscriptCue  # noqa: E402
from webshot.pipeline import convert_url_to_pdf  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
PLAYBOOK_FIXTURE = FIXTURES / "guidde" / "quickguidde-sample-playbook.json"
LMS_PAGE = FIXTURES / "lms_page.html"
PLAYBOOK_ID = "ExamplePlaybook0000001"
PLAYBOOK_TITLE = "Touring Your Project Workspace"
SHARE_URL = f"https://app.guidde.com/share/playbooks/{PLAYBOOK_ID}"


def playbook() -> guidde.Walkthrough:
    return guidde.parse_playbook(
        PLAYBOOK_FIXTURE.read_bytes(), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )


# --------------------------------------------------------------------------- #
# Resolving a playbook id — the spike's question, held as a test
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SHARE_URL, PLAYBOOK_ID),
        (f"{SHARE_URL}?origin=lms", PLAYBOOK_ID),
        (
            f"https://embed.app.guidde.com/playbooks/{PLAYBOOK_ID}?autoplay=true",
            PLAYBOOK_ID,
        ),
        (f"https://embed.app.guidde.com/playbooks/{PLAYBOOK_ID}#t=3", PLAYBOOK_ID),
        (f"https://app.guidde.com/c/v1/quickguidde?id={PLAYBOOK_ID}", PLAYBOOK_ID),
        # A look-alike host must not resolve, however plausible the path.
        (f"https://guidde.com.example.invalid/playbooks/{PLAYBOOK_ID}", None),
        (f"https://example.invalid/share/playbooks/{PLAYBOOK_ID}", None),
        ("https://www.guidde.com/pricing", None),
        ("https://storage.app.guidde.com/v0/b/x/o/y.webp?alt=media", None),
        ("not a url at all", None),
    ],
)
def test_playbook_id_is_read_from_every_guidde_surface(
    url: str, expected: str | None
) -> None:
    assert guidde.playbook_id(url) == expected


@pytest.mark.parametrize(
    "markup",
    [
        f'<iframe src="https://embed.app.guidde.com/playbooks/{PLAYBOOK_ID}"></iframe>',
        f'<iframe data-src="https://embed.app.guidde.com/playbooks/{PLAYBOOK_ID}"></iframe>',
        f'<a href="{SHARE_URL}">Watch</a>',
        f'<div data-url="{SHARE_URL}"></div>',
        f'<meta property="og:video" content="{SHARE_URL}">',
        # A valid mixed-case hostname. `getAttribute` preserves the author's
        # casing, and the live DOM scan's cheap prefilter compared it verbatim
        # — so this walkthrough was dropped before `playbook_id`'s own
        # case-insensitive hostname check could see it, and was silently absent
        # from the bundle, the appendix and the tally (docs/09 P8-24).
        f'<iframe src="https://EMBED.APP.GUIDDE.COM/playbooks/{PLAYBOOK_ID}"></iframe>',
        f'<a href="https://App.Guidde.com/share/playbooks/{PLAYBOOK_ID}">Watch</a>',
    ],
    ids=[
        "iframe",
        "lazy-iframe",
        "anchor",
        "data-attribute",
        "meta",
        "uppercase-host",
        "mixed-case-host",
    ],
)
def test_an_embed_is_found_however_the_page_spells_it(markup: str) -> None:
    assert [embed.playbook_id for embed in guidde.embeds_in_html(markup)] == [
        PLAYBOOK_ID
    ]


def test_one_video_named_twice_is_one_video() -> None:
    markup = (
        f'<iframe data-src="https://embed.app.guidde.com/playbooks/{PLAYBOOK_ID}">'
        f'</iframe><a href="{SHARE_URL}">Open</a>'
    )
    assert len(guidde.embeds_in_html(markup)) == 1


def test_a_page_with_no_guidde_embed_yields_none() -> None:
    assert (
        guidde.embeds_in_html('<iframe src="https://example.invalid/v/1"></iframe>')
        == []
    )


# --------------------------------------------------------------------------- #
# Reading a playbook
# --------------------------------------------------------------------------- #


def test_the_recorded_response_parses_into_a_whole_playbook() -> None:
    book = playbook()
    assert book.id == PLAYBOOK_ID
    assert book.title == PLAYBOOK_TITLE
    assert book.organization == "Example Organization"
    assert len(book.steps) == 11
    # The share page's own call wraps the playbook beside its branding config;
    # a bridge that read the top level would find no steps at all.
    assert "playbook" in json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))


def test_every_step_yields_instruction_text() -> None:
    """Including the silent ones, which is the point of the fallback chain."""
    for step in playbook().steps:
        assert step.instruction.strip(), f"step {step.number} has no instruction"


def test_silent_steps_are_normal_and_keep_their_screenshot() -> None:
    """docs/09 P7-3: 3 of 11 steps are silent screen action, not missing data."""
    silent = [step for step in playbook().steps if step.silent]
    assert len(silent) == 3
    for step in silent:
        assert step.screenshot_url, f"silent step {step.number} lost its screenshot"


def test_the_sample_playbook_is_entirely_generated_narration() -> None:
    """If this ever fails, the reading is unverified territory — see P7-4."""
    assert playbook().unverified_narration == ()


def test_an_unfamiliar_narration_style_is_reported_not_assumed() -> None:
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    document["playbook"]["steps"][1]["audioNote"]["type"] = "humanRecording"
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )
    assert book.unverified_narration == ("humanRecording",)
    bundle = video_bundle.build_video_bundle(book, [])
    assert any("humanRecording" in warning for warning in bundle.warnings)


def test_transcript_timings_are_absolute_and_monotonic() -> None:
    book = playbook()
    cues = [cue for step in book.steps for cue in step.cues]
    assert cues, "the fixture has narration"
    for earlier, later in pairwise(cues):
        assert earlier.start <= later.start
        assert earlier.start <= earlier.end
    assert cues[0].start >= 0


def test_step_starts_are_the_running_sum_of_the_durations() -> None:
    book = playbook()
    running = 0.0
    for step in book.steps:
        assert step.start == pytest.approx(running)
        running += step.duration
    assert running == pytest.approx(book.duration)


def test_upstream_timings_past_the_duration_are_reported_not_corrected() -> None:
    """Guidde's own published .vtt overruns the same way — so it is recorded."""
    book = playbook()
    assert any("stated duration" in warning for warning in book.warnings)


def test_a_response_for_a_different_playbook_is_refused() -> None:
    """Not recorded beats recorded against the wrong video.

    A share page can hydrate more than one playbook, and a walkthrough filed
    under the wrong id is a wrong answer that looks entirely right.
    """
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    document["playbook"]["id"] = "someOtherPlaybookIdHere"
    with pytest.raises(guidde.GuiddeUnavailable, match="someOtherPlaybookIdHere"):
        guidde.parse_playbook(
            json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
        )


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "1e400"])
def test_a_non_finite_number_cannot_reach_an_artifact(literal: str) -> None:
    """`json.loads` accepts these, and every timestamp helper calls `int()`.

    A playbook carrying one used to parse cleanly and then crash the capture
    from inside the artifact writers, where nothing catches it.
    """
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    raw = json.dumps(document).replace('"duration": 81.4', f'"duration": {literal}')
    book = guidde.parse_playbook(raw, expected_id=PLAYBOOK_ID, source_url=SHARE_URL)
    assert book.duration == pytest.approx(81.4), "falls back to the summed steps"
    # And the whole artifact set builds rather than raising.
    bundle = video_bundle.build_video_bundle(book, [])
    assert bundle.walkthrough and bundle.transcript_vtt and bundle.chunks


def test_a_boolean_is_not_a_duration() -> None:
    """`bool` passes `isinstance(x, int)`, so `true` became a 1-second step."""
    assert guidde._number(True) == 0.0
    assert guidde._number(False) == 0.0


def test_a_playbook_cannot_ask_for_unbounded_work() -> None:
    """Step count comes from third-party JSON and drives a request each."""
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    one = document["playbook"]["steps"][1]
    document["playbook"]["steps"] = [dict(one) for _ in range(guidde.MAX_STEPS + 25)]
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )
    assert len(book.steps) == guidde.MAX_STEPS
    assert any("were skipped" in warning for warning in book.warnings)


def test_playbook_strings_are_bounded() -> None:
    """A title lands in the manifest, every chunk, and the PDF's README."""
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    document["playbook"]["title"] = "T" * 50_000
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )
    assert len(book.title) <= guidde.MAX_LABEL_CHARACTERS
    assert book.title.endswith(guidde.TRUNCATION_MARK)


# --------------------------------------------------------------------------- #
# Narration correction markup — the two directives resolve opposite ways
# --------------------------------------------------------------------------- #

CORRECTION_FIXTURE = FIXTURES / "guidde" / "narration-correction-markup.json"
CORRECTION_ID = "CorrectionMarkupFixture01"


def corrections() -> guidde.Walkthrough:
    return guidde.parse_playbook(
        CORRECTION_FIXTURE.read_bytes(),
        expected_id=CORRECTION_ID,
        source_url=f"https://app.guidde.com/share/playbooks/{CORRECTION_ID}",
    )


def test_a_pronunciation_hint_keeps_the_word_a_spelling_fix_keeps_the_fix() -> None:
    """`alt` and `spelling` publish OPPOSITE halves of the same markup.

    This is the assertion that fails on the tempting implementation. In a live
    corpus 93 of 95 occurrences were `alt`, so "keep the first token" is right
    93 times and publishes `examplitics` where the author wrote
    `{examplitics||||spelling=Examplytics}` — a misspelling of the product,
    in the place a reader notices. `alt` is a hint for the speech synthesizer
    (`{does||||alt=duzz}`), and the written half is the display text.
    """
    book = corrections()
    words = " ".join(
        [book.title]
        + [step.title for step in book.steps]
        + [step.instruction for step in book.steps]
        + [step.narration for step in book.steps]
        + [cue.text for step in book.steps for cue in step.cues]
    )
    # The correction is applied...
    assert "Examplytics" in words
    assert "examplitics" not in words
    # ...and the pronunciation hints are not, in either direction: neither the
    # spelling meant for a voice nor the markup that carried it.
    for spoken in ("At-tribute", "duzz", "funktional", "kuh-ler"):
        assert spoken not in words, spoken
    assert "attribute" in words and "does" in words


def test_an_unrecognised_correction_directive_keeps_the_word_and_says_so() -> None:
    """Not guessed, and not silent — the next variant must not ship raw.

    An unknown directive could mean either half, so the literal word stands
    and the manifest names the directive that was not understood
    (CONTRIBUTING rule 5).
    """
    book = corrections()
    assert any("pronounce" in warning for warning in book.warnings), book.warnings
    assert any("does not resolve" in warning for warning in book.warnings)
    assert "colour" in " ".join(step.instruction for step in book.steps)


def test_correction_markup_reaches_no_published_surface() -> None:
    """Resolved once at the bridge, so every surface gets the resolved text.

    `content.md`, `content.txt` and `chunks.jsonl` all render from these
    dataclasses, which is why the fix belongs here and not in three writers
    (CONTRIBUTING rule 1).
    """
    book = corrections()
    surfaces = [
        video_bundle.walkthrough_markdown(book, []),
        video_bundle.walkthrough_plain_text(book, []),
        video_bundle.transcript_text(book),
        video_bundle.transcript_vtt(book),
        json.dumps(video_bundle.steps_payload(book, [])),
        json.dumps(video_bundle.video_chunks(book, [], start_index=0)),
        json.dumps(video_bundle.build_video_bundle(book, []).manifest_record),
    ]
    for rendered in surfaces:
        assert "||||" not in rendered, rendered[:200]
        assert "examplitics" not in rendered
        assert "Examplytics" in rendered


def test_every_published_field_is_resolved_before_the_warning_is_composed() -> None:
    """A guard covers what it covers — including the order it runs in.

    `organization` and `last_updated_by` are printed in the walkthrough's
    provenance line, so they are deliverable text too. Resolving them in the
    constructor, after the warning list is built, would publish the right word
    and report nothing — the check written to catch an unknown directive would
    silently drop it.
    """
    document = json.loads(CORRECTION_FIXTURE.read_text("utf-8"))
    document["playbook"]["orgName"] = "{examplitics||||spelling=Examplytics}"
    document["playbook"]["lastUpdatedBy"] = "A {name||||nickname=N}"
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=CORRECTION_ID, source_url=SHARE_URL
    )
    assert book.organization == "Examplytics"
    assert book.last_updated_by == "A name"
    reported = next(w for w in book.warnings if "does not resolve" in w)
    assert "nickname" in reported, reported
    assert "Examplytics" in video_bundle.walkthrough_plain_text(book, [])


def test_a_directive_is_resolved_before_the_length_bound_not_after() -> None:
    """A truncation must not be able to cut a directive in half.

    Bounding first would leave `{examplitics||||spel` in a title — markup in
    a deliverable, arriving through the guard written to keep markup out.
    """
    document = json.loads(CORRECTION_FIXTURE.read_text("utf-8"))
    filler = "T" * (guidde.MAX_LABEL_CHARACTERS - 10)
    document["playbook"]["title"] = f"{filler}{{examplitics||||spelling=Examplytics}}"
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=CORRECTION_ID, source_url=SHARE_URL
    )
    assert "||||" not in book.title
    assert len(book.title) <= guidde.MAX_LABEL_CHARACTERS


def test_a_response_with_no_steps_is_unavailable_rather_than_empty() -> None:
    with pytest.raises(guidde.GuiddeUnavailable):
        guidde.parse_playbook(
            '{"playbook": {"id": "x", "steps": []}}',
            expected_id="x",
            source_url=SHARE_URL,
        )


def test_a_non_json_response_is_unavailable() -> None:
    with pytest.raises(guidde.GuiddeUnavailable):
        guidde.parse_playbook(
            b"<html>gateway timeout</html>", expected_id="x", source_url=SHARE_URL
        )


# --------------------------------------------------------------------------- #
# The artifacts
# --------------------------------------------------------------------------- #

VTT_CUE = re.compile(
    r"^(\d\d):(\d\d):(\d\d\.\d\d\d) --> (\d\d):(\d\d):(\d\d\.\d\d\d)$", re.MULTILINE
)


def _vtt_seconds(hour: str, minute: str, second: str) -> float:
    return int(hour) * 3600 + int(minute) * 60 + float(second)


def test_the_vtt_parses_and_its_cues_match_the_playbook() -> None:
    book = playbook()
    text = video_bundle.transcript_vtt(book)
    assert text.startswith("WEBVTT")
    found = VTT_CUE.findall(text)
    expected = [cue for step in book.steps for cue in step.cues]
    assert len(found) == len(expected)
    for cue, raw in zip(expected, found, strict=True):
        assert _vtt_seconds(*raw[:3]) == pytest.approx(cue.start, abs=0.001)
        assert _vtt_seconds(*raw[3:]) == pytest.approx(cue.end, abs=0.001)
    # Every cue's end is at or after its start, which is what makes it a VTT a
    # player will accept rather than merely well-formed text.
    for raw in found:
        assert _vtt_seconds(*raw[3:]) >= _vtt_seconds(*raw[:3])


def test_the_reconstructed_timings_match_guiddes_own_published_vtt() -> None:
    """The evidence that per-step cues are relative and the offsets are right.

    Guidde publishes a document-level `.vtt` of its own; WebShot never fetches
    it, and reproducing it cue for cue from the per-step subtitles is what
    proves one intercepted response is enough (docs/09 P7-3).

    Only the timings are compared, and only the timings are Guidde's: every
    cue line of the committed reference is as Guidde published it, while its
    cue text and playbook id were replaced along with the playbook fixture's
    so the corpus carries no third party's narration.
    """
    reference = (FIXTURES / "guidde" / "subtitles-reference.vtt").read_text("utf-8")
    theirs = VTT_CUE.findall(reference)
    ours = VTT_CUE.findall(video_bundle.transcript_vtt(playbook()))
    assert len(ours) == len(theirs) > 0
    for mine, other in zip(ours, theirs, strict=True):
        assert _vtt_seconds(*mine[:3]) == pytest.approx(
            _vtt_seconds(*other[:3]), abs=0.01
        )
        assert _vtt_seconds(*mine[3:]) == pytest.approx(
            _vtt_seconds(*other[3:]), abs=0.01
        )


def test_every_video_chunk_carries_full_provenance() -> None:
    book = playbook()
    asset = video_bundle.VideoAsset(
        id="video-x-step-002",
        file="assets/video-x-step-002.png",
        kind="step-screenshot",
        playbook_id=PLAYBOOK_ID,
        source_url="https://storage.app.guidde.com/x.webp",
        bytes=10,
        sha256="0" * 64,
        step=2,
    )
    chunks = video_bundle.video_chunks(book, [asset], start_index=7)
    assert len(chunks) == len(book.steps)
    assert chunks[0]["id"] == "ch_000008", "ids continue the page's own sequence"
    for chunk, step in zip(chunks, book.steps, strict=True):
        provenance = chunk["meta"]["video"]
        assert provenance["playbook_id"] == PLAYBOOK_ID
        assert provenance["title"] == book.title
        assert provenance["step"] == step.number
        assert provenance["start_seconds"] == pytest.approx(step.start, abs=0.001)
        assert provenance["end_seconds"] == pytest.approx(step.end, abs=0.001)
        assert provenance["timestamp"]
        assert chunk["meta"]["kind"] == "video_step"
        assert chunk["text"].strip()
    assert chunks[1]["meta"]["video"]["screenshot"] == asset.file
    assert chunks[0]["meta"]["video"]["screenshot"] is None


def test_a_chunk_can_be_cited_as_video_step_and_moment() -> None:
    """The acceptance sentence, as an assertion."""
    third = video_bundle.video_chunks(playbook(), [], start_index=0)[2]
    provenance = third["meta"]["video"]
    assert provenance["step"] == 3
    assert provenance["timestamp"] == "0:19"
    assert provenance["playbook_id"] == PLAYBOOK_ID


@pytest.mark.parametrize(
    "instruction",
    [
        "Click [Save] then [Close]",
        "Step one\n## Injected heading\nrest",
        "See ](https://evil.example/x) here",
        "- item\n> quote\n1. numbered",
    ],
)
def test_playbook_prose_cannot_restructure_content_md(instruction: str) -> None:
    """The instruction text is third-party, and content.md is what agents read.

    A bracket ends alt text early; a leading `#` injects a heading, which is
    also an anchor the section placement would later mistake for the page's own.
    """
    book = playbook()
    hostile = replace(book.steps[1], instruction=instruction, cues=())
    book = replace(book, steps=(book.steps[0], hostile, *book.steps[2:]))
    asset = video_bundle.VideoAsset(
        id="video-x-step-002",
        file="assets/x.png",
        kind="step-screenshot",
        playbook_id=PLAYBOOK_ID,
        source_url="https://storage.app.guidde.com/x.webp",
        bytes=1,
        sha256="0" * 64,
        step=2,
    )
    markdown = video_bundle.walkthrough_markdown(book, [asset])
    # The alt text cannot end early and start a reference of its own.
    for alt in re.findall(r"!\[([^\]]*)\]\(", markdown):
        assert "[" not in alt and "]" not in alt
    # Every image reference is one WebShot generated, not one the prose named.
    for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
        assert ref.startswith(("../../assets/", "assets/")), ref
    # And no line of the prose opens a markdown block of its own.
    body = markdown.split("### Step 2", 1)[1].split("### Step 3", 1)[0]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("###", "![", "Narration:", "- `", "*")):
            continue
        assert not stripped.startswith(("#", ">", "- ", "1.")), line


def test_a_walkthrough_is_not_spliced_into_a_code_fence() -> None:
    """A `#` inside a fenced block is a comment, not a section boundary."""
    bundle = video_bundle.build_video_bundle(playbook(), [], heading="Install")
    page = (
        "# Module\n\n## Install\n\nRun this:\n\n"
        "```bash\n# Set the token first\nexport TOKEN=x\n```\n\n"
        "## Next\n\nDone.\n"
    )
    inlined = video_bundle.inline_into_content(page, [bundle])
    fence_open = inlined.index("```bash")
    fence_close = inlined.index("```", fence_open + 3)
    walkthrough = inlined.index(PLAYBOOK_TITLE)
    assert not fence_open < walkthrough < fence_close, (
        "the walkthrough was spliced inside the code block"
    )
    assert "export TOKEN=x" in inlined


def test_a_vtt_second_never_rounds_to_sixty() -> None:
    """`%06.3f` on 59.9996 renders 60.000, which no player accepts."""
    for value in (59.9996, 59.999999999999996, 3599.9999):
        stamp = video_bundle.vtt_timestamp(value)
        assert not stamp.endswith("60.000"), stamp
        seconds = float(stamp.rsplit(":", 1)[1])
        assert seconds < 60


def test_the_walkthrough_marks_silent_steps_rather_than_omitting_them() -> None:
    markdown = video_bundle.walkthrough_markdown(playbook(), [])
    assert markdown.count("### Step ") == 11
    assert video_bundle.SILENT_STEP_NOTE in markdown
    # It says what it is, and does not call itself a transcript of a recording.
    assert "not a transcript of a recording" in markdown


def test_a_walkthrough_is_inlined_where_its_video_was() -> None:
    book = playbook()
    bundle = video_bundle.build_video_bundle(
        book, [], heading="Navigating your project"
    )
    page = (
        "# Module\n\nLede.\n\n"
        "## Navigating your project\n\nThe video below walks through it.\n\n"
        "## Checklist\n\n- Do the thing.\n"
    )
    inlined = video_bundle.inline_into_content(page, [bundle])
    assert inlined.index(book.title) < inlined.index("## Checklist"), (
        "the walkthrough belongs in its own section, before what came next"
    )
    assert inlined.index("The video below") < inlined.index(book.title)


def test_a_walkthrough_with_no_matching_heading_is_appended_not_lost() -> None:
    bundle = video_bundle.build_video_bundle(playbook(), [], heading="Nowhere")
    inlined = video_bundle.inline_into_content("# Module\n\nLede.\n", [bundle])
    assert PLAYBOOK_TITLE in inlined


def test_a_playbook_id_cannot_choose_where_files_land() -> None:
    assert "/" not in video_bundle._slug("../../etc/passwd")
    assert ".." not in video_bundle._slug("..")


# --------------------------------------------------------------------------- #
# One capture, end to end, against a local stand-in
# --------------------------------------------------------------------------- #


def _capture(
    tmp_path: Path, *, videos: bool = True
) -> tuple[Path, Path, dict[str, Any]]:
    """Capture the fixture LMS page, reading walkthroughs from the stand-in."""
    holder: dict[str, bytes] = {"payload": b"{}"}
    with serve(standin(holder)) as origin:
        holder["payload"] = rewritten_payload(origin)
        pdf = tmp_path / "lms.pdf"
        asyncio.run(
            convert_url_to_pdf(
                CaptureOptions(
                    source=normalize_source(str(LMS_PAGE)),
                    output=pdf,
                    videos=videos,
                    guidde_origin=origin,
                    # Nothing on this page is text-in-a-raster, so recognition
                    # has nothing to find and its absence must not make the
                    # test depend on whether Tesseract is installed.
                    ocr=False,
                )
            )
        )
    bundle = pdf.with_suffix(".ai")
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    return pdf, bundle, manifest


@pytest.mark.browser
def test_a_page_with_an_embedded_walkthrough_captures_it_whole(tmp_path: Path) -> None:
    pdf, bundle, manifest = _capture(tmp_path)

    # The manifest lists the video, its id, its steps, and its duration.
    records = manifest["videos"]
    documented = [r for r in records if r["provider"] == "guidde"]
    assert len(documented) == 1
    assert documented[0]["playbook_id"] == PLAYBOOK_ID
    assert documented[0]["steps"] == 11
    assert documented[0]["duration_seconds"] == pytest.approx(81.4)
    assert manifest["video_tally"]["documented_walkthroughs"] == 1

    # The bundle carries the walkthrough in all four shapes.
    directory = bundle / "videos" / PLAYBOOK_ID
    for name in ("walkthrough.md", "steps.json", "transcript.txt", "transcript.vtt"):
        assert (directory / name).is_file(), name
    steps = json.loads((directory / "steps.json").read_text("utf-8"))
    assert len(steps["steps"]) == 11
    assert all(step["instruction"] for step in steps["steps"])

    # Step screenshots landed in the bundle's assets with proper records.
    assets = json.loads((bundle / "assets.json").read_text("utf-8"))
    shots = [a for a in assets["video_assets"] if a["kind"] == "step-screenshot"]
    assert len(shots) == 11
    for shot in shots:
        assert (bundle / shot["file"]).is_file()
        assert shot["sha256"] and shot["bytes"] > 0
        assert shot["playbook_id"] == PLAYBOOK_ID
    # Each step keeps its OWN screenshot. The stand-in serves distinct bytes
    # per URL precisely so that an implementation pointing every step at the
    # first screenshot fails here instead of passing unnoticed.
    assert len({shot["file"] for shot in shots}) == 11
    assert len({shot["sha256"] for shot in shots}) == 11
    assert len({shot["id"] for shot in shots}) == 11
    assert [shot["step"] for shot in shots] == list(range(1, 12))
    # And nothing claims to be shared when it is one playbook's own file.
    assert all(shot["shared_with"] is None for shot in shots)

    # content.md inlines the walkthrough where the video was...
    content = (bundle / "content.md").read_text("utf-8")
    assert content.index(PLAYBOOK_TITLE) < content.index("## Checklist")
    # ...and every image it references actually resolves inside the bundle.
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)
    assert refs, "the inlined walkthrough carries its screenshots"
    for ref in refs:
        assert (bundle / ref).is_file(), f"{ref} does not resolve from content.md"
    # ...and content.txt carries the same walkthrough as plain prose. An agent
    # searching this file and finding no mention of the procedure would
    # reasonably conclude the page had none — `_note_media` already makes that
    # argument for the media note beside it.
    plain = (bundle / "content.txt").read_text("utf-8")
    assert "This is the walkthrough as text" in plain
    assert "Step 11 — Thank you" in plain
    assert "![" not in plain and "\n## " not in plain
    # The standalone copy resolves from its own directory, which is a
    # different relative path for the same file.
    walkthrough = (bundle / "videos" / PLAYBOOK_ID / "walkthrough.md").read_text(
        "utf-8"
    )
    for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", walkthrough):
        assert (bundle / "videos" / PLAYBOOK_ID / ref).resolve().is_file(), ref

    # Chunks carry video, step and timestamp provenance.
    lines = [
        json.loads(line)
        for line in (bundle / "chunks.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    video_chunks = [line for line in lines if line["meta"].get("video")]
    assert len(video_chunks) == 11
    # And the video the bundle could NOT document is a chunk too, so retrieval
    # surfaces the gap rather than returning nothing and reading as complete.
    gaps = [line for line in lines if line["meta"].get("media")]
    assert len(gaps) == 1
    assert gaps[0]["meta"]["kind"] == "untranscribed_media"
    assert gaps[0]["meta"]["media"]["transcribed"] is False
    assert "sandbox-overview.mp4" in gaps[0]["meta"]["media"]["source_url"]
    assert "NOT transcribed" in gaps[0]["text"]
    assert {chunk["id"] for chunk in lines}.__len__() == len(lines), "ids are unique"
    for chunk in video_chunks:
        provenance = chunk["meta"]["video"]
        assert provenance["playbook_id"] == PLAYBOOK_ID
        assert provenance["step"] >= 1
        assert provenance["timestamp"]
        assert provenance["screenshot"]
    # Each step's chunk names its own screenshot, not a shared one.
    assert len({chunk["meta"]["video"]["screenshot"] for chunk in video_chunks}) == 11

    # The PDF gained one bookmark per video and one per step.
    reader = PdfReader(str(pdf))
    titles = _outline_titles(reader.outline)
    assert "Embedded video walkthroughs" in titles
    assert PLAYBOOK_TITLE in titles
    assert sum(1 for title in titles if title.startswith("Step ")) == 11
    appendix = manifest["pdf"]["video_appendix"]
    assert appendix["videos"] == 1 and appendix["steps"] == 11
    assert appendix["tagged"] is False, "docs/09 S8: a merged appendix is not tagged"
    assert len(reader.pages) == appendix["starts_on_page"] - 1 + appendix["pages"]

    # The embedded README is what makes the walkthrough discoverable.
    readme = reader.attachments["README.txt"][0].decode("utf-8")
    assert f"videos/{PLAYBOOK_ID}/walkthrough.md" in readme
    assert "NOT TRANSCRIBED" in readme
    assert f"videos/{PLAYBOOK_ID}/walkthrough.md" in reader.attachments


def _hit_recording_handler(
    hits: list[str],
) -> type[http.server.BaseHTTPRequestHandler]:
    """Serves a pixel and records every path it was asked for."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PIXEL)))
            self.end_headers()
            self.wfile.write(PIXEL)

    return Handler


@pytest.mark.browser
@pytest.mark.parametrize("guard", [True, False], ids=["gate-on", "gate-off"])
def test_an_asset_download_obeys_the_private_address_guard(
    tmp_path: Path, guard: bool
) -> None:
    """`--block-private-requests` has to reach the downloads, and did not.

    The flag is enforced with `context.route`, and Playwright applies route
    handlers to page-initiated traffic **only** — an `APIRequestContext`
    request goes straight past them. Measured, not inferred: with an aborting
    gate installed on a context, `page.goto` is blocked while
    `context.request.get` returns 200 with the body.

    That mattered here more than it would elsewhere, because these URLs are not
    the caller's — they come out of the intercepted Guidde response — and the
    bytes are written into the bundle and embedded in the PDF the user shares.

    Both directions are asserted in one test on purpose: the guard being
    *conditional* is the design decision, and a test that only proved the
    refusal would not notice if the default had started refusing too. An LMS on
    an intranet serving its media from a private address is a real deployment.
    """
    hits: list[str] = []
    with serve(_hit_recording_handler(hits)) as origin:
        url = f"{origin}/private-asset.png"
        path, _body, _cost, problem = asyncio.run(
            _download_once(url, tmp_path, block_private=guard)
        )
    if guard:
        assert path is None
        assert "names an internal address" in problem
        assert hits == [], "the request must not be made, not merely discarded"
        assert not list(tmp_path.iterdir()), "nothing may reach the bundle"
    else:
        assert problem == ""
        assert path is not None and path.is_file()
        assert hits == ["/private-asset.png"]


async def _download_once(
    url: str,
    directory: Path,
    *,
    block_private: bool,
    origin: str | None = None,
) -> tuple[Path | None, bytes, int, str]:
    """One `_download` through a real browser context, as the capture uses it."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            return await video_bundle._download(
                context.request,
                url,
                directory,
                asset_id="video-probe-step-001",
                block_private=block_private,
                origin=origin if origin is not None else url,
            )
        finally:
            await browser.close()


@pytest.mark.parametrize(
    ("url", "origin", "allowed"),
    [
        # Two different public addresses must not read as one service just
        # because their last two octets agree.
        ("https://1.2.3.4/a.png", "https://9.8.3.4", False),
        ("https://1.2.3.4/a.png", "https://1.2.3.4", True),
        ("http://127.0.0.1:8/a.png", "http://127.0.0.1:9", True),
    ],
)
def test_an_address_host_must_match_exactly(
    url: str, origin: str, allowed: bool
) -> None:
    """An IP literal has no registrable domain and its labels are no hierarchy."""
    assert video_bundle.permitted_asset_host(url, origin) is allowed


@pytest.mark.parametrize(
    ("url", "origin", "allowed"),
    [
        # What Guidde actually does: the playbook on one host, its stills on
        # another under the same registrable domain.
        ("https://storage.app.guidde.com/o/x.webp", "https://app.guidde.com", True),
        ("https://app.guidde.com/o/x.webp", "https://app.guidde.com", True),
        ("https://evil.example/steal.png", "https://app.guidde.com", False),
        # The look-alike a suffix check must not accept.
        ("https://guidde.com.evil.example/x.png", "https://app.guidde.com", False),
        # The shape that motivated the rule: a host the signed-in user is
        # authenticated to, named by third-party JSON.
        ("https://lms.example.com/api/me", "https://app.guidde.com", False),
        ("", "https://app.guidde.com", False),
    ],
)
def test_only_the_walkthroughs_own_service_may_name_assets(
    url: str, origin: str, allowed: bool
) -> None:
    assert video_bundle.permitted_asset_host(url, origin) is allowed


@pytest.mark.browser
def test_a_playbook_may_only_name_assets_on_its_own_service(tmp_path: Path) -> None:
    """A playbook naming a foreign host is refused before the request is made.

    The URLs come out of third-party JSON and the fetch carries the capture
    context's cookies, so an unrestricted download is a credentialed read of
    any host the signed-in user is authenticated to, written into the document
    they share.
    """
    hits: list[str] = []
    with serve(_hit_recording_handler(hits)) as home:
        # `localhost` and `127.0.0.1` are different hostnames reaching the same
        # socket, so if the rule were not applied the server would answer and
        # `hits` would record it.
        elsewhere = home.replace("127.0.0.1", "localhost")
        path, _body, cost, problem = asyncio.run(
            _download_once(
                f"{elsewhere}/other-service.png",
                tmp_path,
                block_private=False,
                origin=home,
            )
        )
    assert path is None and cost == 0
    assert "is not on the service this walkthrough was read from" in problem
    assert hits == [], "the request must not be made at all"
    assert not list(tmp_path.iterdir())


def test_a_refused_asset_still_leaves_a_walkthrough() -> None:
    """A refused download is a warning, never a lost walkthrough.

    The page is the deliverable and the video is enrichment (CONTRIBUTING rule
    5), so every step still has to carry its instruction, its timing and its
    chunk — only the screenshot is gone.
    """
    bundle = video_bundle.build_video_bundle(playbook(), [])
    assert len(bundle.chunks) == 11
    assert all(chunk["meta"]["video"]["screenshot"] is None for chunk in bundle.chunks)
    assert all(chunk["meta"]["video"]["timestamp"] for chunk in bundle.chunks)
    assert all(chunk["text"].strip() for chunk in bundle.chunks)
    assert "### Step 1" in bundle.walkthrough
    assert "![" not in bundle.walkthrough, "no image reference without an image"


def test_a_playbook_cannot_write_infinity_into_an_artifact() -> None:
    """Finite inputs can still accumulate to one, and JSON has no spelling for it.

    Three steps of 1e308 sum to inf, which Python's json writes as a bare
    `Infinity` its own parser accepts and no other language's does.
    """
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    huge = dict(document["playbook"]["steps"][1], duration=1e308)
    document["playbook"]["steps"] = [dict(huge) for _ in range(3)]
    document["playbook"].pop("duration", None)
    book = guidde.parse_playbook(
        json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )
    assert all(math.isfinite(step.start) for step in book.steps)
    assert math.isfinite(book.duration)
    # And the writer refuses to emit one even if something slipped through.
    from webshot.bundle.publish import dump_json

    with pytest.raises(ValueError):
        dump_json({"duration_seconds": float("inf")})


def test_a_step_title_cannot_inject_a_heading() -> None:
    """Titles reach a line WebShot composes, so their newlines matter too."""
    book = playbook()
    hostile = replace(book.steps[1], title="Real\n\n## Injected heading")
    book = replace(book, steps=(book.steps[0], hostile, *book.steps[2:]))
    markdown = video_bundle.walkthrough_markdown(book, [])
    assert "\n## Injected heading" not in markdown
    assert "Injected heading" in markdown, "the words are kept, the structure is not"


def test_a_numbered_instruction_keeps_its_text_readable() -> None:
    """`\\1` is not a CommonMark escape — escaping there ships a stray backslash."""
    escaped = video_bundle._as_paragraph("1. Open Projects")
    assert escaped == "1\\. Open Projects"
    assert not escaped.startswith("\\1")


def test_a_page_may_keep_its_own_back_link_shaped_anchor() -> None:
    """A marker-scheme link on the *captured* page is the page's, not ours.

    The appendix rewrite skips those pages, and the assertion that no marker
    survived has to skip them too — bounding one and not the other turned a
    silent hijack of a reader's link into a hard failure of a capture whose PDF
    was already built.
    """
    from pypdf import PdfWriter

    from webshot.render.appendix import BACK_LINK_SCHEME, assert_no_marker_links

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    writer.add_annotation(
        page_number=0,
        annotation=_uri_annotation(f"{BACK_LINK_SCHEME}1"),
    )
    staged = tmp_pdf(writer)
    reader = PdfReader(str(staged))
    # Page 1 is the capture's own: its link is left exactly as the page had it.
    assert_no_marker_links(reader, first_appendix_page=1)
    # Unbounded, the same file is refused — which is the old behaviour, and
    # correct only for pages WebShot authored.
    with pytest.raises(Exception, match="unresolved appendix back-link"):
        assert_no_marker_links(reader)


def _uri_annotation(uri: str) -> Any:
    from pypdf.annotations import Link

    return Link(rect=(0, 0, 10, 10), url=uri)


def tmp_pdf(writer: Any) -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp()) / "probe.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_a_transcript_chunk_crosses_the_mcp_boundary_carrying_its_provenance() -> None:
    """The third provenance kind, and the one that makes a claim about words.

    `ChunkMediaGap` modelled only the absence, so a `media_transcript` chunk
    could not cross this boundary at all — it raised on five extra fields. A
    client that retrieves these words has to be able to see, without another
    call, that a publisher authored them rather than a recognizer guessing at
    them (docs/09 P10-10).
    """
    from webshot.mcp_server.service import _chunk_record

    lines = [
        json.loads(line)
        for line in (
            REPO_ROOT / "tests" / "golden" / "captions-read" / "bundle" / "chunks.jsonl"
        )
        .read_text("utf-8")
        .splitlines()
        if line.strip()
    ]
    record = next(r for r in lines if r["meta"]["kind"] == "media_transcript")
    crossed = _chunk_record(record, record["text"], format_3=True)
    assert crossed.media is not None
    assert crossed.media.transcribed is True
    assert crossed.media.transcript_provenance == "authored"
    assert crossed.media.track_kind == "captions"
    assert crossed.media.language == "en"
    assert crossed.media.end_seconds and crossed.media.end_seconds > 0
    # And the gap chunk on the same page still crosses saying the opposite.
    gap = next(r for r in lines if r["meta"]["kind"] == "untranscribed_media")
    other = _chunk_record(gap, gap["text"], format_3=True)
    assert other.media is not None
    assert other.media.transcribed is False
    assert other.media.transcript_provenance is None


def test_the_gap_record_crosses_the_mcp_boundary_like_the_video_one() -> None:
    """Both provenance kinds, or an agent can retrieve a record it cannot read."""
    from webshot.mcp_server.service import _chunk_record

    lines = [
        json.loads(line)
        for line in (
            REPO_ROOT
            / "tests"
            / "golden"
            / "lms-module-videos"
            / "bundle"
            / "chunks.jsonl"
        )
        .read_text("utf-8")
        .splitlines()
        if line.strip()
    ]
    video = next(r for r in lines if r["meta"].get("video"))
    crossed = _chunk_record(video, video["text"], format_3=True)
    assert crossed.video is not None
    assert crossed.video.playbook_id == PLAYBOOK_ID
    assert crossed.video.timestamp and crossed.video.step >= 1
    gap = next(r for r in lines if r["meta"].get("media"))
    crossed = _chunk_record(gap, gap["text"], format_3=True)
    assert crossed.kind == "untranscribed_media"
    assert crossed.media is not None
    assert crossed.media.transcribed is False
    assert "sandbox-overview.mp4" in crossed.media.source_url


def test_video_step_screenshots_are_listable_over_mcp(tmp_path: Path) -> None:
    """The chunks name these files; an agent must be able to fetch them."""
    from webshot.mcp_server.service import _asset_records

    bundle = REPO_ROOT / "tests" / "golden" / "lms-module-videos" / "bundle"
    records = _asset_records(bundle, roots=(REPO_ROOT,))
    identifiers = {record.id for record in records}
    assert any(name.startswith("video-") for name in identifiers), (
        "the walkthrough's step screenshots are invisible to list_assets"
    )
    assert len([name for name in identifiers if name.startswith("video-")]) == 11


@pytest.mark.browser
def test_the_appendix_leaves_nothing_behind_in_the_output_directory(
    tmp_path: Path,
) -> None:
    """The appendix PDF is as big as every screenshot in it — megabytes.

    It used to be staged beside the output and never removed, so every capture
    of a page with a video littered the user's directory with a copy of the
    appendix. Nothing intermediate may survive the capture.
    """
    pdf, bundle, _manifest = _capture(tmp_path)
    left = {
        entry.name
        for entry in tmp_path.iterdir()
        if not entry.name.startswith(".")  # the output locks are the pipeline's
    }
    assert left == {pdf.name, bundle.name}


@pytest.mark.browser
def test_every_step_bookmark_lands_on_the_page_its_step_is_on(tmp_path: Path) -> None:
    """A bookmark that lands a page away from its step makes an outline untrusted.

    The appendix authors each step heading as a back-link, so the merge reads
    every step's page off its own annotation instead of estimating it — this is
    what proves the reading, rather than the estimate, is what shipped.
    """
    pdf, _bundle, _manifest = _capture(tmp_path)
    reader = PdfReader(str(pdf))
    page_text = [(page.extract_text() or "") for page in reader.pages]
    checked = 0
    for item in _flattened(reader.outline):
        title = str(getattr(item, "title", ""))
        if not title.startswith("Step "):
            continue
        number = title.split()[1]
        page = reader.get_destination_page_number(item)
        assert f"Step {number}" in page_text[page], (
            f"{title!r} points at page {page + 1}, which does not contain it"
        )
        checked += 1
    assert checked == 11


@pytest.mark.browser
def test_a_video_that_cannot_be_documented_is_named_not_omitted(
    tmp_path: Path,
) -> None:
    _pdf, bundle, manifest = _capture(tmp_path)
    media = [r for r in manifest["videos"] if r["provider"] == "media-element"]
    assert len(media) == 1
    assert media[0]["transcribed"] is False
    assert "sandbox-overview.mp4" in media[0]["source_url"]
    assert manifest["video_tally"]["untranscribed_media"] == 1
    content = (bundle / "content.md").read_text("utf-8")
    assert "not transcribed" in content.lower()
    assert "sandbox-overview.mp4" in content
    # Not only content.md: an agent searching the plain text or retrieving over
    # the chunks would otherwise find no mention and conclude there was none.
    text = (bundle / "content.txt").read_text("utf-8")
    assert "NOT transcribed" in text and "sandbox-overview.mp4" in text
    chunk_kinds = [
        json.loads(line)["meta"]["kind"]
        for line in (bundle / "chunks.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert "untranscribed_media" in chunk_kinds


def test_the_three_undocumented_cases_are_described_differently(tmp_path: Path) -> None:
    """Three states, three notes — and none of them says what another says.

    A video with no track has nothing behind it. One whose track could not be
    read has a transcript that exists and is absent for a stated reason. One
    with a subtitles track has a translation, which is not a record of what
    was said. Collapsing any pair would send a reader looking for the wrong
    thing, or let a translation pass as the source.
    """

    def media(title: str, tracks: tuple[CaptionTrack, ...]) -> dict[str, object]:
        return video_bundle.MediaVideo(
            source_url=f"https://example.invalid/{title}.mp4",
            duration_seconds=60.0,
            poster_url="",
            title=title,
            tracks=tracks,
        ).manifest_record

    none = media("none", ())
    unreadable = media(
        "unreadable",
        (
            CaptionTrack(
                kind="captions",
                language="en",
                label="English",
                source_url="captions.vtt",
                readable=False,
            ),
        ),
    )
    translated = media(
        "translated",
        (
            CaptionTrack(
                kind="subtitles",
                language="de",
                label="Deutsch",
                source_url="subtitles-de.vtt",
                readable=True,
                cues=(TranscriptCue(start=0.0, end=1.0, text="Guten Tag"),),
            ),
        ),
    )
    notes = [none["note"], unreadable["note"], translated["note"]]
    assert len(set(notes)) == 3
    assert "no caption track" in none["note"]
    assert "could not read them" in unreadable["note"]
    assert "translation rather than a transcription" in translated["note"]
    # All three are honest about the same thing: nothing of what is *said*
    # reached the bundle, and none claims a provenance it does not have.
    for record in (none, unreadable, translated):
        assert record["transcribed"] is False
        assert record["transcript_provenance"] is None


def test_spoken_audio_counts_as_media_the_bundle_did_not_capture() -> None:
    """`<audio>` is entirely spoken content, so omitting it made the tally wrong.

    Not a missing feature but a false number: `video_tally.total` exists to be
    summed across a journey, and a page with a video and an audio briefing was
    reporting one. The `embedded-media` golden holds the corrected count.
    """
    golden = json.loads(
        (
            REPO_ROOT
            / "tests"
            / "golden"
            / "embedded-media"
            / "bundle"
            / "manifest.json"
        ).read_text("utf-8")
    )
    kinds = sorted(record["media_kind"] for record in golden["videos"])
    assert kinds == ["audio", "video"]
    assert golden["video_tally"]["total"] == 2
    # Both tracks are declared and neither can be read over `file://`, so
    # neither media element is transcribed here. `captions-read` records the
    # same page's other outcome; see docs/09 P10-8 for why they are two files.
    assert golden["video_tally"]["transcribed_media"] == 0
    assert golden["video_tally"]["untranscribed_media"] == 2


@pytest.mark.parametrize(
    "case", ["embedded-media", "captions-read", "lms-module-videos"]
)
def test_the_recorded_video_tally_accounts_for_every_video(case: str) -> None:
    """The parts are exhaustive, asserted as a derivation rather than pinned.

    `untranscribed_media` was `len(media)` and stayed that way after a readable
    caption track began producing a transcript, so a manifest said a video was
    transcribed and counted it as not four lines down. A recorded number would
    have frozen that; this fails for any case whose parts stop reaching its
    total, including cases added later (docs/09 P7-8).
    """
    manifest = json.loads(
        (REPO_ROOT / "tests" / "golden" / case / "bundle" / "manifest.json").read_text(
            "utf-8"
        )
    )
    tally = manifest["video_tally"]
    parts = ("documented_walkthroughs", "transcribed_media", "untranscribed_media")
    assert sum(tally[part] for part in parts) == tally["total"]
    assert tally["total"] == len(manifest["videos"])
    # And each part is the count of records that actually look like it — a sum
    # that matches while both halves are wrong is the failure this catches.
    records = manifest["videos"]
    assert tally["documented_walkthroughs"] == sum(
        1 for record in records if record["provider"] == "guidde"
    )
    assert tally["transcribed_media"] == sum(
        1
        for record in records
        if record["provider"] == "media-element" and record["transcribed"]
    )
    assert tally["untranscribed_media"] == sum(
        1
        for record in records
        if record["provider"] == "media-element" and not record["transcribed"]
    )


def test_a_documented_walkthrough_declares_where_its_text_came_from() -> None:
    """`authored` is the claim that distinguishes it from a machine transcript."""
    record = video_bundle.build_video_bundle(playbook(), []).manifest_record
    assert record["transcribed"] is True
    assert record["transcript_provenance"] == "authored"


@pytest.mark.browser
def test_outbound_links_survive_into_the_pdf_and_the_bundle(tmp_path: Path) -> None:
    """A real anchor becomes a real link annotation — asserted, not assumed."""
    pdf, bundle, _manifest = _capture(tmp_path, videos=False)
    wanted = (
        "https://help.example.com/Portal/Login.aspx"
        "?ReturnUrl=/Portal/KB/ArticleDet?ID=1234"
    )
    links = json.loads((bundle / "links.json").read_text("utf-8"))
    urls = [link["url"] for link in links]
    assert wanted in urls, "the ReturnUrl form is preserved exactly, not unwrapped"
    assert any(
        url.startswith("file://") and url.endswith("checklist.html") for url in urls
    ), "a relative href is absolutised"
    assert wanted in _pdf_link_uris(PdfReader(str(pdf)))


@pytest.mark.browser
def test_no_videos_leaves_the_capture_exactly_as_it_was(tmp_path: Path) -> None:
    """`--no-videos` reproduces today's output, byte for byte."""
    _pdf, bundle, manifest = _capture(tmp_path / "off", videos=False)
    assert "videos" not in manifest, "a manifest without videos does not mention them"
    assert "video_tally" not in manifest
    assert "video_appendix" not in manifest["pdf"]
    assert not (bundle / "videos").exists()
    assets = json.loads((bundle / "assets.json").read_text("utf-8"))
    assert "video_assets" not in assets
    for line in (bundle / "chunks.jsonl").read_text("utf-8").splitlines():
        assert "video" not in json.loads(line)["meta"]


def _flattened(items: Any) -> list[Any]:
    """Every outline entry, nesting removed, in document order."""
    found: list[Any] = []
    for item in items:
        if isinstance(item, list):
            found.extend(_flattened(item))
        else:
            found.append(item)
    return found


def _outline_titles(items: Any) -> list[str]:
    return [
        str(item.title) for item in _flattened(items) if getattr(item, "title", None)
    ]


def _pdf_link_uris(reader: PdfReader) -> list[str]:
    uris: list[str] = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            annotation = reference.get_object()
            action = annotation.get("/A")
            if action and (uri := action.get_object().get("/URI")):
                uris.append(str(uri))
    return uris


# --------------------------------------------------------------------------- #
# Fixes the golden corpus cannot reach
#
# Every assertion below is here because the recorded corpus does not exercise
# the path: the sample playbook is entirely `textToSpeech`, every step has
# subtitle cues, every asset URL is well-formed and declares a length, and the
# LMS fixture has one heading per video. A fix nothing calls is a fix that did
# not ship (docs/09 P7-12), so each one is called here directly.
# --------------------------------------------------------------------------- #


def test_unverified_narration_is_said_in_the_structured_field_too() -> None:
    """`transcript_provenance` was the literal "authored" on every walkthrough.

    Only the sibling prose in `narration` disagreed, so a consumer filtering
    the structured field — the reason a structured field exists — was told the
    subtitles were authoritative in exactly the case the parser had flagged
    (docs/09 P8-18).
    """
    document = json.loads(PLAYBOOK_FIXTURE.read_text("utf-8"))
    document["playbook"]["steps"][1]["audioNote"]["type"] = "humanRecording"
    unverified = guidde.parse_playbook(
        json.dumps(document), expected_id=PLAYBOOK_ID, source_url=SHARE_URL
    )
    record = video_bundle.build_video_bundle(unverified, []).manifest_record
    assert record["transcript_provenance"] == "unverified"
    assert record["narration"].startswith("unverified: ")

    # And the ordinary case still says authored, or the field says nothing.
    authored = video_bundle.build_video_bundle(playbook(), []).manifest_record
    assert authored["transcript_provenance"] == "authored"


def test_authored_narration_reaches_the_transcript_without_subtitle_cues() -> None:
    """A step can carry `audioNote.markdown` and no `subtitles`.

    Parsing set `step.narration`, which makes the step non-silent, and the
    transcript writers emitted only `step.cues` — so `transcript.txt` held the
    heading and nothing under it and `transcript.vtt` held nothing at all, for
    a step the bundle counted as narrated (docs/09 P8-28).
    """
    book = playbook()
    narrated = next(step for step in book.steps if step.cues and step.narration)
    stripped = replace(narrated, cues=())
    assert not stripped.silent, "the step must still count as narrated"

    cues = video_bundle.narration_cues(stripped)
    assert len(cues) == 1
    assert cues[0].text == stripped.narration
    assert cues[0].start == stripped.start and cues[0].end == stripped.end

    without = replace(book, steps=tuple(replace(s, cues=()) for s in book.steps))
    text = video_bundle.transcript_text(without)
    vtt = video_bundle.transcript_vtt(without)
    assert stripped.narration in text
    assert stripped.narration in vtt
    # And the count the manifest publishes describes what the file holds.
    record = video_bundle.build_video_bundle(without, []).manifest_record
    assert record["transcript_cues"] == vtt.count("-->")


def test_a_step_with_neither_cues_nor_narration_stays_silent() -> None:
    """The fallback must not invent narration for a genuinely silent step."""
    book = playbook()
    silent = next(step for step in book.steps if step.silent)
    assert video_bundle.narration_cues(replace(silent, cues=())) == ()


def test_a_walkthrough_that_cannot_be_fetched_is_still_counted() -> None:
    """Detection succeeded, so the bundle knows the video is there.

    Dropping it left `videos`, `video_tally`, `content.md` and the embedded
    README describing a two-player page as a one-video page, and a PDF-only
    consumer saw no trace of the second at all (docs/09 P8-27).
    """
    unreachable = video_bundle.MediaVideo(
        source_url="https://app.guidde.com/share/playbooks/private",
        duration_seconds=None,
        poster_url="",
        title="A private walkthrough",
        note_override="This walkthrough is NOT transcribed. WebShot detected it.",
    )
    enrichment = video_bundle.VideoEnrichment(
        walkthroughs=[video_bundle.build_video_bundle(playbook(), [])],
        media=[unreachable],
    )
    assert enrichment.tally["total"] == 2
    assert enrichment.tally["documented_walkthroughs"] == 1
    assert enrichment.tally["untranscribed_media"] == 1
    record = unreachable.manifest_record
    assert record["transcribed"] is False
    assert "WebShot detected it" in record["note"]
    # The override is used, not the "no authored walkthrough behind it" note,
    # which would be false: there is one, and it is why this record exists.
    assert "no authored walkthrough behind it" not in record["note"]


def test_the_exclusion_list_covers_the_source_recording() -> None:
    """A page that renders its own playbook's recording listed it twice —
    once documented and once NOT TRANSCRIBED (docs/09 P8-26)."""
    book = playbook()
    assert book.source_recording_url, "fixture must name a source recording"
    bundle = video_bundle.build_video_bundle(book, [])
    assert book.source_recording_url in bundle.clip_urls


def test_a_repeated_heading_takes_successive_sections() -> None:
    """`_section_end` always matched the first heading of a given text, so a
    later section's walkthrough was spliced under an earlier, unrelated one
    (docs/09 P8-36)."""
    markdown = "\n".join(
        [
            "# Module",
            "",
            "## Overview",
            "",
            "First module prose.",
            "",
            "## Overview",
            "",
            "Second module prose.",
            "",
        ]
    )
    placed = video_bundle._placed(
        markdown.rstrip().split("\n"),
        [("Overview", "FIRST-BLOCK"), ("Overview", "SECOND-BLOCK")],
    )
    first = placed.index("FIRST-BLOCK")
    second = placed.index("SECOND-BLOCK")
    divider = placed.index("Second module prose.")
    assert first < divider < second, placed


def test_two_videos_under_one_heading_both_land_at_its_end() -> None:
    """The behaviour the occurrence counter must not break."""
    markdown = "# Module\n\n## Overview\n\nOnly prose.\n"
    placed = video_bundle._placed(
        markdown.rstrip().split("\n"),
        [("Overview", "FIRST-BLOCK"), ("Overview", "SECOND-BLOCK")],
    )
    assert placed.index("FIRST-BLOCK") < placed.index("SECOND-BLOCK")
    assert "Only prose." in placed


@pytest.mark.parametrize(
    "url",
    ["http://[", "https://[::", "http://[fe80::1"],
    ids=["unclosed-bracket", "truncated-v6", "unterminated-v6-literal"],
)
def test_a_malformed_asset_url_is_refused_not_raised(url: str) -> None:
    """Both classifiers parse the URL and `urlparse` raises on a malformed one.

    Those calls sit outside the request `try`, so the exception escaped
    `_download` and failed a capture that had already rendered — where the
    documented behaviour is a per-asset warning (docs/09 P8-37).
    """
    with pytest.raises(ValueError):
        video_bundle.permitted_asset_host(url, "https://app.guidde.com")

    problem = asyncio.run(
        video_bundle._download(
            request=None,
            url=url,
            destination_directory=Path("/nonexistent"),
            asset_id="asset-001",
            block_private=True,
            origin="https://app.guidde.com",
        )
    )
    assert problem[0] is None
    assert "not a URL this capture can classify" in problem[3]


def test_the_live_dom_prefilter_lowercases_before_comparing() -> None:
    """The offline scanner above and the live DOM scan are two readers of one
    rule, and only the offline one had a test.

    The live scan's cheap gate compared `getAttribute`'s value verbatim against
    the host constant, so a mixed-case embed never reached the check that would
    have accepted it (docs/09 P8-24).
    """
    script = guidde._SCAN_SCRIPT
    assert ".toLowerCase().includes(" in script
    assert json.dumps(guidde.GUIDDE_HOSTS[0].lower()) in script
    # And the constant it is built from is itself lowercase, or lowercasing
    # the attribute would just move the mismatch to the other side.
    assert guidde.GUIDDE_HOSTS[0] == guidde.GUIDDE_HOSTS[0].lower()


class _FakeResponse:
    """The narrow slice of Playwright's `APIResponse` that `_store` touches."""

    def __init__(self, headers: dict[str, str], body: bytes) -> None:
        self.headers = headers
        self._body = body
        self.reads = 0

    async def body(self) -> bytes:
        self.reads += 1
        return self._body


def test_an_undeclared_body_length_is_refused_before_it_is_read(
    tmp_path: Path,
) -> None:
    """`APIResponse.body()` buffers the whole response in the driver process and
    Playwright's request context has no streaming read.

    So a cap applied afterwards has already paid the cost it exists to refuse:
    a server omitting `Content-Length` and answering with an endless chunked
    body exhausts memory before `MAX_ASSET_BYTES` is consulted — and these URLs
    come out of the intercepted playbook (docs/09 P8-17).
    """
    response = _FakeResponse({"content-type": "image/png"}, PIXEL)
    path, body, cost, problem = asyncio.run(
        video_bundle._store(
            response, "https://storage.app.guidde.com/x.png", tmp_path, asset_id="a-1"
        )
    )
    assert path is None and body == b"" and cost == 0
    assert "no Content-Length" in problem
    assert response.reads == 0, "the body must not be read at all"


def test_an_oversized_declared_length_is_refused_and_still_charged(
    tmp_path: Path,
) -> None:
    """Nothing is read, but the budget is charged: a playbook of oversized
    steps must not be able to name unlimited work for free (docs/09 P7-10)."""
    declared = video_bundle.MAX_ASSET_BYTES + 1
    response = _FakeResponse(
        {"content-length": str(declared), "content-type": "image/png"}, PIXEL
    )
    path, _body, cost, problem = asyncio.run(
        video_bundle._store(
            response, "https://storage.app.guidde.com/x.png", tmp_path, asset_id="a-1"
        )
    )
    assert path is None and cost == declared
    assert "per-file limit" in problem
    assert response.reads == 0


def test_a_declared_length_that_lies_is_still_caught_after_the_read(
    tmp_path: Path,
) -> None:
    """The residual this fix does not close, pinned so it stays a known limit.

    A server can declare a small length and send a large body; the buffering
    happens inside the driver, so the only defences left are the request
    timeout, the per-capture budget, and `permitted_asset_host`. What WebShot
    can still do is refuse to *keep* it, and charge it.
    """
    oversized = b"x" * (video_bundle.MAX_ASSET_BYTES + 1)
    response = _FakeResponse(
        {"content-length": "10", "content-type": "image/png"}, oversized
    )
    path, _body, cost, problem = asyncio.run(
        video_bundle._store(
            response, "https://storage.app.guidde.com/x.png", tmp_path, asset_id="a-1"
        )
    )
    assert path is None and cost == len(oversized)
    assert "per-file limit" in problem


def test_an_ordinary_declared_asset_is_still_stored(tmp_path: Path) -> None:
    """The narrowing must not refuse the case it was written around."""
    response = _FakeResponse(
        {"content-length": str(len(PIXEL)), "content-type": "image/png"}, PIXEL
    )
    path, body, cost, problem = asyncio.run(
        video_bundle._store(
            response, "https://storage.app.guidde.com/x.png", tmp_path, asset_id="a-1"
        )
    )
    assert problem == ""
    assert path is not None and path.read_bytes() == PIXEL
    assert body == PIXEL and cost == len(PIXEL)


# --------------------------------------------------------------------------- #
# A title has to be title-shaped
# --------------------------------------------------------------------------- #

#: What one live capture actually recorded as a `media-element` title: the OCR
#: of the player's own chrome — scrubber, timestamp, mute glyph — reached
#: `aria-label` because the element carried no label of its own, and the DOM
#: scan reads `aria-label` when `title` is empty. Kept verbatim, newlines and
#: all, because a paraphrase of it would not exercise the defect.
OCR_CHROME_TITLE = "Text recognized within visual asset: +e]\ni\na\n> 0:00/4:11\n«)\n—_"


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        # The live blob. Nothing here is a name.
        (OCR_CHROME_TITLE, ""),
        # An author-written title survives untouched — the refusal must not
        # cost the case it was written around.
        ("Quarterly training video", "Quarterly training video"),
        # Recognition is appended *after* the author's own text, joined with
        # ` | `, so the author's half is kept and the recognition dropped.
        (
            "Quarterly training video | Text recognized within visual asset: 0:00",
            "Quarterly training video",
        ),
        # A multi-line value is refused whatever produced it: interpolated into
        # a single-line heading it breaks the document around it.
        ("Two\nlines", ""),
        ("Carriage\rreturn", ""),
        # No title at all is not a refusal, it is simply absence.
        ("", ""),
        ("   ", ""),
    ],
    ids=[
        "ocr-chrome",
        "authored",
        "authored-then-ocr",
        "newline",
        "carriage-return",
        "empty",
        "blank",
    ],
)
def test_only_title_shaped_text_becomes_a_title(raw: str, kept: str) -> None:
    """docs/09 P12-4's shape: a field filled from text that happened to be near.

    The naive implementation is the one that shipped — take whatever the DOM
    offered — and it wrote `> 0:00/4:11` into `content.md` as a blockquote and
    broke a markdown heading across six lines.
    """
    assert video_bundle.media_title(raw)[0] == kept


def test_a_refused_title_is_reported_not_silently_dropped() -> None:
    """Absence must not read as success (CONTRIBUTING rule 5).

    A media element the bundle silently renamed reads exactly like one the
    author never named, so the refusal reaches `manifest.warnings` — and it
    says *why* without quoting the recognized text, because a warning carrying
    OCR output would record a fact about the recording machine (docs/09 P7-13).
    """
    assert video_bundle.media_title("Quarterly training video")[1] == ""
    assert video_bundle.media_title("")[1] == ""
    ocr = video_bundle.media_title(OCR_CHROME_TITLE)[1]
    assert ocr == video_bundle.TITLE_WAS_RECOGNIZED_TEXT
    assert "0:00" not in ocr and "4:11" not in ocr
    assert video_bundle.media_title("Two\nlines")[1] == (
        video_bundle.TITLE_SPANNED_LINES
    )


def test_the_heading_fallback_is_held_to_the_same_shape() -> None:
    """The other side of the same boundary (docs/09 P7-12).

    A refused title falls back to the heading the element sat under, and that
    heading is an `<h2>`'s `innerText` — a `<br>` in it puts a newline into the
    `### …` line and the chunk title, which is the defect the refusal exists to
    stop, arriving through the fix's own fallback.
    """
    video = video_bundle.MediaVideo(
        source_url="https://example.invalid/x.mp4",
        duration_seconds=None,
        poster_url="",
        title="",
        heading="Understanding the\nsandbox environment",
    )
    assert video.name == "Understanding the sandbox environment"
    assert "\n" not in video_bundle._media_note(video, position=1)[0]
    chunk = video_bundle.media_chunks([video], start_index=0)[0]
    assert "\n" not in chunk["meta"]["media"]["title"]


class _TitleOnlyPage:
    """Just enough page for the two evaluations `detect_media_videos` makes.

    `read_caption_tracks` passes an argument list to `evaluate` and the media
    scan does not, which is the whole distinction this needs to draw.
    """

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        return [] if arg is not None else self._entries


def test_the_scan_refuses_an_ocr_title_and_the_capture_says_so() -> None:
    """The wiring, not only the predicate: a fix nothing calls did not ship."""
    page = _TitleOnlyPage(
        [
            {
                "source": "https://example.invalid/sandbox.mp4",
                "mediaKind": "video",
                "title": OCR_CHROME_TITLE,
                "duration": 251.0,
                "heading": "Understanding the sandbox environment",
            }
        ]
    )
    media = asyncio.run(video_bundle.detect_media_videos(page))
    assert [video.title for video in media] == [""]
    assert media[0].refused_title == video_bundle.TITLE_WAS_RECOGNIZED_TEXT

    # And the element is still named by something real, rather than untitled:
    # the heading it sat under is the page's own text.
    note = video_bundle._media_note(media[0], position=1, plain=True)
    assert note[0] == (
        "Video not transcribed: Understanding the sandbox environment (4:11)"
    )
    assert "0:00/4:11" not in "\n".join(note)

    enrichment = video_bundle.VideoEnrichment()
    enrichment.media = asyncio.run(video_bundle._media_or_warn(page, enrichment))
    assert any(
        "refused" in warning and "sandbox.mp4" in warning
        for warning in enrichment.warnings
    ), enrichment.warnings


# --------------------------------------------------------------------------- #
# The walkthrough belongs in content.txt too
# --------------------------------------------------------------------------- #

#: How much of `content.md` a Guidde module's `content.txt` must carry. The
#: two are not equal — `content.txt` drops the image references and the
#: heading marks — but they hold the same words, so a large shortfall means a
#: body of text reached one file and not the other. Measured live: 78 of 78
#: modules with a walkthrough were under this, the worst 379 bytes against
#: 10388.
MIN_PLAIN_TEXT_RATIO = 0.6


def test_the_plain_walkthrough_carries_the_same_claims_without_the_marks() -> None:
    """Everything the markdown says, minus only what makes it markdown.

    `content.txt` is the plain-text rendering of the page everywhere else, so
    heading hashes, image references and backticks in it would be the only
    ones in the file — and `_as_paragraph`'s escaping, which exists to stop
    markdown injection into `content.md`, would put visible stray backslashes
    into a file with no markdown to protect.
    """
    book = playbook()
    plain = video_bundle.walkthrough_plain_text(book, [])
    for mark in ("##", "![", "`", "\\", "> "):
        assert mark not in plain, mark
    # And it is not a summary: every step, its timestamp, its instruction and
    # its narration are all there, plus the sentence saying what this text is.
    assert plain.count("Step ") >= len(book.steps)
    assert "not a transcript of a recording" in plain
    assert video_bundle.SILENT_STEP_NOTE in plain
    for step in book.steps:
        assert video_bundle.timestamp(step.start) in plain
        if step.instruction:
            assert step.instruction.split("\n")[0][:40] in plain
    for cue in (cue for step in book.steps for cue in step.cues):
        assert cue.text in plain


def test_a_named_screenshot_replaces_the_image_reference() -> None:
    """The still is a real artifact of the step; only the markdown link goes."""
    asset = video_bundle.VideoAsset(
        id="video-x-step-001",
        file="assets/x.png",
        kind="step-screenshot",
        playbook_id=PLAYBOOK_ID,
        source_url="https://storage.app.guidde.com/x.webp",
        bytes=1,
        sha256="0" * 64,
        step=1,
    )
    plain = video_bundle.walkthrough_plain_text(playbook(), [asset])
    assert "Screenshot: assets/x.png" in plain


@pytest.mark.parametrize("case", ["lms-module-videos"])
def test_the_walkthrough_reaches_content_txt_not_only_content_md(case: str) -> None:
    """The recorded corpus holds the line this fix draws.

    `inline_into_content` had exactly one call site and it was on `content.md`,
    so the walkthrough — the largest body of text this tool produces — was
    absent from `content.txt` entirely. The argument against that is already
    written down in `_note_media`'s docstring, for the media note beside it:
    an agent that searches `content.txt` and finds no mention would reasonably
    conclude the page had none.
    """
    bundle = REPO_ROOT / "tests" / "golden" / case / "bundle"
    markdown = (bundle / "content.md").read_text("utf-8")
    plain = (bundle / "content.txt").read_text("utf-8")
    # The procedure itself, not merely the video's title.
    assert "This is the walkthrough as text" in plain
    assert "Once you are signed in, open Projects and choose a project." in plain
    assert "Step 11 — Thank you" in plain
    # And no markdown leaked in with it.
    assert "![" not in plain and "\n## " not in plain
    assert len(plain) > MIN_PLAIN_TEXT_RATIO * len(markdown), (
        f"content.txt is {len(plain)} bytes against content.md's "
        f"{len(markdown)}; a Guidde module's plain text holds the same words"
    )
