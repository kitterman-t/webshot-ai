"""The dependency watch says when updates have stopped arriving (docs/09 P84).

On 2026-10-17 the update pipeline had stalled for about a week with no alarm:
Dependabot's pull requests ran no CI, green bumps sat unmerged, and the check
meant to notice read an empty answer as "nothing stale". These tests hold
tools/audit/staleness.py to the opposite: each kind of stall is a finding,
and an input that cannot be read fails the run instead of passing it. No
test touches the network: PyPI's and GitHub's answers are handed in.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.version import Version
from tools.audit import staleness
from tools.audit.staleness import (
    CLEAN,
    INCOMPLETE,
    STALE,
    InputError,
    Release,
    Report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 17, 18, 0, tzinfo=dt.UTC)
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
OTHER = "c18668ad3cf93ea998bef934396af7bb5c839dc7"


def days_ago(days: float) -> str:
    return (NOW - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pypi(**releases: tuple[float, bool]) -> dict[str, Any]:
    """PyPI's JSON: version -> (days since its upload, yanked)."""
    return {
        "releases": {
            version: [{"upload_time_iso_8601": days_ago(age), "yanked": yanked}]
            for version, (age, yanked) in releases.items()
        }
    }


def project(
    root: Path, *, lock: str | None = None, workflows: str | None = None
) -> Path:
    """A repository with one direct dependency, `demo`, locked at 1.0.0."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["demo>=1"]\n'
        '[project.optional-dependencies]\ndev = ["demo[extra]>=1"]\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        lock
        or (
            'version = 1\n[[package]]\nname = "x"\nversion = "0"\n'
            'source = { editable = "." }\n'
            '[[package]]\nname = "demo"\nversion = "1.0.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            '[[package]]\nname = "transitive"\nversion = "0.1"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        ),
        encoding="utf-8",
    )
    flows = root / ".github" / "workflows"
    flows.mkdir(parents=True)
    (flows / "ci.yml").write_text(
        workflows
        or (
            "jobs:\n  a:\n    steps:\n"
            f"      - uses: actions/checkout@{SHA} # v7.0.1\n"
            f"      # - uses: actions/checkout@{OTHER} # v1.0.0\n"
            "      - uses: ./local-action\n"
        ),
        encoding="utf-8",
    )
    return root


def watch(
    root: Path,
    *,
    packages: dict[str, dict[str, Any]] | None = None,
    releases: list[Release] | None = None,
    commit: str = SHA,
    prs: list[dict[str, Any]] | None = None,
    holds: str | None = None,
) -> Report:
    answers = (
        packages if packages is not None else {"demo": pypi(**{"1.0.0": (90, False)})}
    )

    def fetch(name: str) -> dict[str, Any]:
        if name not in answers:
            raise InputError(f"PyPI did not answer for {name}")
        return answers[name]

    holds_path = root / "holds.toml"
    if holds is not None:
        holds_path.write_text(holds, encoding="utf-8")
    return staleness.run_watch(
        root,
        "o/r",
        NOW,
        fetch=fetch,
        releases_of=lambda _: releases if releases is not None else [],
        commit_of=lambda _repo, _tag: commit,
        list_prs=lambda _: prs or [],
        holds_path=holds_path,
    )


def keys(report: Report) -> list[str]:
    return sorted(f.key for f in report.findings)


# --------------------------------------------------------------------------
# Packages


def test_a_clean_lock_is_clean_and_says_what_it_judged(tmp_path: Path) -> None:
    report = watch(project(tmp_path))
    assert report.status == CLEAN, (report.findings, report.incomplete)
    assert report.judged[0] == (
        "1 pin(s) of 1 direct dependencies against PyPI (1 transitive pins are not judged)"
    )
    assert report.judged[1].startswith("1 pinned action(s)")


@pytest.mark.parametrize(
    ("newer", "age", "stale"),
    [
        ("1.0.1", 17.5, True),  # patch: cooldown 3 + 14 days
        ("1.0.1", 16.5, False),
        ("1.1.0", 21.5, True),  # minor: 7 + 14
        ("1.1.0", 20.5, False),
        ("2.0.0", 28.5, True),  # major: 14 + 14
        ("2.0.0", 27.5, False),
    ],
)
def test_a_release_is_stale_past_its_cooldown_plus_two_weeks(
    tmp_path: Path, newer: str, age: float, stale: bool
) -> None:
    answers = {"demo": pypi(**{"1.0.0": (200, False), newer: (age, False)})}
    report = watch(project(tmp_path), packages=answers)
    assert (report.status == STALE) is stale, report.findings
    if stale:
        (finding,) = report.findings
        assert finding.key == "behind:package:demo:1.0.0"
        assert f"demo 1.0.0 is {int(age)} days behind: {newer} came out" in finding.text


def test_the_wait_counts_from_the_first_newer_release(tmp_path: Path) -> None:
    """A project that releases weekly always has a fresh newest release."""
    answers = {
        "demo": pypi(
            **{
                "1.0.0": (200, False),
                "1.1.0": (60, False),
                "1.2.0": (3, False),
                "2.0.0rc1": (1, False),  # pre-releases are never proposed
                "1.3.0": (0.5, True),  # nor yanked ones
            }
        )
    }
    (finding,) = watch(project(tmp_path), packages=answers).findings
    assert "60 days behind: 1.1.0 came out" in finding.text
    assert finding.text.endswith("and the newest is 1.2.0")


def test_a_yanked_or_vanished_locked_version_is_a_finding(tmp_path: Path) -> None:
    yanked = {"demo": pypi(**{"1.0.0": (90, True)})}
    assert keys(watch(project(tmp_path / "a"), packages=yanked)) == [
        "yanked:demo:1.0.0"
    ]
    gone = {"demo": pypi(**{"0.9": (90, False)})}
    assert keys(watch(project(tmp_path / "b"), packages=gone)) == [
        "unpublished:demo:1.0.0"
    ]


def test_pypi_not_answering_makes_the_run_incomplete(tmp_path: Path) -> None:
    report = watch(project(tmp_path), packages={})
    assert report.status == INCOMPLETE
    assert report.incomplete == ["PyPI did not answer for demo"]


@pytest.mark.parametrize(
    ("lock", "message"),
    [
        ("version = 1\n", "uv.lock pins no registry package"),
        ("not = toml = at all", "uv.lock is not TOML"),
        (
            'version = 1\n[[package]]\nname = "other"\nversion = "1"\n'
            'source = { registry = "https://pypi.org/simple" }\n',
            "pyproject.toml names ['demo'], which uv.lock does not pin",
        ),
    ],
)
def test_a_lock_that_cannot_be_read_is_not_a_clean_one(
    tmp_path: Path, lock: str, message: str
) -> None:
    report = watch(project(tmp_path, lock=lock))
    assert report.status == INCOMPLETE
    assert any(message in problem for problem in report.incomplete), report.incomplete


def test_a_missing_lock_is_incomplete(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "uv.lock").unlink()
    report = watch(root)
    assert report.status == INCOMPLETE
    assert report.incomplete[0].startswith("packages: ")


# --------------------------------------------------------------------------
# Holds


def test_a_hold_with_a_reason_and_a_date_stops_the_finding(tmp_path: Path) -> None:
    answers = {"demo": pypi(**{"1.0.0": (200, False), "2.0.0": (60, False)})}
    hold = (
        '[[hold]]\npackage = "Demo"\nreason = "2.0 drops an API"\nuntil = 2026-12-01\n'
    )
    report = watch(project(tmp_path), packages=answers, holds=hold)
    assert report.status == CLEAN
    (note,) = report.notes
    assert note.startswith("held until 2026-12-01: demo 1.0.0 is 60 days behind")


def test_an_expired_hold_is_a_finding(tmp_path: Path) -> None:
    answers = {"demo": pypi(**{"1.0.0": (200, False), "2.0.0": (60, False)})}
    hold = (
        '[[hold]]\npackage = "demo"\nreason = "2.0 drops an API"\nuntil = 2026-10-01\n'
    )
    report = watch(project(tmp_path), packages=answers, holds=hold)
    assert keys(report) == ["hold-expired:package:demo"]
    assert (
        "Its hold ran out on 2026-10-01 (2.0 drops an API)" in report.findings[0].text
    )


def test_a_hold_that_is_no_longer_needed_is_a_finding(tmp_path: Path) -> None:
    holds = (
        '[[hold]]\npackage = "demo"\nreason = "r"\nuntil = 2026-12-01\n'
        '[[hold]]\naction = "nobody/uses-this"\nreason = "r"\nuntil = 2026-12-01\n'
    )
    report = watch(project(tmp_path), holds=holds)
    assert keys(report) == [
        "hold-unknown:action:nobody/uses-this",
        "hold-unneeded:package:demo",
    ]


@pytest.mark.parametrize(
    "hold",
    [
        '[[hold]]\npackage = "demo"\nuntil = 2026-12-01\n',  # no reason
        '[[hold]]\npackage = "demo"\nreason = "r"\n',  # no date
        '[[hold]]\npackage = "demo"\nreason = "r"\nuntil = "soon"\n',
        '[[hold]]\npackage = "demo"\naction = "a/b"\nreason = "r"\nuntil = 2026-12-01\n',
        '[[hold]]\npackage = "demo"\nreason = "r"\nuntil = 2026-12-01\nby = "me"\n',
    ],
)
def test_a_malformed_hold_is_incomplete(tmp_path: Path, hold: str) -> None:
    report = watch(project(tmp_path), holds=hold)
    assert report.status == INCOMPLETE
    assert report.incomplete[0].startswith("holds.toml hold 1")


def test_the_holds_file_reads() -> None:
    text = (REPO_ROOT / "tools" / "audit" / "holds.toml").read_text(encoding="utf-8")
    for hold in staleness.read_holds(text):
        assert hold.until >= dt.date(2026, 10, 17), hold


# --------------------------------------------------------------------------
# Actions


def test_actions_are_read_from_uses_lines_and_never_from_comments(
    tmp_path: Path,
) -> None:
    flows = tmp_path / "w"
    flows.mkdir()
    (flows / "a.yml").write_text(
        f"    - uses: actions/checkout@{SHA} # v7.0.1\n"
        f"      uses: github/codeql-action/init@{OTHER} # v4.1.0\n"
        f"  #   - uses: actions/checkout@{OTHER} # v1.0.0\n"
        "    - uses: actions/cache@v4\n"
        f"    - uses: actions/setup-python@{OTHER}\n"
        "    - uses: ./local\n",
        encoding="utf-8",
    )
    (flows / "b.yml").write_text(f"- uses: actions/checkout@{SHA} # v7.0.1\n", "utf-8")
    pins, findings = staleness.pinned_actions(flows)
    assert [(p.repository, p.tag, p.files) for p in pins] == [
        ("actions/checkout", "v7.0.1", ("a.yml", "b.yml")),
        ("github/codeql-action", "v4.1.0", ("a.yml",)),
    ]
    assert [f.key for f in findings] == [
        "unpinned:a.yml:actions/cache@v4",
        f"untagged:a.yml:actions/setup-python@{OTHER}",
    ]


def test_no_workflow_is_incomplete(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no workflow files"):
        staleness.pinned_actions(tmp_path)


def test_an_action_behind_by_more_than_three_weeks_is_stale(tmp_path: Path) -> None:
    def at(days: float) -> dt.datetime:
        return NOW - dt.timedelta(days=days)

    releases = [
        Release(Version("v7.0.1"), at(90)),
        Release(Version("v6.9.0"), at(10)),  # a backport is not newer
        Release(Version("v7.1.0"), at(21.5)),
        Release(Version("v8.0.0"), at(2)),
    ]
    report = watch(project(tmp_path), releases=releases)
    (finding,) = report.findings
    assert finding.key == "behind:action:actions/checkout:7.0.1"
    assert "actions/checkout (ci.yml) 7.0.1 is 21 days behind: 7.1.0" in finding.text
    assert "cooldown 7 days + 14" in finding.text
    releases[2] = Release(Version("v7.1.0"), at(20.5))
    assert watch(project(tmp_path / "b"), releases=releases).status == CLEAN


def test_a_tag_that_no_longer_names_the_pin_is_a_finding(tmp_path: Path) -> None:
    """tj-actions/changed-files, March 2025: every tag was moved to a malicious
    commit. A SHA pin is safe from that; the next bump, made from the tag, is
    not."""
    report = watch(project(tmp_path), commit=OTHER)
    assert keys(report) == [f"tag-moved:actions/checkout@v7.0.1:{SHA}"]
    assert "the tag now names c18668ad3cf9" in report.findings[0].text


def test_github_not_answering_for_an_action_is_incomplete(tmp_path: Path) -> None:
    root = project(tmp_path)

    def refused(_repo: str) -> list[Release]:
        raise InputError("`gh api repos/actions/checkout/releases` exited 1: HTTP 502")

    report = staleness.run_watch(
        root,
        "o/r",
        NOW,
        fetch=lambda _: pypi(**{"1.0.0": (90, False)}),
        releases_of=refused,
        commit_of=lambda _r, _t: SHA,
        list_prs=lambda _: [],
    )
    assert report.status == INCOMPLETE
    assert "HTTP 502" in report.incomplete[0]


# --------------------------------------------------------------------------
# Dependabot's pull requests


def pr(
    number: int,
    hours: float,
    *,
    checks: list[dict[str, Any]] | None = None,
    armed: bool = True,
    files: tuple[str, ...] = ("uv.lock",),
    login: str = "app/dependabot",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"bump {number}",
        "url": f"https://github.com/o/r/pull/{number}",
        "createdAt": (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": {"login": login},
        "autoMergeRequest": {"mergeMethod": "MERGE"} if armed else None,
        "statusCheckRollup": checks if checks is not None else [GREEN],
        "files": [{"path": path} for path in files],
    }


GREEN = {
    "__typename": "CheckRun",
    "name": "lint",
    "status": "COMPLETED",
    "conclusion": "SUCCESS",
}
RED = {
    "__typename": "CheckRun",
    "name": "golden corpus",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
}
QUEUED = {
    "__typename": "CheckRun",
    "name": "tests",
    "status": "QUEUED",
    "conclusion": None,
}


def judge(*prs: dict[str, Any]) -> Report:
    report = Report()
    staleness.judge_dependabot(list(prs), NOW, report)
    return report


def test_a_young_green_armed_bump_is_fine() -> None:
    report = judge(pr(1, 2), pr(2, 50), pr(3, 5, checks=[], armed=False))
    assert report.findings == [], report.findings
    assert report.judged == ["3 open Dependabot pull request(s)"]


def test_a_bump_no_ci_ran_on_is_a_finding() -> None:
    """2026-10-12 to 10-17: every workflow was off, and the bumps opened then
    had no check at all."""
    report = judge(pr(25, 7, checks=[]))
    assert keys(report) == ["pr-no-checks:25"]
    assert "no check has run on it" in report.findings[0].text


def test_a_failed_bump_is_a_finding_at_once() -> None:
    report = judge(pr(30, 1, checks=[GREEN, RED]))
    assert keys(report) == ["pr-failed:30"]
    assert "checks failed (golden corpus)" in report.findings[0].text


def test_a_bump_whose_auto_merge_is_off_names_the_remedy() -> None:
    """webshot-ai#26 and #28 edit workflow files. Once #27 changed a workflow
    on master under them, GitHub refused the merge to the GITHUB_TOKEN that
    armed it and turned auto-merge off; rebased, #28 merged itself."""
    (finding,) = judge(
        pr(28, 8, armed=False, files=(".github/workflows/ci.yml",))
    ).findings
    assert finding.key == "pr-unarmed:28"
    assert "auto-merge is off" in finding.text
    assert "edits a workflow file" in finding.text
    assert "`@dependabot rebase`" in finding.text
    (plain,) = judge(pr(29, 8, armed=False)).findings
    assert "workflow" not in plain.text and "`@dependabot rebase`" in plain.text


