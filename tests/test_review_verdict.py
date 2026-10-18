"""The Claude review's verdict: what holds a merge, what releases it, and when.

tools/ci/review_verdict.py is the rule and `.github/workflows/claude-review.yml`
runs it as the required check `claude review verdict`. These tests hold the
rule to what docs/09 P89 claims for it: an unaddressed finding or a review
that never finished fails, a resolved or empty review passes, nothing the
check could not read passes, and the workflow reports a verdict on every head
without ever skipping it, since GitHub counts a skipped job as a passed check.

Since P89-9 a review also has to have covered the pull request: a bug agent
and a CLAUDE.md compliance agent that finished and between them read every
changed line, as the session's own record shows. The measurement is tested
here on small synthetic sessions in the record's real shape;
tests/test_review_coverage_replay_private.py replays real ones.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from tools.ci import review_verdict as rv

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
REPO = "owner/repo"
HEAD = "a" * 40
OLD = "b" * 40


def verdict_name(expression: str) -> str:
    """The verdict job's name on a pull request that is not a draft.

    On a draft the job takes another name, so the head carries no required
    check at all; the name after `||` is the one a ruleset requires.
    """
    found = re.fullmatch(
        r"\$\{\{ github\.event\.pull_request\.draft && '([^']+)' \|\| '([^']+)' \}\}",
        expression,
    )
    assert found, expression
    draft, ready = found.groups()
    # A required check matches its name exactly, so the draft's name may
    # extend the real one; it must not equal it.
    assert draft != ready
    return ready


def _workflow(name: str) -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(
        (WORKFLOWS / name).read_text(encoding="utf-8")
    )
    return loaded


# --------------------------------------------------------------------------
# The workflow


def test_every_head_gets_a_verdict_that_is_never_skipped() -> None:
    workflow = _workflow("claude-review.yml")
    # PyYAML reads the key `on` as True.
    assert set(workflow[True]["pull_request"]["types"]) == {
        *rv.REVIEW_EVENTS,
        "synchronize",
    }
    # A workflow-wide group would let a push cancel the review still running.
    assert "concurrency" not in workflow
    verdict = workflow["jobs"]["verdict"]
    assert verdict_name(verdict["name"]) == rv.VERDICT_JOB
    # After the job that rewrites the review's summary, so the first verdict
    # reads the comment as it stays; it never needs that job to succeed.
    assert verdict["needs"] == ["gate", "review", "summary"]
    # Nothing but always(): a skipped verdict is a green one to GitHub.
    assert verdict["if"] == "always()"
    run = verdict["steps"][-1]
    assert '"$RUNNER_TEMP/review_verdict.py" judge' in run["run"]
    assert run["env"]["RUN_HEAD"] == "${{ github.event.pull_request.head.sha }}"
    assert verdict["permissions"] == {
        "actions": "write",
        "checks": "read",
        "contents": "read",
        "issues": "read",
        "pull-requests": "read",
    }


def test_a_draft_gets_no_verdict_rather_than_a_green_or_red_one() -> None:
    verdict = _workflow("claude-review.yml")["jobs"]["verdict"]
    name = verdict["name"]
    draft = re.findall(r"'([^']+)'", name)[0]
    # Not the required name, and not a name a ruleset could match by accident.
    assert draft != rv.VERDICT_JOB
    steps = {step["name"]: step for step in verdict["steps"] if "name" in step}
    assert steps["A draft is not judged"]["if"] == "github.event.pull_request.draft"
    judge = steps["No unaddressed review finding on this head"]
    assert judge["if"] == "${{ !github.event.pull_request.draft }}"


def test_the_review_runs_once_and_a_push_never_cancels_it() -> None:
    jobs = _workflow("claude-review.yml")["jobs"]
    review = jobs["review"]
    assert "name" not in review  # the check is named by its key
    assert rv.REVIEW_JOB == "review"
    assert review["needs"] == "gate"
    assert review["if"] == "needs.gate.outputs.review == 'true'"
    assert review["concurrency"]["cancel-in-progress"] == (
        "${{ github.event.action != 'synchronize' }}"
    )
    gate = jobs["gate"]
    assert '"$RUNNER_TEMP/review_verdict.py" gate' in gate["steps"][-1]["run"]
    env = gate["steps"][-1]["env"]
    # By name, not `head.repo.fork`: a deleted fork's head repository is null.
    assert env["HEAD_REPO"] == "${{ github.event.pull_request.head.repo.full_name }}"
    assert env["HAS_TOKEN"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN != '' }}"
    # The branch name is its author's text; it reaches the script as data.
    assert "head.ref" not in gate["steps"][-1]["run"]


def test_the_reviewers_stop_notice_is_the_one_the_verdict_reads() -> None:
    review = _workflow("claude-review.yml")["jobs"]["review"]
    action = next(s for s in review["steps"] if s.get("id") == "review")
    args = action["with"]["claude_args"]
    assert "'Claude review stopped:'" in args
    assert rv.STOPPED.search("Claude review stopped: it is closed.")


def test_the_verdict_jobs_check_nothing_out_and_read_their_script() -> None:
    """A runner that reuses its workspace hands the next job what this one
    checked out: a sparse checkout of the script left the next jobs on the
    same commit with no pyproject.toml (docs/09 P89)."""
    jobs = {
        "gate": _workflow("claude-review.yml")["jobs"]["gate"],
        "summary": _workflow("claude-review.yml")["jobs"]["summary"],
        "verdict": _workflow("claude-review.yml")["jobs"]["verdict"],
        "rejudge": _workflow("claude-review-rejudge.yml")["jobs"]["rejudge"],
    }
    for name, job in jobs.items():
        uses = [step.get("uses", "") for step in job["steps"]]
        assert not any(u.startswith("actions/checkout@") for u in uses), name
        read = next(
            s
            for s in job["steps"]
            if s.get("name") == "Read the verdict script from the commit under test"
        )
        assert '> "$RUNNER_TEMP/review_verdict.py"' in read["run"], name
        assert read["env"]["REF"] == "${{ github.sha }}", name
        assert '"$RUNNER_TEMP/review_verdict.py"' in job["steps"][-1]["run"], name


def test_re_judging_re_runs_a_verdict_and_never_adds_one() -> None:
    workflow = _workflow("claude-review-rejudge.yml")
    assert workflow[True]["pull_request_review_comment"]["types"] == ["created"]
    job = workflow["jobs"]["rejudge"]
    assert job["name"] != rv.VERDICT_JOB
    assert '"$RUNNER_TEMP/review_verdict.py" rejudge' in job["steps"][-1]["run"]
    assert "claude[bot]" in job["if"]


# --------------------------------------------------------------------------
# The rule


def summary(body: str, hidden_as: str | None = None) -> rv.Summary:
    return rv.Summary(kind="comment", url="u/summary", body=body, hidden_as=hidden_as)


def thread(*, resolved: bool, outdated: bool = False, by: bool = True) -> rv.Thread:
    return rv.Thread(
        url="u/thread",
        path="src/x.py",
        resolved=resolved,
        outdated=outdated,
        by_reviewer=by,
        comment_ids=(1, 2),
    )


@pytest.mark.parametrize(
    ("body", "hidden_as", "expected"),
    [
        (
            "## Code review\n\nNo issues found. Checked for bugs and CLAUDE.md compliance.",
            None,
            "no issues",
        ),
        ("## Code review\n\nClaude review stopped: it is a draft.", None, "stopped"),
        ("## Code review\n\nFound 2 issues:\n\n1. a bug", None, "unaddressed"),
        # A finding that anchors to no changed line comes as a summary.
        ("## Code review\n\nOne more finding that doesn't anchor", None, "unaddressed"),
        # A stop notice in any other words is not recognised as one.
        (
            "## Code review\n\nReview skipped: an automated pull request",
            None,
            "unaddressed",
        ),
        ("Found 1 issue", "resolved", "hidden"),
        ("Found 1 issue", "OUTDATED", "hidden"),
        ("Found 1 issue", "off-topic", "unaddressed"),
        ("No issues found.", "spam", "no issues"),
        # The rewrite of a clean summary whose review skipped a check:
        # not a finding to answer; the verdict fails on the review's record.
        (
            "## Code review\n\n**Claude review incomplete:** Claude reported no issues",
            None,
            "incomplete",
        ),
    ],
)
def test_a_summary_is_clean_hidden_or_a_finding(
    body: str, hidden_as: str | None, expected: str
) -> None:
    assert rv.classify(summary(body, hidden_as)) == expected


def attempt(
    run_id: int, status: str, conclusion: str | None, at: str, sha: str = HEAD
) -> rv.Attempt:
    return rv.Attempt(
        run_id=run_id,
        url=f"u/run/{run_id}",
        head_sha=sha,
        started_at=at,
        status=status,
        conclusion=conclusion,
        job_id=run_id * 10,
    )


def record(
    *, bugs: int = 2, claude_md: int = 2, read: int = 10, total: int = 10, **extra: Any
) -> dict[str, Any]:
    """A coverage record as the review job leaves it (compact)."""

    def part(agents: int) -> dict[str, Any]:
        return {"agents": agents, "unfinished": 0, "lines": read, "files_read": 2}

    found = {
        "version": rv.COVERAGE_VERSION,
        "head": HEAD,
        "stopped": False,
        "changed_lines": total,
        "files": 2,
        "session": {"lines": read, "files_read": 2},
        "passes": {"bugs": part(bugs), "claude_md": part(claude_md)},
    }
    found.update(extra)
    return found


def test_a_running_review_decides_first_then_the_latest_that_ended() -> None:
    done = attempt(1, "completed", "success", "2026-10-17T10:00:00Z")
    failed = attempt(2, "completed", "failure", "2026-10-17T11:00:00Z")
    cancelled = attempt(3, "completed", "cancelled", "2026-10-17T12:00:00Z")
    skipped = attempt(4, "completed", "skipped", "2026-10-17T13:00:00Z")
    running = attempt(5, "in_progress", None, "2026-10-17T09:00:00Z")
    assert rv.review_state([]) == ("none", None)
    assert rv.review_state([cancelled, skipped]) == ("none", None)
    assert rv.review_state([done, cancelled, skipped]) == ("reviewed", done)
    assert rv.review_state([done, failed, cancelled]) == ("failed", failed)
    assert rv.review_state([done, failed, running]) == ("running", running)


def pr(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "number": 7,
        "state": "open",
        "draft": False,
        "user": {"login": "someone", "type": "User"},
        "head": {"sha": HEAD, "ref": "fix/x", "repo": {"full_name": REPO}},
        "base": {"ref": "master"},
        "created_at": "2026-10-17T09:00:00Z",
    }
    base.update(overrides)
    return base


def decide(
    state: str = "reviewed",
    *,
    threads: list[rv.Thread] | None = None,
    summaries: list[rv.Summary] | None = None,
    self_edit: bool = False,
    reviewed: str = HEAD,
    since: int = 0,
    coverage: rv.Covered | None = None,
    **pr_overrides: Any,
) -> rv.Verdict:
    """The rule, with a review that covered the diff unless *coverage* says not."""
    who = None if state == "none" else attempt(9, "completed", "success", "t", reviewed)
    if coverage is None:
        coverage = rv.Covered(who, record() if state == "reviewed" else None, "read")
    return rv.decide(
        pr(**pr_overrides),
        state,
        who,
        rv.Feedback(threads=threads or [], summaries=summaries or []),
        self_edit=self_edit,
        since=since,
        coverage=coverage,
    )


def test_an_unresolved_thread_fails_and_a_resolved_one_passes() -> None:
    failed = decide(threads=[thread(resolved=False)])
    assert not failed.passed and failed.findings == [
        "unresolved thread on src/x.py: u/thread"
    ]
    assert "resolve the thread" in failed.remedy
    # Outdated is not addressed: the line moved, the finding may not have.
    assert not decide(threads=[thread(resolved=False, outdated=True)]).passed
    assert decide(threads=[thread(resolved=True)]).passed


def test_a_summary_that_reports_something_fails_until_it_is_hidden() -> None:
    assert not decide(summaries=[summary("Found 1 issue")]).passed
    assert decide(summaries=[summary("Found 1 issue", "resolved")]).passed
    assert decide(summaries=[summary("No issues found.")]).passed
    assert decide(summaries=[summary("Claude review stopped: already reviewed")]).passed


def test_findings_hold_the_merge_whatever_the_review_state() -> None:
    for state in ("running", "failed", "none"):
        verdict = decide(state, threads=[thread(resolved=False)])
        assert not verdict.passed and verdict.findings


def test_a_review_that_has_not_finished_or_failed_does_not_pass() -> None:
    running = decide("running")
    assert not running.passed and "still running" in running.reason
    failed = decide("failed", summaries=[summary("No issues found.")])
    assert not failed.passed and "failed" in failed.reason
    none = decide("none")
    assert not none.passed and "next push starts one" in none.remedy
    draft = decide("none", draft=True)
    assert not draft.passed and "draft" in draft.reason
    assert "do not do that just to clear this check" in draft.remedy


def test_a_review_that_left_nothing_passes_only_when_it_could_not_run() -> None:
    edits = decide(self_edit=True)
    assert edits.passed and edits.warnings
    assert decide("none", self_edit=True).passed
    # The guard fails a review that posted nothing; one that passed anyway
    # is an anomaly, not a clean review.
    silent = decide()
    assert not silent.passed and "left nothing" in silent.reason


def test_the_verdict_says_how_much_went_unreviewed() -> None:
    same = decide(summaries=[summary("No issues found.")])
    assert "Reviewed this head" in same.reason
    later = decide(summaries=[summary("No issues found.")], reviewed=OLD, since=3)
    assert later.passed and "3 commit(s) since were not reviewed" in later.reason
    unknown = decide(summaries=[summary("No issues found.")], reviewed=OLD, since=-1)
    assert "unknown number of commit(s)" in unknown.reason


@pytest.mark.parametrize(
    ("overrides", "word"),
    [
        ({"user": {"login": "dependabot[bot]", "type": "Bot"}}, "bot"),
        (
            {"head": {"sha": HEAD, "ref": "x", "repo": {"full_name": "fork/repo"}}},
            "fork/repo",
        ),
        ({"head": {"sha": HEAD, "ref": "x", "repo": None}}, "no longer exists"),
        (
            {"head": {"sha": HEAD, "ref": "sync/from-x", "repo": {"full_name": REPO}}},
            "sync",
        ),
    ],
)
def test_the_exemptions_say_why(overrides: dict[str, Any], word: str) -> None:
    reason = rv.exemption(pr(**overrides), REPO)
    assert reason and word in reason
    assert rv.exemption(pr(), REPO) is None
    # sync-repair branches carry an unattended session's code: reviewed.
    repair = pr(head={"sha": HEAD, "ref": "sync-repair/1", "repo": {"full_name": REPO}})
    assert rv.exemption(repair, REPO) is None


def gate_env(**overrides: str) -> dict[str, str]:
    env = {
        "AUTHOR": "someone",
        "AUTHOR_TYPE": "User",
        "DRAFT": "false",
        "HEAD_REPO": REPO,
        "HEAD_REF": "fix/x",
        "REPO": REPO,
        "ACTION": "opened",
        "HAS_TOKEN": "true",
    }
    env.update(overrides)
    return env


def never() -> list[rv.Attempt]:
    raise AssertionError("the runs are read only for a push")


@pytest.mark.parametrize(
    ("overrides", "level", "word"),
    [
        ({"AUTHOR_TYPE": "Bot"}, "notice", "bot"),
        ({"DRAFT": "true"}, "notice", "draft"),
        ({"DRAFT": ""}, "warning", "no draft state"),
        ({"HEAD_REPO": "fork/repo"}, "notice", "fork/repo"),
        ({"HEAD_REPO": ""}, "notice", "no longer exists"),
        ({"HEAD_REF": "sync/from-x"}, "notice", "sync"),
        ({"HAS_TOKEN": "false"}, "warning", "CLAUDE_CODE_OAUTH_TOKEN"),
        ({"ACTION": "edited"}, "notice", "edited"),
    ],
)
def test_the_gate_skips_and_says_why(
    overrides: dict[str, str], level: str, word: str
) -> None:
    review, got_level, reason = rv.gate_decision(gate_env(**overrides), never)
    assert (review, got_level) == (False, level) and word in reason


def test_the_gate_reviews_a_push_only_while_no_review_has_been_attempted() -> None:
    for action in rv.REVIEW_EVENTS:
        assert rv.gate_decision(gate_env(ACTION=action), never)[0]
    push = gate_env(ACTION="synchronize")
    assert rv.gate_decision(push, list)[0]
    superseded = [
        attempt(1, "completed", "cancelled", "t1"),
        attempt(2, "completed", "skipped", "t2"),
    ]
    assert rv.gate_decision(push, lambda: superseded)[0]
    for tried in (
        attempt(3, "completed", "failure", "t3"),
        attempt(4, "in_progress", None, "t4"),
        attempt(5, "completed", "success", "t5"),
    ):
        review, _, reason = rv.gate_decision(push, lambda t=tried: [*superseded, t])
        assert not review and tried.url in reason


# --------------------------------------------------------------------------
# Reading GitHub, and re-running verdicts


class FakeGh:
    """Answers the calls the script makes, from a small model of one PR."""

    def __init__(self, **state: Any) -> None:
        self.pr = state.get("pr", pr())
        self.runs: list[dict[str, Any]] = state.get("runs", [])
        self.jobs: dict[int, list[dict[str, Any]]] = state.get("jobs", {})
        self.threads: list[list[dict[str, Any]]] = state.get("threads", [[]])
        self.comments: list[dict[str, Any]] = state.get("comments", [])
        self.files: list[str] = state.get("files", ["src/x.py"])
        self.checks: dict[str, list[dict[str, Any]]] = state.get("checks", {})
        #: A review job's annotations, by job id: where its coverage record is.
        self.annotations: dict[int, list[dict[str, Any]]] = state.get("annotations", {})
        self.diff: str = state.get("diff", "")
        #: REST issue comments and inline comments, as the coverage step lists them.
        self.issue_comments: list[dict[str, Any]] = state.get("issue_comments", [])
        self.inline_comments: list[dict[str, Any]] = state.get("inline_comments", [])
        self.fail: str | None = state.get("fail")
        self.reruns: list[str] = []
        self.patches: list[tuple[str, str]] = []

    def __call__(self, *args: str) -> str:
        if self.fail and any(self.fail in a for a in args):
            raise rv.GhError(f"HTTP 502 for {self.fail}")
        if args[:3] == ("api", "-H", "Accept: application/vnd.github.diff"):
            assert args[3] == f"repos/{REPO}/pulls/7", args
            return self.diff
        if args[:3] == ("api", "-X", "PATCH"):
            comment_id = int(args[3].rsplit("/", 1)[1])
            body = args[5].removeprefix("body=")
            self.patches.append((args[3], body))
            for c in self.issue_comments:
                if c["id"] == comment_id:
                    c["body"] = body
            return "{}"
        if args[:2] == ("api", "graphql"):
            query = args[3]
            if "reviewThreads" in query:
                nodes = (
                    self.threads.pop(0) if len(self.threads) > 1 else self.threads[0]
                )
                key = "reviewThreads"
            elif "reviews(first" in query:
                nodes, key = [], "reviews"
            else:
                nodes, key = self.comments, "comments"
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                key: {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": nodes,
                                }
                            }
                        }
                    }
                }
            )
        if args[:3] == ("api", "-X", "POST"):
            self.reruns.append(args[3])
            return ""
        path = args[1]
        if path == f"repos/{REPO}/pulls/7":
            return json.dumps(self.pr)
        if "/annotations?" in path:
            job = int(path.split("/check-runs/")[1].split("/")[0])
            return json.dumps(self.annotations.get(job, []))
        if path.startswith(f"repos/{REPO}/issues/7/comments?"):
            return json.dumps(self.issue_comments if "page=1" in path else [])
        if path.startswith(f"repos/{REPO}/pulls/7/comments?"):
            return json.dumps(self.inline_comments if "page=1" in path else [])
        if path.startswith(f"repos/{REPO}/issues/comments/"):
            comment_id = int(path.rsplit("/", 1)[1])
            return json.dumps(
                next(c for c in self.issue_comments if c["id"] == comment_id)
            )
        if "/actions/workflows/claude-review.yml/runs?" in path:
            return json.dumps({"workflow_runs": self.runs})
        if "/jobs?filter=latest" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return json.dumps({"jobs": self.jobs.get(run_id, [])})
        if path.startswith(f"repos/{REPO}/pulls/7/files"):
            return json.dumps([{"filename": f} for f in self.files])
        if "/compare/" in path:
            return json.dumps({"ahead_by": 2})
        if "/check-runs?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return json.dumps({"check_runs": self.checks.get(sha, [])})
        if path.endswith("/pulls") and "/commits/" in path:
            return json.dumps([{"number": 7, "state": "open", "head": self.pr["head"]}])
        raise AssertionError(f"unexpected gh call: {args}")


def review_run(
    run_id: int, conclusion: str | None, *, sha: str = HEAD
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status = "completed" if conclusion else "in_progress"
    run = {
        "id": run_id,
        "head_sha": sha,
        "head_branch": "fix/x",
        "created_at": f"2026-10-17T1{run_id}:00:00Z",
        # GitHub fills this when it is read; it can be empty for a run that
        # was the pull request's (docs/09 P89), and the old head's is here.
        "pull_requests": [{"number": 7}] if sha == HEAD else [],
    }
    jobs = [
        {
            "id": run_id * 10,
            "name": "review",
            "status": status,
            "conclusion": conclusion,
            "started_at": f"2026-10-17T1{run_id}:00:01Z",
            "html_url": f"u/run/{run_id}",
        }
    ]
    return run, jobs


def coverage_annotation(found: dict[str, Any]) -> dict[str, Any]:
    """The coverage step's notice as the annotations API returns it."""
    return {
        "path": ".github",
        "annotation_level": "notice",
        "title": rv.COVERAGE_TITLE,
        "message": json.dumps(found),
    }


