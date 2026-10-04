"""Every MCP guardrail, as a refusal that can be pointed at.

docs/04-spec.md §6.8 makes each of these a MUST, and docs/10's traceability row
for §6.8 says they are proven by "traversal + credential-param rejection
tests".  This file is that proof at the function level; `tests/test_mcp_server.py`
runs the same denials again through a real client session, because a rule that
holds in a unit test and not over the wire has not held.

These tests import no MCP SDK: `policy.py` is deliberately protocol-free.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from webshot.mcp_server import policy
from webshot.mcp_server.policy import Denied

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def fake_resolver(mapping: dict[str, list[str]]):
    """A `getaddrinfo` stand-in, so the network rules need no DNS."""

    def resolve(host: str, port: int | None) -> list[tuple]:
        if host not in mapping:
            raise OSError(f"unknown host {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))
            for address in mapping[host]
        ]

    return resolve


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, ...]:
    (tmp_path / "allowed").mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "keys.txt").write_text("hunter2", encoding="utf-8")
    return policy.normalize_roots([tmp_path / "allowed"], tmp_path / "allowed" / "out")


# --------------------------------------------------------------------------- #
# (a) Filesystem confinement
# --------------------------------------------------------------------------- #


def test_a_path_inside_a_root_resolves(roots: tuple[Path, ...], tmp_path: Path) -> None:
    wanted = tmp_path / "allowed" / "bundle" / "content.md"
    assert policy.resolve_within(wanted, roots, what="path") == wanted


def test_an_absolute_path_outside_every_root_is_refused(
    roots: tuple[Path, ...],
) -> None:
    with pytest.raises(Denied) as failure:
        policy.resolve_within("/etc/passwd", roots, what="bundle path")
    assert "outside this server's allowed roots" in str(failure.value)


def test_dot_dot_traversal_is_refused(roots: tuple[Path, ...], tmp_path: Path) -> None:
    escape = tmp_path / "allowed" / ".." / "secret" / "keys.txt"
    with pytest.raises(Denied):
        policy.resolve_within(escape, roots, what="bundle path")


def test_a_symlink_pointing_out_of_a_root_is_refused(
    roots: tuple[Path, ...], tmp_path: Path
) -> None:
    """The escape a string comparison misses.

    `<root>/escape` is inside the root by every textual test; only resolving it
    shows that reading it reads `<tmp>/secret/keys.txt`.
    """
    link = tmp_path / "allowed" / "escape"
    link.symlink_to(tmp_path / "secret")
    with pytest.raises(Denied):
        policy.resolve_within(link / "keys.txt", roots, what="bundle path")


def test_a_root_that_is_itself_a_symlink_still_works(tmp_path: Path) -> None:
    """Resolution must be symmetric, or a legitimate read is refused."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    resolved = policy.normalize_roots([link], link / "out")
    assert policy.resolve_within(real / "file.md", resolved, what="path")


def test_a_tilde_is_not_expanded(roots: tuple[Path, ...]) -> None:
    """`~/.ssh/id_rsa` from an agent is a path, not a home-directory shortcut."""
    with pytest.raises(Denied):
        policy.resolve_within("~/.ssh/id_rsa", roots, what="bundle path")


def test_the_output_root_is_always_readable(tmp_path: Path) -> None:
    """A server with no configured roots can still read what it produced."""
    resolved = policy.normalize_roots([], tmp_path / "out")
    assert policy.resolve_within(tmp_path / "out" / "a.ai", resolved, what="path")


def test_an_empty_path_is_refused(roots: tuple[Path, ...]) -> None:
    with pytest.raises(Denied):
        policy.resolve_within("", roots, what="bundle path")


def test_a_bundle_without_a_manifest_is_not_a_bundle(
    roots: tuple[Path, ...], tmp_path: Path
) -> None:
    directory = tmp_path / "allowed" / "not-a-bundle"
    directory.mkdir()
    with pytest.raises(Denied) as failure:
        policy.resolve_bundle(directory, roots)
    assert "manifest.json" in str(failure.value)


def test_an_asset_path_from_the_manifest_cannot_escape_its_bundle(
    roots: tuple[Path, ...], tmp_path: Path
) -> None:
    """A manifest is data, and data from a captured page names its own files."""
    bundle = tmp_path / "allowed" / "page.ai"
    bundle.mkdir()
    with pytest.raises(Denied):
        policy.resolve_in_bundle(bundle, "../../secret/keys.txt", roots)
    with pytest.raises(Denied):
        policy.resolve_in_bundle(bundle, "/etc/passwd", roots)


# --------------------------------------------------------------------------- #
# (c) Network policy
# --------------------------------------------------------------------------- #

