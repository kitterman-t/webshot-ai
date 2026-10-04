"""Hostile values for every MCP tool parameter, as a standing matrix.

The Phase 4 reviews found four defects, and **half of them were numbers**:
`limit=-1` reached `file.read(-1)` and returned a whole asset past the server's
cap, and `limit=0` returned a page whose `next_offset` never advanced.  Every
guardrail in `policy.py` had been written against paths, hostnames, and
credentials; nobody had looked at the integers.  A fifth defect — the
`0177.0.0.1` bypass below — came from the same blind spot one layer down, where
a *string* was being interpreted by two different parsers that disagreed.

So this file is not more examples. It is the boundary space of the whole tool
surface, asserted as properties:

1. **Numbers** — no `(offset, limit)` a caller can send exceeds the server's
   cap, produces a page that does not advance, or loses a byte.
2. **Paths** — every hostile path is refused *as a refusal*, never as whatever
   exception the platform happened to raise.
3. **Hosts** — every spelling of an internal address is refused, including the
   ones only one of the two IPv4 parsers agrees about.
4. **The meta-property** — across all four tools and the whole hostile corpus,
   the only exception that ever escapes is `Denied`.

docs/09 P4-10 and P4-11 record what this replaced.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from webshot import netpolicy
from webshot.mcp_server import policy
from webshot.mcp_server.policy import Denied
from webshot.mcp_server.service import WebshotService
from webshot.settings import McpServerSettings, WebshotConfig

CAP = 64


def make_bundle(root: Path, *, content: bytes = b"", chunks: int = 3) -> Path:
    """A minimal format-3 bundle the readers accept."""
    bundle = root / "b.ai"
    (bundle / "assets").mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"bundle_format": 3}), encoding="utf-8"
    )
    (bundle / "content.md").write_bytes(content)
    (bundle / "chunks.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": f"ch_{index:06d}",
                    "text": f"chunk {index} revenue",
                    "meta": {
                        "doc_items": [f"#/texts/{index}"],
                        "headings": [],
                        "kind": "text",
                        "page": None,
                        "locator": f"#/texts/{index}",
                        "sha256": "0" * 64,
                    },
                }
            )
            + "\n"
            for index in range(chunks)
        ),
        encoding="utf-8",
    )
    (bundle / "assets" / "a1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"p" * 200)
    (bundle / "assets.json").write_text(
        json.dumps(
            {
                "bundle_format": 3,
                "form_fields": [],
                "embedded_media": [],
                "visual_assets": [
                    {
                        "id": "a1",
                        "file": "assets/a1.png",
                        "kind": "img",
                        "width": 1.0,
                        "height": 1.0,
                        "alt": "",
                        "aria_label": "",
                        "title": "",
                        "caption": "",
                        "source_url": "",
                        "nearby_heading": "",
                        "sha256": "0" * 64,
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
    return bundle


def service_for(root: Path) -> WebshotService:
    return WebshotService(
        WebshotConfig(
            mcp=McpServerSettings(
                output_root=root,
                markdown_page_bytes=CAP,
                read_asset_max_bytes=CAP,
            )
        )
    )


# --------------------------------------------------------------------------- #
# 1. Numbers
# --------------------------------------------------------------------------- #

#: Every integer a caller can send that is not a plain in-range page request.
#: `2**62` stands in for "an int the JSON schema accepts and the filesystem
#: cannot"; the negatives are the class that reached `file.read(-1)`.
HOSTILE_NUMBERS = [0, -1, -(2**31), 2**62, 2**62 + 1]


@pytest.mark.parametrize("limit", HOSTILE_NUMBERS, ids=lambda n: f"limit={n}")
def test_no_limit_a_caller_can_send_exceeds_the_cap(tmp_path: Path, limit: int) -> None:
    """The cap is the server's (docs/04-spec.md §5.9); a caller may lower it only."""
    bundle = make_bundle(tmp_path / "out", content=b"y" * 4000)
    service = service_for(tmp_path / "out")
    for read in (
        lambda: service.read_markdown(str(bundle), limit=limit),
        lambda: service.read_asset(str(bundle), "a1", limit=limit),
    ):
        try:
            page = read()
        except Denied:
            continue  # a refusal is always an acceptable answer
        payload = page.text if hasattr(page, "text") else page.data_base64
        assert len(payload) <= CAP * 2, f"limit={limit} returned {len(payload)} bytes"


