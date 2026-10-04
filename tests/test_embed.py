"""The self-contained AI PDF: the bundle travelling inside the PDF.

Two properties matter more than the rest, and both have a test that fails
loudly rather than quietly:

*Ordering.* The manifest records the published PDF's SHA-256. Embedding
rewrites the PDF, so an embed that happens after the manifest is written
records the hash of a file that no longer exists. `test_manifest_hash_matches_
the_published_file` is that invariant, and it is the reason the pipeline embeds
between rendering and finalizing.

*Survival.* Chromium's structure tree and outline are what make the rendered
half of the deliverable accessible. The rewrite has to preserve them, which
spike S2 established and this holds it to.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.cli import main
from webshot.render.embed import (
    EMBEDDABLE,
    _asset_mime,
    collect_payloads,
    readme_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "professional_page.html"


def _capture(tmp_path: Path, *extra: str) -> Path:
    output = tmp_path / "capture.pdf"
    code = main([str(FIXTURE), "--output", str(output), *extra])
    assert code == 0, f"capture failed with exit {code}"
    return output


@pytest.fixture(scope="module")
def embedded_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One default capture, reused: each one drives a real browser."""
    return _capture(tmp_path_factory.mktemp("embedded"))


def test_the_bundle_travels_inside_the_pdf(embedded_capture: Path) -> None:
    attachments = PdfReader(str(embedded_capture)).attachments
    # The README is what tells a reader the rest is there, so its absence
    # would make the others undiscoverable rather than merely missing.
    assert "README.txt" in attachments
    for name in ("content.json", "content.md", "chunks.jsonl", "capture.json"):
        assert name in attachments, f"{name} did not travel with the PDF"


def test_the_catalog_advertises_the_associated_files(embedded_capture: Path) -> None:
    """`/AF` is the part pypdf does not write, and ISO 19005-3 requires it."""
    reader = PdfReader(str(embedded_capture))
    associated = reader.trailer["/Root"].get("/AF")
    assert associated is not None, "no document-level /AF array"
    assert len(associated) == len(reader.attachments)
    for embedded in reader.attachment_list:
        assert embedded.associated_file_relationship == "/Data"
        assert embedded.subtype, f"{embedded.name} has no MIME /Subtype"
        assert embedded.alternative_name, f"{embedded.name} has no /UF"


def test_the_metadata_says_the_pdf_is_self_contained(embedded_capture: Path) -> None:
    metadata = PdfReader(str(embedded_capture)).metadata or {}
    assert metadata.get("/WebShotAIReady") == "true"
    assert metadata.get("/WebShotBundleFormat") == "3"
    assert "content.json" in (metadata.get("/WebShotEmbeddedFiles") or "")
    # Chromium's own title survives the rewrite rather than being replaced.
    assert metadata.get("/Title")


def test_embedding_preserves_the_tags_and_the_outline(embedded_capture: Path) -> None:
    reader = PdfReader(str(embedded_capture))
    root = reader.trailer["/Root"]
    assert "/StructTreeRoot" in root, "the rewrite dropped the structure tree"
    assert "/MarkInfo" in root
    assert reader.outline, "the rewrite dropped the outline"
    assert reader.pages[0].extract_text().strip()


def test_the_embedded_content_is_the_real_thing(embedded_capture: Path) -> None:
    """Not merely present — parseable, and carrying its provenance."""
    docling = pytest.importorskip("docling_core.types.doc.document")
    attachments = PdfReader(str(embedded_capture)).attachments

    document = docling.DoclingDocument.model_validate_json(
        attachments["content.json"][0].decode()
    )
    assert document.texts, "the embedded document model has no text"

    chunks = [
        json.loads(line)
        for line in attachments["chunks.jsonl"][0].decode().splitlines()
        if line.strip()
    ]
    assert chunks and chunks[0]["meta"]["doc_items"], "chunks lost their anchors"


def test_manifest_hash_matches_the_published_file(embedded_capture: Path) -> None:
    """The ordering invariant: embed before the manifest is written.

    If this fails, the manifest is describing the pre-embed PDF and every
    consumer that verifies the checksum will reject a file that is fine.
    """
    manifest = json.loads(
        (embedded_capture.with_suffix(".ai") / "manifest.json").read_text()
    )
    published = hashlib.sha256(embedded_capture.read_bytes()).hexdigest()
    assert manifest["pdf"]["sha256"] == published
    assert manifest["pdf"]["bytes"] == embedded_capture.stat().st_size
    assert manifest["pdf"]["embedded_files"], "the manifest does not say what is inside"


def test_the_provenance_record_is_not_self_referential(embedded_capture: Path) -> None:
    """`capture.json` exists because `manifest.json` cannot be embedded."""
    capture = json.loads(
        PdfReader(str(embedded_capture)).attachments["capture.json"][0].decode()
    )
    assert capture["source"] and capture["captured_at"]
    assert capture["tool_versions"]["chromium"]
    assert "pdf" not in capture, "provenance must not record the hash of its carrier"


