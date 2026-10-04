"""Owner-only directories on Windows, made and checked through their ACLs.

Spec §6.3 makes an authentication profile owner-only, and on POSIX that is
mode `0700` on the directory: nobody else can traverse into it, so nothing
inside it needs checking. Neither half carries over. `chmod(0o700)` on Windows
toggles the read-only attribute and sets no ACL, so a profile the CLI made was
as private as whatever it inherited from its parent; and the MCP server's
mode-bit check read `0o777` on every directory, whatever its ACL said
(docs/09 P10-24). This module is the Windows half (docs/09 P10-25).

**Every entry is checked, not the directory alone.** Windows grants every user
"bypass traverse checking" by default, so a file inside a directory nobody
else can open is still readable by anyone its own ACL admits, given its path —
and a Chromium profile's paths are well known (`Default\\Network\\Cookies`).
A directory whose ACL looked right would prove nothing about the cookies.

**The rule.** An entry is owner-only when its owner and every principal an
allow ACE names is you, SYSTEM or Administrators — the last two can read any
file anyway, through the backup and take-ownership privileges — or one of the
placeholders that resolve to those: CREATOR OWNER (whoever creates a child,
which needs write access here) and OWNER RIGHTS. Two SID families are
accepted because they can only narrow access: an AppContainer's package and
capability SIDs (`S-1-15-2-*`, `S-1-15-3-*`) grant access only where the
process's user is granted it too, the intersection Microsoft documents for
AppContainers, so such an ACE admits no other user. Deny ACEs are skipped,
because they only take access away. Every other ACE type, a NULL DACL, an ACL
that cannot be read, and a link or junction whose far side this walk does not
enter all fail the check: a check that cannot see something has not passed it.

**The only module that calls the Windows security API.** Nothing ctypes
builds leaves it; callers get `Security`, `Ace` and `Exposure`.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import UsageError

LOGGER = logging.getLogger("webshot")

#: ACE types, from winnt.h. Only the plain allow ACE is read; the four deny
#: types take access away and are skipped; anything else fails the check.
ACCESS_ALLOWED_ACE_TYPE = 0x0
ACCESS_DENIED_ACE_TYPE = 0x1
_DENY_TYPES = frozenset({ACCESS_DENIED_ACE_TYPE, 0x6, 0xA, 0xC})

#: ACE flag: the ACE came from the parent, so removing it here does nothing.
INHERITED_ACE = 0x10

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
CREATOR_OWNER = "S-1-3-0"
OWNER_RIGHTS = "S-1-3-4"
_APP_CONTAINER = ("S-1-15-2-", "S-1-15-3-")

#: Names for the SIDs a refusal is most likely to show, so the message says
#: "Everyone" rather than making the reader look up `S-1-1-0`.
_NAMES = {
    "S-1-1-0": "Everyone",
    "S-1-2-0": "LOCAL",
    "S-1-3-1": "CREATOR GROUP",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-7": "ANONYMOUS LOGON",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-32-546": "Guests",
    SYSTEM: "SYSTEM",
    ADMINISTRATORS: "Administrators",
}


@dataclass(frozen=True)
class Ace:
    """One access control entry, as far as the check reads it."""

    kind: int
    flags: int
    mask: int
    #: Read for the allow and deny types only; None for every other layout.
    sid: str | None


@dataclass(frozen=True)
class Security:
    """An entry's owner and DACL."""

    owner: str
    #: None is a NULL DACL, which grants everyone full access — not "no rules".
    dacl: tuple[Ace, ...] | None


@dataclass(frozen=True)
class Exposure:
    """The first entry someone besides you can reach, and why."""

    entry: Path
    reason: str
    remedy: str

    def __str__(self) -> str:
        return f"{self.entry} {self.reason}"


def name(sid: str) -> str:
    known = _NAMES.get(sid)
    return f"{known} ({sid})" if known else sid


@dataclass(frozen=True)
class Verdict:
    """Why one entry is not owner-only."""

    #: "null-dacl", "owner", "ace-type" or "grant": what the remedy depends on.
    kind: str
    reason: str
    ace: Ace | None = None


