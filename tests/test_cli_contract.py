"""The CLI surface and the specification's flag table must agree.

docs/09-spike-report.md S9 records why this exists: the hand-written table in
the spec once listed a flag that did not exist (`--custom-css`) and omitted
fourteen that did.  A table nobody checks is worse than no table.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from webshot import build_parser

SPEC = Path(__file__).resolve().parents[1] / "docs" / "04-spec.md"
FLAG_RE = re.compile(r"`(--[a-z0-9][a-z0-9-]*)`")
IGNORED = {"--help"}


def parser_flags() -> set[str]:
    flags = set()
    for action in build_parser()._actions:
        flags.update(
            option for option in action.option_strings if option.startswith("--")
        )
    return flags - IGNORED


def spec_table_rows() -> list[str]:
    """The rows of the '#### Flag compatibility table' in docs/04-spec.md."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if "Flag compatibility table" in line
    )
    rows = []
    for line in lines[start:]:
        if line.startswith("### "):
            break
        if line.startswith("|"):
            rows.append(line)
    return rows


def test_every_cli_option_is_documented() -> None:
    documented = {flag for row in spec_table_rows() for flag in FLAG_RE.findall(row)}
    undocumented = parser_flags() - documented
    assert not undocumented, (
        "these options exist but are missing from the flag table in "
        f"docs/04-spec.md §1.1: {sorted(undocumented)}"
    )


def test_no_documented_current_flag_is_missing_from_the_cli() -> None:
    """Rows not marked *(new)* describe flags v2.2 already ships."""
    invented = set()
    for row in spec_table_rows():
        if "(new)" in row or row.startswith("|---") or "v2.2 flag" in row:
            continue
        invented.update(set(FLAG_RE.findall(row)) - parser_flags())
    assert not invented, (
        "docs/04-spec.md §1.1 lists flags that the CLI does not implement: "
        f"{sorted(invented)}"
    )


def _all_parsers() -> dict[str, argparse.ArgumentParser]:
    from webshot.doctor import build_doctor_parser
    from webshot.journey.cli import build_parser as build_journey_parser
    from webshot.mcp_server.cli import build_mcp_parser

    return {
        "webshot": build_parser(),
        "webshot doctor": build_doctor_parser(),
        "webshot mcp": build_mcp_parser(),
        "webshot journey": build_journey_parser(),
    }


def test_main_help_names_every_subcommand() -> None:
    """`doctor` is the first thing a new user is told to run, and `--help`
    did not mention it, nor any other subcommand."""
    from webshot.cli import SUBCOMMAND_HELP, SUBCOMMANDS

    assert set(SUBCOMMAND_HELP) == set(SUBCOMMANDS), (
        f"subcommands {sorted(SUBCOMMANDS)} but --help describes "
        f"{sorted(SUBCOMMAND_HELP)}"
    )
    text = build_parser().format_help()
    missing = [name for name in SUBCOMMANDS if f"webshot {name} " not in text]
    assert not missing, f"--help does not list {missing}:\n{text}"


def test_help_never_prints_a_default_of_none() -> None:
    """argparse's `None` means "not given"; `--output` has a computed default."""
    # Joined, because argparse wraps to the terminal's width.
    text = " ".join(build_parser().format_help().split())
    assert "(default: None)" not in text, text
    assert text.count("(default: output path with an .ai suffix)") == 1, text
    assert "output/pdf/NAME.pdf" in text, text


def test_every_option_has_help() -> None:
    """An option with no help prints as a bare flag, which `--media` and
    `--allow-http-errors` did."""
    silent = {
        f"{name} {action.option_strings[0]}"
        for name, parser in _all_parsers().items()
        for action in parser._actions
        if action.option_strings and not (action.help or "").strip()
    }
    assert not silent, f"options without help: {sorted(silent)}"


def test_help_cites_no_design_document() -> None:
    """Help says what an option does; `docs/04-spec.md §1.1` means nothing to
    a user, and neither does how the tool used to behave."""
    cited = {
        f"{name} {action.option_strings[0] if action.option_strings else action.dest}"
        for name, parser in _all_parsers().items()
        for action in parser._actions
        if re.search(r"docs/\d|§|before this feature", action.help or "")
    }
    assert not cited, f"help text that cites a design document: {sorted(cited)}"
