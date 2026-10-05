"""`webshot.toml`, `WEBSHOT_*`, and the order they lose to each other.

docs/04-spec.md §1.1 states one rule — **flags > env > file > defaults** — and
one refusal: a config file is read only from a path someone named.  Both are
here, and the refusal is the more important of the two.  WebShot is routinely
pointed at a directory of material it did not produce; a tool that reads
settings from its working directory can be handed a `user_agent`, a set of MCP
roots, or an injected stylesheet by that directory.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from webshot.cli import apply_settings, build_parser, options_from_args, provided_dests
from webshot.config import CaptureOptions
from webshot.errors import UsageError
from webshot.settings import (
    SETTINGS,
    WebshotConfig,
    capture_overrides,
    env_key,
    load_config,
    option_overrides,
)

SOURCE = "https://example.com/article"

# Every `load_config` here passes an explicit environment. A bare call reads
# `os.environ`, so a developer with `WEBSHOT_PDF_SCALE` exported — exactly the
# people working on this feature — would get a red suite that has nothing to do
# with their change (docs/09 P4-12).


def resolve(
    argv: list[str], environ: dict[str, str] | None = None
) -> tuple[object, WebshotConfig]:
    """Run one command line through the whole settings resolution."""
    arguments = [*argv]
    args = build_parser().parse_args(arguments)
    config = apply_settings(args, arguments, environ or {})
    return args, config


def options(argv: list[str], environ: dict[str, str] | None = None):
    args, _ = resolve(argv, environ)
    return options_from_args(args)


def write_config(directory: Path, body: str) -> Path:
    path = directory / "webshot.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# The refusal: no implicit discovery
# --------------------------------------------------------------------------- #


def test_a_webshot_toml_in_the_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the rule: being *in* a directory grants it nothing.

    An attacker who can drop a file into a directory a capture is run from must
    not thereby choose the user agent, the injected CSS, or the MCP roots.
    """
    write_config(tmp_path, '[capture]\nuser_agent = "injected-by-the-cwd"\n')
    monkeypatch.chdir(tmp_path)
    resolved = options([SOURCE])
    assert resolved.user_agent is None


