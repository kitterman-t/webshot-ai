"""What counts as an internal network target.

Extracted from `mcp_server/policy.py` so that two layers can share one answer:
the MCP guardrail, which decides whether a *capture may be requested*, and the
capture session, which decides whether a *page may fetch a subresource*. Those
are different questions with the same classifier, and a second copy of it would
be a second thing to get wrong (docs/09 P4-13).

This module imports nothing but the standard library, and is a leaf: `capture/`
depends on it without depending on the MCP server.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

#: A resolver with `socket.getaddrinfo`'s signature, injected so the network
#: rules can be tested without DNS.
Resolver = Callable[[str, int | None], list[tuple]]

#: Hostnames that name the local machine without going through DNS.
LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", ""})


class UnresolvableHost(Exception):
    """A name with no literal reading that DNS could not answer for."""


#: IPv6 ranges refused whole, whatever IPv4 address they may carry.
_INTERNAL_IPV6 = (
    # Deprecated site-local (RFC 3879), IPv6's counterpart of RFC 1918.
    # `is_global` calls it public, measured on Python 3.11 to 3.14
    # (docs/09 P10-30).
    ipaddress.IPv6Network("fec0::/10"),
    # Local-use NAT64 (RFC 8215). The operator picks the prefix length inside
    # it, so where an IPv4 address sits is a fact about the deployment, not
    # the address, and there is no one reading to unwrap.
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    # Teredo (RFC 4380). The address is a tunnel endpoint that carries a
    # server and an obfuscated client IPv4 address. `is_global` already calls
    # the range non-global, so classifying by what it carries could only turn
    # a refusal into an acceptance. Named here, with the /48 above, so neither
    # refusal rests on Python's copy of the registry.
    ipaddress.IPv6Network("2001::/32"),
)

#: NAT64's well-known prefix (RFC 6052): `64:ff9b::a.b.c.d` is how an
#: IPv6-only network reaches `a.b.c.d` through a translator.
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")

#: IPv4-translated addresses (RFC 2765, SIIT): `::ffff:0:a.b.c.d`.
_IPV4_TRANSLATED = ipaddress.IPv6Network("::ffff:0:0:0/96")


def is_internal(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is anything other than a public destination.

    `is_global` is the predicate that actually means "allocated for the public
    internet", and its negation is what this rule wants. The obvious spelling —
    `is_private or is_loopback or is_link_local or is_reserved` — is *narrower*
    than it looks: `100.64.0.1` (RFC 6598 carrier-grade NAT) is neither private
    nor reserved by that reading, so an enumerated check let it through while
    the docstring claimed CGNAT was covered. The boundary matrix caught it
    (docs/09 P4-11).

    Deliberately wider than the ranges docs/04-spec.md §6.8 enumerates
    (RFC1918, 127/8, 169.254/16, ::1, fd00::/8): CGNAT, the documentation and
    benchmarking blocks, multicast, and future-use space are refused too, and a
    Python that learns about a new special-purpose registry entry tightens this
    rule for free — except multicast, which `is_global` does not classify and
    which is therefore named explicitly below. A default that errs toward refusal can be opened by an
    operator who knows what their network is; one that errs toward reachability
    cannot be closed after the fact.

    An IPv6 address is refused outright if it falls in one of the three ranges
    in `_INTERNAL_IPV6`, and is otherwise classified as the IPv4 address it
    spells, if it spells one (docs/09 P4-12, P10-30).
    """
    if isinstance(address, ipaddress.IPv6Address):
        if any(address in network for network in _INTERNAL_IPV6):
            return True
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            address = embedded
    # Multicast is spelled out because `is_global` does *not* cover it: both
    # `224.0.0.0/4` and `ff00::/8` report `is_global == True`. Replacing the
    # enumerated check with `not is_global` fixed CGNAT and dropped multicast
    # in the same edit, which is how `http://239.255.255.250:1900/` (SSDP) and
    # `http://[ff02::1]/` (mDNS) became reachable (docs/09 P4-12).
    return address.is_multicast or not address.is_global


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address this IPv6 one is another spelling of, if it is one.

    Five forms: IPv4-mapped, IPv4-compatible, 6to4, NAT64's well-known prefix
    and IPv4-translated. In each the IPv4 address is the destination, so it is
    what gets classified, and a public one stays reachable. `is_global` calls
    the IPv6 spelling public in three of them whatever it carries:
    `::127.0.0.1` (IPv4-compatible, `::/96`) normalises to `::7f00:1`
    (docs/09 P4-12), and `64:ff9b::a9fe:a9fe` (NAT64) and `::ffff:0:a9fe:a9fe`
    (IPv4-translated) both carry `169.254.169.254`, the cloud metadata
    endpoint (docs/09 P10-30). RFC 6052 forbids the NAT64 prefix to carry a
    non-global address, but that is a rule for translators, not a fact about
    the address a page names.
    """
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if (
        0 < int(address) < 2**32
        or address in _NAT64_WELL_KNOWN
        or address in _IPV4_TRANSLATED
    ):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


#: Schemes that carry a network target this module can classify. `ws`/`wss`
#: are here because a WebSocket to `ws://127.0.0.1:PORT` reaches the same
#: service `http://127.0.0.1:PORT` does, and the URL is the same shape — the
#: only thing that differed was that nothing asked (docs/09 P8-10).
NETWORK_SCHEMES = frozenset({"http", "https", "ws", "wss"})


def is_internal_url(url: str, resolver: Resolver | None = None) -> bool:
    """Whether this URL names the host or a network behind it.

    `True` for a host that cannot be resolved at all: a target WebShot cannot
    classify is one it should not reach.

    `False` for a scheme with no network target — `data:`, `blob:`, `about:`.
    `file:` is one of those as far as *this* module is concerned; it has no
    host, and what confines it is `is_outside_roots` below, not this function.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in NETWORK_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in LOCAL_HOSTNAMES or host.endswith(".localhost"):
        return True
    try:
        found = readings(host, resolver)
    except UnresolvableHost:
        return True
    return any(is_internal(reading) for reading in found)


