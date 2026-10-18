"""The Claude review's verdict: what holds a merge, what releases it, and when.

tools/ci/review_verdict.py is the rule and `.github/workflows/claude-review.yml`
runs it as the required check `claude review verdict`. These tests hold the
rule to what docs/09 P89 claims for it: an unaddressed finding or a review
that never finished fails, a resolved or empty review passes, nothing the
check could not read passes, and the workflow reports a verdict on every head
without ever skipping it, since GitHub counts a skipped job as a passed check.
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
    assert verdict["needs"] == ["gate", "review"]
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
    )


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
    **pr_overrides: Any,
) -> rv.Verdict:
    who = None if state == "none" else attempt(9, "completed", "success", "t", reviewed)
    return rv.decide(
        pr(**pr_overrides),
        state,
        who,
        rv.Feedback(threads=threads or [], summaries=summaries or []),
        self_edit=self_edit,
        since=since,
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
        self.fail: str | None = state.get("fail")
        self.reruns: list[str] = []

    def __call__(self, *args: str) -> str:
        if self.fail and any(self.fail in a for a in args):
            raise rv.GhError(f"HTTP 502 for {self.fail}")
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
            "name": "review",
            "status": status,
            "conclusion": conclusion,
            "started_at": f"2026-10-17T1{run_id}:00:01Z",
            "html_url": f"u/run/{run_id}",
        }
    ]
    return run, jobs


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