@pytest.mark.parametrize("offset", HOSTILE_NUMBERS, ids=lambda n: f"offset={n}")
def test_no_offset_a_caller_can_send_crashes_or_stalls(
    tmp_path: Path, offset: int
) -> None:
    bundle = make_bundle(tmp_path / "out", content=b"y" * 4000)
    service = service_for(tmp_path / "out")
    try:
        page = service.read_markdown(str(bundle), offset=offset)
    except Denied:
        return
    assert page.next_offset is None or page.next_offset > offset


# No deadline. One example pages through up to 600 bytes four at a time: 150
# calls, each resolving the bundle against the roots and opening content.md,
# after writing a bundle to disk. Its wall time measures the machine's load,
# not the paging: under `pytest -n 4` one example took 316 ms against
# Hypothesis's 200 ms default and failed a run in which every assertion held.
# The property is termination and completeness, not speed. The sanitizer's
# property tests already run without a deadline.
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    size=st.integers(min_value=0, max_value=600),
    # Four is the floor for a *text* window: below it a page cannot hold one
    # UTF-8 code point, so it can neither stay on a character boundary nor
    # advance, and `_window` refuses it rather than splitting one silently
    # (docs/09 P8-64). The refusal has its own test; this property is about
    # the windows that are accepted.
    limit=st.one_of(st.none(), st.integers(min_value=4, max_value=800)),
)
def test_paging_always_terminates_and_loses_nothing(
    tmp_path_factory: pytest.TempPathFactory, size: int, limit: int | None
) -> None:
    """The property a client actually depends on, over the whole input space.

    Follow `next_offset` from zero and you get the file back, byte for byte, in
    a finite number of calls. `next_offset` strictly increasing is what makes
    "finite" true, and it is exactly what `limit=0` broke.

    The page size is never exceeded either — including by the boundary logic,
    which may only shrink a page. Growing one to finish a split code point was
    the other way to fix P8-64 and this assertion is why it was not taken:
    `read_markdown_max_bytes` is a resource bound the server states.
    """
    root = tmp_path_factory.mktemp("paging")
    content = bytes((index * 7) % 251 for index in range(size))
    bundle = make_bundle(root, content=content)
    service = service_for(root)

    collected = bytearray()
    offset: int | None = 0
    seen: set[int] = set()
    while offset is not None:
        assert offset not in seen, "paging revisited an offset"
        seen.add(offset)
        page = service.read_markdown(str(bundle), offset=offset, limit=limit)
        assert page.bytes_total == size
        window = content[offset : page.next_offset if page.next_offset else size]
        assert len(window) <= min(limit or CAP, CAP)
        collected += window
        offset = page.next_offset
    assert bytes(collected) == content


@pytest.mark.parametrize("limit", HOSTILE_NUMBERS, ids=lambda n: f"limit={n}")
def test_query_chunks_never_returns_more_than_it_matched(
    tmp_path: Path, limit: int
) -> None:
    bundle = make_bundle(tmp_path / "out", content=b"y" * 100, chunks=5)
    service = service_for(tmp_path / "out")
    result = service.query_chunks(str(bundle), limit=limit)
    assert result.returned == len(result.chunks)
    assert result.returned <= result.matched
    assert 1 <= result.returned <= 5


def test_a_query_is_a_substring_and_never_a_pattern(tmp_path: Path) -> None:
    """`.*` matches nothing, because v3.0 matches substrings (spec §1.3)."""
    bundle = make_bundle(tmp_path / "out", content=b"y", chunks=3)
    service = service_for(tmp_path / "out")
    assert service.query_chunks(str(bundle), query=".*").matched == 0
    assert service.query_chunks(str(bundle), query="REVENUE").matched == 3
    # An empty query is "no query", not "match the empty string in nothing".
    assert service.query_chunks(str(bundle), query="").matched == 3


# --------------------------------------------------------------------------- #
# 2. Paths
# --------------------------------------------------------------------------- #


def hostile_paths(root: Path, secret: Path) -> list[tuple[str, str]]:
    """`(label, value)` for every shape of path a tool must refuse."""
    return [
        ("absolute-outside", "/etc/passwd"),
        ("absolute-secret", str(secret)),
        ("dot-dot", str(root / ".." / "secret")),
        ("dot-dot-relative", "../../etc"),
        ("tilde", "~/.ssh"),
        ("tilde-bare", "~"),
        ("empty", ""),
        ("dot", "."),
        ("root", "/"),
        # `Path.resolve()` raises ValueError from lstat for this one, which is
        # how it escaped the guardrail as something other than a refusal.
        ("null-byte", "bun\x00dle"),
        ("newline", "bundle\nname"),
        ("very-long", "a" * 5000),
        ("url", "file:///etc/passwd"),
        ("unc", "//server/share"),
    ]


