"""The command line: parse flags, build options, run the pipeline, report.

`webshot SOURCE [options]` captures, and the words in `SUBCOMMANDS` (`doctor`,
`schema`, `mcp`, `journey`) dispatch to their own parsers.
docs/04-spec.md §1.1 pins every flag here to the documented compatibility
table, and a test fails if the two drift apart.

Imports here are deliberately shallow.  Only `.errors` and `.version` are
needed to build the parser's exit-code epilogue and answer `--version`;
everything heavier is imported by the function that uses it, so `--version`,
`--help` and `doctor` do not load the capture stack to do work that never
touches it (docs/09 P10-6).  `tests/test_footprint.py` names the modules that
must stay off that path.  The deferred imports sit *inside* the `try` blocks
that already classify failures, so a dependency that is missing at first use
still exits with a docs/04 §3 code rather than a traceback.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TextIO

from .errors import EXIT_CODES, UsageError, WebShotError, classify
from .version import VERSION

if TYPE_CHECKING:  # pragma: no cover - annotations only; imported where used
    from .config import CaptureOptions
    from .settings import WebshotConfig

LOGGER = logging.getLogger("webshot")


class _DefaultPsm(int):
    """11, and identifiable as the default rather than a request.

    `--ocr-psm` is Tesseract-specific and is ignored under `--ocr-engine
    rapid` (docs/04-spec.md §1.1).  Saying so is only useful if the user
    actually asked for a mode, and argparse cannot otherwise tell a passed
    value from its own default — a plain `!= 11` test would stay silent for
    `--ocr-psm 11`, which *was* a request.  argparse re-runs `type=int` on
    anything passed, so only the default keeps this class.
    """


DEFAULT_OCR_PSM = _DefaultPsm(11)


#: Upstream loggers whose warnings describe the library's own housekeeping,
#: which a user can do nothing about. Capped at ERROR, not silenced: a library
#: failing outright still reaches stderr, and WebShot's own findings travel in
#: the manifest's `warnings`, not through these loggers.
#:
#: * `pypdf` reports repairs it made while reading a PDF, such as "Object count
#:   ... exceeds defined trailer size". Every PDF it reads here is one WebShot
#:   produced (Chromium's print, the walkthrough appendix, OCRmyPDF's output);
#:   no local input format is a PDF.
#: * `docling.backend.html_backend` reports "Clashing hyperlinks" when a heading
#:   or text run holds two links and its item can carry one. The bundle's
#:   `links.json` is read from the page itself and keeps every link.
#: * `fontTools` narrates each table it prunes ("head pruned", "glyf pruned")
#:   at INFO while OCRmyPDF subsets the text-layer font on the protected path.
#:   OCRmyPDF's own command line sets this logger to ERROR; WebShot calls it as
#:   a library, which skips that setup.
QUIETED_UPSTREAM_LOGGERS = ("pypdf", "docling.backend.html_backend", "fontTools")


def setup_logging(verbose: bool = False, *, stream: TextIO | None = None) -> None:
    """One log format for every entry point.

    `stream` exists for `webshot mcp`, where stdout *is* the protocol and a
    stray log line corrupts the JSON-RPC stream.

    `--verbose` lowers WebShot's own loggers to DEBUG and leaves every other
    one at INFO. Lowering the root logger instead put each library's debug
    narration on stderr: a new user's capture of a real page printed
    hundreds of trafilatura and htmldate lines, and even the article fixture
    in `tests/fixtures/` prints 50 lines from docling, trafilatura and
    htmldate around WebShot's 9. A WebShot record still reaches the root
    handler at DEBUG, because propagation is decided by the handler's level,
    not by the parent logger's.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=stream,
    )
    # Only ever lowered, never reset: without `--verbose` the logger inherits
    # INFO from the root as it always did, and a level someone else set on it
    # (pytest's `caplog.at_level`, around a call to `main`) is left alone.
    if verbose:
        logging.getLogger("webshot").setLevel(logging.DEBUG)
    for name in QUIETED_UPSTREAM_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    # pikepdf logs "C++ to Python logger bridge initialized" at INFO the moment
    # it is imported. WebShot never calls it directly — it arrives under
    # OCRmyPDF — and the line describes that library starting up, not anything
    # about the capture.
    #
    # It was invisible before docs/09 P10-6 by accident, not by design: the
    # import used to happen while `webshot.cli` was being imported, which is
    # before this function has run, so the record met no handler and the
    # last-resort one drops anything below WARNING. Deferring the import moved
    # it to *after* `basicConfig`, and 21 of the 23 golden cases gained a line
    # that says nothing about the page they captured. Silencing it here keeps
    # the deferral a load-time change, which is what it was supposed to be.
    #
    # WARNING rather than silence: pikepdf complaining about a malformed PDF is
    # exactly what a capture's warnings exist to carry.
    logging.getLogger("pikepdf").setLevel(logging.WARNING)


