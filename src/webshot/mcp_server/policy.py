"""The MCP boundary: what an agent-driven WebShot is allowed to touch.

The CLI and this server run the same pipeline, and they are not the same threat
model.  A person typing `webshot https://…` chose the URL.  An agent calling
`capture` may be acting on text it read in a page WebShot itself captured
ten seconds earlier, so every parameter here is treated as attacker-controlled
and every rule is deny-by-default (docs/04-spec.md §6.8, ADR-0009).

Nothing in this file imports the MCP SDK: these are ordinary functions over
ordinary values, which is what lets `tests/test_mcp_policy.py` prove each
refusal without a protocol round-trip.  `server.py` is the bridge that turns a
`Denied` into a tool error.
"""

from __future__ import annotations

import re
import stat
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import netpolicy, winacl
from ..errors import UsageError

#: A resolver with `socket.getaddrinfo`'s signature, injected so the network
#: rules can be tested without DNS.
Resolver = netpolicy.Resolver

#: Profile *names*, not paths and not content: one path-free token that has to
#: match a key in `[mcp.auth_profiles]`.
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

#: URL schemes an MCP capture may be pointed at. `file:` is here because a
#: local document is a legitimate capture target, and it is confined to the
#: configured roots by `check_source`.
ALLOWED_SCHEMES = frozenset({"http", "https", "file"})

#: Hostnames that name the local machine without going through DNS.
LOCAL_HOSTNAMES = netpolicy.LOCAL_HOSTNAMES


class Denied(UsageError):
    """An MCP request the policy refuses.

    A `UsageError` — the request is impossible as asked, not a failure of the
    capture — so exit code 2 keeps one definition rather than two.  Over MCP it becomes a tool error whose message says which
    rule refused and what a legitimate caller should do instead — a denial an
    agent cannot act on just becomes a retry loop.
    """


# --------------------------------------------------------------------------- #
# Filesystem confinement (guardrail (a))
# --------------------------------------------------------------------------- #


def normalize_roots(roots: Iterable[Path], output_root: Path) -> tuple[Path, ...]:
    """The directories this server may read, output root always included.

    Resolved eagerly, and resolved *through symlinks*: a root given as a
    symlink and a path arriving under its target must compare equal, or the
    confinement check would refuse legitimate reads and — worse — could be made
    to accept illegitimate ones by choosing which spelling to compare.
    """
    resolved = [output_root.expanduser().resolve()]
    for root in roots:
        candidate = root.expanduser().resolve()
        if candidate not in resolved:
            resolved.append(candidate)
    return tuple(resolved)


@dataclass(frozen=True, slots=True)
class Boundary:
    """The resolved edges of one server: where it may write, and what it may read.

    Derived once, from the config, with no protocol involved — which is what
    lets `webshot mcp --print-roots` show an operator the boundary on a machine
    that has not installed the MCP extra (docs/09 P4-9).
    """

    output_root: Path
    roots: tuple[Path, ...]

    @classmethod
    def of(cls, settings: Any) -> Boundary:
        output_root = settings.output_root.expanduser().resolve()
        return cls(output_root, normalize_roots(settings.roots, output_root))


def resolve_within(candidate: str | Path, roots: Sequence[Path], *, what: str) -> Path:
    """Resolve a caller-supplied path and prove it lands inside a root.

    Three escapes are refused here and tested in `tests/test_mcp_policy.py`:

    * an **absolute path** outside every root (`/etc/passwd`);
    * **`..` traversal**, which `Path.resolve()` normalizes away before the
      comparison, so `<root>/../../etc` is compared as `/etc`;
    * a **symlink** inside a root pointing out of it — `resolve()` follows it,
      so the comparison sees the target rather than the link.

    `~` is *not* expanded.  Tilde expansion is a convenience for a person at a
    keyboard; from an agent, `~/.ssh/id_rsa` is a request WebShot has no reason
    to be able to satisfy, so the character is treated as an ordinary one.
    """
    path = Path(candidate)
    if not path.parts:
        raise Denied(f"{what} is empty.")
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        # A path the operating system cannot even look at — an embedded null
        # byte is the reachable case, and `Path.resolve()` raises `ValueError`
        # from `lstat` for it. The guardrail's contract is that a path it will
        # not accept comes back as a refusal with a reason, not as whatever
        # exception the platform happened to raise (docs/09 P4-11).
        raise Denied(f"{what} is not a usable path: {exc}") from exc
    for root in roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    allowed = ", ".join(str(root) for root in roots)
    raise Denied(
        f"{what} is outside this server's allowed roots. "
        f"Configured roots: {allowed}. Add the directory to [mcp] roots in the "
        "server's webshot.toml if it should be readable."
    )


