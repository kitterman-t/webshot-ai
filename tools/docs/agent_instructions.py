#!/usr/bin/env python3
"""Keep `AGENTS.md` byte-identical to `CLAUDE.md`.

Different tools look for different filenames, and the rules in CLAUDE.md are
the ones that stop this repository shipping a changed capture or a leaked
credential — so an agent that reads the other name must get the same document,
not an older paraphrase of it. Two files that "should say the same thing" drift
silently, and the drift is invisible precisely where it matters: the reader who
needs the rule is the reader who cannot tell they are holding the stale copy.

So the mirror is generated, like `docs/guide/cli.md`, and a test compares it.

    uv run python tools/docs/agent_instructions.py            # check, exit 1 on drift
    uv run python tools/docs/agent_instructions.py --write    # copy CLAUDE.md over AGENTS.md

`CLAUDE.md` is the source. `--write` overwrites `AGENTS.md`, so it prints the
diff it applied: an edit made in the mirror by mistake is work about to be
destroyed, and it should be destroyed loudly.

The comparison is on **bytes**, not decoded text, and the claim in both files
says bytes. A text comparison would read `\r\n` and `\n` as the same document
and the files would still differ on disk — and a check that reports a
difference it then cannot show is CLAUDE.md's "print what the check saw, not a
sentence about what it wanted". Hence `differences()`: when the unified diff
comes back empty, the files differ somewhere the diff cannot render, and it
says where instead.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The document that is edited. The mirror is a copy of this one, never the
#: other way round: a script that guessed the direction — by timestamp, say —
#: would silently pick the wrong one on a fresh checkout, where every file has
#: the same mtime.
SOURCE = REPO_ROOT / "CLAUDE.md"
MIRROR = REPO_ROOT / "AGENTS.md"


def differences(source: bytes, mirror: bytes) -> list[str]:
    """The lines a reader needs in order to fix the drift; empty when in sync."""
    if source == mirror:
        return []

    diff = list(
        difflib.unified_diff(
            source.decode("utf-8", "replace").splitlines(),
            mirror.decode("utf-8", "replace").splitlines(),
            fromfile=SOURCE.name,
            tofile=MIRROR.name,
            lineterm="",
        )
    )
    if diff:
        return diff

    # Equal line by line and still not equal as bytes: line endings, a missing
    # final newline, or trailing whitespace. Printing "they differ" here and
    # stopping would leave the reader with nothing to act on, so say where.
    # `strict=False` on purpose: one file being a prefix of the other is a real
    # case here (a missing final newline), and the scan wants the common span.
    # The default is then the answer rather than a shrug — no byte differs
    # inside that span, so the difference starts where the shorter file ends.
    offset = next(
        (i for i, (a, b) in enumerate(zip(source, mirror, strict=False)) if a != b),
        min(len(source), len(mirror)),
    )
    return [
        f"{SOURCE.name} and {MIRROR.name} have the same lines but not the same "
        "bytes — line endings, trailing whitespace, or a missing final newline.",
        f"  first difference at byte {offset}: "
        f"{SOURCE.name}={source[offset : offset + 1]!r} "
        f"{MIRROR.name}={mirror[offset : offset + 1]!r}",
        f"  lengths: {SOURCE.name}={len(source)} {MIRROR.name}={len(mirror)}",
    ]


def _display(path: Path) -> str:
    """Repository-relative where that reads better, absolute where it must.

    `relative_to` raises on a path outside the tree, which the tests exercise
    and a relocated checkout could produce — and a crash inside the reporting
    path would replace the diagnostic with a traceback about the diagnostic.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check() -> list[str]:
    """Compare the two files on disk. Empty means in sync; anything else fails.

    A missing file is a failure and not a skip. `AGENTS.md` absent means every
    tool that looks for that name gets no rules at all, which is the loudest
    version of the drift this exists to catch — and a check that passes because
    it could not find its input is CLAUDE.md: absence must not read as success,
    which gates in this repository have already done five times.
    """
    missing = [_display(p) for p in (SOURCE, MIRROR) if not p.is_file()]
    if missing:
        return [f"missing: {', '.join(missing)}"]
    return differences(SOURCE.read_bytes(), MIRROR.read_bytes())


def write() -> list[str]:
    """Copy the source over the mirror, returning the diff it applied."""
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    source = SOURCE.read_bytes()
    applied = check() if MIRROR.is_file() else [f"created {MIRROR.name}"]
    MIRROR.write_bytes(source)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="copy CLAUDE.md over AGENTS.md instead of checking",
    )
    args = parser.parse_args(argv)

    if args.write:
        applied = write()
        if applied:
            print(f"{MIRROR.name} rewritten from {SOURCE.name}:")
            print("\n".join(applied))
        else:
            print(f"{MIRROR.name} was already in sync.")
        return 0

    complaints = check()
    if complaints:
        print("\n".join(complaints))
        print()
        print(
            f"{SOURCE.name} and {MIRROR.name} are one document under two names. "
            "Edit CLAUDE.md, then:"
        )
        print("  uv run python tools/docs/agent_instructions.py --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
