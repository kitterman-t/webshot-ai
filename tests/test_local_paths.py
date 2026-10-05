"""`--local-paths relative`: a local capture's bundle does not carry the machine's paths.

A page rendered from `file://` records that file's absolute URL as its source,
and every image and link the browser resolved against it as a `file:` URL under
the same directory. Measured on 132 delivered PDFs rendered from local copies,
four files of each embedded bundle named the capture machine's user and folder layout (docs/09
P14-61). The option records them relative to the source file's directory, as
they are written, so the bundle's digests describe the bytes it holds.

`records.json` holds the URL shapes Chromium resolves under an invented capture
path; `page.html` is a page that produces them. Nothing here reaches the
network: the browser tests read the fixture from disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pikepdf
import pytest

from webshot.bundle.localpaths import LocalPathRecorder
from webshot.bundle.manifest import AssetsFile, QaReport, WebManifestV3
from webshot.config import CaptureOptions
from webshot.errors import UsageError
from webshot.pipeline import convert_url_to_pdf
from webshot.render.embed import readme_text
from webshot.report import resolved_options

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "local_paths"
RECORDS = json.loads((FIXTURES / "records.json").read_text("utf-8"))


def _recorder() -> LocalPathRecorder:
    return LocalPathRecorder(RECORDS["source"], "relative")


@pytest.mark.parametrize("case", RECORDS["cases"], ids=lambda case: case["what"])
def test_each_url_is_recorded_relative_or_kept(case: dict[str, Any]) -> None:
    recorder = _recorder()
    expected = case["url"] if case["recorded"] is None else case["recorded"]
    assert recorder.record(case["url"]) == expected
    # A URL left absolute is counted, so the warning says how many there were.
    assert recorder.kept_absolute == (1 if case["recorded"] is None else 0)


def test_the_option_changes_nothing_unless_asked_for_a_local_source() -> None:
    url = RECORDS["cases"][1]["url"]
    absolute = LocalPathRecorder(RECORDS["source"])
    web = LocalPathRecorder("https://learn.example.org/x", "relative")
    for recorder in (absolute, web):
        assert not recorder.active
        assert recorder.convention is None
        assert recorder.record(url) == url
        links = [{"text": "t", "url": url}]
        assert recorder.record_links(links) == links
    assert _recorder().convention == "relative"


def test_a_url_kept_twice_is_counted_once() -> None:
    """`--legacy-bundle` records the same media in two files."""
    recorder = _recorder()
    outside = RECORDS["cases"][8]["url"]
    recorder.record(outside)
    recorder.record(outside)
    assert recorder.kept_absolute == 1


def test_a_record_gains_no_key_it_did_not_have() -> None:
    """The legacy writer reads blocks whose tracks may carry no `url`."""
    media = {"sourceUrl": RECORDS["cases"][1]["url"], "tracks": [{"kind": "x"}]}
    assert _recorder().record_media(media, "sourceUrl") == {
        "sourceUrl": "./ADA_R-456_Query%20result.png",
        "tracks": [{"kind": "x"}],
    }


def test_residue_names_every_file_still_spelling_the_directory(
    tmp_path: Path,
) -> None:
    """Asked the other way round: whatever field holds it, the path is found."""
    recorder = _recorder()
    directory = (
        "/capture machine/webshot/output/readings/example-course/AbC12-two-boxes"
    )
    (tmp_path / "links.json").write_text(
        json.dumps([{"url": RECORDS["cases"][2]["url"]}]), "utf-8"
    )
    (tmp_path / "content.md").write_text(f"Saved to {directory}/x.csv\n", "utf-8")
    (tmp_path / "content.txt").write_text("**CSV file:** a text format\n", "utf-8")
    (tmp_path / "assets.json").write_text(json.dumps({"u": "./x.png"}), "utf-8")
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "page.html").write_text(directory, "utf-8")
    assert recorder.residue(tmp_path) == ["content.md", "links.json"]
    assert recorder.residue(tmp_path, [{"x": RECORDS["source"]}])[-1] == (
        "manifest.json"
    )


def test_the_warnings_count_without_naming_the_path(tmp_path: Path) -> None:
    """A warning is copied into the bundle; it must not carry what it reports."""
    recorder = _recorder()
    recorder.record(RECORDS["cases"][8]["url"])
    (tmp_path / "links.json").write_text(RECORDS["source"], "utf-8")
    messages = recorder.warnings(tmp_path)
    assert messages == [
        "--local-paths relative: 1 bundle file(s) still name the source file's "
        "directory: links.json.",
        "--local-paths relative: 1 local file URL(s) outside the source file's "
        "directory were recorded as absolute paths.",
    ]
    assert not any("capture" in message for message in messages)


def test_options_refuse_a_value_and_a_bundle_the_convention_cannot_reach(
    tmp_path: Path,
) -> None:
    source = (FIXTURES / "page.html").as_uri()
    with pytest.raises(UsageError, match="absolute or relative"):
        CaptureOptions(source=source, output=tmp_path / "x.pdf", local_paths="rel")  # type: ignore[arg-type]
    with pytest.raises(UsageError, match="protected-viewer"):
        CaptureOptions(
            source=source,
            output=tmp_path / "x.pdf",
            protected_viewer=True,
            local_paths="relative",
        )


def test_the_report_publishes_the_option_only_when_it_was_asked_for(
    tmp_path: Path,
) -> None:
    """Absent by default, so every report written without it keeps its bytes."""
    source = (FIXTURES / "page.html").as_uri()
    default = resolved_options(CaptureOptions(source=source, output=tmp_path / "a"))
    relative = resolved_options(
        CaptureOptions(source=source, output=tmp_path / "a", local_paths="relative")
    )
    assert "local_paths" not in default.model_dump(mode="json")
    assert relative.model_dump(mode="json")["local_paths"] == "relative"
    schema = QaReport.model_json_schema()["$defs"]["ResolvedOptions"]
    assert schema["properties"]["local_paths"] == {
        "title": "Local Paths",
        "type": "string",
        "const": "relative",
    }
    assert "local_paths" not in schema["required"]


def test_the_readme_says_why_its_source_is_relative() -> None:
    plain = readme_text(["content.md"], title="T", source="https://x.test/")
    noted = readme_text(
        ["content.md"], title="T", source="./page.html", local_paths="relative"
    )
    assert "relative to its own directory" in noted
    assert noted.replace(noted.splitlines()[2] + "\n", "") == plain.replace(
        "https://x.test/", "./page.html"
    )


# --------------------------------------------------------------------------- #
# The whole pipeline, on the fixture page copied under a path with a space
# --------------------------------------------------------------------------- #


def _capture(directory: Path, local_paths: str) -> dict[str, Any]:
    item = directory / "item dir"
    shutil.copytree(FIXTURES, item, ignore=shutil.ignore_patterns("records.json"))
    output = directory / "capture.pdf"
    options = CaptureOptions(
        source=(item / "page.html").resolve().as_uri(),
        output=output,
        ai_bundle_directory=directory / "capture.ai",
        embed_assets=True,
        ocr=False,
        local_paths=local_paths,  # type: ignore[arg-type]
    )
    result = asyncio.run(convert_url_to_pdf(options))
    with pikepdf.open(output) as pdf:
        embedded = {
            name: spec.get_file().read_bytes() for name, spec in pdf.attachments.items()
        }
    return {
        "directory": directory,
        "item": item.resolve(),
        "result": result,
        "embedded": embedded,
        "bundle": directory / "capture.ai",
        "manifest": json.loads(
            (directory / "capture.ai" / "manifest.json").read_text()
        ),
    }


@pytest.fixture(scope="module")
def relative(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    yield _capture(tmp_path_factory.mktemp("relative"), "relative")


@pytest.fixture(scope="module")
def absolute(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    yield _capture(tmp_path_factory.mktemp("absolute"), "absolute")


@pytest.mark.browser
def test_no_embedded_file_names_the_capture_directory(relative: dict[str, Any]) -> None:
    item = str(relative["item"]).encode()
    named = [
        name
        for name, data in relative["embedded"].items()
        if b"file:/" in data or item in data
    ]
    # The one outside link is kept, and said so: the page links above itself.
    assert named == ["links.json"]
    links = json.loads(relative["embedded"]["links.json"])
    assert [link["url"] for link in links] == [
        "./attachments/guide.txt",
        "./page.html#later",
        (relative["item"].parent / "outside.txt").as_uri(),
    ]
    assert relative["result"].warnings == [
        "--local-paths relative: 1 local file URL(s) outside the source file's "
        "directory were recorded as absolute paths."
    ]


@pytest.mark.browser
def test_the_records_name_the_page_and_its_files_relative(
    relative: dict[str, Any],
) -> None:
    capture = json.loads(relative["embedded"]["capture.json"])
    manifest = relative["manifest"]
    for record in (capture, manifest):
        assert record["source"] == record["final_url"] == "./page.html"
        assert record["local_paths"] == "relative"
        assert record["page_metadata"]["canonicalUrl"] == "./page.html"
    assets = AssetsFile.model_validate_json(relative["embedded"]["assets.json"])
    assert [asset.source_url for asset in assets.visual_assets] == [
        "./images/figure%20one.svg"
    ]
    readme = relative["embedded"]["README.txt"].decode()
    assert "Captured from: ./page.html\n(A local file." in readme
    WebManifestV3.model_validate(manifest)
    # The QA report describes the run on this machine, and keeps its paths.
    assert relative["result"].source == (relative["item"] / "page.html").as_uri()


@pytest.mark.browser
def test_every_digest_describes_the_bytes_the_bundle_holds(
    relative: dict[str, Any],
) -> None:
    """Written relative, never rewritten afterwards: nothing to re-record."""
    bundle, manifest = relative["bundle"], relative["manifest"]
    for name, record in manifest["files"].items():
        data = (bundle / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"], name
        if name in relative["embedded"]:
            assert relative["embedded"][name] == data, name
    pdf = relative["directory"] / "capture.pdf"
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == manifest["pdf"]["sha256"]
    assert (
        relative["result"].manifest_sha256
        == hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    )


@pytest.mark.browser
def test_without_the_option_a_capture_is_what_it_always_was(
    absolute: dict[str, Any],
) -> None:
    page = (absolute["item"] / "page.html").as_uri()
    capture = json.loads(absolute["embedded"]["capture.json"])
    for record in (capture, absolute["manifest"]):
        assert record["source"] == record["final_url"] == page
        assert "local_paths" not in record
    links = json.loads(absolute["embedded"]["links.json"])
    assert links[0]["url"] == (absolute["item"] / "attachments" / "guide.txt").as_uri()
    assert absolute["result"].warnings == []
    assert "(A local file." not in absolute["embedded"]["README.txt"].decode()
