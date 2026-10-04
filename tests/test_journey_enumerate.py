"""The enumeration pass, and the proof that its stand-in can fail.

Two halves, and the second is the load-bearing one.

The first checks that `enumerate_journey` reads the stand-in correctly. The
second writes the **naive** implementation of each measured hazard and asserts
it gets the wrong answer, because a fixture that cannot fail proves nothing
about the implementation that passes it (docs/09 P7-11). Each naive walker
below is code somebody would plausibly write — not a strawman built to lose.

The stand-in models markup measured on the live journey (docs/13 P11), not
markup invented here: Chakra accordions for sections and groups, `aria-label`
track rows, Emotion hashes and React `useId` values that change on every
render, a completion modal on every visit, and a track view that is not ready
when the click returns.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest
from playwright.async_api import async_playwright
from tools.golden.journey_standin import standin

from conftest import serve
from webshot.errors import (
    AuthenticationError,
    NavigationError,
    UsageError,
    certificate_hint,
)
from webshot.journey import (
    DomContract,
    EnumerationFailure,
    count_failures,
    diff_outlines,
    enumerate_journey,
)
from webshot.journey.cli import _pause_for_sign_in
from webshot.journey.walk import (
    _accordions,
    _dismiss_completion_modal,
    _progress,
    _sign_in_page,
)

T = TypeVar("T")

JOURNEY_PATH = "/journey/example-guided-onboarding"

#: What the stand-in holds, written out rather than derived, so a walk that
#: loses a module has something independent to be wrong against.
EXPECTED_MODULES = (
    "The Help Portal",
    "Navigating Your Implementation",
    "Understanding the Sandbox Environment",
    "An Overview of the Example Environments",
    # An assessment, which the walk records and never opens. They are real on
    # this journey — P12 found two in the first group it looked at.
    "Environments Quiz",
    "Overview of the Project Application",
    "Project Management Overview",
    "Building a Project Plan",
    "Task Management",
    "Projects Reporting",
)

FIRST_TRACK = "Guided Onboarding Groundwork"


async def _panel_of(page: Any, contract: DomContract, index: int) -> str:
    """The panel id of the accordion at `index`, read now.

    Never a constant. The stand-in regenerates its panel ids whenever the view
    changes, exactly as React `useId` does across a remount, so a test that
    named one would be asserting against a value the application does not
    promise — and would stop the fixture from exercising the walk's own rule.
    """
    node = next(a for a in await _accordions(page, contract) if a.index == index)
    return node.panel


@pytest.fixture(scope="module")
def journey_browser() -> Iterator[tuple[Any, Any, str]]:
    """One event loop, one browser and one stand-in for the whole module.

    Every test here needs a fresh *page*, not a fresh browser. Starting the
    Playwright driver and launching Chromium once per test was two dozen
    process start/stop cycles for no isolation this suite uses, and on a
    machine busy with other jobs a new flake is a contention signal before it
    is a bug (docs/09 P10-4).
    """
    loop = asyncio.new_event_loop()
    with serve(standin()) as origin:
        playwright = loop.run_until_complete(async_playwright().start())
        browser = loop.run_until_complete(playwright.chromium.launch(headless=True))
        try:
            yield loop, browser, f"{origin}{JOURNEY_PATH}"
        finally:
            loop.run_until_complete(browser.close())
            loop.run_until_complete(playwright.stop())
            loop.close()


def drive(
    journey_browser: tuple[Any, Any, str], work: Callable[[Any, str], Awaitable[T]]
) -> T:
    """Run `work(page, journey_url)` on a fresh page of the shared browser."""
    loop, browser, url = journey_browser

    async def run() -> T:
        page = await browser.new_page()
        try:
            return await work(page, url)
        finally:
            await page.close()

    return loop.run_until_complete(run())


async def _open_first_track(page: Any, url: str, contract: DomContract) -> None:
    """Get as far as a filled-in track view, the correct way, for a naive test."""
    await page.goto(url)
    await _dismiss_completion_modal(page, contract)
    await page.evaluate(contract.click_accordion_js, 0)
    await page.evaluate(
        contract.click_track_js, [await _panel_of(page, contract, 0), FIRST_TRACK]
    )
    for _ in range(60):
        if await _accordions(page, contract):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the stand-in never filled in its track view")


# --------------------------------------------------------------------------- #
# The walk itself
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_enumeration_reads_all_five_levels(
    journey_browser: tuple[Any, Any, str],
) -> None:
    async def work(page: Any, url: str) -> Any:
        return await enumerate_journey(page, source=url)

    outline = drive(journey_browser, work)
    assert outline.title == "Example Guided Onboarding"
    assert tuple(node.title for node in outline.modules()) == EXPECTED_MODULES
    assert {node.level for node in outline.nodes()} == {
        "journey",
        "section",
        "track",
        "group",
        "module",
    }


@pytest.mark.browser
def test_every_group_carries_the_prose_that_exists_at_no_other_level(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-1: the group's description appears nowhere else."""

    async def work(page: Any, url: str) -> Any:
        return await enumerate_journey(page, source=url)

    outline = drive(journey_browser, work)
    groups = [node for node in outline.nodes() if node.level == "group"]
    assert groups, "the stand-in has no groups, so this proves nothing"
    assert all(node.prose for node in groups)
    assert not outline.warnings