def readings(
    host: str, resolver: Resolver | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address this host string can plausibly mean.

    **All** of them, not the first: the readings disagree, and which one is
    authoritative depends on software WebShot does not control. Deny-by-default
    therefore means one internal reading is enough to refuse the target.

    A host with no literal reading at all is an ordinary name, and DNS is the
    only reading it has — so a name that cannot be resolved is still refused
    rather than attempted. A host that *does* have a literal reading is still
    resolved too, because the resolver's own interpretation of a numeric string
    is a reading in its own right (docs/09 P4-11).
    """
    literals = _literal_addresses(host)
    try:
        resolved = [ipaddress.ip_address(a) for a in _addresses_for(host, resolver)]
    except UnresolvableHost:
        if not literals:
            raise
        # A numeric host the resolver would not touch is still fully classified
        # by its literal readings; refusing it for that would be nonsense.
        resolved = []
    return [*literals, *resolved]


def _literal_addresses(
    host: str,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address a host string can be read as *without* asking DNS.

    Two readings, because the platform resolver and the browser do not agree.
    `ipaddress` is strict — it rejects `0177.0.0.1` outright, since Python
    3.9.5, precisely because the leading zero is ambiguous. `inet_aton` applies
    the legacy rules the WHATWG URL parser (and therefore Chromium) also
    applies: octal, hex, and short forms. On macOS
    `getaddrinfo("0177.0.0.1")` answers **177.0.0.1** — a public address —
    while `inet_aton` and the browser both read **127.0.0.1**.

    Trusting the resolver alone therefore let a capture through the guardrail
    and straight at loopback (docs/09 P4-11).
    """
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        found.append(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        found.append(ipaddress.IPv4Address(socket.inet_aton(host)))
    except (OSError, ValueError):
        pass
    return found


def _addresses_for(host: str, resolver: Resolver | None) -> list[str]:
    resolve = resolver or (lambda name, port: socket.getaddrinfo(name, port))
    try:
        records = resolve(host, None)
    except OSError as exc:
        raise UnresolvableHost(str(exc)) from exc
    found = []
    for record in records:
        sockaddr = record[4]
        if sockaddr and isinstance(sockaddr[0], str):
            found.append(sockaddr[0])
    if not found:
        raise UnresolvableHost(f"{host} resolved to no addresses")
    return found


#: The hosts a `file:` URL may name and still mean this machine.
_THIS_MACHINE = frozenset({"", "localhost"})


def file_url_path(url: str) -> Path:
    """The path on this machine that a `file:` URL names, or `ValueError`.

    `Path(unquote(urlparse(url).path))` is the inverse of `Path.as_uri()` on
    POSIX only. On Windows the URL path of `file:///C:/x` is `/C:/x`, which
    pathlib read as the drive-relative `C:x`, so every local source, every MCP
    root check and every `file:` subresource check misread a Windows path
    (docs/09 P10-24). `url2pathname` is the standard library's inverse on
    each platform, and on POSIX it is `unquote` exactly.

    A URL naming another host is refused rather than reduced to its path. On
    Windows `file://server/share/x` is a UNC path on that server, while every
    check here would have judged the local `/share/x`.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "file":
        raise ValueError(f"not a file: URL: {url}")
    if parsed.netloc.lower() not in _THIS_MACHINE:
        raise ValueError(
            f"{url} names the host {parsed.netloc!r}; a file: URL here must name "
            "this machine (file:///path or file://localhost/path)"
        )
    try:
        return Path(url2pathname(parsed.path))
    except OSError as exc:  # Windows: `nturl2path` raises it for a malformed drive
        raise ValueError(f"{url} is not a usable file: URL: {exc}") from exc


def is_outside_roots(url: str, roots: Sequence[Path]) -> bool:
    """Whether a `file:` request escapes every configured root.

    Only `file:` URLs are judged — everything else has no filesystem target
    and is somebody else's question.  `False` when there are no roots at all:
    an ordinary CLI capture has the whole filesystem available to the user who
    started it, and confining a local document's own images to a directory
    nobody configured would break the plain case for no gain.

    **Why this is needed at all.** The MCP boundary confines the *source* to
    the configured roots (spec §6.8a), and stopped there. A local HTML or
    Markdown document inside a root can reference `file:///anything`, or a
    relative path climbing out of one; Playwright routes those subresource
    requests, and the classifier waved every non-HTTP scheme through as safe.
    Chromium then rendered the outside file into the PDF and harvested it as a
    visual asset — measured, not theorised (docs/09 P8-11).

    Symlinks are resolved before comparing, because the confinement is about
    which bytes are read, not which path was typed.
    """
    if not roots:
        return False
    if urlparse(url).scheme.lower() != "file":
        return False
    try:
        target = file_url_path(url).resolve()
    except (OSError, ValueError):
        return True
    for root in roots:
        try:
            resolved_root = Path(root).resolve()
        except (OSError, ValueError):
            continue
        if target == resolved_root or resolved_root in target.parents:
            return False
    return True
