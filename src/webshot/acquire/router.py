"""ACQUIRE routing: what is this source, and where does its PDF go?

`webshot/acquire/adapters.py` turns a non-HTML local file into HTML; this
module decides names and URLs.  `sanitize_filename` still builds v2.2's slug,
which the golden corpus pins, so it keeps the collision weakness
docs/04-spec.md §5.2 records: the hashed slug the spec describes is not
implemented on the CLI path, and two sources can share a default name unless
`--output` separates them.  The MCP server avoids it by appending a digest of
the source to every name it publishes.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..errors import UsageError
from .adapters import LOCAL_SUFFIXES

#: What a value starts with when it can only be a filesystem path. `.\\` and
#: `..\\` because that is how a relative path is typed on Windows, and a drive
#: letter for the same reason.
_PATH_PREFIXES = ("./", "../", ".\\", "..\\", "~", "/")
_WINDOWS_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")


def sanitize_filename(source: str) -> str:
    """Create a readable, filesystem-safe PDF filename."""
    parsed = urlparse(source)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path)).stem
    else:
        path_bits = [part for part in parsed.path.split("/") if part]
        candidate = "-".join([parsed.netloc, *path_bits[-2:]]) or "web-page"
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-._")
    candidate = re.sub(r"-+", "-", candidate)[:120] or "web-page"
    return f"{candidate}.pdf"


def _refuse_a_path_that_is_not_a_file(value: str, local_path: Path) -> None:
    """A value that can only mean a local file must name one, or it is exit 2.

    Without this, `./typo.csv` reached the hostname test below with `.` as its
    host, became `https://./typo.csv`, and failed as a network error (exit 3)
    for what is a missing file, decided before anything runs. A bare
    `report.csv` did the same through its dot. A bare name counts as a path
    only when its suffix is one WebShot reads, so `example.com` and
    `en.wikipedia.org/wiki/Foo` still become web addresses. Two of those
    suffixes are also top-level domains (`.md`, `.zip`), which is why the
    message for a bare name says how to ask for the web address instead.
    A backslash anywhere is a path: no web address is typed with one.
    """
    bare = False
    if not (
        value in {".", ".."}
        or "\\" in value
        or value.startswith(_PATH_PREFIXES)
        or _WINDOWS_DRIVE_RE.match(value)
    ):
        bare = "/" not in value
        if not bare or Path(value).suffix.lower() not in LOCAL_SUFFIXES:
            return
    if local_path.is_dir():
        raise UsageError(f"{value} is a directory. Give the path of a file in it.")
    if local_path.exists():
        raise UsageError(f"{value} is not a regular file.")
    hint = f" For a web address, write https://{value}" if bare else ""
    raise UsageError(f"No such file: {value}.{hint}")


def normalize_source(value: str) -> str:
    """Normalize an HTTP(S), file URL, or local HTML file into a browser URL.

    An unusable source is a `UsageError` (exit 2), not a capture failure: the
    request is impossible as asked. This also keeps the exit code stable now
    that the option rules run *after* normalization — before Phase 4 moved them
    onto the model, `webshot ftp://x --legacy-bundle --no-ai-bundle` exited 2 on
    the flag conflict, and it would otherwise have started exiting 1 (P4-12).
    """
    value = value.strip()
    local_path = Path(value).expanduser()
    if local_path.exists() and local_path.is_file():
        return local_path.resolve().as_uri()
    if "://" not in value:
        _refuse_a_path_that_is_not_a_file(value, local_path)
        authority = value.split("/", 1)[0]
        hostname = authority.rsplit(":", 1)[0]
        if hostname == "localhost" or re.fullmatch(
            r"\d{1,3}(?:\.\d{1,3}){3}", hostname
        ):
            return f"http://{value}"
        if "." in hostname:
            return f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "file"}:
        return value
    if parsed.scheme:
        raise UsageError(
            "Only http://, https://, file://, and local files are supported."
        )
    raise UsageError(
        f"'{value}' is not a URL or an existing file. Include https:// for web addresses."
    )


def resolve_output(source: str, output: str | None) -> Path:
    path = (
        Path(output).expanduser()
        if output
        else Path("output/pdf") / sanitize_filename(source)
    )
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    return path.resolve()
