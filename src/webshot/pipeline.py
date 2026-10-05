"""The capture pipeline: one source in, one validated PDF and one bundle out.

ACQUIRE -> CAPTURE -> (RENDER | EXTRACT) -> PACKAGE, with the protected-viewer
path branching after CAPTURE.  The stages live in their own modules; this file
is the order they run in, the state they hand each other, and the cleanup that
has to happen whether or not any of it worked.

Publication is deliberately last and deliberately atomic: the PDF is rendered
to a temporary file, validated, and only then swapped into place alongside the
bundle, with the previous outputs kept as backups until both swaps succeed
(docs/04-spec.md §2.2).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .acquire.adapters import MaterializedSource, materialize_source
from .bundle.build import (
    AIBundleResult,
    create_ai_bundle,
    embed_provenance,
    finalize_ai_bundle,
)
from .bundle.localpaths import LocalPathRecorder
from .bundle.manifest import BUNDLE_FORMAT
from .bundle.publish import announce_replacements, publish_ai_bundle, sha256_file
from .bundle.videos import VideoEnrichment, enrich_with_videos
from .capture.discover import read_page
from .capture.landing import (
    WallCheck,
    empty_capture_message,
    requested_url,
    sign_in_wall,
    sign_in_wall_message,
)
from .capture.prepare import (
    BASE_PRINT_CSS,
    CLEAN_PRINT_CSS,
    NOISE_SELECTORS,
    _wait_for_fonts_and_images,
    has_scrolling_tables,
    hide_elements,
    hide_obstructive_overlays,
    isolate_content,
    release_clamped_text,
    release_wide_tables,
    scroll_to_load,
    select_main_content,
)
from .capture.session import launch_context
from .config import CaptureOptions, CaptureResult
from .errors import (
    AuthenticationError,
    BundleBuildError,
    CaptureIntegrityError,
    EnvironmentFailure,
    NavigationError,
    OcrUnavailableError,
    PdfValidationError,
    UsageError,
    certificate_hint,
    is_unreachable,
)
from .ocr.engine import OcrEngine, resolve_engine
from .outputlock import OutputLock
from .protected.assemble import build_protected_viewer_artifacts
from .protected.viewer import capture_sharepoint_pdf_viewer
from .render.clipping import clipping_warnings, find_clipped_text, printed_width
from .render.embed import collect_payloads, embed_bundle
from .render.frames import fit_frames
from .render.ligatures import (
    ligature_forms,
    ligature_warning,
    suppress_ligatures,
)
from .render.pdf import is_tagged, render_pdf, validate_pdf
from .render.video_appendix import append_video_appendix
from .validate.verapdf import validate as verapdf_validate
from .version import VERSION as GENERATOR_VERSION

LOGGER = logging.getLogger("webshot")


#: How many failed URLs a capture lists, in `--report` and the MCP result. The
#: count covers every one.
LISTED_FAILED_REQUESTS = 20


class RequestFailures:
    """Every distinct request the page made and did not get, counted by kind.

    The capture kept the first 20 distinct URLs and nothing else, and the CLI
    printed the length of that list as the total: a page that lost 150 images
    said "20 resource request(s) failed", and the manifest said nothing at all
    (docs/09 P20-6). The list stays a sample, for `--report` and the MCP
    result; the count and the kinds cover every failure.

    Counting stops at `close()`, once the PDF is printed: a request that fails
    after that changes nothing the capture holds, and a count that went on
    moving would disagree with the warning the manifest already carries.
    """

    def __init__(self) -> None:
        self.listed: list[str] = []
        #: Playwright's `resource_type` per distinct URL: image, stylesheet,
        #: script, font, fetch, and so on.
        self.kinds: dict[str, int] = {}
        self._seen: set[str] = set()
        self._closed = False

    @property
    def count(self) -> int:
        return len(self._seen)

    def record(self, request: object) -> None:
        """The `requestfailed` handler."""
        if self._closed:
            return
        url = getattr(request, "url", "unknown request")
        if url in self._seen:
            return
        self._seen.add(url)
        kind = getattr(request, "resource_type", "") or "other"
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if len(self.listed) < LISTED_FAILED_REQUESTS:
            self.listed.append(url)

    def close(self) -> str | None:
        """Stop counting; the manifest warning for what failed, if anything did."""
        self._closed = True
        if not self._seen:
            return None
        return failed_requests_warning(self.count, self.kinds)


def failed_requests_warning(count: int, kinds: dict[str, int]) -> str:
    """The manifest's account of the failed requests: how many, and of what.

    No URL: the manifest travels with the PDF, a URL can carry a token in its
    query, and `--report` already lists them.
    """
    by_kind = ", ".join(
        f"{kind} {number}"
        for kind, number in sorted(kinds.items(), key=lambda item: (-item[1], item[0]))
    )
    return (
        f"{count} request{'' if count == 1 else 's'} for the page's resources "
        f"failed ({by_kind}), so the PDF and the bundle may lack what "
        f"{'it' if count == 1 else 'they'} would have loaded."
    )


def page_count(pages: int) -> str:
    """`1 page`, `2 pages`: the count the log reports for a printed PDF.

    It said "(1 pages, ...)", the line twelve of the golden cases end on
    (docs/09 P20-5).
    """
    return f"{pages} page{'' if pages == 1 else 's'}"


def ignored_option_warnings(options: CaptureOptions) -> list[str]:
    """Flags this run cannot honour, said out loud rather than dropped.

    Every one of these is a *usage* mismatch rather than a failure — the
    capture is still exactly what the rest of the options asked for — so they
    warn and continue, the way `--legacy-bundle` has always behaved on the
    protected path.  Keeping them together means the list can be read (and
    tested) without a browser, and that no path forgets to say one of them.
    """
    warnings = []
    if (
        options.ocr_engine == "rapid"
        and options.ocr_psm_explicit
        # Not on the protected path. There, RapidOCR is the thing ignored and
        # the PSM is passed straight to OCRmyPDF/Tesseract
        # (`protected/assemble.py`, `tesseract_pagesegmode`) — so saying it was
        # ignored contradicted the setting that actually produced the PDF. The
        # separate "--ocr-engine rapid was ignored" warning below is the true
        # one for that combination (docs/09 P8-31).
        and not options.protected_viewer
    ):
        # docs/04-spec.md §1.1: page segmentation is Tesseract's model of a
        # page and RapidOCR has no counterpart.  Only worth saying when a mode
        # was actually asked for — see `cli._DefaultPsm`.
        warnings.append(
            "--ocr-psm is a Tesseract page-segmentation setting and does not "
            "apply to --ocr-engine rapid; it was ignored."
        )
    if options.videos and not (options.ai_bundle and not options.protected_viewer):
        # Said even though nobody asked for it, unlike `--ocr-psm`: skipping
        # enrichment is not an ignored preference, it is content the
        # deliverable does not contain. A capture of a page whose procedure
        # lives in a player produced no walkthrough, no untranscribed-media
        # record and no warning at all — which reads as "the page had no
        # video" (CONTRIBUTING rule 5, docs/09 P7-10).
        asked = (
            " --video-assets had nothing to download." if options.video_assets else ""
        )
        warnings.append(
            "Embedded videos were not read: their walkthroughs and the record "
            "of any media that could not be transcribed are written into the "
            "AI bundle, which this run does not build. Any video on the page "
            f"is absent from this capture.{asked}"
        )
    if options.embed_assets and not (options.embed_bundle and options.ai_bundle):
        # Rule 5: the run did less than it was asked to, so it says so. The
        # guard on `collect_payloads` meant `--embed-assets --no-embed-bundle`
        # succeeded with no attachments and no warning, which reads as a
        # capture that embedded them (docs/09 P8-32). Not an error: the flags
        # are contradictory rather than impossible, and the rest of the capture
        # is exactly what was asked for.
        reason = "--no-embed-bundle" if options.ai_bundle else "--no-ai-bundle"
        warnings.append(
            f"--embed-assets was ignored: {reason} means no bundle payloads are "
            "embedded in the PDF, and the assets travel with them. They are in "
            "the sidecar bundle's assets/ directory."
            if options.ai_bundle
            else "--embed-assets was ignored: --no-ai-bundle means there is no "
            "bundle to embed, so there are no asset attachments either."
        )
    if options.protected_viewer:
        if options.embed_bundle is False:
            # Honoured on the protected path as an explicit refusal rather than
            # silently: `build_protected_viewer_artifacts` embeds its payloads
            # unconditionally, and the QA report said `embed_bundle: false`
            # while the PDF carried attachments (docs/09 P8-33).
            warnings.append(
                "--no-embed-bundle is not supported on the protected-viewer "
                "path: its searchable PDF and its AI payloads are assembled "
                "together, so the attachments are part of the deliverable. The "
                "PDF does carry them."
            )
        if options.ocr_engine != "tesseract":
            # That path's text layer is OCRmyPDF's and its word boxes are
            # Tesseract TSV, so it has one engine end to end (docs/09 P3-7).
            warnings.append(
                f"--ocr-engine {options.ocr_engine} was ignored: the "
                "protected-viewer PDF's text layer and word coordinates are "
                "produced by OCRmyPDF and Tesseract."
            )
        if options.require_content:
            # That path checks its own output against the viewer's page count
            # (spec §2.2) and publishes from inside the builder, so there is
            # no point between the counts and the publication to refuse at.
            warnings.append(
                "--require-content was ignored: the protected-viewer path "
                "checks its pages against the viewer's own page count instead."
            )
        if options.legacy_bundle:
            warnings.append(
                "--legacy-bundle was ignored: the protected-viewer bundle "
                "format is unchanged in v3.0, so there is nothing legacy "
                "to emit."
            )
    return warnings


def _conformance_check(
    options: CaptureOptions, warnings: list[str]
) -> tuple[dict[str, object] | None, str | None]:
    """Run `--validate-pdf` against the file that was actually published.

    Deliberately after publication: the composed file — OCR layer, appendix,
    embedded files and all — is the only one whose conformance means anything
    (ADR-0010).  An absent validator is a warning at both levels, because a
    gate that cannot run has not found a problem; `strict` is about a file that
    was checked and failed.

    Returns the record and, under `strict`, the message the caller should fail
    with.  It reports rather than raises so that the failure can be raised once
    the `CaptureResult` exists: the file *was* published and veraPDF *did* run,
    so exit 7's QA report is the one report that can carry a full findings
    record, and a raise from in here would have thrown it away (docs/09 P5-2).
    """
    if not options.validate_pdf:
        return None, None
    report = verapdf_validate(
        options.output, report_path=options.output.with_suffix(".verapdf.json")
    )
    if report.compliant is None:
        warnings.append(f"PDF/A conformance was not established: {report.detail}")
    elif report.compliant:
        LOGGER.info("veraPDF: %s conformant (%s)", report.flavour, report.tool)
    else:
        summary = "; ".join(report.failures[:3]) or "see the veraPDF report"
        warnings.append(
            f"PDF/A conformance failed ({report.failed_rules} rule(s)): {summary}"
        )
        if options.validate_pdf == "strict":
            return asdict(report), (
                f"{options.output} is not {report.flavour} conformant: {summary}. "
                f"Full report: {report.report_file}"
            )
    return asdict(report), None


def _absorb_enrichment(
    result: AIBundleResult, enrichment: VideoEnrichment, warnings: list[str]
) -> None:
    """Fold the videos' contribution into the bundle's own counts and warnings.

    The manifest publishes one set of counts for the bundle, so the chunks the
    videos added have to be counted there rather than reported separately — a
    `chunks` figure that did not include them would contradict the file it
    describes.
    """
    result.chunk_count += enrichment.chunks
    result.videos = enrichment.records
    result.video_tally = enrichment.tally
    result.warnings.extend(enrichment.warnings)
    warnings.extend(enrichment.warnings)


@contextmanager
def _bundle_errors(destination: Path) -> Iterator[None]:
    """Report a filesystem refusal during bundling as spec §3 code 8.

    The extraction bridge already fails with `BundleBuildError` when docling
    cannot parse the snapshot; an unwritable bundle destination or a full disk
    is the same failure arriving from the filesystem instead of the parser, and
    the spec gives "bundle build/publish failure" its own code precisely so a
    caller can tell it from a generic environment problem.
    """
    try:
        yield
    except OSError as exc:
        raise BundleBuildError(
            f"The AI bundle at {destination} could not be built: {exc}"
        ) from exc


async def _require_ocr_or_fail(options: CaptureOptions, engine: OcrEngine) -> None:
    """`--require-ocr`: turn a missing recognizer into exit 6 (spec §1.1, §3).

    Asked before the browser opens, because whether an engine can run here is a
    property of the environment rather than of the page — the same reason
    `capture/visuals.py` asks it once per capture instead of once per asset.
    Failing here also means a caller who cannot use a capture without OCR does
    not pay for the capture first.

    The protected path is checked against Tesseract whatever `--ocr-engine`
    said, because that is the engine it will actually use: `--ocr-engine rapid`
    is warned-and-ignored there (docs/09 P3-7), and refusing a run over an
    engine it was never going to call would be a false failure.
    """
    # `require_ocr` implies `ocr`: the contradiction is refused on the options
    # model itself, so testing for it again here would read as if it were
    # reachable.
    if not options.require_ocr:
        return
    required = resolve_engine("tesseract") if options.protected_viewer else engine
    reason = await asyncio.to_thread(
        functools.partial(required.unavailable_reason, language=options.ocr_language)
    )
    if reason:
        raise OcrUnavailableError(
            f"{reason} --require-ocr was given, so missing recognition is a "
            "failure rather than a warning. Run `webshot doctor` for how to "
            "install it, or drop --require-ocr to capture without OCR."
        )


def _manifest_checksum(bundle_directory: Path) -> str | None:
    """SHA-256 of a published `manifest.json`, or None if there is no bundle.

    The manifest is the one file its own checksum table cannot cover, so the QA
    report carries it: a consumer that has both can verify the whole bundle
    (docs/04-spec.md §2.2, §5.3).
    """
    manifest = bundle_directory / "manifest.json"
    return sha256_file(manifest) if manifest.is_file() else None


#: The pipeline's stages, in the order they run. Declared rather than spelled
#: at each call site: `clock.mark("captur")` would otherwise ship a bogus key
#: into every QA report with nothing to catch it, and the golden harness masks
#: whatever keys it finds (docs/09 P4-9).
Stage = Literal[
    "acquire", "capture", "extract", "videos", "render", "package", "validate"
]


class _StageClock:
    """Wall-clock seconds per pipeline stage, for the QA report (docs/04 §2.3).

    The pipeline is a sequence, not a tree, so a stage is simply the time since
    the previous checkpoint.  A stage that a run never entered is never marked,
    which is why `--no-ai-bundle` reports no `extract` rather than reporting it
    as instant.
    """

    __slots__ = ("_elapsed", "_last")

    def __init__(self, started: float) -> None:
        self._last = started
        self._elapsed: dict[str, float] = {}

    def mark(self, stage: Stage) -> None:
        now = time.monotonic()
        self._elapsed[stage] = round(now - self._last, 3)
        self._last = now

    @property
    def timings(self) -> dict[str, float]:
        return dict(self._elapsed)


async def convert_url_to_pdf(options: CaptureOptions) -> CaptureResult:
    """Capture one source and atomically publish a validated PDF."""
    started = time.monotonic()
    clock = _StageClock(started)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    # Before anything is created: two runs aimed at one output publish into the
    # same PDF path and the same bundle directory, and the winner can be the
    # PDF from one and the bundle from the other (docs/04-spec.md §5.1).
    # Both destinations, not just the PDF: `--ai-bundle-dir` can point the
    # bundle somewhere unrelated to the output, and two runs writing different
    # PDFs into one shared bundle directory would take different locks and race
    # on the bundle anyway (docs/09 P5-10).
    with OutputLock(
        options.output,
        options.ai_bundle_directory or options.output.with_suffix(".ai"),
    ):
        return await _capture(options, started, clock)


async def capture_open_page(
    page: Page,
    options: CaptureOptions,
    *,
    clock: _StageClock,
    started: float,
    ocr_engine: OcrEngine,
    ai_final_directory: Path,
    source_kind: str,
    original_local_path: Path | None,
    http_status: int | None,
    content_type: str,
    failed_requests: RequestFailures,
    warnings: list[str],
) -> CaptureResult:
    """Capture a page that is already open and already showing what to capture.

    Everything from PREPARE to VALIDATE, and nothing about how the page got
    there: this function does not launch a browser, does not navigate, and does
    not close anything. `convert_url_to_pdf` supplies a page it opened with a
    URL; the journey walker supplies one it reached by clicking, because a
    module of a Continu journey **has no URL of its own** (docs/13) and so
    cannot be captured through the URL entry point at all.

    Re-launching per module was the alternative and is worse twice over: it is
    a browser start apiece across P9-5's low hundreds, and it discards the
    expansion state that is the one thing working in a walker's favour (P9-4).

    What the caller passes is what a page cannot answer for itself. `source_kind`
    and `original_local_path` come from the materialized source, which a clicked
    module does not have; `http_status` and `content_type` come from a navigation
    response, which it also does not have; and `failed_requests` accumulates in a
    handler the caller registered on the page before it opened what it captures.
    """
    temporary_output = options.output.with_name(
        f".{options.output.stem}.{os.getpid()}.tmp.pdf"
    )
    ai_staging_directory: Path | None = None
    ai_result: AIBundleResult | None = None
    enrichment = VideoEnrichment()
    try:
        if options.scroll:
            _, reached_bottom = await scroll_to_load(
                page, options.max_scrolls, options.scroll_delay_s
            )
            if not reached_bottom:
                warnings.append(
                    f"Lazy-load scrolling reached the {options.max_scrolls}-step safety limit."
                )

        warnings.extend(await _wait_for_fonts_and_images(page))
        title = (
            options.document_title
            or (await page.title()).strip()
            or "Untitled web page"
        )
        final_url = page.url if source_kind == "web" else options.source

        # One read of the post-JS DOM feeds both discovery questions
        # (docs/05 task 3.4), before anything is isolated or hidden: the
        # page's declared metadata and its main region are properties of
        # the page as it loaded, not of what capture kept.
        reading = await asyncio.to_thread(
            read_page, await page.content(), url=final_url
        )
        discovered = reading.metadata
        selection = await select_main_content(
            page, options.selector, reading.article, auto=options.auto_selector
        )
        if selection.strategy == "trafilatura":
            LOGGER.info("Trafilatura identified the main content region.")
        elif selection.strategy == "static-selectors":
            LOGGER.info(
                "Selected the main content region from the static selector list."
            )
        elif options.auto_selector and selection.strategy == "full-page":
            # Gated on what was *selected*, not on what was asked for.
            # `--selector` and `--auto-selector` together return the
            # explicit selection (strategy `explicit`), and this branch
            # still fired — so the QA report and the manifest said no
            # region was detected and the full page was used, of a capture
            # that had isolated exactly the region the caller named
            # (docs/09 P8-38).
            warnings.append(
                "No substantial main content region was detected; using the full page."
            )
        selector = selection.selector
        if selector:
            await isolate_content(page, selector)

        hidden = 0
        if options.mode == "clean":
            hidden += await hide_elements(page, NOISE_SELECTORS)
            hidden += await hide_obstructive_overlays(page)
        hidden += await hide_elements(page, options.exclude_selectors)
        if hidden:
            LOGGER.info("Removed %d non-document element(s).", hidden)

        # Before the CSS, because the CSS targets the marks this leaves.
        released = await release_clamped_text(page)
        if released:
            LOGGER.info(
                "Released %d element(s) whose CSS truncated their own text.",
                released,
            )

        await page.add_style_tag(content=BASE_PRINT_CSS)
        if options.mode == "clean":
            await page.add_style_tag(content=CLEAN_PRINT_CSS)
        if options.extra_css:
            await page.add_style_tag(
                content=options.extra_css.read_text(encoding="utf-8")
            )
        await page.emulate_media(media=options.media)
        # After every stylesheet and the media switch, because it measures
        # computed style; before the bundle, so the geometry the bundle
        # records is the layout that is printed (docs/09 P16-2).
        overridden = await suppress_ligatures(page)
        if overridden:
            LOGGER.info(
                "Turned off ligatures the page's own CSS re-enabled on %d element(s).",
                overridden,
            )
        # At the printed width, so after the print CSS and the media switch;
        # before the bundle, so the layout it records is the one printed
        # (docs/09 P16-11). Only a page with a table in a sideways scroller
        # pays for the probe print. A width that cannot be read releases
        # nothing, and the clipping check after the render says it could not
        # measure.
        if await has_scrolling_tables(page):
            width = await printed_width(page, options)
            wrapped = await release_wide_tables(page, width) if width else 0
            if wrapped:
                LOGGER.info(
                    "Let %d scrolling table(s) wrap to fit the printed page.", wrapped
                )
        # After the stylesheets, which size the frames, and after the release
        # of the boxes inside them (P16-17), which lengthens their documents;
        # before the bundle, so each frame's screenshot is of the frame the PDF
        # prints (docs/09 P16-16).
        fitted = await fit_frames(page, options)
        if fitted.released:
            LOGGER.info(
                "Grew %d frame(s) to their document's height for print.",
                fitted.released,
            )
        warnings.extend(fitted.warnings)
        clock.mark("capture")

        # How the bundle spells this machine's paths. Inactive, and so the
        # identity, unless `--local-paths relative` was asked for a local
        # source (docs/09 P14-61).
        local_paths = LocalPathRecorder(options.source, options.local_paths)
        if options.ai_bundle:
            ai_staging_directory = ai_final_directory.with_name(
                f".{ai_final_directory.name}.{os.getpid()}.staging"
            )
            if ai_staging_directory.exists():
                shutil.rmtree(ai_staging_directory)
            LOGGER.info("Building AI-ready content bundle...")
            # An OSError here is the bundle failing to be *built* — an
            # unwritable destination, a full disk — which spec §3 gives its
            # own code rather than leaving to the generic environment one.
            # The extraction bridge already fails this way when docling
            # cannot parse the snapshot; this is the same failure arriving
            # from the filesystem instead of from the parser.
            with _bundle_errors(ai_final_directory):
                ai_result = await create_ai_bundle(
                    page,
                    staging_directory=ai_staging_directory,
                    final_directory=ai_final_directory,
                    source=options.source,
                    title=title,
                    max_assets=options.max_assets,
                    ocr=options.ocr,
                    ocr_language=options.ocr_language,
                    ocr_psm=options.ocr_psm,
                    ocr_engine=ocr_engine,
                    require_ocr=options.require_ocr,
                    original_local_path=original_local_path,
                    auth_mode=options.auth_mode,
                    legacy_bundle=options.legacy_bundle,
                    prior_warnings=warnings,
                    local_paths=local_paths,
                )
            # The bundle's list is now the superset, so replace rather
            # than extend — otherwise the carried-in ones appear twice.
            warnings = list(ai_result.warnings)
            clock.mark("extract")

        if ai_result and options.videos:
            # After the bundle, because enrichment writes into its staging
            # directory and inlines each walkthrough into the `content.md`
            # the extraction stage just produced; before RENDER, because
            # the appendix illustrates every step with a screenshot that
            # has to be on disk by the time Chromium prints it.
            # Deliberately unannounced until something is found: a page
            # with no video is the common case, and a log line on every
            # capture would be noise — and would change the recorded
            # output of every golden that has nothing to do with video.
            with _bundle_errors(ai_final_directory):
                enrichment = await enrich_with_videos(
                    page,
                    staging=ai_result.staging_directory,
                    origin=options.guidde_origin,
                    download_clips=options.video_assets,
                    block_private_requests=options.block_private_requests,
                    chunk_start=ai_result.chunk_count,
                )
            _absorb_enrichment(ai_result, enrichment, warnings)
            if not enrichment.empty:
                LOGGER.info(
                    "Found %d video(s): %d documented as walkthroughs "
                    "(%d step(s)), %d captioned, %d not transcribed.",
                    enrichment.tally["total"],
                    enrichment.tally["documented_walkthroughs"],
                    sum(
                        len(bundle.playbook.steps) for bundle in enrichment.walkthroughs
                    ),
                    enrichment.tally["transcribed_media"],
                    enrichment.tally["untranscribed_media"],
                )
                clock.mark("videos")

        if ai_result:
            # After the videos, which count as content, and before RENDER: under
            # --require-content nothing has been published yet, so refusing here
            # leaves no PDF and no bundle behind (docs/04-spec.md §5 item 14).
            empty = empty_capture_message(
                page_text_characters=ai_result.page_text_characters,
                # Every visual the page showed, saved or not: a page whose
                # pictures were capped away or failed to save is not empty.
                visuals=len(ai_result.assets) + ai_result.unsaved_visuals,
                videos=len(ai_result.videos),
                required=options.require_content,
            )
            if empty and options.require_content:
                raise CaptureIntegrityError(empty)
            if empty:
                warnings.append(empty)
                ai_result.warnings.append(empty)

        if options.debug_screenshot:
            options.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(options.debug_screenshot), full_page=True)

        await render_pdf(
            page,
            options,
            temporary_output,
            title=title,
            final_url=final_url,
            # Chromium bakes `totalPages` in while printing and the
            # appendix is merged on afterwards, so a footer that names a
            # total would be naming a different document (docs/09 P8-68).
            appendix_expected=bool(enrichment.walkthroughs),
        )
        # Before embedding, which copies the warnings into `capture.json`, and
        # before the clipping check, whose narrower viewport can fetch images
        # the printed page never asked for.
        failed = failed_requests.close()
        if failed:
            warnings.append(failed)
            if ai_result:
                ai_result.warnings.append(failed)
        # After the render, so the check cannot move what it checks: it prints
        # the page a second time and narrows the viewport, and the PDF is
        # already written. Before embedding, which copies the warnings into
        # `capture.json` (docs/09 P16-1).
        for clipped in clipping_warnings(await find_clipped_text(page, options)):
            warnings.append(clipped)
            if ai_result:
                ai_result.warnings.append(clipped)
        if enrichment.walkthroughs:
            # Between printing and embedding: the appendix changes the
            # file's page count and its bytes, and both are measured below.
            # In a thread — it prints through a second Chromium and merges
            # with pypdf, and the capture's own browser is still open.
            if ai_result is None:  # pragma: no cover - guarded above
                raise BundleBuildError("Video enrichment ran without a bundle.")
            try:
                composed = await asyncio.to_thread(
                    append_video_appendix,
                    temporary_output,
                    ai_result.staging_directory,
                    enrichment.walkthroughs,
                    title=title,
                    generator_version=GENERATOR_VERSION,
                    paper_format=options.paper_format,
                    margin=options.margin,
                    # The capture's page box, not just its paper name: the
                    # appendix is bound into this file and claimed to match
                    # it, while receiving only `format` (docs/09 P8-29).
                    landscape=options.landscape,
                    prefer_css_page_size=options.prefer_css_page_size,
                    outline=options.outline,
                )
            except Exception as exc:
                # Everything else about enrichment degrades to a warning,
                # and this is the one step that could still fail a capture:
                # it prints through a second Chromium a sandboxed host may
                # refuse, and it merges a file whose own links it does not
                # control. The page and the bundle are already complete and
                # correct without the appendix (docs/09 P7-10).
                message = (
                    "The walkthrough appendix could not be appended to the "
                    f"PDF: {exc}. The captured page is unaffected, and the "
                    "walkthroughs are in the bundle."
                )
                LOGGER.warning(message)
                warnings.append(message)
                ai_result.warnings.append(message)
                composed = None
            if composed is not None:
                ai_result.video_appendix = {
                    "pages": composed.appendix_pages,
                    "starts_on_page": composed.capture_pages + 1,
                    "videos": composed.videos,
                    "steps": composed.steps,
                    # docs/09 S8: the merge does not carry a structure tree,
                    # so the appendix pages are untagged even when the
                    # capture's own are not. Said here rather than left to
                    # be inferred from `pdf.tagged`, which describes the
                    # file as a whole.
                    "tagged": False,
                }
                LOGGER.info(
                    "Appended %d walkthrough page(s) to the PDF.",
                    composed.appendix_pages,
                )
        # The composed file, appendix included, and before `finalize_ai_bundle`
        # writes the warnings into the manifest. `suppress_ligatures` is the
        # intent; this is the outcome, measured where a reader will look.
        forms = ligature_forms(temporary_output)
        if forms:
            # Not logged here as well: the CLI prints every warning once the
            # run ends, and the same sentence twice reads as two findings.
            message = ligature_warning(forms)
            warnings.append(message)
            if ai_result:
                ai_result.warnings.append(message)
        if ai_result and local_paths.active:
            # Last before embedding, which copies the warnings into
            # `capture.json`: every file the bundle publishes is written by now,
            # and these are the records the manifest adds to them.
            for message in local_paths.warnings(
                ai_result.staging_directory,
                (
                    ai_result.page_metadata,
                    asdict(discovered),
                    ai_result.videos,
                    ai_result.video_appendix,
                ),
            ):
                warnings.append(message)
                ai_result.warnings.append(message)
        # Measured once, before the attachments are added — embedding does
        # not touch the structure tree, and `finalize_ai_bundle` needs the
        # same answer the README is given or the two disagree again.
        pdf_is_tagged = is_tagged(temporary_output)
        embedded_files: list[str] = []
        if ai_result and options.embed_bundle:
            # Before `validate_pdf` and `finalize_ai_bundle`, both of which
            # measure the file: the manifest records the published PDF's
            # SHA-256, so a rewrite after it is written invalidates the
            # hash it just recorded (render/embed.py, module docstring).
            with _bundle_errors(ai_final_directory):
                embedded_files = embed_bundle(
                    temporary_output,
                    collect_payloads(
                        ai_result.staging_directory,
                        title=title,
                        source=local_paths.record(options.source),
                        provenance=embed_provenance(
                            ai_result,
                            source=local_paths.record(options.source),
                            final_url=local_paths.record(final_url),
                            title=title,
                            local_paths=local_paths.convention,
                        ),
                        include_assets=options.embed_assets,
                        videos=ai_result.videos,
                        # The measured state, not the request: the README
                        # and `manifest.pdf.tagged` are two provenance
                        # surfaces in one deliverable and they contradicted
                        # each other under --no-tagged-pdf (docs/09 P8-21).
                        tagged=pdf_is_tagged,
                        local_paths=local_paths.convention,
                    ),
                    title=title,
                    bundle_format=BUNDLE_FORMAT,
                )
        pages, size = validate_pdf(temporary_output)
        clock.mark("render")
        if ai_result:
            with _bundle_errors(ai_final_directory):
                finalize_ai_bundle(
                    ai_result,
                    pdf_path=temporary_output,
                    pdf_file_name=options.output.name,
                    pages=pages,
                    pdf_bytes=size,
                    source=local_paths.record(options.source),
                    final_url=local_paths.record(final_url),
                    local_paths=local_paths.convention,
                    title=title,
                    # What the file has, not what was asked for (docs/05 1.1).
                    tagged=pdf_is_tagged,
                    bookmarks=options.outline,
                    http_status=http_status,
                    content_type=content_type,
                    discovered=discovered,
                    selection=selection,
                    embedded_files=embedded_files,
                    videos=ai_result.videos,
                    video_tally=ai_result.video_tally,
                    video_appendix=ai_result.video_appendix,
                )
        pdf_backup = options.output.with_name(
            f".{options.output.name}.{os.getpid()}.backup"
        )
        pdf_backup.unlink(missing_ok=True)
        announce_replacements(options.output, ai_final_directory if ai_result else None)
        if options.output.exists():
            os.replace(options.output, pdf_backup)
        try:
            os.replace(temporary_output, options.output)
            if ai_result:
                publish_ai_bundle(ai_result)
                LOGGER.info("Created AI bundle at %s", ai_final_directory)
        except Exception as exc:
            if options.output.exists():
                os.replace(options.output, temporary_output)
            if pdf_backup.exists():
                os.replace(pdf_backup, options.output)
            # The swap itself is what failed, so the deliverables were
            # rolled back above and nothing was published: spec §3 code 8,
            # the same code the extraction bridge already fails with.
            published = (
                f"{options.output} and its bundle" if ai_result else str(options.output)
            )
            raise BundleBuildError(
                f"Publishing {published} failed and was rolled back: {exc}"
            ) from exc
        else:
            pdf_backup.unlink(missing_ok=True)
        clock.mark("package")
        LOGGER.info(
            "Created %s (%s, %.1f KB)", options.output, page_count(pages), size / 1024
        )
        # veraPDF is a subprocess with a long timeout, and the browser
        # context is still open above this line.
        validation, strict_failure = await asyncio.to_thread(
            _conformance_check, options, warnings
        )
        if options.validate_pdf:
            # Only when the gate actually ran: `timings` promises that a
            # stage a run never entered is absent, and a `validate: 0.0` on
            # every capture reads as "veraPDF ran and was fast" rather than
            # "veraPDF was never asked" (docs/09 P4-12).
            clock.mark("validate")

        result = CaptureResult(
            source=options.source,
            final_url=final_url,
            output=str(options.output),
            title=title,
            pages=pages,
            bytes=size,
            http_status=http_status,
            content_type=content_type,
            duration_seconds=round(time.monotonic() - started, 2),
            captured_at=datetime.now(UTC).isoformat(),
            content_selector=selector,
            source_kind=source_kind,
            ai_bundle=str(ai_final_directory) if ai_result else None,
            ai_summary={
                "items": ai_result.item_count,
                "chunks": ai_result.chunk_count,
                "tables": ai_result.table_count,
                "links": ai_result.link_count,
                "visual_assets": len(ai_result.assets),
                "text_characters": ai_result.text_characters,
                "ocr_words": ai_result.ocr_words,
            }
            if ai_result
            else {},
            failed_requests=list(failed_requests.listed),
            failed_request_count=failed_requests.count,
            warnings=warnings,
            validation=validation,
            timings=clock.timings,
            manifest_sha256=_manifest_checksum(ai_final_directory)
            if ai_result
            else None,
        )
        if strict_failure:
            raise PdfValidationError(strict_failure, result=result)
        return result
    finally:
        # Owned here because they are created here. The caller cleans up what it
        # created — the browser, the materialized source — and nothing else.
        temporary_output.unlink(missing_ok=True)
        if ai_staging_directory and ai_staging_directory.exists():
            shutil.rmtree(ai_staging_directory)


async def _capture(
    options: CaptureOptions, started: float, clock: _StageClock
) -> CaptureResult:
    """The capture itself, with the output lock already held."""
    failed_requests = RequestFailures()
    warnings: list[str] = []
    http_status: int | None = None
    content_type = ""
    # Before `materialize_source`, which creates a temporary directory whose
    # cleanup is owned by code further down: a gate that raises in between
    # leaks that directory until the interpreter garbage-collects it, which
    # defeats the explicit cleanup guard a few lines below (docs/09 P5-10).
    # It is also the right order on its own terms — refusing for a missing
    # recognizer should not first render a 40 MB DOCX.
    ocr_engine = resolve_engine(options.ocr_engine)
    await _require_ocr_or_fail(options, ocr_engine)
    try:
        materialized: MaterializedSource = materialize_source(options.source)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
        # The argument names something that is not there, or is not a file:
        # decided before anything runs, which is what spec §3 code 2 is.
        raise UsageError(str(exc)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise UsageError(str(exc)) from exc
    except OSError as exc:
        # Everything else the filesystem can say — a stale NFS handle, an I/O
        # error, a permission wall — is the environment, not the flags. Calling
        # it a usage error tells an operator their command line is wrong while
        # the mount is what is broken (docs/09 P5-10).
        raise EnvironmentFailure(str(exc)) from exc
    protected_capture_directory: Path | None = None
    ai_final_directory = options.ai_bundle_directory or options.output.with_suffix(
        ".ai"
    )
    if not options.ai_bundle and ai_final_directory.exists():
        warnings.append(
            f"An older AI bundle already exists at {ai_final_directory} and was not updated because --no-ai-bundle was used."
        )
    warnings.extend(ignored_option_warnings(options))
    clock.mark("acquire")

    async with async_playwright() as playwright:
        try:
            context, browser = await launch_context(playwright, options)
        except Exception:
            materialized.cleanup()
            raise
        page = context.pages[0] if context.pages else await context.new_page()

        page.on("requestfailed", failed_requests.record)

        try:
            LOGGER.info("Opening %s", options.source)
            try:
                response = await page.goto(
                    materialized.browser_url,
                    wait_until="domcontentloaded",
                    timeout=options.navigation_timeout_ms,
                )
            except PlaywrightTimeoutError as exc:
                raise NavigationError(
                    f"{options.source} did not load within "
                    f"{options.navigation_timeout_ms / 1000:g}s. Raise --timeout, "
                    "or use --wait-for to name what to wait for."
                ) from exc
            except PlaywrightError as exc:
                if not is_unreachable(exc):
                    raise
                raise NavigationError(
                    f"{options.source} could not be reached: {exc}"
                    f"{certificate_hint(str(exc))}"
                ) from exc
            # Chromium's spelling of what was asked for, which `page.url` is
            # compared with to tell a redirect to another origin (spec §5.4).
            requested = requested_url(response, materialized.browser_url)
            if response:
                http_status = response.status
                content_type = response.headers.get("content-type", "")
            if response and response.status >= 400 and not options.allow_http_errors:
                message = (
                    f"The server returned HTTP {response.status} for {options.source}. "
                    "Use --allow-http-errors only if that page is intentional."
                )
                # 401 and 403 are the far end saying who you are is the
                # problem, which is exactly what spec §3 code 4 is for; every
                # other refused status is the page never becoming available.
                if response.status in (401, 403):
                    raise AuthenticationError(
                        f"{message} A capture of a signed-in page needs "
                        "--storage-state or --auth-profile."
                    )
                raise NavigationError(message)

            if options.wait_for_selector:
                LOGGER.info("Waiting for %s", options.wait_for_selector)
                try:
                    await page.locator(options.wait_for_selector).first.wait_for(
                        state="visible"
                    )
                except PlaywrightTimeoutError as exc:
                    # A sign-in page has none of the page's elements, so an
                    # expired session under --wait-for times out here before
                    # the wall check below can run. Exit 3 would send the
                    # reader to the selector when the session is the problem.
                    # Not under --interactive-auth, whose wall is expected
                    # until the person signs in after this wait.
                    wall = (
                        await sign_in_wall(page, requested)
                        if materialized.kind == "web" and not options.interactive_auth
                        else WallCheck()
                    )
                    if wall.landed:
                        raise AuthenticationError(
                            sign_in_wall_message(
                                options.source, wall.landed, interactive=False
                            )
                        ) from exc
                    unread = f" {wall.unread}" if wall.unread else ""
                    raise NavigationError(
                        f"--wait-for {options.wait_for_selector!r} never became "
                        f"visible on {options.source}.{unread}"
                    ) from exc
            if options.wait_after_load_s:
                await asyncio.sleep(options.wait_after_load_s)

            if options.interactive_auth:
                if not sys.stdin.isatty():
                    raise UsageError(
                        "--interactive-auth requires an interactive terminal. Open the "
                        "headed browser, finish sign-in/MFA, then press Enter in that terminal."
                    )
                LOGGER.info(
                    "Complete sign-in and MFA in the browser. The session remains only "
                    "inside the private authentication profile."
                )
                await asyncio.to_thread(
                    input, "Press Enter after the protected document is visible... "
                )
                await page.wait_for_load_state("domcontentloaded")
                if options.wait_after_load_s:
                    await asyncio.sleep(options.wait_after_load_s)

            # After every wait and after interactive sign-in, so a redirect a
            # script makes once the page has loaded is seen, and so a person
            # who did sign in is not refused. Before both capture paths: a
            # sign-in page is not a page of the document either of them wants.
            if materialized.kind == "web":
                wall = await sign_in_wall(page, requested)
                if wall.landed:
                    raise AuthenticationError(
                        sign_in_wall_message(
                            options.source,
                            wall.landed,
                            interactive=options.interactive_auth,
                        )
                    )
                if wall.unread:
                    # A check that could not run has not passed (CLAUDE.md),
                    # so the manifest says so rather than nothing.
                    warnings.append(wall.unread)

            if options.protected_viewer:
                title = (
                    options.document_title
                    or (await page.title()).strip()
                    or "Protected SharePoint document"
                )
                final_url = page.url
                protected_capture_directory = Path(
                    tempfile.mkdtemp(prefix="webshot-protected-viewer-")
                )
                LOGGER.info(
                    "Capturing every page from the authorized document viewer..."
                )
                viewer_metadata = await capture_sharepoint_pdf_viewer(
                    page, protected_capture_directory
                )
                clock.mark("capture")
                # Every page is on disk, and the builder below writes the
                # manifest from `warnings`.
                failed = failed_requests.close()
                if failed:
                    warnings.append(failed)
                expected_pages = int(viewer_metadata["page_count"])
                LOGGER.info(
                    "Building searchable protected-viewer PDF and AI bundle (%s)...",
                    page_count(expected_pages),
                )
                # `build_protected_viewer_artifacts` runs OCR, builds the AI
                # bundle, renders the searchable PDF and publishes both — four
                # stages the web path reports separately and this one reported
                # as one `package` mark, with `extract` and `render` absent
                # from a run that plainly did both. It is one call, so the
                # marks come from inside it (docs/09 P8-41).
                manifest = await asyncio.to_thread(
                    build_protected_viewer_artifacts,
                    protected_capture_directory,
                    options.output,
                    source_url=options.source,
                    title=title,
                    expected_pages=expected_pages,
                    ocr_language=options.ocr_language,
                    ocr_psm=options.ocr_psm,
                    pdfa=options.pdfa,
                    capture_metadata={
                        "viewer": "Microsoft SharePoint PDF viewer",
                        "viewer_url": final_url,
                        "authentication": "persistent-private-browser-profile"
                        if options.auth_profile
                        else "Playwright-storage-state",
                        **viewer_metadata,
                    },
                    ai_directory=ai_final_directory,
                    # Into the manifest before it is written, not merged into
                    # the QA report afterwards: the bundle is published from
                    # inside that call, so anything added later reached the
                    # report and never the file (docs/09 P8-40).
                    extra_warnings=list(warnings),
                    on_stage=clock.mark,
                )
                pages, size = validate_pdf(options.output)
                clock.mark("package")
                LOGGER.info(
                    "Created %s (%s, %.1f MB)",
                    options.output,
                    page_count(pages),
                    size / (1024 * 1024),
                )
                content_summary = manifest["content"]
                # Seeded with the warnings raised before the branch, not just
                # the bundle's own: this path returns `protected_warnings` and
                # nothing else, so anything already in `warnings` would
                # otherwise be dropped from both the QA report and the log.
                # `manifest["warnings"]` already begins with `warnings`, which
                # is passed into the builder above; taking the manifest's list
                # whole keeps the QA report and the bundle saying exactly the
                # same thing in the same order, rather than the same thing
                # twice.
                protected_warnings = list(manifest["warnings"])
                validation, strict_failure = await asyncio.to_thread(
                    _conformance_check, options, protected_warnings
                )
                if options.validate_pdf:
                    clock.mark("validate")
                pdf_record = manifest["pdf"]
                protected_result = CaptureResult(
                    source=options.source,
                    final_url=final_url,
                    output=str(options.output),
                    title=title,
                    pages=pages,
                    bytes=size,
                    http_status=http_status,
                    content_type=content_type,
                    duration_seconds=round(time.monotonic() - started, 2),
                    captured_at=str(manifest["captured_at"]),
                    source_kind="protected-viewer",
                    ai_bundle=str(ai_final_directory),
                    ai_summary={
                        "pages": int(content_summary["pages"]),
                        "chunks": int(content_summary["chunks"]),
                        "text_characters": int(content_summary["text_characters"]),
                        "ocr_words": int(content_summary["ocr_words"]),
                    },
                    failed_requests=list(failed_requests.listed),
                    failed_request_count=failed_requests.count,
                    warnings=protected_warnings,
                    validation=validation,
                    timings=clock.timings,
                    manifest_sha256=_manifest_checksum(ai_final_directory),
                    page_count_invariant={
                        "source_pages": int(pdf_record["source_pages"]),
                        "appendix_pages": int(pdf_record["transcript_appendix_pages"]),
                        # `pages` from `validate_pdf` above — a fresh read of
                        # the published file — rather than the manifest's own
                        # `pdf.pages`, which is where `expected` already comes
                        # from. Deriving both sides from one record made the
                        # check compare a value to itself (docs/09 P4-12).
                        "actual": pages,
                    },
                )
                if strict_failure:
                    raise PdfValidationError(strict_failure, result=protected_result)
                return protected_result

            return await capture_open_page(
                page,
                options,
                clock=clock,
                started=started,
                ocr_engine=ocr_engine,
                ai_final_directory=ai_final_directory,
                source_kind=materialized.kind,
                original_local_path=materialized.original_path,
                http_status=http_status,
                content_type=content_type,
                failed_requests=failed_requests,
                warnings=warnings,
            )
        finally:
            await context.close()
            if browser:
                await browser.close()
            if protected_capture_directory and protected_capture_directory.exists():
                shutil.rmtree(protected_capture_directory)
            materialized.cleanup()
