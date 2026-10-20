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
  that did not show it read the diff (docs/09 P10-18);
- this run's gate did not succeed, and so skipped a review the event called
  for that no other run has attempted: a push while no review has been
  attempted, or an open, reopen or ready-for-review with no later attempt; or
  the workflow passed no gate result at all (docs/09 P89-11). A gate that
  failed on a push after a review was attempted cost nothing, since the gate
  skips such a push anyway, and the verdict says so in a warning;
- the review that finished skipped a check it reports: its session record
  shows no finished bug agent or no finished CLAUDE.md compliance agent, or it
  left no such record at all (docs/09 P89-9). `coverage` measures that in the
  review job, from the action's execution file, and records it on the job as
  an annotation the verdict reads; `rewrite` replaces the plugin's fixed
  "Checked for bugs and CLAUDE.md compliance" with what was measured. A check
  that ran but did not read every changed line passes, with a warning that
  says how many it read.

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

or re-run the failed job (`gh run rerun <run id> --failed`). A re-run can end
in startup_failure with no jobs, again and again, and leave the pull request
with no review check at all; a fresh event (draft, then ready for review)
starts a new run instead, and every failing verdict prints both (P89-11).

Everything here reads GitHub through `gh`. A read that GitHub answers with a
5xx is asked again twice, a few seconds apart; a read that still fails fails
the verdict: an answer the check could not read is not a clean one.
"""

from __future__ import annotations

import argparse
import itertools
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
#: The first line `rewrite` gives a "No issues found" summary whose review did
#: not cover the pull request. It is not a finding to address: the verdict
#: fails on the review's coverage record instead, and re-reviewing clears it.
INCOMPLETE = re.compile(r"^\W*Claude review incomplete:", re.MULTILINE)
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


#: How long to wait before each retry of a read that GitHub answered with a
#: 5xx. One HTTP 502 ended #291's gate, and a gate that ends that way skips
#: the review after it (docs/09 P89-11). After the last retry the error is
#: raised as it always was: a read that keeps failing has failed.
RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0)
_SERVER_ERROR = re.compile(r"\bHTTP 5\d\d\b")
_FIELDS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})


def is_read(args: Sequence[str]) -> bool:
    """Whether a gh call only reads, so that asking again does nothing twice.

    A GraphQL query is a POST that reads; a mutation is not. A REST call that
    sends fields is a POST unless it names another method.
    """
    method = None
    for flag in ("-X", "--method"):
        if flag in args[:-1]:
            method = args[list(args).index(flag) + 1].upper()
    if tuple(args[:2]) == ("api", "graphql"):
        return method in (None, "POST") and not any(
            re.match(r"query=\s*mutation\b", a) for a in args
        )
    return method == "GET" or (method is None and not _FIELDS.intersection(args))


def gh(*args: str) -> str:
    """Run gh and return its stdout; GhError, with gh's stderr, if it fails.

    A diff carries each file's own bytes, and a fixture in a legacy encoding is
    not UTF-8: read strictly, #291's Windows-1252 CSV ended the coverage step
    with a UnicodeDecodeError, and the review job failed having reviewed the
    whole diff (docs/09 P38-11).  A byte that is not UTF-8 becomes U+FFFD,
    which keeps every line where it was; GitHub's JSON is UTF-8 throughout.

    A read that GitHub answers with a 5xx is asked again, after each of
    `RETRY_DELAYS`; anything else fails at once.
    """
    delays = RETRY_DELAYS if is_read(args) else ()
    for tries, delay in enumerate((*delays, None), start=1):
        try:
            result = subprocess.run(
                ["gh", *args],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as error:
            raise GhError("gh is not on PATH") from error
        if result.returncode == 0:
            return result.stdout
        stderr = result.stderr.strip()
        server = _SERVER_ERROR.search(stderr)
        if delay is None or server is None:
            after = f" after {tries} tries" if tries > 1 else ""
            raise GhError(
                f"gh {' '.join(args[:3])} exited {result.returncode}{after}: "
                f"{stderr[:600]}"
            )
        print(
            f"gh {' '.join(args[:3])} answered {server.group(0)} "
            f"(try {tries}); asking again in {delay:g} s",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise AssertionError("unreachable: the last try raises or returns")


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
    #: The job's id, which is its check run's: where its coverage record is.
    job_id: int = 0


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
    """ "hidden", "stopped", "incomplete", "no issues" or "unaddressed"."""
    reason = (summary.hidden_as or "").strip().lower().replace("_", "-")
    if summary.hidden_as is not None and reason in ADDRESSED_REASONS:
        return "hidden"
    if STOPPED.search(summary.body):
        return "stopped"
    if INCOMPLETE.search(summary.body):
        return "incomplete"
    if NO_ISSUES.search(summary.body):
        return "no issues"
    return "unaddressed"


def attempted(attempt: Attempt) -> bool:
    """Whether a run of the review job was a review attempted: still going, or
    ended other than skipped or cancelled.

    The gate reviews a push only while no review has been attempted, and the
    verdict works out from the same rule what a gate that failed would have
    decided (docs/09 P89-11), so both read it here.
    """
    return attempt.status != "completed" or attempt.conclusion not in (
        None,
        "skipped",
        "cancelled",
    )


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
                    job_id=int(job.get("id") or 0),
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
# What the review read (docs/09 P89-9)
#
# The code-review plugin ends a clean review with a fixed sentence, "No issues
# found. Checked for bugs and CLAUDE.md compliance.", whatever ran. Of the 23
# reviews that posted it from 2026-10-11 to 10-18, 20 started no review agent,
# and none had both passes read every changed line. So the sentence is
# replaced with what the session's own record shows. A review that skipped a
# pass does not pass the verdict; one whose passes read part of the diff
# passes with a warning, by the owner's choice on 2026-10-18.

#: The tool that starts a subagent: "Agent" in every session measured (Claude
#: Code 2.1.295 and 2.1.300), and "Task", the older name the workflow's
#: --allowedTools still lists.
AGENT_TOOLS = frozenset({"Agent", "Task"})
#: The passes the plugin's summary claims, by the key the record uses.
PASSES = {"bugs": "Bugs", "claude_md": "CLAUDE.md compliance"}
#: The annotation the review job leaves its record in, and the verdict reads.
COVERAGE_TITLE = "Claude review coverage"
COVERAGE_VERSION = 1
#: How many consecutive lines of the diff, or of a file the diff changes, must
#: reach the model together for a changed line among them to count as read.
#: One line alone would count "+" and "}" read wherever any diff showed them.
WINDOW = 3
#: The line numbers the Read tool and `cat -n` put in front of each line.
_NUMBERED = re.compile(r"^\s*\d+(?:\t|\N{RIGHTWARDS ARROW})")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_CLAUDE_MD = re.compile(r"CLAUDE\.md|AGENTS\.md")
_COMPLIANCE = re.compile(r"complian|adheren|violat|audit", re.IGNORECASE)
_BUGS = re.compile(r"\bbugs?\b|\blogic\b|\bsecurity\b|\bcorrectness\b", re.IGNORECASE)
#: An agent described as one of the plugin's other steps (is the pull request
#: eligible, where are the CLAUDE.md files, what does it change, is this issue
#: real) is not a pass, whatever words its prompt uses.
_NOT_A_PASS = re.compile(
    r"eligib|status|summar|\bfind\b|\blist\b|\blocate\b|validat|verif|confirm",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Diff:
    """A unified diff: its lines, its files' changed lines, and its hunks."""

    #: Every line, trailing whitespace removed.
    lines: tuple[str, ...]
    #: Each file's path and the indexes of the lines it adds or removes.
    files: tuple[tuple[str, tuple[int, ...]], ...]
    #: Each hunk's new text and old text, as indexes into `lines`: its context
    #: and added lines, and its context and removed lines.
    images: tuple[tuple[int, ...], ...]

    def changed(self) -> list[int]:
        return [i for _, lines in self.files for i in lines]


