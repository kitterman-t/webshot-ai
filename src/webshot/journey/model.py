"""The enumerated tree, its synthetic ids, and the assertions over it.

Pure: nothing here touches a browser, so every rule about what a well-formed
enumeration *is* can be tested without one. The walk lives in `walk.py`.

Identity is a **path, not a server id** (docs/13 P9-4, and the specification's
reversal of navigating by id). Nothing below the journey has an address, so
position in the enumerated tree is the only identity available — which is why
the determinism check in `diff_outlines` is not optional: if two walks of the
same journey disagree, every id from either of them is meaningless.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from itertools import pairwise

#: The five levels, outermost first (docs/13 P9-1). The group between track and
#: module is the level the four-level design missed, and it carries prose that
#: appears nowhere else.
LEVELS: tuple[str, ...] = ("journey", "section", "track", "group", "module")

#: Which level a node's children sit at.
_CHILD_LEVEL = dict(pairwise(LEVELS))


def child_level(level: str) -> str | None:
    """The level below `level`, or None at the leaf."""
    return _CHILD_LEVEL.get(level)


def synthetic_id(parent_id: str, level: str, ordinal: int) -> str:
    """The stable id for the `ordinal`-th `level` under `parent_id`.

    Two-digit ordinals so a lexical sort of the ids is teaching order for the
    journeys this is built for; three-digit would be arbitrary in a different
    direction and P9-5 puts a track at nine groups, not ninety.
    """
    if ordinal < 1:
        raise ValueError(f"ordinal is 1-based, got {ordinal}")
    return f"{parent_id}/{level}-{ordinal:02d}"


@dataclass(frozen=True, slots=True)
class JourneyNode:
    """One node of the enumerated tree.

    `listed` is what the application said it held *after* its children were
    read, and `children` is what was actually read. Keeping both is the whole
    point: they are compared per node rather than summed, because a total
    agrees with itself while a group that silently came back empty hides inside
    it (docs/13 P9-4).
    """

    node_id: str
    level: str
    ordinal: int
    title: str
    children: tuple[JourneyNode, ...] = ()
    listed: int | None = None
    #: The group's own description. Present only on groups, and the reason the
    #: group is a level rather than a heading (P9-1).
    prose: str | None = None
    #: Modules only. `article` or `assessment` as the row renders it, lowercased.
    #: Recorded here so the capture stage has something to refuse with: docs/13
    #: forbids opening an assessment, and an outline of bare titles cannot tell
    #: it which rows those are (P12).
    module_type: str | None = None
    #: Modules only, and authored rather than measured — P9-8 recorded "6 Mins"
    #: against a 251.6 s video. Kept as the LMS renders it.
    duration: str | None = None

    def walk(self) -> Iterator[JourneyNode]:
        """This node, then every descendant, depth-first in teaching order."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True, slots=True)
class JourneyOutline:
    """One enumeration pass over one journey."""

    journey_id: str
    source: str
    title: str
    progress_before: str
    progress_after: str
    root: JourneyNode
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def nodes(self) -> Iterator[JourneyNode]:
        return self.root.walk()

    def modules(self) -> Iterator[JourneyNode]:
        return (node for node in self.nodes() if node.level == "module")

    def rows(self) -> list[tuple[str, str, str]]:
        """`(node_id, level, title)` for every node — the diffable projection.

        Deliberately excludes `listed`: it is an assertion input, not part of
        the shape two walks have to agree on, and including it would let a
        counting bug read as non-determinism.
        """
        return [(node.node_id, node.level, node.title) for node in self.nodes()]


def count_failures(root: JourneyNode) -> list[str]:
    """Every node whose child count disagrees with what the application listed.

    Per node, never in total (docs/13). A journey-wide "247 modules" is
    satisfied by a group that returned nothing, and that is precisely the
    failure the stale-reference hazard produces.
    """
    problems: list[str] = []
    for node in root.walk():
        if node.listed is None:
            continue
        if node.listed != len(node.children):
            problems.append(
                f"{node.node_id} ({node.level} {node.title!r}): the journey listed "
                f"{node.listed} {child_level(node.level) or 'child'}(s), "
                f"{len(node.children)} were read"
            )
    return problems


def diff_outlines(first: JourneyOutline, second: JourneyOutline) -> list[str]:
    """What changed between two enumerations of the same journey.

    Empty means the rendering was deterministic across the two walks, which is
    the evidence that a capture keyed on these ids can be trusted. A difference
    means the walk is racing the application, and docs/13 is explicit that no
    capture from such a walk is usable — so this returns findings rather than a
    boolean, because the operator needs to see *what* moved.
    """
    problems: list[str] = []
    if first.title != second.title:
        problems.append(f"journey title moved: {first.title!r} -> {second.title!r}")
    left = {row[0]: row for row in first.rows()}
    right = {row[0]: row for row in second.rows()}
    for node_id in sorted(set(left) - set(right)):
        problems.append(f"{node_id} ({left[node_id][2]!r}) vanished on the second walk")
    for node_id in sorted(set(right) - set(left)):
        problems.append(
            f"{node_id} ({right[node_id][2]!r}) appeared only on the second walk"
        )
    for node_id in sorted(set(left) & set(right)):
        if left[node_id] != right[node_id]:
            problems.append(
                f"{node_id}: {left[node_id][1:]!r} on the first walk, "
                f"{right[node_id][1:]!r} on the second"
            )
    return problems


def renumber(node: JourneyNode, parent_id: str, ordinal: int) -> JourneyNode:
    """Rebuild `node` and its descendants with ids derived from their position.

    The walk builds titles and structure; identity is assigned here, in one
    place, so an id can never be composed two different ways.
    """
    node_id = (
        parent_id
        if node.level == "journey"
        else synthetic_id(parent_id, node.level, ordinal)
    )
    return replace(
        node,
        node_id=node_id,
        ordinal=ordinal,
        children=tuple(
            renumber(child, node_id, index)
            for index, child in enumerate(node.children, start=1)
        ),
    )
