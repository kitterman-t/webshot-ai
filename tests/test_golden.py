"""Run the golden harness from pytest.

Marked `golden` because it launches a browser and runs Tesseract, so the
default test run skips it (see `addopts` in pyproject.toml).  Run it with:

    uv run pytest -m golden
    uv run python tools/golden/check.py      # same work, readable report
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.check import main as check_main  # noqa: E402
from tools.golden.corpus import CORPUS  # noqa: E402
from tools.golden.harness import (  # noqa: E402
    manifest_digest_failures,
    normalize_text,
)


@pytest.mark.golden
@pytest.mark.browser
@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case.id)
def test_case_matches_its_golden(case: object) -> None:
    assert check_main(["--case", case.id]) == 0  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# The assertions the snapshot comparison cannot make
#
# `normalized_snapshot` recomputes every manifest checksum over the normalized
# text, so a wrong published checksum is replaced before it is compared. These
# tests plant the defect the corpus therefore cannot see and prove the
# assertion catches it — a guard nothing can trip is a guard that proves
# nothing (docs/09 P7-11, P8-42).
# --------------------------------------------------------------------------- #


def _bundle(tmp_path: Path, files: dict[str, str], manifest: dict) -> object:
    from tools.golden.harness import RunArtifacts

    bundle = tmp_path / "capture.ai"
    bundle.mkdir()
    for name, body in files.items():
        # Bytes, not `write_text`: on Windows that writes `\r\n`, and the file
        # would no longer be the one `_record` describes (docs/09 P10-24).
        (bundle / name).write_bytes(body.encode("utf-8"))
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pdf = tmp_path / "capture.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    return RunArtifacts(
        case=CORPUS[0],
        workdir=tmp_path,
        pdf=pdf,
        bundle=bundle,
        report=tmp_path / "report.json",
        exit_code=0,
        stderr="",
    )


def _record(body: str) -> dict:
    data = body.encode("utf-8")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_a_truthful_manifest_reports_no_digest_failures(tmp_path: Path) -> None:
    body = "# a bundle file\n"
    run = _bundle(
        tmp_path, {"content.md": body}, {"files": {"content.md": _record(body)}}
    )
    assert manifest_digest_failures(run) == []


def test_a_wrong_published_checksum_is_caught(tmp_path: Path) -> None:
    """The exact regression the corpus masked: a consumer verifying the bundle
    it was handed would find the checksum does not describe the file."""
    body = "# a bundle file\n"
    record = _record(body)
    record["sha256"] = "0" * 64
    run = _bundle(tmp_path, {"content.md": body}, {"files": {"content.md": record}})
    problems = manifest_digest_failures(run)
    assert any(
        "sha256 for content.md does not describe the file" in p for p in problems
    )


def test_a_wrong_published_length_is_caught(tmp_path: Path) -> None:
    body = "# a bundle file\n"
    record = _record(body)
    record["bytes"] = 1
    run = _bundle(tmp_path, {"content.md": body}, {"files": {"content.md": record}})
    assert any("is 1 bytes" in problem for problem in manifest_digest_failures(run))


def test_a_manifest_naming_a_file_that_was_not_written_is_caught(
    tmp_path: Path,
) -> None:
    run = _bundle(tmp_path, {}, {"files": {"content.md": _record("x")}})
    assert any(
        "was not written" in problem for problem in manifest_digest_failures(run)
    )


def test_the_protected_viewer_list_shape_is_checked_too(tmp_path: Path) -> None:
    """The protected manifest records `files` as a list, not a mapping, and it
    was masked by the same rewrite."""
    body = "page one\n"
    record = {"file": "content.txt", **_record(body)}
    record["sha256"] = "f" * 64
    run = _bundle(tmp_path, {"content.txt": body}, {"files": [record]})
    assert any("content.txt" in problem for problem in manifest_digest_failures(run))


# --------------------------------------------------------------------------- #
# A raster's digest is a fact about the fonts that drew its text (P7-14), so
# the portable profile masks it. These tests plant the defect that masking
# would otherwise hide and prove the assertion catches it.
# --------------------------------------------------------------------------- #


def _raster_bundle(tmp_path: Path, assets: dict, raster: bytes = b"\x89PNG\r\n\x1a\nX"):
    from tools.golden.harness import RunArtifacts

    bundle = tmp_path / "capture.ai"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "asset-001.png").write_bytes(raster)
    (bundle / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
    pdf = tmp_path / "capture.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    return RunArtifacts(
        case=CORPUS[0],
        workdir=tmp_path,
        pdf=pdf,
        bundle=bundle,
        report=tmp_path / "report.json",
        exit_code=0,
        stderr="",
    )


def test_a_truthful_asset_digest_reports_no_failures(tmp_path: Path) -> None:
    from tools.golden.harness import asset_digest_failures

    raster = b"\x89PNG\r\n\x1a\nX"
    run = _raster_bundle(
        tmp_path,
        {
            "visual_assets": [
                {
                    "id": "asset-001",
                    "file": "assets/asset-001.png",
                    "sha256": hashlib.sha256(raster).hexdigest(),
                }
            ]
        },
        raster=raster,
    )
    assert asset_digest_failures(run) == []


def test_a_wrong_asset_digest_is_caught(tmp_path: Path) -> None:
    """The regression masking would otherwise hide: the portable profile
    throws this value away before comparing, so if nothing asserted it, a
    bundle published with a checksum that does not describe its own raster
    would pass every profile — the P8-42 shape, one artifact over."""
    from tools.golden.harness import asset_digest_failures

    run = _raster_bundle(
        tmp_path,
        {
            "visual_assets": [
                {"id": "asset-001", "file": "assets/asset-001.png", "sha256": "0" * 64}
            ]
        },
    )
    problems = asset_digest_failures(run)
    assert any("does not match the digest of" in problem for problem in problems)


def test_an_asset_record_naming_a_missing_raster_is_caught(tmp_path: Path) -> None:
    from tools.golden.harness import asset_digest_failures

    run = _raster_bundle(
        tmp_path,
        {
            "visual_assets": [
                {"id": "gone", "file": "assets/absent.png", "sha256": "0" * 64}
            ]
        },
    )
    assert any("not in the bundle" in problem for problem in asset_digest_failures(run))


def test_the_picture_annotation_shape_is_asserted_too(tmp_path: Path) -> None:
    """content.json carries the same digest under a different shape. A map the
    masker reads and the assertion does not would mask a value nothing checks."""
    from tools.golden.harness import asset_digest_failures

    run = _raster_bundle(tmp_path, {"visual_assets": []})
    (run.bundle / "content.json").write_text(
        json.dumps(
            {
                "pictures": [
                    {
                        "annotations": [
                            {
                                "content": {
                                    "webshot": {
                                        "asset_id": "asset-001",
                                        "file": "assets/asset-001.png",
                                        "sha256": "0" * 64,
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert any("asset-001" in problem for problem in asset_digest_failures(run)), (
        "the annotation shape must be asserted, not only the visual_assets shape"
    )


def _legacy_content(run, record: dict) -> None:
    legacy = run.bundle / "legacy"
    legacy.mkdir()
    (legacy / "content.json").write_text(
        json.dumps({"visual_assets": [record]}), encoding="utf-8"
    )


def test_the_legacy_bundle_digest_is_asserted_too(tmp_path: Path) -> None:
    """The portable profile masks legacy/content.json's raster digest as well.
    The assertion used to read its own list of two artifacts, so that one
    value was masked and checked by nothing (docs/09 P18-1)."""
    from tools.golden.harness import asset_digest_failures

    run = _raster_bundle(tmp_path, {"visual_assets": []})
    _legacy_content(
        run, {"id": "asset-001", "file": "assets/asset-001.png", "sha256": "0" * 64}
    )
    problems = asset_digest_failures(run)
    assert any(
        problem.startswith("legacy/content.json asset-001: sha256")
        for problem in problems
    ), problems


def test_a_truthful_legacy_digest_resolves_against_the_bundle_root(
    tmp_path: Path,
) -> None:
    """`legacy/` holds only JSON; the raster its record names is the bundle's
    own. Resolving it next to the legacy file would fail a correct bundle."""
    from tools.golden.harness import asset_digest_failures

    raster = b"\x89PNG\r\n\x1a\nX"
    run = _raster_bundle(tmp_path, {"visual_assets": []}, raster=raster)
    _legacy_content(
        run,
        {
            "id": "asset-001",
            "file": "assets/asset-001.png",
            "sha256": hashlib.sha256(raster).hexdigest(),
        },
    )
    assert asset_digest_failures(run) == []


def test_a_digest_that_names_no_file_fails_rather_than_being_skipped(
    tmp_path: Path,
) -> None:
    """A published digest nothing can verify is not a pass, and it is also the
    value the portable profile masks, so skipping it meant nothing read it."""
    from tools.golden.harness import asset_digest_failures

    run = _raster_bundle(
        tmp_path, {"visual_assets": [{"id": "asset-001", "sha256": "0" * 64}]}
    )
    problems = asset_digest_failures(run)
    assert any(
        "asset-001" in problem and "names no file" in problem for problem in problems
    ), problems


def test_every_artifact_the_portable_profile_masks_is_asserted() -> None:
    """The masker and the assertion read one tuple. Planting a wrong digest in
    each masked artifact in turn proves the tuple is the one actually read."""
    import tempfile

    from tools.golden.harness import _OCR_BEARING_ARTIFACTS, asset_digest_failures

    wrong = {
        "visual_assets": [
            {"id": "asset-001", "file": "assets/asset-001.png", "sha256": "0" * 64}
        ]
    }
    for artifact in _OCR_BEARING_ARTIFACTS:
        with tempfile.TemporaryDirectory() as directory:
            run = _raster_bundle(Path(directory), {"visual_assets": []})
            path = run.bundle / artifact.removeprefix("bundle/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(wrong), encoding="utf-8")
            assert asset_digest_failures(run), f"{artifact} is masked but not asserted"


def test_the_portable_profile_masks_a_raster_digest_in_every_bundle_shape() -> None:
    """Including the legacy bundle, which was absent from the artifact list:
    its OCR text was scrubbed incidentally while its confidence and digest
    were never masked, so that artifact was not in fact covered."""
    from tools.golden.harness import MASKED_RASTER_DIGEST, portable

    record = {
        "visual_assets": [
            {
                "id": "asset-001",
                "file": "assets/asset-001.png",
                "sha256": "a" * 64,
                "ocr": {"text": "Revenue 2026", "confidence": 96.72},
            }
        ]
    }
    files = {
        name: json.dumps(record)
        for name in (
            "bundle/assets.json",
            "bundle/content.json",
            "bundle/legacy/content.json",
        )
    }
    result = portable(files)
    for name in files:
        assert MASKED_RASTER_DIGEST in result[name], name
        assert "a" * 64 not in result[name], name
        assert "96.72" not in result[name], f"{name}: confidence is font-dependent too"


def test_a_dimension_the_recording_never_wrote_counts_as_drift() -> None:
    """Treating "unknown" as "same" is how a dimension that moves the output
    stays invisible: the fonts changed, the fingerprint had no font key, and
    the run reported "environment matches the recording" while twelve cases
    differed."""
    from tools.golden.harness import PROFILE_KEYS, profile_for

    assert "fonts" in PROFILE_KEYS
    recorded = {"platform": "Darwin", "playwright": "1.62.0", "tesseract": "t 5.5.3"}
    current = {**recorded, "fonts": "369 fonts, 9bedbc3ec25e"}
    profile, reason = profile_for(recorded, current)
    assert profile == "portable"
    assert "fonts" in reason and "not recorded" in reason
    profile, reason = profile_for(current, current)
    assert profile == "strict", "a fingerprint that matches is still strict"


@pytest.mark.parametrize(
    "version",
    ["3.0.0", "3.1.0rc1", "3.1.0.dev0", "3.1.0.post2", "3.1.0+g1a2b3c.d20261003"],
)
def test_every_release_form_of_the_version_is_masked(
    version: str, tmp_path: Path
) -> None:
    """A pre-release or dev build is a version bump, not a change in output."""
    text = f"Producer: WebShot {version}. Created by WebShot {version} (1 page)"
    assert normalize_text(text, tmp_path) == (
        "Producer: WebShot <VERSION>. Created by WebShot <VERSION> (1 page)"
    )


def _font_tree(root: Path, size: int = 4) -> Path:
    """The Linux layout: nothing at the top, every font in a package's folder."""
    font = root / "truetype" / "dejavu" / "DejaVuSans.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"\0" * size)
    return font


