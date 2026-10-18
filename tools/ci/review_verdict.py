"""The Claude review's gate and verdict, and re-judging the verdict.

`.github/workflows/claude-review.yml` reviews a pull request once and judges
every head. Its required check, `claude review verdict`, answers one question
on each head: **has Claude's review left a finding nobody has addressed?**

It fails when

- a review thread Claude started is unresolved, outdated or not;
- a summary Claude posted (a pull request comment, or a review with a body)
  is neither the review's "No issues found" nor a stop notice, and has not
  been hidden as resolved or outdated;
- no review has finished on the pull request: none has run, one is still
  running, or the latest one failed its own guard, the one that fails a review
  that did not show it read the diff (docs/09 P10-18).

It passes when the latest review finished and found nothing, or every thread
it left is resolved and every summary is clean or hidden. A false positive is
answered in its thread and the thread resolved, exactly as a fixed finding is;
there is no switch that waives the check. A pull request that edits the
reviewer passes unreviewed, with a warning, because the review action refuses
to run a workflow file that differs from the default branch's, and the guard
has always passed that case. Three kinds of pull request pass without a review
by policy, and the summary names which (docs/09 P89): a bot's, a fork's, and
the sync's own `sync/` branches. A draft gets no verdict: the workflow runs
the job under another name and judges nothing, so a draft's head carries no
`claude review verdict` until it is marked ready.

The review reads a head once. Commits pushed after it are judged against what
it found, not reviewed again, and the summary says how many there are.

GitHub runs no workflow when a thread is resolved, so nothing would notice a
finding being addressed. The verdict is therefore re-run, never re-created:
re-running a job replaces its result in place. `rejudge` re-runs every failed
verdict on a head; `claude-review-rejudge.yml` calls it after a reply in one
of Claude's threads, once the thread is resolved, and a verdict that passes, or
whose pull request has moved to a new head, calls it for the head that matters.
By hand:

    python tools/ci/review_verdict.py rejudge --repo OWNER/REPO --pr 123

or re-run the failed job (`gh run rerun <run id> --failed`).

Everything here reads GitHub through `gh`. A read that fails fails the
verdict: an answer the check could not read is not a clean one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The workflow that reviews and judges; its review job; its verdict job.
#: tests/test_review_verdict.py holds both names to the workflow file, and
#: the verdict's name to every list of required checks that names it.
WORKFLOW = ".github/workflows/claude-review.yml"
REVIEW_JOB = "review"
VERDICT_JOB = "claude review verdict"
#: The GitHub Actions app, which creates every job's check run.
ACTIONS_APP_ID = 15368
#: Events on which the review runs. On `synchronize` it runs only while no
#: review has been attempted on the pull request.
REVIEW_EVENTS = frozenset({"opened", "ready_for_review", "reopened"})
#: How the reviewer appears: REST names the app's bot user `claude[bot]`;
#: GraphQL names it `claude`, as a Bot rather than a User.
REVIEWER_LOGIN = "claude[bot]"
REVIEWER_BOT = "claude"
SYNC_PREFIX = "sync/"
#: The review's summary when it found nothing, as the code-review plugin
#: writes it.
NO_ISSUES = re.compile(r"^\W*No issues found\b", re.MULTILINE)
#: The first line the workflow tells the reviewer to write when it stops at
#: the plugin's first step on purpose (closed, draft, nothing to review, or
#: already reviewed). Any other summary counts as reporting something.
STOPPED = re.compile(r"^\W*Claude review stopped:", re.MULTILINE)
#: Why a hidden summary counts as addressed. GitHub's other reasons (spam,
#: abuse, off-topic, duplicate, low quality) say nothing about the finding.
ADDRESSED_REASONS = frozenset({"resolved", "outdated"})
#: Check-run conclusions a re-run can turn into a fresh verdict.
RERUNNABLE = frozenset({"failure", "cancelled", "timed_out", "startup_failure"})
#: How long the reply listener waits for the thread to be resolved. A reply
#: and its resolution come from one session a few seconds apart.
REPLY_WAIT_SECONDS = 300
POLL_SECONDS = 15

Json = dict[str, Any]


class GhError(RuntimeError):
    pass


def gh(*args: str) -> str:
    """Run gh and return its stdout; GhError, with gh's stderr, if it fails."""
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise GhError("gh is not on PATH") from error
    if result.returncode != 0:
        raise GhError(
            f"gh {' '.join(args[:3])} exited {result.returncode}: "
            f"{result.stderr.strip()[:600]}"
        )
    return result.stdout


