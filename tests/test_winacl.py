"""Owner-only authentication profiles on Windows (docs/04-spec.md §6.3, docs/09 P10-25).

Three layers, tested where each one can run:

- `judge` is pure: every rule the check applies to one ACL, on any platform.
- `find_exposure` and `make_owner_only` walk a real directory tree with the
  Windows reader and writers replaced, so the walk's order, its completeness
  and its failure paths are gated on the POSIX suites too.
- The reader and writers themselves are ctypes calls into advapi32, and only
  a Windows host can run them. Those tests are skipped elsewhere and measured
  by the weekly `windows-smoke.yml` run, which also captures with a real
  profile twice and checks every entry Chromium left in it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from webshot import winacl
from webshot.capture import session
from webshot.errors import UsageError
from webshot.winacl import Ace, Security

USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
OTHER = "S-1-5-21-1111111111-2222222222-3333333333-1002"
EVERYONE = "S-1-1-0"
AUTHENTICATED_USERS = "S-1-5-11"
CAPABILITY = "S-1-15-3-1024-1065365936-1281604716-3511738428-1654721687"

ALLOW, DENY, ALLOW_CALLBACK = 0x0, 0x1, 0x9
OI_CI, INHERITED = 0x3, 0x10
FULL = 0x1F01FF


def grant(sid: str, flags: int = OI_CI, kind: int = ALLOW) -> Ace:
    return Ace(kind=kind, flags=flags, mask=FULL, sid=sid)


#: What `make_owner_only` writes: you, SYSTEM, Administrators, inherited below.
OWNER_ONLY = Security(
    owner=USER,
    dacl=(grant(USER), grant(winacl.SYSTEM), grant(winacl.ADMINISTRATORS)),
)


def with_ace(ace: Ace, owner: str = USER) -> Security:
    assert OWNER_ONLY.dacl is not None
    return Security(owner=owner, dacl=(*OWNER_ONLY.dacl, ace))


# --------------------------------------------------------------------------- #
# judge: the rule, one ACL at a time
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "security",
    [
        OWNER_ONLY,
        Security(owner=winacl.ADMINISTRATORS, dacl=OWNER_ONLY.dacl),
        Security(owner=winacl.SYSTEM, dacl=OWNER_ONLY.dacl),
        with_ace(grant(winacl.CREATOR_OWNER, flags=0x0B)),
        with_ace(grant(winacl.OWNER_RIGHTS)),
        with_ace(grant("S-1-15-2-1")),
        with_ace(grant(CAPABILITY)),
        with_ace(grant(EVERYONE, kind=DENY)),
        Security(owner=USER, dacl=()),
    ],
    ids=[
        "you-system-administrators",
        "owned-by-administrators",
        "owned-by-system",
        "creator-owner",
        "owner-rights",
        "all-application-packages",
        "appcontainer-capability",
        "deny-everyone",
        "empty-dacl-grants-nothing",
    ],
)
def test_what_only_you_system_and_administrators_reach_passes(
    security: Security,
) -> None:
    assert winacl.judge(security, USER) is None


@pytest.mark.parametrize(
    ("security", "kind", "reason"),
    [
        (
            Security(owner=USER, dacl=None),
            "null-dacl",
            "has a NULL DACL, which grants everyone full access",
        ),
        (
            with_ace(grant(EVERYONE)),
            "grant",
            f"grants Everyone ({EVERYONE}) access",
        ),
        (
            with_ace(grant(AUTHENTICATED_USERS, flags=INHERITED)),
            "grant",
            f"grants Authenticated Users ({AUTHENTICATED_USERS}) access (inherited)",
        ),
        (with_ace(grant("S-1-5-32-545")), "grant", "grants Users (S-1-5-32-545)"),
        (with_ace(grant(OTHER)), "grant", f"grants {OTHER} access"),
        # Replaced by the creator's primary group, which is not you.
        (with_ace(grant("S-1-3-1")), "grant", "grants CREATOR GROUP (S-1-3-1)"),
        # An inherit-only ACE grants nothing here and everything to what is
        # created inside, which is where the cookies are.
        (with_ace(grant(EVERYONE, flags=0x0B)), "grant", "grants Everyone"),
        (
            with_ace(Ace(kind=ALLOW_CALLBACK, flags=0, mask=FULL, sid=None)),
            "ace-type",
            "has an ACE of type 0x9, which this check does not read",
        ),
        (
            Security(owner=OTHER, dacl=OWNER_ONLY.dacl),
            "owner",
            f"is owned by {OTHER}, who can change its ACL at will",
        ),
        (
            Security(owner="", dacl=OWNER_ONLY.dacl),
            "owner",
            "is owned by nobody",
        ),
    ],
    ids=[
        "null-dacl",
        "everyone",
        "authenticated-users-inherited",
        "users",
        "another-user",
        "creator-group",
        "inherit-only-everyone",
        "unread-ace-type",
        "another-owner",
        "no-owner",
    ],
)
def test_anyone_else_fails_and_is_named(
    security: Security, kind: str, reason: str
) -> None:
    verdict = winacl.judge(security, USER)
    assert verdict is not None
    assert verdict.kind == kind
    assert reason in verdict.reason


def test_a_deny_ace_does_not_hide_a_grant_after_it() -> None:
    """Skipping deny ACEs must not end the scan: the grant still counts."""
    assert OWNER_ONLY.dacl is not None
    security = Security(
        owner=USER,
        dacl=(grant(EVERYONE, kind=DENY), *OWNER_ONLY.dacl, grant(OTHER)),
    )
    verdict = winacl.judge(security, USER)
    assert verdict is not None and OTHER in verdict.reason


# --------------------------------------------------------------------------- #
# find_exposure: the walk, over a real tree with the reader replaced
# --------------------------------------------------------------------------- #


def profile_tree(root: Path) -> Path:
    """The shape of a Chromium profile, far smaller."""
    network = root / "Default" / "Network"
    network.mkdir(parents=True)
    (network / "Cookies").write_bytes(b"")
    (root / "Default" / "Preferences").write_text("{}", encoding="utf-8")
    (root / "Local State").write_text("{}", encoding="utf-8")
    return root


class Acls:
    """A stand-in for `read_security`: per-path ACLs, and the paths it was asked.

    Anything not given an ACL is owner-only, so a test states only the
    exception it is about.
    """

    def __init__(self) -> None:
        self.table: dict[Path, Security] = {}
        self.read: list[Path] = []

    def __setitem__(self, path: Path, security: Security) -> None:
        self.table[path] = security

    def __call__(self, path: Path) -> Security:
        self.read.append(path)
        return self.table.get(path, OWNER_ONLY)


@pytest.fixture
def acls(monkeypatch: pytest.MonkeyPatch) -> Acls:
    fake = Acls()
    monkeypatch.setattr(winacl, "current_user_sid", lambda: USER)
    monkeypatch.setattr(winacl, "read_security", fake)
    return fake


def test_every_entry_is_read_and_a_clean_tree_passes(
    tmp_path: Path, acls: Acls
) -> None:
    root = profile_tree(tmp_path / "work")
    assert winacl.find_exposure(root) is None
    everything = {root, *root.rglob("*")}
    assert set(acls.read) == everything
    assert len(acls.read) == len(everything)


def test_a_readable_file_inside_a_private_directory_is_found(
    tmp_path: Path, acls: Acls
) -> None:
    """Windows skips traverse checks by default: the file's own ACL decides."""
    root = profile_tree(tmp_path / "work")
    cookies = root / "Default" / "Network" / "Cookies"
    acls[cookies] = with_ace(grant(EVERYONE, flags=0))
    found = winacl.find_exposure(root)
    assert found is not None
    assert found.entry == cookies
    assert str(found) == f"{cookies} grants Everyone ({EVERYONE}) access"
    assert found.remedy == f'`icacls "{cookies}" /remove:g *{EVERYONE}` removes it.'