def test_every_hostile_path_is_refused_by_every_reader(tmp_path: Path) -> None:
    """The meta-property: a refusal, and never another exception type.

    A guardrail that raises `ValueError` has still stopped the read, but it has
    stopped saying *why* — and over MCP the caller gets a bare platform message
    instead of the rule and the remedy.
    """
    root = tmp_path / "out"
    root.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "escape.ai").symlink_to(secret)
    service = service_for(root)

    cases = [*hostile_paths(root, secret), ("symlink-escape", str(root / "escape.ai"))]
    for label, value in cases:
        for name, call in (
            ("read_markdown", lambda v=value: service.read_markdown(v)),
            ("query_chunks", lambda v=value: service.query_chunks(v)),
            ("get_manifest", lambda v=value: service.get_manifest(v)),
            ("list_assets", lambda v=value: service.list_assets(v)),
            ("read_asset", lambda v=value: service.read_asset(v, "a1")),
        ):
            with pytest.raises(Denied):
                call()
            # `pytest.raises(Denied)` above already fails the test on any other
            # exception type; the loop exists so the failure names the pair.
            assert label and name


@pytest.mark.parametrize(
    "relative",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "assets/../../../etc/passwd",
        "nul\x00l.png",
    ],
    ids=["dotdot", "absolute", "deep-dotdot", "null-byte"],
)
def test_a_manifest_cannot_name_a_file_outside_its_bundle(
    tmp_path: Path, relative: str
) -> None:
    """`assets.json` is data from a captured page, so its paths are hostile too."""
    root = tmp_path / "out"
    bundle = make_bundle(root)
    with pytest.raises(Denied):
        policy.resolve_in_bundle(bundle, relative, policy.normalize_roots([], root))


def test_a_tilde_in_a_manifest_path_is_an_ordinary_directory_name(
    tmp_path: Path,
) -> None:
    """Not an escape, and not expanded — which is the whole point.

    `~/.ssh/id_rsa` from a manifest resolves to `<bundle>/~/.ssh/id_rsa`: a
    strange filename inside the bundle, and nothing more. Refusing it would be
    wrong; expanding it would be the bug this asserts is absent.
    """
    root = tmp_path / "out"
    bundle = make_bundle(root)
    resolved = policy.resolve_in_bundle(
        bundle, "~/.ssh/id_rsa", policy.normalize_roots([], root)
    )
    assert resolved.is_relative_to(bundle)
    assert resolved == (bundle / "~" / ".ssh" / "id_rsa").resolve()
    # Not "home is no parent of it": on Windows the temporary directory is
    # inside the home directory, so that premise was false before anything ran.
    assert resolved != Path.home() / ".ssh" / "id_rsa"


# --------------------------------------------------------------------------- #
# 3. Hosts
# --------------------------------------------------------------------------- #

#: Spellings of an address inside the host or its networks. The octal forms are
#: the interesting ones: `getaddrinfo("0177.0.0.1")` answers 177.0.0.1 (public)
#: on macOS while `inet_aton` and the WHATWG URL parser — and so Chromium —
#: answer 127.0.0.1. Trusting one parser let a capture reach loopback (P4-11).
INTERNAL_SPELLINGS = [
    "http://127.0.0.1/",
    "http://127.1/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://0177.0.0.1/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:7f00:1]/",
    "http://[fd00::1]/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost/",
    "http://app.localhost/",
    "http://user:pass@127.0.0.1/",
    "HTTP://127.0.0.1/",
    "http://127.0.0.1:8080/admin",
    "http://0.0.0.0/",
    "http://100.64.0.1/",
    # Multicast: `is_global` reports these as public, so replacing the
    # enumerated check with `not is_global` dropped them (docs/09 P4-12).
    "http://239.255.255.250:1900/desc.xml",
    "http://224.0.0.1/",
    "http://[ff02::1]/",
    # IPv6 that carries an IPv4 address three different ways.
    "http://[::127.0.0.1]/",
    "http://[0:0:0:0:0:0:7f00:1]/",
    "http://[2002:7f00:1::]/",
]


def only_public(host: str, port: int | None) -> list[tuple]:
    """A resolver that answers every name with one public address.

    Deliberately unhelpful: it means a spelling only gets refused if WebShot
    itself understood it, not because the platform resolver happened to.
    """
    return [(2, 1, 6, "", ("93.184.216.34", port or 0))]


@pytest.mark.parametrize("url", INTERNAL_SPELLINGS, ids=lambda url: url)
def test_every_spelling_of_an_internal_address_is_refused(url: str) -> None:
    with pytest.raises(Denied):
        policy.check_http_target(url, allow_private=False, resolver=only_public)


