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
by half.  What a list's number is, is shared for the same reason: the page
rendered from Markdown is captured, and its snapshot is sanitized again.

Phase 2 put the snapshot on nh3 and called `capture/snapshot.py` "nh3's one
module"; Phase 3 needed a second allowlist, which is a second *policy*, not a
second importer (docs/09 P3-8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import nh3

#: Spec §6.5: no URL scheme outside these survives sanitization, in either
#: document. One scheme narrower than v2's bleach call for local documents,
#: which also allowed `file:`.
URL_SCHEMES = {"http", "https", "mailto", "data"}

#: Attributes whose value is a number, kept as the number Chromium reads from
#: them and dropped where it reads none, in either document.  A policy still
#: has to allow one for it to survive; this decides only what survives.  The
#: value is otherwise the page's text: docling read `start="²"` with `int()`
#: and failed the capture, and read `start="٣"` as 3 where Chromium numbered
#: the list from 1 (docs/09 P30).
INTEGER_ATTRIBUTES = frozenset({("ol", "start")})

#: HTML's rules for parsing integers: ASCII whitespace, a sign, ASCII digits,
#: and anything after them ignored.  `\d` would take `٣`, which Chromium does
#: not.
_HTML_INTEGER = re.compile(r"[\t\n\f\r ]*([-+]?)([0-9]+)")

#: Chromium keeps a parsed integer only within a signed 32-bit int; outside
#: it, the attribute counts as absent (measured in docs/09 P30).
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
#: Digits past this cannot be in range, so they are refused before `int()`
#: sees them: Python's `int()` raises on more than 4,300.
_INT_DIGITS = len(str(_INT_MAX))


def html_integer(value: str) -> int | None:
    """The integer Chromium reads from an attribute value, or None for none."""
    match = _HTML_INTEGER.match(value)
    if match is None:
        return None
    digits = match.group(2).lstrip("0") or "0"
    if len(digits) > _INT_DIGITS:
        return None
    number = int(digits)
    if match.group(1) == "-":
        number = -number
    return number if _INT_MIN <= number <= _INT_MAX else None


#: Must not raise. nh3 reports an exception from its `attribute_filter` as
#: unraisable and keeps the attribute as written, so a filter that fails,
#: fails open: a 5,000-digit `start` reached docling, whose `int()` failed the
#: capture (docs/09 P30-4).
def _filter_attribute(tag: str, attribute: str, value: str) -> str | None:
    if (tag, attribute) not in INTEGER_ATTRIBUTES:
        return value
    number = html_integer(value)
    return None if number is None else str(number)


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
        attribute_filter=_filter_attribute,
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