def test_the_topmost_exposed_entry_is_the_one_named(tmp_path: Path, acls: Acls) -> None:
    root = profile_tree(tmp_path / "work")
    acls[root / "Default"] = with_ace(grant(EVERYONE))
    acls[root / "Default" / "Network" / "Cookies"] = with_ace(
        grant(EVERYONE, flags=INHERITED)
    )
    found = winacl.find_exposure(root)
    assert found is not None and found.entry == root / "Default"


def test_an_inherited_grant_is_not_given_a_remedy_that_cannot_remove_it(
    tmp_path: Path, acls: Acls
) -> None:
    root = profile_tree(tmp_path / "work")
    cookies = root / "Default" / "Network" / "Cookies"
    acls[cookies] = with_ace(grant(EVERYONE, flags=INHERITED))
    found = winacl.find_exposure(root)
    assert found is not None
    assert "/remove:g" not in found.remedy
    assert (
        found.remedy
        == f'`icacls "{cookies}" /reset` gives it the access list it inherits.'
    )


def test_the_profile_directory_itself_is_fixed_by_a_capture(
    tmp_path: Path, acls: Acls
) -> None:
    root = profile_tree(tmp_path / "work")
    acls[root] = with_ace(grant(AUTHENTICATED_USERS, flags=OI_CI | INHERITED))
    found = winacl.find_exposure(root)
    assert found is not None and found.entry == root
    assert "capture run from a terminal" in found.remedy