#: One line per subcommand for `--help`, which otherwise lists only the
#: capture's flags: `doctor`, the first thing a new user is told to run, was
#: not mentioned there at all. Keyed like `SUBCOMMANDS`, and a test holds the
#: two to the same names.
SUBCOMMAND_HELP: dict[str, str] = {
    "doctor": "check that this machine can capture, recognize, and publish",
    "schema": "print a published JSON Schema, or list them",
    "mcp": "serve WebShot's tools to an MCP client over stdio",
    "journey": "walk a Continu training journey and capture its modules",
}

#: `--help`'s closing section, generated from the taxonomy rather than typed
#: out beside it: docs/04-spec.md §3 is a MUST, and a hand-written copy is how
#: a table stops matching the codes the process actually returns.
EXIT_CODE_EPILOGUE = "exit codes:\n" + "\n".join(
    f"  {code}  {meaning}" for code, meaning in sorted(EXIT_CODES.items())
)

SUBCOMMAND_EPILOGUE = (
    "subcommands:\n"
    + "\n".join(
        f"  webshot {name:<9} {summary}" for name, summary in SUBCOMMAND_HELP.items()
    )
    + "\n  Run webshot COMMAND --help for a subcommand's own options."
)


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    """Defaults on every option, and an epilogue that keeps its own line breaks.

    `ArgumentDefaultsHelpFormatter` alone inherits `_fill_text`, which collapses
    whitespace and re-wraps — so the generated exit-code table arrived in
    `--help` as one run-on paragraph, which is precisely what generating it was
    meant to avoid (docs/09 P5-10).
    """

    def _get_help_string(self, action: argparse.Action) -> str | None:
        # A `None` default is argparse's "not given", not a value: printing it
        # told users `--output` defaults to None when it defaults to a path the
        # code computes, and `--ai-bundle-dir`, whose help states its computed
        # default, showed a second, contradicting one.
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