@pytest.mark.parametrize("url", INTERNAL_SPELLINGS, ids=lambda url: url)
def test_the_redirect_check_refuses_the_same_set(url: str) -> None:
    """Guardrail (c) runs twice, and both runs must know the same spellings."""
    with pytest.raises(Denied):
        policy.check_final_url(url, allow_private=False, resolver=only_public)


@pytest.mark.parametrize("url", INTERNAL_SPELLINGS, ids=lambda url: url)
def test_the_operator_opt_in_covers_the_same_set(url: str) -> None:
    """`allow_private_networks` is one switch, not a per-spelling allowlist."""
    policy.check_http_target(url, allow_private=True, resolver=only_public)
    policy.check_final_url(url, allow_private=True, resolver=only_public)


@pytest.mark.parametrize(
    "url",
    ["https://93.184.216.34/", "https://example.com/a", "http://example.com:8080/"],
    ids=["public-literal", "public-name", "public-port"],
)
def test_public_targets_still_pass(url: str) -> None:
    """Deny-by-default has to stay usable, or it gets turned off."""
    policy.check_http_target(url, allow_private=False, resolver=only_public)


@pytest.mark.parametrize(
    "source",
    [
        "ftp://example.com/x",
        "gopher://example.com/",
        "javascript:alert(1)",
        "data:text/html,<h1>x</h1>",
        "jar:file:///etc/passwd!/",
    ],
    ids=["ftp", "gopher", "javascript", "data", "jar"],
)
def test_only_http_https_and_confined_files_are_capturable(
    tmp_path: Path, source: str
) -> None:
    roots = policy.normalize_roots([], tmp_path / "out")
    with pytest.raises(Denied):
        policy.check_source(source, roots=roots, allow_private=False)


# --------------------------------------------------------------------------- #
# 4. Credentials
# --------------------------------------------------------------------------- #


@given(
    name=st.text(min_size=0, max_size=80).filter(
        lambda value: not policy.PROFILE_NAME.fullmatch(value)
    )
)
@settings(max_examples=100)
def test_only_a_profile_name_shape_is_ever_accepted(name: str) -> None:
    """Anything that is not shaped like a name is refused before the lookup.

    Generated rather than listed: the storage-state JSON, the path, and the
    newline injection in `tests/test_mcp_policy.py` are three points in a space
    this covers whole.
    """
    with pytest.raises(Denied):
        policy.resolve_auth_profile(name, {"work": Path("/srv/profiles/work")})


def test_a_wellformed_name_still_has_to_be_configured() -> None:
    with pytest.raises(Denied):
        policy.resolve_auth_profile("staging", {"work": Path("/srv/p/work")})


# --------------------------------------------------------------------------- #
# 5. The tool surface as a whole
# --------------------------------------------------------------------------- #


def test_no_tool_parameter_can_produce_an_unhandled_exception(tmp_path: Path) -> None:
    """The standing guarantee, over the cross product of the corpora above.

    Every tool, every hostile value, in every parameter that accepts one: the
    call either succeeds or raises `Denied`. Anything else is a guardrail that
    stopped working without saying so.
    """
    root = tmp_path / "out"
    bundle = make_bundle(root, content=b"z" * 300)
    service = service_for(root)
    good = str(bundle)

    hostile_values: list[Any] = [
        value for _, value in hostile_paths(root, tmp_path / "secret")
    ]
    calls: list[tuple[str, Any]] = []
    for value in hostile_values:
        calls += [
            (
                f"read_markdown(bundle={value!r})",
                lambda v=value: service.read_markdown(v),
            ),
            (
                f"read_asset(asset_id={value!r})",
                lambda v=value: service.read_asset(good, v),
            ),
            (
                f"query_chunks(query={value!r})",
                lambda v=value: service.query_chunks(good, query=v),
            ),
        ]
    for number in HOSTILE_NUMBERS:
        calls += [
            (
                f"read_markdown(offset={number})",
                lambda n=number: service.read_markdown(good, offset=n),
            ),
            (
                f"read_markdown(limit={number})",
                lambda n=number: service.read_markdown(good, limit=n),
            ),
            (
                f"read_asset(offset={number})",
                lambda n=number: service.read_asset(good, "a1", offset=n),
            ),
            (
                f"read_asset(limit={number})",
                lambda n=number: service.read_asset(good, "a1", limit=n),
            ),
            (
                f"query_chunks(limit={number})",
                lambda n=number: service.query_chunks(good, limit=n),
            ),
        ]

    for label, call in calls:
        try:
            call()
        except Denied:
            pass
        except Exception as exc:
            pytest.fail(f"{label} raised {type(exc).__name__} instead of Denied: {exc}")


