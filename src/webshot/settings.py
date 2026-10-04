"""`webshot.toml` and `WEBSHOT_*` — settings that arrive from outside a command.

docs/04-spec.md §1.1 fixes the precedence: **flags > env > file > defaults**.
This module owns the two lower layers and hands the result to the CLI, which
applies it to the argparse namespace for every option the command line did not
name.  Resolution therefore ends where it always did — in
`cli.options_from_args`, building the one `CaptureOptions` model — so a value
that came from a file is validated by exactly the rules a flag is.

**A config file is loaded only from a path someone named**: `--config FILE`, or
`WEBSHOT_CONFIG`.  There is deliberately no `./webshot.toml` discovery.  WebShot
is routinely run against a directory of someone else's material, and a tool that
picks up settings from wherever it happens to be started can be handed a
`user_agent`, an `[mcp] roots` list, or a `css` file by that directory.  The
refusal is tested (`tests/test_settings.py`).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from .errors import UsageError

# One pattern, imported rather than restated: the request side refuses a name
# that does not match it, so a config that accepts a wider set is a config
# that accepts names nobody can use (docs/09 P8-61). `mcp_server.policy`
# imports only `netpolicy` and `errors`, so this direction is acyclic.
from .mcp_server.policy import PROFILE_NAME

#: The environment variable that names a config file, and the prefix every
#: other setting's variable is built from: `WEBSHOT_<SECTION>_<KEY>`.
CONFIG_ENV_VAR = "WEBSHOT_CONFIG"
ENV_PREFIX = "WEBSHOT_"

#: Default read cap for `read_asset` over MCP (docs/04-spec.md §5.8).
DEFAULT_READ_ASSET_MAX_BYTES = 5 * 1024 * 1024

#: Default cap for one `read_markdown` page. Markdown is text an agent pays for
#: by the token, so the page is smaller than an asset read and is documented as
#: paged rather than truncated.
DEFAULT_MARKDOWN_PAGE_BYTES = 256 * 1024

#: Default cap for one `query_chunks` answer.
DEFAULT_MAX_CHUNKS = 200


#: A path setting written the way a person writes one. Expanding `~` here
#: rather than in a validator per container shape means a setting added later
#: cannot be the one nobody remembered to expand.
ExpandedPath = Annotated[Path, AfterValidator(Path.expanduser)]


def _int_if_numeric(value: Any) -> Any:
    """Read `"11"` as `11`, and leave everything else for the real validator."""
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    return value


class _Section(BaseModel):
    """Base for every config table: an unknown key is a mistake, not a comment."""

    model_config = ConfigDict(extra="forbid")


class CaptureSettings(_Section):
    """`[capture]` — what to capture and how hard to try."""

    mode: Literal["clean", "faithful"] | None = None
    selector: str | None = None
    auto_selector: bool | None = None
    exclude: list[str] | None = None
    wait_for: str | None = None
    delay: float | None = None
    timeout: float | None = None
    #: The positive sense of `--no-scroll`, because a config file that says
    #: `no_scroll = false` is a double negative nobody reads correctly.
    scroll: bool | None = None
    max_scrolls: int | None = None
    scroll_delay: float | None = None
    user_agent: str | None = None
    allow_http_errors: bool | None = None
    max_assets: int | None = None
    css: ExpandedPath | None = None
    #: Positive sense of `--no-ai-bundle`.
    ai_bundle: bool | None = None
    legacy_bundle: bool | None = None
    #: Positive sense of `--no-embed-bundle`.
    embed_bundle: bool | None = None
    embed_assets: bool | None = None
    #: Positive sense of `--no-videos`.
    videos: bool | None = None
    video_assets: bool | None = None


class PdfSettings(_Section):
    """`[pdf]` — page geometry and the two conformance gates."""

    format: Literal["Letter", "Legal", "Tabloid", "A3", "A4", "A5"] | None = None
    landscape: bool | None = None
    margin: str | None = None
    scale: float | None = None
    media: Literal["print", "screen"] | None = None
    prefer_css_page_size: bool | None = None
    #: Positive senses of `--no-header-footer`, `--no-tagged-pdf`, `--no-outline`.
    header_footer: bool | None = None
    tagged: bool | None = None
    outline: bool | None = None
    pdfa: bool | None = None
    validate_pdf: Literal["report", "strict"] | None = None


class OcrSettings(_Section):
    """`[ocr]` — recognition engine and its settings."""

    #: Positive sense of `--no-ocr`.
    enabled: bool | None = None
    #: `--require-ocr`: make a missing recognizer exit 6 instead of a warning.
    #: Settable here because it is a *policy* rather than a per-page choice —
    #: an archive that must be searchable wants it on every run, and that is
    #: what a settings file is for.
    require: bool | None = None
    language: str | None = None
    #: `BeforeValidator` because every environment value arrives as a string and
    #: pydantic does not coerce `str` into an int-valued `Literal` even in lax
    #: mode — so `WEBSHOT_OCR_PSM=11` was rejected as out of range while the
    #: guide promised every setting had a variable (docs/09 P4-12).
    psm: Annotated[Literal[3, 6, 11, 12], BeforeValidator(_int_if_numeric)] | None = (
        None
    )
    engine: Literal["tesseract", "rapid"] | None = None


class McpServerSettings(_Section):
    """`[mcp]` — the agent-facing surface, which is deny-by-default.

    The CLI is driven by a person who chose every path on it.  The MCP server is
    driven by an agent whose instructions may have come from a page WebShot
    itself captured, so every setting here is a boundary rather than a
    convenience, and each one has a refusal test in `tests/test_mcp_server.py`.
    """

    #: Directories `read_markdown`, `query_chunks`, `get_manifest`,
    #: `list_assets`, `read_asset` and `file://` captures may reach.  Empty
    #: means "only what this server produced": `output_root` is always a root.
    roots: list[ExpandedPath] = Field(default_factory=list)
    #: The single directory captures are published under.  No MCP caller may
    #: choose an output path (docs/04-spec.md §6.8).
    output_root: ExpandedPath = Path("output/pdf")
    #: RFC1918 / loopback / link-local targets are refused unless this is on.
    #: The opt-in exists for the legitimate case — an agent capturing a
    #: dashboard on localhost — and is off so that the default server cannot be
    #: talked into reading the host's internal network.
    allow_private_networks: bool = False
    read_asset_max_bytes: int = Field(
        default=DEFAULT_READ_ASSET_MAX_BYTES, gt=0, le=64 * 1024 * 1024
    )
    markdown_page_bytes: int = Field(
        default=DEFAULT_MARKDOWN_PAGE_BYTES, gt=0, le=8 * 1024 * 1024
    )
    #: The third of the server's three read caps, and configurable like the
    #: other two: an unbounded `query_chunks` limit over a large bundle is a way
    #: to spend an agent's whole context window on one call.
    max_chunks: int = Field(default=DEFAULT_MAX_CHUNKS, gt=0, le=10_000)
    #: Named authentication profiles: `capture` takes a *name*, never a path and
    #: never storage-state content (docs/04-spec.md §6.8, guardrail (d)).
    auth_profiles: dict[str, ExpandedPath] = Field(default_factory=dict)

    @field_validator("auth_profiles")
    @classmethod
    def _names_match_the_request_grammar(
        cls, value: dict[str, Path]
    ) -> dict[str, Path]:
        """A key a caller could never name is a misconfiguration, not a profile.

        `resolve_auth_profile` refuses anything that does not match
        `PROFILE_NAME` before it looks the name up — so a TOML key like
        `"work profile"` loaded cleanly, was advertised by `--print-roots`, and
        was unusable by every caller. Startup reported valid configuration for
        something that could not be reached (docs/09 P8-61). Checked here,
        against the same pattern the request side uses, so the two cannot drift.
        """
        invalid = sorted(name for name in value if not PROFILE_NAME.fullmatch(name))
        if invalid:
            raise ValueError(
                "authentication profile names must match "
                f"{PROFILE_NAME.pattern} — these cannot be requested by any "
                f"caller: {', '.join(repr(name) for name in invalid)}"
            )
        return value


class WebshotConfig(_Section):
    """A parsed `webshot.toml`, or the all-defaults config when there is none."""

    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    mcp: McpServerSettings = Field(default_factory=McpServerSettings)


# --------------------------------------------------------------------------- #
# Mapping settings onto the CLI's own option names
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SettingTarget:
    """Where one configurable setting lands in each of the two callers.

    `dest` is the argparse destination the CLI writes it to; `option` is the
    `CaptureOptions` field the MCP server passes it as.  Both are named because
    the two vocabularies genuinely differ — `--no-scroll` is a flag and `scroll`
    is a field — and a table that knew only one of them would force the other
    caller to reconstruct it.
    """

    option: str
    dest: str
    #: True for a setting written in the positive sense whose flag is a `--no-…`.
    invert: bool = False


#: The whole config surface. What is not in this table cannot be set from a file
#: or from the environment, and `tests/test_settings.py` checks every `dest`
#: against the parser and every `option` against `CaptureOptions`, so a rename on
#: either side is a failing test rather than a setting that silently stops
#: working.
SETTINGS: dict[tuple[str, str], SettingTarget] = {
    ("capture", "mode"): SettingTarget("mode", "mode"),
    ("capture", "selector"): SettingTarget("selector", "selector"),
    ("capture", "auto_selector"): SettingTarget("auto_selector", "auto_selector"),
    ("capture", "exclude"): SettingTarget("exclude_selectors", "exclude"),
    ("capture", "wait_for"): SettingTarget("wait_for_selector", "wait_for"),
    ("capture", "delay"): SettingTarget("wait_after_load_s", "delay"),
    # Seconds on both interfaces, milliseconds in the model — converted in
    # `capture_overrides`, which is the only place that has to know.
    ("capture", "timeout"): SettingTarget("navigation_timeout_ms", "timeout"),
    ("capture", "scroll"): SettingTarget("scroll", "no_scroll", invert=True),
    ("capture", "max_scrolls"): SettingTarget("max_scrolls", "max_scrolls"),
    ("capture", "scroll_delay"): SettingTarget("scroll_delay_s", "scroll_delay"),
    ("capture", "user_agent"): SettingTarget("user_agent", "user_agent"),
    ("capture", "allow_http_errors"): SettingTarget(
        "allow_http_errors", "allow_http_errors"
    ),
    ("capture", "max_assets"): SettingTarget("max_assets", "max_assets"),
    ("capture", "css"): SettingTarget("extra_css", "css"),
    ("capture", "ai_bundle"): SettingTarget("ai_bundle", "no_ai_bundle", invert=True),
    ("capture", "legacy_bundle"): SettingTarget("legacy_bundle", "legacy_bundle"),
    ("capture", "embed_bundle"): SettingTarget(
        "embed_bundle", "no_embed_bundle", invert=True
    ),
    ("capture", "embed_assets"): SettingTarget("embed_assets", "embed_assets"),
    ("capture", "videos"): SettingTarget("videos", "no_videos", invert=True),
    ("capture", "video_assets"): SettingTarget("video_assets", "video_assets"),
    ("pdf", "format"): SettingTarget("paper_format", "paper_format"),
    ("pdf", "landscape"): SettingTarget("landscape", "landscape"),
    ("pdf", "margin"): SettingTarget("margin", "margin"),
    ("pdf", "scale"): SettingTarget("scale", "scale"),
    ("pdf", "media"): SettingTarget("media", "media"),
    ("pdf", "prefer_css_page_size"): SettingTarget(
        "prefer_css_page_size", "prefer_css_page_size"
    ),
    ("pdf", "header_footer"): SettingTarget(
        "header_footer", "no_header_footer", invert=True
    ),
    ("pdf", "tagged"): SettingTarget("tagged", "no_tagged_pdf", invert=True),
    ("pdf", "outline"): SettingTarget("outline", "no_outline", invert=True),
    ("pdf", "pdfa"): SettingTarget("pdfa", "pdfa"),
    ("pdf", "validate_pdf"): SettingTarget("validate_pdf", "validate_pdf"),
    ("ocr", "enabled"): SettingTarget("ocr", "no_ocr", invert=True),
    ("ocr", "require"): SettingTarget("require_ocr", "require_ocr"),
    ("ocr", "language"): SettingTarget("ocr_language", "ocr_language"),
    ("ocr", "psm"): SettingTarget("ocr_psm", "ocr_psm"),
    ("ocr", "engine"): SettingTarget("ocr_engine", "ocr_engine"),
}

#: Settings whose value is a list of paths and which therefore *can* be given in
#: one environment variable, split on the platform's path separator.  Every
#: other list-valued setting (`capture.exclude`, whose CSS selectors legitimately
#: contain both commas and colons) is file-or-flag only, and saying so is why
#: this set exists rather than a general splitting rule.
ENV_PATH_LISTS = {("mcp", "roots")}


def env_key(section: str, key: str) -> str:
    """The environment variable one setting is read from."""
    return f"{ENV_PREFIX}{section.upper()}_{key.upper()}"


def _settings_from_env(environ: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Collect `WEBSHOT_<SECTION>_<KEY>` into the shape a config file has.

    Values stay strings: they are validated by the same pydantic models the file
    goes through, so `WEBSHOT_PDF_SCALE=abc` fails the same way `scale = "abc"`
    does, and with the same message.
    """
    collected: dict[str, dict[str, Any]] = {}
    for section, model in WebshotConfig.model_fields.items():
        annotation = model.annotation
        assert annotation is not None and issubclass(annotation, _Section)
        for key in annotation.model_fields:
            raw = environ.get(env_key(section, key))
            if raw is None:
                continue
            value: Any = raw
            if (section, key) in ENV_PATH_LISTS:
                value = [part for part in raw.split(os.pathsep) if part]
            collected.setdefault(section, {})[key] = value
    return collected


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise UsageError(f"Config file does not exist: {path}") from exc
    except IsADirectoryError as exc:
        raise UsageError(f"Config path is a directory, not a file: {path}") from exc
    except OSError as exc:
        raise UsageError(f"Config file could not be read: {path} ({exc})") from exc
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"{path} is not valid TOML: {exc}") from exc


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay wins, one table at a time — the environment over the file."""
    merged = {section: dict(values) for section, values in base.items()}
    for section, values in overlay.items():
        merged.setdefault(section, {}).update(values)
    return merged


def config_path(
    flag: Path | None, environ: Mapping[str, str] | None = None
) -> Path | None:
    """The config file to load, or None. `--config` outranks `WEBSHOT_CONFIG`."""
    if flag is not None:
        return flag.expanduser()
    named = (environ if environ is not None else os.environ).get(CONFIG_ENV_VAR)
    return Path(named).expanduser() if named else None


def load_config(
    flag: Path | None = None, environ: Mapping[str, str] | None = None
) -> WebshotConfig:
    """Build the config from an explicitly named file plus `WEBSHOT_*`.

    Nothing is discovered: with no `--config` and no `WEBSHOT_CONFIG`, the file
    layer is empty even if the working directory contains a `webshot.toml`.
    """
    environ = environ if environ is not None else os.environ
    path = config_path(flag, environ)
    from_file: dict[str, Any] = {}
    if path is not None:
        raw = _read_toml(path)
        if not all(isinstance(value, dict) for value in raw.values()):
            stray = sorted(
                key for key, value in raw.items() if not isinstance(value, dict)
            )
            raise UsageError(
                f"{path}: settings live in tables, but {stray} are at the top "
                "level. Use [capture], [pdf], [ocr], or [mcp]."
            )
        from_file = raw
    from_env = _settings_from_env(environ)
    try:
        return WebshotConfig.model_validate(_merge(from_file, from_env))
    except ValidationError as exc:
        raise UsageError(_explain(exc, path, from_env)) from exc


def _explain(
    error: ValidationError,
    path: Path | None,
    from_env: Mapping[str, dict[str, Any]] = MappingProxyType({}),
) -> str:
    """Turn a pydantic error into a sentence naming where the value came from.

    Which layer supplied it, not merely which layers exist: naming the config
    file for a value the *environment* set sends an operator to edit a file that
    is already correct (docs/09 P4-12).
    """
    lines = []
    for problem in error.errors():
        location = [str(part) for part in problem["loc"]]
        if len(location) >= 2:
            section, key = location[0], location[1]
            if key in from_env.get(section, {}):
                origin = env_key(section, key)
            elif path is not None:
                origin = f"{path}: [{section}] {key}"
            else:
                origin = env_key(section, key)
        else:
            origin = f"{path}: {'.'.join(location)}" if path else ".".join(location)
        lines.append(f"{origin} — {problem['msg']}")
    return "Invalid configuration:\n  " + "\n  ".join(lines)


def _given(
    config: WebshotConfig,
) -> Iterator[tuple[tuple[str, str], SettingTarget, Any]]:
    """Every setting this config actually states, with its target.

    A setting left unset produces nothing, which is what keeps "the file said
    nothing" distinguishable from "the file agreed with the default": the second
    must outrank a lower layer and the first must not.
    """
    for location, target in SETTINGS.items():
        section, key = location
        value = getattr(getattr(config, section), key)
        if value is not None:
            yield location, target, value


def option_overrides(config: WebshotConfig) -> dict[str, Any]:
    """The `{argparse dest: value}` a config contributes, for the CLI."""
    return {
        target.dest: (not value) if target.invert else value
        for _, target, value in _given(config)
    }


def capture_overrides(config: WebshotConfig) -> dict[str, Any]:
    """The `{CaptureOptions field: value}` a config contributes, for MCP.

    The same table the CLI reads, resolved to the model's own vocabulary so the
    MCP server can construct `CaptureOptions` directly.  Two settings need more
    than a rename, and this is the only place that knows it: `timeout` is
    seconds on both interfaces and milliseconds in the model, and a configured
    `psm` is a *request*, so it sets the flag that decides whether ignoring it
    under RapidOCR is worth saying out loud (docs/09 P3-7).
    """
    overrides: dict[str, Any] = {}
    for location, target, value in _given(config):
        if location == ("capture", "timeout"):
            overrides[target.option] = round(float(value) * 1000)
        elif location == ("ocr", "psm"):
            overrides[target.option] = value
            overrides["ocr_psm_explicit"] = True
        else:
            # No inversion here: `invert` describes the *flag* relationship —
            # `scroll = false` becomes `--no-scroll`, but the model's own field
            # is `scroll`, and it takes the setting as written.
            overrides[target.option] = value
    return overrides