def test_an_owner_who_is_someone_else_is_not_told_to_run_a_capture(
    tmp_path: Path, acls: Acls
) -> None:
    """A capture rewrites the DACL; it cannot give the directory back to you."""
    root = profile_tree(tmp_path / "work")
    acls[root] = Security(owner=OTHER, dacl=OWNER_ONLY.dacl)
    found = winacl.find_exposure(root)
    assert found is not None
    assert "capture" not in found.remedy
    assert "administrator" in found.remedy


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege there")
def test_a_link_inside_the_profile_is_refused_not_followed(
    tmp_path: Path, acls: Acls
) -> None:
    """What a link leads to is outside the tree the walk vouches for."""
    root = profile_tree(tmp_path / "work")
    outside = tmp_path / "shared"
    outside.mkdir()
    (outside / "Cookies").write_bytes(b"")
    (root / "Default" / "Cache").symlink_to(outside, target_is_directory=True)
    found = winacl.find_exposure(root)
    assert found is not None
    assert found.entry == root / "Default" / "Cache"
    assert "link or junction" in found.reason
    assert outside / "Cookies" not in acls.read


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege there")
def test_the_profile_directory_named_through_a_link_is_followed(
    tmp_path: Path, acls: Acls
) -> None:
    """The caller named it; the rule is about links leading out of it."""
    real = profile_tree(tmp_path / "real")
    link = tmp_path / "work"
    link.symlink_to(real, target_is_directory=True)
    assert winacl.find_exposure(link) is None
    assert link / "Local State" in acls.read


def test_an_acl_that_cannot_be_read_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acls: Acls
) -> None:
    """A check that cannot see an entry has not passed it."""
    root = profile_tree(tmp_path / "work")
    preferences = root / "Default" / "Preferences"
    real_read = winacl.read_security

    def refusing(path: Path) -> Security:
        if path == preferences:
            raise PermissionError(13, "Access is denied", str(path))
        return real_read(path)

    monkeypatch.setattr(winacl, "read_security", refusing)
    found = winacl.find_exposure(root)
    assert found is not None and found.entry == preferences
    assert "could not have its ACL read" in found.reason


def test_an_entry_removed_during_the_walk_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acls: Acls
) -> None:
    """Chromium deletes cache files as it runs; a file that is gone exposes nothing."""
    root = profile_tree(tmp_path / "work")
    preferences = root / "Default" / "Preferences"
    real_read = winacl.read_security

    def vanishing(path: Path) -> Security:
        if path == preferences:
            path.unlink()
            raise FileNotFoundError(2, "The system cannot find the file", str(path))
        return real_read(path)

    monkeypatch.setattr(winacl, "read_security", vanishing)
    assert winacl.find_exposure(root) is None
    assert root / "Local State" in acls.read


# --------------------------------------------------------------------------- #
# make_owner_only: create, repair, or refuse by name
# --------------------------------------------------------------------------- #


class Writers:
    """Stand-ins for the two ACL writers, recording what they were asked."""

    def __init__(
        self, acls: Acls, *, repairs: Callable[[Path], None] | None = None
    ) -> None:
        self.acls = acls
        self.created: list[Path] = []
        self.restricted: list[Path] = []
        self.repairs = repairs

    def create(self, path: Path) -> None:
        path.mkdir()  # FileExistsError when it is there, as CreateDirectoryW's 183
        self.created.append(path)

    def restrict(self, path: Path) -> None:
        self.restricted.append(path)
        if self.repairs is not None:
            self.repairs(path)
        else:
            self.acls[path] = OWNER_ONLY