#: The staging directory's name prefix. It lives inside `output_root` so that
#: publication is a rename on one filesystem, and `output_root` is always a
#: readable root — so without this, a read tool could reach a capture whose
#: `final_url` check has not run yet (docs/09 P4-12).
STAGING_PREFIX = ".mcp-staging."


def resolve_bundle(candidate: str | Path, roots: Sequence[Path]) -> Path:
    """A bundle directory a read tool was pointed at."""
    bundle = resolve_within(candidate, roots, what="bundle path")
    if any(part.startswith(STAGING_PREFIX) for part in bundle.parts):
        raise Denied(
            "That path is inside a capture that is still being staged. A "
            "capture becomes readable when it is published, which is after "
            "every check on it has passed."
        )
    if not bundle.is_dir():
        raise Denied(f"No bundle directory at {bundle}.")
    if not (bundle / "manifest.json").is_file():
        raise Denied(f"{bundle} has no manifest.json, so it is not a WebShot bundle.")
    return bundle


def resolve_in_bundle(bundle: Path, relative: str, roots: Sequence[Path]) -> Path:
    """A file named by a bundle-relative path, confined twice.

    Once to the bundle — a manifest entry is data, and `assets.json` could name
    `../../secrets` as easily as `assets/img-1.png` — and once to the roots,
    which is what a symlinked bundle would otherwise get around.
    """
    if Path(relative).is_absolute():
        raise Denied(f"Asset path must be relative to the bundle: {relative!r}.")
    try:
        resolved = (bundle / relative).resolve()
    except (OSError, ValueError) as exc:
        raise Denied(f"asset path is not a usable path: {exc}") from exc
    if resolved != bundle and not resolved.is_relative_to(bundle):
        raise Denied(f"{relative!r} resolves outside its bundle.")
    return resolve_within(resolved, roots, what="asset path")


# --------------------------------------------------------------------------- #
# Network policy (guardrail (c))
# --------------------------------------------------------------------------- #


