"""The weekly Windows run reports what it measured (docs/09 P10-24).

For six weeks it reported green: four runs failed 107 or 150 tests and two
never started, because a job-level `continue-on-error` made the job's failure the
run's success, and the capture step was skipped behind the failing tests. The
first tests hold the workflow to the fix; the rest hold
`tools/ci/junit_summary.py`, which writes the run's job summary. Its fixture is
a report pytest itself wrote: one pass, one failure, one setup error, and two
skips with the same reason.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci.junit_summary import main  # noqa: E402
from tools.ci.profile_acl import main as check_profile  # noqa: E402

from webshot import winacl  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "ci" / "junit_mixed.xml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "windows-smoke.yml"


def _code(path: Path) -> str:
    """The workflow without its comments, which quote what they replaced."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_a_failure_in_the_windows_run_fails_the_run() -> None:
    """A scheduled run blocks nothing; `continue-on-error` there only hides."""
    code = _code(WORKFLOW)
    assert "schedule:" in code, "the premise: this workflow runs on a schedule"
    assert "pull_request" not in code, "and on no pull request, so it blocks none"
    assert "continue-on-error" not in code


def test_the_capture_is_measured_even_when_unit_tests_fail() -> None:
    code = _code(WORKFLOW)
    capture = code[code.index("- name: Capture a fixture") :]
    capture = capture[: capture.index("run:")]
    assert "!cancelled()" in capture and "steps.tests" not in capture, capture


def test_the_telemetry_events_are_counted_whatever_else_failed() -> None:
    """docs/09 P10-26: the only measurement of what WebShot's Windows call does."""
    code = _code(WORKFLOW)
    step = code[code.index("- name: Count onnxruntime's telemetry events") :]
    condition = step[: step.index("run:")]
    assert "!cancelled()" in condition and "steps.install.outcome" in condition
    assert "steps.tests" not in condition and "steps.capture" not in condition
    assert "tools/ci/ort_etw.py --out out/etw" in step
    assert '--summary "$GITHUB_STEP_SUMMARY"' in step


def test_the_summary_reads_the_report_the_tests_write() -> None:
    code = _code(WORKFLOW)
    assert "--junitxml=out/pytest.xml" in code
    assert "tools/ci/junit_summary.py out/pytest.xml" in code
    assert '>> "$GITHUB_STEP_SUMMARY"' in code


def test_every_outcome_is_counted_and_every_failure_listed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([str(FIXTURE), "--title", "Unit tests (Windows)"]) == 0
    out = capsys.readouterr().out
    assert "**1 failed, 1 errors, 1 passed, 2 skipped** — 5 test cases" in out
    assert "| `test_sample::test_fails` | failed |" in out
    assert "| `test_sample::test_errors_in_setup` | error |" in out
    assert "O_NOFOLLOW" in out
    # A `|` in a message would otherwise end the table cell early.
    assert "exit 5 \\| piped" in out
    assert "| tesseract is not installed | 2 |" in out


@pytest.mark.parametrize(
    ("content", "said"),
    [
        (None, "No report at"),
        ("<testsuites><testsuite", "Unreadable report"),
        ('<testsuites><testsuite tests="0"/></testsuites>', "no test cases"),
    ],
    ids=["missing", "truncated", "empty"],
)
def test_a_report_that_measured_nothing_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None, said: str
) -> None:
    """Absence is not a clean run: each of these is pytest not finishing."""
    report = tmp_path / "pytest.xml"
    if content is not None:
        report.write_text(content, encoding="utf-8")
    assert main([str(report)]) == 1
    assert said in capsys.readouterr().out


def test_the_installed_pytest_writes_what_this_reads(tmp_path: Path) -> None:
    """The fixture pins one pytest's format; this reads the current one's."""
    (tmp_path / "test_probe.py").write_text(
        "import pytest\n"
        "def test_ok(): pass\n"
        "def test_bad(): assert False, 'boom'\n"
        "@pytest.mark.skip(reason='not here')\n"
        "def test_skip(): pass\n",
        encoding="utf-8",
    )
    report = tmp_path / "out.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report}",
            "test_probe.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "ci" / "junit_summary.py"),
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "**1 failed, 0 errors, 1 passed, 1 skipped**" in completed.stdout
    assert "| not here | 1 |" in completed.stdout