@pytest.fixture
def writers(monkeypatch: pytest.MonkeyPatch, acls: Acls) -> Writers:
    fake = Writers(acls)
    monkeypatch.setattr(winacl, "create_owner_only_directory", fake.create)
    monkeypatch.setattr(winacl, "restrict_to_owner", lambda path: fake.restrict(path))
    return fake


def test_a_new_profile_is_created_owner_only_and_nothing_is_rewritten(
    tmp_path: Path, writers: Writers
) -> None:
    directory = tmp_path / "profiles" / "work"
    winacl.make_owner_only(directory)
    assert writers.created == [directory]
    assert writers.restricted == []
    assert directory.is_dir()


def test_an_owner_only_profile_is_left_as_it_is(
    tmp_path: Path, writers: Writers
) -> None:
    directory = profile_tree(tmp_path / "work")
    winacl.make_owner_only(directory)
    assert writers.restricted == []


def test_an_inherited_grant_on_the_profile_is_replaced(
    tmp_path: Path,
    writers: Writers,
    acls: Acls,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The Windows `chmod 700`: rewrite the directory's ACL, then check again."""
    directory = profile_tree(tmp_path / "work")
    acls[directory] = with_ace(grant(AUTHENTICATED_USERS, flags=OI_CI | INHERITED))
    with caplog.at_level(logging.WARNING, logger="webshot"):
        winacl.make_owner_only(directory)
    assert writers.restricted == [directory]
    assert "was not owner-only" in caplog.text
    assert "Authenticated Users" in caplog.text


def test_what_the_rewrite_cannot_reach_is_refused_by_name(
    tmp_path: Path, writers: Writers, acls: Acls
) -> None:
    directory = profile_tree(tmp_path / "work")
    cookies = directory / "Default" / "Network" / "Cookies"
    acls[directory] = with_ace(grant(EVERYONE))
    acls[cookies] = with_ace(grant(EVERYONE, flags=0))
    with pytest.raises(UsageError) as failure:
        winacl.make_owner_only(directory)
    message = str(failure.value)
    assert writers.restricted == [directory]
    assert f"{cookies} grants Everyone ({EVERYONE}) access" in message
    assert f'icacls "{cookies}" /remove:g *{EVERYONE}' in message
    assert "§6.3" in message


def test_a_rewrite_that_fails_is_refused_with_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acls: Acls
) -> None:
    directory = profile_tree(tmp_path / "work")
    acls[directory] = Security(owner=USER, dacl=None)

    def denied(path: Path) -> None:
        raise PermissionError(5, "Access is denied", str(path))

    monkeypatch.setattr(
        winacl, "create_owner_only_directory", lambda path: path.mkdir()
    )
    monkeypatch.setattr(winacl, "restrict_to_owner", denied)
    with pytest.raises(UsageError) as failure:
        winacl.make_owner_only(directory)
    message = str(failure.value)
    assert "NULL DACL" in message and "Access is denied" in message


def test_a_file_where_the_profile_should_be_is_not_a_profile(
    tmp_path: Path, writers: Writers
) -> None:
    directory = tmp_path / "work"
    directory.write_text("", encoding="utf-8")
    with pytest.raises(FileExistsError):
        winacl.make_owner_only(directory)