def claude_thread(resolved: bool, ids: tuple[int, ...] = (11,)) -> dict[str, Any]:
    return {
        "isResolved": resolved,
        "isOutdated": False,
        "path": "src/x.py",
        "comments": {
            "nodes": [
                {
                    "databaseId": i,
                    "url": f"u/c/{i}",
                    "author": (
                        {"login": "claude", "__typename": "Bot"}
                        if n == 0
                        else {"login": "someone", "__typename": "User"}
                    ),
                }
                for n, i in enumerate(ids)
            ]
        },
    }


def verdict_check(run_id: int, conclusion: str, job: int) -> dict[str, Any]:
    return {
        "id": job,
        "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/{job}",
    }


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> FakeGh:
    monkeypatch.setattr(rv, "gh", fake)
    return fake


def judge(*extra: str) -> int:
    return rv.main(
        [
            "judge",
            "--repo",
            REPO,
            "--pr",
            "7",
            "--run-id",
            "1",
            "--run-head",
            HEAD,
            *extra,
        ]
    )


def test_an_unresolved_thread_fails_the_check_and_re_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, jobs = review_run(1, "success")
    fake = use(
        monkeypatch,
        FakeGh(
            runs=[run],
            jobs={1: jobs},
            threads=[[claude_thread(False)]],
            checks={HEAD: [verdict_check(5, "failure", 55)]},
        ),
    )
    assert judge() == 1
    out = capsys.readouterr().out
    assert "verdict=fail findings=1" in out
    assert "thread UNRESOLVED: src/x.py u/c/11" in out
    assert "gh run rerun 1 --failed" in out
    assert fake.reruns == []