# --------------------------------------------------------------------------- #
# 6. Hostile bundle *contents*, not just hostile parameters
# --------------------------------------------------------------------------- #

#: The corpus above fuzzes what a caller sends. This one fuzzes what a caller
#: points at: `[mcp] roots` exist so a server can read bundles it did not write,
#: and a capture killed mid-publish leaves a truncated one. Both reach the same
#: readers (docs/09 P4-12).
CORRUPT_BUNDLES: list[tuple[str, str, str]] = [
    ("chunks-truncated", "chunks.jsonl", "{oops"),
    ("chunks-not-object", "chunks.jsonl", '"just a string"\n'),
    ("chunks-array", "chunks.jsonl", "[1, 2]\n"),
    ("assets-truncated", "assets.json", "{oops"),
    ("assets-not-object", "assets.json", '"nope"'),
    ("assets-wrong-type", "assets.json", '{"bundle_format": 3, "visual_assets": "x"}'),
    (
        "assets-bad-record",
        "assets.json",
        '{"bundle_format": 3, "visual_assets": [{"id": 1}]}',
    ),
    ("manifest-truncated", "manifest.json", "{oops"),
    ("manifest-not-object", "manifest.json", "[]"),
]


@pytest.mark.parametrize(
    "label,name,content", CORRUPT_BUNDLES, ids=[case[0] for case in CORRUPT_BUNDLES]
)
def test_a_corrupt_bundle_is_refused_rather_than_raised(
    tmp_path: Path, label: str, name: str, content: str
) -> None:
    root = tmp_path / "out"
    bundle = make_bundle(root, content=b"hello")
    (bundle / name).write_text(content, encoding="utf-8")
    service = service_for(root)
    for call in (
        lambda: service.read_markdown(str(bundle)),
        lambda: service.query_chunks(str(bundle)),
        lambda: service.get_manifest(str(bundle)),
        lambda: service.list_assets(str(bundle)),
        lambda: service.read_asset(str(bundle), "a1"),
    ):
        try:
            call()
        except Denied:
            pass
        except Exception as exc:
            pytest.fail(
                f"{label}: {type(exc).__name__} escaped instead of Denied: {exc}"
            )


def test_a_bundle_from_a_later_format_still_reads(tmp_path: Path) -> None:
    """Tolerant, not strict: the contract models forbid extra keys, and a
    reader that enforced that would refuse a bundle it can plainly read the
    moment a later WebShot adds one field (docs/09 P4-12)."""
    root = tmp_path / "out"
    bundle = make_bundle(root, content=b"hello")
    assets = json.loads((bundle / "assets.json").read_text())
    assets["visual_assets"][0]["some_future_field"] = "x"
    (bundle / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
    service = service_for(root)
    listed = service.list_assets(str(bundle))
    assert [asset.id for asset in listed.assets] == ["a1"]
    assert service.read_asset(str(bundle), "a1").bytes_total > 0


def test_a_symlinked_bundle_file_cannot_leave_the_roots(tmp_path: Path) -> None:
    """`resolve_bundle` resolves the directory; the files inside it are paths too.

    A symlink at the last component was never re-resolved, so a `content.md`
    inside a root pointing at a file outside every root was read straight
    through (docs/09 P4-12).
    """
    root = tmp_path / "out"
    bundle = make_bundle(root, content=b"harmless")
    secret = tmp_path / "secret.txt"
    secret.write_text("ROOT:SECRET:CONTENT", encoding="utf-8")
    (bundle / "content.md").unlink()
    (bundle / "content.md").symlink_to(secret)
    service = service_for(root)
    with pytest.raises(Denied):
        service.read_markdown(str(bundle))


def test_a_staged_capture_is_not_readable(tmp_path: Path) -> None:
    """Staging lives inside `output_root`, which is always a readable root — so
    without an explicit refusal a read tool could reach a capture whose
    `final_url` check has not run yet (docs/09 P4-12)."""
    root = tmp_path / "out"
    staged = root / f"{policy.STAGING_PREFIX}1234.0"
    bundle = make_bundle(staged, content=b"unvetted")
    service = service_for(root)
    for call in (
        lambda: service.read_markdown(str(bundle)),
        lambda: service.get_manifest(str(bundle)),
        lambda: service.list_assets(str(bundle)),
    ):
        with pytest.raises(Denied):
            call()


@pytest.mark.parametrize("cap", [4, 5, 8, 11, 64], ids=lambda n: f"cap={n}")
def test_paged_text_reassembles_exactly_across_character_boundaries(
    tmp_path: Path, cap: int
) -> None:
    """The property the byte-level test could not see.

    Slicing on byte offsets and decoding each side with `errors="replace"`
    turned a split character into U+FFFD on *both* pages, destroying it — and
    the comment above the decode claimed the opposite. Reassembling the *text*
    is what catches it (docs/09 P4-12).
    """
    original = "naïve café résumé — ünicode 日本語 🎉 end"
    root = tmp_path / "out"
    bundle = make_bundle(root, content=original.encode("utf-8"))
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(output_root=root, markdown_page_bytes=cap))
    )
    text, offset = "", 0
    while offset is not None:
        page = service.read_markdown(str(bundle), offset=offset)
        assert "\ufffd" not in page.text, "a character was split across pages"
        text += page.text
        offset = page.next_offset
    assert text == original