def build_parser() -> argparse.ArgumentParser:
    # `--help` needs the flag table and nothing else. Reading these two from
    # `.config` here rather than at module scope is what keeps Playwright off
    # the help path once `.config` stopped importing it (docs/09 P10-6).
    from .config import PAPER_FORMATS, validate_css_length

    parser = argparse.ArgumentParser(
        description="Convert web pages and local documents into polished PDFs and AI-ready data bundles.",
        formatter_class=_HelpFormatter,
        epilog=f"{SUBCOMMAND_EPILOGUE}\n\n{EXIT_CODE_EPILOGUE}",
    )
    parser.add_argument(
        "source",
        help=(
            "HTTP(S) URL, or a local HTML, Markdown, JSON/JSONL, CSV/TSV, text, "
            "XML/YAML, EPUB, ZIP, DOCX/PPTX/XLSX (office extra), or image file"
        ),
    )
    parser.add_argument("--version", action="version", version=f"WebShot {VERSION}")
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "output PDF path; a file already there is replaced (default: "
            "output/pdf/NAME.pdf under the current directory, with NAME made "
            "from the web address or the file name)"
        ),
    )
    parser.add_argument("-s", "--selector", help="CSS selector for the content to keep")
    parser.add_argument(
        "--auto-selector",
        action="store_true",
        help="automatically isolate the most substantial semantic content region",
    )
    parser.add_argument(
        "--mode",
        choices=("clean", "faithful"),
        default="clean",
        help="clean removes common page chrome; faithful preserves the page structure",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="SELECTOR",
        help="CSS selector to omit (repeatable)",
    )
    parser.add_argument(
        "--wait-for", metavar="SELECTOR", help="wait for an element before capture"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds to wait after initial load"
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="navigation timeout in seconds"
    )
    parser.add_argument(
        "--no-scroll", action="store_true", help="skip lazy-loading scroll pass"
    )
    parser.add_argument(
        "--max-scrolls", type=int, default=100, help="lazy-load scroll safety limit"
    )
    parser.add_argument(
        "--scroll-delay", type=float, default=0.25, help="seconds between scrolls"
    )
    parser.add_argument(
        "--format",
        choices=PAPER_FORMATS,
        default="Letter",
        dest="paper_format",
        help="paper size",
    )
    parser.add_argument(
        "--landscape", action="store_true", help="use landscape orientation"
    )
    parser.add_argument(
        "--margin", type=validate_css_length, default="0.6in", help="page margin"
    )
    parser.add_argument(
        "--scale", type=float, default=0.9, help="rendering scale from 0.1 to 2.0"
    )
    parser.add_argument(
        "--media",
        choices=("print", "screen"),
        default="print",
        help=(
            "CSS media type the page is laid out with: print applies the "
            "site's print styles, screen keeps the styles shown in a browser "
            "window"
        ),
    )
    parser.add_argument(
        "--no-header-footer",
        action="store_true",
        help="omit title, source, and page numbers",
    )
    parser.add_argument(
        "--title", help="override the document title shown in the header"
    )
    parser.add_argument(
        "--no-tagged-pdf",
        action="store_true",
        help="disable accessible PDF structure tags",
    )
    parser.add_argument(
        "--no-outline",
        action="store_true",
        help="disable PDF bookmarks generated from headings",
    )
    parser.add_argument(
        "--prefer-css-page-size",
        action="store_true",
        help="let the page's own @page size override --format",
    )
    parser.add_argument(
        "--storage-state", type=Path, help="Playwright auth storage-state JSON"
    )
    parser.add_argument(
        "--auth-profile",
        type=Path,
        help=(
            "private persistent browser profile for protected sites; keeps sign-in "
            "local without exporting cookies to JSON"
        ),
    )
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help=(
            "open a headed browser and pause while you complete sign-in/MFA; requires "
            "--auth-profile"
        ),
    )
    parser.add_argument(
        "--protected-viewer",
        action="store_true",
        help=(
            "capture every page exposed by an authenticated SharePoint PDF viewer, "
            "then build a searchable image-backed PDF and page-level AI bundle"
        ),
    )
    parser.add_argument("--css", type=Path, help="additional UTF-8 CSS file to inject")
    parser.add_argument("--user-agent", help="override Chromium's native user-agent")
    parser.add_argument(
        "--allow-http-errors",
        action="store_true",
        help=(
            "capture the page even when the server answers with an HTTP error "
            "status (400 or above); without it such an answer fails the run "
            "with exit 3, or exit 4 for 401 and 403"
        ),
    )
    parser.add_argument(
        "--require-content",
        action="store_true",
        help=(
            "fail with exit 5, publishing nothing, when the capture holds no "
            "image, no video and fewer than 50 characters of text, instead of "
            "warning about it; needs the AI bundle, whose counts it reads"
        ),
    )
    parser.add_argument(
        "--debug-screenshot", type=Path, help="save the prepared full page as a PNG"
    )
    parser.add_argument(
        "--no-ai-bundle",
        action="store_true",
        help="only create the PDF; omit semantic text, chunks, tables, assets, and provenance",
    )
    parser.add_argument(
        "--no-embed-bundle",
        action="store_true",
        help=(
            "do not carry the bundle inside the PDF; the PDF stays a rendering "
            "and the bundle stays a separate directory"
        ),
    )
    parser.add_argument(
        "--embed-assets",
        action="store_true",
        help=(
            "also embed the binary visual assets in the PDF (larger file; the "
            "images are already visible in the rendered pages)"
        ),
    )
    parser.add_argument(
        "--local-paths",
        choices=("absolute", "relative"),
        default="absolute",
        help=(
            "how the bundle records a local source and the local files its page "
            "used: absolute file:// URLs (default), or relative to the source "
            "file's directory, so the bundle does not carry this machine's folder "
            "layout; the PDF's own link annotations and the QA report are not "
            "changed"
        ),
    )
    parser.add_argument(
        "--legacy-bundle",
        action="store_true",
        help=(
            "also emit v2-format content.json and chunks.jsonl under legacy/ inside "
            "the bundle (listed in the manifest with legacy: true); ships in v3.0, "
            "removed in v3.1 — see docs/migration-v2-to-v3.md"
        ),
    )
    parser.add_argument(
        "--ai-bundle-dir",
        type=Path,
        help="AI bundle directory (default: output path with an .ai suffix)",
    )
    parser.add_argument(
        "--no-videos",
        action="store_true",
        help=(
            "do not read the videos embedded in the page: no walkthrough "
            "steps, caption transcripts, or record of untranscribed media in "
            "the bundle, and no walkthrough pages appended to the PDF"
        ),
    )
    parser.add_argument(
        "--video-assets",
        action="store_true",
        help=(
            "also download each embedded video's step clips and source "
            "recording into the bundle (off by default: an agent cannot watch "
            "them, and the stills and narration already carry the procedure)"
        ),
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help=(
            "save visual assets without running text recognition on them, "
            "with either engine (Tesseract or RapidOCR); a --protected-viewer "
            "capture still recognizes its pages, because its searchable PDF "
            "is built from that text"
        ),
    )
    parser.add_argument(
        "--require-ocr",
        action="store_true",
        help=(
            "fail with exit 6 when no OCR engine is available, instead of "
            "capturing without recognized text and warning about it"
        ),
    )
    parser.add_argument(
        "--ocr-language",
        default="eng",
        help="Tesseract language or language combination, such as eng or eng+spa",
    )
    parser.add_argument(
        "--ocr-psm",
        type=int,
        choices=(3, 6, 11, 12),
        default=DEFAULT_OCR_PSM,
        help="Tesseract page segmentation mode; 11 works well for mixed visual assets",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=("tesseract", "rapid"),
        default="tesseract",
        help=(
            "text recognition engine; rapid is RapidOCR, which needs no system "
            "binary: install it from your checkout with uv sync --extra "
            "ocr-rapid (name any other extras you use too)"
        ),
    )
    parser.add_argument(
        "--max-assets",
        type=int,
        default=50,
        help="maximum visible images, charts, canvases, SVGs, and iframes to preserve",
    )
    parser.add_argument(
        "--pdfa",
        action="store_true",
        help=(
            "convert the PDF to PDF/A-3 for archiving; protected-viewer captures "
            "only, because the conversion strips the web path's accessibility tags"
        ),
    )
    parser.add_argument(
        "--validate-pdf",
        nargs="?",
        const="report",
        choices=("report", "strict"),
        help=(
            "check PDF/A conformance of the published file with veraPDF; strict "
            "turns a non-conformant file into exit 7 (an absent validator stays a "
            "warning either way)"
        ),
    )
    parser.add_argument(
        "--report", type=Path, help="write capture details and warnings as JSON"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "TOML settings file; flags override it and WEBSHOT_* overrides it. "
            "Only an explicitly named file is read — WebShot never picks up a "
            "webshot.toml from the working directory"
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every stage as it runs"
    )
    return parser