def gh_json(*args: str) -> Any:
    out = gh(*args)
    try:
        return json.loads(out)
    except json.JSONDecodeError as error:
        raise GhError(
            f"gh {' '.join(args[:3])} returned no JSON: {out[:200]!r}"
        ) from error


def graphql(query: str, **variables: str | int) -> Json:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    data = gh_json(*args)
    if not isinstance(data, dict) or data.get("errors") or "data" not in data:
        raise GhError(f"GraphQL answered {json.dumps(data)[:600]}")
    result: Json = data["data"]
    return result


# --------------------------------------------------------------------------
# What the review left


@dataclass(frozen=True)
class Attempt:
    """One run of the review job: where it ran, on which head, and how it ended."""

    run_id: int
    url: str
    head_sha: str
    started_at: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class Thread:
    url: str
    path: str
    resolved: bool
    outdated: bool
    by_reviewer: bool
    comment_ids: tuple[int, ...]


@dataclass(frozen=True)
class Summary:
    kind: str  # "comment" or "review"
    url: str
    body: str
    #: GitHub's minimizedReason when the summary is hidden, else None.
    hidden_as: str | None


@dataclass
class Feedback:
    threads: list[Thread]
    summaries: list[Summary]


def is_reviewer(author: Mapping[str, Any] | None) -> bool:
    if not author:
        return False
    login = author.get("login")
    return login == REVIEWER_LOGIN or (
        login == REVIEWER_BOT and author.get("__typename") == "Bot"
    )


def classify(summary: Summary) -> str:
    """ "hidden", "stopped", "no issues" or "unaddressed"."""
    reason = (summary.hidden_as or "").strip().lower().replace("_", "-")
    if summary.hidden_as is not None and reason in ADDRESSED_REASONS:
        return "hidden"
    if STOPPED.search(summary.body):
        return "stopped"
    if NO_ISSUES.search(summary.body):
        return "no issues"
    return "unaddressed"


def review_state(attempts: Sequence[Attempt]) -> tuple[str, Attempt | None]:
    """("running" | "reviewed" | "failed" | "none", the attempt that decides it).

    A review still queued or running decides first: what it finds is not known
    yet. Otherwise the latest review that ended decides, skipped and cancelled
    ones aside (a cancelled review was replaced by a newer one).
    """
    ordered = sorted(attempts, key=lambda a: (a.started_at, a.run_id), reverse=True)
    for attempt in ordered:
        if attempt.status != "completed":
            return "running", attempt
    for attempt in ordered:
        if attempt.conclusion in (None, "skipped", "cancelled"):
            continue
        return ("reviewed" if attempt.conclusion == "success" else "failed"), attempt
    return "none", None


