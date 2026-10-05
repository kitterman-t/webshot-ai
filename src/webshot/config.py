"""Capture options and results — the values every stage is configured by.

`CaptureOptions` is the one thing both interfaces build: the CLI translates a
parsed command line into it, and the MCP server constructs it directly from a
tool call.  Every rule about whether a set of options makes sense therefore
lives on the model rather than in either caller, so neither can be given a
combination the other would refuse (docs/02-architecture.md §Module map).

`webshot.toml` and `WEBSHOT_*` are read by `settings.py` and resolved into this
model; the precedence is docs/04-spec.md §1.1's.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .errors import UsageError

if TYPE_CHECKING:  # pragma: no cover - `DEFAULT_VIEWPORT`'s annotation only
    # Type-only, and deliberately so. Importing `playwright.async_api` for a
    # TypedDict cost 179 modules on every path that touches this module —
    # including `webshot --help`, which builds the parser from `PAPER_FORMATS`
    # and never launches a browser (docs/09 P10-6). `capture/session.py` is
    # the bridge that actually drives Playwright; this is the annotation of a
    # dict literal, and `from __future__ import annotations` keeps it a string.
    from playwright.async_api import ViewportSize

#: How a capture authenticated. Derived from the options rather than stored,
#: and defined here so the manifest and the QA report cannot answer it
#: differently for one capture (docs/09 P4-9).
AuthMode = Literal["none", "storage-state", "auth-profile"]

#: A CSS length WebShot accepts for `--margin`, in one place so the flag and
#: the settings layer cannot enforce different rules.
CSS_LENGTH = re.compile(r"(?:0|\d+(?:\.\d+)?(?:px|in|cm|mm))")

#: The paper sizes WebShot prints. Enforced on the model as well as by
#: argparse `choices`, because this value is interpolated into the video
#: appendix's `@page` rule — an unvalidated one injects CSS into a document
#: printed by a browser that carries none of the capture's request gating, and
#: the CLI's `choices` do not cover the MCP server or a direct construction
#: (docs/09 P7-10). `margin` has always been checked here for the same reason.
PAPER_FORMATS = ("Letter", "Legal", "Tabloid", "A3", "A4", "A5")

DEFAULT_NAVIGATION_TIMEOUT_MS = 120_000
DEFAULT_OPERATION_TIMEOUT_MS = 300_000
DEFAULT_VIEWPORT: ViewportSize = {"width": 1440, "height": 1000}


@dataclass(slots=True)
class CaptureOptions:
    source: str
    output: Path
    selector: str | None = None
    auto_selector: bool = False
    mode: Literal["clean", "faithful"] = "clean"
    exclude_selectors: list[str] = field(default_factory=list)
    wait_for_selector: str | None = None
    wait_after_load_s: float = 1.0
    navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS
    operation_timeout_ms: int = DEFAULT_OPERATION_TIMEOUT_MS
    scroll: bool = True
    max_scrolls: int = 100
    scroll_delay_s: float = 0.25
    paper_format: str = "Letter"
    landscape: bool = False
    margin: str = "0.6in"
    scale: float = 0.9
    media: Literal["print", "screen"] = "print"
    header_footer: bool = True
    document_title: str | None = None
    tagged: bool = True
    outline: bool = True
    prefer_css_page_size: bool = False
    storage_state: Path | None = None
    auth_profile: Path | None = None
    interactive_auth: bool = False
    protected_viewer: bool = False
    extra_css: Path | None = None
    user_agent: str | None = None
    allow_http_errors: bool = False
    debug_screenshot: Path | None = None
    ai_bundle: bool = True
    #: Also emit v2-format content.json/chunks.jsonl under legacy/ (docs/05
    #: task 2.5). Ships in v3.0, removed in v3.1.
    legacy_bundle: bool = False
    ai_bundle_directory: Path | None = None
    #: Carry the bundle inside the PDF as associated files, so one file is the
    #: whole deliverable (docs/02 §PDF deliverable). On by default: it is what
    #: makes the PDF self-describing to an agent, and it costs a rewrite of a
    #: file that was about to be written anyway.
    embed_bundle: bool = True
    #: Also embed the binary visual assets. Off by default — the images are
    #: already visible in the rendered pages and `assets.json` carries their
    #: recognized text, so the pixels are a second copy for callers who want
    #: to re-inspect them, not a default that doubles the file.
    embed_assets: bool = False
    #: How the bundle records a local source's location and the local files
    #: its page used: as the absolute `file:` URLs the browser resolved, or
    #: relative to the source file's own directory, so a bundle that travels
    #: does not carry this machine's user name and folder layout (docs/09
    #: P14-61). Absolute by default, so a capture that does not ask is what it
    #: always was. The QA report, which describes the run on this machine,
    #: keeps absolute paths either way.
    local_paths: Literal["absolute", "relative"] = "absolute"
    #: Read the walkthrough of every video embedded in the page and write it
    #: into the bundle and the PDF appendix. On by default: on a page whose
    #: procedure lives inside a player, the walkthrough *is* the content, and a
    #: capture that omits it has recorded the page and lost the point of it.
    #: A page with no embedded walkthrough pays one DOM read for this.
    videos: bool = True
    #: Also download each step's video clip and the source recording. Off by
    #: default, and not because of size: an agent cannot watch a video, the
    #: stills and the narration already carry the procedure, and a bundle whose
    #: bulk is unplayable media is worse for that purpose rather than better.
    #: The value of the clips is human verification and archiving, which is a
    #: real reason to ask for them and not a reason to default to them.
    video_assets: bool = False
    #: Where embedded walkthroughs are read from, and the host a playbook may
    #: name assets on. A field rather than a flag: no test and no recorded
    #: golden may reach live Guidde (docs/06), so both the suite and the
    #: `video` corpus case point this at a loopback stand-in — two real callers
    #: rather than one test seam. It stays off the QA report because every
    #: video's manifest record carries the URL it was actually read from, which
    #: is better evidence than the setting. When a second walkthrough provider
    #: arrives this becomes a per-provider origin rather than a second field.
    guidde_origin: str = "https://app.guidde.com"
    ocr: bool = True
    #: Turn missing recognition from a manifest warning into exit 6
    #: (docs/04-spec.md §1.1, §3). Off by default because degradation is the
    #: documented behaviour: a capture without OCR is still a capture, and the
    #: manifest says what it lacks. A caller that is building a searchable
    #: archive needs the opposite, and cannot get it from a warning.
    require_ocr: bool = False
    #: Turn an empty or nearly empty capture from a manifest warning into exit
    #: 5, with nothing published (docs/04-spec.md §5 item 14). Off by default
    #: for the reason `require_ocr` is: the warning already says what the
    #: capture lacks. A caller filing captures unattended needs the failure,
    #: because a warning in a file nobody opens is how P14-1 went unnoticed.
    require_content: bool = False
    ocr_language: str = "eng"
    ocr_psm: int = 11
    ocr_engine: Literal["tesseract", "rapid"] = "tesseract"
    #: Whether `--ocr-psm` was asked for rather than defaulted. Only used to
    #: decide whether ignoring it is worth telling the user about (docs/05
    #: task 3.5): a warning that fires when nothing was requested is noise.
    ocr_psm_explicit: bool = False
    max_assets: int = 50
    #: Abort every request the page makes to a private, loopback, or link-local
    #: address. Off by default, which keeps the CLI exactly as it was: a person
    #: who types a URL chose it, and a page that loads an intranet image is
    #: their business. The MCP server turns it on whenever it is refusing
    #: internal *targets*, because a guardrail that checks only the top-level
    #: URL is one an attacker walks around with an iframe (docs/09 P4-13).
    block_private_requests: bool = False
    #: Directories a `file:` request may read from, empty meaning "no rule".
    #: Set by the MCP server from its configured roots; a CLI capture leaves it
    #: empty, because the user who typed the command already has the
    #: filesystem their own process can read (docs/09 P8-11).
    filesystem_roots: tuple[Path, ...] = ()
    pdfa: bool = False
    validate_pdf: Literal["report", "strict"] | None = None

    @property
    def auth_mode(self) -> AuthMode:
        """How this capture authenticates — the whole of what is recorded.

        A bundle and a QA report both publish this, and neither publishes the
        *path* it came from: spec §6.1 keeps a session store out of a
        deliverable, and its location is not something either artifact needs.
        """
        if self.auth_profile:
            return "auth-profile"
        if self.storage_state:
            return "storage-state"
        return "none"

    def __post_init__(self) -> None:
        """Every rule about the options themselves, wherever they were built.

        docs/02-architecture.md makes this model the single source of truth that
        the CLI *and* the MCP server construct, so a refusal that lived in
        argparse would not apply to the second one.  Until Phase 4 only the
        `--pdfa` rule had actually moved here and the other eleven stayed in
        `cli.options_from_args`, which is why the MCP server had to build a
        synthetic argparse namespace to reach them (docs/09 P4-8).

        Messages name the *flag* rather than the field because that is what a
        person reading them typed; the MCP guide maps tool parameters onto
        flags for the other caller.
        """
        # Finiteness before the range checks, because NaN fails every
        # comparison silently: `nan < 0` is False, so a NaN delay passed the
        # sign check below and reached `asyncio.sleep`, and `inf` passed it and
        # would have waited forever. A non-finite timeout got further still and
        # died inside `round(args.timeout * 1000)` as an unclassified
        # exception, where the contract says a bad configured value is a usage
        # error (docs/09 P8-50). TOML has no `inf`, but `WEBSHOT_DELAY=inf`
        # and a JSON `1e400` both parse to one.
        for name, value in (
            ("--scale", self.scale),
            ("--delay", self.wait_after_load_s),
            ("--scroll-delay", self.scroll_delay_s),
            ("--timeout", self.navigation_timeout_ms),
        ):
            if not math.isfinite(value):
                raise UsageError(f"{name} must be a finite number, not {value!r}.")
        if not 0.1 <= self.scale <= 2.0:
            raise UsageError("--scale must be between 0.1 and 2.0.")
        if self.wait_after_load_s < 0 or self.scroll_delay_s < 0:
            raise UsageError("--delay and --scroll-delay cannot be negative.")
        if self.navigation_timeout_ms <= 0:
            # Milliseconds, so `--timeout 0.0004` rounds to 0 and is refused —
            # which is right, because Playwright reads a zero timeout as *no*
            # timeout and the capture would hang forever instead.
            raise UsageError(
                "--timeout must be at least 0.001 seconds (it is applied in "
                "milliseconds, and a zero timeout means no timeout at all)."
            )
        if self.max_scrolls < 1:
            raise UsageError("--max-scrolls must be at least 1.")
        if self.max_assets < 0:
            raise UsageError("--max-assets cannot be negative.")
        if not re.fullmatch(r"[A-Za-z0-9_+-]+", self.ocr_language):
            raise UsageError("--ocr-language contains unsupported characters.")
        if self.paper_format not in PAPER_FORMATS:
            raise UsageError(
                f"--format must be one of {', '.join(PAPER_FORMATS)}, not "
                f"{self.paper_format!r}."
            )
        if not CSS_LENGTH.fullmatch(self.margin):
            # Only argparse's `type=` used to check this, so it ran on a flag
            # and on nothing else: a margin from `webshot.toml`, `WEBSHOT_*`,
            # or an MCP call reached Chromium unvalidated and failed there as an
            # unclassified error (docs/09 P4-12).
            raise UsageError(
                f"--margin must be a CSS length such as 0, 12mm, or 0.5in, not "
                f"{self.margin!r}."
            )
        if self.storage_state and not self.storage_state.is_file():
            raise UsageError(f"Storage-state file does not exist: {self.storage_state}")
        if self.storage_state:
            # Existence is not usability. A malformed or non-object file was
            # accepted here and rejected by Chromium from inside
            # `browser.new_context()` as a plain `PlaywrightError`, which the
            # taxonomy classifies as a capture-integrity failure (exit 5) —
            # for a caller-supplied file that was unusable before anything was
            # captured, which is exit 2 (docs/09 P8-51). Parsed, not
            # schema-checked: the shape Playwright wants is Playwright's, and
            # duplicating it here would be a second thing to keep current.
            try:
                state = json.loads(self.storage_state.read_text("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UsageError(
                    f"Storage-state file {self.storage_state} is not readable "
                    f"JSON: {exc}"
                ) from exc
            if not isinstance(state, dict):
                raise UsageError(
                    f"Storage-state file {self.storage_state} is not a JSON "
                    "object; Playwright expects `cookies` and `origins`."
                )
        if self.storage_state and self.auth_profile:
            raise UsageError("Use either --storage-state or --auth-profile, not both.")
        if self.interactive_auth and not self.auth_profile:
            raise UsageError("--interactive-auth requires --auth-profile.")
        if self.protected_viewer and not self.ai_bundle:
            raise UsageError(
                "--protected-viewer requires its page-level AI bundle for provenance and "
                "OCR verification; omit --no-ai-bundle."
            )
        if self.require_ocr and not self.ocr:
            # Contradictory rather than merely redundant: one flag says
            # recognition is mandatory and the other switches it off, so
            # honouring either would be guessing which one was meant.
            raise UsageError(
                "--require-ocr and --no-ocr contradict each other: one makes "
                "missing recognition a failure, the other asks for no "
                "recognition at all."
            )
        if self.require_content and not self.ai_bundle:
            # The check reads the bundle's own counts, so without a bundle it
            # has nothing to read. Running the capture anyway would publish
            # a PDF the flag promised to check and never did.
            raise UsageError(
                "--require-content and --no-ai-bundle contradict each other: "
                "the content check reads the counts the AI bundle records, and "
                "--no-ai-bundle builds none."
            )
        if self.video_assets and not self.videos:
            # Contradictory rather than redundant: one asks for the videos'
            # own media and the other switches video enrichment off entirely,
            # so honouring either would be guessing which was meant.
            raise UsageError(
                "--video-assets and --no-videos contradict each other: one asks "
                "for the embedded walkthroughs' media, the other skips the "
                "walkthroughs altogether."
            )
        if self.legacy_bundle and not self.ai_bundle:
            raise UsageError(
                "--legacy-bundle emits v2-format artifacts inside the AI bundle, "
                "which --no-ai-bundle disables; use one or the other."
            )
        if self.local_paths not in ("absolute", "relative"):
            raise UsageError(
                f"--local-paths must be absolute or relative, not {self.local_paths!r}."
            )
        if (
            self.local_paths == "relative"
            and self.protected_viewer
            and self.source[:5].lower() == "file:"
        ):
            # The protected-viewer bundle is written by its own builder, which
            # this convention does not reach; accepting the flag there would
            # promise relative paths in a bundle that records absolute ones.
            raise UsageError(
                "--local-paths relative applies to the web capture path; the "
                "--protected-viewer bundle records a local source's absolute path."
            )
        if self.extra_css and not self.extra_css.is_file():
            raise UsageError(f"CSS file does not exist: {self.extra_css}")
        if self.pdfa and not self.protected_viewer:
            # ADR-0010 / docs/09 S7: PDF/A conversion removes the structure tree
            # and MarkInfo Chromium builds, so a "PDF/A web capture" would
            # silently trade the accessibility WebShot promises for archival
            # conformance.
            raise UsageError(
                "--pdfa is available on --protected-viewer captures only. "
                "Converting a web capture to PDF/A removes its accessibility "
                "tags (docs/adr/0010), so WebShot refuses rather than "
                "downgrading the output silently."
            )


@dataclass(slots=True)
class CaptureResult:
    source: str
    final_url: str
    output: str
    title: str
    pages: int
    bytes: int
    http_status: int | None
    content_type: str
    duration_seconds: float
    captured_at: str
    content_selector: str | None = None
    source_kind: str = "web"
    ai_bundle: str | None = None
    ai_summary: dict[str, int] = field(default_factory=dict)
    #: The first `LISTED_FAILED_REQUESTS` distinct URLs that failed: a sample,
    #: never the count. `failed_request_count` is the count.
    failed_requests: list[str] = field(default_factory=list)
    #: Every distinct URL that failed, counted. None when nothing counted, which
    #: is not the same as counting none (docs/09 P20-6).
    failed_request_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    # Present only when --validate-pdf ran; the QA report says so explicitly
    # rather than by omission (docs/04-spec.md §2.3, schema v2).
    validation: dict[str, object] | None = None
    #: Wall-clock seconds per pipeline stage, in the order they ran. Only
    #: stages a run actually entered appear.
    timings: dict[str, float] = field(default_factory=dict)
    #: SHA-256 of the published `manifest.json` — the one bundle file the
    #: manifest's own checksum table cannot cover (docs/04-spec.md §2.2).
    manifest_sha256: str | None = None
    #: The protected path's page-count invariant as measured, or None on the
    #: web path, which composes no appendix and so has nothing to reconcile.
    page_count_invariant: dict[str, int] | None = None


def validate_css_length(value: str) -> str:
    """argparse's own check, so a bad `--margin` fails before the browser opens.

    The same rule is enforced on the model, which is what covers the values
    argparse never sees.
    """
    if not CSS_LENGTH.fullmatch(value):
        raise argparse.ArgumentTypeError("use a CSS length such as 0, 12mm, or 0.5in")
    return value
