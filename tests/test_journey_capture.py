"""The capture pass, and the naive implementations that prove its checks bite.

Same method as the enumeration suite: every rule gets an implementation written
*against* it, and the test asserts that one gets the wrong answer. A check that
has never failed has proved nothing.

Two gaps this file does not close, named rather than dropped:

- **Authentication.** The stand-in needs none, so `--auth-profile` and
  `--storage-state` reach the capture path's session code unchanged and are
  exercised nowhere on this route.
- **A genuine SPA race.** The stand-in's only asynchrony is P11-2's readiness
  gap, which the walk polls for deterministically. Nothing here has ever made
  `diff_outlines` fire against a real race, which is the case it exists for.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import pytest
from playwright.async_api import async_playwright
from tools.golden.journey_standin import standin

from conftest import serve
from webshot.config import CaptureOptions
from webshot.journey import DomContract, EnumerationFailure, enumerate_journey
from webshot.journey.capture import (
    Pacer,
    capture_journey,
    outline_digest,
    slug,
)
from webshot.journey.model import JourneyNode

T = TypeVar("T")

JOURNEY_PATH = "/journey/example-guided-onboarding"

#: What the stand-in holds. Nine articles and one assessment: the assessment is
#: the module the walk must record and never open.
EXPECTED_CAPTURED = 9
THE_ASSESSMENT = "Environments Quiz"


@pytest.fixture(scope="module")
def journey_browser() -> Iterator[tuple[Any, Any, str]]:
    """One event loop, one browser and one stand-in for the whole module."""
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
    loop, browser, url = journey_browser

    async def run() -> T:
        page = await browser.new_page()
        try:
            return await work(page, url)
        finally:
            await page.close()

    return loop.run_until_complete(run())


def options_for(root: Path) -> CaptureOptions:
    """Capture options a stand-in module can be captured with.

    OCR off: nothing here is text in a raster, so recognition has nothing to
    find and its absence must not make the suite depend on Tesseract.
    """
    return CaptureOptions(
        source="stand-in",
        output=root / "unused.pdf",
        ocr=False,
        videos=False,
    )


# --------------------------------------------------------------------------- #
# The walk itself
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_the_whole_journey_captures_into_five_levels(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    async def work(page: Any, url: str) -> Any:
        outline = await enumerate_journey(page, source=url)
        return await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
        )

    result = drive(journey_browser, work)
    root = tmp_path / "corpus"

    assert len(result.captured) == EXPECTED_CAPTURED
    assert result.progress_before == result.progress_after == "100%"

    pdfs = sorted(p.relative_to(root) for p in root.rglob("capture.pdf"))
    assert len(pdfs) == EXPECTED_CAPTURED
    # Five levels below the corpus root: section / track / group / module / file.
    assert all(len(p.parts) == 5 for p in pdfs), pdfs
    assert pdfs[0] == Path(
        "01-groundwork/01-guided-onboarding-groundwork/"
        "01-1-introduction/01-the-help-portal/capture.pdf"
    )


@pytest.mark.browser
def test_the_group_prose_is_captured_at_the_level_it_belongs_to(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """docs/13 P9-1: it appears nowhere else in the journey."""

    async def work(page: Any, url: str) -> Any:
        outline = await enumerate_journey(page, source=url)
        return await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    prose_files = sorted((tmp_path / "corpus").rglob("group.md"))
    assert len(prose_files) == 6, prose_files
    text = prose_files[0].read_text(encoding="utf-8")
    assert text.startswith("# 1. Introduction")
    assert "How the guided onboarding is sequenced" in text


@pytest.mark.browser
def test_the_walk_touches_none_of_the_controls_it_must_not(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """Observed from inside the page, not inferred from the outcome list."""

    async def work(page: Any, url: str) -> tuple[Any, Any, Any, Any]:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
        )
        return (
            await page.evaluate("window.__opened_modules"),
            await page.evaluate("window.__next_in_journey_clicks"),
            await page.evaluate("window.__next_in_track_clicks"),
            await page.evaluate("window.__returned_to_dashboard"),
        )

    opened, next_journey, next_track, dashboard = drive(journey_browser, work)

    assert THE_ASSESSMENT not in opened, "the walk opened an assessment"
    assert len(opened) == EXPECTED_CAPTURED
    assert next_journey == 0
    assert next_track == 0
    assert dashboard == 0


@pytest.mark.browser
def test_the_assessment_is_recorded_as_refused_with_a_reason(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    async def work(page: Any, url: str) -> Any:
        outline = await enumerate_journey(page, source=url)
        return await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
        )

    result = drive(journey_browser, work)
    assert len(result.refused) == 1
    refused = result.refused[0]
    assert refused.title == THE_ASSESSMENT
    assert refused.module_type == "assessment"
    assert refused.output is None, "a refused module must not claim an output"
    assert "may create an attempt" in (refused.reason or "")
    # Recorded, so the corpus can say what it does not hold.
    assert refused.node_id in {o.node_id for o in result.outcomes}


# --------------------------------------------------------------------------- #
# The naive implementations
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_hazard_a_capture_that_opens_every_row_opens_the_assessment(
    journey_browser: tuple[Any, Any, str],
) -> None:
    """The refusal, demonstrated rather than asserted.

    The naive capture does what a walker written before P12-2 would: it treats
    every module row alike, because P9-2 said the type badge carried no signal.
    It opens the assessment on the first group it reaches.
    """

    async def work(page: Any, url: str) -> list[str]:
        contract = DomContract()
        outline = await enumerate_journey(page, source=url)
        section = outline.root.children[0]
        track = section.children[1]  # the track holding the assessment
        panel = await _expand(page, contract, section.ordinal - 1, "section")
        assert await page.evaluate(contract.click_track_js, [panel, track.title])
        from webshot.journey.walk import _wait_for_accordions

        await _wait_for_accordions(page, contract, "track")
        group = track.children[0]
        group_panel = await _expand(page, contract, group.ordinal - 1, "group")
        for module in group.children:
            # No branch on module_type: every row is a module, so open it.
            await page.evaluate(contract.click_module_js, [group_panel, module.title])
            await asyncio.sleep(0.35)
            assert await page.evaluate(
                contract.click_named_button_js, contract.back_to_track
            )
            await _wait_for_accordions(page, contract, "track")
            group_panel = await _expand(page, contract, group.ordinal - 1, "group")
        return list(await page.evaluate("window.__opened_modules"))

    opened = drive(journey_browser, work)
    assert THE_ASSESSMENT in opened, (
        "the naive capture did not reach the assessment; this hazard is not modelled"
    )


async def _expand(page: Any, contract: DomContract, index: int, what: str) -> str:
    from webshot.journey.walk import _ensure_expanded

    return (await _ensure_expanded(page, contract, index, what)).panel


@pytest.mark.browser
def test_hazard_a_module_the_journey_does_not_hold_is_an_error(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """The outline is the contract, and a disagreement stops the walk."""

    async def work(page: Any, url: str) -> str:
        outline = await enumerate_journey(page, source=url)
        section = outline.root.children[0]
        track = section.children[0]
        group = track.children[0]
        ghost = JourneyNode(
            node_id=f"{group.node_id}/module-99",
            level="module",
            ordinal=99,
            title="A Module That Is Not There",
            module_type="article",
        )
        tampered = replace(
            outline,
            root=_swap_group(outline.root, group.node_id, (*group.children, ghost)),
        )
        try:
            await capture_journey(
                page,
                outline=tampered,
                options=options_for(tmp_path),
                root=tmp_path / "corpus",
                minimum_interval_s=0.0,
            )
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "A Module That Is Not There" in message
    assert "the journey does not" in message


def _swap_group(
    node: JourneyNode, node_id: str, children: tuple[JourneyNode, ...]
) -> JourneyNode:
    if node.node_id == node_id:
        return replace(node, children=children)
    return replace(
        node, children=tuple(_swap_group(c, node_id, children) for c in node.children)
    )


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_a_resumed_run_captures_only_what_is_left_and_the_union_is_complete(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """P9-5 puts this journey in the low hundreds; a failure at ninety must not
    cost the eighty-nine before it. The union is what proves coverage."""
    root = tmp_path / "corpus"

    async def first_half(page: Any, url: str) -> Any:
        outline = await enumerate_journey(page, source=url)
        # Stand in for an interrupted run: three modules already done.
        done = [m.node_id for m in outline.modules() if m.module_type != "assessment"][
            :3
        ]
        root.mkdir(parents=True, exist_ok=True)
        (root / "resume.json").write_text(
            json.dumps({"outline_digest": outline_digest(outline), "completed": done}),
            encoding="utf-8",
        )
        return await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    result = drive(journey_browser, first_half)

    skipped = [o for o in result.outcomes if o.status == "skipped-resumed"]
    assert len(skipped) == 3
    assert len(result.captured) == EXPECTED_CAPTURED - 3
    # The union, which is the point: every module is accounted for exactly once.
    assert len(result.outcomes) == EXPECTED_CAPTURED + 1
    assert len({o.node_id for o in result.outcomes}) == len(result.outcomes)


@pytest.mark.browser
def test_a_resume_built_against_a_different_tree_is_refused(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """The gap the three decisions made together.

    A synthetic path resolves against *any* tree of the same shape, so resume
    state from a journey that has since changed maps silently onto different
    modules and every count assertion still passes. The digest is what makes
    that loud, at resume time rather than halfway through.
    """
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> str:
        outline = await enumerate_journey(page, source=url)
        root.mkdir(parents=True, exist_ok=True)
        (root / "resume.json").write_text(
            json.dumps(
                {
                    "outline_digest": "0" * 64,  # a different journey's shape
                    "completed": [next(outline.modules()).node_id],
                }
            ),
            encoding="utf-8",
        )
        try:
            await capture_journey(
                page,
                outline=outline,
                options=options_for(tmp_path),
                root=root,
                minimum_interval_s=0.0,
            )
        except EnumerationFailure as exc:
            return str(exc)
        return ""

    message = drive(journey_browser, work)
    assert "structure changed" in message
    assert "resolve against any" in message
    assert outline_digest_prefix_in(message)


def outline_digest_prefix_in(message: str) -> bool:
    return "000000000000" in message


# --------------------------------------------------------------------------- #
# Pacing
# --------------------------------------------------------------------------- #


def test_the_pacing_floor_delays_when_it_binds() -> None:
    """docs/11 §2's commitment, proven to be able to fire.

    Deliberately not asserted through a real capture: a capture is render + OCR
    + bundle + PDF and already costs seconds, so a floor beneath it never
    delays anything. A control that cannot fire is P7-9's shape, and the only
    way to know this one can is to drive it where nothing else sets the pace.
    """
    pacer = Pacer(minimum_s=0.3)

    async def three_rapid_opens() -> None:
        for _ in range(3):
            await pacer.wait()

    asyncio.run(three_rapid_opens())

    assert pacer.bound, "the floor never delayed anything"
    assert len(pacer.intervals) == 2
    assert all(interval >= 0.29 for interval in pacer.intervals), pacer.intervals


def test_a_floor_beneath_the_work_never_binds_and_says_so() -> None:
    """The honest other half: a zero floor delays nothing and reports it."""
    pacer = Pacer(minimum_s=0.0)

    async def two_opens() -> None:
        await pacer.wait()
        await pacer.wait()

    asyncio.run(two_opens())
    assert not pacer.bound
    assert pacer.delays == [0.0]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_slugs_keep_teaching_order_that_prose_loses() -> None:
    assert slug("1. Introduction", 1) == "01-1-introduction"
    assert slug("Dashboards & Reporting", 3) == "03-dashboards-reporting"
    assert slug("", 7) == "07"
    assert sorted([slug("b", 10), slug("a", 2)]) == ["02-a", "10-b"]


@pytest.mark.browser
def test_a_type_that_is_not_measured_safe_is_refused_not_opened(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """The rule that outranks the others must not fail open.

    A denylist of one string captures everything that is not exactly that
    string, and the type is read positionally from a row with no stable handle
    (P12-1). Three routes, none exotic on this application, and the stand-in
    carries all three:

    - the type cell renders **empty**
    - the LMS names the type something this build does not know (`quiz`)
    - a **fourth** `<p>` is appended, so the positional read lands on the badge
      and the real type becomes invisible

    Neither existing check can see any of them. `diff_outlines` cannot by
    construction — a structural DOM change is identical on both passes — and
    the `parts.length >= 3` filter only catches rows with too *few* paragraphs.
    """

    async def work(page: Any, url: str) -> tuple[Any, list[Any]]:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-types/")
        )
        result = await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
        )
        return result, list(await page.evaluate("window.__opened_modules"))

    result, opened = drive(journey_browser, work)

    unrecognised = {
        "The Help Portal",  # empty cell -> None
        "Navigating Your Implementation",  # 'quiz'
        "Understanding the Sandbox Environment",  # 'new', from an appended <p>
    }
    # The load-bearing assertion, read from inside the page: none of them opened.
    assert not (unrecognised & set(opened)), sorted(unrecognised & set(opened))

    refused = {outcome.title for outcome in result.refused}
    assert unrecognised <= refused, sorted(unrecognised - refused)
    # And the two refusals stay distinguishable: "this is an assessment" and
    # "I do not recognise this type" are different facts for an operator.
    reasons = {o.title: (o.reason or "") for o in result.refused}
    assert "may create an attempt" in reasons[THE_ASSESSMENT]
    assert "not recognised" in reasons["Navigating Your Implementation"]
    assert "'quiz'" in reasons["Navigating Your Implementation"], reasons
    assert "absent" in reasons["The Help Portal"], reasons


@pytest.mark.browser
def test_an_operator_can_permit_a_type_after_looking_at_it(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """Refusing the unknown must be recoverable without weakening the default."""

    async def work(page: Any, url: str) -> Any:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-types/")
        )
        return await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=tmp_path / "corpus",
            minimum_interval_s=0.0,
            also_open={"quiz"},
        )

    result = drive(journey_browser, work)
    captured = {outcome.title for outcome in result.captured}
    assert "Navigating Your Implementation" in captured
    # Permitting one type permits exactly that one.
    refused = {outcome.title for outcome in result.refused}
    assert "The Help Portal" in refused
    assert THE_ASSESSMENT in refused


def test_an_assessment_can_never_be_permitted() -> None:
    """The one refusal no flag may lift."""
    from webshot.journey.capture import resolve_openable

    with pytest.raises(EnumerationFailure, match="assessment"):
        resolve_openable({"assessment"})
    assert resolve_openable({"quiz"}) == {"article", "quiz"}
    assert resolve_openable(None) == {"article"}


# --------------------------------------------------------------------------- #
# The corpus index, and the chunk paths
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_the_index_records_the_refusal_and_a_naive_one_does_not(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """docs/13: absence is stated, never implied.

    The naive index is built the obvious way — from what was captured. It is
    complete, consistent, and describes a journey with **no assessment in it**,
    which is not the journey. A reader could not tell that from the file.
    """
    from webshot.journey.index import build_index

    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> Any:
        outline = await enumerate_journey(page, source=url)
        result = await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )
        return outline, result

    outline, result = drive(journey_browser, work)
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))

    # The naive index: only the modules that produced a directory.
    naive = build_index(
        outline,
        {o.node_id: o for o in result.captured},
        root,
        progress_before=result.progress_before,
        progress_after=result.progress_after,
    )

    def module_titles(entry: dict[str, Any], want: str) -> set[str]:
        if entry["level"] == "module":
            return {entry["title"]} if entry.get("status") == want else set()
        found: set[str] = set()
        for child in entry.get("children", []):
            found |= module_titles(child, want)
        return found

    assert module_titles(naive["root"], "refused") == set(), (
        "the naive index already records refusals; this hazard is not modelled"
    )
    assert module_titles(index["root"], "refused") == {THE_ASSESSMENT}

    # And it is legible without parsing: the absence has its own section.
    markdown = (root / "index.md").read_text(encoding="utf-8")
    section = markdown[markdown.index("## What this corpus does not hold") :]
    assert THE_ASSESSMENT in section
    assert "may create an attempt" in section
    assert index["counts"] == {"modules": 10, "captured": 9, "refused": 1}


@pytest.mark.browser
def test_the_index_is_useful_read_alone(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """The requirement docs/13 states sharply: a file listing is not an index."""
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))

    def modules(entry: dict[str, Any]) -> list[dict[str, Any]]:
        if entry["level"] == "module":
            return [entry]
        return [m for child in entry.get("children", []) for m in modules(child)]

    every = modules(index["root"])
    assert len(every) == 10
    for module in every:
        # Everything docs/13 asks the index to carry, on every module.
        assert module["node_id"] and module["title"]
        assert "module_type" in module
        assert "duration_authored" in module
        assert "duration_measured_seconds" in module
        assert "status" in module
        assert "pdf" in module and "videos" in module
    captured = [m for m in every if m["status"] == "captured"]
    assert all(m["pdf"] and m["pdf"].endswith("capture.pdf") for m in captured)
    # A refused module claims no file, and says so rather than omitting it.
    refused = [m for m in every if m["status"] == "refused"]
    assert all(
        m["pdf"] is None and m["duration_measured_seconds"] is None for m in refused
    )

    # The group prose the index points at is the prose that exists nowhere else.
    def groups(entry: dict[str, Any]) -> list[dict[str, Any]]:
        if entry["level"] == "group":
            return [entry]
        return [g for child in entry.get("children", []) for g in groups(child)]

    assert all(g["prose"] and g["prose_file"] for g in groups(index["root"]))


@pytest.mark.browser
def test_two_modules_with_the_same_text_are_told_apart_only_by_the_chunk_path(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """docs/13: a retrieved fragment must know where it sits.

    The stand-in can put the same title and the same body under two different
    groups — legitimate, because groups are index-addressed. Strip the journey
    path back off and the two modules' chunk records are **byte-identical**:
    same heading, same text, same per-bundle id. That is the naive
    implementation, and it is what a chunk without its path is.
    """
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-twins/")
        )
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)

    # Matched on the module directory exactly. Three other modules have
    # "Overview" in their titles, and a substring filter picked them up.
    twins = sorted(
        p for p in root.rglob("chunks.jsonl") if p.parent.parent.name == "01-overview"
    )
    assert len(twins) == 2, [str(p) for p in twins]

    def records(path: Path) -> list[dict[str, Any]]:
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]

    first, second = records(twins[0]), records(twins[1])
    stripped_first = [
        {**r, "meta": {k: v for k, v in r["meta"].items() if k != "journey"}}
        for r in first
    ]
    stripped_second = [
        {**r, "meta": {k: v for k, v in r["meta"].items() if k != "journey"}}
        for r in second
    ]

    assert stripped_first == stripped_second, (
        "the two modules differ even without the path; this hazard is not modelled"
    )
    assert first != second, "the journey path did not tell them apart"

    paths = {r["meta"]["journey"]["node_id"] for r in first + second}
    assert len(paths) == 2
    for record in first + second:
        stamp = record["meta"]["journey"]
        assert stamp["journey"] and stamp["section"] and stamp["track"]
        assert stamp["group"] and stamp["module"]


@pytest.mark.browser
def test_stamping_a_chunk_path_leaves_its_digest_correct(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """`meta.sha256` digests the chunk's TEXT, so adding to `meta` is free.

    Asserted rather than assumed, because folding the path into the text
    instead would be defensible for retrieval and would silently invalidate
    every digest — the harness would catch it, and so should this.
    """
    import hashlib

    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    checked = 0
    for path in root.rglob("chunks.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            digest = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
            assert record["meta"]["sha256"] == digest, record["id"]
            assert record["meta"]["journey"]["node_id"]
            checked += 1
    assert checked, "no chunks were checked, so this proves nothing"


def test_a_measured_duration_describes_the_module_or_is_absent() -> None:
    """The index's number must not be one video wearing the module's name.

    The case is this repository's own `lms-module-videos` golden: two videos,
    one reporting 81.4 s and one reporting nothing. Taking the first published
    81.4 as the module's measured duration, beside the authored label it exists
    to be compared against.
    """
    from webshot.journey.index import _measured_total

    assert _measured_total([]) is None
    assert _measured_total(
        [{"duration_seconds": 81.4}, {"duration_seconds": 18.6}]
    ) == pytest.approx(100.0)
    # The golden's shape. A partial sum here would be a number named for the
    # module that describes one of its parts.
    assert (
        _measured_total([{"duration_seconds": 81.4}, {"duration_seconds": None}])
        is None
    )
    assert _measured_total([{"duration_seconds": None}]) is None
    # The single-video case P9-8 measured still answers.
    assert _measured_total([{"duration_seconds": 251.6}]) == pytest.approx(251.6)


def test_the_transcript_tally_cannot_stand_in_for_a_missing_duration() -> None:
    """Why the shortfall is not left to `video_tally.untranscribed_media`.

    That counts videos with no *transcript*. P9-8's own measurement is an
    untranscribed Continu-hosted video reporting 251.6 s — untranscribed and
    fully measured — so the two sets are not the same, and a sibling that
    happens to correlate on one sample is not a statement of incompleteness.
    """
    from webshot.journey.index import _measured_total

    untranscribed_but_measured = [
        {"duration_seconds": 251.6, "transcribed": False},
        {"duration_seconds": 81.4, "transcribed": True},
    ]
    # Complete despite one being untranscribed: the tally would have implied a
    # shortfall that is not there.
    assert _measured_total(untranscribed_but_measured) == pytest.approx(333.0)

    transcribed_but_unmeasured = [
        {"duration_seconds": None, "transcribed": True},
        {"duration_seconds": 81.4, "transcribed": True},
    ]
    # Incomplete while the tally would have reported nothing untranscribed.
    assert _measured_total(transcribed_but_unmeasured) is None


# --------------------------------------------------------------------------- #
# The corpus media store, and cross-references
# --------------------------------------------------------------------------- #


def _media(root: Path) -> dict[str, Any]:
    return json.loads((root / "media" / "index.json").read_text(encoding="utf-8"))


def _index(root: Path) -> dict[str, Any]:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def _modules(entry: dict[str, Any]) -> list[dict[str, Any]]:
    if entry["level"] == "module":
        return [entry]
    return [m for child in entry.get("children", []) for m in _modules(child)]


@pytest.mark.browser
def test_one_copy_many_references_and_every_referrer_recorded(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """docs/13: the back-reference is half the requirement.

    The naive dedupe keeps one copy and drops the referrers. It looks correct —
    one file per distinct image, no duplication — and the corpus can no longer
    answer "which modules used this", which is the question a deduplicated
    store exists to make answerable.
    """
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    media = _media(root)

    assert media["distinct"] == 2, media
    assert media["shared"] == 1, "no asset recurs, so dedupe proves nothing here"
    # One stored copy per distinct hash, and the file is really there.
    for asset in media["assets"]:
        assert asset["file"], asset
        assert (root / asset["file"]).is_file()
        assert asset["references"], "an asset with no referrer is unattributable"
    # The naive store: one copy each, referrers dropped.
    naive = [{k: v for k, v in a.items() if k != "references"} for a in media["assets"]]
    assert all("references" not in a for a in naive)
    by_hash = {a["sha256"]: a for a in media["assets"]}
    shared = max(
        by_hash.values(), key=lambda a: len({r["node_id"] for r in a["references"]})
    )
    assert len({r["node_id"] for r in shared["references"]}) == 9
    # Every referrer names the module and the file inside that module's bundle.
    for reference in shared["references"]:
        assert reference["node_id"].startswith("journey/")
        assert reference["bundle_file"].startswith("assets/")


@pytest.mark.browser
def test_an_asset_used_by_one_module_survives_the_dedupe(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """The common case an optimisation is most likely to eat."""
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    media = _media(root)
    singles = [
        asset
        for asset in media["assets"]
        if len({r["node_id"] for r in asset["references"]}) == 1
    ]
    assert len(singles) == 1, media["assets"]
    assert (root / singles[0]["file"]).is_file()
    assert singles[0]["references"][0]["node_id"]


@pytest.mark.browser
def test_a_dedupe_keyed_on_filename_would_collapse_two_different_images(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """`asset-001.png` exists in every module and means something different.

    The stand-in puts the single-module image first where it appears, so that
    module's `asset-001` is a different picture from everyone else's. Keyed on
    filename the store holds one entry for two images; keyed on content it
    holds two.
    """
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    media = _media(root)

    by_name: dict[str, set[str]] = {}
    for asset in media["assets"]:
        for reference in asset["references"]:
            by_name.setdefault(reference["bundle_file"], set()).add(asset["sha256"])

    collisions = {name: hashes for name, hashes in by_name.items() if len(hashes) > 1}
    assert collisions, (
        "no filename holds two different images, so keying on the name would "
        "have been indistinguishable from keying on content"
    )
    # Content keying keeps them apart; name keying would not.
    assert media["distinct"] == 2
    assert len(by_name) < media["distinct"] + len(collisions) + 1


@pytest.mark.browser
def test_an_ambiguous_cross_reference_is_recorded_not_guessed(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """Titles are ambiguous by construction, so resolution must be able to say so.

    The naive resolver matches by title and takes the first hit. Against the
    twinned fixture — two modules in different groups sharing a title — it
    picks one of two, and nothing downstream can tell it guessed.
    """
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(
            page, source=url.replace("/journey/", "/journey-twins/")
        )
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    index = _index(root)

    every = [
        r for module in _modules(index["root"]) for r in module.get("references") or []
    ]
    ambiguous = [r for r in every if r["ambiguous"]]
    assert ambiguous, "nothing was ambiguous, so this fixture proves nothing"
    for reference in ambiguous:
        # The rule: no id is claimed, and every candidate is kept.
        assert reference["node_id"] is None
        assert len(reference["candidates"]) >= 1
    # The naive resolver would have produced an id for each of these.
    naive = [r["candidates"][0] for r in ambiguous]
    assert all(isinstance(node_id, str) for node_id in naive)
    # And the index says so out loud rather than leaving it in the data.
    assert any("ambiguous" in warning for warning in index["warnings"])


@pytest.mark.browser
def test_an_unambiguous_cross_reference_carries_the_synthetic_id(
    journey_browser: tuple[Any, Any, str], tmp_path: Path
) -> None:
    """docs/13: a module referencing another by title carries that module's id."""
    root = tmp_path / "corpus"

    async def work(page: Any, url: str) -> None:
        outline = await enumerate_journey(page, source=url)
        await capture_journey(
            page,
            outline=outline,
            options=options_for(tmp_path),
            root=root,
            minimum_interval_s=0.0,
        )

    drive(journey_browser, work)
    index = _index(root)
    every = [
        r for module in _modules(index["root"]) for r in module.get("references") or []
    ]
    resolved = [r for r in every if not r["ambiguous"]]
    assert resolved, "no reference resolved, so this proves nothing"
    for reference in resolved:
        assert reference["node_id"] and reference["node_id"].startswith("journey/")
        assert reference["candidates"] == [reference["node_id"]]
    assert index["warnings"] == []
