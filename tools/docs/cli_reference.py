"""Generate `docs/guide/cli.md` from the argparse parsers themselves.

docs/09 S9 records what happens to a hand-maintained CLI table: it listed a
flag that did not exist and omitted fourteen that did.  The spec's
compatibility table is checked against the parser by
`tests/test_cli_contract.py`; the *reference page* goes one better and is
generated from it, with `tests/test_docs.py` failing if the checked-in file and
the parser have drifted.

Run `python tools/docs/cli_reference.py --write` after changing an option.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePath

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from webshot.bundle.manifest import SCHEMAS  # noqa: E402
from webshot.cli import build_parser  # noqa: E402
from webshot.doctor import build_doctor_parser  # noqa: E402
from webshot.errors import EXIT_CODES  # noqa: E402
from webshot.journey.cli import build_parser as build_journey_parser  # noqa: E402
from webshot.mcp_server.cli import build_mcp_parser  # noqa: E402

REFERENCE = REPO_ROOT / "docs" / "guide" / "cli.md"

PREAMBLE = """\
<!--
  GENERATED FILE — do not edit.
  Source: tools/docs/cli_reference.py, run against the argparse parsers in
  src/webshot/. Regenerate with `python tools/docs/cli_reference.py --write`;
  tests/test_docs.py fails if this file and the parsers have drifted.
-->

# CLI reference

Every option WebShot accepts, read off the parser rather than transcribed.
The compatibility story — which flags came from v2.2 and which are new — is in
[the specification](../04-spec.md#11-cli); this page is what the tool does
today.

```
webshot SOURCE [options]
webshot doctor [options]
webshot schema [NAME] [--write DIR]
webshot mcp [options]
webshot journey URL --dry-run [options]
```

`SOURCE` is an `http(s)` URL or a local file. Local documents that are not HTML
are converted first; the formats are listed in
[the quickstart](quickstart.md#local-documents).

Settings can also come from a TOML file and from the environment, in the order
**flags > `WEBSHOT_*` > `--config` file > defaults** — see
[Configuration](configuration.md).
"""

EPILOGUE = """\
## `webshot schema`

Prints one published JSON Schema, or lists them all when given no name.
`--write DIR` writes every schema into `DIR`, which is how the repository's
`schemas/` directory is regenerated after a contract model changes.

Available schemas: {schemas}.

## Exit codes

WebShot **never exits `1`**; each code means one thing
([specification §3](../04-spec.md#3-exit-codes-must)).
Scripts that tested `$? -eq 1` must test `$? -ne 0`. Interrupting a run with
Ctrl-C exits `130`, the shell's own SIGINT convention.

{exit_codes}

With `--report`, a run writes its QA report whether or not it succeeded: the
file carries the same `exit_code` the process returned, plus an `error` string
that is absent entirely from a successful report.
"""


def _exit_code_table() -> str:
    """The taxonomy as a table, read from the code rather than typed beside it.

    This page shipped a hand-written copy, and by the time Phase 5.1 finished
    the migration the copy disagreed with the constant in two rows *and* still
    told readers that unclassified paths exit 1 — which had stopped being true.
    A generated table cannot do that (docs/09 P5-8).
    """
    rows = "\n".join(
        f"| {code} | {meaning} |" for code, meaning in sorted(EXIT_CODES.items())
    )
    return f"| Code | Meaning |\n|---|---|\n{rows}"


def _option_row(action: argparse.Action) -> tuple[str, str, str, str] | None:
    """One table row: flags, argument, default, help."""
    if action.dest == "help" or not action.option_strings:
        return None
    flags = ", ".join(f"`{option}`" for option in action.option_strings)
    if action.choices:
        argument = " \\| ".join(f"`{choice}`" for choice in action.choices)
    elif action.nargs == 0:  # a flag: store_true, store_const, version
        argument = "—"
    else:
        argument = f"`{action.metavar or action.dest.upper()}`"
    default = ""
    if action.default is False:
        default = "off"
    elif isinstance(action.default, PurePath):
        # `str()` of a path is the platform's spelling, so a Windows checkout
        # regenerated this page with `output\\pdf` and failed the drift test
        # (docs/09 P10-24). The page documents one CLI for every platform.
        default = f"`{action.default.as_posix()}`"
    elif action.default not in (None, argparse.SUPPRESS, [], "") and action.nargs != 0:
        default = f"`{action.default}`"
    help_text = (action.help or "").replace("(default: %(default)s)", "").strip()
    help_text = help_text.replace("|", "\\|")
    return flags, argument, default, help_text


def _table(parser: argparse.ArgumentParser) -> str:
    rows = [row for row in map(_option_row, parser._actions) if row is not None]
    lines = ["| Option | Value | Default | What it does |", "|---|---|---|---|"]
    lines += [
        f"| {flags} | {argument} | {default} | {help_text} |"
        for flags, argument, default, help_text in rows
    ]
    return "\n".join(lines)


def _positionals(parser: argparse.ArgumentParser) -> str:
    rows = [
        f"| `{action.dest}` | {(action.help or '').strip()} |"
        for action in parser._actions
        if not action.option_strings
    ]
    if not rows:
        return ""
    return "\n".join(["| Argument | What it is |", "|---|---|", *rows])


def render() -> str:
    """The whole reference page, exactly as it is stored."""
    capture = build_parser()
    sections = [PREAMBLE, "\n## `webshot SOURCE`\n\n"]
    positionals = _positionals(capture)
    if positionals:
        sections.append(positionals + "\n\n")
    sections.append(_table(capture) + "\n")
    sections.append("\n## `webshot doctor`\n\n")
    sections.append(
        "Checks that this machine can capture, recognize, and publish, and "
        "prints a one-line fix for anything it cannot. Exit `0` when every "
        "required check passes.\n\n"
    )
    sections.append(_table(build_doctor_parser()) + "\n")
    sections.append("\n## `webshot mcp`\n\n")
    sections.append(
        "Serves WebShot's tools to an MCP client over stdio. The rules the "
        "server runs under, and how to configure them, are in the "
        "[MCP guide](mcp.md).\n\n"
    )
    sections.append(_table(build_mcp_parser()) + "\n")
    sections.append("\n## `webshot journey`\n\n")
    sections.append(
        "Walks a Continu training journey, whose modules have no URLs of their "
        "own. It enumerates the tree twice and diffs the two walks, "
        "then captures exactly the module set that enumeration produced — a "
        "PDF and bundle per module, five directory levels deep. Every module "
        "typed `assessment` is recorded and never opened. `--dry-run` stops "
        "after the enumeration.\n\n"
    )
    journey = build_journey_parser()
    journey_positionals = _positionals(journey)
    if journey_positionals:
        sections.append(journey_positionals + "\n\n")
    sections.append(_table(journey) + "\n\n")
    sections.append(
        EPILOGUE.format(
            schemas=", ".join(f"`{name}`" for name in sorted(SCHEMAS)),
            exit_codes=_exit_code_table(),
        )
    )
    return "".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help=f"write {REFERENCE.name} in place"
    )
    arguments = parser.parse_args(argv)
    page = render()
    if arguments.write:
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(page, encoding="utf-8")
        print(f"wrote {REFERENCE.relative_to(REPO_ROOT)}")
        return 0
    sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
