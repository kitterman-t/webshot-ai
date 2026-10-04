"""The v2.2 output contract, enforced against the recorded golden corpus.

These tests need no browser: they read the goldens recorded by
`tools/golden/record.py` and check that every recorded artifact still matches
the frozen pydantic models, and that `schemas/` is exactly what those models
export.  A change here means the published contract changed — which is allowed,
but only deliberately (see CONTRIBUTING.md).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from webshot.bundle.manifest import (
    SCHEMAS,
    AssetsFile,
    QaReport,
    ViewerCapture,
    ViewerChunk,
    ViewerContent,
    ViewerManifest,
    WebChunk,
    WebChunkV3,
    WebContent,
    WebManifest,
    WebManifestV3,
    render_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden"
SCHEMA_ROOT = REPO_ROOT / "schemas"


def golden_cases() -> list[Path]:
    if not GOLDEN_ROOT.is_dir():  # a clean checkout always has these
        return []
    return sorted(path for path in GOLDEN_ROOT.iterdir() if path.is_dir())


def test_golden_corpus_is_present() -> None:
    assert golden_cases(), "no goldens recorded — run tools/golden/record.py"


@pytest.mark.parametrize("case", golden_cases(), ids=lambda path: path.name)
def test_recorded_bundle_matches_the_contract(case: Path) -> None:
    bundle = case / "bundle"
    if not bundle.exists():  # --no-ai-bundle cases publish a PDF only
        return

    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    content = json.loads((bundle / "content.json").read_text("utf-8"))
    chunk_lines = (bundle / "chunks.jsonl").read_text("utf-8").splitlines()

    if manifest.get("bundle_format") == 3:
        WebManifestV3.model_validate(manifest)
        AssetsFile.model_validate_json((bundle / "assets.json").read_text("utf-8"))
        for line in chunk_lines:
            WebChunkV3.model_validate_json(line)
        # content.json's schema is docling-core's own — loadability is the
        # contract (acceptance G3), not a schema WebShot maintains.
        from webshot.extract.docling_bridge import validate_document_json

        validate_document_json((bundle / "content.json").read_text("utf-8"))
        legacy_content = bundle / "legacy" / "content.json"
        if legacy_content.exists():
            WebContent.model_validate_json(legacy_content.read_text("utf-8"))
            for line in (
                (bundle / "legacy" / "chunks.jsonl").read_text("utf-8").splitlines()
            ):
                WebChunk.model_validate_json(line)
    elif manifest.get("schema_version") == "1.0":
        WebManifest.model_validate(manifest)
        WebContent.model_validate(content)
        for line in chunk_lines:
            WebChunk.model_validate_json(line)
    else:
        ViewerManifest.model_validate(manifest)
        ViewerContent.model_validate(content)
        ViewerCapture.model_validate_json((bundle / "capture.json").read_text("utf-8"))
        for line in chunk_lines:
            ViewerChunk.model_validate_json(line)


@pytest.mark.parametrize("case", golden_cases(), ids=lambda path: path.name)
def test_recorded_qa_report_matches_the_frozen_contract(case: Path) -> None:
    report = case / "report.json"
    if not report.exists():  # the protected-viewer case has no CLI report
        return
    QaReport.model_validate_json(report.read_text("utf-8"))


def test_manifest_lists_every_published_bundle_file() -> None:
    """The manifest's checksum table must cover the bundle, itself excepted."""
    for case in golden_cases():
        bundle = case / "bundle"
        if not bundle.exists():
            continue
        manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
        listed = (
            set(manifest["files"])
            if isinstance(manifest["files"], dict)
            else {entry["file"] for entry in manifest["files"]}
        )
        on_disk = {
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        # Raster assets are recorded in bundle-rasters.json, not in the golden
        # tree, so they are listed in the manifest without a file to compare.
        assert on_disk <= listed, f"{case.name}: unlisted files {on_disk - listed}"


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_published_schema_matches_the_models(name: str) -> None:
    path = SCHEMA_ROOT / f"{name}.schema.json"
    assert path.is_file(), f"{path} is missing — run `webshot schema` export"
    assert path.read_text("utf-8") == render_schema(name), (
        f"{path.name} is stale; regenerate it with "
        '`python -c "from pathlib import Path; '
        "from webshot.bundle.manifest import export_schemas; "
        "export_schemas(Path('schemas'))\""
    )


def test_no_schema_file_is_orphaned() -> None:
    published = {
        path.stem.removesuffix(".schema") for path in SCHEMA_ROOT.glob("*.json")
    }
    assert published == set(SCHEMAS)


def test_the_release_version_has_exactly_one_source() -> None:
    """One literal, and everything else derives from it (docs/09 P5-3).

    `src/webshot/version.py` is it. `pyproject.toml` reads that file through
    hatchling's version hook rather than repeating the number, because the way
    two literals stop agreeing is someone bumping one of them — and the three
    places the version is *published* (the wheel's metadata, `--version`, and
    every bundle's `generator_version`) would then disagree about which build
    produced a capture.
    """
    import importlib.metadata
    import re

    from webshot.version import VERSION

    assert importlib.metadata.version("webshot") == VERSION
    assert __import__("webshot").__version__ == VERSION

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'path = "src/webshot/version.py"' in pyproject

    # And no second literal anywhere the tree can reach. The golden corpus is
    # exempt because the harness masks the version out of it by design; the
    # lock file records dependency versions, not WebShot's.
    literal = re.compile(rf"(?<![\w.]){re.escape(VERSION)}(?![\w.])")
    strays = [
        f"{path.relative_to(REPO_ROOT)}"
        for pattern in ("src/**/*.py", "tools/**/*.py", "*.toml", "*.py")
        for path in REPO_ROOT.glob(pattern)
        if path.is_file()
        and path != REPO_ROOT / "src" / "webshot" / "version.py"
        and literal.search(path.read_text(encoding="utf-8"))
    ]
    assert not strays, f"the version is hardcoded outside version.py in: {strays}"


def test_the_published_report_schema_says_error_is_never_null() -> None:
    """The contract has to describe the document the writer actually emits.

    `QaReport.error` is typed `str | None` so the model can hold "no error",
    but the serializer drops the key entirely rather than writing null — so a
    published schema saying `anyOf: [string, null]` would bless a document
    WebShot cannot produce, and force every consumer to handle a value it will
    never see. The presence of the key *is* the failure signal (spec §2.3), and
    that is only true if the schema says so (docs/09 P5-10).
    """
    schema = json.loads((SCHEMA_ROOT / "qa-report.schema.json").read_text("utf-8"))
    assert schema["properties"]["error"] == {"title": "Error", "type": "string"}
    assert "error" not in schema["required"]

    # And the writer's half of the same promise, on a real report shape.
    from webshot.bundle.manifest import QaReport

    minimal = {
        "schema_version": "2",
        "source": "s",
        "final_url": None,
        "output": None,
        "title": None,
        "pages": None,
        "bytes": None,
        "http_status": None,
        "content_type": "",
        "duration_seconds": 0.0,
        "timings": {},
        "captured_at": "2026-01-01T00:00:00+00:00",
        "content_selector": None,
        "source_kind": None,
        "ai_bundle": None,
        "counts": None,
        "options": None,
        "validation": None,
        "failed_requests": [],
        "warnings": [],
        "manifest_sha256": None,
        "exit_code": 0,
    }
    assert "error" not in QaReport.model_validate(minimal).model_dump(mode="json")
    failed = QaReport.model_validate({**minimal, "exit_code": 3, "error": "boom"})
    assert failed.model_dump(mode="json")["error"] == "boom"


def test_the_readme_install_url_matches_the_released_version() -> None:
    """The one place the version is published *outside* the code (docs/09 P5-10).

    `test_the_release_version_has_exactly_one_source` scans `src/`, `tools/`
    and the root's Python and TOML — it cannot see Markdown. Left unchecked, a
    bump would ship a README pointing at a wheel that does not exist under a
    tag that does not exist: a 404 for every new user, which is exactly the
    drift the single-source hook exists to abolish.

    This repository has cut no release yet, so the README installs from a
    clone. The clone line is asserted so that the test still checks something
    while no release URL exists; any release URL added later must name the
    current version.
    """
    from webshot.version import VERSION

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "git clone https://github.com/kitterman-t/webshot-ai" in readme, (
        "README.md no longer shows how to install from a clone"
    )
    release_wheels = re.findall(
        r"/releases/download/v([^/\s)]+)/webshot-([^\s)]+?)-py3-none-any\.whl",
        readme,
    )
    stale = [pair for pair in release_wheels if pair != (VERSION, VERSION)]
    assert not stale, f"README.md names a release other than v{VERSION}: {stale}"
