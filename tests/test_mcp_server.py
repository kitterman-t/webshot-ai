"""The MCP server, exercised through a real client session.

docs/05-implementation-plan.md's Phase 4 acceptance is that "an MCP client can
capture a fixture URL and read markdown + chunks end-to-end in CI", and that
every guardrail has a denied case.  Both live here, driven through
`mcp.Client`, so what is tested is what a client actually gets: schema
validation, structured content, and errors as the protocol delivers them —
not WebShot functions called directly.

The happy path runs a real browser against a fixture HTTP server on loopback,
which is exactly the target the default policy refuses; the harness turns
`allow_private_networks` on, and `test_capture_refuses_a_loopback_target_by_default`
proves that a server which has not is still closed.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

mcp_client = pytest.importorskip("mcp.client.client", reason="needs webshot[mcp]")

from mcp import Client, StdioServerParameters, stdio_client  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from conftest import serve  # noqa: E402
from webshot.config import CaptureResult  # noqa: E402
from webshot.mcp_server import server as mcp_server  # noqa: E402
from webshot.mcp_server import service as mcp_service  # noqa: E402
from webshot.mcp_server.results import UNTRUSTED_NOTICE  # noqa: E402
from webshot.settings import McpServerSettings, WebshotConfig  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE_PAGE = "professional_page.html"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    """Serve `tests/fixtures/` on loopback. No test may touch the internet."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES)
    )
    with serve(handler) as origin:  # type: ignore[arg-type]
        yield origin


def config_for(
    output_root: Path, *, allow_private: bool = False, **settings: Any
) -> WebshotConfig:
    return WebshotConfig(
        mcp=McpServerSettings(
            output_root=output_root,
            allow_private_networks=allow_private,
            **settings,
        )
    )


def staged_result(options: Any, **overrides: Any) -> CaptureResult:
    """What a stubbed `convert_url_to_pdf` leaves behind, plus its result.

    Three tests need a capture that "happened" without a browser; they differ
    only in the final URL and how long it took, so the boilerplate is here.
    """
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_bytes(b"%PDF-1.7 staged")
    return CaptureResult(
        **{
            "source": options.source,
            "final_url": "https://public.example/article",
            "output": str(options.output),
            "title": "Article",
            "pages": 1,
            "bytes": 15,
            "http_status": 200,
            "content_type": "text/html",
            "duration_seconds": 0.1,
            "captured_at": datetime.now(UTC).isoformat(),
            **overrides,
        }
    )


def public_resolver(host: str, port: int | None) -> list[tuple]:
    """Every name resolves to one public address, so no test needs DNS."""
    return [(2, 1, 6, "", ("93.184.216.34", port or 0))]