def judge(security: Security, user: str) -> Verdict | None:
    """Why `security` admits someone other than `user`, or None if it does not.

    Pure, so every branch is tested on any platform; the ACL it is handed is
    what `read_security` returns on Windows.
    """
    if security.dacl is None:
        return Verdict(
            "null-dacl", "has a NULL DACL, which grants everyone full access"
        )
    if security.owner not in (user, SYSTEM, ADMINISTRATORS):
        # The owner can always rewrite the ACL, so an owner who is someone else
        # is access waiting to be granted.
        owner = name(security.owner) if security.owner else "nobody"
        return Verdict("owner", f"is owned by {owner}, who can change its ACL at will")
    permitted = {user, SYSTEM, ADMINISTRATORS, CREATOR_OWNER, OWNER_RIGHTS}
    for ace in security.dacl:
        if ace.kind in _DENY_TYPES:
            continue
        if ace.kind != ACCESS_ALLOWED_ACE_TYPE or ace.sid is None:
            return Verdict(
                "ace-type",
                f"has an ACE of type {ace.kind:#x}, which this check does not read",
                ace,
            )
        if ace.sid in permitted or ace.sid.startswith(_APP_CONTAINER):
            continue
        inherited = " (inherited)" if ace.flags & INHERITED_ACE else ""
        return Verdict("grant", f"grants {name(ace.sid)} access{inherited}", ace)
    return None


def find_exposure(directory: Path) -> Exposure | None:
    """The first entry under `directory`, itself included, that is not owner-only.

    Parents are checked before their children, so the entry named is the
    topmost one: fixing it can fix everything it passed down.
    """
    user = current_user_sid()
    pending = [directory]
    while pending:
        entry = pending.pop()
        try:
            # The directory itself is followed, as the caller named it; an
            # entry inside it is not, because a link leads out of the profile.
            info = os.stat(entry) if entry == directory else os.lstat(entry)
            if entry != directory and _is_link(info):
                return Exposure(
                    entry,
                    "is a link or junction, and this check does not follow it "
                    "out of the profile",
                    "Remove it; a browser profile does not make links.",
                )
            security = read_security(entry)
        except OSError as exc:
            if not os.path.lexists(entry):
                continue  # Removed since it was listed; it exposes nothing now.
            return Exposure(
                entry,
                f"could not have its ACL read ({exc})",
                "Make sure you can read its permissions, then run again.",
            )
        verdict = judge(security, user)
        if verdict is not None:
            return Exposure(
                entry, verdict.reason, _remedy(entry, verdict, entry == directory)
            )
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            children = sorted(os.scandir(entry), key=lambda child: child.name)
        except OSError as exc:
            return Exposure(
                entry,
                f"could not be listed ({exc})",
                "Make sure you can list it, then run again.",
            )
        pending.extend(Path(child.path) for child in reversed(children))
    return None


