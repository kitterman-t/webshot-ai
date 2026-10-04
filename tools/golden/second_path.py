#!/usr/bin/env python3
"""Re-check the golden corpus from a *different absolute path*.

A recorded golden can depend on where the repository happens to live, and a
local check can never notice: it compares this checkout against goldens
recorded from this checkout, so any path-dependent value agrees by
construction. `verify.py` reporting 8/8 is true and uninformative for that
class — which is precisely the round-trip `verify.py` was written to prevent
(docs/09 P6-2).

It has happened twice. P6-3 was a byte count that moved with the recording
machine. P7-13 was a chunk's own SHA-256, taken over text ending
`file://<absolute path>/tests/fixtures/…` — byte-identical text, different
hash, green locally and red on CI.

So: stand the working tree up somewhere else and run the same check. Anything
that disagrees between two paths is something the corpus should not be pinning.

    uv run python tools/golden/second_path.py

What it checks is the working tree, not just HEAD: uncommitted changes are
carried over through `git stash create`, and untracked files under `tests/` are
copied, because re-recording a golden is exactly the moment this matters and a
new case is often still untracked.
"""

from __future__ import annotations

import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Untracked files worth carrying across. A freshly recorded case is untracked,
#: and checking a second path without it would report a missing golden rather
#: than the path-dependence this exists to find.
CARRIED = ("tests/golden", "tests/fixtures")


def _git(*arguments: str, cwd: Path = REPO_ROOT) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _stream(text: str | None) -> str:
    # An empty stream is a finding in its own right — the only time this
    # message has failed to explain itself, stderr was empty — so it is named
    # rather than printed as nothing, and kept distinct from "never captured".
    if text is None:
        return "(not captured)"
    if not text.strip():
        return "(empty)" if not text else repr(text)
    return text.strip().replace("\n", "\n    ")


def _git_failure(step: str, exc: subprocess.CalledProcessError) -> str:
    """What the failed git call saw, not a sentence about what we wanted.

    Printing stderr alone once produced `could not stand up a second checkout:`
    with nothing after the colon, naming neither the command nor its status.
    The command and exit code come last because `verify.py` keeps only the
    tail of a failed step's output, and they are what identifies the step.
    """
    command = exc.cmd if isinstance(exc.cmd, str) else shlex.join(exc.cmd)
    status = str(exc.returncode)
    if exc.returncode < 0:
        # A negative status is a signal, which also explains empty streams.
        try:
            status += f" (killed by {signal.Signals(-exc.returncode).name})"
        except ValueError:
            status += " (killed by a signal)"
    return "\n".join(
        (
            f"could not stand up a second checkout: {step} failed",
            f"  stdout: {_stream(exc.stdout)}",
            f"  stderr: {_stream(exc.stderr)}",
            f"  command: {command}",
            f"  exit code: {status}",
        )
    )


def _state_to_check() -> str:
    """A commit-ish holding the working tree, without disturbing it.

    `git stash create` writes the commit objects and returns the hash — it does
    *not* touch the index, the working tree, or the stash list. On a clean tree
    it prints nothing, and HEAD is already what we want.
    """
    return _git("stash", "create") or "HEAD"


def _carry_untracked(destination: Path) -> int:
    carried = 0
    listing = _git("ls-files", "--others", "--exclude-standard", *CARRIED)
    for name in listing.splitlines():
        source = REPO_ROOT / name
        if not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        carried += 1
    return carried


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or [])
    workspace = Path(tempfile.mkdtemp(prefix="webshot-second-path-"))
    # A different absolute path is the whole point, so the name is deliberately
    # not derived from the repository's own.
    checkout = workspace / "checkout"
    try:
        # One git call per try, so a failure names the call that failed.
        try:
            state = _state_to_check()
        except subprocess.CalledProcessError as exc:
            print(_git_failure("snapshotting the working tree", exc))
            return 1
        try:
            _git("worktree", "add", "--detach", str(checkout), state)
        except subprocess.CalledProcessError as exc:
            print(_git_failure("adding the worktree", exc))
            return 1
        try:
            carried = _carry_untracked(checkout)
        except subprocess.CalledProcessError as exc:
            print(_git_failure("listing the untracked files to carry", exc))
            return 1
        print(f"checking the golden corpus from {checkout} ({carried} untracked)")
        completed = subprocess.run(
            [sys.executable, "tools/golden/check.py", *arguments],
            cwd=checkout,
            check=False,
        )
        if completed.returncode != 0:
            print(
                "\nThe corpus differs when the repository sits somewhere else, so "
                "something recorded depends on this checkout's path. Look for a "
                "value derived from text the harness normalizes — a digest, a "
                "length — rather than for a path in the output; the path itself "
                "is masked already."
            )
        return completed.returncode
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(checkout)],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