# --------------------------------------------------------------------------- #
# The owner-only check against a profile Chromium wrote (docs/09 P10-25)
# --------------------------------------------------------------------------- #


def test_the_profile_chromium_wrote_is_what_gets_checked() -> None:
    """Two captures into one profile, then the check over what they left."""
    code = _code(WORKFLOW)
    profile = code[code.index("- name: Capture twice with an authentication profile") :]
    profile = profile[: profile.index("- name: Check every entry")]
    assert "!cancelled()" in profile and "steps.tests" not in profile, profile
    assert "for run in 1 2" in profile and "--auth-profile out/profile" in profile
    assert "tools/ci/profile_acl.py out/profile" in code


def _profile_tree(root: Path, files: int) -> Path:
    (root / "Default").mkdir(parents=True)
    for index in range(files):
        (root / "Default" / f"entry-{index}").write_bytes(b"")
    return root


@pytest.fixture
def owner_only(monkeypatch: pytest.MonkeyPatch) -> dict[Path, winacl.Security]:
    user = "S-1-5-21-1-2-3-1001"
    clean = winacl.Security(
        owner=user,
        dacl=tuple(
            winacl.Ace(kind=0, flags=0x3, mask=0x1F01FF, sid=sid)
            for sid in (user, winacl.SYSTEM, winacl.ADMINISTRATORS)
        ),
    )
    exceptions: dict[Path, winacl.Security] = {}
    monkeypatch.setattr(winacl, "current_user_sid", lambda: user)
    monkeypatch.setattr(
        winacl, "read_security", lambda path: exceptions.get(path, clean)
    )
    return exceptions


def test_a_profile_chromium_wrote_privately_passes_and_says_what_it_saw(
    tmp_path: Path,
    owner_only: dict[Path, winacl.Security],
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile_tree(tmp_path / "profile", files=20)
    assert check_profile([str(profile)]) == 0
    out = capsys.readouterr().out
    assert "22 entries under" in out
    assert "22  owner: you" in out
    assert "every entry is owner-only" in out


def test_an_empty_profile_measured_nothing_and_fails(
    tmp_path: Path,
    owner_only: dict[Path, winacl.Security],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing in it passes any ACL check; that is not a measurement."""
    profile = tmp_path / "profile"
    profile.mkdir()
    assert check_profile([str(profile)]) == 1
    assert "not a profile Chromium wrote" in capsys.readouterr().out
    assert check_profile([str(tmp_path / "absent")]) == 1


def test_an_exposed_entry_fails_the_run_and_names_the_fix(
    tmp_path: Path,
    owner_only: dict[Path, winacl.Security],
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile_tree(tmp_path / "profile", files=20)
    cookies = profile / "Default" / "entry-3"
    owner_only[cookies] = winacl.Security(
        owner="S-1-5-21-1-2-3-1001",
        dacl=(winacl.Ace(kind=0, flags=0, mask=0x120089, sid="S-1-1-0"),),
    )
    assert check_profile([str(profile)]) == 1
    out = capsys.readouterr().out
    assert f"{cookies} grants Everyone (S-1-1-0) access" in out
    assert "remedy: `icacls" in out


def test_the_entries_a_program_granted_to_someone_else_are_named(
    tmp_path: Path,
    owner_only: dict[Path, winacl.Security],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Counts say an ACE exists; only the listing says which files carry it."""
    profile = _profile_tree(tmp_path / "profile", files=20)
    user = "S-1-5-21-1-2-3-1001"
    capability = "S-1-15-3-1024-1-2-3"
    network = profile / "Default" / "entry-7"
    inherited = profile / "Default" / "entry-8"
    owner_only[network] = winacl.Security(
        owner=user,
        dacl=(winacl.Ace(kind=0, flags=0x0, mask=0x1F01FF, sid=capability),),
    )
    owner_only[inherited] = winacl.Security(
        owner=user,
        dacl=(winacl.Ace(kind=0, flags=0x10, mask=0x1F01FF, sid=capability),),
    )
    assert check_profile([str(profile)]) == 0
    out = capsys.readouterr().out
    listing = out[out.index("set explicitly") :]
    assert f"flags=0x00: {capability}" in listing
    assert str(Path("Default") / "entry-7") in listing
    assert "entry-8" not in listing, "an inherited ACE is not one a program set"
