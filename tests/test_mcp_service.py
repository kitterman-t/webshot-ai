"""The MCP tools' behaviour, without the MCP SDK.

`tests/test_mcp_server.py` drives the same operations through a real client
session and needs `webshot[mcp]`; this file needs none of it, which is the
point of `service.py` being protocol-free (docs/09 P4-9).  Staging, publication,
paging, and the bundle readers are WebShot behaviour, and behaviour that can
only be tested behind an optional extra is behaviour that stops being tested.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest

from webshot.errors import WebShotError
from webshot.mcp_server.policy import Boundary, Denied
from webshot.mcp_server.service import BundleNotFound, WebshotService
from webshot.settings import McpServerSettings, WebshotConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_BUNDLE = REPO_ROOT / "tests" / "golden" / "article-clean" / "bundle"


def service_for(output_root: Path, **settings: Any) -> WebshotService:
    return WebshotService(
        WebshotConfig(mcp=McpServerSettings(output_root=output_root, **settings))
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A real recorded bundle, copied where the service is allowed to read it.

    The golden corpus is the only bundle in the repository guaranteed to exist
    and to be in the current format, so the readers are tested against real
    records rather than a hand-built fake that could drift from them.

    The one thing the goldens do not carry is the raster bytes — they are
    recorded as dimensions and checksums in `bundle-rasters.json`, not as files
    — so each asset the manifest lists gets a stand-in of its recorded size.
    `read_asset` transfers bytes; what it must get right is *which* bytes, and
    how many.
    """
    import shutil

    destination = tmp_path / "out" / "article-clean.ai"
    shutil.copytree(GOLDEN_BUNDLE, destination)
    assets = json.loads((destination / "assets.json").read_text("utf-8"))
    for index, record in enumerate(assets.get("visual_assets") or [], start=1):
        raster = destination / record["file"]
        raster.parent.mkdir(parents=True, exist_ok=True)
        raster.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]) * 64)
    return destination


# --------------------------------------------------------------------------- #
# The boundary
# --------------------------------------------------------------------------- #


def test_the_boundary_resolves_without_a_protocol(tmp_path: Path) -> None:
    """What `webshot mcp --print-roots` prints, computed from config alone."""
    boundary = Boundary.of(
        McpServerSettings(output_root=tmp_path / "out", roots=[tmp_path / "shared"])
    )
    assert boundary.output_root == (tmp_path / "out").resolve()
    assert (tmp_path / "out").resolve() in boundary.roots
    assert (tmp_path / "shared").resolve() in boundary.roots


# --------------------------------------------------------------------------- #
# Reading a bundle
# --------------------------------------------------------------------------- #


def test_markdown_pages_by_byte_offset(bundle: Path, tmp_path: Path) -> None:
    service = service_for(tmp_path / "out", markdown_page_bytes=64)
    first = service.read_markdown(str(bundle))
    assert first.complete is False
    assert first.next_offset == 64
    assert len(first.text.encode("utf-8")) <= 64

    # Paging to the end reassembles the file exactly, which is the property a
    # client depends on and the one a character-offset API would break.
    collected = bytearray()
    offset: int | None = 0
    while offset is not None:
        page = service.read_markdown(str(bundle), offset=offset)
        collected += (bundle / "content.md").read_bytes()[offset : page.next_offset]
        offset = page.next_offset
    assert bytes(collected) == (bundle / "content.md").read_bytes()


def test_a_caller_cannot_raise_the_servers_page_cap(
    bundle: Path, tmp_path: Path
) -> None:
    service = service_for(tmp_path / "out", markdown_page_bytes=64)
    assert service.read_markdown(str(bundle), limit=10_000_000).next_offset == 64


def test_reading_past_the_end_is_refused(bundle: Path, tmp_path: Path) -> None:
    service = service_for(tmp_path / "out")
    with pytest.raises(BundleNotFound):
        service.read_markdown(str(bundle), offset=10_000_000)
    with pytest.raises(BundleNotFound):
        service.read_markdown(str(bundle), offset=-1)


def test_chunks_are_read_through_the_published_contract(
    bundle: Path, tmp_path: Path
) -> None:
    """A format-3 chunk arrives with its provenance, not with silent nulls."""
    service = service_for(tmp_path / "out")
    result = service.query_chunks(str(bundle))
    assert result.matched == len(
        [line for line in (bundle / "chunks.jsonl").read_text().splitlines() if line]
    )
    first = result.chunks[0]
    assert first.id and first.locator
    assert first.kind in {"text", "table", "figure"}
    assert isinstance(first.headings, list)