INTERNAL_URLS = [
    "http://127.0.0.1:8080/dashboard",  # 127/8
    "http://10.0.0.5/admin",  # RFC1918
    "http://172.16.4.2/",  # RFC1918
    "http://192.168.1.1/",  # RFC1918
    "http://169.254.169.254/latest/meta-data/",  # link-local (cloud metadata)
    "http://[::1]:9000/",  # IPv6 loopback
    "http://[fd00::1]/",  # IPv6 unique-local
    "http://localhost:3000/",  # by name, without DNS
    "http://app.localhost/",  # the .localhost suffix
]


@pytest.mark.parametrize("url", INTERNAL_URLS, ids=lambda url: url)
def test_internal_targets_are_refused_by_default(url: str) -> None:
    with pytest.raises(Denied) as failure:
        policy.check_http_target(url, allow_private=False)
    assert "refuse internal targets by default" in str(failure.value)


@pytest.mark.parametrize("url", INTERNAL_URLS, ids=lambda url: url)
def test_the_operator_can_opt_in_to_internal_targets(url: str) -> None:
    """The legitimate case §6.8 names: an agent capturing a localhost dashboard."""
    policy.check_http_target(url, allow_private=True)


def test_a_public_name_is_allowed() -> None:
    resolver = fake_resolver({"example.com": ["93.184.216.34"]})
    policy.check_http_target(
        "https://example.com/a", allow_private=False, resolver=resolver
    )


def test_a_name_that_resolves_inward_is_refused() -> None:
    """The interesting case: nothing about the URL looks internal."""
    resolver = fake_resolver({"intranet.example.com": ["10.1.2.3"]})
    with pytest.raises(Denied) as failure:
        policy.check_http_target(
            "https://intranet.example.com/", allow_private=False, resolver=resolver
        )
    assert "10.1.2.3" in str(failure.value)