def test_a_config_is_read_when_the_flag_names_it(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[capture]\nuser_agent = "named-explicitly"\n')
    assert options([SOURCE, "--config", str(path)]).user_agent == "named-explicitly"


def test_a_config_is_read_when_the_environment_names_it(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[capture]\nuser_agent = "named-by-env"\n')
    resolved = options([SOURCE], {"WEBSHOT_CONFIG": str(path)})
    assert resolved.user_agent == "named-by-env"


def test_the_flag_outranks_the_environment_variable(tmp_path: Path) -> None:
    from_env = write_config(tmp_path, '[capture]\nuser_agent = "from-env"\n')
    from_flag = tmp_path / "flag.toml"
    from_flag.write_text('[capture]\nuser_agent = "from-flag"\n', encoding="utf-8")
    resolved = options(
        [SOURCE, "--config", str(from_flag)], {"WEBSHOT_CONFIG": str(from_env)}
    )
    assert resolved.user_agent == "from-flag"


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


def test_precedence_is_flag_then_env_then_file_then_default(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        '[capture]\nuser_agent = "from-file"\n[pdf]\nscale = 0.5\nmargin = "1in"\n',
    )
    environ = {env_key("capture", "user_agent"): "from-env"}

    default_only = options([SOURCE])
    assert default_only.scale == 0.9 and default_only.margin == "0.6in"

    file_only = options([SOURCE, "--config", str(path)])
    assert file_only.user_agent == "from-file"
    assert file_only.scale == 0.5
    assert file_only.margin == "1in"

    env_over_file = options([SOURCE, "--config", str(path)], environ)
    assert env_over_file.user_agent == "from-env"
    assert env_over_file.scale == 0.5  # the file still supplies what env did not

    flag_over_env = options(
        [SOURCE, "--config", str(path), "--user-agent", "from-flag", "--scale", "1.5"],
        environ,
    )
    assert flag_over_env.user_agent == "from-flag"
    assert flag_over_env.scale == 1.5


def test_a_flag_set_to_its_own_default_still_outranks_the_file(tmp_path: Path) -> None:
    """`--scale 0.9` is a request, not an absence.

    This is why resolution re-parses against an all-`None` parser instead of
    comparing values: a namespace cannot otherwise distinguish the two, and the
    file would silently win an argument the user thought they had made.
    """
    path = write_config(tmp_path, "[pdf]\nscale = 0.5\n")
    assert options([SOURCE, "--config", str(path), "--scale", "0.9"]).scale == 0.9


def test_provided_dests_reports_only_what_the_command_line_named() -> None:
    named = provided_dests([SOURCE, "--scale", "0.9", "--no-scroll"])
    assert {"source", "scale", "no_scroll"} <= named
    assert "margin" not in named
    assert "user_agent" not in named


# --------------------------------------------------------------------------- #
# The settings themselves
# --------------------------------------------------------------------------- #


def test_negated_flags_are_configured_in_the_positive_sense(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[capture]\nscroll = false\nai_bundle = false\n[ocr]\nenabled = false\n"
        "[pdf]\nheader_footer = false\ntagged = false\noutline = false\n",
    )
    resolved = options([SOURCE, "--config", str(path)])
    assert resolved.scroll is False
    assert resolved.ai_bundle is False
    assert resolved.ocr is False
    assert resolved.header_footer is False
    assert resolved.tagged is False
    assert resolved.outline is False


def test_a_configured_ocr_psm_counts_as_a_request(tmp_path: Path) -> None:
    """`--ocr-psm` is warned-and-ignored under RapidOCR only when it was asked
    for (docs/09 P3-7), and asking through a config file is still asking."""
    path = write_config(tmp_path, "[ocr]\npsm = 6\n")
    resolved = options([SOURCE, "--config", str(path)])
    assert resolved.ocr_psm == 6
    assert resolved.ocr_psm_explicit is True
    assert options([SOURCE]).ocr_psm_explicit is False


def test_environment_values_are_validated_like_file_values() -> None:
    with pytest.raises(UsageError) as failure:
        load_config(None, {env_key("pdf", "scale"): "not-a-number"})
    assert env_key("pdf", "scale") in str(failure.value)


def test_a_configured_value_meets_the_same_validation_a_flag_does(
    tmp_path: Path,
) -> None:
    """Resolution ends in `options_from_args`, so the file cannot smuggle a
    value past a rule the command line enforces."""
    path = write_config(tmp_path, "[pdf]\nscale = 9.0\n")
    with pytest.raises(Exception) as failure:
        options([SOURCE, "--config", str(path)])
    assert "--scale" in str(failure.value)


def test_mcp_roots_arrive_from_a_path_separated_environment_variable() -> None:
    raw = os.pathsep.join(["/srv/one", "/srv/two"])
    config = load_config(None, {env_key("mcp", "roots"): raw})
    assert config.mcp.roots == [Path("/srv/one"), Path("/srv/two")]


def test_mcp_settings_come_from_the_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[mcp]\n"
        'roots = ["/srv/captures"]\n'
        'output_root = "/srv/out"\n'
        "allow_private_networks = true\n"
        "read_asset_max_bytes = 1024\n"
        '[mcp.auth_profiles]\nwork = "/srv/profiles/work"\n',
    )
    config = load_config(path, {})
    assert config.mcp.roots == [Path("/srv/captures")]
    assert config.mcp.output_root == Path("/srv/out")
    assert config.mcp.allow_private_networks is True
    assert config.mcp.read_asset_max_bytes == 1024
    assert config.mcp.auth_profiles == {"work": Path("/srv/profiles/work")}


# --------------------------------------------------------------------------- #
# Bad input is a usage error (exit 2), not a traceback
# --------------------------------------------------------------------------- #


def test_a_missing_config_file_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(UsageError) as failure:
        load_config(tmp_path / "absent.toml")
    assert failure.value.exit_code == 2


def test_malformed_toml_is_a_usage_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[capture\nmode = clean\n")
    with pytest.raises(UsageError) as failure:
        load_config(path, {})
    assert "not valid TOML" in str(failure.value)


def test_an_unknown_setting_is_refused_by_name(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[capture]\nmoed = "clean"\n')
    with pytest.raises(UsageError) as failure:
        load_config(path, {})
    assert "moed" in str(failure.value)


def test_an_unknown_table_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[captures]\nmode = "clean"\n')
    with pytest.raises(UsageError) as failure:
        load_config(path, {})
    assert "captures" in str(failure.value)


def test_a_setting_outside_any_table_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'mode = "clean"\n')
    with pytest.raises(UsageError) as failure:
        load_config(path, {})
    assert "top level" in str(failure.value)


def test_an_invalid_choice_names_the_file_and_the_key(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[capture]\nmode = "sideways"\n')
    with pytest.raises(UsageError) as failure:
        load_config(path, {})
    message = str(failure.value)
    assert str(path) in message and "mode" in message


# --------------------------------------------------------------------------- #
# The mapping table cannot rot
# --------------------------------------------------------------------------- #


def test_every_configurable_setting_maps_to_a_real_cli_option() -> None:
    dests = {action.dest for action in build_parser()._actions}
    orphans = {
        f"[{section}] {key} -> {target.dest}"
        for (section, key), target in SETTINGS.items()
        if target.dest not in dests
    }
    assert not orphans, f"settings pointing at options that no longer exist: {orphans}"


def test_every_configurable_setting_maps_to_a_real_options_field() -> None:
    """The other half of the table, which the MCP server resolves through."""
    fields = {field.name for field in dataclasses.fields(CaptureOptions)}
    orphans = {
        f"[{section}] {key} -> {target.option}"
        for (section, key), target in SETTINGS.items()
        if target.option not in fields
    }
    assert not orphans, f"settings pointing at fields that no longer exist: {orphans}"


def test_every_capture_setting_is_in_the_mapping_table() -> None:
    """A field added to a settings model with no target would parse and vanish."""
    mapped = set(SETTINGS)
    for section in ("capture", "pdf", "ocr"):
        model = WebshotConfig.model_fields[section].annotation
        assert model is not None
        for key in model.model_fields:
            assert (section, key) in mapped, f"[{section}] {key} maps to no option"


def test_an_empty_config_overrides_nothing() -> None:
    assert option_overrides(WebshotConfig()) == {}
    assert capture_overrides(WebshotConfig()) == {}


def test_both_vocabularies_resolve_the_same_settings(tmp_path: Path) -> None:
    """The CLI and the MCP server read one table, so they cannot disagree.

    Spot-checks the three settings whose two spellings differ by more than a
    rename: an inverted flag, a unit conversion, and the one that also sets a
    second field.
    """
    path = write_config(
        tmp_path,
        "[capture]\nscroll = false\ntimeout = 45.0\n[ocr]\npsm = 6\n",
    )
    config = load_config(path, {})
    assert option_overrides(config) == {
        "no_scroll": True,
        "timeout": 45.0,
        "ocr_psm": 6,
    }
    assert capture_overrides(config) == {
        "scroll": False,
        "navigation_timeout_ms": 45_000,
        "ocr_psm": 6,
        "ocr_psm_explicit": True,
    }
    # And both spellings produce the same options object.
    from_cli = options([SOURCE, "--config", str(path)])
    from_mcp = CaptureOptions(
        source=from_cli.source,
        output=from_cli.output,
        ai_bundle_directory=from_cli.ai_bundle_directory,
        **capture_overrides(config),
    )
    for field in ("scroll", "navigation_timeout_ms", "ocr_psm", "ocr_psm_explicit"):
        assert getattr(from_cli, field) == getattr(from_mcp, field), field


def test_every_setting_can_be_given_through_the_environment() -> None:
    """The guide says every setting has a `WEBSHOT_<TABLE>_<KEY>` variable.

    That claim needed a test rather than a promise: `[ocr] psm` is an
    int-valued `Literal`, and pydantic does not coerce a string into one even
    in lax mode, so `WEBSHOT_OCR_PSM=11` was rejected as out of range while the
    documentation said it worked (docs/09 P4-12). List-valued settings are
    file-or-flag only by design and are named here so the exemption is explicit
    rather than a gap.
    """
    representative: dict[tuple[str, str], str] = {
        ("capture", "mode"): "faithful",
        ("capture", "selector"): "main",
        ("capture", "auto_selector"): "true",
        ("capture", "wait_for"): "#ready",
        ("capture", "delay"): "2.5",
        ("capture", "timeout"): "45",
        ("capture", "scroll"): "false",
        ("capture", "max_scrolls"): "7",
        ("capture", "scroll_delay"): "0.5",
        ("capture", "user_agent"): "agent/1",
        ("capture", "allow_http_errors"): "true",
        ("capture", "require_content"): "true",
        ("capture", "max_assets"): "12",
        ("capture", "css"): "~/style.css",
        ("capture", "ai_bundle"): "false",
        ("capture", "legacy_bundle"): "true",
        ("capture", "embed_bundle"): "false",
        ("capture", "embed_assets"): "true",
        ("capture", "local_paths"): "relative",
        ("capture", "videos"): "false",
        ("capture", "video_assets"): "true",
        ("pdf", "format"): "A4",
        ("pdf", "landscape"): "true",
        ("pdf", "margin"): "12mm",
        ("pdf", "scale"): "1.1",
        ("pdf", "media"): "screen",
        ("pdf", "prefer_css_page_size"): "true",
        ("pdf", "header_footer"): "false",
        ("pdf", "tagged"): "false",
        ("pdf", "outline"): "false",
        ("pdf", "pdfa"): "true",
        ("pdf", "validate_pdf"): "strict",
        ("ocr", "enabled"): "false",
        ("ocr", "require"): "true",
        ("ocr", "language"): "eng+deu",
        ("ocr", "psm"): "6",
        ("ocr", "engine"): "rapid",
        ("mcp", "roots"): "/srv/a",
        ("mcp", "output_root"): "/srv/out",
        ("mcp", "allow_private_networks"): "true",
        ("mcp", "read_asset_max_bytes"): "1024",
        ("mcp", "markdown_page_bytes"): "2048",
        ("mcp", "max_chunks"): "50",
    }
    #: Values a single environment variable cannot express unambiguously.
    file_or_flag_only = {("capture", "exclude"), ("mcp", "auth_profiles")}

    for section in ("capture", "pdf", "ocr", "mcp"):
        model = WebshotConfig.model_fields[section].annotation
        assert model is not None
        for key in model.model_fields:
            location = (section, key)
            if location in file_or_flag_only:
                continue
            assert location in representative, (
                f"[{section}] {key} has no environment round-trip case; add one "
                "or list it as file-or-flag only"
            )
            value = representative[location]
            config = load_config(None, {env_key(section, key): value})
            assert getattr(getattr(config, section), key) is not None, (
                f"{env_key(section, key)}={value!r} did not reach the config"
            )
