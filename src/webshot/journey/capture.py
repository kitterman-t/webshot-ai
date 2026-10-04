"""Stage 2 of the journey walker: capture exactly what the enumeration listed.

The enumeration is the contract. This module consumes a `JourneyOutline` and
does not re-derive the tree — a capture that discovered its own work would have
nothing to be checked against, which is the whole reason docs/13 makes the walk
two passes.

Four things it owes, and each of them is a rule rather than a preference:

- **A module in the outline and not captured is an error**, named. So is one
  encountered and not in the outline: the structure moved under us.
- **Counts are asserted at every level**, never only in total. A journey-wide
  total is satisfied by a group that came back silently empty, which is exactly
  what P9-4's stale-reference hazard produces.
- **Every `assessment` is refused**, recorded with the reason and never opened.
  P12-2 found two in the first group of the real journey, so this is live on
  every run rather than a precaution.
- **Progress is read before and after and compared.** It feeds a real training
  record; a walk that moved it stops.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from ..config import CaptureOptions
from ..pipeline import RequestFailures, _StageClock, capture_open_page
from ..version import VERSION
from .index import write_index
from .model import JourneyNode, JourneyOutline, count_failures
from .walk import (
    DomContract,
    EnumerationFailure,
    _accordions,
    _ensure_expanded,
    _progress,
    _wait_for_accordions,
    open_module,
    return_to_track,
)

LOGGER = logging.getLogger("webshot.journey")

#: The one module type the walker refuses to open (docs/13), and the only one
#: no flag may ever permit.
ASSESSMENT = "assessment"

#: The only type measured safe to open on this LMS. An **allowlist**, and the
#: distinction is the whole point: the first version of this check was
#: `type == "assessment" -> refuse`, which captured every value that was not
#: exactly that string. The type is read positionally from a row with no stable
#: handle (P12-1), so an empty cell, a rename to `quiz`, or a fourth `<p>`
#: appended after the type all read as safe-to-open under a denylist — and
#: neither self-check can see any of them, because a structural DOM change is
#: identical on both enumeration passes.
#:
#: `article` covers video modules too (P9-8). Refusing the unknown is
#: recoverable and loud; opening it is neither.
OPENABLE = frozenset({"article"})

RESUME_FILE = "resume.json"


def resolve_openable(also_open: Iterable[str] | None) -> set[str]:
    """The set of types this walk will open, refusing to add `assessment`.

    An operator who has looked at a journey holding a legitimately new type can
    name it, so refusing the unknown stays recoverable without weakening the
    default. `assessment` is not among what can be named: it is the one refusal
    docs/13 puts above every other requirement, and a flag that could lift it
    would make the safety rule a preference.
    """
    extra = {value.strip().lower() for value in (also_open or ()) if value.strip()}
    if ASSESSMENT in extra:
        raise EnumerationFailure(
            f"{ASSESSMENT!r} cannot be permitted. Opening one may create an attempt "
            "against the user's training record, which docs/13 places above every "
            "other requirement here — it is not a default to be overridden."
        )
    return set(OPENABLE) | extra


def slug(text: str, ordinal: int) -> str:
    """`03-building-a-project-plan` — teaching order first, then the title.

    The numeric prefix is the point: prose loses the order a journey teaches in,
    and a lexical sort of these directories restores it (docs/13).
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{ordinal:02d}-{cleaned}" if cleaned else f"{ordinal:02d}"