def test_the_font_fingerprint_reads_fonts_in_subdirectories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`iterdir()` read only the top level, and a Linux distribution puts no
    font there, so the fingerprint was "none" on every Linux machine and a font
    update reported "environment matches the recording" (docs/09 P18-2)."""
    from tools.golden import harness

    font = _font_tree(tmp_path)
    monkeypatch.setattr(harness, "FONT_DIRECTORIES", (str(tmp_path),))
    before = harness.font_fingerprint()
    assert before.startswith("1 fonts, "), before

    font.write_bytes(b"\0" * 8)  # the font was rebuilt
    assert harness.font_fingerprint() != before


def test_a_font_reached_twice_is_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root nested in another (macOS's `Supplemental/` was listed on its own
    as well) or a symlinked font must not be counted twice: the count is part
    of the value a recording is compared against."""
    from tools.golden import harness

    font = _font_tree(tmp_path)
    (tmp_path / "alias.ttf").symlink_to(font)
    monkeypatch.setattr(harness, "FONT_DIRECTORIES", (str(tmp_path), str(font.parent)))
    twice = harness.font_fingerprint()
    assert twice.startswith("1 fonts, ")

    # Under the target's name, so the value is the one the font alone gives,
    # whichever path the walk happened to meet first.
    (tmp_path / "alias.ttf").unlink()
    monkeypatch.setattr(harness, "FONT_DIRECTORIES", (str(font.parent),))
    assert harness.font_fingerprint() == twice