# --------------------------------------------------------------------------- #
# What the page fetches, not what the caller asked for
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "ws://127.0.0.1:9000/socket",
        "ws://localhost:9000/socket",
        "wss://169.254.169.254/",
        "ws://[::1]:9000/",
        "ws://0.0.0.0:9000/",
    ],
    ids=lambda url: url,
)
def test_a_websocket_to_an_internal_address_is_classified_internal(url: str) -> None:
    """`context.route` does not carry WebSocket traffic; the handshake is routed
    separately, so it needed the classifier to know the scheme at all.

    A captured public page opening `ws://127.0.0.1:PORT` could talk to an
    internal service and copy the replies into the DOM while `final_url` stayed
    public and the capture published (docs/09 P8-10).
    """
    assert netpolicy.is_internal_url(url, resolver=only_public)


def test_a_public_websocket_is_still_reachable() -> None:
    assert not netpolicy.is_internal_url("wss://example.com/socket", only_public)


def test_a_file_subresource_outside_every_root_is_refused(tmp_path: Path) -> None:
    """Guardrail (a) confined the source and nothing confined what it then read.

    An allowed local document could reference `file:///anywhere`; Playwright
    routes those requests and the classifier called every non-HTTP scheme safe,
    so Chromium rendered the outside file into the PDF and harvested it as a
    visual asset (docs/09 P8-11).
    """
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    inside = root / "nested" / "figure.png"
    inside.write_bytes(b"")
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"")

    assert netpolicy.is_outside_roots(outside.as_uri(), [root])
    assert netpolicy.is_outside_roots("file:///etc/shadow", [root])
    assert not netpolicy.is_outside_roots(inside.as_uri(), [root])
    assert not netpolicy.is_outside_roots(root.as_uri(), [root])


@pytest.mark.parametrize(
    "name", ["plain.png", "with space.png", "100%.png", "hash#.png", "naïve—ü.png"]
)
def test_a_file_url_names_the_path_it_was_made_from(tmp_path: Path, name: str) -> None:
    """`file_url_path` inverts `Path.as_uri()` on this platform.

    On Windows the URL path of `file:///C:/x` is `/C:/x`, which
    `Path(unquote(...))` read as the drive-relative `C:x`: every local source
    failed to load and every file inside an MCP root was judged outside it
    (docs/09 P10-24). The weekly Windows run is where the drive-letter half of
    this is measured.
    """
    path = (tmp_path / name).resolve()
    assert netpolicy.file_url_path(path.as_uri()) == path
    local = "file://localhost" + path.as_uri().removeprefix("file://")
    assert netpolicy.file_url_path(local) == path


def test_a_file_url_naming_another_host_is_not_a_local_path(tmp_path: Path) -> None:
    """Every check read the URL's path and ignored its host.

    So `file://server/<root>/page.html` was judged as the file inside the root,
    while on Windows it is a UNC path on `server` (docs/09 P10-24).
    """
    root = tmp_path / "root"
    root.mkdir()
    inside = (root / "page.html").resolve()
    inside.write_bytes(b"<p>inside</p>")
    elsewhere = "file://server.example" + inside.as_uri().removeprefix("file://")

    with pytest.raises(ValueError, match=r"server\.example"):
        netpolicy.file_url_path(elsewhere)
    assert netpolicy.is_outside_roots(elsewhere, [root])
    with pytest.raises(Denied, match="not a path on this machine"):
        policy.check_source(elsewhere, roots=[root], allow_private=False)
    # The same file, named without a host, is still allowed.
    assert not netpolicy.is_outside_roots(inside.as_uri(), [root])
    policy.check_source(inside.as_uri(), roots=[root], allow_private=False)