def test_query_chunks_reports_what_the_limit_hid(bundle: Path, tmp_path: Path) -> None:
    service = service_for(tmp_path / "out")
    everything = service.query_chunks(str(bundle))
    limited = service.query_chunks(str(bundle), limit=1)
    assert limited.returned == 1
    assert limited.matched == everything.matched > 1


def test_a_substring_query_is_case_insensitive(bundle: Path, tmp_path: Path) -> None:
    service = service_for(tmp_path / "out")
    result = service.query_chunks(str(bundle), query="REVENUE")
    assert result.matched >= 1
    assert all("revenue" in chunk.text.lower() for chunk in result.chunks)


def test_assets_are_read_from_the_file_the_manifest_declares(
    bundle: Path, tmp_path: Path
) -> None:
    service = service_for(tmp_path / "out")
    listed = service.list_assets(str(bundle))
    assert listed.assets, "the fixture bundle has at least one visual asset"
    asset = listed.assets[0]
    assert asset.id and asset.file and asset.sha256

    data = service.read_asset(str(bundle), asset.id)
    assert data.complete is True
    assert len(base64.b64decode(data.data_base64)) == data.bytes_total
    assert data.bytes_total == (bundle / asset.file).stat().st_size


def test_a_protected_viewer_bundle_reports_no_visual_assets(tmp_path: Path) -> None:
    """Its visuals are its pages, already counted — and its content.json is the
    word-level OCR store, which must not be parsed to discover that."""
    viewer = REPO_ROOT / "tests" / "golden" / "protected-viewer" / "bundle"
    if not (viewer / "manifest.json").is_file():  # pragma: no cover
        pytest.skip("the protected-viewer golden has not been recorded")
    import shutil

    destination = tmp_path / "out" / "viewer.ai"
    shutil.copytree(viewer, destination)
    service = service_for(tmp_path / "out")
    assert service.list_assets(str(destination)).assets == []
    # Its chunks still read, through the non-format-3 branch.
    assert service.query_chunks(str(destination)).matched >= 1


def test_an_unknown_asset_is_refused(bundle: Path, tmp_path: Path) -> None:
    service = service_for(tmp_path / "out")
    with pytest.raises(BundleNotFound):
        service.read_asset(str(bundle), "asset-does-not-exist")


def test_a_bundle_without_markdown_says_why(tmp_path: Path) -> None:
    bundle = tmp_path / "out" / "bare.ai"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"bundle_format": 3}', encoding="utf-8")
    service = service_for(tmp_path / "out")
    with pytest.raises(BundleNotFound) as failure:
        service.read_markdown(str(bundle))
    assert "--no-ai-bundle" in str(failure.value)


def test_the_readers_are_confined_to_the_roots(tmp_path: Path) -> None:
    """The same guardrail `tests/test_mcp_policy.py` proves, reached the way a
    tool reaches it."""
    service = service_for(tmp_path / "out")
    for call in (
        lambda: service.read_markdown("/etc"),
        lambda: service.query_chunks("/etc"),
        lambda: service.get_manifest("/etc"),
        lambda: service.list_assets("/etc"),
        lambda: service.read_asset("/etc", "asset-001"),
    ):
        with pytest.raises(Denied):
            call()


# --------------------------------------------------------------------------- #
# Options, staging, publication
# --------------------------------------------------------------------------- #


def test_options_come_from_the_config_and_the_call(tmp_path: Path) -> None:
    config = WebshotConfig.model_validate(
        {
            "capture": {"mode": "faithful", "max_assets": 7},
            "pdf": {"format": "A4"},
            "mcp": {"output_root": str(tmp_path / "out")},
        }
    )
    service = WebshotService(config)
    options = service.build_options(
        source="https://example.com/a",
        staged_pdf=tmp_path / "out" / ".staging" / "a.pdf",
        auth_profile_directory=None,
        selector="main",
    )
    assert options.mode == "faithful"  # from the file
    assert options.max_assets == 7  # from the file
    assert options.paper_format == "A4"  # from the file
    assert options.selector == "main"  # from the call
    assert options.storage_state is None
    assert options.interactive_auth is False