@pytest.mark.browser
def test_synthetic_ids_are_paths_and_place_every_module(
    journey_browser: tuple[Any, Any, str],
) -> None:
    async def work(page: Any, url: str) -> Any:
        return await enumerate_journey(page, source=url)

    outline = drive(journey_browser, work)
    first = next(outline.modules())
    assert first.node_id == (
        "journey/example-guided-onboarding/section-01/track-01/group-01/module-01"
    )
    ids = [node.node_id for node in outline.nodes()]
    assert len(ids) == len(set(ids)), "a synthetic id was reused"


@pytest.mark.browser
def test_counts_agree_at_every_level(journey_browser: tuple[Any, Any, str]) -> None:
    async def work(page: Any, url: str) -> Any:
        return await enumerate_journey(page, source=url)

    assert count_failures(drive(journey_browser, work).root) == []


@pytest.mark.browser
def test_two_walks_agree_which_is_the_determinism_evidence(
    journey_browser: tuple[Any, Any, str],
) -> None:
    async def work(page: Any, url: str) -> tuple[Any, Any]:
        return (
            await enumerate_journey(page, source=url),
            await enumerate_journey(page, source=url),
        )

    first, second = drive(journey_browser, work)
    assert diff_outlines(first, second) == []


@pytest.mark.browser
def test_the_walk_touches_none_of_the_three_controls_it_must_not(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """Opens no module, never presses Next in Journey, never Returns to Dashboard.

    Observed from inside the page rather than inferred from the outline: a
    walker can be wrong about its output and wrong about its blast radius in
    the same bug (docs/09 P10-1).
    """

    async def work(page: Any, url: str) -> tuple[Any, Any, Any, str]:
        await enumerate_journey(page, source=url)
        return (
            await page.evaluate("window.__opened_modules"),
            await page.evaluate("window.__next_in_journey_clicks"),
            await page.evaluate("window.__returned_to_dashboard"),
            page.url,
        )

    opened, next_clicks, dashboard, final_url = drive(journey_browser, work)
    assert opened == []
    assert next_clicks == 0
    assert dashboard == 0
    assert final_url.endswith(JOURNEY_PATH), (
        "the walk navigated; this journey has one URL"
    )


@pytest.mark.browser
def test_progress_is_read_before_and_after_and_recorded(
    journey_browser: tuple[Any, Any, str],
) -> None:
    async def work(page: Any, url: str) -> Any:
        return await enumerate_journey(page, source=url)

    outline = drive(journey_browser, work)
    assert outline.progress_before == outline.progress_after == "100%"


@pytest.mark.browser
def test_expansion_survives_the_return_to_the_journey(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """Asserted as a property of the stand-in. It discriminates nothing.

    P9-4 measured it as the one thing in the walker's favour, and the walk uses
    it to make its second look at a section cheap. No naive implementation
    fails here, so this guards the fixture, not the walker.
    """

    async def work(page: Any, url: str) -> bool:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        await page.evaluate(contract.click_named_button_js, contract.back_to_journey)
        sections = await _accordions(page, contract)
        return sections[0].expanded

    assert drive(journey_browser, work) is True


# --------------------------------------------------------------------------- #
# The stand-in can fail: one naive implementation per hazard
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_hazard_the_completion_modal_swallows_every_click(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P11-1, and the one that would have broken every real run.

    The naive walker goes straight to work. The click returns cleanly,
    `aria-expanded` never changes, and the body still has content — nothing
    looks wrong, and the walk would report a journey with no tracks in it.
    """

    async def work(page: Any, url: str) -> tuple[bool, bool, int]:
        contract = DomContract()
        await page.goto(url)
        clicked = bool(await page.evaluate(contract.click_accordion_js, 0))
        sections = await _accordions(page, contract)
        body = await page.inner_text("body")
        return clicked, sections[0].expanded, len(body)

    clicked, expanded, body_length = drive(journey_browser, work)
    assert clicked, (
        "the click did not even find the control; that is a different failure"
    )
    assert not expanded, "the modal is not intercepting, so this hazard is not modelled"
    assert body_length > 0, "the page went blank, which is not the measured symptom"


@pytest.mark.browser
def test_the_walk_dismisses_the_modal_with_the_control_that_stays_on_the_journey(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """`View Journey Details`, never `Return to Dashboard`, which navigates away."""

    async def work(page: Any, url: str) -> tuple[bool, Any, Any]:
        contract = DomContract()
        await page.goto(url)
        dismissed = await _dismiss_completion_modal(page, contract)
        return (
            dismissed,
            await page.evaluate(contract.modal_js),
            await page.evaluate("window.__returned_to_dashboard"),
        )

    dismissed, remaining, dashboard = drive(journey_browser, work)
    assert dismissed is True
    assert remaining is None
    assert dashboard == 0


@pytest.mark.browser
def test_hazard_offsetparent_reports_a_fixed_dialog_as_absent(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/09 P10-3 — the guard defeated by the property that makes it block.

    A `position: fixed` element always reports a null `offsetParent`, so the
    obvious visibility check answers "no dialog" while one is covering the page.
    This is the bug that made the first version of the modal handling do
    nothing at all.
    """

    async def work(page: Any, url: str) -> tuple[Any, Any]:
        await page.goto(url)
        naive = await page.evaluate(
            """() => {
                const dialog = document.querySelector('[role="dialog"]');
                return !dialog || dialog.offsetParent === null ? null : 'blocking';
            }"""
        )
        correct = await page.evaluate(DomContract().modal_js)
        return naive, correct

    naive, correct = drive(journey_browser, work)
    assert naive is None, "the fixed-position quirk is not reproduced"
    assert correct is not None
    assert "You Did It" in correct["text"]


@pytest.mark.browser
def test_hazard_a_track_view_is_not_ready_when_the_click_returns(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P11-2. The naive walker reads straight after the click."""

    async def work(page: Any, url: str) -> tuple[str, int, int]:
        contract = DomContract()
        await page.goto(url)
        await _dismiss_completion_modal(page, contract)
        await page.evaluate(contract.click_accordion_js, 0)
        await page.evaluate(
            contract.click_track_js, [await _panel_of(page, contract, 0), FIRST_TRACK]
        )
        immediately = await _accordions(page, contract)
        title = await page.title()
        await asyncio.sleep(0.6)
        return title, len(immediately), len(await _accordions(page, contract))

    title, immediately, later = drive(journey_browser, work)
    assert title == FIRST_TRACK, "the title had not moved, so the shell is not modelled"
    assert immediately == 0, (
        "the view was ready at once; the readiness gap is not modelled"
    )
    assert later > 0


@pytest.mark.browser
def test_a_track_that_never_fills_in_is_a_timeout_not_an_empty_track(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """CONTRIBUTING rule 5, against the failure a fixed wait produces."""

    async def work(page: Any, url: str) -> str:
        # A readiness budget shorter than the stand-in's fill, so the poll runs out.
        contract = DomContract(readiness_timeout_ms=50, poll_interval_ms=10)
        try:
            await enumerate_journey(page, source=url, contract=contract)
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "never rendered an accordion" in message
    assert "recording it as empty" in message


@pytest.mark.browser
def test_hazard_collapsed_content_stays_in_the_dom_so_presence_witnesses_nothing(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P11-4. Chakra height-collapses; it does not unmount.

    The naive witness asks whether the group's rows are present. After a
    collapse they still are, so it reports an expansion that did not happen.
    `aria-expanded` disagrees, and is right.
    """

    async def work(page: Any, url: str) -> tuple[bool, bool, bool]:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        await page.evaluate(contract.click_accordion_js, 0)  # expand
        opened = (await _accordions(page, contract))[0]
        await page.evaluate(contract.click_accordion_js, 0)  # collapse again
        collapsed = (await _accordions(page, contract))[0]
        modules = await page.evaluate(contract.panel_modules_js, opened.panel)
        return (
            collapsed.expanded,
            bool(modules),
            "The Help Portal" in (await page.inner_text("body")),
        )

    aria_expanded, rows_present, text_present = drive(journey_browser, work)
    assert aria_expanded is False, "aria-expanded did not flip back"
    assert rows_present is True, (
        "the panel unmounted, so the measured collapse is not modelled"
    )
    assert text_present is True, (
        "the text left innerText, which is not what was measured"
    )


@pytest.mark.browser
def test_hazard_emotion_hashes_and_react_ids_change_on_every_render(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P11-3: neither is stable, so neither may be keyed on."""

    async def work(page: Any, url: str) -> tuple[bool, bool]:
        contract = DomContract()
        await page.goto(url)
        await _dismiss_completion_modal(page, contract)
        read = """() => {
            const node = document.querySelector('button.chakra-accordion__button');
            return {cls: node.className, id: node.id};
        }"""
        before = await page.evaluate(read)
        await page.evaluate(contract.click_accordion_js, 1)
        after = await page.evaluate(read)
        return before["cls"] == after["cls"], before["id"] == after["id"]

    same_class, same_id = drive(journey_browser, work)
    assert not same_class, "the Emotion hash is stable here, so keying on it would pass"
    assert not same_id, (
        "the React useId value is stable here, so keying on it would pass"
    )


@pytest.mark.browser
def test_hazard_a_held_reference_expands_a_different_accordion_silently(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-4, and the reason no reference survives a click.

    Sections and groups are the same component, and the application reuses the
    row nodes, so a handle taken in the journey view is a *group* control in
    the track view. It stays attached, the click raises nothing, and a
    different accordion opens than the one the walker meant.
    """

    async def work(
        page: Any, url: str
    ) -> tuple[str, str, bool, list[tuple[int, bool]]]:
        contract = DomContract()
        await page.goto(url)
        await _dismiss_completion_modal(page, contract)
        await page.evaluate(contract.click_accordion_js, 0)
        held = await page.query_selector_all("button.chakra-accordion__button")
        # The first <p> is the title; the button's own text also carries the
        # completion word beside the tick.
        meant = (
            await held[1].evaluate("n => n.querySelector('p').textContent")
        ).strip()
        await page.evaluate(
            contract.click_track_js, [await _panel_of(page, contract, 0), FIRST_TRACK]
        )
        for _ in range(60):
            if await _accordions(page, contract):
                break
            await asyncio.sleep(0.05)
        now = (await held[1].evaluate("n => n.querySelector('p').textContent")).strip()
        attached = await held[1].evaluate("node => node.isConnected")
        await held[1].click()  # no error, and the wrong thing happens
        return (
            meant,
            now,
            attached,
            [(a.index, a.expanded) for a in await _accordions(page, contract)],
        )

    meant, now, attached, states = drive(journey_browser, work)
    assert meant == "Key Configurations"
    assert attached, (
        "the node was replaced, which makes the failure loud rather than silent"
    )
    assert now != meant, "the reference did not rebind, so this hazard is not modelled"
    assert (1, True) in states, "the wrong accordion did not open"
    assert (0, False) in states, "the group the walker meant is not still shut"


@pytest.mark.browser
def test_hazard_five_levels_a_four_level_walker_finds_no_modules(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-1. The naive walker looks for modules directly inside a track."""

    async def work(page: Any, url: str) -> int:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        rows = await page.evaluate(
            """() => document.querySelectorAll('[role="article"]').length"""
        )
        return int(rows)

    assert drive(journey_browser, work) == 0, (
        "a four-level walk found modules; the group level is not modelled"
    )


@pytest.mark.browser
def test_hazard_nothing_in_the_dom_until_opened(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-4. Reading the tree off the loaded page yields no track at all."""

    async def work(page: Any, url: str) -> tuple[str, int]:
        contract = DomContract()
        await page.goto(url)
        await _dismiss_completion_modal(page, contract)
        return await page.inner_text("body"), await page.locator("a").count()

    body, anchors = drive(journey_browser, work)
    assert FIRST_TRACK not in body
    assert "1. Introduction" not in body
    # Link-following finds only chrome, so a walker built on <a> gets nowhere.
    assert anchors == 4


@pytest.mark.browser
def test_hazard_stale_document_title_mislabels_the_journey(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-4. The naive walker takes the journey's name from document.title."""

    async def work(page: Any, url: str) -> tuple[str, str]:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        await page.evaluate(contract.click_named_button_js, contract.back_to_journey)
        return await page.title(), await page.inner_text(contract.heading)

    naive, correct = drive(journey_browser, work)
    assert naive == FIRST_TRACK, "the title is not going stale"
    assert correct == "Example Guided Onboarding"


@pytest.mark.browser
def test_the_standin_offers_no_hook_production_does_not_have(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 P9-6: no data-testid, and aria-label only where production has it."""

    async def work(page: Any, url: str) -> dict[str, int]:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        return await page.evaluate(
            """() => ({
                testids: document.querySelectorAll('[data-testid]').length,
                svgs: document.querySelectorAll('svg').length,
                named_svgs: Array.from(document.querySelectorAll('svg'))
                    .filter(s => s.getAttribute('aria-label') || s.querySelector('title')).length,
                labelled: Array.from(document.querySelectorAll('[aria-label]'))
                    .filter(n => n.tagName !== 'BUTTON').length,
            })"""
        )

    found = drive(journey_browser, work)
    assert found["testids"] == 0
    assert found["svgs"] > 0, (
        "no icons rendered, so their unlabelledness proves nothing"
    )
    assert found["named_svgs"] == 0
    assert found["labelled"] == 0, (
        "aria-label appeared somewhere production does not have it"
    )


# --------------------------------------------------------------------------- #
# The failure paths — what the walk does when it cannot be trusted
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_a_walk_that_moved_progress_is_stopped_not_footnoted(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The one failure that reaches outside the machine (docs/13, safety)."""

    async def work(page: Any, url: str) -> str:
        try:
            await enumerate_journey(
                page, source=url.replace("/journey/", "/journey-drift/")
            )
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "progress moved" in message
    assert "training record" in message


@pytest.mark.browser
def test_a_count_that_disagrees_with_the_second_look_is_a_finding(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """Per node, never in total — and the second look is taken after a delay.

    The stand-in can be asked to drop a module from the first group once every
    group in the track is open: the structure moving under the walk, which is
    what this assertion is for. Nothing about the walk's own first read can
    notice — the module was there when it looked.

    This is the assertion that would have been a tautology if `listed` were
    read one statement after `children`, which is how it was written first
    (docs/09 P10-2).
    """

    async def work(page: Any, url: str) -> tuple[list[str], int]:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-shift/")
        )
        return count_failures(outline.root), len(list(outline.modules()))

    problems, modules = drive(journey_browser, work)
    assert problems, (
        "the structure moved and no count noticed; the second look is not real"
    )
    assert any("were read" in problem for problem in problems)
    assert modules == len(EXPECTED_MODULES), (
        "the walk lost content rather than reporting drift"
    )


@pytest.mark.browser
def test_a_collapse_does_NOT_trip_the_count_and_that_is_a_stated_limit(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """What this assertion cannot see, said out loud rather than assumed away.

    P11-4: Chakra keeps a collapsed panel mounted, rows and all. So a group
    that is collapsed after being read still answers with its modules, and the
    per-node count agrees. The assertion catches the structure *moving*, not
    the structure *closing* — and `aria-expanded` is what covers the second
    case, at expansion time.

    P11-5 measured `allowMultiple`, so no real journey collapses a sibling
    anyway; this records the boundary rather than a risk.
    """

    async def work(page: Any, url: str) -> list[str]:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-solo/")
        )
        return count_failures(outline.root)

    assert drive(journey_browser, work) == []


@pytest.mark.browser
def test_a_disagreeing_count_makes_the_outline_unusable_as_a_contract() -> None:
    """The exit code is what says "do not capture against this" (spec §3, exit 5)."""
    import tempfile

    from webshot.cli import main

    with serve(standin()) as origin:
        destination = Path(tempfile.mkdtemp()) / "outline.json"
        code = main(
            [
                "journey",
                f"{origin}/journey-shift/example-guided-onboarding",
                "--dry-run",
                "-o",
                str(destination),
            ]
        )
    assert code == 5
    # Written anyway: an operator cannot act on why it is wrong without seeing it.
    assert destination.is_file()


@pytest.mark.browser
def test_two_track_rows_with_the_same_label_are_refused(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """Track rows are reached by `aria-label`, so identical siblings are unaddressable.

    Sections and groups need no such guard: `data-index` is the component's own
    ordering (P11-3), which is one of the places the measured markup removed an
    assumption instead of adding a check.
    """

    async def work(page: Any, url: str) -> str:
        base = DomContract()
        contract = DomContract(
            panel_tracks_js=base.panel_tracks_js.replace(
                ".map(node => node.getAttribute('aria-label'));",
                ".map(() => 'Overview');",
            )
        )
        try:
            await enumerate_journey(page, source=url, contract=contract)
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "more than one track" in message
    assert "recorded twice" in message


@pytest.mark.browser
def test_the_cli_writes_an_outline_that_validates_against_its_published_schema() -> (
    None
):
    """`webshot journey URL --dry-run`, end to end, through the real subcommand."""
    import json
    import tempfile

    from webshot.bundle.manifest import JourneyOutlineV1
    from webshot.cli import main

    with serve(standin()) as origin:
        destination = Path(tempfile.mkdtemp()) / "outline.json"
        code = main(
            ["journey", f"{origin}{JOURNEY_PATH}", "--dry-run", "-o", str(destination)]
        )
        assert code == 0
        payload = json.loads(destination.read_text(encoding="utf-8"))

    JourneyOutlineV1.model_validate(payload)
    assert payload["module_count"] == len(EXPECTED_MODULES)
    assert payload["determinism_findings"] == []
    assert payload["progress_before"] == payload["progress_after"]


def test_capture_is_attempted_rather_than_refused() -> None:
    """The inverse of the assertion this replaced.

    Until L1b-b, `webshot journey` without `--dry-run` exited **2**: a usage
    error saying the capture pass did not exist. It exists now, so the command
    tries to walk. Against an unreachable host that is a navigation failure —
    exit **3** — and the point of the test is that it is no longer 2, because a
    usage error there would mean the refusal had outlived what it refused.
    """
    from webshot.cli import main

    code = main(["journey", "https://example.invalid/journey/x"])
    assert code == 3, code


@pytest.mark.browser
def test_two_sections_may_share_a_track_title_without_colliding(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The ultrareview's finding, and the fixture gap that hid it.

    P11-5 keeps every section expanded, so by the time the walk reaches section
    two, section one's track rows are still in the document. A click resolved
    document-wide takes the first DOM-order match, which is section one's — and
    section two is then recorded holding section one's content, under section
    two's synthetic ids.

    Neither self-check sees it. `diff_outlines` compares two walks that are
    deterministically wrong in the same way, which is docs/09 P10-2's shape.
    `count_failures` sees `listed == len(children)` because the right *number*
    of nodes is emitted; only what is inside them is wrong. Identity is what
    moved, and nothing was looking at identity.
    """

    async def work(
        page: Any, url: str
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-dupe/")
        )
        sections = outline.root.children
        first, second = sections[0].children[0], sections[1].children[0]
        return (
            [first.title, second.title],
            [group.title for group in first.children],
            [group.title for group in second.children],
            count_failures(outline.root),
        )

    titles, first_groups, second_groups, problems = drive(journey_browser, work)

    assert titles == ["Overview", "Overview"], (
        "the stand-in did not duplicate the title"
    )
    # The load-bearing assertion: each section keeps its own content.
    assert first_groups == ["1. Introduction", "2. Environments"]
    assert second_groups == ["1. Getting Started", "2. Planning", "3. Reporting"]
    assert first_groups != second_groups
    # And the reason this needed a fixture rather than an assertion: the counts
    # agree either way, so a passing count check proves nothing here.
    assert problems == []


@pytest.mark.browser
def test_a_skipped_determinism_check_is_null_not_an_empty_list(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """`[]` is a result. A check that did not run must not publish one.

    `--no-verify-determinism` used to write `determinism_findings: []`, which
    is exactly what a *passed* check writes. The artifact then said nothing
    about the difference, while the flag's own help text says the outline is
    unusable as a capture contract.
    """
    import json
    import tempfile

    from webshot.bundle.manifest import JourneyOutlineV1
    from webshot.cli import main

    with serve(standin()) as origin:
        destination = Path(tempfile.mkdtemp()) / "outline.json"
        code = main(
            [
                "journey",
                f"{origin}{JOURNEY_PATH}",
                "--dry-run",
                "--no-verify-determinism",
                "-o",
                str(destination),
            ]
        )
        payload = json.loads(destination.read_text(encoding="utf-8"))

    assert code == 0
    JourneyOutlineV1.model_validate(payload)
    assert payload["determinism_findings"] is None, "a skipped check published a result"
    assert any("determinism was not verified" in w for w in payload["warnings"])


@pytest.mark.browser
def test_a_group_that_yields_no_module_is_reported_not_recorded_empty(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The trigger is the least-verified assumption in the walk.

    Module rows are still matched by P9-4's inferred `[role="article"]` — the
    one shape P11-3 did not measure. If that selector is wrong on the real LMS,
    every group returns nothing, every count agrees on `0 == 0`, and a journey
    of empty groups is published as a success. So an empty group stops the
    walk rather than being recorded.
    """

    async def work(page: Any, url: str) -> str:
        base = DomContract()
        # A mounted panel that yields no module: the shape a wrong selector has.
        contract = DomContract(
            # A shape that matches no row: what a wrong module selector does.
            panel_modules_js=base.panel_modules_js.replace(
                "parts.length >= 3", "parts.length >= 99"
            )
        )
        try:
            await enumerate_journey(page, source=url, contract=contract)
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "yielded no module" in message
    assert "selector does not match" in message


@pytest.mark.browser
def test_the_second_walk_is_counted_too(journey_browser: tuple[Any, Any, str]) -> None:
    """Both walks, not just the published one.

    `rows()` excludes `listed` on purpose, so a count that disagrees only on
    the second reading is invisible to `diff_outlines`. Checking one walk left
    half of a split this design states out loud. Proven by the labels: findings
    from both walks reach the artifact.
    """
    import json
    import tempfile

    from webshot.cli import main

    with serve(standin()) as origin:
        destination = Path(tempfile.mkdtemp()) / "outline.json"
        code = main(
            [
                "journey",
                f"{origin}/journey-shift/example-guided-onboarding",
                "--dry-run",
                "-o",
                str(destination),
            ]
        )
        payload = json.loads(destination.read_text(encoding="utf-8"))

    assert code == 5
    findings = payload["count_findings"]
    assert any(f.startswith("walk 1:") for f in findings)
    assert any(f.startswith("walk 2:") for f in findings), "only one walk was counted"


@pytest.mark.browser
def test_a_failing_outline_says_so_in_the_file_not_only_the_exit_code(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The exit code is not in the artifact, so the artifact has to carry it.

    A consumer handed `outline.json` could not otherwise tell an outline that
    failed its own count assertions from one that passed them.
    """
    import json
    import tempfile

    from webshot.cli import main

    with serve(standin()) as origin:
        clean = Path(tempfile.mkdtemp()) / "clean.json"
        broken = Path(tempfile.mkdtemp()) / "broken.json"
        main(["journey", f"{origin}{JOURNEY_PATH}", "--dry-run", "-o", str(clean)])
        main(
            [
                "journey",
                f"{origin}/journey-shift/example-guided-onboarding",
                "--dry-run",
                "-o",
                str(broken),
            ]
        )
        good = json.loads(clean.read_text(encoding="utf-8"))
        bad = json.loads(broken.read_text(encoding="utf-8"))

    assert good["count_findings"] == []
    assert bad["count_findings"], "the failing outline looks identical to the clean one"


@pytest.mark.browser
def test_an_assessment_is_recorded_by_type_so_capture_can_refuse_it(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """docs/13 forbids opening an assessment. An outline of titles cannot say which.

    P9 recorded that no assessment appeared in this journey and that the type
    badge said `Article` for everything. Both were wrong: assessments are
    present, and the row's third `<p>` names the type. The enumeration records
    it — never branching on it here, because stage 1 opens nothing — so the
    capture pass has something to refuse with.
    """

    async def work(page: Any, url: str) -> tuple[list[Any], list[Any]]:
        outline = await enumerate_journey(page, source=url)
        modules = list(outline.modules())
        return (
            [
                (m.title, m.module_type, m.duration)
                for m in modules
                if m.module_type == "assessment"
            ],
            [m.module_type for m in modules],
        )

    assessments, types = drive(journey_browser, work)

    assert assessments == [("Environments Quiz", "assessment", "Less than a minute")]
    assert set(types) == {"article", "assessment"}, "every module must carry a type"
    assert all(t for t in types), "a module was recorded with no type at all"


@pytest.mark.browser
def test_a_section_title_is_the_heading_not_the_heading_plus_its_status(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The accordion button's `textContent` swallows the completion word.

    Measured: the button holds two `<p>`s — "Groundwork" and "Completed" — so
    reading the whole button gives "GroundworkCompleted" and every synthetic id
    and output path inherits it.
    """

    async def work(page: Any, url: str) -> tuple[list[str], str]:
        outline = await enumerate_journey(page, source=url)
        titles = [n.title for n in outline.nodes() if n.level in {"section", "group"}]
        naive = await page.evaluate(
            "() => document.querySelector('button.chakra-accordion__button').textContent.trim()"
        )
        return titles, str(naive)

    titles, naive = drive(journey_browser, work)

    assert "Groundwork" in titles
    assert not any("Completed" in t for t in titles), (
        "a status word leaked into a title"
    )
    assert naive.endswith("Completed"), (
        "the stand-in no longer reproduces the swallowed status"
    )


@pytest.mark.browser
def test_the_modal_is_dismissed_from_inside_the_dialog_not_the_page(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The half that was fixed on reasoning and held by nothing.

    A portalled dialog leaves page chrome *earlier* in document order, so a
    document-wide resolver looking for `View Journey Details` finds the page's
    control first, clicks it, and reports success while the dialog is still up
    swallowing every subsequent click. The decoy exists so that reverting the
    scoping fails instead of passing.
    """

    async def work(page: Any, url: str) -> tuple[bool, Any, Any]:
        contract = DomContract()
        await page.goto(url)
        dismissed = await _dismiss_completion_modal(page, contract)
        return (
            dismissed,
            await page.evaluate(contract.modal_js),
            await page.evaluate("window.__decoy_clicks"),
        )

    dismissed, remaining, decoys = drive(journey_browser, work)
    assert dismissed is True
    assert remaining is None, "the dialog survived, so the click went somewhere else"
    assert decoys == [], f"the walk clicked page chrome instead of the dialog: {decoys}"


@pytest.mark.browser
def test_back_to_journey_is_matched_exactly_not_by_substring(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """`includes` takes the first button whose text merely *contains* the name.

    The stand-in carries a `Back to Journey Overview` button earlier in
    document order. A substring match picks it; normalised equality picks the
    real `← Back to Journey`, arrow and all.
    """

    async def work(page: Any, url: str) -> tuple[str, Any]:
        contract = DomContract()
        await _open_first_track(page, url, contract)
        await page.evaluate(contract.click_named_button_js, contract.back_to_journey)
        await asyncio.sleep(0.4)
        return (
            await page.inner_text(contract.heading),
            await page.evaluate("window.__decoy_clicks"),
        )

    heading, decoys = drive(journey_browser, work)
    assert heading == "Example Guided Onboarding", (
        "the walk did not return to the journey"
    )
    assert decoys == [], f"a decoy absorbed the click: {decoys}"


@pytest.mark.browser
def test_a_panel_id_does_not_survive_a_track_visit(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """`aria-controls` is safe to read, not to carry across a navigation.

    Two assertions, and the first is about the stand-in: the panel id must
    actually change across a track round trip, or the second proves nothing.
    The walk then has to read both tracks of the section — the second one is
    the one a carried id loses.
    """

    async def work(page: Any, url: str) -> tuple[str, str, list[str]]:
        contract = DomContract()
        await page.goto(url)
        await _dismiss_completion_modal(page, contract)
        await page.evaluate(contract.click_accordion_js, 0)
        before = await _panel_of(page, contract, 0)
        await page.evaluate(contract.click_track_js, [before, FIRST_TRACK])
        for _ in range(60):
            if await _accordions(page, contract):
                break
            await asyncio.sleep(0.05)
        await page.evaluate(contract.click_named_button_js, contract.back_to_journey)
        await asyncio.sleep(0.6)
        after = await _panel_of(page, contract, 0)

        outline = await enumerate_journey(page, source=url)
        section_one = outline.root.children[0]
        return before, after, [t.title for t in section_one.children]

    before, after, tracks = drive(journey_browser, work)

    assert before != after, "the stand-in kept its panel id, so this proves nothing"
    assert tracks == [FIRST_TRACK, "Understanding Your Example Environment"]


# --------------------------------------------------------------------------- #
# `--interactive-auth`, and a failure that names its own cause
# --------------------------------------------------------------------------- #


def test_interactive_auth_refuses_when_there_is_no_terminal_to_prompt_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused, never silently skipped.

    A flag that quietly does nothing when it cannot do its job is the
    absence-reads-as-success shape: the run would look like an authenticated
    walk that found no journey, which is the exact confusion this whole fix
    exists to remove.
    """
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(UsageError, match="needs a terminal"):
        asyncio.run(_pause_for_sign_in(object(), "https://example.invalid/j"))


SIGN_IN_PAGE = (
    "<!doctype html><title>Sign in</title><form><input type='password' name='p'></form>"
)
ORDINARY_PAGE = "<!doctype html><title>Not a login</title><p>Nothing to see.</p>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [(SIGN_IN_PAGE, True), (ORDINARY_PAGE, False)],
    ids=["password-field", "ordinary-page"],
)
def test_a_sign_in_page_is_recognised_and_an_ordinary_one_is_not(
    journey_browser: tuple[Any, Any, str], markup: str, expected: bool
) -> None:
    """Both directions, because a detector that always fires proves nothing.

    An unauthenticated run used to fail as "could not read the journey's
    progress" — true, and it sends the reader to the DOM contract when the
    actual problem is that they are not signed in.
    """

    async def work(page: Any, _url: str) -> str:
        await page.set_content(markup)
        return await _sign_in_page(page)

    assert bool(drive(journey_browser, work)) is expected


def test_the_progress_refusal_says_sign_in_when_that_is_what_it_found(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The message a person reads has to name the cause, not the symptom."""

    async def work(page: Any, _url: str) -> tuple[str, int]:
        await page.set_content(SIGN_IN_PAGE)
        try:
            await _progress(page, DomContract())
        except AuthenticationError as exc:
            return str(exc), exc.exit_code
        raise AssertionError("a sign-in page has no progress; this should have failed")

    message, exit_code = drive(journey_browser, work)
    assert "sign-in page" in message
    assert "--auth-profile" in message
    # The exit code, not the class name. Spec §3 reserves 4 for "authentication
    # required, or the auth material was rejected", and a caller testing
    # `$? -eq 4` is the reader this serves. Asserting the exception type instead
    # would let the same misclassification return under a different class, which
    # is how this defect arrived: the message was corrected and the number was
    # not.
    assert exit_code == 4
    # And it does not also offer the DOM-contract explanation, which would send
    # the reader to the wrong place alongside the right one.
    assert "read-only pass stayed read-only" not in message


def test_a_journey_behind_a_refused_certificate_names_the_proxy_as_a_suspect() -> None:
    """The journey opens its own page, so it is a second door for the same error.

    A stub rather than the stand-in: what is under test is the message built
    around the browser's string, and the real string is held by the capture
    path's loopback test in test_exit_codes.py.
    """

    class RefusedPage:
        async def goto(self, url: str, **_: object) -> None:
            raise RuntimeError(f"Page.goto: net::ERR_CERT_AUTHORITY_INVALID at {url}")

    source = "https://lms.example/journey/x"
    with pytest.raises(NavigationError) as raised:
        asyncio.run(enumerate_journey(RefusedPage(), source=source))
    assert str(raised.value).endswith(certificate_hint(str(raised.value)))
    assert "proxy" in str(raised.value)
    assert raised.value.exit_code == 3
