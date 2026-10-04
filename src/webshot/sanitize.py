"""The one module that imports nh3 (CONTRIBUTING rule 1).

WebShot publishes two kinds of HTML and holds them to two allowlists: the
captured DOM snapshot, which keeps form and media records because the bundle's
fidelity contract depends on them, and a rendered local document, which keeps
Markdown's own output and nothing else.  Both are policy, so both are data
here — one file where they can be read side by side and diffed against each
other, rather than two independent `nh3.clean` calls in two stages.

That matters most for the part they share.  Spec §6.5 is a MUST with one
sentence in it — no `<script>`, no event handlers, no URL scheme outside
http(s)/data/mailto — and a MUST with two definitions is a MUST that can drift
by half.

Phase 2 put the snapshot on nh3 and called `capture/snapshot.py` "nh3's one
module"; Phase 3 needed a second allowlist, which is a second *policy*, not a
second importer (docs/09 P3-8).
"""

from __future__ import annotations

from dataclasses import dataclass

import nh3

#: Spec §6.5: no URL scheme outside these survives sanitization, in either
#: document. One scheme narrower than v2's bleach call for local documents,
#: which also allowed `file:`.
URL_SCHEMES = {"http", "https", "mailto", "data"}


@dataclass(frozen=True, slots=True)
class Policy:
    """One allowlist: what may survive, and under what attributes."""

    tags: frozenset[str]
    attributes: dict[str, set[str]]
    #: Attribute prefixes allowed on every tag regardless of the map above.
    generic_prefixes: frozenset[str] = frozenset()


def sanitize(html: str, policy: Policy) -> str:
    """Apply one allowlist. The only `nh3.clean` call in the package."""
    return nh3.clean(
        html,
        tags=set(policy.tags),
        attributes=policy.attributes,
        generic_attribute_prefixes=set(policy.generic_prefixes),
        url_schemes=URL_SCHEMES,
    )


def with_nh3_defaults(extra: dict[str, set[str]]) -> dict[str, set[str]]:
    """nh3's per-tag defaults, widened by `extra`.

    Overlap is deliberate where it happens: a policy that names an attribute
    keeps it even if an upstream default stops including it.
    """
    merged = {tag: set(allowed) for tag, allowed in nh3.ALLOWED_ATTRIBUTES.items()}
    for tag, names in extra.items():
        merged[tag] = merged.get(tag, set()) | names
    return merged


def widen_default_tags(extra: set[str]) -> frozenset[str]:
    """nh3's default tag set plus `extra`."""
    return frozenset(nh3.ALLOWED_TAGS | extra)