def test_the_option_surface_is_an_allowlist() -> None:
    """A flag WebShot grows later must not become reachable over MCP by default.

    The previous shape built a whole CLI namespace and blanked the dangerous
    fields, which inherited every future option (docs/09 P4-8).
    """
    reachable = set(WebshotService.TOOL_PARAMETER_TO_OPTION.values())
    assert "storage_state" not in reachable
    assert "interactive_auth" not in reachable
    assert "output" not in reachable
    assert "ai_bundle_directory" not in reachable
    assert "debug_screenshot" not in reachable


def test_options_meet_the_same_rules_the_cli_enforces(tmp_path: Path) -> None:
    """`CaptureOptions` owns the rules, so MCP cannot ask for what the CLI refuses."""
    config = WebshotConfig.model_validate(
        {
            "capture": {"ai_bundle": False},
            "mcp": {"output_root": str(tmp_path / "out")},
        }
    )
    service = WebshotService(config)
    with pytest.raises(WebShotError) as failure:
        service.build_options(
            source="https://example.com/a",
            staged_pdf=tmp_path / "out" / "a.pdf",
            auth_profile_directory=None,
            protected_viewer=True,
        )
    assert "--no-ai-bundle" in str(failure.value)


def test_a_capture_publishes_under_the_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webshot.config import CaptureResult
    from webshot.mcp_server import service as service_module

    output_root = tmp_path / "out"

    async def fake_capture(options: Any) -> CaptureResult:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_bytes(b"%PDF-1.7 staged")
        bundle = options.ai_bundle_directory
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "manifest.json").write_text(
            json.dumps({"bundle_format": 3}), encoding="utf-8"
        )
        return CaptureResult(
            source=options.source,
            final_url="https://example.com/a",
            output=str(options.output),
            title="A",
            pages=1,
            bytes=15,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.1,
            captured_at="2026-01-01T00:00:00+00:00",
            ai_summary={"items": 3, "chunks": 2, "ocr_words": 5},
        )

    monkeypatch.setattr(service_module, "convert_url_to_pdf", fake_capture)
    service = service_for(output_root, allow_private_networks=True)
    summary = asyncio.run(service.capture(source="http://127.0.0.1:9/a"))

    assert Path(summary.pdf).parent == output_root.resolve()
    assert Path(summary.bundle or "").parent == output_root.resolve()
    assert summary.bundle_format == 3
    # The staging directory is gone, whatever happened.
    # No staging directory left behind. `.lock` files are excluded on purpose:
    # §5.1's lock file persists by design, because deleting it on release would
    # let a second process lock a *fresh* inode while the first still held the
    # old one (docs/09 P5-10).
    assert not [
        entry
        for entry in output_root.iterdir()
        if entry.name.startswith(".") and entry.suffix != ".lock"
    ]
    # And the counts are the QA report's, built by the one shared function.
    assert summary.counts.items == 3
    assert summary.counts.chunks == 2
    assert summary.counts.ocr_words == 5
    assert summary.counts.links is None


def test_a_capture_refused_after_the_fact_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webshot.config import CaptureResult
    from webshot.mcp_server import service as service_module

    output_root = tmp_path / "out"

    async def redirected(options: Any) -> CaptureResult:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_bytes(b"%PDF-1.7 staged")
        return CaptureResult(
            source=options.source,
            final_url="http://169.254.169.254/latest/meta-data/",
            output=str(options.output),
            title="redirected",
            pages=1,
            bytes=15,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.1,
            captured_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(service_module, "convert_url_to_pdf", redirected)
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(output_root=output_root)),
        resolver=lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port or 0))],
    )
    with pytest.raises(Denied) as failure:
        asyncio.run(service.capture(source="https://public.example/start"))
    assert "redirect target" in str(failure.value)
    published = [entry for entry in output_root.iterdir() if entry.suffix != ".lock"]
    assert published == [], "a refused capture left artifacts behind"