def test_every_address_a_name_resolves_to_is_checked() -> None:
    """One public answer must not launder a loopback one."""
    resolver = fake_resolver({"split.example.com": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(Denied):
        policy.check_http_target(
            "https://split.example.com/", allow_private=False, resolver=resolver
        )


def test_an_ipv4_mapped_ipv6_loopback_is_refused() -> None:
    with pytest.raises(Denied):
        policy.check_http_target("http://[::ffff:127.0.0.1]/", allow_private=False)


def test_a_name_that_cannot_be_resolved_is_refused_rather_than_attempted() -> None:
    with pytest.raises(Denied) as failure:
        policy.check_http_target(
            "https://nowhere.invalid/", allow_private=False, resolver=fake_resolver({})
        )
    assert "could not be resolved" in str(failure.value)


def test_a_redirect_into_the_private_network_is_refused_after_the_fact() -> None:
    """Guardrail (c)'s second half: the URL that was actually loaded.

    A capture that started at a public URL and ended at the cloud metadata
    service must not be published, which is why the MCP capture stages its
    output until this check has run.
    """
    with pytest.raises(Denied) as failure:
        policy.check_final_url(
            "http://169.254.169.254/latest/meta-data/", allow_private=False
        )
    assert "redirect target" in str(failure.value)


def test_a_redirect_that_stays_public_is_fine() -> None:
    resolver = fake_resolver({"www.example.com": ["93.184.216.34"]})
    policy.check_final_url(
        "https://www.example.com/final", allow_private=False, resolver=resolver
    )


def test_a_file_source_is_confined_to_the_roots(
    roots: tuple[Path, ...], tmp_path: Path
) -> None:
    inside = (tmp_path / "allowed" / "page.html").resolve()
    policy.check_source(inside.as_uri(), roots=roots, allow_private=False)
    outside = (tmp_path / "secret" / "keys.txt").resolve()
    with pytest.raises(Denied):
        policy.check_source(outside.as_uri(), roots=roots, allow_private=False)


def test_an_unsupported_scheme_is_refused(roots: tuple[Path, ...]) -> None:
    with pytest.raises(Denied) as failure:
        policy.check_source("ftp://example.com/x", roots=roots, allow_private=False)
    assert "Unsupported source scheme" in str(failure.value)


# --------------------------------------------------------------------------- #
# (d) No credential-bearing parameters
# --------------------------------------------------------------------------- #

PROFILES = {"work": Path("/srv/profiles/work")}

CREDENTIAL_SHAPED = [
    pytest.param(
        '{"cookies": [{"name": "session", "value": "abc"}]}', id="storage-state-json"
    ),
    pytest.param("/Users/someone/.webshot/profiles/work", id="absolute-path"),
    pytest.param("../../etc/passwd", id="relative-path"),
    pytest.param("work profile", id="whitespace"),
    pytest.param("work\nstorage_state=/tmp/x.json", id="newline-injection"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("value", CREDENTIAL_SHAPED)
def test_only_a_profile_name_is_accepted(value: str) -> None:
    """The MCP surface has no parameter that can carry a credential.

    Raw storage-state content is the case docs/04-spec.md §1.3 calls out by
    name: it is refused because it is not shaped like a name, before any
    lookup, so the message can explain the rule.
    """
    with pytest.raises(Denied) as failure:
        policy.resolve_auth_profile(value, PROFILES)
    assert "never storage-state" in str(failure.value)


def test_an_unconfigured_profile_name_is_refused_with_the_available_ones() -> None:
    with pytest.raises(Denied) as failure:
        policy.resolve_auth_profile("staging", PROFILES)
    assert "work" in str(failure.value)


def test_a_configured_profile_name_resolves_to_its_directory() -> None:
    assert policy.resolve_auth_profile("work", PROFILES) == Path("/srv/profiles/work")


# --------------------------------------------------------------------------- #
# (e) Profiles that need an interactive first run
# --------------------------------------------------------------------------- #


def test_an_unprepared_profile_is_refused_with_the_command_to_run(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "profiles" / "work"
    directory.mkdir(parents=True)
    with pytest.raises(Denied) as failure:
        policy.check_profile_ready("work", directory)
    message = str(failure.value)
    assert "--interactive-auth" in message
    assert "cannot open a headed browser" in message


def test_a_missing_profile_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Denied):
        policy.check_profile_ready("work", tmp_path / "absent")


POSIX_MODES = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: chmod sets no ACL on Windows; the ACL check is "
    "test_a_windows_profile_is_judged_by_its_acl and the tests after it",
)


@POSIX_MODES
def test_a_group_readable_profile_is_refused(tmp_path: Path) -> None:
    """spec §6.3: a profile anyone else can read is not a private profile."""
    directory = tmp_path / "profiles" / "work"
    directory.mkdir(parents=True)
    (directory / "Local State").write_text("{}", encoding="utf-8")
    os.chmod(directory, 0o750)
    with pytest.raises(Denied) as failure:
        policy.check_profile_ready("work", directory)
    assert "chmod 700" in str(failure.value)


@POSIX_MODES
def test_a_prepared_owner_only_profile_is_accepted(tmp_path: Path) -> None:
    directory = tmp_path / "profiles" / "work"
    directory.mkdir(parents=True)
    (directory / "Local State").write_text("{}", encoding="utf-8")
    os.chmod(directory, 0o700)
    assert policy.check_profile_ready("work", directory) == directory


def _signed_in(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Local State").write_text("{}", encoding="utf-8")
    return directory


def test_a_windows_profile_is_judged_by_its_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked, not refused wholesale, and never by mode bits (docs/09 P10-25).

    Windows reports a directory as mode 0o777 whatever its ACL grants, so the
    mode check refused every profile there as "world-accessible" (P10-24).
    Run everywhere, with the platform set and the ACL reader replaced, so the
    branch is exercised by the gated suites; the real reader is measured by
    `test_an_owner_only_windows_profile_is_accepted` on the Windows run.
    """
    directory = _signed_in(tmp_path / "profiles" / "work")
    os.chmod(directory, 0o777)  # what Windows reports, so the bits cannot decide
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(policy.winacl, "find_exposure", lambda path: None)
    assert policy.check_profile_ready("work", directory) == directory


def test_a_windows_profile_another_user_can_read_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _signed_in(tmp_path / "profiles" / "work")
    cookies = directory / "Default" / "Network" / "Cookies"
    exposure = policy.winacl.Exposure(
        cookies,
        "grants Everyone (S-1-1-0) access",
        f'`icacls "{cookies}" /remove:g *S-1-1-0` removes it.',
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(policy.winacl, "find_exposure", lambda path: exposure)
    with pytest.raises(Denied) as failure:
        policy.check_profile_ready("work", directory)
    message = str(failure.value)
    assert f"{cookies} grants Everyone (S-1-1-0) access" in message
    assert "icacls" in message and "/remove:g *S-1-1-0" in message
    assert f"webshot --auth-profile {directory} <url>" in message
    assert "chmod" not in message and "mode" not in message


WINDOWS = pytest.mark.skipif(
    sys.platform != "win32", reason="reads and writes a real Windows ACL"
)


@WINDOWS
def test_an_owner_only_windows_profile_is_accepted(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    policy.winacl.create_owner_only_directory(directory)
    _signed_in(directory)
    assert policy.check_profile_ready("work", directory) == directory


@WINDOWS
def test_a_windows_profile_file_everyone_can_read_is_refused(tmp_path: Path) -> None:
    """The directory is private; the file inside it is not, and that is enough.

    Windows skips traverse checks for every user by default, so a readable
    file under a private directory is readable by path (docs/09 P10-25).
    """
    directory = tmp_path / "work"
    policy.winacl.create_owner_only_directory(directory)
    _signed_in(directory)
    subprocess.run(
        ["icacls", str(directory / "Local State"), "/grant", "*S-1-1-0:(R)"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(Denied) as failure:
        policy.check_profile_ready("work", directory)
    assert "Local State grants Everyone (S-1-1-0) access" in str(failure.value)