def review_attempts(
    repo: str,
    number: int,
    head_ref: str,
    opened_at: str,
    *,
    exclude_run: int | None = None,
) -> list[Attempt]:
    """Every run of the review job on pull request *number*.

    A run is the pull request's when it ran on the pull request's branch, from
    a `pull_request` event, no earlier than the pull request was opened. Its
    `pull_requests` field cannot decide that alone: GitHub fills it when it is
    read, and a review run that named its pull request while it was open named
    nothing once it had merged (docs/09 P89). A run that names a different pull
    request is that one's.
    """
    branch = urllib.parse.quote(head_ref, safe="")
    runs: list[Json] = []
    for page in range(1, 4):
        found = gh_json(
            "api",
            f"repos/{repo}/actions/workflows/{WORKFLOW.rsplit('/', 1)[1]}/runs"
            f"?event=pull_request&branch={branch}&per_page=100&page={page}",
        )
        batch = found.get("workflow_runs") if isinstance(found, dict) else None
        if not isinstance(batch, list):
            raise GhError(
                f"the run list for {head_ref} has no workflow_runs: {found!r:.300}"
            )
        runs += batch
        if len(batch) < 100:
            break
    if not opened_at:
        raise GhError(f"#{number} has no creation time to bound its runs by")
    attempts = []
    for run in runs:
        if run.get("id") == exclude_run:
            continue
        if (
            run.get("head_branch") != head_ref
            or str(run.get("created_at") or "") < opened_at
        ):
            continue
        named = {p.get("number") for p in run.get("pull_requests") or []}
        if named and number not in named:
            continue
        jobs = gh_json(
            "api",
            f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
        )["jobs"]
        for job in jobs:
            if job.get("name") != REVIEW_JOB:
                continue
            attempts.append(
                Attempt(
                    run_id=int(run["id"]),
                    url=str(job.get("html_url") or run.get("html_url")),
                    head_sha=str(run.get("head_sha") or ""),
                    started_at=str(
                        job.get("started_at") or run.get("created_at") or ""
                    ),
                    status=str(job.get("status") or "unknown"),
                    conclusion=job.get("conclusion"),
                )
            )
    return attempts


_THREADS = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved isOutdated path
          comments(first: 100) {
            nodes { databaseId url author { login __typename } }
          }
        }
      }
    }
  }
}
"""

_SUMMARIES = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      %s(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          url body isMinimized minimizedReason author { login __typename }
        }
      }
    }
  }
}
"""


def _pages(query: str, connection: str, repo: str, number: int) -> list[Json]:
    owner, name = repo.split("/", 1)
    nodes: list[Json] = []
    after: str | None = None
    while True:
        variables: dict[str, str | int] = {
            "owner": owner,
            "name": name,
            "number": number,
        }
        if after:
            variables["after"] = after
        pull = (graphql(query, **variables).get("repository") or {}).get("pullRequest")
        if not isinstance(pull, dict) or not isinstance(pull.get(connection), dict):
            raise GhError(f"#{number} has no {connection} in the answer")
        part = pull[connection]
        nodes += part.get("nodes") or []
        info = part.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return nodes
        after = str(info.get("endCursor"))


def fetch_threads(repo: str, number: int) -> list[Thread]:
    threads = []
    for node in _pages(_THREADS, "reviewThreads", repo, number):
        comments = (node.get("comments") or {}).get("nodes") or []
        if not comments:
            continue
        first = comments[0]
        threads.append(
            Thread(
                url=str(first.get("url") or ""),
                path=str(node.get("path") or ""),
                resolved=bool(node.get("isResolved")),
                outdated=bool(node.get("isOutdated")),
                by_reviewer=is_reviewer(first.get("author")),
                comment_ids=tuple(
                    int(c["databaseId"]) for c in comments if c.get("databaseId")
                ),
            )
        )
    return threads


def fetch_feedback(repo: str, number: int) -> Feedback:
    summaries = []
    for kind, connection in (("comment", "comments"), ("review", "reviews")):
        for node in _pages(_SUMMARIES % connection, connection, repo, number):
            body = str(node.get("body") or "")
            # A review with no body is the envelope of inline comments, which
            # are judged as threads.
            if not is_reviewer(node.get("author")) or not body.strip():
                continue
            summaries.append(
                Summary(
                    kind=kind,
                    url=str(node.get("url") or ""),
                    body=body,
                    hidden_as=(
                        str(node.get("minimizedReason") or "unknown")
                        if node.get("isMinimized")
                        else None
                    ),
                )
            )
    threads = [t for t in fetch_threads(repo, number) if t.by_reviewer]
    return Feedback(threads=threads, summaries=summaries)


