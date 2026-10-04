"""`webshot mcp` — parse the subcommand's flags and serve.

Kept apart from `server.py` so that starting the server and *being* the server
are separable: this module has no MCP import, and the SDK is loaded only when a
transport is actually about to run.  That matters because `mcp` lives in the
`webshot[mcp]` extra — `webshot doctor` and every capture must keep working on
an install that never asked for it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ..cli import setup_logging
from ..errors import EnvironmentFailure, UsageError
from ..settings import CONFIG_ENV_VAR, WebshotConfig, load_config
from .policy import Boundary

LOGGER = logging.getLogger("webshot.mcp")

#: From the checkout, never `pip install webshot[mcp]`: WebShot is not on PyPI,
#: and that name there belongs to an unrelated project.
INSTALL_HINT = (
    "The MCP server needs the official MCP SDK, which ships in the webshot[mcp]\n"
    "extra. Install webshot[mcp] from your checkout:\n"
    "    uv sync --extra mcp\n"
    "naming any other extras you use too, because uv sync removes the rest."
)


def build_mcp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webshot mcp",
        description=(
            "Serve WebShot's capture and bundle-reading tools to an MCP client."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="how the client connects; stdio is the only one",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "TOML settings file holding this server's [mcp] section — roots, "
            f"output root, network policy, auth profiles. {CONFIG_ENV_VAR} "
            "names it too; nothing is discovered from the working directory"
        ),
    )
    parser.add_argument(
        "--print-roots",
        action="store_true",
        help="print the resolved policy and exit, without serving",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log tool calls and refusals to stderr (never stdout, which is the protocol)",
    )
    return parser


def describe(config: WebshotConfig) -> str:
    """The resolved boundary, in the order an operator should check it.

    Computed from `policy` rather than from a running server, so the one
    command that exists to *audit* the boundary works on a machine that has not
    installed the MCP extra (docs/09 P4-9).
    """
    boundary = Boundary.of(config.mcp)
    settings = config.mcp
    roots = "\n                   ".join(str(root) for root in boundary.roots)
    return "\n".join(
        [
            f"output root        {boundary.output_root}",
            f"readable roots     {roots}",
            "private networks   "
            + ("allowed" if settings.allow_private_networks else "refused (default)"),
            f"read_asset cap     {settings.read_asset_max_bytes} bytes",
            f"markdown page      {settings.markdown_page_bytes} bytes",
            "auth profiles      "
            + (", ".join(sorted(settings.auth_profiles)) or "none configured"),
        ]
    )


def main(argv: Sequence[str]) -> int:
    arguments = build_mcp_parser().parse_args(list(argv))
    # stdio *is* the protocol channel: a log line on stdout corrupts the
    # JSON-RPC stream, so every diagnostic goes to stderr.
    setup_logging(arguments.verbose, stream=sys.stderr)
    config = load_config(arguments.config)
    if arguments.print_roots:
        # Deliberately before the SDK import: auditing what this server would
        # allow must not require the server to be installable.
        print(describe(config))
        return 0
    try:
        from .server import serve_stdio
    except ImportError as exc:  # pragma: no cover - exercised by the extra's absence
        raise EnvironmentFailure(f"{exc}\n\n{INSTALL_HINT}") from exc

    if arguments.transport != "stdio":  # pragma: no cover - argparse forbids it
        raise UsageError(f"Unsupported transport {arguments.transport!r}.")
    LOGGER.info("WebShot MCP server ready on stdio.")
    asyncio.run(serve_stdio(config))
    return 0