def _diff_path(header: str) -> str:
    rest = header[len("diff --git ") :]
    if rest.startswith("a/") and " b/" in rest:
        return rest.rsplit(" b/", 1)[1]
    return rest


def parse_diff(text: str) -> Diff:
    """Read a unified diff as `git diff` and GitHub's diff media type write it.

    A hunk's lines are counted from its header, so a removed line that begins
    "--" or an added one that begins "++" is a line of the hunk, not a header.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    files: list[tuple[str, list[int]]] = []
    images: list[tuple[int, ...]] = []
    new_text: list[int] = []
    old_text: list[int] = []
    old = new = 0

    def close() -> None:
        for image in (new_text, old_text):
            if image:
                images.append(tuple(image))
            image.clear()

    for i, line in enumerate(lines):
        if old > 0 or new > 0:
            mark = line[:1]
            if mark == "\\":  # "\ No newline at end of file"
                continue
            if mark == "+":
                new -= 1
                new_text.append(i)
                files[-1][1].append(i)
            elif mark == "-":
                old -= 1
                old_text.append(i)
                files[-1][1].append(i)
            else:  # context; an empty one is "" once trailing space is gone
                old -= 1
                new -= 1
                new_text.append(i)
                old_text.append(i)
            if old <= 0 and new <= 0:
                close()
            continue
        if line.startswith("diff --git "):
            close()
            files.append((_diff_path(line), []))
        elif line.startswith("+++ b/") and files:
            files[-1] = (line[len("+++ b/") :], files[-1][1])
        elif (found := _HUNK.match(line)) and files:
            close()
            old = int(found.group(1)) if found.group(1) is not None else 1
            new = int(found.group(2)) if found.group(2) is not None else 1
    close()
    return Diff(
        lines=tuple(lines),
        files=tuple((path, tuple(changed)) for path, changed in files),
        images=tuple(images),
    )


def read_stream(raw: str) -> list[Json]:
    """The action's execution file: a JSON array, or JSON values concatenated.

    The P10-18 guard reads it the same way. Anything left that is not JSON
    raises: a record cut short could hide what an agent read.
    """
    records: list[Any] = []
    index, decoder = 0, json.JSONDecoder()
    while index < len(raw):
        while index < len(raw) and raw[index] in " \t\r\n":
            index += 1
        if index >= len(raw):
            break
        value, index = decoder.raw_decode(raw, index)
        records.append(value)
    if len(records) == 1 and isinstance(records[0], list):
        records = records[0]
    return [r for r in records if isinstance(r, dict)]


def agent_passes(description: str, prompt: str) -> frozenset[str]:
    """The passes an agent was started for, from what it was told.

    Its description is the model's label for it and decides first; the prompt
    decides only an agent whose description names nothing.
    """
    for text, is_label in ((description, True), (prompt, False)):
        found = set()
        if _CLAUDE_MD.search(text) and _COMPLIANCE.search(text):
            found.add("claude_md")
        if _BUGS.search(text):
            found.add("bugs")
        if found:
            return frozenset(found)
        if is_label and _NOT_A_PASS.search(text):
            return frozenset()
    return frozenset()


@dataclass
class Reader:
    """The session, or one agent it started, and what reached its context."""

    key: str
    parent: str | None
    description: str
    model: str
    passes: frozenset[str]
    finished: bool
    #: Each tool result, and the agent's prompt: (text, from the Read tool).
    texts: list[tuple[str, bool]] = field(default_factory=list)


SESSION = "session"


def _blocks(record: Mapping[str, Any]) -> list[Json]:
    content = (record.get("message") or {}).get("content")
    return (
        [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def readers(records: Sequence[Mapping[str, Any]]) -> dict[str, Reader]:
    """The session and each agent it started, with what each one read.

    What a reader read is what reached the model: a tool result's `content`,
    not the `tool_use_result` beside it. When a Bash output is long, Claude
    Code saves it to a file and shows the model 2 KB of it; `content` holds
    those 2 KB, and `tool_use_result.stdout` holds all of it.
    """
    found = {SESSION: Reader(SESSION, None, "the session", "", frozenset(), True)}
    tools: dict[str, str] = {}
    for record in records:
        if record.get("type") != "assistant":
            continue
        owner = str(record.get("parent_tool_use_id") or SESSION)
        for block in _blocks(record):
            if block.get("type") != "tool_use":
                continue
            key, name = str(block.get("id")), str(block.get("name"))
            tools[key] = name
            if name in AGENT_TOOLS:
                given = block.get("input") or {}
                description = str(given.get("description") or "")
                prompt = str(given.get("prompt") or "")
                reader = Reader(
                    key,
                    owner,
                    description,
                    str(given.get("model") or given.get("subagent_type") or ""),
                    agent_passes(description, prompt),
                    False,
                )
                reader.texts.append((prompt, False))
                found[key] = reader
    for record in records:
        if record.get("type") != "user":
            continue
        owner = str(record.get("parent_tool_use_id") or SESSION)
        outcome = record.get("tool_use_result")
        for block in _blocks(record):
            if block.get("type") != "tool_result":
                continue
            key = str(block.get("tool_use_id"))
            name = tools.get(key, "")
            reader = found.setdefault(
                owner,
                Reader(owner, SESSION, "an agent never started", "", frozenset(), True),
            )
            reader.texts.append((_text(block.get("content")), name == "Read"))
            if name in AGENT_TOOLS and key in found:
                status = outcome.get("status") if isinstance(outcome, dict) else None
                found[key].finished = not block.get("is_error") and status in (
                    None,
                    "completed",
                )
    return found


Windows = tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]


def windows(texts: Sequence[tuple[str, bool]]) -> Windows:
    """Every run of one, two and three consecutive lines in *texts*.

    A Read result's line numbers are removed; so are `cat -n`'s, from a text
    whose lines mostly carry them.
    """
    ones: set[str] = set()
    twos: set[tuple[str, str]] = set()
    threes: set[tuple[str, str, str]] = set()
    for text, from_read in texts:
        raw = text.split("\n")
        numbered = sum(1 for line in raw if _NUMBERED.match(line))
        variants = []
        if not from_read:
            variants.append([line.rstrip() for line in raw])
        if from_read or (raw and numbered * 2 >= len(raw)):
            variants.append([_NUMBERED.sub("", line, count=1).rstrip() for line in raw])
        for lines in variants:
            ones.update(lines)
            twos.update(itertools.pairwise(lines))
            threes.update(zip(lines, lines[1:], lines[2:], strict=False))
    return ones, twos, threes


def _substantive(line: str) -> bool:
    """Whether a line says something: "+    )" and "+" alone do not."""
    return sum(1 for ch in line if ch.isalnum()) >= 2


def _anchored(matched: Sequence[tuple[int, int, bool]]) -> set[int]:
    """Positions covered by matched runs that touch a substantive one.

    *matched* holds (start, length, substantive) for each run that reached the
    model. Overlapping and adjacent runs join; a joined stretch counts only if
    one of its runs holds a line that says something. Three lines that say
    nothing ("+    )", "+", "+") match in any Python diff, so on their own
    they prove nothing about which file was read; inside a stretch the model
    demonstrably read, they were read with it.
    """
    covered: dict[int, bool] = {}
    for start, length, strong in matched:
        for position in range(start, start + length):
            covered[position] = covered.get(position, False) or strong
    out: set[int] = set()
    stretch: list[int] = []
    anchored = False
    for position in sorted(covered):
        if stretch and position != stretch[-1] + 1:
            if anchored:
                out.update(stretch)
            stretch, anchored = [], False
        stretch.append(position)
        anchored = anchored or covered[position]
    if anchored:
        out.update(stretch)
    return out


def lines_read(diff: Diff, seen: Windows) -> set[int]:
    """The changed lines of *diff* that reached the model in a run of WINDOW.

    A run counts as the diff's own text (`gh pr diff`, `git diff`, a file the
    diff was saved to), or as a file's text around the line (a file read at the
    reviewed head shows its added lines, one read at the base its removed
    ones). A hunk shorter than WINDOW on its side is matched whole. Runs of
    lines that say nothing count only beside a run that does (`_anchored`).
    """
    ones, twos, threes = seen
    lines, count = diff.lines, len(diff.lines)
    changed = set(diff.changed())
    matched = [
        (start, WINDOW, any(_substantive(line) for line in run))
        for start in range(count - WINDOW + 1)
        if (run := lines[start : start + WINDOW]) in threes
    ]
    read = _anchored(matched) & changed
    for image in diff.images:
        text = [lines[k][1:] for k in image]
        size = min(WINDOW, len(text))
        runs = []
        for start in range(len(text) - size + 1):
            run = tuple(text[start : start + size])
            if (
                (size == 3 and run in threes)
                or (size == 2 and run in twos)
                or (size == 1 and run[0] in ones)
            ):
                runs.append((start, size, any(_substantive(line) for line in run)))
        read |= {image[p] for p in _anchored(runs)} & changed
    return read


def _lineage(found: Mapping[str, Reader], key: str) -> list[str]:
    """*key* and every finished agent started under it, at any depth."""
    out, queue = [key], [key]
    while queue:
        parent = queue.pop()
        for child in found.values():
            if child.parent == parent and child.key != parent and child.finished:
                out.append(child.key)
                queue.append(child.key)
    return out


def _coverage_of(diff: Diff, read: set[int]) -> Json:
    unread = [
        [path, sum(1 for i in changed if i in read), len(changed)]
        for path, changed in diff.files
        if changed and not all(i in read for i in changed)
    ]
    return {
        "lines": len(read.intersection(diff.changed())),
        "files_read": sum(1 for _, changed in diff.files if changed) - len(unread),
        "unread": unread,
    }


def measure(records: Sequence[Mapping[str, Any]], diff: Diff) -> Json:
    """What the session read of *diff*, as a whole and pass by pass."""
    found = readers(records)
    read = {key: lines_read(diff, windows(r.texts)) for key, r in found.items()}
    everything: set[int] = set().union(*read.values())
    passes: Json = {}
    for key in PASSES:
        started = [r for r in found.values() if key in r.passes]
        covered: set[int] = set()
        for reader in started:
            if reader.finished:
                for member in _lineage(found, reader.key):
                    covered |= read[member]
        passes[key] = {
            "agents": sum(1 for r in started if r.finished),
            "unfinished": sum(1 for r in started if not r.finished),
            "names": [r.description for r in started if r.finished],
            **_coverage_of(diff, covered),
        }
    return {
        "version": COVERAGE_VERSION,
        "changed_lines": len(diff.changed()),
        "files": sum(1 for _, changed in diff.files if changed),
        "unchanged_files": sum(1 for _, changed in diff.files if not changed),
        "session": _coverage_of(diff, everything),
        "passes": passes,
        "readers": [
            {
                "description": r.description,
                "model": r.model,
                "passes": sorted(r.passes),
                "finished": r.finished,
                "lines": len(read[r.key]),
            }
            for r in found.values()
        ],
    }


def compact(record: Mapping[str, Any]) -> Json:
    """The record without its lists: what the annotation carries."""

    def counts(part: Mapping[str, Any]) -> Json:
        return {
            k: v
            for k, v in part.items()
            if k in ("agents", "unfinished", "lines", "files_read")
        }

    return {
        **{
            k: record[k]
            for k in ("version", "head", "stopped", "changed_lines", "files")
            if k in record
        },
        "session": counts(record["session"]),
        "passes": {key: counts(record["passes"][key]) for key in PASSES},
    }


def coverage_gaps(record: Mapping[str, Any]) -> list[str]:
    """Why the record cannot vouch that both passes ran; empty when they did.

    This is what fails the verdict: a pass that no agent finished, or a
    record this version cannot read, which is a gap, not a pass. How much of
    the diff a pass read is `coverage_shortfalls`, and only reported.
    """
    try:
        if record.get("version") != COVERAGE_VERSION:
            return [
                f"its coverage record is version {record.get('version')!r}, "
                f"not {COVERAGE_VERSION}"
            ]
        int(record["changed_lines"])
        gaps = []
        for key, name in PASSES.items():
            part = record["passes"][key]
            int(part["lines"])
            if int(part["agents"]) < 1:
                gaps.append(f"{name}: not checked; no agent finished that pass")
        return gaps
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        return [f"its coverage record is unreadable ({error!r})"]


def coverage_shortfalls(record: Mapping[str, Any]) -> list[str]:
    """Each pass that ran and read fewer than all the changed lines.

    Reported in the summary and as a warning on the verdict, never failed: the
    owner chose on 2026-10-18 to block on a skipped pass and to show partial
    reading (docs/09 P89-9). The counts stay in the record, so the rule can be
    tightened later without measuring anything again.
    """
    if record.get("version") != COVERAGE_VERSION:
        return []  # coverage_gaps reports it
    try:
        total = int(record["changed_lines"])
        return [
            f"{name}: {int(part['lines'])} of {total} changed lines read"
            for key, name in PASSES.items()
            if int((part := record["passes"][key])["agents"]) >= 1
            and int(part["lines"]) < total
        ]
    except (KeyError, TypeError, ValueError, AttributeError):
        return []  # coverage_gaps reports the unreadable record


def coverage_state(record: Mapping[str, Any]) -> str:
    """ "incomplete" (a pass skipped), "partial" (lines unread) or "complete"."""
    if coverage_gaps(record):
        return "incomplete"
    return "partial" if coverage_shortfalls(record) else "complete"


def _files_phrase(part: Mapping[str, Any], files: int) -> str:
    return f"{part['files_read']} of {files} file(s) in full"


def _unread_phrase(part: Mapping[str, Any], limit: int = 12) -> str:
    unread = part.get("unread") or []
    shown = ", ".join(
        f"`{path}` ({read} of {total})" for path, read, total in unread[:limit]
    )
    more = f", and {len(unread) - limit} more" if len(unread) > limit else ""
    return f" Not read in full: {shown}{more}." if unread else ""


def summary_body(record: Mapping[str, Any], *, run_url: str, rerun: str = "") -> str:
    """The summary comment, rewritten to say what the review measurably did.

    It says "No issues found" only when both passes ran, and then says how
    much of the diff they read; over a pass that did not run it never does.
    """
    total, files = record["changed_lines"], record["files"]
    state = coverage_state(record)
    lines = ["## Code review", ""]
    if state == "incomplete":
        lines += [
            "**Claude review incomplete:** Claude reported no issues, but this "
            "review skipped a check it reports, so that is not a clean result.",
            "",
        ]
    elif state == "partial":
        lines += [
            "No issues found in what was read. Both checks ran, but they did not "
            "read every changed line of the diff; a line no agent read was not "
            "checked.",
            "",
        ]
    else:
        lines += ["No issues found.", ""]
    lines += [
        "What ran, as the session's own record shows (counted by the workflow, "
        "not reported by Claude):",
        "",
    ]
    for key, name in PASSES.items():
        part = record["passes"][key]
        if part["agents"] < 1:
            lines.append(
                f"- **{name}:** not checked: no agent for this pass finished"
                + (
                    f" ({part['unfinished']} started and did not)."
                    if part.get("unfinished")
                    else "."
                )
            )
            continue
        names = "; ".join(part.get("names") or [])
        lines.append(
            f"- **{name}:** {part['agents']} agent(s)"
            + (f" ({names})" if names else "")
            + f", which between them read {part['lines']} of the {total} changed "
            f"lines, {_files_phrase(part, files)}." + _unread_phrase(part)
        )
    session = record["session"]
    lines.append(
        f"- **The session as a whole** read {session['lines']} of the {total} "
        f"changed lines, {_files_phrase(session, files)}." + _unread_phrase(session)
    )
    if record.get("unchanged_files"):
        lines.append(
            f"- {record['unchanged_files']} file(s) change no line of text "
            "(binary, renamed or mode only) and are not counted."
        )
    lines += [
        "",
        f"A changed line counts as read when it reached a model inside a run of "
        f"{WINDOW} consecutive lines of the diff, or of the file around it. "
        f"Measured against the diff at {str(record.get('head') or '?')[:12]}, in "
        f"[the review's run]({run_url}).",
    ]
    if state == "incomplete":
        lines += [
            "",
            "`claude review verdict` fails until a review runs both checks. "
            "Re-running the failed review job reviews the pull request again; "
            "on a re-run, Claude's earlier comments do not stop it."
            + (f" To re-run it: `{rerun}`" if rerun else ""),
        ]
    elif state == "partial":
        lines += [
            "",
            "`claude review verdict` passes this with a warning naming the lines "
            "each check did not read.",
        ]
    return "\n".join(lines) + "\n"


def coverage_markdown(record: Mapping[str, Any]) -> str:
    """The run summary: the record, and every reader the session had."""
    out = [
        f"## Claude review coverage: {coverage_state(record)}",
        "",
        "| Reader | Model | Passes | Finished | Changed lines read |",
        "|---|---|---|---|---|",
    ]
    for r in record.get("readers") or []:
        out.append(
            f"| {r['description'] or '?'} | {r['model'] or '-'} | "
            f"{', '.join(r['passes']) or '-'} | {'yes' if r['finished'] else 'no'} | "
            f"{r['lines']} of {record['changed_lines']} |"
        )
    out.append("")
    out += [f"- {gap} (fails the verdict)" for gap in coverage_gaps(record)]
    out += [f"- {short} (a warning)" for short in coverage_shortfalls(record)]
    if out[-1] == "":
        out.append("- Both passes ran and read every changed line.")
    return "\n".join(out) + "\n"


def read_coverage(repo: str, attempt: Attempt) -> tuple[Json | None, str]:
    """The coverage record a review job left, or None and why there is none."""
    if not attempt.job_id:
        return None, "its job id is unknown, so its record cannot be read"
    found = gh_json(
        "api", f"repos/{repo}/check-runs/{attempt.job_id}/annotations?per_page=100"
    )
    if not isinstance(found, list):
        raise GhError(f"the annotations of job {attempt.job_id} are not a list")
    records = [
        a for a in found if isinstance(a, dict) and a.get("title") == COVERAGE_TITLE
    ]
    if not records:
        return None, "it left no coverage record"
    if len(records) > 1:
        return None, f"it left {len(records)} coverage records, not one"
    try:
        record = json.loads(str(records[0].get("message") or ""))
    except json.JSONDecodeError as error:
        return None, f"its coverage record is not JSON ({error})"
    if not isinstance(record, dict):
        return None, "its coverage record is not an object"
    return record, "read"


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


@dataclass(frozen=True)
class Covered:
    """The review whose coverage record decides, and that record.

    `attempt` is None when no review that read the pull request finished;
    `record` is None when that review left no record, and `note` says why.
    """

    attempt: Attempt | None
    record: Json | None
    note: str = ""


def find_coverage(
    repo: str, attempts: Sequence[Attempt], state: str, attempt: Attempt | None
) -> Covered:
    """The coverage record that decides the verdict.

    A failed review is its own. Otherwise the latest review that finished
    decides, passing over one that stopped at the plugin's first step on
    purpose (closed, draft, already reviewed): it read nothing by design, and
    an earlier review may have read it all.
    """
    if state == "failed" and attempt is not None:
        record, why = read_coverage(repo, attempt)
        return Covered(attempt, record, why)
    if state != "reviewed":
        return Covered(None, None, f"the review state is {state}")
    stopped: list[str] = []
    for each in sorted(attempts, key=lambda a: (a.started_at, a.run_id), reverse=True):
        if each.status != "completed" or each.conclusion in (
            None,
            "skipped",
            "cancelled",
        ):
            continue
        record, why = read_coverage(repo, each)
        if record is not None and record.get("stopped") is True:
            stopped.append(each.url)
            continue
        if each.conclusion != "success":
            return Covered(each, None, f"it ended {each.conclusion}; its log says why")
        passed_over = (
            f"; {len(stopped)} later review(s) stopped at the plugin's first step"
            if stopped
            else ""
        )
        return Covered(each, record, why + passed_over)
    if stopped:
        return Covered(
            None,
            None,
            f"every review on it stopped at the plugin's first step ({', '.join(stopped)})",
        )
    return Covered(None, None, "no review on it finished")


def _coverage_problem(covered: Covered) -> str | None:
    """Why the review that decides cannot pass, or None.

    A skipped pass and a missing record fail; a pass that read part of the
    diff does not (`coverage_shortfalls`, reported as a warning).
    """
    if covered.attempt is None:
        return f"No review read this pull request: {covered.note}."
    where = f"{covered.attempt.url} on {covered.attempt.head_sha[:12]}"
    if covered.record is None:
        return (
            f"The review ({where}) left no record of what it read: {covered.note}. "
            "Whether it ran its checks over the diff is unknown, and unknown is "
            "not covered."
        )
    gaps = coverage_gaps(covered.record)
    if gaps:
        return f"The review ({where}) skipped a check it reports: {'; '.join(gaps)}."
    return None


def if_rerun_fails(number: object) -> str:
    """What to do when a re-run will not start (docs/09 P89-11).

    A re-run can end in startup_failure with no jobs, over and over, and the
    attempt it starts replaces the run's checks: the pull request is left with
    no review check at all, which blocks it as surely as a red one. A fresh
    event starts a new run instead.
    """
    return (
        "If that re-run ends in startup_failure with no jobs, it leaves the pull "
        "request with no review checks at all (docs/09 P89-11): mark it draft and "
        f"then ready for review instead (`gh pr ready {number} --undo`, then "
        f"`gh pr ready {number}`), which starts a fresh run."
    )


def _rereview(attempt: Attempt | None) -> str:
    if attempt is None:
        return (
            "A review that reads it is needed: its next push starts one while no "
            "review has been attempted, and marking it draft and then ready for "
            "review starts one in any case."
        )
    command = (
        f"gh run rerun {attempt.run_id} --job {attempt.job_id}"
        if attempt.job_id
        else f"gh run rerun {attempt.run_id}"
    )
    return (
        f"Review it again: `{command}` re-runs that review and its verdict. On a "
        "re-run, Claude's earlier comments do not stop the review."
    )


#: What `needs.gate.result` can read. Anything else, an empty value included,
#: means the gate's result did not reach the verdict.
GATE_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


@dataclass(frozen=True)
class Gate:
    """This run's gate: how it ended, and what that cost (docs/09 P89-11).

    `result` is `needs.gate.result` as the workflow passed it, None when it
    passed nothing. `lost` says why a review this run should have started did
    not run, or that nobody can tell; `note`, why a gate that did not succeed
    cost no review. A gate that succeeded has neither.
    """

    result: str | None
    event: str | None = None
    lost: str = ""
    note: str = ""


def gate_outcome(
    result: str | None,
    event: str | None,
    run_id: int,
    attempts: Sequence[Attempt],
) -> Gate:
    """What this run's gate ending as *result* cost the pull request.

    A gate that did not succeed decided nothing, and the review job after it
    was skipped whatever the event called for. The review state cannot show
    that: an earlier review still decides it, so a pull request marked ready
    for review to be reviewed again, whose gate then failed, passed on the
    review it asked to replace. So the gate's own rule is applied here to the
    runs the verdict read: a push is reviewed only while no review has been
    attempted, and a review event always is, so what makes up for its loss is
    a review attempted by a later run.
    """
    if result not in GATE_RESULTS:
        given = "no gate result" if result is None else f"the gate result {result!r}"
        return Gate(
            result,
            event,
            lost=(
                f"The workflow passed this verdict {given}, so whether this run "
                "should have reviewed the pull request is unknown, and a review "
                "nobody knows was due is not one that ran."
            ),
        )
    if result == "success":
        return Gate(result, event)
    ended = f"This run's gate ended {result}, so the review job after it was skipped"
    others = sorted(
        (a for a in attempts if a.run_id != run_id and attempted(a)),
        key=lambda a: (a.started_at, a.run_id),
        reverse=True,
    )
    if event == "synchronize":
        if others:
            return Gate(
                result,
                event,
                note=(
                    f"{ended}. That cost no review: a push after a review was "
                    f"attempted ({others[0].url}) is not reviewed again."
                ),
            )
        return Gate(
            result,
            event,
            lost=(
                f"{ended}, and this push was the pull request's first chance at "
                "one: no review has been attempted on it."
            ),
        )
    if event is not None and event in REVIEW_EVENTS:
        named = event.replace("_", " ")
        later = [a for a in others if a.run_id > run_id]
        if later:
            return Gate(
                result,
                event,
                note=(
                    f"{ended}. A later run attempted the review this {named} "
                    f"event called for ({later[0].url})."
                ),
            )
        earlier = (
            f" The latest review attempted ({others[0].url}) came before this event."
            if others
            else " No review has been attempted on it."
        )
        return Gate(
            result,
            event,
            lost=(
                f"{ended}, and a pull request {named} is always reviewed: no run "
                f"since this one has attempted a review.{earlier}"
            ),
        )
    return Gate(
        result,
        event,
        lost=(
            f"{ended}, on a {event or 'missing'} event, which is not one the gate "
            "decides, so whether this run should have reviewed is unknown."
        ),
    )


def decide(
    pr: Mapping[str, Any],
    state: str,
    attempt: Attempt | None,
    feedback: Feedback,
    *,
    self_edit: bool,
    since: int,
    coverage: Covered,
    gate: Gate,
) -> Verdict:
    """The verdict on *pr*'s head, from what was read about it. Pure.

    A gate that did not succeed is reported on every verdict: as the reason
    when it cost a review, as a warning when it cost none.
    """
    verdict = _decide(
        pr,
        state,
        attempt,
        feedback,
        self_edit=self_edit,
        since=since,
        coverage=coverage,
        gate=gate,
    )
    if gate.note:
        verdict.warnings.insert(0, gate.note)
    return verdict


def _decide(
    pr: Mapping[str, Any],
    state: str,
    attempt: Attempt | None,
    feedback: Feedback,
    *,
    self_edit: bool,
    since: int,
    coverage: Covered,
    gate: Gate,
) -> Verdict:
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
    # Before the review state, which an earlier review decides: the review this
    # run should have started may be the one that would have found something.
    if gate.lost:
        return Verdict(
            False,
            gate.lost,
            remedy=(
                "Re-running this run's failed jobs runs the gate again, then the "
                "review it decides and this verdict after them."
                if gate.result in GATE_RESULTS
                else f"The verdict step in {WORKFLOW} passes `needs.gate.result` "
                "as --gate-result; a run whose workflow does not is judged by a "
                "fresh event, not by a re-run of the same file."
            ),
        )
    where = f"{attempt.url} on {attempt.head_sha[:12]}" if attempt else ""
    if state == "running":
        return Verdict(
            False,
            f"A review is still running ({where}).",
            remedy="When it ends, its own verdict re-runs this one.",
        )
    if state == "failed":
        gaps = (
            coverage_gaps(coverage.record)
            if coverage.record is not None and coverage.attempt == attempt
            else []
        )
        why = (
            f"it skipped a check it reports: {'; '.join(gaps)}."
            if gaps
            else "it did not show that it read the diff."
        )
        return Verdict(
            False,
            f"The latest review failed ({where}, {attempt.conclusion if attempt else '?'}): "
            + why,
            remedy=(
                "Read that job's log. Re-running its failed jobs reviews again, and "
                "on a re-run Claude's earlier comments do not stop the review; a "
                "run the log calls stale needs a fresh event instead (mark the "
                "pull request draft, then ready for review)."
            ),
        )
    commented = bool(feedback.threads or feedback.summaries)
    head = str((pr.get("head") or {}).get("sha") or "")
    if state == "reviewed" and commented:
        problem = _coverage_problem(coverage)
        if problem:
            return Verdict(
                False, problem, remedy=_rereview(coverage.attempt or attempt)
            )
        reviewed = coverage.attempt or attempt
        scope = (
            "this head"
            if reviewed is None or reviewed.head_sha == head
            else f"{reviewed.head_sha[:12]}; the "
            f"{since if since >= 0 else 'unknown number of'} commit(s) since were "
            "not reviewed"
        )
        record = coverage.record or {}
        short = coverage_shortfalls(record)
        if short:
            return Verdict(
                True,
                f"Reviewed {scope}. Its bug and CLAUDE.md compliance agents ran, "
                "but did not read every changed line. No finding is left "
                "unaddressed.",
                warnings=[
                    f"{line}; a line no agent read was not checked." for line in short
                ],
            )
        return Verdict(
            True,
            f"Reviewed {scope}. Its bug and CLAUDE.md compliance agents read all "
            f"{record.get('changed_lines', '?')} changed lines. No finding is left "
            "unaddressed.",
        )
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


def judge(
    repo: str,
    pr: Mapping[str, Any],
    *,
    gate_result: str | None,
    event: str | None,
    run_id: int,
) -> tuple[Verdict, list[str]]:
    """Read everything the verdict depends on, then decide. Returns what was seen.

    *gate_result* and *event* are this run's `needs.gate.result` and
    `github.event.action`, None when the workflow passed none.
    """
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
    gate = gate_outcome(gate_result, event, run_id, attempts)
    seen.append(
        f"this run's gate: {gate_result!r}, event {event!r}"
        + (f": {gate.lost or gate.note}" if gate.lost or gate.note else "")
    )
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
    coverage = find_coverage(repo, attempts, state, attempt)
    seen.append(
        f"coverage from {coverage.attempt.url if coverage.attempt else 'no review'}: "
        + (
            json.dumps(coverage.record, sort_keys=True)
            if coverage.record is not None
            else f"none ({coverage.note})"
        )
    )
    reviewed = coverage.attempt or attempt
    since = 0
    if (
        state == "reviewed"
        and reviewed is not None
        and reviewed.head_sha not in ("", head)
    ):
        # Only the count in the summary depends on this, not the verdict, so a
        # head that a force-push made unreachable costs the number, not the check.
        try:
            since = commits_since(repo, reviewed.head_sha, head)
        except GhError as error:
            seen.append(f"commits since the reviewed head: unknown ({error})")
            since = -1
    verdict = decide(
        pr,
        state,
        attempt,
        feedback,
        self_edit=self_edit,
        since=since,
        coverage=coverage,
        gate=gate,
    )
    return verdict, seen


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
        (a for a in attempts() if attempted(a)),
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
        verdict, seen = judge(
            args.repo,
            pr,
            gate_result=args.gate_result,
            event=args.event,
            run_id=args.run_id,
        )
    except (GhError, KeyError, TypeError, ValueError) as error:
        verdict = Verdict(
            False,
            f"The verdict could not read what it judges: {error}",
            remedy="An answer this check could not read is not a clean one.",
        )
    seen.append(f"this run's review job: {args.review_result!r}")
    for line in seen:
        print(line)
    for warning in verdict.warnings:
        print(f"::warning::{warning}")
    for finding in verdict.findings:
        print(f"::error::{finding}")
    print(("" if verdict.passed else "::error::") + verdict.reason)
    if not verdict.passed:
        verdict.remedy = (
            f"{verdict.remedy} To re-judge at once, re-run this run's failed "
            f"jobs: gh run rerun {args.run_id} --failed. {if_rerun_fails(args.pr)}"
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


def _listing(path: str) -> list[Json]:
    """Every item of a paged REST list, page by page."""
    out: list[Json] = []
    for page in range(1, 31):
        found = gh_json(
            "api", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}"
        )
        if not isinstance(found, list):
            raise GhError(f"{path} is not a list: {found!r:.300}")
        out += [item for item in found if isinstance(item, dict)]
        if len(found) < 100:
            return out
    raise GhError(f"{path} lists more items than this reads")


def runs_comments(repo: str, number: int, since: str) -> tuple[list[Json], list[Json]]:
    """The reviewer's issue comments and inline comments created since *since*."""

    def mine(item: Mapping[str, Any]) -> bool:
        return (item.get("user") or {}).get("login") == REVIEWER_LOGIN and str(
            item.get("created_at") or ""
        ) >= since

    issue = [c for c in _listing(f"repos/{repo}/issues/{number}/comments") if mine(c)]
    inline = [c for c in _listing(f"repos/{repo}/pulls/{number}/comments") if mine(c)]
    return issue, inline