def test_progress_is_reported_from_queued_to_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from webshot.config import CaptureResult
    from webshot.mcp_server import service as service_module

    async def fake_capture(options: Any) -> CaptureResult:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_bytes(b"%PDF-1.7 staged")
        return CaptureResult(
            source=options.source,
            final_url="http://127.0.0.1:9/a",
            output=str(options.output),
            title="A",
            pages=1,
            bytes=15,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.1,
            captured_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(service_module, "convert_url_to_pdf", fake_capture)
    seen: list[tuple[float, str]] = []

    async def record(value: float, message: str) -> None:
        seen.append((value, message))

    service = service_for(tmp_path / "out", allow_private_networks=True)
    asyncio.run(service.capture(source="http://127.0.0.1:9/a", progress=record))
    assert seen[0] == (0, "queued")
    assert seen[-1] == (100, "published")


# --------------------------------------------------------------------------- #
# Regressions the Phase 4 code review found
# --------------------------------------------------------------------------- #


def test_a_tool_argument_overrides_the_same_setting_from_the_config(
    tmp_path: Path,
) -> None:
    """The config and the call may both name one option; the call wins.

    Passing the two layers as separate `**` expansions made this a duplicate
    keyword argument, so a server configured with `[capture] mode` crashed on
    every call that also passed `mode`.
    """
    config = WebshotConfig.model_validate(
        {
            "capture": {"mode": "faithful", "auto_selector": True},
            "mcp": {"output_root": str(tmp_path / "out")},
        }
    )
    options = WebshotService(config).build_options(
        source="https://example.com/a",
        staged_pdf=tmp_path / "out" / "a.pdf",
        auth_profile_directory=None,
        mode="clean",
    )
    assert options.mode == "clean"  # the call
    assert options.auto_selector is True  # still the config


@pytest.mark.parametrize("limit", [0, -1], ids=["zero", "negative"])
def test_a_non_positive_limit_is_refused(
    bundle: Path, tmp_path: Path, limit: int
) -> None:
    """`read(-1)` reads to end of file, so a negative limit would have returned
    a whole asset and defeated the server's cap; a zero limit returns a page
    whose `next_offset` equals its `offset`, which never advances."""
    service = service_for(tmp_path / "out", markdown_page_bytes=64)
    with pytest.raises(BundleNotFound):
        service.read_markdown(str(bundle), limit=limit)
    asset = service.list_assets(str(bundle)).assets[0]
    with pytest.raises(BundleNotFound):
        service.read_asset(str(bundle), asset.id, limit=limit)


def test_listing_does_not_stat_a_path_the_bundle_may_not_name(
    tmp_path: Path,
) -> None:
    """A root may hold bundles WebShot did not write, and `assets.json` is data.

    The record is still listed — hiding it would hide the evidence — but with
    no size, so a crafted path cannot answer "does this file exist, and how big
    is it?" about anything outside the bundle.
    """
    bundle = tmp_path / "out" / "crafted.ai"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"bundle_format": 3}), encoding="utf-8"
    )
    (bundle / "assets.json").write_text(
        json.dumps(
            {
                "bundle_format": 3,
                "form_fields": [],
                "embedded_media": [],
                "visual_assets": [
                    {
                        "id": "a1",
                        "file": "/etc/hosts",
                        "kind": "img",
                        "width": 1.0,
                        "height": 1.0,
                        "alt": "",
                        "aria_label": "",
                        "title": "",
                        "caption": "",
                        "source_url": "",
                        "nearby_heading": "",
                        "sha256": "",
                        "ocr": {
                            "text": "",
                            "confidence": None,
                            "language": None,
                            "engine": None,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = service_for(tmp_path / "out")
    listed = service.list_assets(str(bundle))
    assert listed.assets[0].id == "a1"
    assert listed.assets[0].bytes is None
    with pytest.raises(Denied):
        service.read_asset(str(bundle), "a1")


def test_the_option_allowlist_names_only_real_option_fields() -> None:
    """The audit value of an allowlist depends on its targets being real.

    `settings.SETTINGS` is checked against `CaptureOptions` in both directions;
    this table was checked in neither, so a typo would have surfaced as a
    `TypeError` on a live capture rather than as a failing test (docs/09 P4-12).
    """
    import dataclasses

    from webshot.config import CaptureOptions

    fields = {field.name for field in dataclasses.fields(CaptureOptions)}
    unknown = set(WebshotService.TOOL_PARAMETER_TO_OPTION.values()) - fields
    assert not unknown, f"allowlist names fields that do not exist: {sorted(unknown)}"


def test_a_capture_reports_the_source_that_was_actually_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check_source` returns the value it approved, and that is the value used.

    Its return was previously discarded, so the contract its docstring states —
    "a caller cannot forget to use the checked value" — was enforced nowhere.
    """
    from webshot.config import CaptureResult
    from webshot.mcp_server import service as service_module

    seen: list[str] = []

    async def record_source(options: Any) -> CaptureResult:
        seen.append(options.source)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_bytes(b"%PDF-1.7 staged")
        return CaptureResult(
            source=options.source,
            final_url="http://127.0.0.1:9/a",
            output=str(options.output),
            title="A",
            pages=1,
            bytes=15,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.1,
            captured_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(service_module, "convert_url_to_pdf", record_source)
    service = service_for(tmp_path / "out", allow_private_networks=True)
    asyncio.run(service.capture(source="127.0.0.1:9/a"))
    # `normalize_source` turned the bare authority into a URL, and that is what
    # both the policy check and the capture saw.
    assert seen == ["http://127.0.0.1:9/a"]


def test_the_subresource_filter_is_on_exactly_when_internal_targets_are_refused(
    tmp_path: Path,
) -> None:
    """Guardrail (c) covers what the page fetches, not only what was asked for.

    And it is off when the operator opted into internal targets, because then
    the localhost dashboard's own subresources are the point (docs/09 P4-13).
    """
    closed = service_for(tmp_path / "out").build_options(
        source="https://example.com/a",
        staged_pdf=tmp_path / "out" / "a.pdf",
        auth_profile_directory=None,
    )
    assert closed.block_private_requests is True

    opened = service_for(tmp_path / "out", allow_private_networks=True).build_options(
        source="http://127.0.0.1/a",
        staged_pdf=tmp_path / "out" / "a.pdf",
        auth_profile_directory=None,
    )
    assert opened.block_private_requests is False


def test_the_cli_never_blocks_subresources_by_default() -> None:
    """The CLI is human-driven and unchanged: a person who types a URL chose it."""
    from webshot.cli import build_parser, options_from_args

    options = options_from_args(build_parser().parse_args(["https://example.com/a"]))
    assert options.block_private_requests is False


def test_a_capture_locks_the_path_it_will_publish_to(tmp_path: Path) -> None:
    """spec §5.1 applies to the MCP server, and on the *published* path.

    The pipeline locks whatever output it is handed, and what this caller hands
    it is inside a per-process staging directory — a path no other process can
    name, so a lock there always succeeds and guards nothing. The swap that
    matters happens afterwards, into `output_root`. Two servers sharing one
    output root — the ordinary case, since a stdio server is spawned per client
    from the same `webshot.toml` — compute the same slug from the same URL, and
    guardrail §6.8f does not help: it serializes captures *within* one instance
    (docs/09 P5-9).

    Held from before the capture rather than around the swap, so the second
    caller is refused early instead of after paying for a capture it cannot
    publish.
    """
    from webshot.outputlock import OutputLock, lock_path

    output_root = tmp_path / "captures"
    output_root.mkdir()
    source = REPO_ROOT / "tests" / "fixtures" / "sample_notes.txt"
    service = service_for(output_root, roots=[REPO_ROOT])

    # What the service would publish to, computed the way the service computes
    # it — from the output root, the source's slug, and a digest of the
    # complete normalized source (docs/09 P8-54).
    from webshot.acquire.router import normalize_source
    from webshot.mcp_server.service import published_name

    destination = output_root / published_name(normalize_source(str(source)))

    held = OutputLock(destination)
    held.acquire()
    try:
        with pytest.raises(WebShotError) as caught:
            asyncio.run(service.capture(source=str(source)))
        assert caught.value.exit_code == 2
        # The lock file it names is what makes this test discriminating. The
        # staged PDF has the *same basename* as the published one, so a lock on
        # the wrong path produces a message that reads identically — and,
        # because the pipeline locks whatever output it is handed, locking the
        # staged path here made the service collide with itself and raise this
        # very error for entirely the wrong reason. The path is the only part
        # of the message the two cases spell differently.
        assert str(lock_path(destination)) in str(caught.value)
        # And it refused before capturing: nothing was published, and no
        # staging directory was left behind.
        assert not destination.exists()
        assert not list(output_root.glob(".mcp-staging.*"))
    finally:
        held.release()
    # The lock file stays; what must not survive is the *lock*, so the check is
    # that the path is claimable again (docs/09 P5-10).
    OutputLock(destination).acquire()