def edits_reviewer(repo: str, number: int) -> bool:
    """Whether the pull request's diff changes the reviewer's workflow."""
    for page in range(1, 31):
        files = gh_json(
            "api", f"repos/{repo}/pulls/{number}/files?per_page=100&page={page}"
        )
        if not isinstance(files, list):
            raise GhError(f"the file list of #{number} is not a list: {files!r:.300}")
        if any(f.get("filename") == WORKFLOW for f in files):
            return True
        if len(files) < 100:
            return False
    raise GhError(f"#{number} lists more files than GitHub returns")


def commits_since(repo: str, base: str, head: str) -> int:
    found = gh_json("api", f"repos/{repo}/compare/{base}...{head}")
    ahead = found.get("ahead_by") if isinstance(found, dict) else None
    if not isinstance(ahead, int):
        raise GhError(f"compare {base[:12]}...{head[:12]} has no ahead_by")
    return ahead


# --------------------------------------------------------------------------
# The verdict


@dataclass
class Verdict:
    passed: bool
    reason: str
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remedy: str = ""


def exemption(pr: Mapping[str, Any], repo: str) -> str | None:
    """Why a pull request passes without a review, or None if it needs one."""
    user = pr.get("user") or {}
    head = pr.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    if user.get("type") == "Bot":
        return (
            f"it was opened by a bot ({user.get('login')}). The review action "
            "refuses a run a bot started, and what proves a dependency bump is "
            "the golden corpus and parity, which run regardless."
        )
    if head_repo != repo:
        return (
            f"its head is {head_repo or 'a repository that no longer exists'}, "
            f"not {repo}. A fork's run gets no secret to review with, and only "
            "a person merges a fork's pull request."
        )
    if str(head.get("ref") or "").startswith(SYNC_PREFIX):
        return (
            "it is the sync's own branch. What it carries was reviewed where it "
            "was first merged, and the sync merges it by its own rules."
        )
    return None


ADDRESS = (
    "Address each one: fix it and push, or reply in the thread saying why it "
    "is not a problem; then resolve the thread. A summary is addressed by "
    "hiding it as resolved or outdated. This check is re-run after a reply in "
    "one of Claude's threads, once the thread is resolved."
)


def decide(
    pr: Mapping[str, Any],
    state: str,
    attempt: Attempt | None,
    feedback: Feedback,
    *,
    self_edit: bool,
    since: int,
) -> Verdict:
    """The verdict on *pr*'s head, from what was read about it. Pure."""
    unresolved = [t for t in feedback.threads if not t.resolved]
    open_summaries = [s for s in feedback.summaries if classify(s) == "unaddressed"]
    findings = [
        f"unresolved thread{' (outdated)' if t.outdated else ''} on {t.path}: {t.url}"
        for t in unresolved
    ] + [f"{s.kind} that reports something: {s.url}" for s in open_summaries]
    if findings:
        return Verdict(
            False,
            f"Claude's review left {len(findings)} finding(s) nobody has addressed.",
            findings=findings,
            remedy=ADDRESS,
        )
    where = f"{attempt.url} on {attempt.head_sha[:12]}" if attempt else ""
    if state == "running":
        return Verdict(
            False,
            f"A review is still running ({where}).",
            remedy="When it ends, its own verdict re-runs this one.",
        )
    if state == "failed":
        return Verdict(
            False,
            f"The latest review failed ({where}, {attempt.conclusion if attempt else '?'}): "
            "it did not show that it read the diff.",
            remedy=(
                "Read that job's log. Re-running its failed jobs reviews again; a "
                "run the log calls stale needs a fresh event instead (mark the "
                "pull request draft, then ready for review)."
            ),
        )
    commented = bool(feedback.threads or feedback.summaries)
    head = str((pr.get("head") or {}).get("sha") or "")
    if state == "reviewed" and commented:
        scope = (
            "this head"
            if attempt is None or attempt.head_sha == head
            else f"{attempt.head_sha[:12]}; the "
            f"{since if since >= 0 else 'unknown number of'} commit(s) since were "
            "not reviewed"
        )
        return Verdict(True, f"Reviewed {scope}. No finding is left unaddressed.")
    if self_edit:
        return Verdict(
            True,
            "Not reviewed: this pull request edits the reviewer's workflow, and the "
            "review action refuses to run a copy that differs from the default "
            "branch's.",
            warnings=[
                f"{WORKFLOW} changed, so no review read this pull request. Read "
                "the diff yourself before it merges."
            ],
        )
    if state == "reviewed":
        return Verdict(
            False,
            f"The review passed ({where}) but left nothing on the pull request, "
            "and the pull request does not edit the reviewer.",
            remedy="Its guard should have failed it; read that job's log.",
        )
    if pr.get("draft"):
        return Verdict(
            False,
            "Not reviewed yet: this pull request is a draft.",
            remedy=(
                "Nothing to fix while it is held. Marking it ready for review "
                "starts the review; do not do that just to clear this check."
            ),
        )
    return Verdict(
        False,
        "No review has run on this pull request.",
        remedy=(
            "Its next push starts one, because a push is reviewed while no "
            "review has been attempted; so does marking it draft and then ready "
            "for review."
        ),
    )