def test_a_pass_re_runs_the_stale_failed_verdicts_on_its_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, jobs = review_run(1, "success")
    fake = use(
        monkeypatch,
        FakeGh(
            runs=[run],
            jobs={1: jobs},
            threads=[[claude_thread(True)]],
            annotations={10: [coverage_annotation(record())]},
            checks={
                HEAD: [
                    verdict_check(1, "failure", 10),  # this run's own, earlier attempt
                    verdict_check(5, "failure", 55),
                    verdict_check(6, "success", 66),
                    verdict_check(7, "cancelled", 77),
                ]
            },
        ),
    )
    assert judge() == 0
    assert "verdict=pass findings=0" in capsys.readouterr().out
    assert fake.reruns == [
        f"repos/{REPO}/actions/jobs/55/rerun",
        f"repos/{REPO}/actions/jobs/77/rerun",
    ]


def test_a_verdict_on_an_old_head_re_runs_the_new_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, jobs = review_run(1, "success", sha=OLD)
    fake = use(
        monkeypatch,
        FakeGh(
            runs=[run],
            jobs={1: jobs},
            threads=[[claude_thread(False)]],
            checks={HEAD: [verdict_check(5, "failure", 55)]},
        ),
    )
    assert (
        rv.main(
            ["judge", "--repo", REPO, "--pr", "7", "--run-id", "1", "--run-head", OLD]
        )
        == 1
    )
    assert fake.reruns == [f"repos/{REPO}/actions/jobs/55/rerun"]


