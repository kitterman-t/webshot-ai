"""The golden corpus: every capture the harness records and re-checks.

Cases are deliberately small and local — no test may touch the public internet
(docs/06-quality-and-testing.md).  Between them they exercise every branch of
the orchestrator that Phase 0.6 moved: both capture modes, selector isolation
(explicit and automatic), page-geometry options, the OCR and AI-bundle opt-outs,
every local-source adapter, and the protected-viewer assembly path.

Each case also declares what its PDF must structurally be — tagged or not,
bookmarked or not — which is checked directly against the file rather than
recorded, so the assertion holds on every profile and cannot be re-recorded
away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WEB_OCR_ARTIFACTS = (
    "bundle/content.json",
    "bundle/content.md",
    "bundle/chunks.jsonl",
    "pdf-text.txt",
)
#: Where an *absence* is asserted, and deliberately the same pair the positive
#: sentinels use — the DOM's answer and the render's. An earlier version limited
#: this to `pdf-text.txt`, reasoning that extraction keeps a hidden element and
#: so could not be asked to drop the string. Measured against the recorded
#: `clamped-noise` golden that is simply not what happens: the hidden text is
#: absent from `content.md`, `content.txt` and `content.html` as well. Asserting
#: both is therefore strictly stronger, passes today, and keeps an absence
#: claim the mirror image of the presence claim beside it.
ABSENT_SENTINEL_ARTIFACTS = ("bundle/content.md", "pdf-text.txt")

VIEWER_OCR_ARTIFACTS = (
    "bundle/content.txt",
    "bundle/content.md",
    "bundle/chunks.jsonl",
    "bundle/ocr/text-layer.txt",
    "pdf-text.txt",
)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One recorded capture.

    `sentinels` are strings that exist **only** inside a raster on the fixture
    page (a canvas chart, a scanned page).  Finding them in the recognized text
    proves OCR ran, which is how the portable comparison profile keeps checking
    OCR after the recognized words themselves stop being byte-comparable.

    They also carry a second job now: a sentinel inside a CSS-truncated region,
    asserted against both `bundle/content.md` and `pdf-text.txt`, is what makes
    the DOM and the render disagree loudly instead of silently
    (docs/13 P9-7, P13-1).  `absent_sentinels` is the inverse assertion.
    """

    id: str
    #: How the case is produced. `cli` shells out; `protected` and `video` call
    #: the pipeline directly, because both need a fixture standing in for
    #: something a command line cannot supply — recorded page images for one, a
    #: Guidde share page for the other.
    kind: Literal["cli", "protected", "video", "captions"] = "cli"
    source: str = ""
    args: tuple[str, ...] = field(default_factory=tuple)
    sentinels: tuple[str, ...] = field(default_factory=tuple)
    sentinel_artifacts: tuple[str, ...] = WEB_OCR_ARTIFACTS
    #: Strings that must **not** appear, in `absent_sentinel_artifacts`. Added
    #: for the clamp release: an element that is both a noise target and clamped
    #: can be un-hidden by the release, and "the capture kept this out" is not
    #: something a presence assertion can say. A recorded golden would not catch
    #: it either — the un-hidden text would simply be recorded as expected
    #: output.
    absent_sentinels: tuple[str, ...] = field(default_factory=tuple)
    absent_sentinel_artifacts: tuple[str, ...] = ABSENT_SENTINEL_ARTIFACTS
    #: Whether the produced PDF must carry a structure tree and `MarkInfo`.
    #: This is docs/05 task 1.1's assertion: `--no-tagged-pdf` is a *request*,
    #: and something has to check that Chromium honored it in both directions.
    #: Image-backed protected PDFs are never tagged (docs/09 S8).
    tagged: bool = True
    #: Whether the produced PDF must carry bookmarks.
    outline: bool = True
    #: Whether the PDF's text layer is OCR output rather than rendered text.
    #: The portable profile masks it for the same reason it masks recognized
    #: text everywhere else: it is a function of the Tesseract build.
    ocr_pdf_text: bool = False


