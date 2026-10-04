"""One copy of every distinct asset, and every module that referenced it.

docs/13 asks for "media dedupe by content hash across the corpus — the same
screenshot recurs across modules; one copy, many references, **every
referencing module recorded**". The back-reference is half the requirement:
without it the corpus can no longer answer "which modules used this image",
which is the question a deduplicated store exists to make answerable.

**Scope: files on disk that carry a `sha256`.** Measured across the goldens
rather than inferred from the layout sketch:

| population | carries | in scope |
|---|---|---|
| `visual_assets[]` | `file`, `sha256` | yes |
| `video_assets[]` | `file`, `sha256` | yes |
| `embedded_media[]` | `media_type`, `source_url`, `tracks` — no file, no hash | **no** |

`embedded_media` is a *reference* to something on a remote host, not a file, so
there is no content to hash and nothing to deduplicate. That also settles the
sketch's `videos/`: by default a video is a reference and only becomes a file
under `--video-assets`, while page images and Guidde step stills are always on
disk. A store holding those is not a `videos/` directory, so it is called
`media/`.

**Module bundles are not rewritten.** The store is built beside them, not out
of them. docs/13 asks in one place for one copy across the corpus and in
another that each module keep its own bundle "so one module can be handed to an
agent alone", and those cannot both be true of the same bytes. Provenance is
the requirement's own stated rationale, and a capability with a stated reason
is not worth breaking to satisfy a storage reading of an ambiguous phrase — so
`media/` is one copy per distinct hash *plus* the back-references, and the
bundles stay hand-off-able.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("webshot.journey")

MEDIA_DIRECTORY = "media"
MEDIA_INDEX = "index.json"

#: Both populations that name a file and hash it. `embedded_media` is absent
#: deliberately — see the module docstring.
HASHED_POPULATIONS = ("visual_assets", "video_assets")


def _records(bundle: Path, warnings: list[str], node_id: str) -> list[dict[str, Any]]:
    assets = bundle / "assets.json"
    if not assets.is_file():
        return []
    try:
        data = json.loads(assets.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            f"{node_id}: {assets} could not be read ({exc}), so none of its media "
            "is in the corpus store"
        )
        return []
    found: list[dict[str, Any]] = []
    for population in HASHED_POPULATIONS:
        for record in data.get(population) or []:
            if record.get("sha256") and record.get("file"):
                found.append({**record, "population": population})
    return found


def build_media_store(root: Path, modules: list[tuple[str, Path]]) -> dict[str, Any]:
    """Copy one of each distinct asset into `media/`, recording every referrer.

    `modules` is `(node_id, module_directory)` for every module that produced a
    bundle. Built from what capture already wrote: nothing re-opens a module,
    and the hashes are consumed rather than recomputed — `assets.json` has
    carried `sha256` since the bundle format did.
    """
    warnings: list[str] = []
    store = root / MEDIA_DIRECTORY
    entries: dict[str, dict[str, Any]] = {}

    for node_id, directory in modules:
        bundle = directory / "bundle"
        for record in _records(bundle, warnings, node_id):
            source = bundle / str(record["file"])
            digest = str(record["sha256"])
            if not source.is_file():
                warnings.append(
                    f"{node_id}: {record['file']} is named in assets.json and is not "
                    "on disk, so it is recorded as a reference with no stored copy"
                )
            entry = entries.setdefault(
                digest,
                {
                    "sha256": digest,
                    "file": None,
                    "bytes": record.get("bytes"),
                    "kinds": [],
                    "references": [],
                },
            )
            kind = str(record.get("kind") or record["population"])
            if kind not in entry["kinds"]:
                entry["kinds"].append(kind)
            # The half of the requirement that is easy to drop: which modules
            # used this, and under what name inside each of them.
            entry["references"].append(
                {
                    "node_id": node_id,
                    "asset_id": record.get("id"),
                    "population": record["population"],
                    "bundle_file": str(record["file"]),
                }
            )
            if entry["file"] is None and source.is_file():
                store.mkdir(parents=True, exist_ok=True)
                stored = store / f"{digest[:16]}{source.suffix}"
                if not stored.exists():
                    shutil.copy2(source, stored)
                entry["file"] = str(stored.relative_to(root))
                if entry.get("bytes") is None:
                    entry["bytes"] = stored.stat().st_size

    index = {
        "media_format": 1,
        "directory": MEDIA_DIRECTORY,
        "distinct": len(entries),
        "references": sum(len(entry["references"]) for entry in entries.values()),
        # Stated rather than left to be derived: the number of assets that
        # appear in more than one module is the only figure that says whether
        # the store earned its existence.
        "shared": sum(
            1
            for entry in entries.values()
            if len({r["node_id"] for r in entry["references"]}) > 1
        ),
        "assets": [entries[digest] for digest in sorted(entries)],
        "warnings": warnings,
    }
    if entries:
        store.mkdir(parents=True, exist_ok=True)
        (store / MEDIA_INDEX).write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return index
