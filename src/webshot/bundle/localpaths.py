"""How a bundle records where a local source, and the local files it used, were.

A capture of a local file records `file:///<this machine's path>/<page>.html`
as its `source`, and every image and link the page resolved against it as a
`file:` URL under the same directory. Those are true, and on any other machine
they are also useless: they name a folder that is not there, and they carry the
capturing user's name and folder layout into every deliverable. Measured on
132 delivered PDFs rendered from local copies, four files of the embedded
bundle held it (docs/09 P14-61).

`--local-paths relative` records each such URL relative to the source file's
own directory instead: `./page.html`, `./images/figure.png`,
`./attachments/guide.docx`, `./page.html#section`. That is what the page's own
HTML said before the browser resolved it, so nothing is claimed that the
capture did not see, and the manifest says which convention it used. A `file:`
URL outside that directory cannot be written relative without naming the
folders above it, so it is recorded as it was and counted in a warning, never
silently dropped.

The values are rewritten as they are written, never afterwards, so every
digest the bundle carries is taken over the bytes it actually holds.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

LocalPaths = Literal["absolute", "relative"]

#: A `file:` URL as it can appear inside JSON, YAML, Markdown or plain text: up
#: to the first character that ends a string or a link in any of them.
_FILE_URL = re.compile(r"file:[^\s\"'<>\\)\]]+", re.IGNORECASE)

#: The source copy under `source/` is the caller's input verbatim, not a record
#: WebShot wrote, so a path the input itself spells is not this option's to
#: report. The manifest is written after the check and from recorded values.
_NOT_SCANNED = ("source/", "manifest.json")


def _path_segments(url: str) -> list[str] | None:
    """A local `file:` URL's path as raw (still-encoded) segments, or None.

    A `file:` URL naming any host but `localhost` is a remote share (a UNC path
    on Windows), not this machine's folder, and is left alone.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != "file" or parts.netloc.lower() not in {"", "localhost"}:
        return None
    return parts.path.split("/")


class LocalPathRecorder:
    """Records `file:` URLs under the source's directory relative to it.

    One per capture. Inactive — every value passes through unchanged — unless
    the convention is `relative` *and* the source is a local file: a web
    capture has no local directory to be relative to, and saying `relative`
    in its manifest would describe a convention nothing was written in.
    """

    def __init__(self, source: str, local_paths: LocalPaths = "absolute") -> None:
        segments = _path_segments(source) if local_paths == "relative" else None
        self.active = bool(segments) and len(segments or ()) > 1
        #: The source directory's decoded segments; the leading "" is the root.
        self._base = [unquote(s) for s in (segments or [])[:-1]]
        #: The same directory as a filesystem path, which is how a JSON or text
        #: file would spell it if it did not spell it as a URL. `url2pathname`
        #: because a Windows URL path is `/C:/Users/...`.
        self._directory = (
            Path(url2pathname("/".join((segments or [])[:-1]))) if self.active else None
        )
        #: `file:` URLs outside the source's directory, recorded as they were.
        #: A set, because `--legacy-bundle` records the same media twice.
        self._kept: set[str] = set()

    @property
    def kept_absolute(self) -> int:
        """How many distinct local URLs were recorded absolute."""
        return len(self._kept)

    @property
    def convention(self) -> Literal["relative"] | None:
        """What the manifest records, or None to leave the key out entirely.

        Absent rather than `"absolute"` so that a capture which did not ask for
        this is byte for byte what it always was.
        """
        return "relative" if self.active else None

    def _relative(self, url: str) -> str | None:
        segments = _path_segments(url)
        depth = len(self._base)
        if segments is None or len(segments) <= depth:
            return None
        if [unquote(s) for s in segments[:depth]] != self._base:
            return None
        remainder = segments[depth:]
        # Chromium removes dot segments before a URL is recorded; one that is
        # still here could climb out of the directory, so it is not rewritten.
        if any(unquote(s) in {".", ".."} for s in remainder):
            return None
        parts = urlsplit(url)
        relative = "./" + "/".join(remainder)
        if parts.query:
            relative += f"?{parts.query}"
        if parts.fragment:
            relative += f"#{parts.fragment}"
        return relative

    def under_base(self, url: str) -> bool:
        return self.active and self._relative(url) is not None

    def record(self, url: str) -> str:
        """`url` as the bundle should record it."""
        if not self.active or url[:5].lower() != "file:":
            return url
        relative = self._relative(url)
        if relative is None:
            self._kept.add(url)
            return url
        return relative

    def _recorded(self, record: dict[str, Any], key: str) -> dict[str, Any]:
        """`record` with `key` rewritten, if it has one; never a key it lacked."""
        if not self.active or not isinstance(record.get(key), str):
            return record
        return {**record, key: self.record(record[key])}

    def record_links(self, links: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._recorded(link, "url") for link in links]

    def record_media(self, media: dict[str, Any], key: str) -> dict[str, Any]:
        """An embedded-media record, in either spelling: `source_url` or `sourceUrl`."""
        if not self.active:
            return media
        recorded = self._recorded(media, key)
        if isinstance(media.get("tracks"), list):
            recorded = {
                **recorded,
                "tracks": [self._recorded(track, "url") for track in media["tracks"]],
            }
        return recorded

    def residue(self, staging: Path, records: Iterable[Any] = ()) -> list[str]:
        """Bundle files, and manifest-bound records, that still name the directory.

        The rewrite covers the fields known to hold a resolved URL; this asks
        the question the other way round, over every file the bundle holds, so
        a field nobody listed cannot carry the path out unannounced. It reads
        both spellings: a `file:` URL under the directory, and the directory's
        own filesystem path.
        """
        if not self.active or self._directory is None:
            return []
        directory = str(self._directory)
        spellings = {directory, json.dumps(directory)[1:-1]}

        def names_it(text: str) -> bool:
            if any(spelling in text for spelling in spellings):
                return True
            return any(self.under_base(m.group(0)) for m in _FILE_URL.finditer(text))

        found = []
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            relative = path.relative_to(staging).as_posix()
            if relative.startswith(_NOT_SCANNED):
                continue
            if names_it(path.read_bytes().decode("utf-8", errors="replace")):
                found.append(relative)
        if any(names_it(json.dumps(record, ensure_ascii=False)) for record in records):
            found.append("manifest.json")
        return found

    def warnings(self, staging: Path, records: Iterable[Any] = ()) -> list[str]:
        """What the convention could not do, for the manifest's `warnings`."""
        messages = []
        residue = self.residue(staging, records)
        if residue:
            messages.append(
                f"--local-paths relative: {len(residue)} bundle file(s) still "
                f"name the source file's directory: {', '.join(residue)}."
            )
        if self.kept_absolute:
            messages.append(
                f"--local-paths relative: {self.kept_absolute} local file URL(s) "
                "outside the source file's directory were recorded as absolute "
                "paths."
            )
        return messages