def provided_dests(argv: Sequence[str]) -> set[str]:
    """Which options this command line actually named.

    Precedence needs the difference between "the user asked for `--scale 0.9`"
    and "argparse filled in its own 0.9", and a parsed namespace cannot tell
    them apart.  Re-parsing against a parser whose defaults are all `None` can:
    anything left `None` was never on the command line.  (`append` actions are
    safe here — argparse turns a `None` default into a fresh list.)
    """
    detector = build_parser()
    for action in detector._actions:
        if action.dest not in {"help", "version"}:
            action.default = None
    namespace = detector.parse_args(list(argv))
    return {dest for dest, value in vars(namespace).items() if value is not None}


def apply_settings(
    args: argparse.Namespace,
    argv: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> WebshotConfig:
    """Fold `webshot.toml` and `WEBSHOT_*` into an already-parsed namespace.

    docs/04-spec.md §1.1: flags > env > file > defaults.  The first of those is
    enforced here by skipping every option the command line named; the middle
    two are ordered inside `settings.load_config`.  Everything then flows
    through `options_from_args`, so a file-supplied value meets the same
    validation a flag does.
    """
    from .settings import load_config, option_overrides

    config = load_config(args.config, environ)
    named = provided_dests(argv)
    overrides = {
        dest: value
        for dest, value in option_overrides(config).items()
        if dest not in named
    }
    # `--no-ocr` and `[ocr] require` are the first pair where a *file* value can
    # make a *flag* impossible rather than merely losing to it: the flag names
    # `no_ocr`, the setting lands on `require_ocr`, and `CaptureOptions` then
    # refuses the combination — so the file wins by making the command line
    # unusable, which inverts spec §1.1's flags > env > file (docs/09 P5-10).
    # Precedence means the flag decides; a setting it contradicts is dropped.
    if "no_ocr" in named and args.no_ocr:
        overrides.pop("require_ocr", None)
    # The symmetric case, and it was missing. A lower-priority layer setting
    # `[ocr] enabled = false` lands on `no_ocr`, and an explicit
    # `--require-ocr` then meets it in `CaptureOptions` as a contradiction —
    # so the file made the flag unusable, which is the same inversion of
    # flags > env > file read from the other side (docs/09 P8-66). One rule,
    # two directions: whichever of the pair the command line named wins, and
    # the setting it contradicts is dropped.
    if "require_ocr" in named and args.require_ocr:
        overrides.pop("no_ocr", None)
    # The same pair again, for the content check and the bundle it reads.
    if "no_ai_bundle" in named and args.no_ai_bundle:
        overrides.pop("require_content", None)
    if "require_content" in named and args.require_content:
        overrides.pop("no_ai_bundle", None)
    for dest, value in overrides.items():
        setattr(args, dest, value)
    return config


def _same_path(left: Path, right: Path) -> bool:
    """Whether two paths name the same file, before either one exists.

    `Path.samefile` needs both to exist and the report never does yet, so the
    comparison is on resolved paths. `strict=False` keeps a parent that has
    not been created from raising.
    """
    try:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(
            strict=False
        )
    except OSError:
        return False


def _refuse_report_collision(args: argparse.Namespace, output: Path) -> None:
    """A `--report` that names an artifact of the same run destroys it.

    The QA report is written *after* the capture is published — deliberately,
    so a report that cannot be written does not fail a capture sitting on disk
    — which means `--report` pointing at the output PDF replaced the finished
    file with JSON, and the command exited 0 reporting success for a run whose
    only deliverable it had just overwritten (docs/09 P8-53).

    Here rather than on `CaptureOptions`: the report path is not a capture
    option, and the other caller of that model cannot choose either path
    (spec §6.8b), so this is the CLI's rule to keep. Refused rather than
    renamed — which of the two the caller meant is not something to guess.
    """
    report = getattr(args, "report", None)
    if report is None:
        return
    bundle = getattr(args, "ai_bundle_dir", None) or output.with_suffix(".ai")
    for what, other in (("the output PDF", output), ("the AI bundle", bundle)):
        if _same_path(report, other):
            raise UsageError(
                f"--report names {what} ({other}). The report is written after "
                "the capture is published, so it would overwrite the artifact "
                "this run just produced."
            )


def options_from_args(args: argparse.Namespace) -> CaptureOptions:
    """Translate a parsed command line into the options model.

    Translation only: every rule about whether the options make *sense* lives
    on `CaptureOptions` itself, so the MCP server gets the same refusals
    without going through argparse (docs/09 P4-8).
    """
    from .acquire.router import normalize_source, resolve_output
    from .config import CaptureOptions

    source = normalize_source(args.source)
    output = resolve_output(source, args.output)
    _refuse_report_collision(args, output)
    return CaptureOptions(
        source=source,
        output=output,
        selector=args.selector,
        auto_selector=args.auto_selector,
        mode=args.mode,
        exclude_selectors=args.exclude,
        wait_for_selector=args.wait_for,
        wait_after_load_s=args.delay,
        navigation_timeout_ms=round(args.timeout * 1000),
        scroll=not args.no_scroll,
        max_scrolls=args.max_scrolls,
        scroll_delay_s=args.scroll_delay,
        paper_format=args.paper_format,
        landscape=args.landscape,
        margin=args.margin,
        scale=args.scale,
        media=args.media,
        header_footer=not args.no_header_footer,
        document_title=args.title,
        tagged=not args.no_tagged_pdf,
        outline=not args.no_outline,
        prefer_css_page_size=args.prefer_css_page_size,
        storage_state=args.storage_state.resolve() if args.storage_state else None,
        auth_profile=args.auth_profile.expanduser().resolve()
        if args.auth_profile
        else None,
        interactive_auth=args.interactive_auth,
        protected_viewer=args.protected_viewer,
        extra_css=args.css.resolve() if args.css else None,
        user_agent=args.user_agent,
        allow_http_errors=args.allow_http_errors,
        require_content=args.require_content,
        debug_screenshot=args.debug_screenshot.resolve()
        if args.debug_screenshot
        else None,
        ai_bundle=not args.no_ai_bundle,
        embed_bundle=not args.no_embed_bundle,
        embed_assets=args.embed_assets,
        local_paths=args.local_paths,
        legacy_bundle=args.legacy_bundle,
        ai_bundle_directory=args.ai_bundle_dir.resolve()
        if args.ai_bundle_dir
        else output.with_suffix(".ai"),
        videos=not args.no_videos,
        video_assets=args.video_assets,
        ocr=not args.no_ocr,
        require_ocr=args.require_ocr,
        ocr_language=args.ocr_language,
        ocr_psm=int(args.ocr_psm),
        ocr_engine=args.ocr_engine,
        ocr_psm_explicit=not isinstance(args.ocr_psm, _DefaultPsm),
        max_assets=args.max_assets,
        pdfa=args.pdfa,
        validate_pdf=args.validate_pdf,
    )


def _record_failure(
    destination: Path | None,
    options: CaptureOptions | None,
    source: str,
    exc: BaseException,
    exit_code: int,
    started: float,
    credential_paths: Sequence[Path | None] = (),
    output: Path | None = None,
) -> None:
    """Write the QA report for a run that failed (docs/05 task 5.1).

    Before Phase 5 a failing run wrote no report at all, so `exit_code` in a
    QA report was 0 by construction and the field said nothing (spec §2.3 says
    as much, and this is the task that fixes it).  A report for a failure is
    the more useful of the two: it is what a caller attaches to the ticket
    about the capture that did not happen.

    Two shapes, one rule.  A failure that happened *after* publication — today
    only `--validate-pdf=strict` — carried its `CaptureResult` out on the
    exception, so its report is everything a successful run would have said
    plus the verdict that failed it.  Every other failure reports what it
    genuinely knew and null for the rest.  The choice lives here rather than at
    the call site so that both shapes go through the same `try`: writing a
    report must never replace the failure with a different one, and that rule
    would not have covered an arm written beside it.
    """
    if destination is None:
        return
    # Never over an artifact. `options_from_args` refuses a `--report` that
    # names the output, but the refusal is itself a failure that lands here —
    # and writing the explanation over the file it was protecting would be a
    # perfect circle (docs/09 P8-53). Also covers the case where the run
    # failed *after* publication for some other reason.
    # `options.output` when the model was built, `args.output` when building it
    # is what failed — which is precisely the case here, because refusing the
    # collision happens *inside* `options_from_args`. Reading only the model
    # left `options` as None on the one path this guard exists for.
    published = options.output if options is not None else output
    if published is not None and _same_path(destination, published):
        LOGGER.warning(
            "Not writing the capture report to %s: it is this run's output "
            "path, and the report would overwrite it.",
            destination,
        )
        return
    result = getattr(exc, "result", None)
    try:
        # Inside the `try`, not above it. `.report` carries the QA report's
        # whole model stack, and an import that fails here has to degrade to
        # the warning below like any other failure to write — never replace
        # the failure it was called to record, which is the rule this
        # function's docstring already states (docs/09 P10-6).
        from .report import (
            redact_credential_paths,
            write_failure_report,
            write_report,
        )

        message = redact_credential_paths(
            str(exc) or exc.__class__.__name__, *credential_paths
        )
        if result is not None and options is not None:
            write_report(
                result, options, destination, exit_code=exit_code, error=message
            )
        else:
            write_failure_report(
                destination,
                options,
                # The resolved source when there is one, and the raw argument
                # when the failure was reaching that resolution: a report whose
                # `source` is empty because normalization is what failed would
                # be describing the wrong thing.
                source=options.source if options else source,
                error=message,
                exit_code=exit_code,
                duration_seconds=round(time.monotonic() - started, 2),
            )
    except (OSError, ImportError) as report_exc:  # pragma: no cover
        LOGGER.warning("Could not write the capture report: %s", report_exc)
    else:
        LOGGER.info("Wrote capture report to %s", destination.resolve())


def _report_destination(arguments: Sequence[str]) -> Path | None:
    """`--report`'s value, read off raw argv before argparse has run.

    Needed because argparse's own errors are raised *before* the parsed
    namespace exists, and the failure-report contract covers them too — see
    `_parse_or_report`. Deliberately forgiving: this is a best-effort read for
    the purpose of writing a diagnostic, and a spelling it does not recognise
    simply means no report, which is where this started.
    """
    for index, argument in enumerate(arguments):
        if argument == "--report" and index + 1 < len(arguments):
            return Path(arguments[index + 1])
        if argument.startswith("--report="):
            return Path(argument.split("=", 1)[1])
    return None


def _parse_or_report(
    parser: argparse.ArgumentParser, arguments: Sequence[str], started: float
) -> argparse.Namespace:
    """Parse, and write the QA report if argparse refuses.

    docs/04-spec.md §2.3: a run given `--report` records its exit code and its
    error, whether it succeeded or failed. `parse_args` raises `SystemExit(2)`
    from inside itself, before the namespace exists and therefore before the
    failure-report boundary below could be established — so `--scale nope`,
    an unknown option, or a missing source exited 2 and wrote nothing, which
    is the one shape of failure an automated caller is most likely to hit
    (docs/09 P8-73).

    argparse has already printed its own message to stderr by the time it
    raises; `parser.error` is overridden on the copy used here so the text is
    available to put in the report as well. The `SystemExit` is re-raised
    untouched: argparse's exit code is argparse's to choose.
    """
    captured: list[str] = []

    class _Reporting(type(parser)):  # type: ignore[misc]
        def error(self, message: str) -> NoReturn:
            captured.append(message)
            super().error(message)
            raise AssertionError("unreachable: argparse.error never returns")

    parser.__class__ = _Reporting
    try:
        return parser.parse_args(list(arguments))
    except SystemExit as exit_signal:
        code = int(exit_signal.code or 0)
        if code:
            _record_failure(
                _report_destination(arguments),
                None,
                # No normalized source yet; the raw argument is what was asked
                # for, and an empty one is honest when none was given.
                next((a for a in arguments if not a.startswith("-")), ""),
                UsageError(captured[-1] if captured else "invalid command line"),
                code,
                started,
            )
        raise


async def async_main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    started = time.monotonic()
    args = _parse_or_report(parser, arguments, started)
    setup_logging(args.verbose)
    options: CaptureOptions | None = None
    try:
        apply_settings(args, arguments)
        options = options_from_args(args)
        # The capture stack, loaded at the one moment a capture is certain to
        # follow. Inside the `try` so that a missing or unloadable dependency
        # becomes a §3 exit code and a QA report, exactly like any other
        # failure to capture (docs/09 P10-6).
        from .pipeline import convert_url_to_pdf

        result = await convert_url_to_pdf(options)
    except Exception as exc:
        # `Exception`, not a list of the types someone remembered. docs/04-spec.md
        # §3 says v3 never exits 1, and a hand-maintained tuple makes that true
        # only for the exceptions in it: a `ValueError` out of a bridge, a pypdf
        # parser type, a `subprocess.SubprocessError` from Tesseract — each
        # escaped as a traceback and Python's own exit 1, which is the outcome
        # this whole taxonomy exists to abolish (docs/09 P5-9). Knowing which
        # exception means which code is exactly what `classify` was written to
        # own, so the caller does not keep a second copy of that knowledge.
        #
        # `KeyboardInterrupt` and `SystemExit` derive from `BaseException` and
        # are deliberately not caught here: 130 is `main`'s answer to the first,
        # and the second is argparse on its way out.
        # The type as well as the message: `str(KeyError("x"))` is just `"'x'"`,
        # and an operator who hits a genuine WebShot bug needs to know what
        # escaped. `--verbose` adds the traceback, which a `WebShotError` — a
        # deliberate, explained refusal — does not need (docs/09 P5-10).
        LOGGER.error(
            "%s: %s",
            type(exc).__name__,
            exc,
            exc_info=args.verbose and not isinstance(exc, WebShotError),
        )
        code = classify(exc)
        _record_failure(
            args.report,
            options,
            args.source,
            exc,
            code,
            started,
            (args.storage_state, args.auth_profile),
            # `--output` is an unconverted string on the namespace.
            output=Path(args.output) if args.output else None,
        )
        return code
    # Deliberately outside the `try`: by here the capture is published and
    # valid, and a `--report` that cannot be written must not turn it into a
    # failed run — that reported exit 9 for a capture sitting on disk
    # (docs/09 P5-10).
    if args.report:
        try:
            from .report import write_report

            write_report(result, options, args.report)
        except Exception as exc:  # pragma: no cover - a disk that filled mid-run
            LOGGER.warning("Could not write the capture report: %s", exc)
        else:
            LOGGER.info("Wrote capture report to %s", args.report.resolve())
    for warning in result.warnings:
        LOGGER.warning(warning)
    # The count is in the warning above. This line printed the length of the
    # report's list as the total, and the list stops at 20 (docs/09 P20-6).
    if result.failed_request_count:
        LOGGER.warning(
            failed_requests_pointer(
                result.failed_request_count, len(result.failed_requests)
            )
        )
    return 0


def failed_requests_pointer(total: int, listed: int) -> str:
    """Where the failed URLs are, and how many of them it holds."""
    if listed < total:
        return (
            f"The capture report (--report) lists the first {listed} of the "
            f"{total} failed request URLs."
        )
    return "The capture report (--report) lists the failed request URLs."


def schema_main(argv: Sequence[str]) -> int:
    """`webshot schema [NAME]` — print a published JSON Schema (spec §1.1).

    The schemas ship in `schemas/`, but a consumer that has installed the
    package should not have to find the repository to read the contract its
    bundles claim to follow.
    """
    from .bundle.manifest import SCHEMAS, export_schemas, render_schema

    parser = argparse.ArgumentParser(
        prog="webshot schema",
        description="Print a published JSON Schema, or list the available ones.",
    )
    parser.add_argument(
        "name",
        nargs="?",
        choices=sorted(SCHEMAS),
        help="which contract to print; omit to list them all",
    )
    parser.add_argument(
        "--write",
        type=Path,
        metavar="DIR",
        help=(
            "write every schema into DIR instead of printing one; this is how "
            "schemas/ is regenerated when a contract model changes"
        ),
    )
    arguments = parser.parse_args(list(argv))
    if arguments.write is not None:
        for path in export_schemas(arguments.write):
            print(path)
        return 0
    if arguments.name is None:
        print("\n".join(sorted(SCHEMAS)))
        return 0
    sys.stdout.write(render_schema(arguments.name))
    return 0


def doctor_main(argv: Sequence[str]) -> int:
    """`webshot doctor` — report what this machine can and cannot do (spec §1.2).

    Deferred like `mcp_main` below, and for a sharper reason: `doctor` exists
    to say which optional tools are missing, so it is the one command that
    must run on the most broken machine in the fleet. Loading the capture
    stack to start it made the diagnosis depend on the thing being diagnosed.
    `doctor.py` itself is unaffected — its module scope is stdlib only and its
    probes already use `importlib.util.find_spec` rather than importing, which
    is what keeps a missing optional tool a WARN or OPTIONAL line instead of a
    crash (docs/09 P8-43).
    """
    from .doctor import main as probe_main

    return probe_main(argv)


def journey_main(argv: Sequence[str]) -> int:
    """`webshot journey URL --dry-run` — enumerate a Continu journey (docs/13)."""
    from .journey.cli import main as walk_main

    return walk_main(argv)


def mcp_main(argv: Sequence[str]) -> int:
    """`webshot mcp` — serve the agent-facing tools over stdio (spec §1.3)."""
    from .mcp_server.cli import main as serve_main

    return serve_main(argv)


#: Subcommands, which are words rather than flags per docs/04-spec.md §1.1. A
#: local file literally named `doctor` can still be captured as `./doctor`.
SUBCOMMANDS: dict[str, Callable[[Sequence[str]], int]] = {
    "doctor": doctor_main,
    "schema": schema_main,
    "mcp": mcp_main,
    "journey": journey_main,
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in SUBCOMMANDS:
        try:
            return SUBCOMMANDS[arguments[0]](arguments[1:])
        except Exception as exc:
            # Same rule as the capture path: a subcommand that fails in an
            # unforeseen way still owes the caller a code from the taxonomy.
            LOGGER.error("%s", exc)
            return classify(exc)
        except KeyboardInterrupt:
            # Outside the spec §3 taxonomy on purpose: 130 is the shell's own
            # convention for SIGINT, and a caller that interrupted the run
            # already knows why it stopped.
            return 130
    try:
        return asyncio.run(async_main(arguments))
    except KeyboardInterrupt:
        LOGGER.error("Capture cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