def test_opting_out_leaves_the_pdf_a_rendering(tmp_path: Path) -> None:
    output = _capture(tmp_path, "--no-embed-bundle")
    reader = PdfReader(str(output))
    assert not reader.attachments
    assert "/AF" not in reader.trailer["/Root"]
    # The bundle is still produced; only its passage into the PDF is off.
    assert (output.with_suffix(".ai") / "content.json").is_file()
    manifest = json.loads((output.with_suffix(".ai") / "manifest.json").read_text())
    assert manifest["pdf"]["embedded_files"] == []


def test_assets_are_excluded_until_asked_for(embedded_capture: Path) -> None:
    names = set(PdfReader(str(embedded_capture)).attachments)
    assert not any(name.startswith("assets/") for name in names)
    # Their recognized text and context travel regardless, which is why the
    # pixels are opt-in rather than missing information.
    assert "assets.json" in names


def test_collect_payloads_skips_what_a_capture_did_not_produce(
    tmp_path: Path,
) -> None:
    (tmp_path / "content.md").write_text("# only this one\n", encoding="utf-8")
    payloads = collect_payloads(tmp_path, title="T", source="s://x")

    assert set(payloads) == {"README.txt", "content.md"}
    assert next(iter(payloads)) == "README.txt", "the guide must be listed first"
    assert payloads["content.md"].mime_type == "text/markdown"


def test_the_readme_names_every_attachment() -> None:
    """A guide that omits a file is worse than no guide."""
    names = ["content.json", "chunks.jsonl", "tables/table-001.csv"]
    text = readme_text(names, title="A page", source="https://example.com")
    for name in names:
        assert name in text
    # The one caveat a reader acting on recognized text has to be given.
    assert "machine-generated" in text


def test_every_embeddable_declares_a_usable_mime_type() -> None:
    for name, mime, description in EMBEDDABLE:
        assert "/" in mime, f"{name} has no MIME type"
        assert " " not in mime, f"{name}'s MIME type would mangle the /Subtype"
        assert description.strip(), f"{name} is embedded without saying what it is"


def test_the_sanitized_html_travels_with_the_pdf(embedded_capture: Path) -> None:
    """`content.html` was the one always-written artifact the PDF did not carry.

    It is not a duplicate of `content.md`: `docling_bridge` warns, in the
    bundle's own warnings, that a caption it could not re-attach to a nested
    table "is preserved in content.html" — so a consumer handed only the PDF
    lost data that exists nowhere else in the file (docs/09 P8-19).
    """
    names = set(PdfReader(str(embedded_capture)).attachments)
    assert "content.html" in names
    body = PdfReader(str(embedded_capture)).attachments["content.html"][0]
    # Bytes, not text: `read_text` folds the `\r\n` Windows writes into `\n`,
    # and the embedded copy is the file's bytes (docs/09 P10-24).
    sidecar = (embedded_capture.with_suffix(".ai") / "content.html").read_bytes()
    assert body == sidecar


def test_the_provenance_record_names_the_engine_that_recognized_the_text(
    embedded_capture: Path,
) -> None:
    """`tool_versions` lists what is *installed*, which under `--ocr-engine
    rapid` names two recognizers and identifies neither as the one that ran.

    The sidecar manifest already derived the actual engine; the embedded copy
    did not, so a PDF-only consumer could not tell whose confidence figures it
    was reading (docs/09 P8-22).
    """
    capture = json.loads(
        PdfReader(str(embedded_capture)).attachments["capture.json"][0].decode()
    )
    manifest = json.loads(
        (embedded_capture.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    assert "engine" in capture["ocr"], "the embedded copy must say which engine ran"
    assert capture["ocr"]["engine"] == manifest["ocr"]["engine"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("clip.mp4", "video/mp4"),
        ("recording.webm", "video/webm"),
        ("capture.MOV", "video/quicktime"),
        ("transcript.vtt", "text/vtt"),
        ("still.png", "image/png"),
        ("unknown.bin", "application/octet-stream"),
    ],
)
def test_downloaded_video_assets_keep_their_media_type(
    name: str, expected: str
) -> None:
    """`--video-assets --embed-assets` gathered `.mp4`/`.webm` into the PDF as
    `application/octet-stream`, which tells a consumer nothing and stops it
    choosing a player (docs/09 P8-20)."""
    assert _asset_mime(Path(name)) == expected


def test_the_readme_does_not_claim_tags_a_capture_was_told_not_to_make() -> None:
    """Under `--no-tagged-pdf` this sentence contradicted
    `manifest.pdf.tagged: false` — two provenance surfaces in one deliverable
    disagreeing, exactly when the user disabled the thing one of them asserted
    (docs/09 P8-21)."""
    videos = [{"transcribed": True, "title": "V", "steps": 3, "directory": "videos/v"}]
    tagged = readme_text([], title="T", source="s://x", videos=videos, tagged=True)
    untagged = readme_text([], title="T", source="s://x", videos=videos, tagged=False)
    assert "The captured pages before them do." in tagged
    assert "The captured pages before them do." not in untagged
    assert "--no-tagged-pdf" in untagged
    # The appendix half is true either way: merging never carries a tree.
    for text in (tagged, untagged):
        assert "appendix pages carry no PDF structure tags" in text
