"""The documentation has to describe the thing that exists.

Three checks, all cheap, each catching a class of rot the site build cannot:

* the CLI reference page is **generated** from the parsers, so a new option that
  is not in it is a failing test rather than a page that quietly goes stale
  (docs/09 S9 records what the hand-maintained version cost);
* the MCP guide publishes the tool contract, which is frozen after v3.0, so the
  names it documents must be the names the server registers;
* `AGENTS.md` is a generated mirror of `CLAUDE.md`, and an agent reading the
  stale name would get rules this repository has already moved on from.

`mkdocs build --strict` is a separate CI job: it is what proves the links
resolve, and it needs the `docs` extra.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.docs import agent_instructions  # noqa: E402
from tools.docs.cli_reference import REFERENCE, _option_row, render  # noqa: E402

GUIDE = REPO_ROOT / "docs" / "guide"


def test_the_cli_reference_matches_the_parsers() -> None:
    assert REFERENCE.is_file(), f"{REFERENCE} is missing"
    assert REFERENCE.read_text(encoding="utf-8") == render(), (
        "docs/guide/cli.md no longer matches the argparse parsers. "
        "Regenerate it: python tools/docs/cli_reference.py --write"
    )


def test_a_path_default_is_documented_the_same_on_every_platform() -> None:
    """A Windows checkout rendered `output\\pdf` and failed the test above
    (docs/09 P10-24); the page is one document for every platform."""
    from pathlib import PureWindowsPath

    parser = argparse.ArgumentParser()
    action = parser.add_argument("--output", default=PureWindowsPath("output/pdf"))
    row = _option_row(action)
    assert row is not None
    assert row[2] == "`output/pdf`"


def test_the_mcp_guide_documents_exactly_the_registered_tools() -> None:
    """Tool names are an API contract, so the page and the server must agree."""
    pytest.importorskip("mcp.server.mcpserver", reason="needs webshot[mcp]")
    from webshot.mcp_server.server import (
        TOOL_NAMES,
        build_server,
        registered_tool_names,
    )
    from webshot.settings import WebshotConfig

    registered = set(asyncio.run(registered_tool_names(build_server(WebshotConfig()))))
    assert registered == set(TOOL_NAMES)

    guide = (GUIDE / "mcp.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^### `([a-z_]+)`$", guide, flags=re.MULTILINE))
    assert documented == registered, (
        "docs/guide/mcp.md and the server disagree about the tool set: "
        f"only in the guide {sorted(documented - registered)}, "
        f"only in the server {sorted(registered - documented)}"
    )


def test_the_mcp_guide_states_that_returned_content_is_untrusted() -> None:
    """The one instruction an integrating client has to take away from the page.

    Asserted rather than trusted to review: it is the mitigation for the whole
    class of attack this server's guardrails exist to contain, and a docs edit
    that softened it would otherwise go unnoticed.
    """
    guide = (GUIDE / "mcp.md").read_text(encoding="utf-8")
    assert "## Returned page content is untrusted data" in guide
    assert "never instructions" in guide or "not instructions" in guide


def test_every_guide_page_is_in_the_site_navigation() -> None:
    """A page nobody linked is a page nobody reads."""
    navigation = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for page in sorted(GUIDE.glob("*.md")):
        relative = f"guide/{page.name}"
        assert relative in navigation, f"{relative} is not in mkdocs.yml's nav"


def test_the_agent_instructions_mirror_is_in_sync() -> None:
    """`AGENTS.md` is `CLAUDE.md`, and the two must not have drifted."""
    assert agent_instructions.check() == [], (
        "AGENTS.md no longer matches CLAUDE.md. Edit CLAUDE.md, then regenerate: "
        "uv run python tools/docs/agent_instructions.py --write"
    )


def test_the_mirror_check_fails_when_a_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted mirror is the loudest drift there is, so it cannot read as pass.

    The guard written to catch a gate that could not fail is itself a gate that
    could not fail; this repository has shipped that exact shape once already.
    """
    present = tmp_path / "CLAUDE.md"
    present.write_text("rules", encoding="utf-8")
    monkeypatch.setattr(agent_instructions, "SOURCE", present)
    monkeypatch.setattr(agent_instructions, "MIRROR", tmp_path / "AGENTS.md")

    complaints = agent_instructions.check()
    assert complaints, "a missing AGENTS.md reported as in sync"
    assert "AGENTS.md" in complaints[0]


def test_the_mirror_check_reports_a_difference_the_diff_cannot_show() -> None:
    """Byte-equal comparison, byte-level diagnostic.

    Line endings and a missing final newline differ as bytes while every line
    compares equal, so a unified diff of the lines comes back empty. Failing
    with an empty diff would tell the reader nothing about what to fix.
    """
    complaints = agent_instructions.differences(b"same line\n", b"same line\r\n")
    assert complaints, "a line-ending difference reported as in sync"
    assert any("byte 9" in line for line in complaints), complaints
    assert any("lengths" in line for line in complaints), complaints

    # The other case the diagnostic names: one file a prefix of the other, so
    # the scan finds no differing byte and the reported offset is the point
    # where the shorter file stopped.
    truncated = agent_instructions.differences(b"same line\n", b"same line")
    assert any("byte 9" in line for line in truncated), truncated