@pytest.mark.skipif(
    sys.platform != "win32", reason="a drive-letter path is a path only on Windows"
)
def test_a_drive_letter_path_is_a_path_not_a_url_scheme(tmp_path: Path) -> None:
    """`C:\\page.html` parses with the one-letter scheme `c`, which was refused as
    an unsupported scheme rather than confined as the path it is."""
    root = tmp_path / "root"
    root.mkdir()
    inside = (root / "page.html").resolve()
    inside.write_bytes(b"<p>inside</p>")
    assert policy.check_source(str(inside), roots=[root], allow_private=False)
    with pytest.raises(Denied, match="outside this server's allowed roots"):
        policy.check_source(
            str((tmp_path / "elsewhere.html").resolve()),
            roots=[root],
            allow_private=False,
        )


def test_a_symlinked_file_subresource_cannot_leave_the_roots(tmp_path: Path) -> None:
    """The confinement is about which bytes are read, not which path was typed."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "innocent.txt"
    link.symlink_to(outside)
    assert netpolicy.is_outside_roots(link.as_uri(), [root])


def test_an_unconfigured_capture_keeps_the_whole_filesystem(tmp_path: Path) -> None:
    """A CLI run has no roots, and inventing some would break local documents.

    The user who typed the command already has whatever their own process can
    read; this rule exists for the boundary that does not.
    """
    assert not netpolicy.is_outside_roots("file:///etc/hosts", [])


@pytest.mark.parametrize(
    "url",
    ["https://example.com/a", "data:text/plain,x", "about:blank"],
    ids=["https", "data", "about"],
)
def test_only_file_urls_are_judged_against_the_roots(url: str) -> None:
    assert not netpolicy.is_outside_roots(url, [Path("/nowhere")])


@pytest.mark.parametrize(
    "source",
    [
        "https://alice:secret@example.com/report",
        "https://alice@example.com/report",
        "http://:token@example.com/",
    ],
    ids=["user-and-password", "user-only", "password-only"],
)
def test_a_credential_in_the_source_url_is_refused(source: str, tmp_path: Path) -> None:
    """Spec §6.8: this surface takes no credential-bearing parameters.

    The check looked at the target host and returned the string unchanged, so
    the service went on to log it, derive the published filename from it, and
    record it in the manifest and the summary — a password on disk in four
    places and in the agent's transcript (docs/09 P8-15). Refused rather than
    stripped: the caller asked for an authenticated fetch, and silently making
    an unauthenticated one answers a different question.
    """
    with pytest.raises(Denied, match="credential"):
        policy.check_source(
            source, roots=[tmp_path], allow_private=True, resolver=only_public
        )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://example.com/report?id=1", "https://example.com/report?id=2"),
        ("https://example.com/a/doc", "https://example.com/b/a/doc"),
        ("file:///one/notes.txt", "file:///two/notes.txt"),
    ],
    ids=["query-string", "same-tail-segments", "same-basename"],
)
def test_two_sources_never_publish_to_one_name(first: str, second: str) -> None:
    """MCP callers cannot choose an output path and captures are serialized,
    so a shared name means the later capture silently replaces the earlier
    PDF *and* its bundle with nothing to notice it by (docs/09 P8-54)."""
    from webshot.acquire.router import sanitize_filename
    from webshot.mcp_server.service import published_name

    # The old slug really does collide — otherwise this test proves nothing.
    assert sanitize_filename(first) == sanitize_filename(second)
    assert published_name(first) != published_name(second)


def test_a_published_name_still_reads_as_what_it_captured() -> None:
    """A digest that replaced the slug would trade one problem for another."""
    from webshot.mcp_server.service import published_name

    name = published_name("https://docs.example.com/guide/install?v=2")
    assert name.startswith("docs.example.com-guide-install-")
    assert name.endswith(".pdf")


def test_the_same_source_always_publishes_to_the_same_name() -> None:
    """Re-capturing a source must replace its own artifacts, not accumulate."""
    from webshot.mcp_server.service import published_name

    source = "https://example.com/report?id=1"
    assert published_name(source) == published_name(source)


def _chunk_bundle(directory: Path, count: int) -> Path:
    """A minimal format-3 bundle with `count` chunks."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"bundle_format": 3}), encoding="utf-8"
    )
    (directory / "chunks.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "id": f"ch_{index:06d}",
                    "text": f"needle chunk number {index}",
                    "meta": {
                        "doc_items": [f"#/texts/{index}"],
                        "headings": [],
                        "kind": "text",
                        "page": None,
                        "locator": f"#/texts/{index}",
                        "sha256": "0" * 64,
                    },
                }
            )
            for index in range(count)
        )
        + "\n",
        encoding="utf-8",
    )
    return directory