def outline_digest(outline: JourneyOutline) -> str:
    """A digest of the shape a resume was built against.

    Resume state keys on the synthetic path, and a path resolves against *any*
    tree of the same shape: `section-01/track-02/group-03/module-01` exists in
    the journey as it was and in the journey as it now is, and nothing notices
    they are different modules. A resumed run that skipped work on that basis
    would publish a corpus half from one structure and half from another, with
    every count assertion passing.
    """
    payload = json.dumps(outline.rows(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ModuleOutcome:
    """What happened to one module, and why."""

    node_id: str
    title: str
    module_type: str | None
    status: Literal["captured", "refused", "skipped-resumed"]
    output: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class Pacer:
    """A floor on how often a host is asked for the next module.

    docs/11 §2 commits to per-host pacing before batch mode ships, and this is
    batch mode. The walk touches one host, so per-host and global coincide and
    the meaningful control is the gap between module opens.

    It records what it actually achieved rather than what it was configured for.
    A floor beneath a cost that already exceeds it never fires — P7-9's shape,
    a limit applied where it cannot bind — and the only way to know which case
    a run was in is to measure the interval instead of assuming the setting
    describes it.
    """

    minimum_s: float
    _last: float | None = None
    intervals: list[float] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)

    async def wait(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            elapsed = now - self._last
            remaining = self.minimum_s - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
                self.delays.append(round(remaining, 3))
                now = time.monotonic()
            else:
                self.delays.append(0.0)
            self.intervals.append(round(now - self._last, 3))
        self._last = now

    @property
    def bound(self) -> bool:
        """Whether the floor ever actually delayed anything."""
        return any(delay > 0 for delay in self.delays)


@dataclass(frozen=True, slots=True)
class JourneyCaptureResult:
    journey_id: str
    root: Path
    outcomes: tuple[ModuleOutcome, ...]
    progress_before: str
    progress_after: str
    pacing: dict[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def captured(self) -> tuple[ModuleOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "captured")

    @property
    def refused(self) -> tuple[ModuleOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "refused")


def module_directory(root: Path, outline: JourneyOutline, module: JourneyNode) -> Path:
    """Where one module's PDF and bundle land — five levels, group included."""
    trail = _trail(outline.root, module.node_id)
    if trail is None:  # pragma: no cover - the caller walks the same tree
        raise EnumerationFailure(f"{module.node_id} is not in this outline")
    directory = root
    for node in trail[1:]:
        directory /= slug(node.title, node.ordinal)
    return directory


def _trail(node: JourneyNode, node_id: str) -> list[JourneyNode] | None:
    if node.node_id == node_id:
        return [node]
    for child in node.children:
        found = _trail(child, node_id)
        if found is not None:
            return [node, *found]
    return None


def _read_resume(path: Path, digest: str) -> set[str]:
    """Load what a previous run finished, refusing a mismatched tree loudly."""
    if not path.is_file():
        return set()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnumerationFailure(
            f"the resume file at {path} could not be read: {exc}. Delete it to start "
            "the journey again rather than resuming onto an unknown state."
        ) from exc
    recorded = str(state.get("outline_digest", ""))
    if recorded != digest:
        raise EnumerationFailure(
            "the journey's structure changed since this run was interrupted: the "
            f"resume file was built against outline {recorded[:12] or '(absent)'} and "
            f"this walk enumerated {digest[:12]}. Synthetic paths resolve against any "
            "tree of the same shape, so resuming would fill one corpus from two "
            "different structures with every count still agreeing. Delete "
            f"{path.name} to capture the journey afresh."
        )
    return {str(node_id) for node_id in state.get("completed", [])}


def _write_resume(path: Path, digest: str, completed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generator": "webshot",
                "generator_version": VERSION,
                "outline_digest": digest,
                "completed": sorted(completed),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _listed(outline_children: tuple[JourneyNode, ...]) -> list[str]:
    return [child.title for child in outline_children]


def _reconcile(level: str, where: str, expected: list[str], seen: list[str]) -> None:
    """Both halves of the contract, at one node, in one place.

    Missing is "the outline said this is here and it is not"; extra is "the
    structure moved under us". Neither is survivable, because everything the
    capture publishes is addressed by a path derived from the outline.
    """
    missing = [title for title in expected if title not in seen]
    extra = [title for title in seen if title not in expected]
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"the outline lists {missing!r} and the journey does not")
        if extra:
            parts.append(f"the journey lists {extra!r} and the outline does not")
        raise EnumerationFailure(
            f"{where}: {'; '.join(parts)}. The enumeration is the contract this "
            "capture is checked against, so a disagreement at the "
            f"{level} level stops the walk rather than being reconciled silently."
        )


async def _capture_one_module(
    page: Any,
    module: JourneyNode,
    *,
    destination: Path,
    options: CaptureOptions,
    source: str,
    failed_requests: RequestFailures,
) -> None:
    """Hand the open module page to the capture seam, and nothing more.

    Deliberately thin. The videos on this page are `enrich_with_videos`'s job
    inside that seam, and a branch here that looked at one would mean the seam
    was crossed (docs/13). The clamped text is `release_clamped_text`'s job in
    the same seam, and the walker owes the page nothing for it.
    """
    from ..ocr.engine import resolve_engine
    from ..outputlock import OutputLock

    bundle_directory = destination / "bundle"
    module_options = replace(
        options,
        source=source,
        output=destination / "capture.pdf",
        ai_bundle_directory=bundle_directory,
    )
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    clock = _StageClock(started)
    # Per module as well as per journey: the corpus lock excludes a second
    # walker, and this one still matters because another process can target a
    # single module's output path directly.
    with OutputLock(module_options.output, bundle_directory):
        await capture_open_page(
            page,
            module_options,
            clock=clock,
            started=started,
            ocr_engine=resolve_engine(module_options.ocr_engine),
            ai_final_directory=bundle_directory,
            # A clicked module has no materialized source and no navigation
            # response; these are the four things the seam asks the caller for
            # precisely because the page cannot answer them.
            source_kind="web",
            original_local_path=None,
            http_status=None,
            content_type="",
            failed_requests=failed_requests,
            warnings=[],
        )


def stamp_chunk_paths(
    bundle: Path, outline: JourneyOutline, module: JourneyNode
) -> int:
    """Write this module's place in the journey onto every chunk it produced.

    A post-pass over the bundle rather than a change to the shared chunk writer,
    and deliberately: the writer serves every capture, and adding a field there
    would move `chunks.jsonl` on all 25 golden cases for a fact none of them
    has. A page captured on its own belongs to no journey.

    `meta.sha256` is the digest of the chunk's **text**, not of the record
    (`chunk_digest_failures`, tools/golden/harness.py), so adding to `meta`
    leaves it correct. Folding the path into the text instead would be
    defensible for retrieval and would require recomputing it — the harness
    catches that on any machine, which is why this note is here rather than a
    surprise later.
    """
    path = bundle / "chunks.jsonl"
    if not path.is_file():
        return 0
    trail = _trail(outline.root, module.node_id) or []
    names = {node.level: node.title for node in trail}
    stamp = {
        "journey": outline.title,
        "journey_id": outline.journey_id,
        "section": names.get("section", ""),
        "track": names.get("track", ""),
        "group": names.get("group", ""),
        "module": module.title,
        "node_id": module.node_id,
    }
    # At "\n" only. `json.dumps(ensure_ascii=False)` writes U+0085, U+2028 and
    # U+2029 raw inside a record, and `splitlines()` breaks at all three
    # (docs/09 P10-28).
    lines = path.read_text(encoding="utf-8").split("\n")
    stamped: list[str] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        meta = record.setdefault("meta", {}) if isinstance(record, dict) else None
        if not isinstance(meta, dict):
            raise ValueError(
                f"{path} line {number} is not a chunk record: a JSON object "
                "whose meta, if present, is an object"
            )
        meta["journey"] = stamp
        stamped.append(json.dumps(record, ensure_ascii=False) + "\n")
    # Every record is parsed and stamped before the file is written, because
    # opening it for writing empties it: a record that failed either step
    # used to leave the module's bundle with an empty chunks.jsonl (P10-28).
    path.write_text("".join(stamped), encoding="utf-8", newline="\n")
    return len(stamped)


async def capture_journey(
    page: Any,
    *,
    outline: JourneyOutline,
    options: CaptureOptions,
    root: Path,
    contract: DomContract | None = None,
    minimum_interval_s: float = 1.0,
    resume: bool = True,
    also_open: Iterable[str] | None = None,
) -> JourneyCaptureResult:
    """Capture exactly the module set the enumeration produced.

    Opens no assessment, presses no traversal control, and compares the
    journey's progress before and after.
    """
    contract = contract or DomContract()
    openable = resolve_openable(also_open)
    digest = outline_digest(outline)
    resume_path = root / RESUME_FILE
    completed = _read_resume(resume_path, digest) if resume else set()
    pacer = Pacer(minimum_s=minimum_interval_s)
    outcomes: list[ModuleOutcome] = []
    warnings: list[str] = []

    progress_before = await _progress(page, contract)

    sections = await _accordions(page, contract)
    _reconcile(
        "section",
        f"the journey {outline.title!r}",
        _listed(outline.root.children),
        [node.label for node in sections],
    )

    for section in outline.root.children:
        opened = await _ensure_expanded(
            page, contract, section.ordinal - 1, f"the section {section.title!r}"
        )
        track_titles = await page.evaluate(contract.panel_tracks_js, opened.panel)
        _reconcile(
            "track",
            f"the section {section.title!r}",
            _listed(section.children),
            [str(title) for title in track_titles or []],
        )

        for track in section.children:
            # Re-derived per track, not carried from the reconcile above. The
            # panel id is React `useId` output (P11-3) and the walk returns to
            # the journey between tracks, so an id read before that round trip
            # names a panel that no longer exists. The stand-in re-issues them
            # on every view change, which is how this was caught.
            section_panel = (
                await _ensure_expanded(
                    page,
                    contract,
                    section.ordinal - 1,
                    f"the section {section.title!r}",
                )
            ).panel
            if not await page.evaluate(
                contract.click_track_js, [section_panel, track.title]
            ):
                raise EnumerationFailure(
                    f"the track row {track.title!r} was not present in its section's "
                    f"panel ({section_panel})"
                )
            groups = await _wait_for_accordions(
                page, contract, f"the track {track.title!r}"
            )
            _reconcile(
                "group",
                f"the track {track.title!r}",
                _listed(track.children),
                [node.label for node in groups],
            )

            for group in track.children:
                group_panel = (
                    await _ensure_expanded(
                        page, contract, group.ordinal - 1, f"the group {group.title!r}"
                    )
                ).panel
                seen = await page.evaluate(contract.panel_modules_js, group_panel)
                _reconcile(
                    "module",
                    f"the group {group.title!r}",
                    _listed(group.children),
                    [str(record.get("title", "")) for record in seen or []],
                )
                _write_group_prose(root, outline, group, warnings)

                for module in group.children:
                    outcome = await _visit_module(
                        page,
                        module,
                        contract=contract,
                        outline=outline,
                        options=options,
                        root=root,
                        completed=completed,
                        pacer=pacer,
                        track_title=track.title,
                        group_panel=group_panel,
                        openable=openable,
                    )
                    outcomes.append(outcome)
                    if outcome.status == "captured":
                        completed.add(module.node_id)
                        _write_resume(resume_path, digest, completed)
                    if outcome.status == "captured":
                        # Returning from a module lands on the track view, and a
                        # track view is a shell when the click returns (P11-2) —
                        # so the accordions have to be waited for before the
                        # panel id can be re-derived. And re-derived it must be:
                        # no reference survives a click (P9-4).
                        await _wait_for_accordions(
                            page, contract, f"the track {track.title!r}"
                        )
                        group_panel = (
                            await _ensure_expanded(
                                page,
                                contract,
                                group.ordinal - 1,
                                f"the group {group.title!r}",
                            )
                        ).panel

            if not await page.evaluate(
                contract.click_named_button_js, contract.back_to_journey
            ):
                raise EnumerationFailure(
                    f"no {contract.back_to_journey!r} control on the track "
                    f"{track.title!r}"
                )

    progress_after = await _progress(page, contract)
    if progress_before != progress_after:
        raise EnumerationFailure(
            f"the journey's progress moved during a read-only walk: "
            f"{progress_before!r} -> {progress_after!r}. This is a training record; "
            "the walk is stopped rather than reported as a footnote."
        )

    problems = count_failures(outline.root)
    if problems:
        raise EnumerationFailure(
            "the outline this capture was checked against does not agree with "
            f"itself: {problems}"
        )
    _assert_every_module_accounted_for(outline, outcomes)

    write_index(
        outline,
        {outcome.node_id: outcome for outcome in outcomes},
        root,
        progress_before=progress_before,
        progress_after=progress_after,
    )

    return JourneyCaptureResult(
        journey_id=outline.journey_id,
        root=root,
        outcomes=tuple(outcomes),
        progress_before=progress_before,
        progress_after=progress_after,
        pacing={
            "minimum_interval_s": minimum_interval_s,
            "intervals_s": list(pacer.intervals),
            "delays_s": list(pacer.delays),
            # Stated rather than implied. A floor beneath a cost that already
            # exceeds it never fires, and a run that reports only its setting
            # cannot tell you which case it was in.
            "floor_bound": pacer.bound,
        },
        warnings=tuple(warnings),
    )


async def _visit_module(
    page: Any,
    module: JourneyNode,
    *,
    contract: DomContract,
    outline: JourneyOutline,
    options: CaptureOptions,
    root: Path,
    completed: set[str],
    pacer: Pacer,
    track_title: str,
    group_panel: str,
    openable: set[str],
) -> ModuleOutcome:
    """Refuse, skip, or capture one module — in that order of precedence."""
    kind = (module.module_type or "").strip().lower()
    if kind == ASSESSMENT:
        # Never opened, and recorded so the corpus can say what it does not
        # hold. Clicking one may create an attempt, which is worse than marking
        # a page viewed — the one module type the walker refuses outright.
        LOGGER.info("Refusing the assessment %r (%s)", module.title, module.node_id)
        return ModuleOutcome(
            node_id=module.node_id,
            title=module.title,
            module_type=module.module_type,
            status="refused",
            reason=(
                "assessments are never opened: entering one may create an attempt "
                "against the user's training record (docs/13)"
            ),
        )
    if kind not in openable:
        # The other half of the allowlist, and a distinct fact from the one
        # above: "this is an assessment" and "I could not establish what this
        # is" need to be told apart by whoever reads the corpus.
        reason = (
            "the module's type is absent — the row's last <p> read empty, so what "
            "this module is could not be established"
            if not kind
            else f"the module type {module.module_type!r} is not recognised"
        )
        LOGGER.warning("Refusing %r (%s): %s", module.title, module.node_id, reason)
        return ModuleOutcome(
            node_id=module.node_id,
            title=module.title,
            module_type=module.module_type,
            status="refused",
            reason=(
                f"{reason}. Only {sorted(openable)} are opened, because a module "
                "whose type is not the one measured safe may be an assessment "
                "under another name. Pass --allow-module-type once you have "
                "looked at one."
            ),
        )

    destination = module_directory(root, outline, module)
    if module.node_id in completed:
        LOGGER.info("Already captured, skipping %s", module.node_id)
        return ModuleOutcome(
            node_id=module.node_id,
            title=module.title,
            module_type=module.module_type,
            status="skipped-resumed",
            output=str(destination),
            reason="captured by an earlier run of this journey",
        )

    await pacer.wait()
    # From the click that opens the module, which is when its resources load.
    # The walker passed an empty list here, so no module's bundle could say a
    # request had failed (docs/09 P20-6).
    failed_requests = RequestFailures()
    page.on("requestfailed", failed_requests.record)
    try:
        await open_module(page, contract, group_panel, module.title)
        try:
            await _capture_one_module(
                page,
                module,
                destination=destination,
                options=options,
                source=f"{outline.source}#{module.node_id}",
                failed_requests=failed_requests,
            )
        finally:
            await return_to_track(page, contract, f"the module {module.title!r}")
    finally:
        page.remove_listener("requestfailed", failed_requests.record)
    stamp_chunk_paths(destination / "bundle", outline, module)
    return ModuleOutcome(
        node_id=module.node_id,
        title=module.title,
        module_type=module.module_type,
        status="captured",
        output=str(destination),
    )


def _write_group_prose(
    root: Path, outline: JourneyOutline, group: JourneyNode, warnings: list[str]
) -> None:
    """The group's description, at the group's own level.

    It exists nowhere else in the journey (P9-1), so losing it loses content no
    other capture recovers. Written once at the level it belongs to rather than
    copied into every module beneath it: it is a property of the group, and the
    output shape in docs/13 gives the group a directory precisely because it is
    a level rather than a heading.
    """
    trail = _trail(outline.root, group.node_id)
    if trail is None:  # pragma: no cover - the caller walks this tree
        return
    directory = root
    for node in trail[1:]:
        directory /= slug(node.title, node.ordinal)
    directory.mkdir(parents=True, exist_ok=True)
    if not group.prose:
        warnings.append(
            f"the group {group.title!r} ({group.node_id}) carries no description; "
            "docs/13 P9-1 expects prose at this level and none was found"
        )
        return
    (directory / "group.md").write_text(
        f"# {group.title}\n\n{group.prose}\n", encoding="utf-8", newline="\n"
    )


def _assert_every_module_accounted_for(
    outline: JourneyOutline, outcomes: list[ModuleOutcome]
) -> None:
    """No module in the outline may be unaccounted for, whatever its fate."""
    expected = {module.node_id for module in outline.modules()}
    seen = {outcome.node_id for outcome in outcomes}
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra:
        raise EnumerationFailure(
            f"coverage does not match the outline: {len(missing)} module(s) listed and "
            f"never reached ({missing[:5]}), {len(extra)} reached and not listed "
            f"({extra[:5]})."
        )