def pull_diff(repo: str, number: int) -> tuple[str, str]:
    """The pull request's diff as `gh pr diff` reads it, and the head it is of.

    Read when the review has just ended, because that is the diff the reviewer
    was given: #208's review sat queued for an hour while the pull request
    moved, and its session read the later head, not the event's.
    """
    for _ in range(3):
        before = str(
            (gh_json("api", f"repos/{repo}/pulls/{number}").get("head") or {}).get(
                "sha"
            )
            or ""
        )
        text = gh(
            "api",
            "-H",
            "Accept: application/vnd.github.diff",
            f"repos/{repo}/pulls/{number}",
        )
        after = str(
            (gh_json("api", f"repos/{repo}/pulls/{number}").get("head") or {}).get(
                "sha"
            )
            or ""
        )
        if before and before == after:
            return text, before
    raise GhError(f"#{number}'s head kept moving while its diff was read")


def _annotation(text: str) -> str:
    """A workflow command's message, escaped as the runner unescapes it."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def cmd_coverage(args: argparse.Namespace) -> int:
    """Measure what the review read; record it on the job; fail an incomplete one."""
    print(
        f"execution_file={args.execution_file or 'none'} self_modified={args.self_modified} "
        f"event head={args.head[:12] or 'none'} since={args.since}"
    )
    if not args.execution_file:
        if args.self_modified == "true":
            print(
                "No session to measure: the action refuses to run a workflow file "
                "that differs from the default branch's. The verdict passes this "
                "pull request unreviewed, with a warning."
            )
            return 0
        print(
            "::error::The review step left no execution file, so what it read cannot be measured."
        )
        return 1
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", args.since or ""):
        print(
            f"::error::--since is {args.since!r}, so this run's comments cannot be told from older ones."
        )
        return 1
    try:
        with open(args.execution_file, encoding="utf-8") as handle:
            records = read_stream(handle.read())
        diff_text, measured_head = pull_diff(args.repo, args.pr)
        issue, inline = runs_comments(args.repo, args.pr, args.since)
    except (OSError, ValueError, GhError) as error:
        print(f"::error::Could not measure what the review read: {error}")
        return 1
    diff = parse_diff(diff_text)
    record = measure(records, diff)
    stops = [c for c in issue if STOPPED.search(str(c.get("body") or ""))]
    summaries = [
        c
        for c in issue
        if NO_ISSUES.search(str(c.get("body") or ""))
        and not STOPPED.search(str(c.get("body") or ""))
    ]
    record["head"] = measured_head
    record["stopped"] = bool(stops) and len(stops) == len(issue) and not inline
    if args.head and measured_head != args.head:
        print(
            f"::warning::The pull request moved from {args.head[:12]} to "
            f"{measured_head[:12]} after this run's event; coverage is measured "
            "against the diff at the later head, which is the one `gh pr diff` "
            "gives once the review has ended."
        )
    print(
        f"records={len(records)} diff at {measured_head[:12]}: {len(diff.lines)} lines, "
        f"{record['changed_lines']} changed, in {record['files']} file(s); this run's "
        f"comments: issue={len(issue)} (summaries={len(summaries)}, stops={len(stops)}) "
        f"inline={len(inline)}"
    )
    for reader in record["readers"]:
        print(
            f"reader: {reader['description'] or '?'} model={reader['model'] or '-'} "
            f"passes={','.join(reader['passes']) or '-'} finished={reader['finished']} "
            f"read={reader['lines']}/{record['changed_lines']}"
        )
    for key, name in PASSES.items():
        part = record["passes"][key]
        print(
            f"pass {name}: agents={part['agents']} unfinished={part['unfinished']} "
            f"read={part['lines']}/{record['changed_lines']} files={part['files_read']}/{record['files']}"
        )
    _append("GITHUB_STEP_SUMMARY", coverage_markdown(record))
    print(
        f"::notice title={COVERAGE_TITLE}::{_annotation(json.dumps(compact(record)))}"
    )
    full = {k: v for k, v in record.items() if k != "readers"}
    _append("GITHUB_OUTPUT", f"coverage={json.dumps(full)}\n")
    _append("GITHUB_OUTPUT", f"summaries={','.join(str(c['id']) for c in summaries)}\n")
    gaps, short = coverage_gaps(record), coverage_shortfalls(record)
    if record["stopped"]:
        print(
            "The review stopped at the plugin's first step and says why; it read "
            "nothing by design. The verdict looks for an earlier review that did."
        )
        print(f"coverage=stopped gaps={len(gaps)} shortfalls={len(short)}")
        return 0
    # A skipped pass fails the review; a pass that read part of the diff is
    # reported and does not (the owner's rule, docs/09 P89-9).
    for gap in gaps:
        print(f"::error::{gap}")
    for line in short:
        print(f"::warning::{line}; a line no agent read was not checked.")
    print(f"coverage={coverage_state(record)} gaps={len(gaps)} shortfalls={len(short)}")
    return 1 if gaps else 0


def cmd_rewrite(args: argparse.Namespace) -> int:
    """Replace each "No issues found" summary of this run with what was measured."""
    try:
        record = json.loads(os.environ.get("COVERAGE") or "")
    except json.JSONDecodeError as error:
        print(f"::error::COVERAGE is not the coverage step's record: {error}")
        return 1
    if not isinstance(record, dict) or "passes" not in record:
        print(f"::error::COVERAGE has no passes: {str(record)[:300]}")
        return 1
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{args.repo}"
        f"/actions/runs/{args.run_id}"
    )
    body = summary_body(
        record, run_url=run_url, rerun=f"gh run rerun {args.run_id} --failed"
    )
    failed = 0
    for raw in args.comments.split(","):
        if not raw.strip():
            continue
        path = f"repos/{args.repo}/issues/comments/{int(raw)}"
        try:
            before = gh_json("api", path)
            author = (before.get("user") or {}).get("login")
            old = str(before.get("body") or "")
            if (
                author != REVIEWER_LOGIN
                or not NO_ISSUES.search(old)
                or STOPPED.search(old)
            ):
                print(
                    f"::error::{before.get('html_url')} is by {author} and is not a "
                    f"'No issues found' summary; it is left alone. It reads: {old[:200]!r}"
                )
                failed += 1
                continue
            gh("api", "-X", "PATCH", path, "-f", f"body={body}")
            after = str(gh_json("api", path).get("body") or "")
        except GhError as error:
            print(f"::error::could not rewrite comment {raw}: {error}")
            failed += 1
            continue
        if after.replace("\r\n", "\n").strip() != body.strip():
            print(
                f"::error::{before.get('html_url')} reads back other than written: {after[:300]!r}"
            )
            failed += 1
            continue
        print(
            f"rewrote {before.get('html_url')}; it said: {' '.join(old.split())[:300]}"
        )
        print(f"it now begins: {' '.join(after.split())[:300]}")
    return 1 if failed else 0


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
    judge_cmd.add_argument("--review-result")
    # No default that reads as an answer: a gate result or an event the
    # workflow did not pass is None, and the verdict fails on it (P89-11).
    judge_cmd.add_argument("--gate-result", help="this run's needs.gate.result")
    judge_cmd.add_argument("--event", help="this run's github.event.action")
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
    coverage = sub.add_parser(
        "coverage", help="measure what the review read, in the review job"
    )
    coverage.add_argument("--repo", required=True)
    coverage.add_argument("--pr", type=int, required=True)
    coverage.add_argument("--head", default="", help="the event's head, to compare")
    coverage.add_argument("--execution-file", default="")
    coverage.add_argument("--self-modified", default="")
    coverage.add_argument("--since", default="", help="when the review step began")
    rewrite = sub.add_parser(
        "rewrite", help="rewrite this run's summaries with the measured coverage"
    )
    rewrite.add_argument("--repo", required=True)
    rewrite.add_argument(
        "--comments", required=True, help="comma-separated comment ids"
    )
    rewrite.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "gate":
        return cmd_gate(args)
    if args.command == "judge":
        return cmd_judge(args)
    if args.command == "coverage":
        return cmd_coverage(args)
    if args.command == "rewrite":
        return cmd_rewrite(args)
    if args.after_comment and not args.pr:
        parser.error("--after-comment needs --pr")
    return cmd_rejudge(args)


if __name__ == "__main__":
    sys.exit(main())