def test_every_matching_chunk_is_reachable_by_paging(tmp_path: Path) -> None:
    """Without an offset the scan always started from the beginning, so every
    match past `limit` was unreachable however a client called this — while the
    tool's description said omitting the query pages through everything
    (docs/09 P8-63)."""
    bundle = _chunk_bundle(tmp_path / "roots" / "capture.ai", 25)
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(roots=[tmp_path / "roots"]))
    )

    seen: list[str] = []
    offset: int | None = 0
    for _ in range(20):  # a bounded loop: a paging bug must not hang the test
        page = service.query_chunks(str(bundle), limit=10, offset=offset or 0)
        seen.extend(chunk.id for chunk in page.chunks)
        offset = page.next_offset
        if offset is None:
            break
    assert offset is None, "paging did not terminate"
    assert len(seen) == 25
    assert len(set(seen)) == 25, "a page repeated records from an earlier one"
    assert seen == sorted(seen), "paging did not preserve document order"


def test_paging_a_filtered_query_counts_matches_not_lines(tmp_path: Path) -> None:
    """`offset` has to mean the same thing with and without a query, or a
    client needs two paging loops."""
    bundle = _chunk_bundle(tmp_path / "roots" / "capture.ai", 25)
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(roots=[tmp_path / "roots"]))
    )
    first = service.query_chunks(str(bundle), query="number 1", limit=3)
    assert first.matched == 11  # 1, 10..19
    assert first.returned == 3
    second = service.query_chunks(
        str(bundle), query="number 1", limit=3, offset=first.next_offset or 0
    )
    assert {chunk.id for chunk in first.chunks} & {
        chunk.id for chunk in second.chunks
    } == set()


def test_an_offset_past_the_end_terminates(tmp_path: Path) -> None:
    bundle = _chunk_bundle(tmp_path / "roots" / "capture.ai", 3)
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(roots=[tmp_path / "roots"]))
    )
    page = service.query_chunks(str(bundle), offset=99)
    assert page.chunks == []
    assert page.next_offset is None, "an empty page must not point at itself"


def test_a_negative_offset_is_refused(tmp_path: Path) -> None:
    bundle = _chunk_bundle(tmp_path / "roots" / "capture.ai", 3)
    service = WebshotService(
        WebshotConfig(mcp=McpServerSettings(roots=[tmp_path / "roots"]))
    )
    with pytest.raises(Exception, match="offset cannot be negative"):
        service.query_chunks(str(bundle), offset=-1)


@pytest.mark.parametrize("limit", [4, 5, 7, 11, 64])
def test_markdown_paging_never_loses_a_character(limit: int, tmp_path: Path) -> None:
    """A window too small for one code point used to destroy it.

    `_incomplete_tail` shrinks a page off a split character, but when the whole
    window *is* one incomplete character there is nothing to shrink to — so
    `limit=1` on `é` returned the lead byte alone, decoding to U+FFFD, and the
    next page began on the continuation byte and decoded to U+FFFD too. The
    character appeared on neither page (docs/09 P8-64). Four bytes is now the
    floor, and at the floor a 4-byte code point still round-trips.
    """
    from webshot.mcp_server.service import _read_window

    body = "é☃𝄞 ascii tail ünïcödé"
    path = tmp_path / "content.md"
    path.write_text(body, encoding="utf-8")

    pages, offset = [], 0
    for _ in range(500):  # bounded: a paging bug must not hang the suite
        chunk, next_offset, _total = _read_window(path, offset, limit, text=True)
        assert chunk, "a page that advances by nothing is a loop"
        pages.append(chunk.decode("utf-8", errors="replace"))
        if next_offset is None:
            break
        offset = next_offset
    assert "".join(pages) == body
    assert "�" not in "".join(pages)


def test_an_asset_window_is_not_given_character_boundaries(tmp_path: Path) -> None:
    """Bytes have no characters to split, and an asset page is bytes."""
    from webshot.mcp_server.service import _read_window

    path = tmp_path / "asset.png"
    path.write_bytes(bytes(range(200, 256)))
    chunk, next_offset, total = _read_window(path, 0, 4)
    assert len(chunk) == 4, "a binary page must be exactly the window asked for"
    assert next_offset == 4 and total == 56


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_a_text_window_too_small_for_a_character_is_refused(
    limit: int, tmp_path: Path
) -> None:
    """Refused, not rounded up: rounding up puts a page over a cap the server
    declares, and the caller learns nothing (docs/09 P8-64)."""
    from webshot.mcp_server.service import _window

    with pytest.raises(Exception, match="at least 4 bytes for text"):
        _window(limit, 64, text=True)
    # A binary window has no characters to split and keeps the old floor.
    assert _window(limit, 64) == limit