def judge(repo: str, pr: Mapping[str, Any]) -> tuple[Verdict, list[str]]:
    """Read everything the verdict depends on, then decide. Returns what was seen."""
    number = int(pr["number"])
    head = str(pr["head"]["sha"])
    seen = [
        f"pull request #{number} at {head[:12]} "
        f"({'draft' if pr.get('draft') else 'ready'}, {pr.get('state')}), "
        f"base {(pr.get('base') or {}).get('ref')}, by {(pr.get('user') or {}).get('login')}"
    ]
    reason = exemption(pr, repo)
    if reason:
        return Verdict(True, f"Exempt: {reason}"), seen
    if pr.get("state") != "open":
        return (
            Verdict(
                False,
                f"The pull request is {pr.get('state')}.",
                remedy="Reopening it starts a new review and verdict.",
            ),
            seen,
        )
    attempts = review_attempts(
        repo, number, str(pr["head"]["ref"]), str(pr.get("created_at") or "")
    )
    state, attempt = review_state(attempts)
    seen += [
        f"review {a.url}: {a.status}/{a.conclusion} on {a.head_sha[:12]}"
        for a in attempts
    ] or ["review: no run of the review job on this pull request"]
    seen.append(f"review state: {state}")
    feedback = fetch_feedback(repo, number)
    seen += [
        f"thread {'resolved' if t.resolved else 'UNRESOLVED'}"
        f"{', outdated' if t.outdated else ''}: {t.path} {t.url}"
        for t in feedback.threads
    ]
    seen += [
        f"{s.kind} {classify(s)}"
        f"{f' (hidden as {s.hidden_as})' if s.hidden_as else ''}: {s.url}"
        for s in feedback.summaries
    ]
    self_edit = edits_reviewer(repo, number)
    seen.append(f"edits {WORKFLOW}: {self_edit}")
    since = 0
    if (
        state == "reviewed"
        and attempt is not None
        and attempt.head_sha not in ("", head)
    ):
        # Only the count in the summary depends on this, not the verdict, so a
        # head that a force-push made unreachable costs the number, not the check.
        try:
            since = commits_since(repo, attempt.head_sha, head)
        except GhError as error:
            seen.append(f"commits since the reviewed head: unknown ({error})")
            since = -1
    return decide(pr, state, attempt, feedback, self_edit=self_edit, since=since), seen


def summary_markdown(verdict: Verdict) -> str:
    lines = [
        f"## Claude review verdict: {'pass' if verdict.passed else 'fail'}",
        "",
        verdict.reason,
    ]
    if verdict.findings:
        lines += ["", *(f"- {f}" for f in verdict.findings)]
    if verdict.remedy:
        lines += ["", verdict.remedy]
    for warning in verdict.warnings:
        lines += ["", f"**Warning:** {warning}"]
    return "\n".join(lines) + "\n"