def test_no_fonts_at_all_is_still_reported_as_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.golden import harness

    (tmp_path / "truetype").mkdir()
    (tmp_path / "truetype" / "README").write_text("not a font", encoding="utf-8")
    monkeypatch.setattr(harness, "FONT_DIRECTORIES", (str(tmp_path),))
    assert harness.font_fingerprint() == "none"


def test_the_portable_profile_masks_the_ocr_path_versions() -> None:
    """An OCRmyPDF bump moved only `text_layer_engine` and the producer pikepdf
    writes, and the check reported it as a real output change. The tool's name
    stays compared: a different engine is a different output."""
    from tools.golden.harness import portable

    def snapshot(engine: str, producer: str) -> dict[str, str]:
        return {
            "bundle/manifest.json": json.dumps(
                {"pdf": {"searchable_ocr_layer": True, "text_layer_engine": engine}}
            ),
            "pdf.json": json.dumps({"metadata": {"/Producer": producer}}),
        }

    recorded = portable(
        snapshot("OCRmyPDF 17.12.1", "pikepdf 10.12.0"), ocr_pdf_text=True
    )
    bumped = portable(
        snapshot("OCRmyPDF 17.13.0", "pikepdf 10.16.0"), ocr_pdf_text=True
    )
    assert bumped == recorded
    assert "OCRmyPDF <VERSION>" in recorded["bundle/manifest.json"]
    assert "pikepdf <VERSION>" in recorded["pdf.json"]
    other_engine = portable(
        snapshot("Tesseract 5.5.3", "pikepdf 10.12.0"), ocr_pdf_text=True
    )
    assert other_engine != recorded
