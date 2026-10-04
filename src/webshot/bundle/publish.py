"""Checksums and the atomic swap that makes a bundle appear all at once.

A crash must never leave a half-written bundle where consumers look
(docs/04-spec.md §2.2), so the staging directory is renamed into place and the
previous bundle is kept as a backup until the rename succeeds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoids a cycle: build.py produces what this module ships
    from .build import AIBundleResult

LOGGER = logging.getLogger("webshot")


@dataclass(frozen=True, slots=True)
class EmbeddedPayload:
    """One bundle file on its way into the PDF as an associated file.

    A named record rather than a tuple because these three values are exactly
    what ISO 19005-3 clause 6.8 is picky about, and a positional tuple crossing
    a module boundary into that code is how the MIME type got mangled in v2
    (docs/09 P1-3).  It lives here because the bundle writer produces them and
    the PDF composition consumes them.
    """

    data: bytes
    description: str
    mime_type: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_json(value: Any) -> str:
    """The one JSON spelling every bundle artifact is written in.

    `allow_nan=False` because Python's default emits bare `NaN` and `Infinity`,
    which its own parser accepts and no other language's does — a bundle is
    read by whatever an agent is written in, so a value Python alone can parse
    is a broken artifact. Raising here turns "silently unreadable file" into a
    failure with a name (docs/09 P7-12).
    """
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path: Path, value: Any) -> None:
    # `newline="\n"`, as on every text writer in the package: without it
    # Windows writes "\r\n", and the same capture has other bytes and other
    # digests there (docs/09 P10-27).
    path.write_text(dump_json(value), encoding="utf-8", newline="\n")


def announce_replacements(*deliverables: Path | None) -> None:
    """Say which deliverables already on disk this publication replaces.

    The swap is atomic and was silent, and the default output name keeps only
    a local file's stem, so `report.csv` and then `report.json` both publish
    `output/pdf/report.pdf` and the second replaced the first unannounced.
    Logged rather than refused: capturing again to the same path is the
    ordinary way to refresh a capture. Called at the swap, where the
    replacement happens, so a run that fails earlier never claims it.
    """
    for path in deliverables:
        if path is not None and path.exists():
            LOGGER.info("Replacing existing %s", path)


def publish_directory(staging: Path, final: Path) -> None:
    """Swap a staged directory into place, keeping the old one until it lands.

    Both bundle paths publish this way, so a crash never leaves a half-written
    bundle where a consumer looks (docs/04-spec.md §2.2).
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    backup = final.with_name(f".{final.name}.{os.getpid()}.backup")
    if backup.exists():
        shutil.rmtree(backup)
    if final.exists():
        os.replace(final, backup)
    try:
        os.replace(staging, final)
    except Exception:
        if backup.exists() and not final.exists():
            os.replace(backup, final)
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup)


def publish_ai_bundle(result: AIBundleResult) -> None:
    publish_directory(result.staging_directory, result.final_directory)


def relative_pdf_path(pdf: Path, bundle_directory: Path) -> str:
    """How a consumer standing inside the bundle reaches the published PDF.

    `../<name>` is right only when the bundle sits beside the PDF, which is the
    default and was assumed to be the only case. With `--ai-bundle-dir` pointing
    anywhere else the hard-coded prefix resolved to a file that does not exist,
    so `manifest.json` — whose whole job is to let a consumer find and verify
    the artifacts — could not locate the one it had just checksummed
    (docs/09 P8-49).

    A POSIX relative path where one exists, because that is what the rest of
    the manifest's `files` keys are and a consumer joins them the same way.
    An absolute POSIX path when the two live on different roots and no relative
    path can be written; the alternative is a `..`-chain that depends on the
    reader's platform.
    """
    try:
        return PurePosixPath(
            os.path.relpath(pdf.resolve(), start=bundle_directory.resolve())
        ).as_posix()
    except (OSError, ValueError):
        return pdf.resolve().as_posix()
