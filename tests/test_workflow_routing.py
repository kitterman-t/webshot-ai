"""Every workflow runs on a GitHub-hosted runner, and no fork's run gets a secret.

The repository is public. A `pull_request` run uses the workflow file in the
PR's merge commit, which a fork writes, so a fork can name any runner a label
reaches (docs/09 P10-22); the defence is that there is no runner of ours for
it to name. These tests hold the files to that: hosted labels only, no
repository variable choosing one, no trigger that hands a fork's code this
repository's secrets, and a reviewer that says why it skipped rather than
skipping in silence.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUNS_ON = re.compile(r"^\s*runs-on:\s*(.+?)\s*$", re.MULTILINE)
HOSTED = re.compile(r"(ubuntu|macos|windows)-(latest|\d[\w.-]*)")
#: The labels an expression can choose: each quoted literal after `&&`/`||`.
CHOSEN = re.compile(r"(?:&&|\|\|)\s*'([^']+)'")
#: What may remain of a `runs-on` expression once its literals are removed.
#: Anything else — `vars.`, `inputs.`, `github.event` — is a runner someone
#: other than this file chooses.
EXPRESSION_TOKENS = {"${{", "}}", "matrix.os", "==", "&&", "||"}

# Naming the files makes a renamed or added workflow fail here instead of
# leaving nothing to check.
EXPECTED = {
    "ci.yml",
    "claude-review.yml",
    "docs.yml",
    "release.yml",
    "windows-smoke.yml",
}


def _code(path: Path) -> str:
    """The workflow without its comments, which name what they warn against."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _workflows() -> dict[str, str]:
    return {path.name: _code(path) for path in sorted(WORKFLOWS.glob("*.yml"))}


def _labels(expression: str) -> list[str] | None:
    """The runner labels `runs-on` can resolve to, or None if it cannot say."""
    if not expression.startswith("${{"):
        return [expression]
    rest = re.sub(r"'[^']*'", " ", expression)
    if set(rest.split()) - EXPRESSION_TOKENS:
        return None
    return CHOSEN.findall(expression) or None


def test_every_job_names_a_github_hosted_runner() -> None:
    workflows = _workflows()
    assert set(workflows) == EXPECTED, sorted(workflows)
    seen = [
        (name, match.group(1))
        for name, text in workflows.items()
        for match in RUNS_ON.finditer(text)
    ]
    assert {name for name, _ in seen} == EXPECTED, seen
    wrong = [
        (name, expression)
        for name, expression in seen
        if (labels := _labels(expression)) is None
        or not all(HOSTED.fullmatch(label) for label in labels)
    ]
    assert not wrong, f"a job that can select a non-hosted runner: {wrong}"


def test_no_trigger_runs_a_forks_code_with_this_repositorys_secrets() -> None:
    """`pull_request_target` and `workflow_run` both run with the base's secrets."""
    offending = [
        (name, trigger)
        for name, text in _workflows().items()
        for trigger in ("pull_request_target", "workflow_run")
        if re.search(rf"^\s*{trigger}\s*:", text, re.MULTILINE)
    ]
    assert not offending, offending


def test_the_reviewer_runs_only_when_the_gate_says_so_and_says_why() -> None:
    code = _workflows()["claude-review.yml"]
    review = code[code.index("\n  review:") :]
    assert "needs: gate" in review
    assert "if: needs.gate.outputs.review == 'true'" in review
    gate = code[code.index("\n  gate:") : code.index("\n  review:")]
    # By name, not `head.repo.fork`: a deleted fork's head repository is null,
    # and null reads as "not a fork".
    assert "github.event.pull_request.head.repo.full_name" in gate
    assert "head.repo.fork" not in code
    assert "secrets.CLAUDE_CODE_OAUTH_TOKEN != ''" in gate
    assert gate.count('>> "$GITHUB_STEP_SUMMARY"') >= 2, "both branches report"
    assert "show_full_output: false" in review


def test_the_uv_cache_is_on_wherever_uv_is_set_up() -> None:
    settings = [
        (name, value.strip())
        for name, text in _workflows().items()
        for value in re.findall(r"enable-cache:(.*)", text)
    ]
    assert settings, "no workflow sets up uv"
    assert all(value == "true" for _, value in settings), settings
