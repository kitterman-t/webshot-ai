"""The `--report` QA report, schema v2 (docs/04-spec.md §2.3).

A capture is a claim about a page, and this file is the evidence for it: what
was asked for after every settings layer was reconciled, how long each stage
took, what was counted, which gates ran and what they said.  The contract models
live in `bundle/manifest.py` with every other published schema; this module is
only the construction, so `webshot schema qa-report` and the file `--report`
writes can never describe different things.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .bundle.manifest import (
    PageCountInvariant,
    PdfValidationRecord,
    QaReport,
    ReportCounts,
    ReportValidation,
    ResolvedOptions,
)
from .bundle.publish import write_json
from .config import CaptureOptions, CaptureResult

#: `CaptureOptions` fields the report deliberately does not publish, and why.
#: Declared rather than simply omitted so that "this was left out on purpose"
#: and "nobody remembered this one" stop looking the same — the report's job is
#: to say what the run was configured with, and a silent gap defeats it
#: (docs/09 P4-9). `tests/test_pdf_validation.py` fails when a new option is
#: neither published nor listed here.
UNPUBLISHED_OPTIONS: dict[str, str] = {
    "source": "published at the top level of the report",
    "output": "published at the top level of the report",
    "storage_state": "credential-bearing: reduced to `auth_mode`",
    "auth_profile": "credential-bearing: reduced to `auth_mode`",
    "interactive_auth": (
        "a terminal interaction, not a property of the capture; it cannot be "
        "true for any run that produced a report"
    ),
    "debug_screenshot": "a developer aid whose path says nothing about the capture",
    "operation_timeout_ms": "not settable; a constant of this WebShot build",
    "ocr_psm_explicit": (
        "bookkeeping for whether a warning is worth printing, not a setting"
    ),
    "filesystem_roots": (
        "the MCP server's own configuration, not the caller's: publishing the "
        "operator's directory layout into an artifact the caller reads back "
        "would describe the boundary to the party it constrains"
    ),
    "guidde_origin": (
        "a test seam, not a capture setting: every video's manifest record "
        "carries the URL its walkthrough was actually read from"
    ),
}


def resolved_options(options: CaptureOptions) -> ResolvedOptions:
    """Everything the run was configured with, minus the credential paths."""
    return ResolvedOptions(
        mode=options.mode,
        selector=options.selector,
        auto_selector=options.auto_selector,
        exclude=list(options.exclude_selectors),
        wait_for=options.wait_for_selector,
        delay_seconds=options.wait_after_load_s,
        navigation_timeout_seconds=options.navigation_timeout_ms / 1000,
        scroll=options.scroll,
        embed_bundle=options.embed_bundle,
        embed_assets=options.embed_assets,
        max_scrolls=options.max_scrolls,
        scroll_delay_seconds=options.scroll_delay_s,
        paper_format=options.paper_format,
        landscape=options.landscape,
        margin=options.margin,
        scale=options.scale,
        media=options.media,
        header_footer=options.header_footer,
        document_title=options.document_title,
        tagged=options.tagged,
        outline=options.outline,
        prefer_css_page_size=options.prefer_css_page_size,
        protected_viewer=options.protected_viewer,
        css=str(options.extra_css) if options.extra_css else None,
        user_agent=options.user_agent,
        allow_http_errors=options.allow_http_errors,
        block_private_requests=options.block_private_requests,
        ai_bundle=options.ai_bundle,
        videos=options.videos,
        video_assets=options.video_assets,
        ai_bundle_directory=str(options.ai_bundle_directory)
        if options.ai_bundle_directory
        else None,
        legacy_bundle=options.legacy_bundle,
        max_assets=options.max_assets,
        ocr=options.ocr,
        require_ocr=options.require_ocr,
        ocr_language=options.ocr_language,
        ocr_page_segmentation_mode=options.ocr_psm,
        ocr_engine=options.ocr_engine,
        pdfa=options.pdfa,
        validate_pdf=options.validate_pdf,
        auth_mode=options.auth_mode,
    )


def counts(result: CaptureResult) -> ReportCounts:
    """The spec §2.3 tally, read off whichever bundle this path produced.

    The one definition, used by the QA report and by the MCP `capture` result:
    two counts of a single capture must never be able to disagree.

    A number the capture path does not produce is absent rather than zero — the
    protected-viewer bundle has no links, tables, or document items, and a
    `--no-ai-bundle` run counted nothing at all. `ReportCounts` says what that
    means; `.get()` is what makes it true without a second copy of the rule.
    """
    summary = result.ai_summary
    return ReportCounts(
        # The protected path's `pages` is the *document's* page count, which is
        # what the bundle counts; `result.pages` is the composed PDF's and
        # includes the transcript appendix, so using it here made the report
        # contradict its own manifest (docs/09 P4-12). The PDF's own count is
        # published separately as `validation.pdf_pages`.
        pages=summary.get("pages", result.pages),
        items=summary.get("items"),
        tables=summary.get("tables"),
        links=summary.get("links"),
        chunks=summary.get("chunks"),
        # Page images are the protected path's visuals, and they are already
        # reported as `pages`; counting them twice would overstate the capture.
        visual_assets=summary.get("visual_assets"),
        ocr_words=summary.get("ocr_words"),
        text_characters=summary.get("text_characters"),
        failed_requests=result.failed_request_count,
    )


def validation(result: CaptureResult) -> ReportValidation:
    """Which gates ran on the published file, and what they found.

    `pdf_readable` is not a boolean WebShot could report as false: the pipeline
    refuses to publish a PDF pypdf cannot parse (docs/04-spec.md §2.2), so a
    report exists only for files that passed.  It is stated anyway because a QA
    artifact that silently omits a check it ran is the failure mode this schema
    exists to prevent.
    """
    invariant = None
    if result.page_count_invariant is not None:
        measured = result.page_count_invariant
        expected = measured["source_pages"] + measured["appendix_pages"]
        # `actual` is `validate_pdf`'s re-read of the *published* file, not the
        # manifest's own `pdf.pages`. The manifest derives its appendix count
        # as `total - source`, so `expected` and a manifest-sourced `actual`
        # are the same number by construction and the check could never fail —
        # the thing `assemble._verify`'s docstring warns about (docs/09 P4-12).
        actual = measured["actual"]
        invariant = PageCountInvariant(
            expected=expected,
            actual=actual,
            source_pages=measured["source_pages"],
            appendix_pages=measured["appendix_pages"],
            passed=expected == actual,
        )
    pdfa = (
        PdfValidationRecord.model_validate(result.validation)
        if result.validation is not None
        else None
    )
    return ReportValidation(
        pdf_readable=True,
        pdf_pages=result.pages,
        page_count_invariant=invariant,
        pdfa=pdfa,
    )


def build_qa_report(
    result: CaptureResult,
    options: CaptureOptions,
    *,
    exit_code: int = 0,
    error: str | None = None,
) -> QaReport:
    """Assemble the report for a capture that reached publication.

    `error` is not a contradiction of "reached publication": a
    `--validate-pdf=strict` run publishes the file and *then* fails it, so its
    report is a complete record of a capture that happened plus the reason its
    exit code is 7 (docs/05 task 5.1).
    """
    return QaReport(
        schema_version="2",
        source=result.source,
        final_url=result.final_url,
        output=result.output,
        title=result.title,
        pages=result.pages,
        bytes=result.bytes,
        http_status=result.http_status,
        content_type=result.content_type,
        duration_seconds=result.duration_seconds,
        timings=dict(result.timings),
        captured_at=result.captured_at,
        content_selector=result.content_selector,
        source_kind=result.source_kind,
        ai_bundle=result.ai_bundle,
        counts=counts(result),
        options=resolved_options(options),
        validation=validation(result),
        failed_requests=list(result.failed_requests),
        warnings=list(result.warnings),
        manifest_sha256=result.manifest_sha256,
        exit_code=exit_code,
        error=error,
    )


def build_failure_report(
    options: CaptureOptions | None,
    *,
    source: str,
    error: str,
    exit_code: int,
    duration_seconds: float,
) -> QaReport:
    """Assemble the report for a run that never reached publication.

    Everything a failed run genuinely knows, and null for everything else.  A
    failure this early has no PDF to describe, no counts to publish and no
    gates to report, so `counts` and `validation` are absent objects rather
    than objects full of zeros: spec §2.3's whole reason for choosing null over
    0 was that "measured none" and "never measured" must not look alike, and a
    capture that did not happen is the strongest case of that.

    `options` is null only when the failure was the options themselves — a
    `webshot.toml` that would not parse, or a combination the model refuses.
    """
    return QaReport(
        schema_version="2",
        source=source,
        final_url=None,
        output=str(options.output) if options else None,
        title=None,
        pages=None,
        bytes=None,
        http_status=None,
        content_type="",
        duration_seconds=duration_seconds,
        timings={},
        captured_at=datetime.now(UTC).isoformat(),
        content_selector=None,
        source_kind=None,
        ai_bundle=None,
        counts=None,
        options=resolved_options(options) if options else None,
        validation=None,
        failed_requests=[],
        warnings=[],
        manifest_sha256=None,
        exit_code=exit_code,
        error=error,
    )


def write_report(
    result: CaptureResult,
    options: CaptureOptions,
    destination: Path,
    *,
    exit_code: int = 0,
    error: str | None = None,
) -> None:
    """Write the QA report where `--report` asked for it.

    Through `write_json`, which is the one JSON spelling every WebShot artifact
    is written in — so a change to that spelling reaches the QA report too.
    """
    _write(
        destination, build_qa_report(result, options, exit_code=exit_code, error=error)
    )


def write_failure_report(
    destination: Path,
    options: CaptureOptions | None,
    *,
    source: str,
    error: str,
    exit_code: int,
    duration_seconds: float,
) -> None:
    """Write the QA report for a run that failed (docs/04-spec.md §3, §2.3)."""
    _write(
        destination,
        build_failure_report(
            options,
            source=source,
            error=error,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        ),
    )


def redact_credential_paths(message: str, *paths: Path | None) -> str:
    """Keep a session store's location out of the report's `error` string.

    spec §2.3 keeps `--storage-state` and `--auth-profile` out of `options` —
    "a QA report is a file people attach to tickets, and the location of a
    session store is not something it needs to carry". `error` arrived in
    Phase 5.1 as a raw `str(exc)` with no such gate, and at least one refusal
    interpolates the path directly (`Storage-state file does not exist: …`),
    which put it straight back into the artifact the rule exists to keep it out
    of (docs/09 P5-10).

    Applied here, where both report shapes converge, rather than at each raise
    site: a rule enforced per-message is one the next message forgets. The
    paths come from the *parsed command line* rather than from
    `CaptureOptions`, because the refusal that names a storage-state file is
    raised while building those options — so at that moment there is no
    `CaptureOptions` to read them from, which is exactly when the leak happens.
    """
    # Both spellings of each path: the options model resolves what the command
    # line gave it, so the message that leaks may carry either — and replacing
    # only one of them leaves a recognisable prefix of the other behind.
    spellings = sorted(
        {
            text
            for path in paths
            if path
            for text in (str(path), str(Path(path).expanduser().resolve()))
        },
        key=len,
        reverse=True,
    )
    for spelling in spellings:
        message = message.replace(spelling, "<redacted auth path>")
    return message


def _write(destination: Path, report: QaReport) -> None:
    """Both writers land here, so both produce the same JSON spelling.

    `model_dump` is already the whole rule — `QaReport` drops an absent `error`
    itself — so this adds only the directory and `write_json`, which is the one
    JSON spelling every WebShot artifact is written in.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, report.model_dump(mode="json"))
