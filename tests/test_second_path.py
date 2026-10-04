"""A failed git call in `second_path.py` names itself.

On 2026-09-28 a full `verify.py` run failed the second-path step in 0.2 s with
`could not stand up a second checkout:` and nothing after the colon: the
message printed git's stderr and nothing else, and stderr was empty. The step
passed when re-run alone, so the cause is unknown — which is exactly why the
next occurrence has to identify the command and its status by itself.

No git runs here. `subprocess.run` is replaced for each test, so the failure
is whatever the test plants, and the shared repository is never touched.
"""

from __future__ import annotations

import shlex
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden import second_path  # noqa: E402

STASH_SHA = "0123456789abcdef0123456789abcdef01234567"


def _fake_git(
    fail: tuple[str, ...], exc: Callable[[list[str]], subprocess.CalledProcessError]
) -> tuple[list[list[str]], Callable[..., subprocess.CompletedProcess[str]]]:
    """A `subprocess.run` whose git call starting with `fail` raises `exc`."""
    calls: list[list[str]] = []

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = list(command)
        calls.append(command)
        if tuple(command[1 : 1 + len(fail)]) == fail:
            raise exc(command)
        stdout = STASH_SHA if command[1:3] == ["stash", "create"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return calls, run


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(second_path.tempfile, "mkdtemp", lambda prefix: str(root))
    return root


def _silent(returncode: int) -> Callable[[list[str]], subprocess.CalledProcessError]:
    return lambda command: subprocess.CalledProcessError(
        returncode, command, output="", stderr=""
    )


def test_a_silent_stash_failure_names_its_command_and_status(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, run = _fake_git(("stash", "create"), _silent(128))
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    out = capsys.readouterr().out
    assert "snapshotting the working tree failed" in out
    assert "command: git stash create\n" in out
    assert "exit code: 128\n" in out
    assert "stdout: (empty)\n" in out
    assert "stderr: (empty)\n" in out
    # The step that failed is the last one attempted: no worktree was added
    # from a state that was never computed.
    assert not any(call[1:3] == ["worktree", "add"] for call in calls)


def test_a_silent_worktree_failure_names_its_command_and_status(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, run = _fake_git(("worktree", "add"), _silent(128))
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    out = capsys.readouterr().out
    # Quoted as the report quotes it: on Windows the path has backslashes,
    # which `shlex` wraps in single quotes.
    checkout = shlex.quote(str(workspace / "checkout"))
    assert "adding the worktree failed" in out
    assert "snapshotting" not in out
    assert f"command: git worktree add --detach {checkout} {STASH_SHA}\n" in out
    assert "exit code: 128\n" in out
    assert "stderr: (empty)\n" in out


def test_a_signal_is_named_rather_than_left_as_a_negative_number(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # SIGTERM because every platform's `signal` module defines it; Windows has
    # no SIGKILL, and a test that skips there proves nothing there.
    code = -int(signal.SIGTERM)
    _, run = _fake_git(("worktree", "add"), _silent(code))
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    assert f"exit code: {code} (killed by SIGTERM)\n" in capsys.readouterr().out


def test_a_signal_this_platform_cannot_name_is_still_called_a_signal() -> None:
    # 200 is no signal on any platform, so this is the branch a POSIX-only
    # name like SIGKILL takes wherever the `signal` module lacks it.
    exc = subprocess.CalledProcessError(-200, ["git", "stash", "create"], "", "")
    assert "exit code: -200 (killed by a signal)" in second_path._git_failure("x", exc)


def test_what_git_did_say_is_still_printed_whole(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = (
        "fatal: Unable to create '.git/index.lock': File exists.\n\nAnother git process"
    )
    _, run = _fake_git(
        ("worktree", "add"),
        lambda command: subprocess.CalledProcessError(
            128, command, output="Preparing worktree\n", stderr=lock
        ),
    )
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    out = capsys.readouterr().out
    assert "stdout: Preparing worktree\n" in out
    assert "stderr: fatal: Unable to create '.git/index.lock': File exists.\n" in out
    assert "    Another git process\n" in out


def test_the_command_and_status_survive_verify_keeping_only_the_tail(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `verify.py` shows the last 25 lines of a failed step. However much git
    # wrote, the lines that identify the call must be among them.
    noise = "\n".join(f"hint: line {n}" for n in range(60))
    _, run = _fake_git(
        ("worktree", "add"),
        lambda command: subprocess.CalledProcessError(
            128, command, output="", stderr=noise
        ),
    )
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    tail = "\n".join(capsys.readouterr().out.strip().splitlines()[-25:])
    assert "command: git worktree add --detach" in tail
    assert "exit code: 128" in tail


def test_a_failure_carrying_untracked_files_is_reported_too(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The same boundary as the two calls above: `ls-files` used to escape as a
    # traceback whose message omits both streams.
    _, run = _fake_git(("ls-files",), _silent(128))
    monkeypatch.setattr(second_path.subprocess, "run", run)

    assert second_path.main([]) == 1

    out = capsys.readouterr().out
    assert "listing the untracked files to carry failed" in out
    assert (
        "command: git ls-files --others --exclude-standard tests/golden tests/fixtures\n"
        in out
    )


def test_a_stream_that_was_never_captured_is_not_called_empty() -> None:
    exc = subprocess.CalledProcessError(1, ["git", "status"])
    report = second_path._git_failure("checking", exc)
    assert "stdout: (not captured)" in report
    assert "(empty)" not in report
