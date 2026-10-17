#!/usr/bin/env python3
"""Fail when the dependency updates have stopped arriving.

    uv run python tools/audit/staleness.py --repo OWNER/NAME           # judge and report
    uv run python tools/audit/staleness.py --repo OWNER/NAME --issue   # and keep an issue
    uv run python tools/audit/staleness.py heartbeat --repo OWNER/NAME --workflow FILE

Dependabot proposes updates and `dependabot-auto-merge.yml` merges the green
ones, so nothing tells a person that the pipeline has stopped: no bump is the
same as no news. On 2026-10-17 it had stopped for about a week. Dependabot's
pull requests had run no CI, two green bumps could not merge themselves, and
the alarm that should have said so read an empty answer as "none stale"
(docs/09 P84).

So this judges the pipeline by its results rather than by its parts:

- **Direct dependencies.** Every package `pyproject.toml` names, at the
  version `uv.lock` pins, against PyPI. One is stale when a newer stable
  release has been out longer than Dependabot's cooldown for that kind of
  release plus `SLACK_DAYS`. Transitive pins are not judged: Dependabot does
  not propose them, many are held back by what depends on them, and an alarm
  that is always red is one nobody reads. Their vulnerabilities are
  tools/audit/check.py's job.
- **Pinned actions.** Every `uses: owner/repo@<sha> # <tag>` in the
  workflows, against the action's releases, by the same rule; and the tag in
  the comment must still name the pinned commit, because a tag that moved is
  how a hijacked action arrives (the pin itself is safe; the next bump is not).
- **Dependabot's pull requests.** One open past `DEPENDABOT_OPEN_HOURS`, one
  with no check run or no auto-merge after `DEPENDABOT_GRACE_HOURS`, and one
  whose checks failed. Each of those waits for a person who has not been told.

A package or action may be held, with a reason and a date to look again,
in `holds.toml` beside this file. An expired hold is a problem, and so is a
hold on something that is no longer behind, so the list cannot rot quietly.

Nothing here reads absence as success (CLAUDE.md): an input that cannot be
read, a PyPI or GitHub answer that does not come, a lock that pins nothing,
makes the run INCOMPLETE, which fails it as surely as a stale pin does.

`heartbeat` asks whether another repository's run of a workflow is still
happening: a scheduled workflow that GitHub disabled, or that stopped, says
nothing at all, so a second scheduler has to notice.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
import tomllib
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[2]
HOLDS = Path(__file__).with_name("holds.toml")

#: Dependabot's cooldown, in days, as `.github/dependabot.yml` sets it. A
#: release is not proposed until it is this old; tests/test_dependency_staleness.py
#: holds these to the file, so the alarm and the pipeline cannot disagree.
COOLDOWN_DAYS: dict[str, dict[str, int]] = {
    "uv": {"major": 14, "minor": 7, "patch": 3},
    # GitHub Actions takes `default-days` only; Dependabot cannot tell an
    # action's major release from its patch (GitHub's options reference).
    "github-actions": {"major": 7, "minor": 7, "patch": 7},
}
#: How long past its cooldown a release may wait before it counts as stale:
#: one weekly Dependabot run to propose it, and one more week for its pull
#: request to pass CI and merge, or for a person to fix it.
SLACK_DAYS = 14
#: How long a Dependabot pull request may stay open. A green one merges itself
#: within the hour, so three days means it needs a person (docs/09 P10-31).
DEPENDABOT_OPEN_HOURS = 72
#: How long a Dependabot pull request may go without a check run or without
#: auto-merge armed. Both start within minutes; on 2026-10-17 neither had
#: happened for days, because every workflow was off.
DEPENDABOT_GRACE_HOURS = 6
#: Dependabot's login as `gh pr list --json author` spells it.
DEPENDABOT = "app/dependabot"
#: Why a green bump that edits a workflow file sits unmerged. Measured on
#: webshot-ai#26 and #28 (docs/09 P84-3): once another pull request had
#: changed a workflow file on master under them, merging would have written
#: workflow text neither side held, and GitHub refused that to the
#: GITHUB_TOKEN that armed auto-merge ("Tried to create or update workflow
#: without `workflows` permission") and turned auto-merge off. #28, rebased by
#: Dependabot onto that master, then merged itself.
REBASE = (
    "It edits a workflow file, and GitHub turns a bump's auto-merge off when "
    "master's workflow files have moved under it (docs/09 P84-3). "
)
#: Check-run conclusions and status states that mean a check did not pass.
FAILED = {
    "FAILURE",
    "TIMED_OUT",
    "STARTUP_FAILURE",
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
}

ISSUE_TITLE = "dependencies: updates have stalled"
HEARTBEAT_TITLE = "dependencies: the dependency watch has stopped"
#: How long another repository's daily run may be missing before the
#: heartbeat fails: a day, plus GitHub's lateness on scheduled runs.
HEARTBEAT_HOURS = 36.0
PYPI = "https://pypi.org/pypi/{name}/json"

#: Exit statuses, as tools/audit/check.py uses them: a run that could not
#: finish says so with a status of its own.
CLEAN = 0
STALE = 1
INCOMPLETE = 2

#: `uses: owner/repo[/path]@ref` and its trailing comment, on a line that is
#: not itself a comment (a public block of a marked workflow is one).
USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<action>[^\s@#]+)@(?P<ref>[^\s#]+)"
    r"(?:\s+#\s*(?P<tag>\S+))?",
    re.MULTILINE,
)
SHA = re.compile(r"[0-9a-f]{40}")


class InputError(RuntimeError):
    """An input could not be read; the message says which and what was seen."""


@dataclass(frozen=True)
class Finding:
    """One problem. `key` names it stably across runs, so a report can tell
    a new problem from one it already announced."""

    key: str
    text: str


@dataclass
class Report:
    judged: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> int:
        if self.incomplete:
            return INCOMPLETE
        return STALE if self.findings else CLEAN


@dataclass(frozen=True)
class Release:
    version: Version
    published: dt.datetime


@dataclass(frozen=True)
class Hold:
    kind: str  # "package" or "action"
    name: str
    reason: str
    until: dt.date


@dataclass(frozen=True)
class ActionPin:
    repository: str
    sha: str
    tag: str
    files: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.repository}@{self.tag}"


# --------------------------------------------------------------------------
# Small pieces


def parse_time(stamp: str) -> dt.datetime:
    """An ISO 8601 time from PyPI or GitHub, as an aware UTC datetime."""
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def bump_kind(old: Version, new: Version) -> str:
    """ "major", "minor" or "patch": the first release component that differs."""
    a, b = (*old.release, 0, 0), (*new.release, 0, 0)
    if a[0] != b[0]:
        return "major"
    if a[1] != b[1]:
        return "minor"
    return "patch"


def allowed_days(ecosystem: str, kind: str) -> int:
    return COOLDOWN_DAYS[ecosystem][kind] + SLACK_DAYS


def lag(
    current: Version, releases: Iterable[Release], now: dt.datetime
) -> tuple[Release, Release, dt.timedelta] | None:
    """(first newer release, newest release, time since the first) or None.

    The wait is measured from the *first* release newer than the pin, not the
    newest: a project that releases every week always has a fresh newest
    release, and measuring from that would never find the lock behind.
    """
    newer = [r for r in releases if r.version > current and not r.version.is_prerelease]
    if not newer:
        return None
    first = min(newer, key=lambda r: (r.published, r.version))
    newest = max(newer, key=lambda r: r.version)
    return first, newest, now - first.published


def _days(delta: dt.timedelta) -> int:
    return int(delta.total_seconds() // 86400)


def _hours(delta: dt.timedelta) -> int:
    return int(delta.total_seconds() // 3600)


# --------------------------------------------------------------------------
# Inputs


def locked_versions(lock_text: str) -> dict[NormalizedName, list[Version]]:
    """Every registry package uv.lock pins, with each version it pins."""
    try:
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as error:
        raise InputError(f"uv.lock is not TOML: {error}") from None
    pins: dict[NormalizedName, list[Version]] = {}
    for package in lock.get("package", []):
        source = package.get("source", {})
        if not isinstance(source, dict) or "registry" not in source:
            continue  # the project itself, or a path/git source
        name = canonicalize_name(str(package["name"]))
        try:
            version = Version(str(package["version"]))
        except (KeyError, InvalidVersion) as error:
            raise InputError(f"uv.lock pins {name} unreadably: {error!r}") from None
        pins.setdefault(name, []).append(version)
    if not pins:
        raise InputError("uv.lock pins no registry package")
    return {name: sorted(set(versions)) for name, versions in pins.items()}


def direct_dependencies(pyproject_text: str) -> set[NormalizedName]:
    """Every package pyproject.toml asks for itself, in any extra or group."""
    try:
        project = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError as error:
        raise InputError(f"pyproject.toml is not TOML: {error}") from None
    lists: list[list[Any]] = [project.get("project", {}).get("dependencies", [])]
    lists += list(project.get("project", {}).get("optional-dependencies", {}).values())
    lists += list(project.get("dependency-groups", {}).values())
    names: set[NormalizedName] = set()
    for requirements in lists:
        for line in requirements:
            if not isinstance(line, str):
                continue  # an `{include-group = ...}` table
            try:
                names.add(canonicalize_name(Requirement(line).name))
            except InvalidRequirement as error:
                raise InputError(f"pyproject.toml: {line!r}: {error}") from None
    if not names:
        raise InputError("pyproject.toml names no dependency")
    return names


def read_holds(text: str) -> list[Hold]:
    """The holds in holds.toml; a malformed one is an input error, not a pass."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise InputError(f"holds.toml is not TOML: {error}") from None
    holds = []
    for number, entry in enumerate(document.get("hold", []), start=1):
        kinds = [k for k in ("package", "action") if k in entry]
        unknown = set(entry) - {"package", "action", "reason", "until"}
        if len(kinds) != 1 or unknown or not entry.get("reason"):
            raise InputError(
                f"holds.toml hold {number} needs exactly one of package/action, "
                f"a reason and an until date; it has {sorted(entry)}"
            )
        until = entry.get("until")
        if not isinstance(until, dt.date) or isinstance(until, dt.datetime):
            raise InputError(f"holds.toml hold {number}: until must be a date")
        name = str(entry[kinds[0]])
        holds.append(
            Hold(
                kinds[0],
                canonicalize_name(name) if kinds[0] == "package" else name,
                str(entry["reason"]),
                until,
            )
        )
    return holds