def _is_link(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _remedy(entry: Path, verdict: Verdict, is_profile: bool) -> str:
    if verdict.kind == "owner":
        return (
            "Use a profile directory you created, or have an administrator "
            "give this one to you."
        )
    if is_profile:
        return (
            "A capture run from a terminal with this profile makes the "
            "directory owner-only."
        )
    ace = verdict.ace
    if verdict.kind == "grant" and ace is not None and not ace.flags & INHERITED_ACE:
        return f'`icacls "{entry}" /remove:g *{ace.sid}` removes it.'
    return f'`icacls "{entry}" /reset` gives it the access list it inherits.'


def make_owner_only(directory: Path) -> None:
    """Create `directory` owner-only, or make an existing one so (spec §6.3).

    The Windows counterpart of `mkdir(mode=0o700)` then `chmod(0o700)`. A new
    directory gets a protected DACL — you, SYSTEM, Administrators, inherited
    by everything created inside it. An existing one that fails the check has
    that DACL applied, which Windows also pushes down to every entry that
    inherits; what it cannot reach — an ACE set on an entry itself, an owner
    who is someone else — is refused by name rather than used.
    """
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        create_owner_only_directory(directory)
    except FileExistsError:
        if not directory.is_dir():
            raise
    found = find_exposure(directory)
    if found is None:
        return
    LOGGER.warning(
        "The authentication profile %s was not owner-only (%s). Replacing its "
        "access list with one that admits only you, SYSTEM and Administrators.",
        directory,
        found,
    )
    try:
        restrict_to_owner(directory)
    except OSError as exc:
        raise UsageError(
            f"The authentication profile {found}, and WebShot could not make it "
            f"owner-only ({exc}). {found.remedy} (docs/04-spec.md §6.3)"
        ) from exc
    found = find_exposure(directory)
    if found is not None:
        raise UsageError(
            f"The authentication profile is not owner-only: {found}. "
            f"{found.remedy} Then run again (docs/04-spec.md §6.3)."
        )


def _owner_only_sddl(user: str) -> str:
    """A protected DACL granting full control to you, SYSTEM and Administrators.

    `P` stops the parent's ACEs from being inherited; `OICI` passes these to
    every file and directory created inside.
    """
    grants = "".join(f"(A;OICI;FA;;;{sid})" for sid in (user, SYSTEM, ADMINISTRATORS))
    return f"D:P{grants}"


if sys.platform == "win32":  # pragma: no cover - measured by windows-smoke.yml
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SDDL_REVISION_1 = 1
    _TOKEN_QUERY = 0x8
    _TOKEN_USER = 1

    class _ACL(ctypes.Structure):
        _fields_ = (
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_ushort),
            ("AceCount", ctypes.c_ushort),
            ("Sbz2", ctypes.c_ushort),
        )

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = (
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        )

    class _ACCESS_ACE(ctypes.Structure):
        """ACCESS_ALLOWED_ACE and ACCESS_DENIED_ACE: the SID starts at SidStart."""

        _fields_ = (
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        )

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        )

    _PVOID = ctypes.POINTER(ctypes.c_void_p)

    _GetNamedSecurityInfoW = _advapi32.GetNamedSecurityInfoW
    _GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        _PVOID,
        _PVOID,
        _PVOID,
        _PVOID,
        _PVOID,
    ]
    _GetNamedSecurityInfoW.restype = wintypes.DWORD

    _SetNamedSecurityInfoW = _advapi32.SetNamedSecurityInfoW
    _SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _SetNamedSecurityInfoW.restype = wintypes.DWORD

    _GetAce = _advapi32.GetAce
    _GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, _PVOID]
    _GetAce.restype = wintypes.BOOL

    _ConvertSidToStringSidW = _advapi32.ConvertSidToStringSidW
    _ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, _PVOID]
    _ConvertSidToStringSidW.restype = wintypes.BOOL

    _ConvertSddl = _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    _ConvertSddl.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        _PVOID,
        ctypes.POINTER(wintypes.ULONG),
    ]
    _ConvertSddl.restype = wintypes.BOOL

    _GetSecurityDescriptorDacl = _advapi32.GetSecurityDescriptorDacl
    _GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        _PVOID,
        ctypes.POINTER(wintypes.BOOL),
    ]
    _GetSecurityDescriptorDacl.restype = wintypes.BOOL

    _OpenProcessToken = _advapi32.OpenProcessToken
    _OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _OpenProcessToken.restype = wintypes.BOOL

    _GetTokenInformation = _advapi32.GetTokenInformation
    _GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _GetTokenInformation.restype = wintypes.BOOL

    _GetCurrentProcess = _kernel32.GetCurrentProcess
    _GetCurrentProcess.argtypes = []
    _GetCurrentProcess.restype = wintypes.HANDLE

    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _LocalFree = _kernel32.LocalFree
    _LocalFree.argtypes = [ctypes.c_void_p]
    _LocalFree.restype = ctypes.c_void_p

    _CreateDirectoryW = _kernel32.CreateDirectoryW
    _CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    _CreateDirectoryW.restype = wintypes.BOOL

    def _failure(code: int, path: Path | None = None) -> OSError:
        # With a winerror, OSError picks the errno and the subclass itself:
        # 2 and 3 become FileNotFoundError, 183 FileExistsError.
        filename = os.fspath(path) if path is not None else None
        return OSError(None, ctypes.FormatError(code), filename, code)

    def _last_failure(path: Path | None = None) -> OSError:
        return _failure(ctypes.get_last_error(), path)

    def _extended(path: Path) -> str:
        """The `\\\\?\\` form, so a deep profile entry is not cut at MAX_PATH."""
        text = os.path.abspath(path)
        if text.startswith("\\\\?\\"):
            return text
        if text.startswith("\\\\"):
            return "\\\\?\\UNC\\" + text[2:]
        return "\\\\?\\" + text

    def _sid_string(sid: int | None) -> str:
        if not sid:
            return ""
        text = ctypes.c_void_p()
        if not _ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _last_failure()
        try:
            return ctypes.wstring_at(text.value or 0)
        finally:
            _LocalFree(text)

    def current_user_sid() -> str:
        """The SID of the user this process runs as."""
        token = wintypes.HANDLE()
        if not _OpenProcessToken(
            _GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise _last_failure()
        try:
            needed = wintypes.DWORD()
            _GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not _GetTokenInformation(
                token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)
            ):
                raise _last_failure()
            user = ctypes.cast(buffer, ctypes.POINTER(_SID_AND_ATTRIBUTES)).contents
            return _sid_string(user.Sid)
        finally:
            _CloseHandle(token)

    def read_security(path: Path) -> Security:
        """`path`'s owner and DACL, read through a followed link as Windows opens it."""
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        code = _GetNamedSecurityInfoW(
            _extended(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if code:
            raise _failure(code, path)
        try:
            if not dacl.value:
                return Security(owner=_sid_string(owner.value), dacl=None)
            count = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents.AceCount
            aces = []
            for index in range(count):
                address = ctypes.c_void_p()
                if not _GetAce(dacl, index, ctypes.byref(address)) or not address.value:
                    raise _last_failure(path)
                start = address.value + _ACCESS_ACE.SidStart.offset
                header = ctypes.cast(address, ctypes.POINTER(_ACE_HEADER)).contents
                mask, sid = 0, None
                if header.AceType in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
                    body = ctypes.cast(address, ctypes.POINTER(_ACCESS_ACE)).contents
                    mask = body.Mask
                    sid = _sid_string(start)
                aces.append(Ace(header.AceType, header.AceFlags, mask, sid))
            return Security(owner=_sid_string(owner.value), dacl=tuple(aces))
        finally:
            _LocalFree(descriptor)

    def _descriptor(sddl: str) -> ctypes.c_void_p:
        descriptor = ctypes.c_void_p()
        if not _ConvertSddl(sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
            raise _last_failure()
        return descriptor

    def create_owner_only_directory(path: Path) -> None:
        """Create `path` owned by you, with the owner-only DACL from the start.

        Created with it rather than given it afterwards, so there is no moment
        in which the directory exists with its parent's ACL.
        """
        user = current_user_sid()
        descriptor = _descriptor(f"O:{user}{_owner_only_sddl(user)}")
        try:
            attributes = _SECURITY_ATTRIBUTES(
                ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor.value, False
            )
            if not _CreateDirectoryW(_extended(path), ctypes.byref(attributes)):
                raise _last_failure(path)
        finally:
            _LocalFree(descriptor)

    def restrict_to_owner(path: Path) -> None:
        """Replace `path`'s DACL with the owner-only one, protected from its parent.

        `SetNamedSecurityInfoW` pushes the new inheritable ACEs down to every
        entry that inherits; an ACE set on an entry itself stays, which is
        why `make_owner_only` checks again afterwards.
        """
        descriptor = _descriptor(_owner_only_sddl(current_user_sid()))
        try:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not _GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                raise _last_failure()
            code = _SetNamedSecurityInfoW(
                _extended(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if code:
                raise _failure(code, path)
        finally:
            _LocalFree(descriptor)

else:
    # Defined so callers type-check and tests can replace them; reached on
    # POSIX only by a test that forgot to.
    def current_user_sid() -> str:
        raise NotImplementedError("Windows ACLs are read only on Windows")

    def read_security(path: Path) -> Security:
        raise NotImplementedError("Windows ACLs are read only on Windows")

    def create_owner_only_directory(path: Path) -> None:
        raise NotImplementedError("Windows ACLs are written only on Windows")

    def restrict_to_owner(path: Path) -> None:
        raise NotImplementedError("Windows ACLs are written only on Windows")