def test_a_read_that_fails_fails_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, jobs = review_run(1, "success")
    use(monkeypatch, FakeGh(runs=[run], jobs={1: jobs}, fail="graphql"))
    assert judge() == 1
    out = capsys.readouterr().out
    assert "could not read what it judges" in out and "verdict=fail" in out


def test_a_run_is_the_pull_requests_by_branch_and_time_not_by_its_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mine, mine_jobs = review_run(1, "success", sha=OLD)  # lists no pull request
    other, other_jobs = review_run(2, "failure")
    other["pull_requests"] = [{"number": 8}]
    before, before_jobs = review_run(3, "failure")
    before["created_at"] = "2026-10-16T00:00:00Z"  # an earlier pull request's
    elsewhere, elsewhere_jobs = review_run(4, "failure")
    elsewhere["head_branch"] = "fix/y"
    use(
        monkeypatch,
        FakeGh(
            runs=[mine, other, before, elsewhere],
            jobs={1: mine_jobs, 2: other_jobs, 3: before_jobs, 4: elsewhere_jobs},
        ),
    )
    found = rv.review_attempts(REPO, 7, "fix/x", "2026-10-17T09:00:00Z")
    assert [a.run_id for a in found] == [1]
    with pytest.raises(rv.GhError):
        rv.review_attempts(REPO, 7, "fix/x", "")


def test_no_review_run_at_all_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, FakeGh())
    assert judge() == 1
    assert "No review has run on this pull request" in capsys.readouterr().out


def test_an_exempt_pull_request_passes_without_reading_its_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bot = pr(user={"login": "dependabot[bot]", "type": "Bot"})
    use(monkeypatch, FakeGh(pr=bot, fail="claude-review.yml/runs"))
    assert judge() == 0
    assert "Exempt: it was opened by a bot" in capsys.readouterr().out