def pinned_actions(workflows: Path) -> tuple[list[ActionPin], list[Finding]]:
    """Every action the workflows use, and a finding for each that is not a
    commit SHA with its tag beside it (which is the only form judged here)."""
    files = sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml"))
    if not files:
        raise InputError(f"no workflow files in {workflows}")
    seen: dict[tuple[str, str, str], list[str]] = {}
    findings = []
    for path in files:
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            action, ref, tag = match["action"], match["ref"], match["tag"]
            if action.startswith(("./", "docker://")):
                continue
            repository = "/".join(action.split("/")[:2])
            if not SHA.fullmatch(ref):
                findings.append(
                    Finding(
                        f"unpinned:{path.name}:{action}@{ref}",
                        f"{path.name} uses {action}@{ref}, which is not a commit SHA",
                    )
                )
                continue
            if not tag:
                findings.append(
                    Finding(
                        f"untagged:{path.name}:{action}@{ref}",
                        f"{path.name} pins {action}@{ref[:12]} with no tag comment, "
                        "so neither a person nor this check can tell what it is",
                    )
                )
                continue
            seen.setdefault((repository, ref, tag), []).append(path.name)
    if not seen and not findings:
        raise InputError(f"the workflows in {workflows} use no action")
    pins = [
        ActionPin(repository, sha, tag, tuple(sorted(set(names))))
        for (repository, sha, tag), names in sorted(seen.items())
    ]
    return pins, findings


