"""`webshot journey URL` — enumerate one journey, then capture what it listed.

Both stages of docs/13's journey walker. The tree is enumerated twice and the
two walks diffed; only a walk that agreed with itself is captured, and
`--dry-run` stops after the outline is written.

The browser session is the capture path's own (`capture/session.launch_context`)
rather than a second launcher, so an auth profile gets the same owner-only
treatment (`0700`, or an owner-only ACL on Windows — docs/09 P10-25), a storage
state loads the same way, and the private-network policy is the same policy. A journey behind a login is the only kind there is.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import CaptureOptions
from ..errors import UsageError, classify
from ..version import VERSION
from .capture import JourneyCaptureResult, capture_journey, slug
from .model import JourneyNode, JourneyOutline, count_failures, diff_outlines
from .walk import DomContract, EnumerationFailure, enumerate_journey

LOGGER = logging.getLogger("webshot.journey")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webshot journey",
        description=(
            "Walk a Continu training journey. Enumerates the tree twice and diffs "
            "the two walks, then captures exactly the module set that enumeration "
            "produced. Never opens an assessment, and never presses 'Next in "
            "Journey' or 'Next in Track'."
        ),
    )
    parser.add_argument("url", help="the journey's URL — the one address it has")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="stop after the enumeration instead of capturing what it listed",
    )
    parser.add_argument(
        "-o",
        "--outline",
        type=Path,
        default=Path("outline.json"),
        help="where to write the enumeration manifest (default: ./outline.json)",
    )
    parser.add_argument(
        "--storage-state", type=Path, help="Playwright auth storage-state JSON"
    )
    parser.add_argument(
        "--auth-profile",
        type=Path,
        help="private persistent browser profile for a signed-in LMS",
    )
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help="open a headed browser and pause for sign-in; requires --auth-profile",
    )
    parser.add_argument(
        "--block-private-requests",
        action="store_true",
        help="refuse requests to private, loopback and link-local addresses",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        help=(
            "directory the captured journey lands in, five levels deep "
            "(default: ./<journey-id>). Ignored under --dry-run"
        ),
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help=(
            "floor on the gap between opening one module and the next "
            "(default: 1.0), so the walk paces its requests to the journey's "
            "host. The achieved intervals are reported, because a floor beneath "
            "a cost that already exceeds it never fires"
        ),
    )
    parser.add_argument(
        "--allow-module-type",
        action="append",
        default=None,
        metavar="TYPE",
        help=(
            "also open modules of this type, after you have looked at one "
            "(repeatable). Only 'article' is opened by default, because a type "
            "this build does not recognise may be an assessment under another "
            "name. 'assessment' itself can never be permitted"
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "capture every module again instead of skipping what a previous run "
            "of this journey finished"
        ),
    )
    parser.add_argument(
        "--no-verify-determinism",
        action="store_true",
        help=(
            "skip the second walk. The diff of two walks is the evidence that the "
            "ids are stable, so this makes the outline unusable as a capture contract"
        ),
    )
    return parser


def _corpus_root(args: argparse.Namespace, outline: JourneyOutline) -> Path:
    """Where the captured journey lands. Named after the journey, not the run."""
    if args.corpus:
        return Path(args.corpus)
    named = slug(outline.title, 1).split("-", 1)[-1]
    return Path(named or outline.journey_id)


def _node_payload(node: JourneyNode) -> dict[str, Any]:
    payload = asdict(node)
    payload["children"] = [_node_payload(child) for child in node.children]
    return payload


def outline_payload(
    outline: JourneyOutline,
    determinism: list[str] | None,
    count_findings: list[str],
) -> dict[str, Any]:
    """The published `outline.json`, matching `schemas/journey-outline.schema.json`."""
    return {
        "outline_format": 1,
        "generator": "webshot",
        "generator_version": VERSION,
        "enumerated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": outline.source,
        "journey_id": outline.journey_id,
        "title": outline.title,
        "progress_before": outline.progress_before,
        "progress_after": outline.progress_after,
        "module_count": len(list(outline.modules())),
        "determinism_findings": determinism,
        # Written into the artifact, not only logged. A consumer holding this
        # file could not otherwise tell an outline that failed its own count
        # assertions from one that passed them: the exit code carried that fact
        # and the exit code is not in the file.
        "count_findings": count_findings,
        "root": _node_payload(outline.root),
        "warnings": list(outline.warnings),
    }


def render_outline(outline: JourneyOutline) -> str:
    """The human-readable outline — what a reviewer reads before a real walk."""
    depth = {"journey": 0, "section": 1, "track": 2, "group": 3, "module": 4}
    return "\n".join(
        f"{'    ' * depth[node.level]}{node.level:<8}  {node.title}"
        for node in outline.nodes()
    )


async def _pause_for_sign_in(page: Any, url: str) -> None:
    """Open the journey headed and wait for a human to finish signing in.

    Here rather than inside `enumerate_journey`, which is a library function
    that goes goto → dismiss modal → read heading → read progress. A blocking
    `input()` in the middle of that would put an interactive prompt inside a
    walk whose whole value is being headless and scriptable. The single-page
    path gets away with it because `_capture` is already the imperative
    pipeline; this one has a CLI seam and the pause belongs in it.

    The flag promised this and did not do it: it set `headless=False` and
    nothing else, so a headed browser opened and the walk raced past the login
    page into "could not read the journey's progress" seven seconds later. The
    help text and the generated `docs/guide/cli.md` both described the pause,
    which makes it a defect rather than a missing feature — the two documents a
    person actually follows disagreed with the code.
    """
    if not sys.stdin.isatty():
        # Refused, not skipped. `input()` on a closed stdin raises EOFError
        # somewhere further in, and a flag that silently does nothing when it
        # cannot do its job is the absence-reads-as-success shape: the run
        # would look like an authenticated walk that found no journey.
        raise UsageError(
            "--interactive-auth needs a terminal to prompt at, and stdin is not "
            "one. Run it from an interactive shell, or sign in once by hand into "
            "the same --auth-profile directory and re-run without the flag."
        )
    await page.goto(url, wait_until="domcontentloaded")
    LOGGER.info(
        "Complete sign-in and MFA in the browser window. The session remains only "
        "inside the private authentication profile, and never reaches a bundle."
    )
    await asyncio.to_thread(
        input, "Press Enter once the journey page is visible and signed in... "
    )
    await page.wait_for_load_state("domcontentloaded")


async def _enumerate(
    args: argparse.Namespace,
) -> tuple[
    JourneyOutline,
    JourneyOutline | None,
    list[str] | None,
    JourneyCaptureResult | None,
]:
    """The outline to publish, the second walk if one ran, and the diff.

    The second walk is returned rather than consumed here so its counts can
    be checked too; `None` for both it and the diff is what a skipped
    determinism check looks like, and nothing downstream may confuse that
    with an empty result.
    """
    from playwright.async_api import async_playwright

    from ..capture.session import launch_context

    # Only the session-shaped fields are read. `output` is required by the
    # options model and is pointed at the outline so nothing invents a second
    # meaning for it; this path renders no PDF.
    options = CaptureOptions(
        source=args.url,
        output=args.outline,
        storage_state=args.storage_state,
        auth_profile=args.auth_profile.expanduser().resolve()
        if args.auth_profile
        else None,
        interactive_auth=args.interactive_auth,
        block_private_requests=args.block_private_requests,
    )
    async with async_playwright() as playwright:
        context, browser = await launch_context(playwright, options)
        try:
            page = await context.new_page()
            if args.interactive_auth:
                await _pause_for_sign_in(page, args.url)
            contract = DomContract()
            first = await enumerate_journey(page, source=args.url, contract=contract)
            if args.no_verify_determinism:
                # `None`, never `[]`. An empty findings list is the *result* of
                # a check that ran and found nothing, and a skipped check that
                # published the same value would be the absence-reads-as-success
                # shape this repository has now written down four times. The
                # warning says the same thing in prose for whoever reads the
                # file rather than the schema.
                return (
                    replace(
                        first,
                        warnings=(
                            *first.warnings,
                            "determinism was not verified: --no-verify-determinism "
                            "skipped the second walk, so nothing here has been "
                            "checked against a second reading and this outline is "
                            "not usable as a capture contract",
                        ),
                    ),
                    None,
                    None,
                    None,
                )
            second = await enumerate_journey(page, source=args.url, contract=contract)
            determinism = diff_outlines(first, second)
            if args.dry_run or determinism:
                # Never capture against a walk that disagreed with itself: the
                # ids the whole corpus is addressed by would be meaningless.
                return first, second, determinism, None
            captured = await capture_journey(
                page,
                outline=first,
                options=options,
                root=_corpus_root(args, first),
                contract=contract,
                minimum_interval_s=args.min_interval,
                resume=not args.no_resume,
                also_open=args.allow_module_type,
            )
            return first, second, determinism, captured
        finally:
            await context.close()
            if browser:
                await browser.close()


def main(argv: Sequence[str]) -> int:
    args = build_parser().parse_args(list(argv))
    if args.interactive_auth and not args.auth_profile:
        raise UsageError("--interactive-auth requires --auth-profile")

    try:
        outline, second, determinism, captured = asyncio.run(_enumerate(args))
    except Exception as exc:
        LOGGER.error("%s", exc)
        return classify(exc)

    # Both walks are counted, not just the one that gets published. `rows()`
    # deliberately excludes `listed`, so a count that disagrees only on the
    # second reading is invisible to `diff_outlines` — checking one walk left
    # exactly half of a split this design states out loud.
    problems = [f"walk 1: {problem}" for problem in count_failures(outline.root)]
    if second is not None:
        problems += [f"walk 2: {problem}" for problem in count_failures(second.root)]
    for problem in problems:
        LOGGER.error("count mismatch: %s", problem)
    for finding in determinism or ():
        LOGGER.error("the two walks disagreed: %s", finding)
    for warning in outline.warnings:
        LOGGER.warning("%s", warning)

    args.outline.parent.mkdir(parents=True, exist_ok=True)
    args.outline.write_text(
        json.dumps(
            outline_payload(outline, determinism, problems),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    sys.stdout.write(render_outline(outline) + "\n")
    LOGGER.info(
        "%d modules across %d sections; outline written to %s",
        len(list(outline.modules())),
        len(outline.root.children),
        args.outline,
    )

    if problems or determinism:  # `determinism` is None when skipped, which is falsy
        # The outline is written anyway: an operator needs to see what the walk
        # produced in order to act on why it is wrong. The exit code is what
        # says it must not be used as a capture contract.
        return classify(
            EnumerationFailure(
                "the enumeration is not usable as a capture contract; see the errors above"
            )
        )

    if captured is not None:
        _report_capture(captured)
    return 0


def _report_capture(result: JourneyCaptureResult) -> None:
    """Say what was captured, what was refused, and what the pacing achieved."""
    for outcome in result.refused:
        LOGGER.info(
            "refused %s (%s): %s", outcome.title, outcome.node_id, outcome.reason
        )
    for warning in result.warnings:
        LOGGER.warning("%s", warning)
    LOGGER.info(
        "%d module(s) captured, %d refused, %d already done; progress %s -> %s",
        len(result.captured),
        len(result.refused),
        sum(1 for o in result.outcomes if o.status == "skipped-resumed"),
        result.progress_before,
        result.progress_after,
    )
    pacing = result.pacing
    if pacing["intervals_s"]:
        # Reported rather than assumed. docs/11 §2 asks for pacing; whether the
        # floor ever bound is a fact about this run, and a limit that cannot
        # fire is P7-9's shape.
        LOGGER.info(
            "pacing: floor %.2fs, achieved intervals min %.2fs / max %.2fs, floor %s",
            pacing["minimum_interval_s"],
            min(pacing["intervals_s"]),
            max(pacing["intervals_s"]),
            "delayed a module open" if pacing["floor_bound"] else "never bound",
        )
    LOGGER.info("corpus written to %s", result.root)
