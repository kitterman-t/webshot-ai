"""One capture at a time per set of published paths (docs/04-spec.md §5.1).

Two runs aimed at the same `--output` do not conflict over their *temporary*
files — those carry the process id — but they publish into the same PDF path
and the same `.ai` directory, and the last one to finish wins. What it wins is
not necessarily either capture: the PDF can come from one run and the bundle
from the other, which produces a manifest whose `pdf.sha256` does not match the
file beside it. The spec calls for a per-output lockfile and exit 2.

**The exclusion is the kernel's, not ours.** The first version of this module
claimed the lock by creating a file with `O_EXCL` and writing the owner's pid
into it, then judged a lock stale by asking whether that pid was still alive.
Every part of that was a race, and a review proved three of them (docs/09
P5-10): the file exists for a moment *before* the pid is written, so a
concurrent run reads an empty file, concludes the owner is dead, and takes a
live lock; the stale-takeover `unlink` was unconditional, so it could delete a
lock a third process had just claimed; and a lock file containing a pid too
large for `pid_t` made `os.kill` raise `OverflowError`, which is not an
`OSError`, so it escaped the handler and made the output path permanently
uncapturable. On Windows the probe was worse than any of them — CPython's
`os.kill` special-cases only the two console-control signals, so signal 0
becomes `TerminateProcess` and the liveness *check* would have killed the
holder.

`flock` has none of that. The lock lives on the open file description, the
kernel releases it when the process dies however it dies, and there is no
staleness to reason about and no pid to parse. The pid is still written into
the file, but only so the refusal can name who holds it.

**The file is deliberately not deleted on release**, whether the run
succeeded or failed. Unlinking it would reintroduce the race the rest of this
module exists to avoid: another process can already hold an open descriptor to
that inode, so a new run would create a *fresh* file and lock that instead, and
both would believe they hold it. What stays behind is a dotfile beside each
published path (`.report.pdf.lock`, `.report.ai.lock`) holding the pid of the
last run that took it. It holds no lock, because the kernel released that when
the run ended however it ended, so a leftover never blocks the next run, which
reuses it, and there is nothing to clean up after a failure.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from types import TracebackType

from .errors import UsageError

#: Longest basename most filesystems accept. The lock adds a dot and a suffix
#: to the name it guards, so a name near the limit would make the *lock* fail
#: on a capture that used to work — a regression a review caught before it
#: shipped (docs/09 P5-10).
MAX_NAME_BYTES = 255

#: POSIX only. Windows has no `os.O_NOFOLLOW`, and naming it unguarded made
#: every capture there exit 5 before it started, for as long as a weekly
#: Windows run reported green (docs/09 P10-24). Where it is 0, `_open_lock`
#: gets the same guarantee another way.
_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)


def lock_path(target: Path) -> Path:
    """The lock beside one published path. Hidden, and named after what it guards.

    Shortened by digest when the decorated name would not fit: a truncated stem
    plus eight hex characters of the full name keeps it unique in practice
    without inheriting the guarded name's length.
    """
    name = f".{target.name}.lock"
    if len(name.encode("utf-8")) <= MAX_NAME_BYTES:
        return target.with_name(name)
    from hashlib import sha256

    digest = sha256(target.name.encode("utf-8")).hexdigest()[:8]
    room = MAX_NAME_BYTES - len(f"..{digest}.lock".encode())
    stem = target.name.encode("utf-8")[:room].decode("utf-8", "ignore")
    return target.with_name(f".{stem}.{digest}.lock")


#: Where the Windows byte lock sits: past anything the pid write reaches.
#: Windows byte-range locks are mandatory, so the byte-0 lock this used to take
#: made the pid unreadable to the very run it was written for, and a refusal
#: there could never name the holder (docs/09 P10-24). Windows allows a lock
#: past the end of the file, so the file stays as small as the pid.
_WINDOWS_LOCK_OFFSET = 1 << 20


def _take(descriptor: int) -> None:
    """Take an exclusive, non-blocking lock on an open file, or raise `OSError`."""
    if sys.platform == "win32":  # pragma: no cover - POSIX CI
        import msvcrt

        # `msvcrt.locking` locks from the current offset, and the pid is then
        # written from 0, so the offset goes back whether or not this locked.
        os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _give_back(descriptor: int) -> None:
    """Release the lock and close the file.

    Closing is the release on POSIX. Windows releases a closed handle's byte
    locks too, but in a time that "depends upon available system resources"
    (the `LockFile` docs), and the `_locking` docs ask for the region to be
    unlocked before the close — otherwise a run started straight after this
    one can find the lock still held.
    """
    if sys.platform == "win32":  # pragma: no cover - POSIX CI
        import msvcrt

        try:
            os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # The close below still releases it, later.
    os.close(descriptor)


def _open_lock(path: Path) -> int:
    """Open or create the lock file without ever opening it through a symlink.

    **Where `O_NOFOLLOW` exists** the kernel refuses a symlink at `path`, and
    this is the one `os.open` it always was (docs/09 P8-12).

    **Where it does not** (Windows), three steps give the same answer:

    * create with `O_EXCL` only when nothing, not even a dangling link, has
      the name — so a planted link is never opened with `O_CREAT`, which
      would create an empty file at its target;
    * otherwise open without `O_CREAT`, so a dangling link fails instead;
    * then refuse the descriptor unless the name is not a link and names the
      very file the descriptor holds. A link to a writable file is opened —
      nothing is written through it yet — and closed unwritten.

    What remains is a race: a dangling link planted between the `lexists`
    and the `O_EXCL` open can still leave an empty file at its target, and the
    third step refuses the run. Planting one on Windows needs the
    create-symbolic-link privilege, which by default only an administrator
    holds, or Developer Mode (docs/04-spec.md §4.1).
    """
    if _NOFOLLOW:
        return os.open(path, os.O_CREAT | os.O_RDWR | _NOFOLLOW, 0o600)
    if not os.path.lexists(path):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
    descriptor = os.open(path, os.O_RDWR)
    try:
        named = os.lstat(path)
        if stat.S_ISLNK(named.st_mode):
            raise OSError(errno.EPERM, f"its lock file {path} is a symbolic link")
        if not os.path.samestat(named, os.fstat(descriptor)):
            raise OSError(
                errno.EPERM, f"its lock file {path} was replaced while it was opened"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


class OutputLock:
    """Hold the lock for one or more published paths, or refuse the run.

    More than one path because a capture publishes more than one artifact, and
    `--ai-bundle-dir` can point the bundle somewhere unrelated to the PDF: two
    runs writing different PDFs into one shared bundle directory would take
    different locks and race on the bundle anyway (docs/09 P5-10). Acquisition
    is sorted and non-blocking, so a partial overlap between two runs is a
    refusal rather than a deadlock.
    """

    __slots__ = ("_descriptors", "targets")

    def __init__(self, *targets: Path) -> None:
        #: Sorted and de-duplicated: a stable order means two runs contending
        #: for the same pair cannot each hold one of them.
        self.targets = sorted(set(targets))
        self._descriptors: list[int] = []

    def acquire(self) -> None:
        try:
            for target in self.targets:
                self._claim(target)
        except BaseException:
            self.release()
            raise

    def _claim(self, target: Path) -> None:
        path = lock_path(target)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Never through a symlink: the lock path is predictable and the
            # output directory may be writable by another local user — `/tmp`
            # is the obvious case. Pre-created as a symlink, an ordinary open
            # follows it, and the truncate-and-write below then destroys
            # whatever it pointed at and replaces the contents with our pid. A
            # capture aimed at a shared directory could therefore corrupt an
            # unrelated file the WebShot user happens to be able to write
            # (docs/09 P8-12).
            descriptor = _open_lock(path)
        except OSError as exc:
            # A destination WebShot cannot even create a lock beside is one it
            # cannot publish to, and that is decided before anything runs —
            # spec §3 code 2. Failing here rather than after a sixty-second
            # capture is the point; the message names the destination, not the
            # hidden lock file, because the destination is what the caller
            # chose (docs/09 P5-10).
            raise UsageError(
                f"{target} cannot be published to: {exc.strerror or exc}"
            ) from exc
        # `_open_lock` refuses a symlink; this refuses everything else that is
        # not a plain file, and it is checked on the descriptor we hold rather
        # than on the path, so nothing can be swapped underneath between the
        # two. A FIFO would block the write below forever.
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(errno.EPERM, "not a regular file")
        except OSError as exc:
            os.close(descriptor)
            raise UsageError(
                f"{target} cannot be published to: its lock file {path} is not a "
                "regular file. Remove it and try again."
            ) from exc
        try:
            _take(descriptor)
        except OSError:
            owner = _read_owner(descriptor)
            os.close(descriptor)
            held_by = f" (pid {owner})" if owner else ""
            # Deliberately naming no flag: the MCP server holds this same lock,
            # and no MCP parameter selects an output path (spec §6.8b), nor may
            # a client delete a file (§6.8a). "Try again shortly" is the one
            # instruction both callers can act on.
            # Deliberately not advising deletion. This message is reached only
            # after the kernel confirmed another process holds the lock, so the
            # inode is live: unlinking it lets the next run create and lock a
            # *different* inode while the original holder keeps publishing, and
            # the advice would hand the reader the exact interleaving the lock
            # exists to prevent. The recorded pid can also be stale during the
            # brief handoff between holders, so "no run is in progress" is not
            # something the file can be read to mean. The lock does not outlive
            # its holder — closing releases it — so there is nothing to clean
            # up either (docs/09 P8-13). The lock *path* is still named — it
            # is the only part of this message that differs between "another
            # run holds it" and "this run collided with its own staged copy",
            # and a test depends on telling those apart (docs/09 P5-10).
            raise UsageError(
                f"Another WebShot run{held_by} is already writing {target.name} "
                f"(lock: {path}). Try again once it finishes, or capture to a "
                "different output path."
            ) from None
        except BaseException:
            os.close(descriptor)
            raise
        # Registered before the metadata is written, not after. `ftruncate` or
        # `write` can fail on a full or failing filesystem, and until the
        # descriptor is in this list `acquire`'s cleanup cannot close it — a
        # CLI process would drop it at exit, but a long-lived MCP server keeps
        # both the descriptor and the kernel lock and refuses every later
        # capture of that destination until it restarts (docs/09 P8-14).
        self._descriptors.append(descriptor)
        # Informational only — the exclusion is the kernel's. Truncate first so
        # a shorter pid cannot leave a longer one's digits behind.
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())

    def release(self) -> None:
        """Drop every lock this instance holds."""
        while self._descriptors:
            _give_back(self._descriptors.pop())

    def __enter__(self) -> OutputLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _read_owner(descriptor: int) -> int:
    """The pid recorded in a lock file, or 0 when it does not say.

    Only ever used to make a refusal more informative. Nothing depends on it
    being right, which is the point: the previous design decided *exclusion*
    from this number and raced with the moment before it was written.
    """
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        return int(os.read(descriptor, 32).decode("utf-8", "ignore").strip() or 0)
    except (OSError, ValueError):
        return 0
