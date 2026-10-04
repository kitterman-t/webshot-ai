"""The enumeration's rules, without a browser.

The walk needs Chromium; what a well-formed enumeration *is* does not, so the
assertions that decide whether an outline may be used as a capture contract are
tested here and run in the default pytest pass.
"""

from __future__ import annotations

from dataclasses import replace

from webshot.journey.model import (
    LEVELS,
    JourneyNode,
    JourneyOutline,
    child_level,
    count_failures,
    diff_outlines,
    renumber,
    synthetic_id,
)


def module(title: str) -> JourneyNode:
    return JourneyNode(node_id="", level="module", ordinal=0, title=title)


def tree(*, listed: int | None = None, modules: int = 2) -> JourneyNode:
    group = JourneyNode(
        node_id="",
        level="group",
        ordinal=0,
        title="1. Introduction",
        prose="Why this group exists.",
        listed=modules if listed is None else listed,
        children=tuple(module(f"Module {index}") for index in range(1, modules + 1)),
    )
    track = JourneyNode(
        node_id="",
        level="track",
        ordinal=0,
        title="A Track",
        listed=1,
        children=(group,),
    )
    section = JourneyNode(
        node_id="",
        level="section",
        ordinal=0,
        title="A Section",
        listed=1,
        children=(track,),
    )
    root = JourneyNode(
        node_id="",
        level="journey",
        ordinal=1,
        title="A Journey",
        listed=1,
        children=(section,),
    )
    return renumber(replace(root, node_id="journey/j"), "journey/j", 1)


def outline(root: JourneyNode) -> JourneyOutline:
    return JourneyOutline(
        journey_id="j",
        source="https://example.invalid/journey/j",
        title="A Journey",
        progress_before="Your Progress 100%",
        progress_after="Your Progress 100%",
        root=root,
    )


def test_the_five_levels_are_the_measured_ones() -> None:
    assert LEVELS == ("journey", "section", "track", "group", "module")
    assert child_level("track") == "group"
    assert child_level("module") is None


def test_ids_are_paths_and_sort_into_teaching_order() -> None:
    parent = "journey/j/section-01"
    assert synthetic_id(parent, "track", 2) == "journey/j/section-01/track-02"
    ordered = [synthetic_id(parent, "track", n) for n in (10, 2, 1)]
    assert sorted(ordered) == [
        "journey/j/section-01/track-01",
        "journey/j/section-01/track-02",
        "journey/j/section-01/track-10",
    ]


def test_an_ordinal_below_one_is_refused_rather_than_formatted() -> None:
    import pytest

    with pytest.raises(ValueError, match="1-based"):
        synthetic_id("journey/j", "section", 0)


def test_counts_that_agree_produce_no_findings() -> None:
    assert count_failures(tree()) == []


def test_a_group_that_lost_a_module_is_named_not_summed() -> None:
    """The failure a journey-wide total hides."""
    problems = count_failures(tree(listed=0))
    assert len(problems) == 1
    assert "group-01" in problems[0]
    assert "'1. Introduction'" in problems[0]
    assert "listed 0" in problems[0]
    assert "2 were read" in problems[0]


def test_two_identical_walks_diff_to_nothing() -> None:
    assert diff_outlines(outline(tree()), outline(tree())) == []


def test_a_module_that_appeared_on_only_one_walk_is_reported_both_ways() -> None:
    first, second = outline(tree(modules=2)), outline(tree(modules=3))
    forward = diff_outlines(first, second)
    assert any("appeared only on the second walk" in line for line in forward)
    backward = diff_outlines(second, first)
    assert any("vanished on the second walk" in line for line in backward)


def test_a_renamed_node_at_the_same_position_is_a_difference() -> None:
    moved = tree()
    section = moved.children[0]
    renamed = replace(section, title="A Different Section")
    changed = replace(moved, children=(renamed,))
    findings = diff_outlines(outline(moved), outline(changed))
    assert any("section-01" in line for line in findings)


def test_the_diff_ignores_counts_so_a_counting_bug_is_not_read_as_a_race() -> None:
    """`listed` is an assertion input, not part of the shape two walks must share."""
    assert diff_outlines(outline(tree()), outline(tree(listed=0))) == []
    assert count_failures(tree(listed=0)) != []
