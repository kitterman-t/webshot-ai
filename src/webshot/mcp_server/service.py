"""What the MCP tools *do*, with no MCP in sight.

`server.py` is the bridge — the only module that may import the SDK — and for
that claim to mean anything the bridge has to be thin.  Capturing, staging,
publishing, and reading a bundle back are WebShot operations that happen to be
reachable over a protocol, so they live here, where `tests/test_mcp_service.py`
exercises them on an install that never asked for `webshot[mcp]` (docs/09 P4-9).

Progress is a plain callback rather than an SDK `Context` for the same reason:
the one thing the tools need from the protocol is "tell the caller how far
along this is", and that is expressible without importing anything.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import json
import mimetypes
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError

from ..acquire.router import normalize_source, sanitize_filename
from ..bundle.manifest import (
    BUNDLE_FORMAT,
    AssetOcr,
    BundleVisualAssetRecord,
    VisualAssetRecord,
    WebChunkV3,
)
from ..bundle.publish import publish_directory
from ..config import CaptureOptions, CaptureResult
from ..doctor import run_checks, summarize
from ..outputlock import OutputLock
from ..pipeline import convert_url_to_pdf
from ..report import counts
from ..settings import WebshotConfig, capture_overrides
from . import policy
from .results import (
    AssetData,
    AssetList,
    AssetRecord,
    CaptureSummary,
    ChunkMedia,
    ChunkQuery,
    ChunkRecord,
    ChunkVideoProvenance,
    DoctorCheck,
    DoctorReport,
    MarkdownPage,
)

#: `(fraction complete, what is happening)`. Awaited if the caller supplies one.
ProgressCallback = Callable[[float, str], Awaitable[None]]

#: Distinguishes two services in one process (see `_staging_directory`).
_SERVICE_TOKENS = itertools.count()

#: How often a running capture reports progress. A capture is a browser, an OCR
#: pass, and a PDF composition; docs/04-spec.md §5.9 requires the client's
#: timeout to exceed the operation timeout, and a heartbeat is what makes a long
#: capture distinguishable from a hung one in the meantime.
PROGRESS_INTERVAL_SECONDS = 5.0
_PROGRESS_FLOOR = 10.0
_PROGRESS_CEILING = 85.0


class BundleNotFound(policy.Denied):
    """The bundle exists and is readable, but does not hold what was asked for.

    Distinct from a guardrail refusal even though both reach the caller as a
    tool error: `Denied`'s subclasses are enumerable, and docs/04-spec.md §6.8's
    six rules have to stay countable from the code (docs/09 P4-9). A missing
    `content.md` is a fact about a bundle, not a rule that refused.
    """


def published_name(normalized: str) -> str:
    """A readable, collision-resistant filename for a source the caller chose.

    `sanitize_filename` builds a slug from the host and the last two path
    segments and drops the query string entirely, so two report URLs differing
    only by `?id=` — and two local files sharing a basename — produced the same
    published path. MCP callers cannot choose an output path and captures are
    serialized, so the later one silently replaced the earlier PDF *and* its
    bundle with no way for either caller to notice (docs/09 P8-54).

    The slug is kept and a short digest of the complete normalized source is
    appended, so the name still reads as what it captured and two sources can
    no longer share one. Twelve hex characters: this is a collision guard
    inside one output root, not a security boundary.
    """
    stem = Path(sanitize_filename(normalized)).stem
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}.pdf"


class WebshotService:
    """The server's state: its boundary, and its one capture slot."""

    #: How a `capture` tool parameter is named on `CaptureOptions`. This mapping
    #: is an **allowlist**, and that is the point: an option WebShot grows later
    #: is not reachable over MCP until someone adds it here deliberately. The
    #: previous shape — build a full CLI namespace, then blank the dangerous
    #: fields — inherited every future flag by default, which is the opposite of
    #: how the rest of this surface works (docs/09 P4-8).
    TOOL_PARAMETER_TO_OPTION: ClassVar[dict[str, str]] = {
        "mode": "mode",
        "selector": "selector",
        "auto_selector": "auto_selector",
        "exclude": "exclude_selectors",
        "ocr": "ocr",
        "protected_viewer": "protected_viewer",
        "title": "document_title",
    }

    def __init__(
        self, config: WebshotConfig, *, resolver: policy.Resolver | None = None
    ) -> None:
        self.config = config
        self.boundary = policy.Boundary.of(config.mcp)
        self.resolver = resolver
        # The capture slot serializes captures within *this* service, so the
        # staging directory has to be unique per service too: two servers in
        # one process sharing an output root would otherwise compute the same
        # path and delete each other's in-flight capture (docs/09 P4-12).
        self._token = f"{next(_SERVICE_TOKENS):x}"
        # Guardrail (f): one capture at a time per server instance, which is
        # also why one service belongs to one event loop — the lock binds to
        # the first loop that contends it, and driving one service from two
        # `asyncio.run()` calls raises rather than silently not serializing.
        # `build_server` creates one service per server, so that holds. Two
        # Chromium captures in one process contend for the same staging names
        # and the same output paths, and an agent that fires five `capture`
        # calls in parallel should get five captures, in order, not a race.
        self._capture_slot = asyncio.Lock()

    @property
    def output_root(self) -> Path:
        return self.boundary.output_root

    @property
    def roots(self) -> tuple[Path, ...]:
        return self.boundary.roots

    # ----------------------------------------------------------------- #
    # capture
    # ----------------------------------------------------------------- #

    def _staging_directory(self) -> Path:
        """A private directory inside the output root to capture into.

        Captures are staged rather than published directly so that
        `check_final_url` can refuse *before* anything appears where a consumer
        looks: a public URL that redirects to an internal one must leave no
        artifact behind (docs/04-spec.md §6.8, guardrail (c)).
        """
        staging = self.output_root / f".mcp-staging.{os.getpid()}.{self._token}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        return staging

    def build_options(
        self,
        *,
        source: str,
        staged_pdf: Path,
        auth_profile_directory: Path | None,
        **requested: Any,
    ) -> CaptureOptions:
        """Build capture options from the server's config plus this request.

        Three layers, narrowing: the server's `webshot.toml`, then the tool
        parameters this call actually set, then the paths only the server may
        choose. `CaptureOptions` itself applies every rule the CLI's flags meet,
        so a combination the command line refuses is refused here too.
        """
        chosen = {
            self.TOOL_PARAMETER_TO_OPTION[name]: value
            for name, value in requested.items()
            if value is not None
        }
        # Merged, not passed as two `**` expansions: a server whose config sets
        # `[capture] mode` and a call that also passes `mode` would otherwise be
        # a duplicate keyword argument rather than an override.
        return CaptureOptions(
            **{**capture_overrides(self.config), **chosen},
            source=source,
            output=staged_pdf,
            ai_bundle_directory=staged_pdf.with_suffix(".ai"),
            auth_profile=auth_profile_directory,
            # Guardrail (c) applies to what the page fetches, not only to what
            # the caller asked for. Off when the operator has opted into
            # internal targets, because then the dashboard's own subresources
            # are the point (docs/09 P4-13).
            block_private_requests=not self.config.mcp.allow_private_networks,
            # Guardrail (a) applies to what the page *reads*, not only to the
            # source string. `check_source` confines the top-level document and
            # nothing confined what that document then referenced, so an
            # allowed local file could pull `file:///anywhere` into the PDF and
            # the asset bundle. Unconditional: unlike the network rule this one
            # is not relaxed by `allow_private_networks`, which is about
            # addresses, not about the filesystem (docs/09 P8-11).
            filesystem_roots=tuple(self.roots),
        )

    async def capture(
        self,
        *,
        source: str,
        progress: ProgressCallback | None = None,
        auth_profile: str | None = None,
        **requested: Any,
    ) -> CaptureSummary:
        # `check_source` returns the source it approved, so the capture cannot
        # proceed on a different string from the one that was checked.
        # It resolves DNS, which is a blocking call, and `capture`
        # is the one tool the SDK awaits on the event loop rather than running
        # in a worker thread. A slow or unreachable resolver would otherwise
        # freeze the whole stdio connection — the same reasoning `pipeline.py`
        # applies to veraPDF.
        normalized = await asyncio.to_thread(
            policy.check_source,
            normalize_source(source),
            roots=self.roots,
            allow_private=self.config.mcp.allow_private_networks,
            resolver=self.resolver,
        )
        profile_directory = None
        if auth_profile is not None:
            profile_directory = policy.check_profile_ready(
                auth_profile,
                policy.resolve_auth_profile(
                    auth_profile, self.config.mcp.auth_profiles
                ),
            )
        await _report(progress, 0, "queued")
        async with self._capture_slot:
            return await self._capture_now(
                normalized=normalized,
                progress=progress,
                profile_directory=profile_directory,
                **requested,
            )

    async def _capture_now(
        self,
        *,
        normalized: str,
        progress: ProgressCallback | None,
        profile_directory: Path | None,
        **requested: Any,
    ) -> CaptureSummary:
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging = self._staging_directory()
        try:
            staged_pdf = staging / published_name(normalized)
            # docs/04-spec.md §5.1's lock, on the path that is actually
            # *published*. The pipeline locks the output it is given, and the
            # output this caller gives it is inside a staging directory no other
            # process can name — so the lock there always succeeds and protects
            # nothing, while the real swap happens in `_publish` afterwards.
            # Two servers sharing one `output_root` compute the same slug from
            # the same URL, and nothing else stops their swaps from interleaving
            # into a PDF from one run beside a bundle from the other. Guardrail
            # (f) does not cover it: it serializes captures within one server
            # instance, and a stdio server is spawned per client (docs/09 P5-9).
            final_pdf = self.published_path(staged_pdf)
            lock = OutputLock(final_pdf, final_pdf.with_suffix(".ai"))
            # Off the event loop: `capture` is awaited on it, and an
            # `output_root` on a stalled network mount would otherwise freeze
            # the whole stdio connection — the same reason `check_source` runs
            # its resolver in a thread a few lines above.
            await asyncio.to_thread(lock.acquire)
            try:
                options = self.build_options(
                    source=normalized,
                    staged_pdf=staged_pdf,
                    auth_profile_directory=profile_directory,
                    **requested,
                )
                await _report(progress, _PROGRESS_FLOOR, f"capturing {normalized}")
                async with _heartbeat(progress):
                    result = await convert_url_to_pdf(options)
                await _report(progress, 90, "checking the page that was really loaded")
                # Guardrail (c), second half: the URL the browser ended on, not
                # the one the caller asked for.
                await asyncio.to_thread(
                    policy.check_final_url,
                    result.final_url,
                    allow_private=self.config.mcp.allow_private_networks,
                    resolver=self.resolver,
                )
                published = self._publish(staged_pdf, result)
            finally:
                lock.release()
            await _report(progress, 100, "published")
            return published
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def published_path(self, staged_pdf: Path) -> Path:
        """Where a staged capture will be published.

        One definition, because two callers need the same answer: the §5.1 lock
        has to guard the path `_publish` will actually write, and a second
        spelling of it would let the lock silently guard a file nobody
        publishes — restoring the interleaving it was added to prevent
        (docs/09 P5-10).

        Guardrail (b) is structural rather than validated: the destination is
        computed from the server's own output root and the source's slug, and
        `resolve_within` re-proves it lands there before anything moves.
        """
        return policy.resolve_within(
            self.output_root / staged_pdf.name, [self.output_root], what="output path"
        )

    def _publish(self, staged_pdf: Path, result: CaptureResult) -> CaptureSummary:
        """Move a verified capture out of staging and into the output root."""
        final_pdf = self.published_path(staged_pdf)
        staged_bundle = staged_pdf.with_suffix(".ai")
        final_bundle = final_pdf.with_suffix(".ai")
        # The PDF and its bundle are one deliverable: the bundle's manifest
        # records the PDF's SHA-256, so a new PDF beside a previous bundle is
        # not a partial success, it is two artifacts that describe different
        # captures. `publish_directory` restores the old bundle when its own
        # swap fails — and `os.replace` above had already overwritten the PDF
        # irreversibly, so a failure left exactly that mismatch while the tool
        # reported an error (docs/09 P8-55). The main pipeline backs the PDF up
        # and rolls it back; this path now does the same.
        backup = final_pdf.with_name(f".{final_pdf.name}.{os.getpid()}.backup")
        backup.unlink(missing_ok=True)
        replaced = final_pdf.exists()
        if replaced:
            os.replace(final_pdf, backup)
        os.replace(staged_pdf, final_pdf)
        bundle_directory: Path | None = None
        try:
            if staged_bundle.is_dir():
                publish_directory(staged_bundle, final_bundle)
                bundle_directory = final_bundle
        except BaseException:
            if replaced:
                os.replace(backup, final_pdf)
            else:
                final_pdf.unlink(missing_ok=True)
            raise
        finally:
            backup.unlink(missing_ok=True)
        manifest = (
            _read_manifest(bundle_directory, self.roots) if bundle_directory else {}
        )
        return CaptureSummary(
            source=result.source,
            final_url=result.final_url,
            title=result.title,
            pdf=str(final_pdf),
            bundle=str(bundle_directory) if bundle_directory else None,
            bundle_format=manifest.get("bundle_format"),
            captured_at=result.captured_at,
            pages=result.pages,
            bytes=result.bytes,
            http_status=result.http_status,
            # The same tally the QA report publishes, built by the same
            # function: two counts of one capture must never disagree.
            counts=counts(result),
            manifest_sha256=result.manifest_sha256,
            warnings=list(result.warnings),
            # This summary returned only `warnings`, so an MCP caller got an
            # apparently clean capture with no sign that images, frames or
            # subresources were missing — including the ones the
            # private-network filter deliberately aborted, which is the case
            # an agent most needs to know about (docs/09 P8-65). The first 20
            # distinct URLs; `counts.failed_requests` counts them all, and a
            # warning says how many and of what kind (docs/09 P20-6).
            failed_requests=list(result.failed_requests),
            timings=dict(result.timings),
        )

    # ----------------------------------------------------------------- #
    # bundle readers
    # ----------------------------------------------------------------- #

    def read_markdown(
        self, bundle: str, offset: int = 0, limit: int | None = None
    ) -> MarkdownPage:
        directory = policy.resolve_bundle(bundle, self.roots)
        markdown = _bundle_file(directory, "content.md", self.roots)
        if not markdown.is_file():
            raise BundleNotFound(
                f"{directory} has no content.md — it was captured with "
                "--no-ai-bundle, so there is no markdown to read."
            )
        window = _window(limit, self.config.mcp.markdown_page_bytes, text=True)
        page, next_offset, total = _read_window(markdown, offset, window, text=True)
        return MarkdownPage(
            bundle=str(directory),
            offset=offset,
            next_offset=next_offset,
            bytes_total=total,
            complete=next_offset is None,
            # `_read_window` ends a page on a character boundary, so this
            # decodes whole. `errors="replace"` remains for the case the
            # boundary cannot fix: a `content.md` that is not valid UTF-8,
            # which a bundle WebShot did not write may well be.
            text=page.decode("utf-8", errors="replace"),
        )

    def query_chunks(
        self,
        bundle: str,
        query: str | None = None,
        limit: int | None = 20,
        offset: int = 0,
    ) -> ChunkQuery:
        """Matching chunks, one page at a time.

        `offset` counts *matches*, not lines, so paging a filtered query means
        the same thing as paging an unfiltered one. Without it the scan always
        started from the beginning and returned the same first records: every
        match past `limit` was unreachable no matter how a client called this,
        while the tool's own description said that omitting `query` pages
        through everything (docs/09 P8-63). `next_offset` is null on the last
        page, matching the convention `read_markdown` and `read_asset` already
        use, so a client has one paging loop rather than two.
        """
        directory = policy.resolve_bundle(bundle, self.roots)
        path = _bundle_file(directory, "chunks.jsonl", self.roots)
        if not path.is_file():
            raise BundleNotFound(
                f"{directory} has no chunks.jsonl — it was captured with "
                "--no-ai-bundle, so there is nothing to query."
            )
        format_3 = (
            _read_manifest(directory, self.roots).get("bundle_format") == BUNDLE_FORMAT
        )
        # `None` means the server's own cap here, as it does on the two paged
        # readers: the protocol-free API has one convention.
        cap = self.config.mcp.max_chunks
        limit = cap if limit is None else max(1, min(limit, cap))
        if offset < 0:
            raise BundleNotFound("offset cannot be negative.")
        needle = query.lower() if query else None
        matched = 0
        chunks: list[ChunkRecord] = []
        # Streamed rather than read whole: a bundle's chunk file is the largest
        # text artifact it has, and this scan reads every line of it.
        with path.open(encoding="utf-8") as lines:
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BundleNotFound(
                        f"{path} line {number} is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(record, dict):
                    raise BundleNotFound(f"{path} line {number} is not a chunk record.")
                text = str(record.get("text", ""))
                if needle is not None and needle not in text.lower():
                    continue
                matched += 1
                if matched > offset and len(chunks) < limit:
                    chunks.append(_chunk_record(record, text, format_3=format_3))
        return ChunkQuery(
            bundle=str(directory),
            query=query,
            matched=matched,
            offset=offset,
            returned=len(chunks),
            # Null on the last page. `offset + len(chunks)` rather than a
            # counter, so a page that returned nothing because the offset is
            # past the end says "no more" instead of pointing at itself.
            next_offset=(
                offset + len(chunks) if offset + len(chunks) < matched else None
            ),
            chunks=chunks,
        )

    def get_manifest(self, bundle: str) -> dict[str, Any]:
        return _read_manifest(policy.resolve_bundle(bundle, self.roots), self.roots)

    def list_assets(self, bundle: str) -> AssetList:
        directory = policy.resolve_bundle(bundle, self.roots)
        records = []
        for asset in _asset_records(directory, self.roots):
            # `assets.json` is data from a captured page's bundle, and a root
            # may hold bundles WebShot did not write. Confining the path before
            # stat()-ing it is what stops a crafted record turning a listing
            # into "does /etc/shadow exist, and how big is it?".
            path = _confined(directory, asset.file, self.roots)
            records.append(
                asset.model_copy(
                    update={
                        "bytes": path.stat().st_size
                        if path and path.is_file()
                        else None
                    }
                )
            )
        return AssetList(bundle=str(directory), assets=records)

    def read_asset(
        self, bundle: str, asset_id: str, offset: int = 0, limit: int | None = None
    ) -> AssetData:
        directory = policy.resolve_bundle(bundle, self.roots)
        for asset in _asset_records(directory, self.roots):
            if asset.id == asset_id:
                break
        else:
            raise BundleNotFound(f"No asset {asset_id!r} in {directory}.")
        if not asset.file:
            raise BundleNotFound(f"Asset {asset_id!r} has no file to read.")
        # The manifest is data from a captured page's bundle, so the path it
        # names is validated exactly as a caller-supplied one would be.
        path = policy.resolve_in_bundle(directory, asset.file, self.roots)
        if not path.is_file():
            raise BundleNotFound(
                f"Asset file {asset.file} is missing from {directory}."
            )
        window = _window(limit, self.config.mcp.read_asset_max_bytes)
        chunk, next_offset, total = _read_window(path, offset, window)
        media_type, _ = mimetypes.guess_type(path.name)
        return AssetData(
            bundle=str(directory),
            asset_id=asset_id,
            file=asset.file,
            media_type=media_type or "application/octet-stream",
            bytes_total=total,
            offset=offset,
            next_offset=next_offset,
            complete=next_offset is None,
            data_base64=base64.b64encode(chunk).decode("ascii"),
        )

    def doctor(self) -> DoctorReport:
        results = run_checks(
            # The operator's configured language, not a hard-coded one: an agent
            # asking whether this machine can capture should be told about the
            # language packs this server would actually ask Tesseract for.
            language=self.config.ocr.language or "eng",
            output=self.output_root,
        )
        exit_code = summarize(results)
        return DoctorReport(
            checks=[DoctorCheck(**asdict(result)) for result in results],
            exit_code=exit_code,
            ready=exit_code == 0,
        )


# --------------------------------------------------------------------------- #
# Reading bundle files
# --------------------------------------------------------------------------- #


#: The longest UTF-8 code point, and therefore the smallest text window that
#: can be guaranteed to advance by at least one whole character.
MIN_TEXT_WINDOW = 4


def _window(limit: int | None, cap: int, *, text: bool = False) -> int:
    """The caller's page size, or the server's — whichever is smaller.

    A non-positive `limit` is refused rather than clamped. `read(-1)` reads to
    end of file, so a negative limit would have returned the whole asset and
    defeated `read_asset_max_bytes` outright (docs/04-spec.md §5.9); and a zero
    limit returns an empty page whose `next_offset` equals its `offset`, which
    is a client paging loop that never advances. Neither is a request worth
    guessing at.

    A *text* window below four bytes is refused for the same reason one step
    further on: a page that cannot hold one code point cannot both stay on a
    character boundary and advance, so it was silently doing neither — the
    split character decoded to U+FFFD on both sides and was lost (docs/09
    P8-64). Refused rather than rounded up, because rounding up puts the page
    over a cap the server declares.
    """
    if limit is None:
        return cap
    if limit < 1:
        raise BundleNotFound(f"limit must be at least 1 byte, not {limit}.")
    if text and limit < MIN_TEXT_WINDOW:
        raise BundleNotFound(
            f"limit must be at least {MIN_TEXT_WINDOW} bytes for text, not "
            f"{limit}: a smaller page cannot hold one UTF-8 character, so it "
            "could not advance without splitting one."
        )
    return min(limit, cap)


def _incomplete_tail(chunk: bytes) -> int:
    """Drop a trailing UTF-8 sequence the window cut in half.

    Byte offsets and multi-byte text do not mix by themselves: slicing at an
    arbitrary offset and decoding each side with `errors="replace"` turns a
    split character into U+FFFD on *both* pages, so the character is destroyed
    rather than delivered — a client reassembling `content.md` from
    `next_offset` gets corruption at every page seam (docs/09 P4-12).

    Ending the page on a character boundary fixes it without making the API
    stateful: each page decodes whole, and `next_offset` points at the start of
    the next character.

    Returns the number of trailing bytes to drop, which is 0 when the page
    already ends cleanly. The caller may instead *extend* the read by the
    complement — see `_read_window`. Trimming to nothing and returning the
    fragment anyway was the earlier answer, on the reasoning that a page which
    advances by nothing is the worse failure; it is, but it is not the only
    other option. `limit=1` on `é` returned the lead byte alone, so that page
    decoded to U+FFFD and the next one began on the continuation byte and
    decoded to U+FFFD too — the character destroyed rather than delivered, in
    the one case the boundary logic exists for (docs/09 P8-64).
    """
    for back in range(1, min(4, len(chunk)) + 1):
        byte = chunk[-back]
        if byte < 0x80:  # ASCII: the page already ends on a boundary
            return 0
        if byte >= 0xC0:  # a lead byte: does its sequence fit in the window?
            needed = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return back if back < needed else 0
        # a continuation byte — keep walking back to find its lead
    return 0


def _read_window(
    path: Path, offset: int, window: int, *, text: bool = False
) -> tuple[bytes, int | None, int]:
    """One page of a file: its bytes, where the next page starts, and the size.

    Seeks rather than reading the whole file, so paging an N-byte asset costs N
    bytes of I/O in total instead of N per page.

    `text=True` keeps pages on UTF-8 character boundaries, because the caller
    hands the client a decoded string. Two ways to stay on one, and which
    applies depends on whether trimming would empty the page:

    Shrinking is the only move: drop the split character's leading bytes and
    let the next page start at it. That always makes progress *provided the
    window can hold one code point*, which is why `_window` refuses a text
    limit below `MIN_TEXT_WINDOW`. It used to accept any positive limit, and
    `limit=1` on `é` then returned the lead byte alone — U+FFFD on this page,
    U+FFFD on the next when it resumed mid-sequence, and the character on
    neither (docs/09 P8-64).

    Growing the read to finish the code point was the other candidate and is
    worse: it would put a page over `read_markdown_max_bytes`, and that cap is
    a resource bound the server states. Refusing a window too small to be
    usable tells the caller something true; quietly exceeding a declared limit
    does not.

    `text=False` does none of this. An asset is bytes and has no characters to
    split.
    """
    if offset < 0:
        raise BundleNotFound("offset cannot be negative.")
    total = path.stat().st_size
    if offset > total:
        raise BundleNotFound(
            f"offset {offset} is past the end of the file ({total} bytes)."
        )
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(window)
        if text and offset + len(chunk) < total:
            tail = _incomplete_tail(chunk)
            # `tail < len(chunk)` always holds for a window of at least four
            # bytes — no UTF-8 code point is longer — which `_window` enforces.
            if 0 < tail < len(chunk):
                chunk = chunk[:-tail]
    end = offset + len(chunk)
    return chunk, (end if end < total else None), total


def _confined(bundle: Path, relative: str, roots: Sequence[Path]) -> Path | None:
    """An asset's path if the bundle may name it, else None.

    `list_assets` still reports the record — hiding it would hide the evidence —
    but reports no size for it, and `read_asset` refuses it outright.
    """
    if not relative:
        return None
    try:
        return policy.resolve_in_bundle(bundle, relative, roots)
    except policy.Denied:
        return None


def _bundle_file(bundle: Path, name: str, roots: Sequence[Path]) -> Path:
    """A file inside a bundle, confined the way an asset path is.

    `resolve_bundle` resolves the *directory*, which leaves a symlink at the
    last component unresolved: a `content.md` inside a root pointing at
    `/etc/shadow` was read straight through, because these four fixed names
    were the one place the confinement was assumed rather than applied
    (docs/09 P4-12). Guardrail (a) says "resolved through symlinks", and that
    has to include the files, not just the directory holding them.
    """
    return policy.resolve_in_bundle(bundle, name, roots)


def _load_json(path: Path, *, what: str) -> Any:
    """Parse one bundle file, or refuse with a reason.

    A root may legitimately hold bundles WebShot did not write, and a capture
    killed mid-publish leaves a truncated one. Either way the agent should be
    told the bundle is unreadable, not handed a `JSONDecodeError` whose text
    includes a fragment of the file (docs/09 P4-12).
    """
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleNotFound(f"{what} could not be read: {exc}") from exc


def _read_manifest(directory: Path, roots: Sequence[Path] = ()) -> dict[str, Any]:
    manifest = (
        _bundle_file(directory, "manifest.json", roots)
        if roots
        else (directory / "manifest.json")
    )
    loaded = _load_json(manifest, what=f"{directory}/manifest.json")
    if not isinstance(loaded, dict):
        raise BundleNotFound(f"{directory}/manifest.json is not a JSON object.")
    return loaded


def _chunk_record(record: dict[str, Any], text: str, *, format_3: bool) -> ChunkRecord:
    """One chunk, read through the published contract where there is one.

    A format-3 chunk is validated against `WebChunkV3` — the model the writer
    uses — so a field renamed in a future bundle format breaks here rather than
    silently reaching an agent as `null`. Only the records actually returned are
    validated; validating a whole bundle to answer one query would be a tax on
    every search.
    """
    try:
        validated = WebChunkV3.model_validate(record) if format_3 else None
    except ValidationError:
        validated = None  # a bundle from a later format still reads leniently
    if validated is not None:
        video = validated.meta.video
        media = validated.meta.media
        return ChunkRecord(
            id=validated.id,
            text=validated.text,
            headings=list(validated.meta.headings),
            kind=validated.meta.kind,
            page=validated.meta.page,
            locator=validated.meta.locator,
            video=(
                ChunkVideoProvenance(**video.model_dump())
                if video is not None
                else None
            ),
            media=(ChunkMedia(**media.model_dump()) if media is not None else None),
        )
    # The protected-viewer bundle's chunks (`id`, `page`, `kind`, `text`) and
    # any pre-format-3 bundle, which have no `meta` at all.
    return ChunkRecord(
        id=str(record.get("id", "")),
        text=text,
        headings=list(record.get("heading_path") or []),
        kind=record.get("kind"),
        page=record.get("page"),
        locator=record.get("locator"),
    )


def _asset_records(directory: Path, roots: Sequence[Path] = ()) -> list[AssetRecord]:
    """Visual-asset records, from whichever file this bundle format keeps them in.

    The manifest decides, and it is the cheap file — a couple of kilobytes,
    against a protected-viewer `content.json` that can be megabytes of
    word-level OCR and holds no asset records at all. Reading that to discover
    it has nothing was the single most expensive thing these tools did.
    """
    manifest = _read_manifest(directory, roots)
    if manifest.get("bundle_format") == BUNDLE_FORMAT:
        source: Path | None = _bundle_file(directory, "assets.json", roots)
    elif manifest.get("schema_version") == "1.0":
        # v2's web bundle carried the same records inside content.json.
        source = _bundle_file(directory, "content.json", roots)
    else:
        # The protected-viewer bundle's visuals are its pages, already reported
        # as `counts.pages`; it has no visual-asset records.
        return []
    if source is None or not source.is_file():
        return []
    loaded = _load_json(source, what=str(source))
    if not isinstance(loaded, dict):
        return []
    # Both lists, each through its own reader. The page's own visuals and a
    # walkthrough's downloaded stills are different record shapes — feeding the
    # second to `VisualAssetRecord` fails ten validations and falls to the
    # lenient path, which silently drops the playbook and step that make a
    # still identifiable at all (docs/09 P7-12).
    return [
        _asset_record(record)
        for record in loaded.get("visual_assets") or []
        if isinstance(record, dict)
    ] + [
        _video_asset_record(record)
        for record in loaded.get("video_assets") or []
        if isinstance(record, dict)
    ]


def _video_asset_record(raw: dict[str, Any]) -> AssetRecord:
    """One still downloaded for an embedded walkthrough.

    Read leniently on purpose, like `_asset_record`: this is a reader of other
    people's files, and a bundle from a later WebShot must not make
    `list_assets` refuse what it can plainly describe.
    """
    return AssetRecord(
        id=str(raw.get("id", "")),
        file=str(raw.get("file", "")),
        kind=str(raw.get("kind", "")),
        width=float(raw.get("width") or 0),
        height=float(raw.get("height") or 0),
        bytes=raw.get("bytes"),
        source_url=str(raw.get("source_url", "")),
        sha256=str(raw.get("sha256", "")),
        playbook_id=str(raw.get("playbook_id") or "") or None,
        step=raw.get("step"),
    )


def _asset_record(raw: dict[str, Any]) -> AssetRecord:
    """One asset, typed where the published contract fits, lenient where not.

    Validating is worth doing — it is how a field renamed in a future bundle
    format stops reaching an agent as a silent `null`. Validating *strictly* is
    not: the contract models set `extra="forbid"`, so a bundle from a later
    WebShot that added one asset field would fail every record and make
    `list_assets` and `read_asset` refuse a bundle they can plainly read. A
    reader of other people's files degrades; the writer's contract is enforced
    against recorded bundles in `tests/test_contracts.py` (docs/09 P4-12).
    """
    typed: VisualAssetRecord
    try:
        # The record `assets.json` holds now, not v2's. v2's shape forbids
        # `frame_content`, so every current record would fail it and be read
        # by the lenient path below (docs/09 P16-7).
        typed = BundleVisualAssetRecord.model_validate(raw)
    except ValidationError:
        raw_ocr = raw.get("ocr")
        ocr: dict[str, Any] = raw_ocr if isinstance(raw_ocr, dict) else {}
        typed = VisualAssetRecord(
            id=str(raw.get("id", "")),
            file=str(raw.get("file", "")),
            kind=str(raw.get("kind", "")),
            width=_as_float(raw.get("width")),
            height=_as_float(raw.get("height")),
            alt=str(raw.get("alt", "")),
            aria_label=str(raw.get("aria_label", "")),
            title=str(raw.get("title", "")),
            caption=str(raw.get("caption", "")),
            source_url=str(raw.get("source_url", "")),
            nearby_heading=str(raw.get("nearby_heading", "")),
            sha256=str(raw.get("sha256", "")),
            ocr=AssetOcr(
                text=str(ocr.get("text", "")),
                confidence=_as_float(ocr.get("confidence"), default=None),
                language=_as_text(ocr.get("language")),
                engine=_as_text(ocr.get("engine")),
            ),
        )
    return AssetRecord(
        id=typed.id,
        file=typed.file,
        kind=typed.kind,
        width=typed.width,
        height=typed.height,
        bytes=None,
        alt=typed.alt,
        caption=typed.caption,
        source_url=typed.source_url,
        sha256=typed.sha256,
        ocr_text=typed.ocr.text,
        ocr_confidence=typed.ocr.confidence,
        ocr_engine=typed.ocr.engine,
    )


def _as_float(value: Any, default: float | None = 0.0) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


async def _report(
    progress: ProgressCallback | None, value: float, message: str
) -> None:
    """Tell the caller how far along this is, if it is still listening.

    Best-effort by design. A client that closed the stream mid-capture makes the
    notification raise, and letting that propagate would report a fully
    published capture as a failure: the final `_report(100, "published")` runs
    *after* the PDF and the bundle are in place, so the caller would be told the
    capture failed and never learn the path it succeeded at (docs/09 P4-12).
    """
    if progress is None:
        return
    with contextlib.suppress(Exception):
        await progress(value, message)


@contextlib.asynccontextmanager
async def _heartbeat(progress: ProgressCallback | None) -> AsyncIterator[None]:
    """Report progress while a capture runs, so a client can tell it is alive.

    The capture is one long await with no callbacks to hook, so the heartbeat
    is a timer rather than real stage progress, and it stops short of 100 —
    claiming a percentage the pipeline did not report would be worse than
    saying only "still working".
    """
    if progress is None:
        yield
        return
    finished = asyncio.Event()

    async def beat() -> None:
        value = _PROGRESS_FLOOR
        while not finished.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    finished.wait(), timeout=PROGRESS_INTERVAL_SECONDS
                )
            if finished.is_set():
                return
            value = min(value + 5, _PROGRESS_CEILING)
            with contextlib.suppress(Exception):
                await progress(value, "capturing…")

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        finished.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


#: The tool names the server publishes, in spec §1.3's order. Declared here
#: rather than in the bridge so the contract test can read it without the SDK.
TOOL_NAMES: tuple[str, ...] = (
    "capture",
    "read_markdown",
    "query_chunks",
    "get_manifest",
    "list_assets",
    "read_asset",
    "doctor",
)