# --------------------------------------------------------------------------
# What PyPI and GitHub say


def gh(*arguments: str) -> str:
    """Run gh; a failure raises InputError naming the command and its stderr."""
    try:
        completed = subprocess.run(
            ["gh", *arguments], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise InputError("gh is not installed") from None
    if completed.returncode != 0:
        raise InputError(
            f"`gh {' '.join(arguments)}` exited {completed.returncode}: "
            + (completed.stderr.strip() or "(no output)")
        )
    return completed.stdout


def fetch_pypi(name: str) -> dict[str, Any]:
    """PyPI's JSON for *name*, retried; a missing answer is an InputError."""
    url = PYPI.format(name=name)
    request = urllib.request.Request(
        url, headers={"User-Agent": "webshot-dependency-watch"}
    )
    last = ""
    for attempt in range(3):
        if attempt:
            time.sleep(2**attempt)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                document = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            if error.code == 404:
                break
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last = repr(error)
            continue
        if isinstance(document, dict) and isinstance(document.get("releases"), dict):
            return document
        last = "an answer with no releases"
    raise InputError(f"PyPI did not answer for {name} ({url}): {last}")


def pypi_releases(document: Mapping[str, Any]) -> tuple[list[Release], set[Version]]:
    """(installable releases, yanked versions) from PyPI's JSON.

    A release with no files cannot be installed and is left out; one whose
    files are all yanked is reported as yanked. A release's date is its first
    upload: that is when it could first be proposed.
    """
    releases, yanked = [], set()
    for raw, files in document.get("releases", {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        live = [f for f in files if not f.get("yanked")]
        if files and not live:
            yanked.add(version)
        if not live:
            continue
        published = min(parse_time(f["upload_time_iso_8601"]) for f in live)
        releases.append(Release(version, published))
    return releases, yanked


def github_releases(repository: str) -> list[Release]:
    """An action's published releases whose tags order as versions."""
    raw = gh("api", f"repos/{repository}/releases?per_page=100")
    releases = []
    for entry in json.loads(raw or "[]"):
        if (
            entry.get("draft")
            or entry.get("prerelease")
            or not entry.get("published_at")
        ):
            continue
        try:
            version = Version(str(entry["tag_name"]))
        except InvalidVersion:
            continue
        releases.append(Release(version, parse_time(entry["published_at"])))
    return releases


def tag_commit(repository: str, tag: str) -> str:
    return gh("api", f"repos/{repository}/commits/{tag}", "--jq", ".sha").strip()


def open_pull_requests(repository: str) -> list[dict[str, Any]]:
    """Every open pull request, listed, never searched.

    `gh pr list --author` asks GitHub's search API, which answered another
    repository's token with nothing, and nothing read as "no stale bump"
    (docs/09 P84). Listing is ordinary repository data. The limit keeps the
    list complete: a count that leaves some out still reads as authority.
    """
    raw = gh(
        "pr",
        "list",
        "-R",
        repository,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        "number,title,url,createdAt,author,autoMergeRequest,statusCheckRollup,files",
    )
    prs = json.loads(raw or "[]")
    if not isinstance(prs, list):
        raise InputError(f"gh pr list for {repository} returned {type(prs).__name__}")
    return prs


# --------------------------------------------------------------------------
# Judging


def _held(holds: Sequence[Hold], kind: str, name: str) -> Hold | None:
    return next((h for h in holds if h.kind == kind and h.name == name), None)


def judge_behind(
    *,
    kind: str,
    name: str,
    label: str,
    ecosystem: str,
    current: Version,
    releases: Sequence[Release],
    now: dt.datetime,
    holds: Sequence[Hold],
    report: Report,
    used: dict[tuple[str, str], bool],
) -> None:
    """Add a finding when *name* at *current* has waited too long for a release.

    For a held name, records in *used* whether any pin of it is behind. One
    name can have several pins (a lock forked by a marker, an action bumped in
    one workflow and not another), so whether the hold is still needed is
    decided once per name in run_watch, after every pin is judged, and not
    here, where a current pin would call the hold unneeded while another pin
    still needs it (review of #248).
    """
    found = lag(current, releases, now)
    hold = _held(holds, kind, name)
    if hold is not None:
        used[(kind, name)] = used.get((kind, name), False) or found is not None
    if found is None:
        return
    first, newest, waited = found
    bump = bump_kind(current, first.version)
    allowed = allowed_days(ecosystem, bump)
    if waited <= dt.timedelta(days=allowed):
        return
    text = (
        f"{label} {current} is {_days(waited)} days behind: {first.version} came "
        f"out on {first.published:%Y-%m-%d} ({bump}; cooldown "
        f"{COOLDOWN_DAYS[ecosystem][bump]} days + {SLACK_DAYS} allowed), and the "
        f"newest is {newest.version}"
    )
    if hold is None:
        report.findings.append(Finding(f"behind:{kind}:{name}:{current}", text))
    elif hold.until < now.date():
        report.findings.append(
            Finding(
                f"hold-expired:{kind}:{name}",
                f"{text}. Its hold ran out on {hold.until} ({hold.reason}): "
                "update it, or renew the hold with a reason",
            )
        )
    else:
        report.notes.append(f"held until {hold.until}: {text} ({hold.reason})")


def judge_packages(
    lock_text: str,
    pyproject_text: str,
    holds: Sequence[Hold],
    now: dt.datetime,
    report: Report,
    used: dict[tuple[str, str], bool],
    fetch: Callable[[str], Mapping[str, Any]] = fetch_pypi,
) -> None:
    pins = locked_versions(lock_text)
    direct = direct_dependencies(pyproject_text)
    missing = sorted(direct - pins.keys())
    if missing:
        raise InputError(
            f"pyproject.toml names {missing}, which uv.lock does not pin from a registry"
        )

    def ask(name: NormalizedName) -> tuple[NormalizedName, Mapping[str, Any] | str]:
        try:
            return name, fetch(name)
        except InputError as error:
            return name, str(error)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        answers = dict(pool.map(ask, sorted(direct)))
    judged = 0
    for name in sorted(direct):
        answer = answers[name]
        if isinstance(answer, str):
            report.incomplete.append(answer)
            continue
        releases, yanked = pypi_releases(answer)
        known = {r.version for r in releases} | yanked
        for version in pins[name]:
            judged += 1
            if version in yanked:
                report.findings.append(
                    Finding(
                        f"yanked:{name}:{version}",
                        f"uv.lock pins {name} {version}, which PyPI has yanked",
                    )
                )
            elif version not in known:
                report.findings.append(
                    Finding(
                        f"unpublished:{name}:{version}",
                        f"uv.lock pins {name} {version}, which PyPI does not list",
                    )
                )
            judge_behind(
                kind="package",
                name=name,
                label=name,
                ecosystem="uv",
                current=version,
                releases=releases,
                now=now,
                holds=holds,
                report=report,
                used=used,
            )
    transitive = sum(len(v) for n, v in pins.items() if n not in direct)
    report.judged.append(
        f"{judged} pin(s) of {len(direct)} direct dependencies against PyPI "
        f"({transitive} transitive pins are not judged)"
    )


def judge_actions(
    workflows: Path,
    holds: Sequence[Hold],
    now: dt.datetime,
    report: Report,
    used: dict[tuple[str, str], bool],
    releases_of: Callable[[str], list[Release]] = github_releases,
    commit_of: Callable[[str, str], str] = tag_commit,
) -> None:
    pins, findings = pinned_actions(workflows)
    report.findings += findings
    cache: dict[str, list[Release] | str] = {}
    judged = 0
    for pin in pins:
        if pin.repository not in cache:
            try:
                cache[pin.repository] = releases_of(pin.repository)
            except InputError as error:
                cache[pin.repository] = str(error)
        releases = cache[pin.repository]
        if isinstance(releases, str):
            report.incomplete.append(releases)
            continue
        try:
            commit = commit_of(pin.repository, pin.tag)
        except InputError as error:
            report.incomplete.append(str(error))
            continue
        try:
            current = Version(pin.tag)
        except InvalidVersion:
            report.findings.append(
                Finding(
                    f"unordered:{pin}",
                    f"{pin} ({', '.join(pin.files)}) has a tag that does not read "
                    "as a version, so its age cannot be judged",
                )
            )
            continue
        judged += 1
        if commit != pin.sha:
            report.findings.append(
                Finding(
                    f"tag-moved:{pin}:{pin.sha}",
                    f"{pin} is pinned to {pin.sha[:12]} in {', '.join(pin.files)}, "
                    f"but the tag now names {commit[:12]}: either the comment is "
                    "wrong or the tag was moved, which is how a hijacked action "
                    "arrives. Check before any bump",
                )
            )
        judge_behind(
            kind="action",
            name=pin.repository,
            label=f"{pin.repository} ({', '.join(pin.files)})",
            ecosystem="github-actions",
            current=current,
            releases=releases,
            now=now,
            holds=holds,
            report=report,
            used=used,
        )
    report.judged.append(
        f"{judged} pinned action(s) in {len({p.repository for p in pins})} "
        "action repositories against their releases"
    )


def _checks(pr: Mapping[str, Any]) -> tuple[list[str], list[str], int]:
    """(failed check names, pending check names, number of checks)."""
    failed, pending = [], []
    rollup = pr.get("statusCheckRollup") or []
    for check in rollup:
        name = str(check.get("name") or check.get("context") or "?")
        if check.get("__typename") == "StatusContext" or "state" in check:
            state = str(check.get("state", "")).upper()
            if state in FAILED:
                failed.append(name)
            elif state in {"PENDING", "EXPECTED"}:
                pending.append(name)
            continue
        if str(check.get("status", "")).upper() != "COMPLETED":
            pending.append(name)
        elif str(check.get("conclusion", "")).upper() in FAILED:
            failed.append(name)
    return sorted(set(failed)), sorted(set(pending)), len(rollup)


def judge_dependabot(
    prs: Sequence[Mapping[str, Any]], now: dt.datetime, report: Report
) -> None:
    mine = [
        pr
        for pr in prs
        if isinstance(pr.get("author"), dict)
        and pr["author"].get("login") == DEPENDABOT
    ]
    for pr in mine:
        number, url, title = pr["number"], pr["url"], pr["title"]
        age = now - parse_time(str(pr["createdAt"]))
        failed, pending, count = _checks(pr)
        workflows = any(
            str(f.get("path", "")).startswith(".github/workflows/")
            for f in pr.get("files") or []
        )
        where = f"Dependabot's {url} ({title})"
        if failed:
            report.findings.append(
                Finding(
                    f"pr-failed:{number}",
                    f"{where}: checks failed ({', '.join(failed)}). It waits for "
                    "a person to fix it on its branch",
                )
            )
        if age > dt.timedelta(hours=DEPENDABOT_GRACE_HOURS):
            if count == 0:
                report.findings.append(
                    Finding(
                        f"pr-no-checks:{number}",
                        f"{where} is {_hours(age)} h old and no check has run on "
                        "it: were workflows off? Close and reopen it, or comment "
                        "`@dependabot recreate`, to start them",
                    )
                )
            if pr.get("autoMergeRequest") is None and not failed:
                report.findings.append(
                    Finding(
                        f"pr-unarmed:{number}",
                        f"{where} is {_hours(age)} h old and auto-merge is off, so "
                        f"nothing will merge it. {REBASE if workflows else ''}"
                        "Comment `@dependabot rebase`: the rebased bump is armed again",
                    )
                )
        if age > dt.timedelta(hours=DEPENDABOT_OPEN_HOURS):
            why = "its checks failed" if failed else "it needs a person"
            if not failed and not pending and workflows:
                why = (
                    f"it is green and still open. {REBASE}Comment `@dependabot rebase`"
                )
            report.findings.append(
                Finding(
                    f"pr-open:{number}",
                    f"{where} has been open {_hours(age)} h: {why}",
                )
            )
    report.judged.append(f"{len(mine)} open Dependabot pull request(s)")


def run_watch(
    root: Path,
    repository: str,
    now: dt.datetime,
    *,
    fetch: Callable[[str], Mapping[str, Any]] = fetch_pypi,
    releases_of: Callable[[str], list[Release]] = github_releases,
    commit_of: Callable[[str, str], str] = tag_commit,
    list_prs: Callable[[str], list[dict[str, Any]]] = open_pull_requests,
    holds_path: Path | None = None,
) -> Report:
    """Every judgment, each step's failure recorded rather than raised."""
    report = Report()
    used: dict[tuple[str, str], bool] = {}
    holds: list[Hold] = []
    path = holds_path if holds_path is not None else root / HOLDS.relative_to(REPO_ROOT)
    try:
        holds = read_holds(path.read_text(encoding="utf-8")) if path.is_file() else []
    except InputError as error:
        report.incomplete.append(str(error))

    def step(action: Callable[[], None], what: str) -> None:
        try:
            action()
        except InputError as error:
            report.incomplete.append(f"{what}: {error}")
        except OSError as error:
            report.incomplete.append(f"{what}: {error}")

    step(
        lambda: judge_packages(
            (root / "uv.lock").read_text(encoding="utf-8"),
            (root / "pyproject.toml").read_text(encoding="utf-8"),
            holds,
            now,
            report,
            used,
            fetch,
        ),
        "packages",
    )
    step(
        lambda: judge_actions(
            root / ".github" / "workflows",
            holds,
            now,
            report,
            used,
            releases_of,
            commit_of,
        ),
        "actions",
    )
    step(lambda: judge_dependabot(list_prs(repository), now, report), "Dependabot")
    for hold in holds:
        key = (hold.kind, hold.name)
        if key in used:
            if not used[key]:
                report.findings.append(
                    Finding(
                        f"hold-unneeded:{hold.kind}:{hold.name}",
                        f"holds.toml holds {hold.name}, which is not behind any "
                        "more: remove the hold",
                    )
                )
        elif not report.incomplete:
            report.findings.append(
                Finding(
                    f"hold-unknown:{hold.kind}:{hold.name}",
                    f"holds.toml holds the {hold.kind} {hold.name}, which nothing "
                    "here uses: remove the hold",
                )
            )
    return report


# --------------------------------------------------------------------------
# The heartbeat


def run_heartbeat(
    repository: str,
    workflow: str,
    now: dt.datetime,
    max_hours: float = HEARTBEAT_HOURS,
    api: Callable[..., str] | None = None,
) -> Report:
    """Problems with *repository*'s runs of *workflow*: disabled, or not run lately."""
    api = api or gh
    report = Report()
    try:
        meta = json.loads(
            api("api", f"repos/{repository}/actions/workflows/{workflow}")
        )
        runs = json.loads(
            api(
                "api",
                f"repos/{repository}/actions/workflows/{workflow}/runs?per_page=20",
            )
        ).get("workflow_runs")
    except (InputError, ValueError, AttributeError) as error:
        if "HTTP 404" in str(error):
            # Not an unreadable answer but a true one: the watch is not
            # there, as between a merge here and the export that carries it.
            report.findings.append(
                Finding(
                    "heartbeat-missing",
                    f"{repository} has no workflow {workflow}, so nothing there "
                    f"judges the dependencies ({error})",
                )
            )
        else:
            report.incomplete.append(f"{repository} {workflow}: {error}")
        return report
    if not isinstance(runs, list) or not isinstance(meta, dict):
        report.incomplete.append(
            f"{repository} {workflow}: the answer is not a workflow"
        )
        return report
    if not meta.get("created_at"):
        # Read as "added just now", a missing date would excuse a workflow
        # that has never run; absent is not young.
        report.incomplete.append(
            f"{repository} {workflow}: no created_at in {sorted(meta)}"
        )
        return report
    report.judged.append(f"{len(runs)} recent run(s) of {repository} {workflow}")
    # GitHub disables a public repository's scheduled workflows after 60 days
    # without activity ("disabled_inactivity"): the alarm goes quiet exactly
    # when nobody is looking.
    state = str(meta.get("state", "missing"))
    if state != "active":
        report.findings.append(
            Finding(
                f"heartbeat-state:{state}",
                f"{repository}'s {workflow} is {state}: enable it under Actions "
                f"(https://github.com/{repository}/actions/workflows/{workflow})",
            )
        )
    completed = [
        r for r in runs if r.get("status") == "completed" and r.get("created_at")
    ]
    created = parse_time(str(meta["created_at"]))
    limit = dt.timedelta(hours=max_hours)
    if not completed:
        if now - created > limit:
            report.findings.append(
                Finding(
                    "heartbeat-none",
                    f"{repository}'s {workflow} has no completed run since it was "
                    f"added on {created:%Y-%m-%d}",
                )
            )
        return report
    latest = max(completed, key=lambda r: parse_time(str(r["created_at"])))
    age = now - parse_time(str(latest["created_at"]))
    if age > limit:
        report.findings.append(
            Finding(
                "heartbeat-late",
                f"{repository}'s {workflow} last ran {_hours(age)} h ago "
                f"({latest.get('html_url', latest.get('id'))}); it runs daily",
            )
        )
    if latest.get("conclusion") not in {"success", "failure"}:
        report.findings.append(
            Finding(
                f"heartbeat-conclusion:{latest.get('conclusion')}",
                f"{repository}'s latest {workflow} run concluded "
                f"{latest.get('conclusion')!r}, so it judged nothing "
                f"({latest.get('html_url', latest.get('id'))})",
            )
        )
    return report


# --------------------------------------------------------------------------
# Reporting


KEYS = re.compile(r"<!-- keys: (.*?) -->")


def render(report: Report, run_url: str | None, mention: str | None) -> str:
    lines = []
    if report.incomplete:
        lines += [
            "The check could not finish, so it is not clean:",
            "",
            *(f"- {problem}" for problem in report.incomplete),
            "",
        ]
    if report.findings:
        lines += [
            "Behind, or waiting for a person:",
            "",
            *(f"- {finding.text}" for finding in report.findings),
            "",
        ]
    if report.notes:
        lines += ["Held, not counted:", "", *(f"- {note}" for note in report.notes), ""]
    lines += [f"Judged: {'; '.join(report.judged) or 'nothing'}."]
    if run_url:
        lines += ["", f"Run: {run_url}"]
    lines += [
        "",
        'What to do: docs/06-quality-and-testing.md, "Dependency updates". '
        "This issue closes itself on the next clean run.",
    ]
    if mention:
        lines += ["", f"@{mention}"]
    lines += ["", f"<!-- keys: {' '.join(sorted(keys_of(report)))} -->"]
    return "\n".join(lines) + "\n"


def _incomplete_key(problem: str) -> str:
    return "incomplete:" + hashlib.sha256(problem.encode("utf-8")).hexdigest()[:12]


def keys_of(report: Report) -> dict[str, str]:
    """Each problem's stable key, mapped to the text a person reads."""
    keys = {re.sub(r"\s", "_", f.key): f.text for f in report.findings}
    keys.update({_incomplete_key(p): p for p in report.incomplete})
    return keys


def sync_issue(
    repository: str,
    title: str,
    report: Report,
    run_url: str | None,
    mention: str | None,
    api: Callable[..., str] | None = None,
) -> str:
    """Keep one open issue titled *title* while *report* is not clean.

    The body is rewritten every run; a comment is added only when a problem
    appears that the issue did not already list, so a notification means
    something new. Written with the workflow's own token, the issue is GitHub
    Actions', not the owner's, so GitHub notifies the owner of it; an issue
    written with the owner's own token notified nobody for five days
    (docs/09 P84-1). Listed, never searched, as open_pull_requests says.
    """
    api = api or gh
    raw = api(
        "issue",
        "list",
        "-R",
        repository,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        "number,title,body",
    )
    existing = [i for i in json.loads(raw or "[]") if i.get("title") == title]
    if report.status == CLEAN:
        for issue in existing:
            api(
                "issue",
                "close",
                str(issue["number"]),
                "-R",
                repository,
                "--comment",
                f"Clean: {'; '.join(report.judged)}."
                + (f" Run: {run_url}" if run_url else ""),
            )
        return f"closed {len(existing)} issue(s)" if existing else "no issue to close"
    body = render(report, run_url, mention)
    if not existing:
        api("issue", "create", "-R", repository, "--title", title, "--body", body)
        return "opened an issue"
    issue = existing[0]
    old = KEYS.search(str(issue.get("body") or ""))
    old_keys = set(old.group(1).split()) if old else set()
    keys = keys_of(report)
    api("issue", "edit", str(issue["number"]), "-R", repository, "--body", body)
    added = sorted(set(keys) - old_keys)
    if added:
        fresh = [keys[key] for key in added]
        api(
            "issue",
            "comment",
            str(issue["number"]),
            "-R",
            repository,
            "--body",
            "New since the last run:\n\n"
            + "\n".join(f"- {text}" for text in fresh)
            + (f"\n\n@{mention}" if mention else ""),
        )
        return f"updated issue #{issue['number']} and noted {len(added)} new problem(s)"
    return f"updated issue #{issue['number']}"


def print_report(report: Report) -> None:
    for finding in report.findings:
        print(f"PROBLEM     {finding.text}")
    for note in report.notes:
        print(f"HELD        {note}")
    for problem in report.incomplete:
        print(f"INCOMPLETE  {problem}")
    print(f"Judged: {'; '.join(report.judged) or 'nothing'}.")
    if report.incomplete:
        print("The check could not finish, so it is not clean.")
    elif report.findings:
        print(f"{len(report.findings)} problem(s).")
    else:
        print("Clean: nothing to report.")


def run_url_from_env() -> str | None:
    if not os.environ.get("GITHUB_RUN_ID"):
        return None
    return (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="the repository whose Dependabot pull requests and issue to use "
        "(default: $GITHUB_REPOSITORY)",
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--issue",
        action="store_true",
        help="keep one issue in --issue-repo open while this is not clean",
    )
    parser.add_argument(
        "--issue-repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="where the issue lives (default: $GITHUB_REPOSITORY)",
    )
    parser.add_argument("--mention", help="a login to @-mention in the issue")
    sub = parser.add_subparsers(dest="command")
    beat = sub.add_parser(
        "heartbeat", help="fail if --repo's --workflow stopped running"
    )
    beat.add_argument("--workflow", required=True)
    beat.add_argument("--max-hours", type=float, default=HEARTBEAT_HOURS)
    args = parser.parse_args(argv)

    now = dt.datetime.now(dt.UTC)
    if not args.repo:
        print("INCOMPLETE  no --repo given and $GITHUB_REPOSITORY is unset")
        return INCOMPLETE
    try:
        if args.command == "heartbeat":
            title = HEARTBEAT_TITLE
            report = run_heartbeat(args.repo, args.workflow, now, args.max_hours)
        else:
            title = ISSUE_TITLE
            report = run_watch(args.root, args.repo, now)
    except Exception:  # a crash must still reach the issue, and say what it was
        report = Report(incomplete=[f"the check crashed:\n{traceback.format_exc()}"])
        title = HEARTBEAT_TITLE if args.command == "heartbeat" else ISSUE_TITLE
    print_report(report)
    status = report.status
    if args.issue:
        if not args.issue_repo:
            print("INCOMPLETE  --issue needs --issue-repo or $GITHUB_REPOSITORY")
            return INCOMPLETE
        try:
            print(
                sync_issue(
                    args.issue_repo, title, report, run_url_from_env(), args.mention
                )
            )
        except (InputError, ValueError) as error:
            print(f"INCOMPLETE  the issue could not be written: {error}")
            return INCOMPLETE
        # The workflow's fallback step opens an issue itself only when this
        # line was never written.
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write("reported=true\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