def test_a_reply_re_judges_once_its_thread_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(rv.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    fake = use(
        monkeypatch,
        FakeGh(
            threads=[
                [],  # the reply has not reached the thread list yet
                [claude_thread(False, (11, 12))],
                [claude_thread(True, (11, 12))],
            ],
            checks={HEAD: [verdict_check(5, "failure", 55)]},
        ),
    )
    args = ["rejudge", "--repo", REPO, "--pr", "7", "--after-comment", "12"]
    assert rv.main(args) == 0
    assert fake.reruns == [f"repos/{REPO}/actions/jobs/55/rerun"]
    assert now[0] == 2 * rv.POLL_SECONDS


def test_a_reply_left_unresolved_or_outside_claudes_threads_re_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = [0.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(rv.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    checks = {HEAD: [verdict_check(5, "failure", 55)]}
    args = ["rejudge", "--repo", REPO, "--pr", "7", "--after-comment", "12"]
    fake = use(
        monkeypatch, FakeGh(threads=[[claude_thread(False, (11, 12))]], checks=checks)
    )
    assert rv.main([*args, "--wait", "60"]) == 0
    assert (
        fake.reruns == [] and "still unresolved after 60 s" in capsys.readouterr().out
    )
    theirs = claude_thread(True, (11, 12))
    theirs["comments"]["nodes"][0]["author"] = {
        "login": "someone",
        "__typename": "User",
    }
    fake = use(monkeypatch, FakeGh(threads=[[theirs]], checks=checks))
    assert rv.main(args) == 0
    assert (
        fake.reruns == [] and "not one of Claude's threads" in capsys.readouterr().out
    )


def test_a_finished_ci_run_re_judges_the_pull_requests_with_that_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use(monkeypatch, FakeGh(checks={HEAD: [verdict_check(5, "failure", 55)]}))
    assert rv.main(["rejudge", "--repo", REPO, "--sha", HEAD]) == 0
    assert fake.reruns == [f"repos/{REPO}/actions/jobs/55/rerun"]
    fake = use(monkeypatch, FakeGh(checks={HEAD: [verdict_check(5, "failure", 55)]}))
    assert rv.main(["rejudge", "--repo", REPO, "--sha", OLD]) == 0
    assert fake.reruns == []


def test_a_rejudge_that_cannot_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeGh(fail="check-runs"))
    assert rv.main(["rejudge", "--repo", REPO, "--pr", "7"]) == 1


# --------------------------------------------------------------------------
# What the review read (docs/09 P89-9)

#: Two files: a change with a removed and an added run, and a new test file.
SAMPLE_DIFF = """\
diff --git a/src/x.py b/src/x.py
index 1111111..2222222 100644
--- a/src/x.py
+++ b/src/x.py
@@ -1,6 +1,7 @@
 def keep():
     return 1
-def old():
-    return 2
+def new():
+    return 3
+
 def tail():
     return 4
diff --git a/tests/test_x.py b/tests/test_x.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_x.py
@@ -0,0 +1,3 @@
+from x import new
+def test_new():
+    assert new() == 3
"""
#: The file at the reviewed head, as the Read tool shows it.
SAMPLE_X_AT_HEAD = (
    "1\tdef keep():\n2\t    return 1\n3\tdef new():\n4\t    return 3\n5\t\n"
    "6\tdef tail():\n7\t    return 4\n"
)


class Session:
    """Builds an execution stream in the shape claude-code-action writes it."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.count = 0

    def _id(self) -> str:
        self.count += 1
        return f"toolu_{self.count:04d}"

    def tool(
        self,
        name: str,
        result: str,
        *,
        owner: str | None = None,
        stdout: str | None = None,
        **given: Any,
    ) -> str:
        key = self._id()
        use = {"type": "tool_use", "id": key, "name": name, "input": given}
        self.records.append(
            {
                "type": "assistant",
                "message": {"content": [use]},
                "parent_tool_use_id": owner,
            }
        )
        done: dict[str, Any] = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": key, "content": result}
                ]
            },
            "parent_tool_use_id": owner,
        }
        if stdout is not None:
            done["tool_use_result"] = {"stdout": stdout, "stderr": ""}
        self.records.append(done)
        return key

    def agent(
        self,
        description: str,
        prompt: str = "",
        *,
        owner: str | None = None,
        finished: bool = True,
    ) -> str:
        key = self._id()
        use = {
            "type": "tool_use",
            "id": key,
            "name": "Agent",
            "input": {"description": description, "prompt": prompt, "model": "opus"},
        }
        self.records.append(
            {
                "type": "assistant",
                "message": {"content": [use]},
                "parent_tool_use_id": owner,
            }
        )
        if finished:
            self.records.append(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": key,
                                "content": [{"type": "text", "text": "No issues."}],
                            }
                        ]
                    },
                    "parent_tool_use_id": owner,
                    "tool_use_result": {"status": "completed"},
                }
            )
        return key

    def measure(self, diff: str = SAMPLE_DIFF) -> dict[str, Any]:
        return rv.measure(self.records, rv.parse_diff(diff))


def numbered(text: str) -> str:
    return "\n".join(f"{n}\t{line}" for n, line in enumerate(text.splitlines(), 1))


def test_a_diff_is_read_by_its_hunk_counts() -> None:
    diff = rv.parse_diff(SAMPLE_DIFF)
    assert [(path, len(lines)) for path, lines in diff.files] == [
        ("src/x.py", 5),
        ("tests/test_x.py", 3),
    ]
    # A removed line that begins "--" and an added one that begins "++" are
    # lines of the hunk, not the next file's header.
    tricky = (
        "diff --git a/a.sql b/a.sql\n--- a/a.sql\n+++ b/a.sql\n@@ -1,2 +1,2 @@\n"
        " select 1;\n--- a comment\n+++ a counter\n\\ No newline at end of file\n"
    )
    assert rv.parse_diff(tricky).files == (("a.sql", (5, 6)),)
    renamed = (
        "diff --git a/old.png b/new.png\nsimilarity index 100%\nrename from old.png\n"
    )
    assert rv.parse_diff(renamed).files == (("new.png", ()),)


@pytest.mark.parametrize(
    ("description", "prompt", "passes"),
    [
        # Descriptions the reviewer gave its agents on 2026-10-17 and 18.
        ("CLAUDE.md compliance A", "", {"claude_md"}),
        ("CLAUDE.md compliance review", "", {"claude_md"}),
        ("Opus bug scan diff-only", "", {"bugs"}),
        ("Opus bug scan logic/security", "", {"bugs"}),
        ("Bug review of PR 208", "", {"bugs"}),
        ("Check PR status", "Report: is it closed? draft? bugs?", set()),
        ("Check PR eligibility", "", set()),
        ("Find CLAUDE.md files", "list CLAUDE.md files for compliance", set()),
        ("Summarize PR changes", "do not look for bugs", set()),
        ("Validate issue 1", "Is this bug real?", set()),
        # An unhelpful label falls back to the prompt.
        ("Agent 3", "Scan for obvious bugs in the diff.", {"bugs"}),
        ("Agent 1", "Audit changes for CLAUDE.md compliance.", {"claude_md"}),
        ("Review", "Check for bugs and CLAUDE.md compliance.", {"bugs", "claude_md"}),
    ],
)
def test_an_agent_counts_for_the_pass_it_was_started_for(
    description: str, prompt: str, passes: set[str]
) -> None:
    assert rv.agent_passes(description, prompt) == passes


def test_agents_that_read_the_whole_diff_cover_it() -> None:
    s = Session()
    # The way real agents read a long diff: the output is saved to a file and
    # 2 KB of it shown, then the file is read with the Read tool.
    for name in ("CLAUDE.md compliance A", "Opus bug scan"):
        key = s.agent(name)
        preview = "<persisted-output>\nPreview (first 2KB):\n" + SAMPLE_DIFF[:120]
        s.tool("Bash", preview, owner=key, stdout=SAMPLE_DIFF, command="gh pr diff 7")
        s.tool("Read", numbered(SAMPLE_DIFF), owner=key, file_path="/tmp/saved.txt")
    found = s.measure()
    assert found["changed_lines"] == 8 and found["files"] == 2
    assert rv.coverage_gaps(found) == []
    for key in rv.PASSES:
        assert found["passes"][key]["agents"] == 1
        assert found["passes"][key]["lines"] == 8


def test_a_session_that_read_it_alone_checked_neither_pass() -> None:
    """#273's shape: the session read the whole diff and started no agent."""
    s = Session()
    s.tool("Bash", SAMPLE_DIFF, command="gh pr diff 7")
    found = s.measure()
    assert found["session"]["lines"] == 8
    assert rv.coverage_gaps(found) == [
        "Bugs: not checked; no agent finished that pass",
        "CLAUDE.md compliance: not checked; no agent finished that pass",
    ]


def test_part_of_the_diff_is_part_covered_and_says_which() -> None:
    """#275's shape, small: `gh pr diff | head` and nothing more."""
    s = Session()
    head = "\n".join(SAMPLE_DIFF.splitlines()[:13])  # through src/x.py's hunk
    for name in ("CLAUDE.md compliance A", "Opus bug scan"):
        key = s.agent(name)
        s.tool("Bash", head, owner=key, command="gh pr diff 7 | head -13")
    found = s.measure()
    assert found["passes"]["bugs"]["lines"] == 5
    assert found["passes"]["bugs"]["unread"] == [["tests/test_x.py", 0, 3]]
    # Both passes ran, so nothing fails; the shortfall is reported.
    assert rv.coverage_gaps(found) == []
    assert rv.coverage_state(found) == "partial"
    assert rv.coverage_shortfalls(found) == [
        "Bugs: 5 of 8 changed lines read",
        "CLAUDE.md compliance: 5 of 8 changed lines read",
    ]


def test_what_counts_is_what_reached_the_model_not_what_the_tool_returned() -> None:
    """A long output is saved to a file and the model shown 2 KB of it. The
    record keeps the whole stdout beside that preview; counting it would call
    #208's diff read where its session was shown 33 lines of it."""
    s = Session()
    for name in ("CLAUDE.md compliance A", "Opus bug scan"):
        key = s.agent(name)
        preview = "<persisted-output>\nPreview (first 2KB):\n" + SAMPLE_DIFF[:300]
        s.tool("Bash", preview, owner=key, stdout=SAMPLE_DIFF, command="gh pr diff 7")
    found = s.measure()
    assert found["passes"]["bugs"]["lines"] < 8
    assert rv.coverage_state(found) == "partial"


def test_an_agent_that_did_not_finish_covers_nothing() -> None:
    s = Session()
    done = s.agent("CLAUDE.md compliance A")
    s.tool("Bash", SAMPLE_DIFF, owner=done, command="gh pr diff 7")
    lost = s.agent("Opus bug scan", finished=False)
    s.tool("Bash", SAMPLE_DIFF, owner=lost, command="gh pr diff 7")
    found = s.measure()
    assert found["passes"]["bugs"]["agents"] == 0
    assert found["passes"]["bugs"]["unfinished"] == 1
    assert rv.coverage_gaps(found) == ["Bugs: not checked; no agent finished that pass"]
    body = rv.summary_body({**found, "head": HEAD}, run_url="u/run")
    assert "(1 started and did not)" in body


def test_an_agents_own_agents_and_its_prompt_count_for_its_pass() -> None:
    s = Session()
    compliance = s.agent("CLAUDE.md compliance A")
    helper = s.agent("Read the test file", owner=compliance)
    lines = SAMPLE_DIFF.splitlines()
    s.tool("Bash", "\n".join(lines[13:]), owner=helper, command="x")
    s.tool("Bash", "\n".join(lines[:13]), owner=compliance, command="y")
    # A diff handed over in the prompt reached the agent as surely as a tool's.
    s.agent("Opus bug scan", prompt="Here is the diff:\n" + SAMPLE_DIFF)
    assert rv.coverage_gaps(s.measure()) == []


def test_a_file_read_at_the_head_shows_its_added_lines_not_its_removed_ones() -> None:
    s = Session()
    key = s.agent("Opus bug scan")
    s.tool("Read", SAMPLE_X_AT_HEAD, owner=key, file_path="/w/src/x.py")
    part = s.measure()["passes"]["bugs"]
    # def new(), return 3 and the blank line; not def old() or return 2.
    assert part["lines"] == 3
    assert part["unread"] == [["src/x.py", 3, 5], ["tests/test_x.py", 0, 3]]


def test_lines_that_say_nothing_are_not_read_on_their_own() -> None:
    """Three lines like "+    )", "+", "+" match in any Python diff. #277's
    session was credited three lines of a file it never opened that way."""
    diff = (
        "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1,1 +1,4 @@\n"
        " call(\n+    )\n+\n+\n"
    )
    s = Session()
    key = s.agent("Opus bug scan")
    s.tool("Bash", "other.py's diff\n+    )\n+\n+\nmore", owner=key, command="x")
    assert s.measure(diff)["passes"]["bugs"]["lines"] == 0
    # Inside a stretch that says something, they were read with it.
    s.tool("Bash", diff, owner=key, command="gh pr diff 7")
    assert s.measure(diff)["passes"]["bugs"]["lines"] == 3


def test_the_stream_is_read_in_every_shape_the_action_writes() -> None:
    one = {"type": "result", "num_turns": 1}
    assert rv.read_stream(json.dumps([one, one])) == [one, one]
    pretty = json.dumps(one, indent=2) + "\n" + json.dumps(one)
    assert rv.read_stream(pretty) == [one, one]
    with pytest.raises(ValueError):
        rv.read_stream(json.dumps(one) + '\n{"type": "assist')


@pytest.mark.parametrize(
    ("found", "gap"),
    [
        (record(version=0), "version 0"),
        ({"version": rv.COVERAGE_VERSION}, "unreadable"),
        (record(bugs=0), "Bugs: not checked"),
        (record(claude_md=0), "CLAUDE.md compliance: not checked"),
    ],
)
def test_a_record_that_cannot_vouch_for_both_passes_is_a_gap(
    found: dict[str, Any], gap: str
) -> None:
    gaps = rv.coverage_gaps(found)
    assert gaps and gap in " ".join(gaps)
    assert rv.coverage_state(found) == "incomplete"


def test_a_pass_that_read_part_of_the_diff_is_a_shortfall_not_a_gap() -> None:
    """The owner's rule (2026-10-18): block on a skipped check, report partial
    reading. The counts stay in the record for a stricter rule later."""
    assert rv.coverage_gaps(record(read=9)) == []
    assert rv.coverage_shortfalls(record(read=9)) == [
        "Bugs: 9 of 10 changed lines read",
        "CLAUDE.md compliance: 9 of 10 changed lines read",
    ]
    assert rv.coverage_state(record(read=9)) == "partial"
    assert rv.coverage_shortfalls(record()) == []
    assert rv.coverage_state(record()) == "complete"
    # A skipped pass has no lines to fall short of; the other pass's still count.
    assert rv.coverage_shortfalls(record(bugs=0, read=4)) == [
        "CLAUDE.md compliance: 4 of 10 changed lines read"
    ]
    assert rv.coverage_shortfalls(record(version=0, read=4)) == []


def test_the_rewritten_summary_says_no_issues_only_over_checks_that_ran() -> None:
    clean = rv.summary_body(record(), run_url="u/run")
    assert rv.classify(summary(clean)) == "no issues"
    assert clean.splitlines()[2] == "No issues found."
    assert "**CLAUDE.md compliance:** 2 agent(s)" in clean
    assert "Checked for bugs" not in clean
    part = rv.summary_body(record(read=4), run_url="u/run")
    assert rv.classify(summary(part)) == "no issues"
    assert part.splitlines()[2].startswith("No issues found in what was read.")
    assert "did not read every changed line" in part
    assert "read 4 of the 10 changed lines" in part
    assert "passes this with a warning" in part
    skipped = rv.summary_body(
        record(bugs=0, read=4) | {"session": {"lines": 4, "files_read": 1}},
        run_url="u/run",
        rerun="gh run rerun 5 --failed",
    )
    assert rv.classify(summary(skipped)) == "incomplete"
    assert not rv.NO_ISSUES.search(skipped)
    assert "**Bugs:** not checked" in skipped
    assert "read 4 of the 10 changed lines" in skipped
    assert "`gh run rerun 5 --failed`" in skipped


def nobody_covered() -> rv.Covered:
    return rv.Covered(None, None, "no review on it finished")


def test_a_skipped_pass_fails_partial_reading_warns_and_complete_passes_clean() -> None:
    clean = [summary("No issues found.")]
    passed = decide(summaries=clean)
    assert passed.passed and "read all 10 changed lines" in passed.reason
    assert passed.warnings == []
    reviewed = attempt(9, "completed", "success", "t")
    skipped = decide(
        summaries=clean, coverage=rv.Covered(reviewed, record(claude_md=0))
    )
    assert not skipped.passed
    assert "skipped a check it reports" in skipped.reason
    assert "CLAUDE.md compliance: not checked" in skipped.reason
    assert "gh run rerun 9 --job 90" in skipped.remedy
    partial = decide(
        summaries=clean,
        coverage=rv.Covered(reviewed, record(read=3, total=10)),
    )
    assert partial.passed and "did not read every changed line" in partial.reason
    assert partial.warnings == [
        "Bugs: 3 of 10 changed lines read; a line no agent read was not checked.",
        "CLAUDE.md compliance: 3 of 10 changed lines read; a line no agent read "
        "was not checked.",
    ]
    assert "**Warning:** CLAUDE.md compliance: 3 of 10" in rv.summary_markdown(partial)
    unknown = decide(
        summaries=clean,
        coverage=rv.Covered(reviewed, None, "it left no coverage record"),
    )
    assert not unknown.passed and "unknown is not covered" in unknown.reason
    nobody = decide(
        summaries=[summary("Claude review stopped: it already has a comment")],
        coverage=rv.Covered(None, None, "every review on it stopped"),
    )
    assert not nobody.passed and "No review read this pull request" in nobody.reason
    failed = decide("failed", coverage=rv.Covered(reviewed, record(bugs=0)))
    assert not failed.passed and "Bugs: not checked" in failed.reason
    # A review job that failed for another reason says so, whatever it read.
    other = decide("failed", coverage=rv.Covered(reviewed, record(read=3)))
    assert not other.passed and "did not show that it read the diff" in other.reason
    # Findings still come first, and a reviewer edit still passes unreviewed.
    assert decide(threads=[thread(resolved=False)], coverage=nobody_covered()).findings
    assert decide(self_edit=True, coverage=nobody_covered()).passed


def test_the_verdict_reads_the_record_of_the_review_that_read_the_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = attempt(1, "completed", "success", "2026-10-17T10:00:00Z")
    stop = attempt(2, "completed", "success", "2026-10-17T11:00:00Z")
    skipped = attempt(3, "completed", "skipped", "2026-10-17T12:00:00Z")
    stopped = record(stopped=True, bugs=0, claude_md=0, read=0)
    use(
        monkeypatch,
        FakeGh(
            annotations={
                10: [coverage_annotation(record())],
                20: [coverage_annotation(stopped)],
            }
        ),
    )
    # A stop at the plugin's first step read nothing on purpose; the review
    # before it decides.
    covered = rv.find_coverage(REPO, [first, stop, skipped], "reviewed", stop)
    assert covered.attempt == first and covered.record == record()
    assert "stopped at the plugin's first step" in covered.note
    alone = rv.find_coverage(REPO, [stop], "reviewed", stop)
    assert alone.attempt is None and "every review on it stopped" in alone.note
    # Two records, or one that is not JSON, vouch for nothing.
    use(
        monkeypatch,
        FakeGh(
            annotations={
                10: [coverage_annotation(record()), coverage_annotation(record())],
                20: [{"title": rv.COVERAGE_TITLE, "message": "{not json"}],
            }
        ),
    )
    assert rv.read_coverage(REPO, first) == (
        None,
        "it left 2 coverage records, not one",
    )
    assert rv.read_coverage(REPO, stop)[0] is None
    assert rv.read_coverage(REPO, attempt(4, "completed", "success", "t")) == (
        None,
        "it left no coverage record",
    )


def test_a_clean_summary_from_a_review_with_no_record_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, jobs = review_run(1, "success")
    fake = FakeGh(runs=[run], jobs={1: jobs}, threads=[[claude_thread(True)]])
    use(monkeypatch, fake)
    assert judge() == 1
    out = capsys.readouterr().out
    assert "coverage from u/run/1: none (it left no coverage record)" in out
    assert "left no record of what it read" in out and "verdict=fail" in out
    fake.annotations = {10: [coverage_annotation(record())]}
    assert judge() == 0
    assert "read all 10 changed lines" in capsys.readouterr().out


def coverage_args(path: Path, **overrides: str) -> list[str]:
    values = {
        "--repo": REPO,
        "--pr": "7",
        "--head": HEAD,
        "--execution-file": str(path),
        "--self-modified": "false",
        "--since": "2026-10-17T10:00:00Z",
    }
    values.update(overrides)
    return ["coverage", *[item for pair in values.items() for item in pair]]


def claude_comment(comment_id: int, body: str) -> dict[str, Any]:
    return {
        "id": comment_id,
        "user": {"login": rv.REVIEWER_LOGIN},
        "created_at": "2026-10-17T10:05:00Z",
        "body": body,
        "html_url": f"u/c/{comment_id}",
    }


PLUGIN_SUMMARY = (
    "## Code review\n\nNo issues found. Checked for bugs and CLAUDE.md compliance."
)


def test_the_coverage_step_records_what_it_measured_and_fails_a_skipped_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    outputs, summary_file = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    s = Session()
    s.tool("Bash", SAMPLE_DIFF, command="gh pr diff 7")
    stream = tmp_path / "execution.json"
    stream.write_text(json.dumps(s.records))
    older = claude_comment(40, "No issues found.") | {
        "created_at": "2026-10-16T00:00:00Z"
    }
    use(
        monkeypatch,
        FakeGh(
            diff=SAMPLE_DIFF,
            issue_comments=[older, claude_comment(41, PLUGIN_SUMMARY)],
        ),
    )
    assert rv.main(coverage_args(stream)) == 1
    out = capsys.readouterr().out
    notice = next(line for line in out.splitlines() if line.startswith("::notice"))
    assert notice.startswith(f"::notice title={rv.COVERAGE_TITLE}::")
    found = json.loads(notice.split("::", 2)[2])
    assert found == rv.compact(found) and found["head"] == HEAD
    assert found["passes"]["bugs"]["agents"] == 0 and found["session"]["lines"] == 8
    assert "::error::Bugs: not checked" in out
    assert "coverage=incomplete gaps=2 shortfalls=0" in out
    written = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert written["summaries"] == "41"  # this run's, not the older one
    assert json.loads(written["coverage"])["session"]["unread"] == []
    assert "| the session |" in summary_file.read_text()


def test_the_coverage_step_warns_on_partial_reading_and_does_not_fail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    s = Session()
    head = "\n".join(SAMPLE_DIFF.splitlines()[:13])
    for name in ("CLAUDE.md compliance A", "Opus bug scan"):
        s.tool("Bash", head, owner=s.agent(name), command="gh pr diff 7 | head -13")
    stream = tmp_path / "execution.json"
    stream.write_text(json.dumps(s.records))
    use(
        monkeypatch,
        FakeGh(diff=SAMPLE_DIFF, issue_comments=[claude_comment(41, PLUGIN_SUMMARY)]),
    )
    assert rv.main(coverage_args(stream)) == 0
    out = capsys.readouterr().out
    assert "::warning::CLAUDE.md compliance: 5 of 8 changed lines read" in out
    assert "::error::" not in out
    assert "coverage=partial gaps=0 shortfalls=2" in out


def test_the_coverage_step_passes_a_stop_and_a_reviewer_edit_and_nothing_unread(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    stream = tmp_path / "execution.json"
    stream.write_text(json.dumps(Session().records))
    stop = claude_comment(41, "Claude review stopped: it already has a comment")
    use(monkeypatch, FakeGh(diff=SAMPLE_DIFF, issue_comments=[stop]))
    assert rv.main(coverage_args(stream)) == 0
    assert "coverage=stopped" in capsys.readouterr().out
    edited = {"--execution-file": "", "--self-modified": "true"}
    assert rv.main(coverage_args(stream, **edited)) == 0
    assert rv.main(coverage_args(stream, **{"--execution-file": ""})) == 1
    assert rv.main(coverage_args(stream, **{"--since": ""})) == 1
    use(monkeypatch, FakeGh(diff=SAMPLE_DIFF, fail="application/vnd.github.diff"))
    assert rv.main(coverage_args(stream)) == 1
    assert "Could not measure what the review read" in capsys.readouterr().out


def test_the_rewrite_replaces_only_the_reviewers_clean_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    theirs = claude_comment(42, "No issues found.") | {"user": {"login": "someone"}}
    fake = use(
        monkeypatch,
        FakeGh(issue_comments=[claude_comment(41, PLUGIN_SUMMARY), theirs]),
    )
    monkeypatch.setenv("COVERAGE", json.dumps(record(claude_md=0)))
    rewrite = ["rewrite", "--repo", REPO, "--run-id", "5", "--comments"]
    assert rv.main([*rewrite, "41"]) == 0
    ((path, body),) = fake.patches
    assert path == f"repos/{REPO}/issues/comments/41"
    assert rv.classify(summary(body)) == "incomplete"
    assert "**CLAUDE.md compliance:** not checked" in body
    assert "gh run rerun 5 --failed" in body
    out = capsys.readouterr().out
    assert "it said: ## Code review No issues found. Checked for bugs" in out
    # Anyone else's comment, or anything but a clean summary, is left alone.
    assert rv.main([*rewrite, "42"]) == 1
    monkeypatch.setenv("COVERAGE", "")
    assert rv.main([*rewrite, "41"]) == 1
    assert len(fake.patches) == 1


def test_the_review_job_measures_after_the_guard_and_another_job_rewrites() -> None:
    jobs = _workflow("claude-review.yml")["jobs"]
    review = jobs["review"]
    steps = [s.get("name", s.get("uses", "")) for s in review["steps"]]
    guard = steps.index("The review must finish, unrefused, and leave a comment")
    fetch = steps.index("Read the coverage script from the commit under test")
    measure = steps.index(
        "The review must run both checks, and says how much each read"
    )
    assert guard < fetch < measure == len(steps) - 1
    step = review["steps"][measure]
    assert step["id"] == "coverage" and step["if"] == "always()"
    assert '"$RUNNER_TEMP/review_verdict.py" coverage' in step["run"]
    assert step["env"]["EXECUTION_FILE"] == "${{ steps.review.outputs.execution_file }}"
    assert review["steps"][fetch]["env"]["REF"] == "${{ github.sha }}"
    assert review["outputs"] == {
        "coverage": "${{ steps.coverage.outputs.coverage }}",
        "summaries": "${{ steps.coverage.outputs.summaries }}",
    }
    # The job that runs the model gets no write access; the rewrite does.
    assert "write" not in {
        value for key, value in review["permissions"].items() if key != "id-token"
    }
    rewrite = jobs["summary"]
    assert rewrite["needs"] == "review"
    assert rewrite["if"] == "always() && needs.review.outputs.summaries != ''"
    assert rewrite["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }
    assert '"$RUNNER_TEMP/review_verdict.py" rewrite' in rewrite["steps"][-1]["run"]


def test_the_reviewer_is_told_what_is_measured_and_a_rerun_reviews_again() -> None:
    review = _workflow("claude-review.yml")["jobs"]["review"]
    action = next(s for s in review["steps"] if s.get("id") == "review")
    args = action["with"]["claude_args"]
    assert "Run every step and every agent the code review describes" in args
    assert "read that file to its end" in args
    # "Does not need review" is no longer a reason to stop: a stop reads
    # nothing, and the verdict passes only a review that read the diff.
    assert "Stop at the first step only when" in args
    assert "does not need review" not in args
    assert "${{ github.run_attempt > 1 && ' This run re-runs a review" in args