# --------------------------------------------------------------------------- #
# The capture path: which half runs where
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_posix_profile_is_created_0700_whatever_the_umask(tmp_path: Path) -> None:
    """docs/10's "unit test on profile creation", which had not existed (P10-25)."""
    directory = tmp_path / "profiles" / "work"
    previous = os.umask(0o022)
    try:
        session.prepare_profile(directory)
    finally:
        os.umask(previous)
    assert directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_group_readable_posix_profile_is_made_owner_only(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir()
    directory.chmod(0o755)
    session.prepare_profile(directory)
    assert directory.stat().st_mode & 0o777 == 0o700


def test_a_windows_profile_goes_to_the_acl_and_not_to_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chmod(0o700)` there sets the read-only attribute and no ACL (P10-24)."""
    directory = tmp_path / "work"
    made: list[Path] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winacl, "make_owner_only", made.append)
    monkeypatch.setattr(
        Path, "chmod", lambda *args, **kwargs: pytest.fail("chmod on Windows")
    )
    session.prepare_profile(directory)
    assert made == [directory]


# --------------------------------------------------------------------------- #
# The real thing: advapi32 on a Windows host
# --------------------------------------------------------------------------- #

WINDOWS = pytest.mark.skipif(
    sys.platform != "win32", reason="reads and writes a real Windows ACL"
)


def icacls(*arguments: str) -> None:
    subprocess.run(["icacls", *arguments], check=True, capture_output=True)


@WINDOWS
def test_the_user_is_the_one_whoami_names() -> None:
    """Measured against a second source, not against the code under test."""
    output = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sid = re.search(r"S-1-5-[0-9-]+", output)
    assert sid is not None, output
    assert winacl.current_user_sid() == sid.group(0)


@WINDOWS
def test_a_created_directory_carries_exactly_the_owner_only_acl(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "work"
    winacl.create_owner_only_directory(directory)
    user = winacl.current_user_sid()
    security = winacl.read_security(directory)
    assert security.owner == user
    assert security.dacl is not None
    granted = sorted((ace.kind, ace.flags, ace.sid) for ace in security.dacl)
    assert granted == sorted(
        (ALLOW, OI_CI, sid) for sid in (user, winacl.SYSTEM, winacl.ADMINISTRATORS)
    )
    # And what is created inside inherits it, which is the point of OICI.
    (directory / "Local State").write_text("{}", encoding="utf-8")
    inside = winacl.read_security(directory / "Local State")
    assert inside.dacl is not None
    assert {ace.sid for ace in inside.dacl} == {
        user,
        winacl.SYSTEM,
        winacl.ADMINISTRATORS,
    }
    assert all(ace.flags & INHERITED for ace in inside.dacl)
    assert winacl.find_exposure(directory) is None


@WINDOWS
def test_a_readable_file_is_found_and_the_named_remedy_fixes_it(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "work"
    winacl.create_owner_only_directory(directory)
    cookies = directory / "Cookies"
    cookies.write_bytes(b"")
    icacls(str(cookies), "/grant", f"*{EVERYONE}:(R)")
    found = winacl.find_exposure(directory)
    assert found is not None
    assert found.entry == cookies
    assert f"Everyone ({EVERYONE})" in found.reason
    # Run the command the message gives, exactly as given.
    command = re.fullmatch(
        r'`icacls "(.+)" (/remove:g) (\*S-[0-9-]+)` removes it\.', found.remedy
    )
    assert command is not None, found.remedy
    icacls(*command.groups())
    assert winacl.find_exposure(directory) is None


@WINDOWS
def test_an_inherited_grant_is_replaced_down_the_tree(tmp_path: Path) -> None:
    """`SetNamedSecurityInfoW` pushes the new ACL to what inherits from it."""
    directory = tmp_path / "work"
    (directory / "Default").mkdir(parents=True)
    icacls(str(directory), "/grant", f"*{AUTHENTICATED_USERS}:(OI)(CI)(M)")
    (directory / "Default" / "Cookies").write_bytes(b"")
    before = winacl.find_exposure(directory)
    assert before is not None and "Authenticated Users" in before.reason
    winacl.make_owner_only(directory)
    assert winacl.find_exposure(directory) is None
    cookies = winacl.read_security(directory / "Default" / "Cookies")
    assert cookies.dacl is not None
    assert AUTHENTICATED_USERS not in {ace.sid for ace in cookies.dacl}


@WINDOWS
def test_a_grant_set_inside_is_refused_rather_than_stripped(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    winacl.create_owner_only_directory(directory)
    cookies = directory / "Cookies"
    cookies.write_bytes(b"")
    icacls(str(cookies), "/grant", f"*{EVERYONE}:(R)")
    with pytest.raises(UsageError) as failure:
        winacl.make_owner_only(directory)
    assert str(cookies) in str(failure.value)


@WINDOWS
def test_a_junction_inside_the_profile_is_refused(tmp_path: Path) -> None:
    """`mklink /J` needs no privilege, unlike a symbolic link."""
    directory = tmp_path / "work"
    winacl.create_owner_only_directory(directory)
    outside = tmp_path / "shared"
    outside.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(directory / "Cache"), str(outside)],
        check=True,
        capture_output=True,
    )
    found = winacl.find_exposure(directory)
    assert found is not None
    assert found.entry == directory / "Cache"
    assert "link or junction" in found.reason