def call(
    config: WebshotConfig,
    tool: str,
    arguments: dict[str, Any],
    *,
    resolver: Any = None,
) -> Any:
    """One tool call through a client session, returning the result object.

    `Client(server)` is the SDK's in-process transport: a real session, real
    schema validation, real structured content, without a subprocess. The
    subprocess path is proven once, separately, by
    `test_the_webshot_mcp_subcommand_serves_over_stdio`.
    """

    async def run() -> Any:
        async with Client(mcp_server.build_server(config, resolver=resolver)) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def denied(
    config: WebshotConfig,
    tool: str,
    arguments: dict[str, Any],
    *,
    resolver: Any = None,
) -> str:
    """Assert a call was refused, and return the message the agent sees.

    Two refusal shapes count, and both are refusals: a tool error
    (`is_error`, which is how a policy denial reaches the model) and a
    protocol error (`MCPError`, which is how a malformed request does).
    """
    try:
        result = call(config, tool, arguments, resolver=resolver)
    except MCPError as protocol_error:  # pragma: no cover - transport-level refusal
        return str(protocol_error)
    assert result.is_error, f"{tool} was expected to be refused but succeeded"
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def structured(config: WebshotConfig, tool: str, arguments: dict[str, Any]) -> Any:
    result = call(config, tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


# --------------------------------------------------------------------------- #
# The tool contract
# --------------------------------------------------------------------------- #


def test_the_server_publishes_exactly_the_specified_tools(tmp_path: Path) -> None:
    """docs/04-spec.md §1.3's table, which is frozen after v3.0."""

    async def run() -> list[str]:
        return await mcp_server.registered_tool_names(
            mcp_server.build_server(config_for(tmp_path))
        )

    assert asyncio.run(run()) == sorted(mcp_service.TOOL_NAMES)


def test_capture_accepts_no_output_path_and_no_credentials(tmp_path: Path) -> None:
    """Guardrails (b) and (d), proven structurally rather than by validation.

    There is no parameter to reject because there is no parameter: output goes
    where the server's config says, and the only authentication input is a
    profile *name*.
    """

    async def schema() -> dict[str, Any]:
        server = mcp_server.build_server(config_for(tmp_path))
        tools = {tool.name: tool for tool in await server.list_tools()}
        return tools["capture"].input_schema

    properties = set(asyncio.run(schema())["properties"])
    forbidden = {
        "output",
        "output_path",
        "ai_bundle_dir",
        "ai_bundle_directory",
        "storage_state",
        "storage_state_json",
        "cookies",
        "password",
        "interactive_auth",
    }
    assert not (properties & forbidden)
    assert "auth_profile" in properties


def test_every_content_bearing_result_carries_the_untrusted_notice() -> None:
    """The one instruction a client must apply to everything this server returns."""
    from webshot.mcp_server.results import (
        AssetList,
        CaptureSummary,
        ChunkQuery,
        MarkdownPage,
    )

    for model in (CaptureSummary, MarkdownPage, ChunkQuery, AssetList):
        assert model.model_fields["notice"].default == UNTRUSTED_NOTICE
    assert "UNTRUSTED DATA" in mcp_server.INSTRUCTIONS


def test_only_a_webshot_error_reaches_the_model_with_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_refusals()` translates WebShot's own errors and nothing else.

    The refusal tests below prove a `WebShotError` keeps its text. This is the
    other half: an exception WebShot did not anticipate is a crash, and its
    text — a path, a library's internals — stays in the server log rather
    than reaching the model (docs/09 P10-17).
    """

    def crash(self: Any) -> Any:
        raise RuntimeError("internal detail at /private/state")

    monkeypatch.setattr(mcp_service.WebshotService, "doctor", crash)
    message = denied(config_for(tmp_path), "doctor", {})
    assert "doctor" in message
    assert "internal detail" not in message


# --------------------------------------------------------------------------- #
# (c) Network policy, over the wire
# --------------------------------------------------------------------------- #


def test_capture_refuses_a_loopback_target_by_default(
    tmp_path: Path, fixture_site: str
) -> None:
    message = denied(
        config_for(tmp_path),
        "capture",
        {"source": f"{fixture_site}/{FIXTURE_PAGE}"},
    )
    assert "allow_private_networks" in message
    assert not list(tmp_path.glob("*.pdf"))


def test_capture_refuses_the_cloud_metadata_address(tmp_path: Path) -> None:
    message = denied(
        config_for(tmp_path),
        "capture",
        {"source": "http://169.254.169.254/latest/meta-data/"},
    )
    assert "link-local" in message


# --------------------------------------------------------------------------- #
# (a) Filesystem confinement, over the wire
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("read_markdown", {"bundle": "/etc"}),
        ("query_chunks", {"bundle": "/etc"}),
        ("get_manifest", {"bundle": "/etc"}),
        ("list_assets", {"bundle": "/etc"}),
        ("read_asset", {"bundle": "/etc", "asset_id": "asset-1"}),
    ],
)
def test_every_reader_refuses_a_path_outside_the_roots(
    tmp_path: Path, tool: str, arguments: dict[str, Any]
) -> None:
    assert "allowed roots" in denied(config_for(tmp_path), tool, arguments)


def test_a_reader_refuses_dot_dot_traversal_out_of_a_root(tmp_path: Path) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    (tmp_path / "secret").mkdir()
    message = denied(
        config_for(output_root),
        "get_manifest",
        {"bundle": str(output_root / ".." / "secret")},
    )
    assert "allowed roots" in message


def test_a_reader_refuses_a_symlink_that_leaves_a_root(tmp_path: Path) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "manifest.json").write_text("{}", encoding="utf-8")
    (output_root / "escape.ai").symlink_to(secret)
    assert "allowed roots" in denied(
        config_for(output_root),
        "get_manifest",
        {"bundle": str(output_root / "escape.ai")},
    )


# --------------------------------------------------------------------------- #
# (d)/(e) Credentials, over the wire
# --------------------------------------------------------------------------- #


def test_raw_storage_state_content_is_rejected(
    tmp_path: Path, fixture_site: str
) -> None:
    """The case docs/04-spec.md §1.3 names: profile *names* only.

    Two layers have to hold. The schema has no `storage_state` parameter at
    all, and an undeclared argument is a protocol error rather than something
    quietly dropped; and `auth_profile` refuses anything not shaped like a
    name, so the content cannot arrive through the parameter that does exist.
    """
    schema_error = denied(
        config_for(tmp_path, allow_private=True),
        "capture",
        {
            "source": f"{fixture_site}/{FIXTURE_PAGE}",
            "storage_state": '{"cookies": [{"name": "session", "value": "secret"}]}',
        },
    )
    assert "storage_state" in schema_error
    assert "no credential-bearing parameters" in schema_error
    assert not list(tmp_path.rglob("*.pdf")), "the refused call still captured"

    for smuggled_value in (
        '{"cookies": [{"name": "session", "value": "secret"}]}',
        "/Users/someone/.webshot/profiles/work",
        "cookies=session%3Dsecret",
    ):
        message = denied(
            config_for(tmp_path, allow_private=True),
            "capture",
            {
                "source": f"{fixture_site}/{FIXTURE_PAGE}",
                "auth_profile": smuggled_value,
            },
        )
        assert message, f"{smuggled_value!r} produced no explanation"
        assert not list(tmp_path.rglob("*.pdf")), "the refused call still captured"
    # The path-shaped value reaches WebShot's own rule, which states it.
    assert "never storage-state" in denied(
        config_for(tmp_path, allow_private=True),
        "capture",
        {
            "source": f"{fixture_site}/{FIXTURE_PAGE}",
            "auth_profile": "/Users/someone/.webshot/profiles/work",
        },
    )


def test_an_unconfigured_profile_name_is_refused(
    tmp_path: Path, fixture_site: str
) -> None:
    message = denied(
        config_for(tmp_path, allow_private=True),
        "capture",
        {"source": f"{fixture_site}/{FIXTURE_PAGE}", "auth_profile": "work"},
    )
    assert "No authentication profile named 'work'" in message


def test_a_profile_needing_an_interactive_first_run_is_refused(
    tmp_path: Path, fixture_site: str
) -> None:
    profile = tmp_path / "profiles" / "work"
    profile.mkdir(parents=True)
    config = config_for(
        tmp_path / "out", allow_private=True, auth_profiles={"work": profile}
    )
    message = denied(
        config,
        "capture",
        {"source": f"{fixture_site}/{FIXTURE_PAGE}", "auth_profile": "work"},
    )
    assert "--interactive-auth" in message
    assert "cannot open a headed browser" in message


# --------------------------------------------------------------------------- #
# (b)/(c) Nothing is published unless the whole capture was allowed
# --------------------------------------------------------------------------- #


def test_a_capture_that_redirected_internally_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardrail (c)'s teeth: `final_url` is checked *before* publication.

    The pipeline is stubbed rather than driven, because no offline fixture can
    be both publicly addressable and able to redirect to link-local. What is
    under test is the staging gate — that a capture whose final URL fails the
    policy leaves nothing where a consumer looks.
    """
    output_root = tmp_path / "out"

    async def capture_that_redirects(options: Any) -> CaptureResult:
        bundle = options.ai_bundle_directory
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "manifest.json").write_text("{}", encoding="utf-8")
        return staged_result(
            options,
            final_url="http://169.254.169.254/latest/meta-data/",
            title="redirected",
        )

    monkeypatch.setattr(mcp_service, "convert_url_to_pdf", capture_that_redirects)
    message = denied(
        config_for(output_root),
        "capture",
        {"source": "https://public.example/start"},
        resolver=public_resolver,
    )
    assert "redirect target" in message
    published = [entry for entry in output_root.iterdir() if entry.suffix != ".lock"]
    assert published == [], "a refused capture left artifacts behind"


def test_captures_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardrail (f): one capture at a time per server instance."""
    concurrent = 0
    peak = 0

    async def slow_capture(options: Any) -> CaptureResult:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        try:
            await asyncio.sleep(0.05)
            return staged_result(options)
        finally:
            concurrent -= 1

    monkeypatch.setattr(mcp_service, "convert_url_to_pdf", slow_capture)
    backend = mcp_service.WebshotService(
        config_for(tmp_path / "out", allow_private=True)
    )

    async def run() -> None:
        await asyncio.gather(
            *(backend.capture(source=f"http://127.0.0.1/{index}") for index in range(4))
        )

    asyncio.run(run())
    assert peak == 1, f"{peak} captures ran at once"


# --------------------------------------------------------------------------- #
# The end-to-end path
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_a_client_captures_a_fixture_url_and_reads_the_bundle(
    tmp_path: Path, fixture_site: str
) -> None:
    """Phase 4's acceptance criterion, in one session.

    The client never touches the filesystem: it captures, is told where the
    bundle is, and reads markdown, chunks, the manifest, and an asset back
    through the tools.
    """
    output_root = tmp_path / "captures"
    config = config_for(output_root, allow_private=True)
    source = f"{fixture_site}/{FIXTURE_PAGE}"

    async def session() -> dict[str, Any]:
        async with Client(mcp_server.build_server(config)) as client:
            collected: dict[str, Any] = {}
            progress: list[tuple[float, str | None]] = []

            async def note(
                value: float, total: float | None, message: str | None
            ) -> None:
                progress.append((value, message))

            captured = await client.call_tool(
                "capture", {"source": source}, progress_callback=note
            )
            assert not captured.is_error, captured.content
            collected["capture"] = captured.structured_content
            collected["progress"] = progress
            bundle = collected["capture"]["bundle"]
            for tool, arguments in (
                ("read_markdown", {"bundle": bundle}),
                ("query_chunks", {"bundle": bundle, "query": "revenue", "limit": 5}),
                ("get_manifest", {"bundle": bundle}),
                ("list_assets", {"bundle": bundle}),
                ("doctor", {}),
            ):
                result = await client.call_tool(tool, arguments)
                assert not result.is_error, (tool, result.content)
                collected[tool] = result.structured_content
            asset_id = collected["list_assets"]["assets"][0]["id"]
            asset = await client.call_tool(
                "read_asset", {"bundle": bundle, "asset_id": asset_id}
            )
            assert not asset.is_error, asset.content
            collected["read_asset"] = asset.structured_content
            return collected

    results = asyncio.run(session())

    capture = results["capture"]
    assert capture["final_url"].startswith(fixture_site)
    assert Path(capture["pdf"]).is_file()
    assert Path(capture["pdf"]).parent == output_root.resolve()
    assert Path(capture["bundle"]).parent == output_root.resolve()
    assert capture["bundle_format"] == 3
    assert capture["counts"]["chunks"] > 0
    assert capture["manifest_sha256"]
    assert capture["notice"] == UNTRUSTED_NOTICE
    # Nothing is left in the staging directory the capture ran in.
    # No staging directory left behind. `.lock` files are excluded on purpose:
    # §5.1's lock file persists by design, because deleting it on release would
    # let a second process lock a *fresh* inode while the first still held the
    # old one (docs/09 P5-10).
    assert not [
        entry
        for entry in output_root.iterdir()
        if entry.name.startswith(".") and entry.suffix != ".lock"
    ]
    assert results["progress"], "no progress notifications were delivered"

    markdown = results["read_markdown"]
    assert "Quarterly" in markdown["text"] or markdown["bytes_total"] > 0
    assert markdown["complete"] is True

    chunks = results["query_chunks"]
    assert chunks["matched"] >= 1
    assert chunks["returned"] == len(chunks["chunks"])
    assert all("revenue" in chunk["text"].lower() for chunk in chunks["chunks"])
    # The provenance fields, not just the text: they are read through the
    # published chunk contract, so a bundle-format rename has to break here
    # rather than reach an agent as a silent null.
    first = chunks["chunks"][0]
    assert first["id"]
    assert first["locator"]
    assert first["kind"] in {"text", "table", "figure"}
    assert isinstance(first["headings"], list)

    # Wrapped, like every other content-returning tool: `title`,
    # `page_metadata` and `warnings` are page text, and this tool alone
    # returned them with neither the UNTRUSTED marker in its description nor a
    # result-level notice (docs/09 P8-56).
    assert results["get_manifest"]["notice"]
    manifest = results["get_manifest"]["manifest"]
    assert manifest["bundle_format"] == 3
    assert manifest["content"]["ocr_words"] >= 0

    asset = results["read_asset"]
    assert asset["complete"] is True
    assert len(base64.b64decode(asset["data_base64"])) == asset["bytes_total"]
    assert results["doctor"]["checks"]


@pytest.mark.browser
def test_read_markdown_and_read_asset_page_within_their_caps(
    tmp_path: Path, fixture_site: str
) -> None:
    """A cap the caller cannot raise, and a `next_offset` that actually resumes."""
    output_root = tmp_path / "captures"
    config = config_for(output_root, allow_private=True, markdown_page_bytes=64)
    source = f"{fixture_site}/{FIXTURE_PAGE}"

    async def session() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with Client(mcp_server.build_server(config)) as client:
            captured = await client.call_tool("capture", {"source": source})
            assert not captured.is_error, captured.content
            bundle = captured.structured_content["bundle"]
            first = await client.call_tool("read_markdown", {"bundle": bundle})
            second = await client.call_tool(
                "read_markdown",
                {"bundle": bundle, "offset": first.structured_content["next_offset"]},
            )
            # A caller asking for more than the server's page size gets the
            # server's page size.
            greedy = await client.call_tool(
                "read_markdown", {"bundle": bundle, "limit": 10_000_000}
            )
            return (
                first.structured_content,
                second.structured_content,
                greedy.structured_content,
            )

    first, second, greedy = asyncio.run(session())
    assert first["complete"] is False
    assert first["next_offset"] == 64
    assert len(first["text"].encode("utf-8")) <= 64
    assert second["offset"] == 64
    assert greedy["next_offset"] == 64, "the caller's limit must not raise the cap"


# --------------------------------------------------------------------------- #
# The real subcommand
# --------------------------------------------------------------------------- #


def test_the_webshot_mcp_subcommand_serves_over_stdio(tmp_path: Path) -> None:
    """`webshot mcp` really is an MCP server, spawned the way a client spawns it."""
    config = tmp_path / "webshot.toml"
    config.write_text(f'[mcp]\noutput_root = "{tmp_path / "out"}"\n', encoding="utf-8")

    async def run() -> list[str]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "webshot", "mcp", "--config", str(config)],
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return sorted(tool.name for tool in listed.tools)

    assert asyncio.run(asyncio.wait_for(run(), timeout=90)) == sorted(
        mcp_service.TOOL_NAMES
    )


def test_print_roots_reports_the_resolved_policy(tmp_path: Path) -> None:
    """An operator has to be able to see the boundary without reading the code."""
    config = tmp_path / "webshot.toml"
    config.write_text(
        f'[mcp]\noutput_root = "{tmp_path / "out"}"\nroots = ["{tmp_path / "shared"}"]\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            "mcp",
            "--config",
            str(config),
            "--print-roots",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
    )
    assert str(tmp_path / "out") in completed.stdout
    assert str(tmp_path / "shared") in completed.stdout
    assert "refused (default)" in completed.stdout


def test_the_capture_tool_reports_progress_and_publishes_where_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardrail (b): the destination is the server's, computed from its root."""
    output_root = tmp_path / "out"

    async def fake_capture(options: Any) -> CaptureResult:
        return staged_result(options)

    monkeypatch.setattr(mcp_service, "convert_url_to_pdf", fake_capture)
    result = call(
        config_for(output_root),
        "capture",
        {"source": "https://public.example/article"},
        resolver=public_resolver,
    )
    assert not result.is_error, result.content
    published = Path(result.structured_content["pdf"])
    assert published.parent == output_root.resolve()
    assert published.is_file()
    assert json.loads(json.dumps(result.structured_content))["warnings"] == []


@pytest.mark.browser
def test_a_captured_page_cannot_reach_an_internal_address(tmp_path: Path) -> None:
    """The hole a top-level-URL check leaves open, closed.

    Guarding only the URL the caller asked for guards the front door: a page —
    local or remote — can `fetch()` or iframe an internal address, and the
    response lands in the capture the agent then reads back. Angle E of the
    max-effort review demonstrated exactly that end to end (docs/09 P4-13).

    The assertion is on the *internal server*, not on the captured text: a
    subresource that arrives after the snapshot leaves no trace in the markdown
    either way, so "the leak text is absent" would pass without the filter. What
    cannot happen quietly is the request itself.

    The source here is a local file inside the roots, so the source check passes
    and the page is what tries to reach loopback.
    """
    received: list[str] = []

    class Recorder(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(self.path)
            body = b"<html><body>INTERNAL-SECRET</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    with serve(Recorder) as origin:
        root = tmp_path / "root"
        root.mkdir()
        page = root / "exfil.html"
        page.write_text(
            f"""<!doctype html><html><body><h1>outer</h1>
            <img src="{origin}/img">
            <iframe src="{origin}/frame"></iframe>
            <script>fetch("{origin}/fetch");</script>
            </body></html>""",
            encoding="utf-8",
        )
        config = WebshotConfig(
            mcp=McpServerSettings(output_root=tmp_path / "out", roots=[root])
        )

        async def run() -> Any:
            async with Client(mcp_server.build_server(config)) as client:
                captured = await client.call_tool("capture", {"source": str(page)})
                assert not captured.is_error, captured.content
                bundle = captured.structured_content["bundle"]
                read = await client.call_tool("read_markdown", {"bundle": bundle})
                return read.structured_content

        markdown = asyncio.run(run())["text"]

    assert received == [], f"the page reached the internal server: {received}"
    assert "outer" in markdown, "the capture itself must still work"
    assert "INTERNAL-SECRET" not in markdown