def check_http_target(
    url: str,
    *,
    allow_private: bool,
    resolver: Resolver | None = None,
    what: str = "URL",
) -> None:
    """Refuse an http(s) target that names the host or a network behind it.

    Every address the name resolves to is checked, not just the first: a name
    with one public and one loopback record must be refused, or the check is
    decided by resolver ordering.

    This cannot close the gap between *this* lookup and the browser's own
    (a name whose answer changes in between — DNS rebinding).  What it does
    close is the case an agent can actually steer: being handed an internal URL,
    or a public one that redirects inward, which is why `check_final_url` runs
    the same rule again on what was really loaded.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in LOCAL_HOSTNAMES or host.endswith(".localhost"):
        if not allow_private:
            raise _private_denial(what, url, host or "(no host)")
        return
    if allow_private:
        return
    try:
        readings = netpolicy.readings(host, resolver)
    except netpolicy.UnresolvableHost as exc:
        raise Denied(
            f"{host} could not be resolved, so this server cannot tell whether it "
            f"is an internal address ({exc}). The capture was not attempted."
        ) from exc
    for reading in readings:
        if netpolicy.is_internal(reading):
            raise _private_denial(what, url, f"{host} = {reading}")


def _private_denial(what: str, url: str, detail: str) -> Denied:
    return Denied(
        f"{what} {url} points at a private, loopback, or link-local address "
        f"({detail}). MCP captures refuse internal targets by default so that a "
        "prompt-injected agent cannot use WebShot to read this host's network. "
        "An operator who wants localhost captures sets "
        "[mcp] allow_private_networks = true in the server's webshot.toml."
    )


def check_source(
    source: str,
    *,
    roots: Sequence[Path],
    allow_private: bool,
    resolver: Resolver | None = None,
) -> str:
    """Validate the one parameter every capture starts from.

    Returns the source unchanged when it is allowed, so a caller cannot forget
    to use the checked value.
    """
    parsed = urlparse(source)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        try:
            local = netpolicy.file_url_path(source)
        except ValueError as exc:
            raise Denied(
                f"file:// source is not a path on this machine: {exc}. Pass a "
                "file:/// URL or a plain path inside the configured roots."
            ) from exc
        resolve_within(local, roots, what="file:// source")
        return source
    if scheme in ALLOWED_SCHEMES:  # http, https — file: handled above
        # Userinfo is a credential, and spec §6.8 says this surface takes none.
        # Refused rather than stripped: the caller asked for an authenticated
        # fetch, and quietly performing an unauthenticated one would answer a
        # different question. Refused before anything is logged, because the
        # service logs the source string, derives the published filename from
        # it, and records it in the manifest and the summary — so a password in
        # the URL ended up on disk in four places and in the agent's transcript
        # (docs/09 P8-15).
        parsed_credentials = urlparse(source)
        if parsed_credentials.username or parsed_credentials.password:
            raise Denied(
                "The source URL carries embedded credentials. MCP captures take "
                "no credential-bearing parameters (docs/04-spec.md §6.8); use a "
                "configured auth profile name instead."
            )
        check_http_target(
            source, allow_private=allow_private, resolver=resolver, what="source"
        )
        return source
    if not scheme or _names_a_drive(scheme):
        # A bare path from an agent is a filesystem read wearing a URL's
        # clothes; it goes through the same confinement a file:// URL does.
        resolve_within(source, roots, what="source path")
        return source
    raise Denied(
        f"Unsupported source scheme {scheme!r}. MCP captures accept http, https, "
        f"and file paths inside the configured roots."
    )


def _names_a_drive(scheme: str) -> bool:
    """`C:\\page.html` parses with the scheme `c`. On Windows that is a path.

    No URL scheme is one letter long, so nothing else is read as a path; and
    only on Windows, where a drive letter is what the letter can be.
    """
    return sys.platform == "win32" and len(scheme) == 1


def check_final_url(
    final_url: str,
    *,
    allow_private: bool,
    resolver: Resolver | None = None,
) -> None:
    """Re-run the network rule on the URL that was actually loaded.

    A public URL that redirects to `http://169.254.169.254/` has fetched an
    internal page by the time this runs, which is why the MCP capture stages its
    output and only publishes after this returns (docs/04-spec.md §6.8).
    """
    if urlparse(final_url).scheme.lower() in {"http", "https"}:
        check_http_target(
            final_url,
            allow_private=allow_private,
            resolver=resolver,
            what="the redirect target",
        )


# --------------------------------------------------------------------------- #
# Credentials (guardrails (d) and (e))
# --------------------------------------------------------------------------- #


def resolve_auth_profile(name: str, profiles: dict[str, Path]) -> Path:
    """Turn an authentication profile *name* into the directory it configures.

    The MCP surface has no parameter that can carry a credential: no
    storage-state path, no storage-state JSON, no profile directory.  A name is
    a lookup key into `[mcp.auth_profiles]`, and anything that is not shaped
    like one is refused before the lookup so that the error explains the rule
    rather than reporting a missing key.
    """
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise Denied(
            "auth_profile takes the *name* of a profile configured in "
            "[mcp.auth_profiles] — not a path, and never storage-state "
            "content. WebShot's MCP tools accept no credential-bearing "
            "parameters (docs/04-spec.md §6.8)."
        )
    if name not in profiles:
        available = ", ".join(sorted(profiles)) or "none configured"
        raise Denied(
            f"No authentication profile named {name!r}. "
            f"Profiles this server offers: {available}."
        )
    return profiles[name]


def check_profile_ready(name: str, directory: Path) -> Path:
    """Refuse a profile whose first run has to happen at a keyboard.

    A persistent Chromium profile is created by signing in, which needs a
    headed browser and a human.  An MCP server has neither, so an agent asking
    for an unprepared profile gets the instruction rather than a browser that
    hangs waiting for a terminal nobody is watching (docs/04-spec.md §5.8).
    """
    if not directory.is_dir() or not any(directory.iterdir()):
        raise Denied(
            f"Authentication profile {name!r} has not been signed in yet. "
            f"Run this in a terminal first:\n"
            f"    webshot --interactive-auth --auth-profile {directory} <url>\n"
            "The MCP server cannot open a headed browser or complete MFA."
        )
    if sys.platform == "win32":
        # Mode bits describe nothing here: Windows reports 0o777 (or 0o555)
        # whatever the ACL grants (docs/09 P10-24). The ACL is the check, over
        # every entry, because Windows skips traverse checks by default and a
        # private directory does not make its files private (docs/09 P10-25).
        found = winacl.find_exposure(directory)
        if found is not None:
            raise Denied(
                f"Authentication profile {directory} is not owner-only: {found}. "
                f"{found.remedy} A capture with this profile run from a terminal "
                f"(`webshot --auth-profile {directory} <url>`) makes the directory "
                "owner-only and names anything inside it that it cannot fix "
                "(docs/04-spec.md §6.3)."
            )
        return directory
    mode = directory.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise Denied(
            f"Authentication profile {directory} is group- or world-accessible "
            f"(mode {stat.filemode(mode)}). Restore owner-only permissions with "
            f"`chmod 700 {directory}` before using it (docs/04-spec.md §6.3)."
        )
    return directory