CORPUS: tuple[GoldenCase, ...] = (
    GoldenCase(
        "article-clean",
        source="tests/fixtures/professional_page.html",
        sentinels=("Revenue 2026",),
    ),
    GoldenCase(
        "article-faithful",
        source="tests/fixtures/professional_page.html",
        args=("--mode", "faithful"),
        sentinels=("Revenue 2026",),
    ),
    GoldenCase(
        "article-selector",
        source="tests/fixtures/professional_page.html",
        args=("--selector", "main", "--no-header-footer"),
        sentinels=("Revenue 2026",),
    ),
    GoldenCase(
        "article-auto-selector",
        source="tests/fixtures/professional_page.html",
        args=("--auto-selector", "--exclude", "table", "--title", "Auto selected"),
        sentinels=("Revenue 2026",),
    ),
    GoldenCase(
        "article-geometry",
        source="tests/fixtures/professional_page.html",
        args=(
            "--landscape",
            "--format",
            "A4",
            "--scale",
            "1.0",
            "--margin",
            "12mm",
            "--media",
            "screen",
            "--prefer-css-page-size",
        ),
        sentinels=("Revenue 2026",),
    ),
    GoldenCase(
        "article-no-ai-bundle",
        source="tests/fixtures/professional_page.html",
        args=("--no-ai-bundle",),
    ),
    GoldenCase(
        "article-no-ocr",
        source="tests/fixtures/professional_page.html",
        args=("--no-ocr",),
    ),
    GoldenCase(
        "article-capped-assets",
        source="tests/fixtures/professional_page.html",
        args=("--max-assets", "0"),
    ),
    GoldenCase(
        "article-untagged",
        source="tests/fixtures/professional_page.html",
        args=("--no-tagged-pdf", "--no-outline", "--no-scroll"),
        tagged=False,
        outline=False,
    ),
    # Pins the --legacy-bundle escape hatch through v3.0 (docs/05 task 2.5);
    # the case and the flag are removed together at v3.1.
    GoldenCase(
        "article-legacy",
        source="tests/fixtures/professional_page.html",
        args=("--legacy-bundle",),
        sentinels=("Revenue 2026",),
    ),
    # The three fidelity fixtures below were added by Phase 2's corpus audit
    # (docs/06 fixture corpus; docs/02 fidelity mapping): the parity harness can
    # only measure form fields, definition lists, and embedded-media/track
    # records if some fixture exercises them — and their v2 baselines had to be
    # recorded while the v2 extraction path still existed.
    GoldenCase("form-fields", source="tests/fixtures/form_page.html"),
    # Two password fields whose values overlap — the second contains the first.
    # A separate page rather than two more fields on `form_page.html`, because
    # that fixture has a frozen v2 parity baseline that cannot be re-recorded
    # (v2 no longer exists to run), and changing the page would leave parity
    # comparing a v3 capture against a v2 capture of a different document.
    # Deliberately has no baseline of its own: `parity/check.py` skips a case
    # without one and says so, which is the honest outcome for a fixture that
    # postdates v2 (docs/09 P8-8).
    GoldenCase(
        "form-overlapping-passwords",
        source="tests/fixtures/form_overlapping_passwords.html",
    ),
    GoldenCase("definition-structures", source="tests/fixtures/structures_page.html"),
    GoldenCase("embedded-media", source="tests/fixtures/media_page.html"),
    # An LMS module page with video enrichment off. Pins everything around the
    # feature: the outbound anchors (including the `Login.aspx?ReturnUrl=`
    # form, which must survive unrewritten), the lazily-hydrated player iframe,
    # the plain media records, and the proof that `--no-videos` output is
    # exactly what it was before this feature existed.
    GoldenCase(
        "lms-module",
        source="tests/fixtures/lms_page.html",
        args=("--no-videos",),
    ),
    # And the same page with enrichment on. This is the most intricate output
    # WebShot produces — an appended appendix, a two-level outline, per-step
    # chunks, four artifacts per video — so leaving it to assertions alone
    # would reopen exactly the gap docs/09 P6-2 was written about, in the
    # newest subsystem. It runs the pipeline directly rather than through the
    # command line because it needs a Guidde stand-in on a loopback port, the
    # same reason `protected-viewer` does.
    GoldenCase("lms-module-videos", kind="video"),
    # Text the page truncates with CSS alone. `innerText` returns all of it, so
    # the bundle is complete and every text assertion passes while the render
    # drops the clipped part — which is why this case names BOTH artifacts:
    # `bundle/content.md` is the DOM's answer and `pdf-text.txt` is the render's,
    # and the defect is precisely the two disagreeing (docs/13 P9-7, P13).
    GoldenCase(
        "clamped-text",
        source="tests/fixtures/clamped_page.html",
        sentinels=(
            "CLAMPEDLINECLAMPOMEGA",
            "CLAMPEDCOLLAPSESIGMA",
            "UNCLAMPEDBASELINEALPHA",
        ),
        sentinel_artifacts=("bundle/content.md", "pdf-text.txt"),
    ),
    # The clamp release must not resurrect what clean mode removes. A noise
    # selector and a clamp on the SAME element put `display: none` and
    # `display: block` at equal specificity, and the release won on source
    # order. The assertion is an absence, which no recorded golden can make:
    # the un-hidden text would simply have been recorded as expected output.
    GoldenCase(
        "clamped-noise",
        source="tests/fixtures/clamped_noise.html",
        sentinels=("BODYTEXTPRESENT",),
        sentinel_artifacts=("bundle/content.md", "pdf-text.txt"),
        absent_sentinels=("NOISESHOULDNOTAPPEAR",),
    ),
    # An equivalent page to `embedded-media`, over http instead of file://, and
    # the scheme is the whole case. A `file://` document is an opaque origin, so
    # the browser fetches the caption file and discards the cues — that case
    # records a declared-and-unreadable track, and it *cannot* exercise path 1.
    # Served over loopback the captions are read, and the two cases together are
    # what make "a track we could not read" and "a track we read" distinguishable
    # rather than both arriving as `transcribed: false`. Its own fixture because
    # `media_page.html` is the input a frozen parity baseline was recorded from
    # (docs/09 P10-8).
    #
    # The two sentinel sets are the two halves of path 1's one safety property.
    # The English captions must reach the surfaces an agent searches — a
    # transcript readable only in its own file is one the bundle effectively
    # does not have. The German subtitles must reach none of them: retrieved,
    # a translation is indistinguishable from a record of what was said. Both
    # are asserted here rather than only in a unit test, because this is where
    # the whole pipeline runs and a golden would happily record either failure.
    GoldenCase(
        "captions-read",
        kind="captions",
        sentinels=("Welcome to the quarterly capture workflow review",),
        sentinel_artifacts=(
            "bundle/videos/media-01/transcript.txt",
            "bundle/content.md",
            "bundle/content.txt",
            "bundle/chunks.jsonl",
        ),
        absent_sentinels=("Willkommen zur",),
        absent_sentinel_artifacts=(
            "bundle/content.md",
            "bundle/content.txt",
            "bundle/chunks.jsonl",
            "pdf-text.txt",
        ),
    ),
    GoldenCase("markdown-report", source="tests/fixtures/sample_report.md"),
    GoldenCase("csv-data", source="tests/fixtures/sample_data.csv"),
    GoldenCase("json-data", source="tests/fixtures/sample_data.json"),
    GoldenCase("jsonl-records", source="tests/fixtures/sample_records.jsonl"),
    GoldenCase("text-notes", source="tests/fixtures/sample_notes.txt"),
    GoldenCase("svg-chart", source="tests/fixtures/chart-source.svg"),
    GoldenCase(
        "protected-viewer",
        kind="protected",
        sentinels=("SENTINEL PAGE ONE ALPHA", "SENTINEL PAGE TWO BRAVO"),
        sentinel_artifacts=VIEWER_OCR_ARTIFACTS,
        tagged=False,
        ocr_pdf_text=True,
    ),
)

CASES_BY_ID = {case.id: case for case in CORPUS}