def test_a_bump_open_three_days_needs_a_person() -> None:
    report = judge(
        pr(13, 128, checks=[RED]),
        pr(26, 109, files=(".github/workflows/release.yml",)),
        pr(27, 100, checks=[QUEUED]),
        pr(40, 300, login="someone"),
    )
    assert keys(report) == ["pr-failed:13", "pr-open:13", "pr-open:26", "pr-open:27"]
    texts = {f.key: f.text for f in report.findings}
    assert texts["pr-open:13"].endswith("open 128 h: its checks failed")
    assert "`@dependabot rebase`" in texts["pr-open:26"]
    assert texts["pr-open:27"].endswith("open 100 h: it needs a person")
    assert report.judged == ["3 open Dependabot pull request(s)"]


def test_status_contexts_count_as_checks() -> None:
    error = {"__typename": "StatusContext", "context": "ci/x", "state": "ERROR"}
    assert keys(judge(pr(5, 1, checks=[error]))) == ["pr-failed:5"]


def test_pull_requests_are_listed_without_the_search_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh pr list --author` asks GitHub's search API, which answered another
    repository's token with nothing (docs/09 P84)."""
    calls: list[tuple[str, ...]] = []

    def fake(*arguments: str) -> str:
        calls.append(arguments)
        return json.dumps([pr(1, 1)])

    monkeypatch.setattr(staleness, "gh", fake)
    assert staleness.open_pull_requests("o/r")[0]["number"] == 1
    (call,) = calls
    assert "--author" not in call and "--search" not in call and "--label" not in call
    assert call[call.index("--limit") + 1] == "500"
    assert "statusCheckRollup" in call[-1] and "autoMergeRequest" in call[-1]


# --------------------------------------------------------------------------
# The heartbeat


def heartbeat(meta: dict[str, Any], runs: list[dict[str, Any]]) -> Report:
    def api(*arguments: str) -> str:
        return json.dumps(
            {"workflow_runs": runs} if arguments[1].endswith("per_page=20") else meta
        )

    return staleness.run_heartbeat("o/r", "dependency-watch.yml", NOW, api=api)


ACTIVE = {"state": "active", "created_at": "2026-10-01T00:00:00Z"}


def run(hours: float, conclusion: str = "success") -> dict[str, Any]:
    return {
        "id": 1,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "html_url": "https://github.com/o/r/actions/runs/1",
    }


def test_a_watch_that_ran_today_is_alive() -> None:
    assert heartbeat(ACTIVE, [run(30, "failure"), run(50)]).status == CLEAN


def test_a_watch_that_github_disabled_is_a_finding() -> None:
    """GitHub disables a public repository's scheduled workflows after 60 days
    without activity, and a disabled workflow fails nothing."""
    report = heartbeat({**ACTIVE, "state": "disabled_inactivity"}, [run(1500)])
    assert keys(report) == ["heartbeat-late", "heartbeat-state:disabled_inactivity"]


def test_a_watch_that_stopped_or_judged_nothing_is_a_finding() -> None:
    assert keys(heartbeat(ACTIVE, [run(37)])) == ["heartbeat-late"]
    assert keys(heartbeat(ACTIVE, [run(2, "cancelled")])) == [
        "heartbeat-conclusion:cancelled"
    ]
    assert keys(heartbeat(ACTIVE, [])) == ["heartbeat-none"]
    # A workflow added a day ago has had no schedule to miss yet.
    young = {**ACTIVE, "created_at": (NOW - dt.timedelta(hours=20)).isoformat()}
    assert heartbeat(young, []).status == CLEAN


def test_a_heartbeat_that_cannot_read_the_workflow_is_incomplete() -> None:
    assert heartbeat({"state": "active"}, [run(1)]).status == INCOMPLETE

    def refused(*_: str) -> str:
        raise InputError("`gh api ...` exited 1: HTTP 502")

    report = staleness.run_heartbeat("o/r", "w.yml", NOW, api=refused)
    assert report.status == INCOMPLETE and "HTTP 502" in report.incomplete[0]


def test_a_watch_that_is_not_there_is_a_finding() -> None:
    """Between a merge and the export that carries the workflow, or after
    someone deletes it: either way nothing is judging the dependencies."""

    def missing(*_: str) -> str:
        raise InputError("`gh api ...` exited 1: gh: Not Found (HTTP 404)")

    report = staleness.run_heartbeat("o/r", "w.yml", NOW, api=missing)
    assert keys(report) == ["heartbeat-missing"]
    assert report.findings[0].text.startswith("o/r has no workflow w.yml")


# --------------------------------------------------------------------------
# The issue


class Issues:
    """A fake `gh issue` that keeps what was written."""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *arguments: str) -> str:
        self.calls.append(arguments)
        if arguments[:2] == ("issue", "list"):
            assert "--search" not in arguments
            return json.dumps(self.existing)
        return ""

    def verbs(self) -> list[str]:
        return [call[1] for call in self.calls]


def stale(*keys_: str) -> Report:
    return Report(
        judged=["1 thing"],
        findings=[staleness.Finding(key, f"text of {key}") for key in keys_],
    )


def test_the_first_problem_opens_an_issue_that_mentions_the_owner() -> None:
    issues = Issues()
    staleness.sync_issue("o/r", "t", stale("a"), "https://run", "owner", api=issues)
    assert issues.verbs() == ["list", "create"]
    body = issues.calls[1][-1]
    assert "- text of a" in body and "Run: https://run" in body
    assert "@owner" in body and "<!-- keys: a -->" in body


def test_a_known_problem_rewrites_the_issue_without_a_new_notification() -> None:
    issues = Issues([{"number": 7, "title": "t", "body": "old\n<!-- keys: a -->\n"}])
    staleness.sync_issue("o/r", "t", stale("a"), None, "owner", api=issues)
    assert issues.verbs() == ["list", "edit"]


def test_a_new_problem_is_commented_so_someone_hears_of_it() -> None:
    issues = Issues([{"number": 7, "title": "t", "body": "<!-- keys: a -->"}])
    staleness.sync_issue("o/r", "t", stale("a", "b"), None, "owner", api=issues)
    assert issues.verbs() == ["list", "edit", "comment"]
    comment = issues.calls[2][-1]
    assert (
        "- text of b" in comment and "text of a" not in comment and "@owner" in comment
    )


def test_a_clean_run_closes_the_issue_and_an_incomplete_one_keeps_it() -> None:
    issues = Issues(
        [{"number": 7, "title": "t", "body": ""}, {"number": 8, "title": "u"}]
    )
    staleness.sync_issue("o/r", "t", Report(judged=["x"]), None, None, api=issues)
    assert issues.verbs() == ["list", "close"] and issues.calls[1][2] == "7"
    issues = Issues([{"number": 7, "title": "t", "body": ""}])
    staleness.sync_issue(
        "o/r", "t", Report(incomplete=["PyPI down"]), None, None, api=issues
    )
    assert issues.verbs() == ["list", "edit", "comment"]
    assert "could not finish" in issues.calls[1][-1]


def test_a_crash_still_reaches_the_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_: Any, **__: Any) -> Report:
        raise KeyError("number")

    issues = Issues()
    monkeypatch.setattr(staleness, "run_watch", boom)
    monkeypatch.setattr(staleness, "gh", issues)
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert (
        staleness.main(["--repo", "o/r", "--issue", "--issue-repo", "o/r"])
        == INCOMPLETE
    )
    assert issues.verbs() == ["list", "create"]
    assert "the check crashed" in issues.calls[1][-1]
    assert output.read_text() == "reported=true\n"


# --------------------------------------------------------------------------
# The repository's own files


def _cooldowns() -> dict[str, dict[str, int]]:
    config = yaml.safe_load(
        (REPO_ROOT / ".github" / "dependabot.yml").read_text("utf-8")
    )
    found = {}
    for update in config["updates"]:
        cooldown = update["cooldown"]
        default = cooldown["default-days"]
        found[update["package-ecosystem"]] = {
            kind: cooldown.get(f"semver-{kind}-days", default)
            for kind in ("major", "minor", "patch")
        }
    return found


def test_the_alarm_and_dependabot_agree_on_the_cooldown() -> None:
    assert _cooldowns() == staleness.COOLDOWN_DAYS


def test_every_ecosystem_has_a_cooldown_and_actions_take_only_the_default() -> None:
    """GitHub's options reference: the semver keys are not supported for
    GitHub Actions. Cooldown applies to version updates only, so no setting
    here can delay a security update."""
    config = yaml.safe_load(
        (REPO_ROOT / ".github" / "dependabot.yml").read_text("utf-8")
    )
    ecosystems = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(ecosystems) == {"uv", "github-actions"}
    assert set(ecosystems["github-actions"]["cooldown"]) == {"default-days"}
    for update in config["updates"]:
        days = update["cooldown"].values()
        assert all(isinstance(d, int) and 1 <= d <= 30 for d in days), update


def test_the_watch_runs_on_a_schedule_on_a_hosted_runner_and_writes_an_issue() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "dependency-watch.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(workflow[True]) == {"schedule", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["watch"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["if"] == "github.repository == 'kitterman-t/webshot-ai'"
    assert job["permissions"]["issues"] == "write"
    assert {v for k, v in job["permissions"].items() if k != "issues"} == {"read"}
    steps = {step.get("id"): step for step in job["steps"]}
    assert "staleness.py --issue" in steps["watch"]["run"]
    assert "set -euo pipefail" in steps["watch"]["run"]
    # If the check never reached its issue, the next step still opens one.
    fallback = job["steps"][-1]
    assert fallback["if"] == "failure() && steps.watch.outputs.reported != 'true'"
    assert "gh issue create" in fallback["run"]
    assert staleness.ISSUE_TITLE in fallback["run"]


def test_a_hold_on_a_name_with_one_pin_behind_and_one_current_is_still_needed(
    tmp_path: Path,
) -> None:
    """A lock can pin one package twice, forked by a marker, and a partial
    bump can leave an action pinned two ways. The hold is judged once per
    name: needed while any pin of it is behind (review of #248)."""
    lock = (
        'version = 1\n[[package]]\nname = "x"\nversion = "0"\n'
        'source = { editable = "." }\n'
        '[[package]]\nname = "demo"\nversion = "1.0.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        '[[package]]\nname = "demo"\nversion = "2.0.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    answers = {"demo": pypi(**{"1.0.0": (200, False), "2.0.0": (60, False)})}
    hold = (
        '[[hold]]\npackage = "demo"\nreason = "a marker caps it"\nuntil = 2026-12-01\n'
    )
    report = watch(project(tmp_path / "held", lock=lock), packages=answers, holds=hold)
    assert report.findings == [], report.findings
    (note,) = report.notes
    assert note.startswith("held until 2026-12-01: demo 1.0.0 is 60 days behind")
    # Without the hold the behind pin is a finding and the current one is not.
    report = watch(project(tmp_path / "free", lock=lock), packages=answers)
    assert keys(report) == ["behind:package:demo:1.0.0"]
    # Once no pin is behind, the hold is unneeded, and said so once.
    current = {"demo": pypi(**{"1.0.0": (200, False), "2.0.0": (60, False)})}
    both_current = lock.replace('version = "1.0.0"', 'version = "2.0.0"')
    report = watch(
        project(tmp_path / "done", lock=both_current), packages=current, holds=hold
    )
    assert keys(report) == ["hold-unneeded:package:demo"]


def test_a_hold_on_an_action_pinned_two_ways_is_still_needed(tmp_path: Path) -> None:
    workflows = (
        "jobs:\n  a:\n    steps:\n"
        f"      - uses: actions/checkout@{SHA} # v7.0.1\n"
        f"      - uses: actions/checkout@{OTHER} # v8.0.0\n"
    )
    root = project(tmp_path, workflows=workflows)
    releases = [
        Release(Version("v7.0.1"), NOW - dt.timedelta(days=90)),
        Release(Version("v8.0.0"), NOW - dt.timedelta(days=60)),
    ]
    tags = {"v7.0.1": SHA, "v8.0.0": OTHER}
    hold = '[[hold]]\naction = "actions/checkout"\nreason = "r"\nuntil = 2026-12-01\n'
    (root / "holds.toml").write_text(hold, encoding="utf-8")
    report = staleness.run_watch(
        root,
        "o/r",
        NOW,
        fetch=lambda _: pypi(**{"1.0.0": (90, False)}),
        releases_of=lambda _: releases,
        commit_of=lambda _repo, tag: tags[tag],
        list_prs=lambda _: [],
        holds_path=root / "holds.toml",
    )
    assert report.findings == [], report.findings
    (note,) = report.notes
    assert "actions/checkout (ci.yml) 7.0.1 is 60 days behind" in note
