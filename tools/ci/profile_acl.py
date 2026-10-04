"""Print what the owner-only check sees in a profile Chromium actually wrote.

`windows-smoke.yml` runs this after two captures with `--auth-profile`: the
first creates the profile, the second checks and reuses what Chromium left in
it, which is the tree an MCP server is handed (docs/09 P10-25). The unit tests
build their trees themselves; this is the one place the check meets the files
and ACLs a real browser makes.

It prints every owner and every ACE the tree carries, with how many entries
carry it, and names each entry that carries an ACE of its own (not inherited)
for anyone but you, SYSTEM and Administrators: those are what some program
set on purpose, and the counts alone cannot say which files they are on. A run
shows what was there, not only a verdict. It fails when
the check finds an exposure, and when the profile holds too little to be one
Chromium wrote: an empty directory passes any ACL check by having nothing in it.

    python tools/ci/profile_acl.py out/profile
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from webshot import winacl

#: A profile after one page load holds far more than this; fewer means the
#: browser did not write it, and a pass would measure nothing.
MIN_ENTRIES = 10

#: Entries listed per explicit ACE; the rest are counted.
MAX_LISTED = 10


def describe(sid: str, user: str) -> str:
    return "you" if sid == user else winacl.name(sid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", type=Path)
    profile = parser.parse_args(argv).profile.resolve()
    if not profile.is_dir():
        print(f"no profile at {profile}")
        return 1
    user = winacl.current_user_sid()
    entries = [profile, *sorted(profile.rglob("*"))]
    seen: Counter[str] = Counter()
    explicit: dict[str, list[Path]] = {}
    ordinary = {user, winacl.SYSTEM, winacl.ADMINISTRATORS}
    for entry in entries:
        try:
            security = winacl.read_security(entry)
        except OSError as exc:
            seen[f"unreadable ({exc.__class__.__name__})"] += 1
            continue
        seen[f"owner: {describe(security.owner, user)}"] += 1
        if security.dacl is None:
            seen["NULL DACL"] += 1
            continue
        for ace in security.dacl:
            who = describe(ace.sid, user) if ace.sid else "(not read)"
            key = f"ACE type={ace.kind:#x} flags={ace.flags:#04x}: {who}"
            seen[key] += 1
            if ace.sid not in ordinary and not ace.flags & winacl.INHERITED_ACE:
                explicit.setdefault(key, []).append(entry.relative_to(profile))
    print(f"{len(entries)} entries under {profile}")
    for key, count in sorted(seen.items()):
        print(f"{count:7d}  {key}")
    for key, where in sorted(explicit.items()):
        print(f"set explicitly, {key}")
        for relative in where[:MAX_LISTED]:
            print(f"    {relative}")
        if len(where) > MAX_LISTED:
            print(f"    ...and {len(where) - MAX_LISTED} more")
    found = winacl.find_exposure(profile)
    print(f"find_exposure: {found if found else 'nothing; every entry is owner-only'}")
    if found is not None:
        print(f"remedy: {found.remedy}")
        return 1
    if len(entries) < MIN_ENTRIES:
        print(f"only {len(entries)} entries: not a profile Chromium wrote")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