def _append(env_name: str, text: str) -> None:
    path = os.environ.get(env_name)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


# --------------------------------------------------------------------------
# Re-judging


def verdict_checks(repo: str, sha: str) -> list[Json]:
    name = urllib.parse.quote(VERDICT_JOB, safe="")
    found = gh_json(
        "api",
        f"repos/{repo}/commits/{sha}/check-runs?check_name={name}"
        f"&app_id={ACTIONS_APP_ID}&filter=latest&per_page=100",
    )
    checks = found.get("check_runs") if isinstance(found, dict) else None
    if not isinstance(checks, list):
        raise GhError(f"the check runs of {sha[:12]} are not a list: {found!r:.300}")
    return checks


def rerun_failed(repo: str, sha: str, *, skip_run: int | None = None) -> list[str]:
    """Re-run every failed verdict on *sha*; the jobs re-run, as URLs.

    An Actions check run's id is its job's id. A verdict still running reads
    the current state anyway, and one that passed has nothing to add.
    """
    reran = []
    for check in verdict_checks(repo, sha):
        url = str(check.get("html_url") or check.get("id"))
        if skip_run is not None and f"/actions/runs/{skip_run}/" in url:
            continue
        if (
            check.get("status") != "completed"
            or check.get("conclusion") not in RERUNNABLE
        ):
            continue
        gh("api", "-X", "POST", f"repos/{repo}/actions/jobs/{check['id']}/rerun")
        reran.append(url)
    return reran


def wait_for_resolution(
    repo: str,
    number: int,
    comment_id: int,
    *,
    wait: float = REPLY_WAIT_SECONDS,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> Thread | None:
    """The thread holding *comment_id*, once it is resolved or at the deadline.

    A reply can reach the API a moment before its thread lists it, so a
    thread not found yet is looked for again, like one not resolved yet.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + wait
    while True:
        thread = next(
            (t for t in fetch_threads(repo, number) if comment_id in t.comment_ids),
            None,
        )
        if thread is not None and (thread.resolved or not thread.by_reviewer):
            return thread
        if clock() >= deadline:
            return thread
        sleep(poll)


# --------------------------------------------------------------------------
# The commands


def gate_decision(
    env: Mapping[str, str], attempts: Callable[[], list[Attempt]]
) -> tuple[bool, str, str]:
    """(review?, annotation level, why) for one pull_request event.

    The fork test compares names rather than reading `head.repo.fork`: a pull
    request whose fork was deleted has no head repository, so `fork` is null,
    which reads as "not a fork". An empty name is simply not this repository's.
    """
    author, kind = env.get("AUTHOR", ""), env.get("AUTHOR_TYPE", "")
    draft, head_repo = env.get("DRAFT", ""), env.get("HEAD_REPO", "")
    repo, action = env.get("REPO", ""), env.get("ACTION", "")
    if kind == "Bot":
        return (
            False,
            "notice",
            (
                f"it was opened by a bot ({author}), and the action refuses a run a "
                "bot initiated."
            ),
        )
    if draft == "true":
        return (
            False,
            "notice",
            ("it is a draft. Marking it ready for review starts one."),
        )
    if draft != "false":
        return (
            False,
            "warning",
            (
                f"the event carried no draft state (draft={draft or 'unset'}), so "
                "whether it is ready is unknown."
            ),
        )
    if head_repo != repo:
        return (
            False,
            "notice",
            (
                f"its head is {head_repo or 'a repository that no longer exists'}, "
                f"not {repo}. GitHub gives a fork's run no secrets, so there is "
                "nothing to review with."
            ),
        )
    if env.get("HEAD_REF", "").startswith(SYNC_PREFIX):
        return (
            False,
            "notice",
            (
                "it is the sync's own branch: its changes were reviewed where they "
                "were first merged."
            ),
        )
    if env.get("HAS_TOKEN") != "true":
        return (
            False,
            "warning",
            (
                "the CLAUDE_CODE_OAUTH_TOKEN secret did not reach this run, so the "
                f"reviewer cannot sign in. Set it as a repository secret on {repo}."
            ),
        )
    if action in REVIEW_EVENTS:
        return True, "notice", f"the pull request was {action.replace('_', ' ')}."
    if action != "synchronize":
        return (
            False,
            "notice",
            f"a {action or 'missing'} event does not start a review.",
        )
    tried = sorted(
        (
            a
            for a in attempts()
            if a.status != "completed"
            or a.conclusion not in (None, "skipped", "cancelled")
        ),
        key=lambda a: (a.started_at, a.run_id),
        reverse=True,
    )
    if tried:
        latest = tried[0]
        return (
            False,
            "notice",
            (
                f"it has been reviewed already ({latest.url}, {latest.conclusion or latest.status}). "
                "A push is judged by the verdict, not reviewed again."
            ),
        )
    return (
        True,
        "notice",
        (
            "no review has been attempted on it yet: GitHub starts no run for a "
            "pull request opened while it conflicts with its base, so this push is "
            "its first chance."
        ),
    )


def cmd_gate(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    env.setdefault("REPO", args.repo)
    print(
        f"author={env.get('AUTHOR')} ({env.get('AUTHOR_TYPE')}) draft={env.get('DRAFT')} "
        f"head={env.get('HEAD_REPO') or 'none'}:{env.get('HEAD_REF')} repo={env.get('REPO')} "
        f"action={env.get('ACTION')} token-present={env.get('HAS_TOKEN')}"
    )
    try:
        review, level, reason = gate_decision(
            env,
            lambda: review_attempts(
                args.repo,
                args.pr,
                env.get("HEAD_REF", ""),
                env.get("OPENED_AT", ""),
                exclude_run=args.run_id,
            ),
        )
    except GhError as error:
        print(f"::error::the gate could not read this pull request's reviews: {error}")
        return 1
    if review:
        _append(
            "GITHUB_STEP_SUMMARY",
            f"## Claude review: running\n\nIt runs because {reason}\n",
        )
    else:
        _append(
            "GITHUB_STEP_SUMMARY",
            f"## Claude review: skipped\n\nNo review ran on this event because {reason}\n",
        )
        print(f"::{level}::Claude review skipped: {reason}")
    print(f"review={str(review).lower()}")
    _append("GITHUB_OUTPUT", f"review={str(review).lower()}\n")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    pr: Json | None = None
    seen: list[str] = []
    try:
        pr = gh_json("api", f"repos/{args.repo}/pulls/{args.pr}")
        if not isinstance(pr, dict):
            raise GhError(f"#{args.pr} answered {pr!r:.200}")
        verdict, seen = judge(args.repo, pr)
    except (GhError, KeyError, TypeError, ValueError) as error:
        verdict = Verdict(
            False,
            f"The verdict could not read what it judges: {error}",
            remedy="An answer this check could not read is not a clean one.",
        )
    if args.review_result:
        seen.append(f"this run's review job: {args.review_result}")
    for line in seen:
        print(line)
    for warning in verdict.warnings:
        print(f"::warning::{warning}")
    for finding in verdict.findings:
        print(f"::error::{finding}")
    print(("" if verdict.passed else "::error::") + verdict.reason)
    if not verdict.passed:
        verdict.remedy = (
            f"{verdict.remedy} To re-judge at once, re-run this job: "
            f"gh run rerun {args.run_id} --failed"
        ).strip()
    if verdict.remedy:
        print(verdict.remedy)
    head = str(((pr or {}).get("head") or {}).get("sha") or "")
    print(
        f"verdict={'pass' if verdict.passed else 'fail'} findings={len(verdict.findings)} "
        f"head={head[:12] or 'unknown'} run-head={args.run_head[:12]}"
    )
    _append("GITHUB_STEP_SUMMARY", summary_markdown(verdict))
    if pr is not None and pr.get("state") == "open" and head:
        # A verdict is about one head. When the pull request has moved on, the
        # one that matters is the new head's, which may have judged a review
        # still running; when this one passes, an older failed one on the same
        # head is stale. Either way, re-run them rather than leave them red.
        if head != args.run_head or verdict.passed:
            try:
                for url in rerun_failed(args.repo, head, skip_run=args.run_id):
                    print(f"re-ran the verdict {url}")
            except GhError as error:
                print(
                    f"::warning::could not re-run the verdicts on {head[:12]}: {error}"
                )
    return 0 if verdict.passed else 1


def cmd_rejudge(args: argparse.Namespace) -> int:
    try:
        heads: list[tuple[int, str]] = []
        if args.sha:
            found = gh_json("api", f"repos/{args.repo}/commits/{args.sha}/pulls")
            heads = [
                (int(p["number"]), args.sha)
                for p in found
                if p.get("state") == "open"
                and (p.get("head") or {}).get("sha") == args.sha
            ]
            if not heads:
                print(f"no open pull request has {args.sha[:12]} as its head")
        else:
            pr = gh_json("api", f"repos/{args.repo}/pulls/{args.pr}")
            if pr.get("state") != "open":
                print(f"#{args.pr} is {pr.get('state')}; nothing to re-judge")
                return 0
            if args.after_comment:
                thread = wait_for_resolution(
                    args.repo, args.pr, args.after_comment, wait=args.wait
                )
                if thread is None:
                    print(
                        f"comment {args.after_comment} starts no thread on #{args.pr}"
                    )
                    return 0
                if not thread.by_reviewer:
                    print(
                        f"{thread.url} is not one of Claude's threads; nothing to re-judge"
                    )
                    return 0
                if not thread.resolved:
                    print(
                        f"{thread.url} is still unresolved after {args.wait:.0f} s, so the "
                        "verdict stands. Resolving it later needs a re-run of the "
                        "verdict (this command with --pr alone, or gh run rerun)."
                    )
                    return 0
                print(f"{thread.url} is resolved")
            heads = [(int(pr["number"]), str(pr["head"]["sha"]))]
        for number, sha in heads:
            reran = rerun_failed(args.repo, sha)
            for url in reran:
                print(f"#{number}: re-ran the verdict {url}")
            if not reran:
                print(f"#{number}: no failed verdict on {sha[:12]} to re-run")
    except GhError as error:
        print(f"::error::could not re-judge: {error}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate", help="whether this event reviews the pull request")
    gate.add_argument("--repo", required=True)
    gate.add_argument("--pr", type=int, required=True)
    gate.add_argument("--run-id", type=int, required=True)
    judge_cmd = sub.add_parser("judge", help="the verdict on the pull request's head")
    judge_cmd.add_argument("--repo", required=True)
    judge_cmd.add_argument("--pr", type=int, required=True)
    judge_cmd.add_argument("--run-id", type=int, required=True)
    judge_cmd.add_argument("--run-head", required=True)
    judge_cmd.add_argument("--review-result", default="")
    rejudge = sub.add_parser("rejudge", help="re-run the failed verdicts on a head")
    rejudge.add_argument("--repo", required=True)
    which = rejudge.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int)
    which.add_argument("--sha")
    rejudge.add_argument(
        "--after-comment",
        type=int,
        help="with --pr: a reply in one of Claude's threads; wait for that thread to be resolved",
    )
    rejudge.add_argument("--wait", type=float, default=REPLY_WAIT_SECONDS)
    args = parser.parse_args(argv)
    if args.command == "gate":
        return cmd_gate(args)
    if args.command == "judge":
        return cmd_judge(args)
    if args.after_comment and not args.pr:
        parser.error("--after-comment needs --pr")
    return cmd_rejudge(args)


if __name__ == "__main__":
    sys.exit(main())
