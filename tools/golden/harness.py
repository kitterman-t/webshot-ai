"""Record and re-check golden bundles for the fixture corpus.

The golden corpus is WebShot's parity yardstick: every later phase has to keep
producing the same artifacts for the same inputs, and "the same" needs a
definition that survives clock ticks, temporary directories, and tool upgrades.

Two comparison profiles exist (docs/06-quality-and-testing.md):

`strict`
    Everything is byte-compared after the volatile-value normalization below:
    timestamps, absolute paths, tool versions, and the PDF's own byte size and
    checksum.  Raster assets are compared by format and dimensions rather than
    pixel bytes.  This is the profile that catches refactoring mistakes, and it
    is used whenever the current environment matches the one the golden was
    recorded in.

`portable`
    Everything `strict` does, plus: OCR-derived text, confidences, and counts
    become placeholders, and the versions in the PDF producer and OCR engine
    strings are masked.  Tesseract and Chromium legitimately produce different
    pixels and different words across versions, so a machine that differs from
    the recording environment checks structure rather than recognition output.
    OCR is still proven to work — by the per-case sentinel assertions, which
    must be found in the recognized text on both profiles.

`check.py` picks the profile automatically from `environment.json` and says
which one it used.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # allow `python tools/golden/check.py`
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.corpus import CASES_BY_ID, CORPUS, GoldenCase  # noqa: E402
from webshot.extract.docling_bridge import OCR_CONFIDENCE_IN_READING_RE  # noqa: E402
from webshot.render.pdf import is_tagged  # noqa: E402

GOLDEN_ROOT = REPO_ROOT / "tests" / "golden"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
VIEWER_FIXTURES = FIXTURES / "viewer"

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}

TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
PDF_DATE_RE = re.compile(r"D:\d{14}(?:[+-]\d{2}'\d{2}')?")
#: Any PEP 440 release, so a 3.1.0rc1 or 3.1.0.dev0 build masks as fully as a
#: plain release does instead of leaving its suffix behind as a golden diff.
#: A local version's segments are joined by dots, which keeps a sentence's
#: closing period out of the match.
WEBSHOT_VERSION_RE = re.compile(
    r"WebShot \d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?"
    r"(?:\+[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?"
)
SKIA_RE = re.compile(r"Skia/PDF m\d+")
#: The OCR path's own version strings: OCRmyPDF names itself in the
#: manifest's `text_layer_engine`, and pikepdf rewrites the PDF producer when
#: OCRmyPDF saves. They are the Skia string's counterparts on that path, so a
#: dependency bump that changes nothing but these is not an output change
#: (docs/09 P20-11).
OCR_ENGINE_VERSION_RE = re.compile(
    r"\b(OCRmyPDF|pikepdf) \d+(?:\.\d+)+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?"
)
OCR_PREFIX = "Text recognized within visual asset"

# Masked numbers stay numbers: a golden must still validate against the frozen
# contract models in src/webshot/bundle/manifest.py, so a placeholder may never
# change a field's type.  -1 is impossible for every masked field (byte counts,
# character counts, confidences), which keeps it readable as "not compared".
MASKED_INT = -1
MASKED_FLOAT = -1.0
# The string counterpart, for values that are neither counts nor measurements:
# the QA report's manifest checksum covers a file containing the capture
# timestamp, so it is different on every run by construction.
MASKED_TEXT = "<not compared>"

#: A chunk's own digest. Masked because it is taken over the text *before* this
#: harness normalizes it, so for any chunk quoting a `file://` source it is a
#: hash of the recording machine's checkout path. `chunk_digest_failures`
#: checks the property this value used to stand in for (docs/09 P7-13).
MASKED_CHUNK_DIGEST = "<CHUNK-SHA256>"
#: A raster's digest moves with the fonts that drew its text (P7-14).
MASKED_RASTER_DIGEST = "<RASTER-SHA256>"
#: The confidence in the line that opens recognized text on a reading surface
#: (docs/09 P20-4). It is the recognizer's figure, masked as its words are.
MASKED_OCR_CONFIDENCE = "<OCR-CONFIDENCE>"

# Blanket text replacement of recognized words is a blunt instrument: a short
# recognized line ("240", "Q1") also occurs inside byte counts, dimensions, and
# checksums, where replacing it would both mask real regressions and produce
# malformed JSON.  Short lines are therefore masked only in the fields that are
# *declared* to hold OCR output, never by substring search.
MIN_SCRUBBABLE_FRAGMENT = 8


# --------------------------------------------------------------------------- #
# Running a case
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RunArtifacts:
    case: GoldenCase
    workdir: Path
    pdf: Path
    bundle: Path
    report: Path
    exit_code: int
    stderr: str
    argv: list[str] = field(default_factory=list)


def default_entrypoint() -> list[str]:
    return [sys.executable, "-m", "webshot"]


def run_case(case: GoldenCase, workdir: Path, entrypoint: list[str]) -> RunArtifacts:
    """Produce one case's artifacts inside `workdir`."""
    workdir.mkdir(parents=True, exist_ok=True)
    pdf = workdir / f"{case.id}.pdf"
    bundle = workdir / f"{case.id}.ai"
    report = workdir / "report.json"

    if case.kind == "protected":
        return _run_protected_case(case, workdir, pdf, bundle, report)
    if case.kind == "captions":
        return _run_captions_case(case, workdir, pdf, bundle, report)
    if case.kind == "video":
        return _run_video_case(case, workdir, pdf, bundle, report)

    argv = [
        *entrypoint,
        case.source,
        "--output",
        str(pdf),
        "--report",
        str(report),
        *case.args,
    ]
    completed = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_clean_environment(),
        check=False,
    )
    return RunArtifacts(
        case=case,
        workdir=workdir,
        pdf=pdf,
        bundle=bundle,
        report=report,
        exit_code=completed.returncode,
        stderr=completed.stderr,
        argv=[case.source, "--output", "<OUT>", "--report", "<OUT>", *case.args],
    )


VIEWER_SOURCE_URL = "https://fixture.invalid/protected/quarterly-operations-review"
VIEWER_TITLE = "Quarterly Operations Review"


def assemble_viewer_fixture(
    workdir: Path, pdf: Path, bundle: Path, *, pdfa: bool = False
) -> dict[str, Any]:
    """Build the protected-viewer deliverables from the committed page images.

    The capture half of that path needs a live authenticated viewer, so the
    corpus starts from recorded page images: everything after the browser —
    OCR, the searchable PDF, the transcript appendix, embeds, the bundle — is
    exercised exactly as in production.  The veraPDF gate uses this too, with
    `pdfa=True`, so the file it validates is assembled the same way the golden
    corpus records.
    """
    from webshot.protected.assemble import build_protected_viewer_artifacts

    pages_directory = workdir / "pages-input"
    pages_directory.mkdir(parents=True, exist_ok=True)
    sources = sorted(VIEWER_FIXTURES.glob("page-*.png"))
    if not sources:
        raise FileNotFoundError(
            f"No protected-viewer page fixtures in {VIEWER_FIXTURES}"
        )
    for page in sources:
        shutil.copy2(page, pages_directory / page.name)

    return build_protected_viewer_artifacts(
        pages_directory,
        pdf,
        source_url=VIEWER_SOURCE_URL,
        title=VIEWER_TITLE,
        expected_pages=len(sources),
        pdfa=pdfa,
        capture_metadata={
            "viewer": "WebShot protected-viewer fixture",
            "viewer_url": VIEWER_SOURCE_URL,
            "authentication": "fixture",
        },
        ai_directory=bundle,
    )


LMS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "lms_page.html"


def _run_video_case(
    case: GoldenCase, workdir: Path, pdf: Path, bundle: Path, report: Path
) -> RunArtifacts:
    """Capture the LMS fixture with its walkthrough read from a local stand-in.

    No test may touch live Guidde (docs/06), and a recorded golden must not
    depend on a third party being up — so the share page is served from
    loopback and the pipeline is driven in-process, a command line having no
    way to be told where the stand-in is bound.

    The QA report and the log are recorded the way a `cli` case's are. Without
    them this case pinned neither `counts.chunks`, nor the resolved options,
    nor the warnings, nor a single line of what enrichment said — in the only
    case that has a video at all.
    """
    import asyncio
    import logging

    from tools.golden.guidde_standin import (
        rewritten_payload,
        standin,
    )
    from tools.golden.loopback import serve
    from webshot.acquire.router import normalize_source
    from webshot.config import CaptureOptions
    from webshot.pipeline import convert_url_to_pdf
    from webshot.report import write_report

    exit_code = 0
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger = logging.getLogger("webshot")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    holder: dict[str, bytes] = {"payload": b"{}"}
    try:
        with serve(standin(holder)) as origin:
            holder["payload"] = rewritten_payload(origin)
            options = CaptureOptions(
                source=normalize_source(str(LMS_FIXTURE)),
                output=pdf,
                ai_bundle_directory=bundle,
                guidde_origin=origin,
                # Nothing here is text in a raster, so recognition has nothing
                # to find and its absence must not make the case depend on
                # whether Tesseract is installed.
                ocr=False,
            )
            result = asyncio.run(convert_url_to_pdf(options))
        write_report(result, options, report)
        for warning in result.warnings:
            logger.warning(warning)
    except Exception as exc:  # recorded as behavior, not raised past the harness
        exit_code = 1
        log.write(f"{type(exc).__name__}: {exc}\n")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return RunArtifacts(
        case=case,
        workdir=workdir,
        pdf=pdf,
        bundle=bundle,
        report=report,
        exit_code=exit_code,
        stderr=log.getvalue(),
        argv=["<video capture of tests/fixtures/lms_page.html via a Guidde stand-in>"],
    )


def _run_captions_case(
    case: GoldenCase, workdir: Path, pdf: Path, bundle: Path, report: Path
) -> RunArtifacts:
    """Capture the caption fixture over loopback, because scheme decides this.

    `embedded-media` captures an equivalent page from a `file://` URL and must
    keep doing so — it is the recorded evidence that a declared track which
    cannot be read is reported as exactly that. But it can never exercise
    path 1: every `file://` document is an opaque origin, so the browser fetches
    the VTT and discards the cues, and a case that reads zero cues would pass
    while proving nothing (docs/09 P7-11's shape).

    Over http the captions are read, so this case serves the fixture directory
    on loopback and drives the pipeline in-process — the same reason `protected`
    and `video` do, a fixture a command line has no way to be told the address
    of. It uses its own page: `media_page.html` is the input a frozen v2 parity
    baseline was recorded from and cannot be edited (docs/09 P10-5).
    """
    import asyncio
    import http.server
    import logging

    from tools.golden.loopback import serve
    from webshot.acquire.router import normalize_source
    from webshot.config import CaptureOptions
    from webshot.pipeline import convert_url_to_pdf
    from webshot.report import write_report

    directory = str(FIXTURES)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=directory, **kwargs)  # type: ignore[arg-type]

        def log_message(self, *args: object) -> None:
            """Quiet: the harness records stderr and this is not output."""

    exit_code = 0
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger = logging.getLogger("webshot")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with serve(Handler) as origin:
            options = CaptureOptions(
                source=normalize_source(f"{origin}/captions_page.html"),
                output=pdf,
                ai_bundle_directory=bundle,
                ocr=False,
            )
            result = asyncio.run(convert_url_to_pdf(options))
        write_report(result, options, report)
        for warning in result.warnings:
            logger.warning(warning)
    except Exception as exc:  # recorded as behavior, not raised past the harness
        exit_code = 1
        log.write(f"{type(exc).__name__}: {exc}\n")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return RunArtifacts(
        case=case,
        workdir=workdir,
        pdf=pdf,
        bundle=bundle,
        report=report,
        exit_code=exit_code,
        stderr=log.getvalue(),
        argv=["<capture of tests/fixtures/captions_page.html over loopback>"],
    )


def _run_protected_case(
    case: GoldenCase, workdir: Path, pdf: Path, bundle: Path, report: Path
) -> RunArtifacts:
    """Record the protected-viewer assembly as one corpus case."""
    exit_code = 0
    stderr = ""
    try:
        assemble_viewer_fixture(workdir, pdf, bundle)
    except Exception as exc:  # recorded as behavior, not raised past the harness
        exit_code = 1
        stderr = f"{type(exc).__name__}: {exc}\n"
    return RunArtifacts(
        case=case,
        workdir=workdir,
        pdf=pdf,
        bundle=bundle,
        report=report,
        exit_code=exit_code,
        stderr=stderr,
        argv=["<protected-viewer assembly from tests/fixtures/viewer>"],
    )


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def _clean_environment() -> dict[str, str]:
    """The environment a recorded case runs in, with WebShot's own stripped.

    Phase 4 gave WebShot a settings layer that reads `WEBSHOT_*` and
    `WEBSHOT_CONFIG`, which means the corpus stopped being hermetic the moment
    it landed: a maintainer who exported `WEBSHOT_CONFIG` while trying the new
    feature would re-record every case under their personal settings — visibly
    in the report's `options`, and invisibly in the PDFs — and CI on a clean
    machine would call the whole corpus regressed (docs/09 P4-12).
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WEBSHOT_")
    } | {"PYTHONWARNINGS": "ignore", "TZ": "UTC"}


SIZE_RE = re.compile(r"\d+(?:\.\d+)? (KB|MB)\b")
#: The loopback stand-in binds port 0, so its origin differs on every run. It
#: reaches the recorded artifacts legitimately — a video's `source_url` is the
#: URL its walkthrough was actually read from — so it is normalized rather than
#: removed, the same treatment the working directory gets.
#:
#: The scheme is optional because not every leak is a URL: Trafilatura reads a
#: loopback page's `sitename` as the bare `127.0.0.1:<port>`, and Chromium's
#: print header puts the same bare host beside the title in `pdf-text.txt`. A
#: scheme-anchored mask left both, and a golden holding an ephemeral port
#: cannot pass twice — it would have failed on the next run rather than this
#: one, which is the worse way to find out.
STANDIN_RE = re.compile(r"(?:http://)?127\.0\.0\.1:\d+")


def normalize_text(text: str, workdir: Path) -> str:
    """Replace values that legitimately differ between two identical runs."""
    replacements = {
        str(workdir): "<OUT>",
        str(workdir.resolve()): "<OUT>",
        str(REPO_ROOT): "<REPO>",
        str(REPO_ROOT.resolve()): "<REPO>",
    }
    # Longest first: on macOS a temporary directory is reported both as
    # /var/folders/… and as its /private/var/folders/… resolution.
    for directory in sorted(replacements, key=len, reverse=True):
        text = text.replace(directory, replacements[directory])
    text = TIMESTAMP_RE.sub("<TIMESTAMP>", text)
    text = PDF_DATE_RE.sub("<PDFDATE>", text)
    text = WEBSHOT_VERSION_RE.sub("WebShot <VERSION>", text)
    # "Created … (2 pages, 252.4 KB)". The PDF's size is environment, not
    # output — `_normalize_manifest` has always said so by masking
    # `pdf.bytes` — and since the PDF began carrying `capture.json` the size
    # moves with the recording machine's own tool versions, which that same
    # function masks for the same reason (docs/09 P6-3). Page counts stay
    # compared; only the byte figure goes.
    text = SIZE_RE.sub(r"<SIZE> \1", text)
    text = STANDIN_RE.sub("<STANDIN>", text)
    return text


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_raster(name: str) -> bool:
    return Path(name).suffix.lower() in RASTER_SUFFIXES


def _raster_facts(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
        }


def _pdf_facts(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))

    def outline_titles(items: Any) -> list[Any]:
        titles: list[Any] = []
        for item in items:
            if isinstance(item, list):
                titles.append(outline_titles(item))
            else:
                titles.append(getattr(item, "title", None))
        return titles

    metadata = {
        key: str(value)
        for key, value in dict(reader.metadata or {}).items()
        if key not in {"/CreationDate", "/ModDate"}
    }
    return {
        "page_count": len(reader.pages),
        "page_sizes": [
            [
                round(float(page.mediabox.width), 2),
                round(float(page.mediabox.height), 2),
            ]
            for page in reader.pages
        ],
        "outline": outline_titles(reader.outline),
        "attachments": [
            {
                "name": attachment.name,
                "af_relationship": str(attachment.associated_file_relationship),
                "subtype": str(attachment.subtype),
                "description": str(attachment.description),
            }
            for attachment in reader.attachment_list
        ],
        "metadata": metadata,
    }


def _pdf_page_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts = []
    for number, page in enumerate(reader.pages, start=1):
        parts.append(f"--- page {number} ---\n{page.extract_text()}")
    return "\n".join(parts).rstrip() + "\n"


def snapshot(run: RunArtifacts) -> dict[str, str]:
    """Turn one run into the comparable, environment-normalized golden form."""
    files: dict[str, str] = {}
    rasters: dict[str, dict[str, Any]] = {}
    binaries: dict[str, dict[str, Any]] = {}
    normalized_bundle: dict[str, str] = {}

    if run.bundle.exists():
        for path in sorted(run.bundle.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(run.bundle).as_posix()
            if _is_raster(relative):
                rasters[relative] = _raster_facts(path)
                continue
            try:
                content = normalize_text(path.read_text(encoding="utf-8"), run.workdir)
            except UnicodeDecodeError:
                # A preserved source copy can be any file the user captured —
                # an office document, an archive. Record it by size and digest
                # rather than refusing to record the case at all.
                data = path.read_bytes()
                binaries[relative] = {"bytes": len(data), "sha256": _sha256(data)}
                continue
            if relative.endswith("chunks.jsonl"):
                content = _normalize_chunks(content)
            normalized_bundle[relative] = content

        for relative, content in normalized_bundle.items():
            if relative == "manifest.json":
                continue
            files[f"bundle/{relative}"] = content

        manifest_text = normalized_bundle.get("manifest.json")
        if manifest_text is not None:
            files["bundle/manifest.json"] = _normalize_manifest(
                manifest_text, normalized_bundle, rasters, binaries
            )

    files["bundle-rasters.json"] = _dump(rasters)
    if binaries:
        files["bundle-binaries.json"] = _dump(binaries)

    if run.pdf.exists():
        files["pdf.json"] = normalize_text(_dump(_pdf_facts(run.pdf)), run.workdir)
        files["pdf-text.txt"] = normalize_text(_pdf_page_text(run.pdf), run.workdir)

    if run.report.exists():
        report = json.loads(normalize_text(run.report.read_text("utf-8"), run.workdir))
        report["duration_seconds"] = MASKED_FLOAT
        report["bytes"] = MASKED_INT
        # QA report v2 (docs/04-spec.md §2.3) adds two more values that differ
        # on every run by construction rather than by regression: per-stage
        # wall clock, and the checksum of a manifest that carries the capture
        # timestamp.
        if isinstance(report.get("timings"), dict):
            report["timings"] = dict.fromkeys(report["timings"], MASKED_FLOAT)
        if report.get("manifest_sha256"):
            report["manifest_sha256"] = MASKED_TEXT
        files["report.json"] = _dump(report)

    files["run.json"] = _dump(
        {
            "case": run.case.id,
            "kind": run.case.kind,
            "argv": run.argv,
            "exit_code": run.exit_code,
            "bundle_present": run.bundle.exists(),
            "pdf_present": run.pdf.exists(),
        }
    )
    files["stderr.txt"] = normalize_text(run.stderr, run.workdir)
    return files


def _normalize_chunks(text: str) -> str:
    """Replace each chunk's own digest with a placeholder.

    Only the digest: the `text` it describes is compared byte for byte as
    usual, and `chunk_digest_failures` proves the two agree in the actual
    output. Line-by-line so a malformed line is left alone rather than losing
    the whole file.

    Split at `\\n` only, as every reader of `chunks.jsonl` here is: the writer
    leaves U+0085, U+2028 and U+2029 raw inside a record, and `splitlines()`
    cut such a record in two, left both halves unmasked and rejoined them
    with `\\n` (docs/09 P10-28). Rejoining at `\\n` returns the text's own
    final newline, so none is added.
    """
    lines = []
    for line in text.split("\n"):
        try:
            record = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            lines.append(line)
            continue
        if record is None:
            lines.append(line)
            continue
        meta = record.get("meta")
        if isinstance(meta, dict) and "sha256" in meta:
            meta["sha256"] = MASKED_CHUNK_DIGEST
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines)


def _normalize_manifest(
    manifest_text: str,
    normalized_bundle: dict[str, str],
    rasters: dict[str, dict[str, Any]],
    binaries: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Re-checksum the manifest against the *normalized* bundle contents.

    A manifest records byte sizes and SHA-256 of files that contain a capture
    timestamp, so its checksums cannot be compared as recorded.  Recomputing
    them over the normalized text keeps the integrity signal — any real content
    change still moves the hash — while raster entries fall back to the
    dimensions-and-format policy.
    """
    manifest = json.loads(manifest_text)
    manifest["captured_at"] = "<TIMESTAMP>"
    manifest["generator_version"] = "<VERSION>"
    # Upstream library versions are environment, not output (06: tool-version
    # strings are normalized before comparison) — and a absent tool stays
    # visibly absent.
    if isinstance(manifest.get("tool_versions"), dict):
        manifest["tool_versions"] = {
            tool: None if value is None else "<VERSION>"
            for tool, value in manifest["tool_versions"].items()
        }

    pdf_record = manifest.get("pdf")
    if isinstance(pdf_record, dict):
        pdf_record["bytes"] = MASKED_INT
        pdf_record["sha256"] = "<PDF-SHA256>"

    def rewrite(relative: str, extras: dict[str, Any]) -> dict[str, Any]:
        # Flags like `legacy: true` are contract, not content — they survive.
        keep = {key: value for key, value in extras.items() if key == "legacy"}
        if relative in rasters:
            return {"bytes": MASKED_INT, "sha256": "<RASTER-SHA256>", **keep}
        if binaries and relative in binaries:
            return {**binaries[relative], **keep}
        content = normalized_bundle.get(relative)
        if content is None:
            return {"bytes": MASKED_INT, "sha256": "<MISSING>", **keep}
        data = content.encode("utf-8")
        return {"bytes": len(data), "sha256": _sha256(data), **keep}

    files = manifest.get("files")
    if isinstance(files, dict):  # web-path manifest
        manifest["files"] = {
            relative: rewrite(relative, files[relative]) for relative in sorted(files)
        }
    elif isinstance(files, list):  # protected-viewer manifest
        manifest["files"] = [
            {"file": entry["file"], **rewrite(entry["file"], entry)} for entry in files
        ]
    return _dump(manifest)


# --------------------------------------------------------------------------- #
# Portable profile: drop OCR- and browser-version-dependent values
# --------------------------------------------------------------------------- #


def _load_json(files: dict[str, str], name: str) -> Any:
    """Parse one snapshot artifact, blaming the artifact if it is not JSON."""
    try:
        return json.loads(files[name])
    except json.JSONDecodeError as exc:
        raise ValueError(f"golden artifact {name} is not valid JSON: {exc}") from exc


#: The artifacts that can carry declared OCR fields, in either bundle format.
#: The legacy (v2) bundle carries the same `visual_assets` shape, and was
#: missing here: its OCR text was scrubbed incidentally by the fragment
#: pass while its confidence and raster digest were never masked at all,
#: so the portable profile did not in fact cover that artifact.
_OCR_BEARING_ARTIFACTS = (
    "bundle/content.json",
    "bundle/assets.json",
    "bundle/legacy/content.json",
)


def _visit_ocr_record(ocr: Any, *, mask: bool, deep: bool) -> list[str]:
    """Collect (and optionally mask) one `ocr` record's recognition output.

    `deep` is the viewer-page shape, which also carries word boxes, counts,
    and an alternate reading.
    """
    found: list[str] = []
    if not isinstance(ocr, dict):
        return found
    if ocr.get("text"):
        found.append(ocr["text"])
        if mask:
            ocr["text"] = "<OCR-TEXT>"
    if mask and ocr.get("confidence") is not None:
        ocr["confidence"] = MASKED_FLOAT
    if deep:
        if mask:
            ocr["word_count"] = MASKED_INT
            ocr["words"] = []
        alternate = ocr.get("alternate")
        if isinstance(alternate, dict):
            if alternate.get("text"):
                found.append(alternate["text"])
                if mask:
                    alternate["text"] = "<OCR-TEXT>"
            if mask and alternate.get("confidence") is not None:
                alternate["confidence"] = MASKED_FLOAT
    return found


def _raster_records(data: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every record that names a rasterized asset and carries its digest.

    One map, two readers: `asset_digest_failures` asserts these digests
    against the files, and the portable profile masks them. Keeping the
    shapes in a single place is the same precaution `_visit_ocr_fields`
    documents -- a collector and a masker that each carry their own copy of
    the layout drift apart silently.
    """
    records: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(data, dict):
        return records
    for index, asset in enumerate(data.get("visual_assets") or []):
        if isinstance(asset, dict):
            records.append((asset.get("id") or f"visual_assets[{index}]", asset))
    for index, picture in enumerate(data.get("pictures") or []):
        if not isinstance(picture, dict):
            continue
        for position, annotation in enumerate(picture.get("annotations") or []):
            if not isinstance(annotation, dict):
                continue
            webshot_record = (annotation.get("content") or {}).get("webshot")
            if isinstance(webshot_record, dict):
                label = (
                    webshot_record.get("asset_id")
                    or f"pictures[{index}].annotations[{position}]"
                )
                records.append((label, webshot_record))
    return records


def _visit_ocr_fields(
    data: Any, *, mask: bool, mask_raster_digests: bool = False
) -> list[str]:
    """Walk every field declared to hold recognition output, in one pass.

    One walker serves both questions the portable profile asks — "which
    strings did Tesseract produce" (for the fragment scrub) and "mask them
    structurally" — so the per-format map of where OCR lives cannot drift
    between a collector and a masker.  Handles every shape: v2 asset records,
    viewer page records, and format-3 assets.json plus picture annotations.
    """
    found: list[str] = []
    for asset in data.get("visual_assets", []):
        found.extend(_visit_ocr_record(asset.get("ocr"), mask=mask, deep=False))
    for page in data.get("pages", []):
        found.extend(_visit_ocr_record(page.get("ocr"), mask=mask, deep=True))
    for picture in data.get("pictures", []):
        for annotation in picture.get("annotations", []):
            if annotation.get("kind") == "description" and annotation.get("text"):
                found.append(annotation["text"])
                if mask:
                    annotation["text"] = "<OCR-TEXT>"
            webshot_record = (annotation.get("content") or {}).get("webshot")
            if isinstance(webshot_record, dict):
                _visit_ocr_record(webshot_record.get("ocr"), mask=mask, deep=False)
    if mask_raster_digests:
        # A digest over pixels Chromium drew is a fact about the font stack,
        # not about WebShot (P7-14). `asset_digest_failures` asserts it.
        for _, record in _raster_records(data):
            if record.get("sha256") is not None:
                record["sha256"] = MASKED_RASTER_DIGEST
    return found


PAGE_MARKER_RE = re.compile(r"^--- page \d+ ---$", re.MULTILINE)


def _mask_ocr_pdf_text(files: dict[str, str]) -> dict[str, str]:
    """Blank the body of an OCR-derived PDF text dump, keeping its page markers.

    On the protected path every word in the PDF's text layer came out of
    Tesseract — through OCRmyPDF, whose reading is its own and need not match
    the TSV pass fragment for fragment.  Page count and page order still get
    compared; the words are proven by the sentinels instead.
    """
    result = dict(files)
    if "pdf-text.txt" in result:
        markers = PAGE_MARKER_RE.findall(result["pdf-text.txt"])
        result["pdf-text.txt"] = (
            "\n".join(f"{marker}\n<OCR-TEXT>" for marker in markers) + "\n"
        )
    if "bundle/ocr/text-layer.txt" in result:
        result["bundle/ocr/text-layer.txt"] = "<OCR-TEXT>\n"
    return result


def portable(files: dict[str, str], *, ocr_pdf_text: bool = False) -> dict[str, str]:
    """Reduce a snapshot to what is stable across Tesseract/Chromium versions.

    `ocr_pdf_text` is the case's own declaration that its PDF text layer and
    appendix are recognition output rather than rendered text; it drives every
    masking that follows from that fact, so the profile never has to guess by
    sniffing the artifacts.
    """
    if ocr_pdf_text:
        files = _mask_ocr_pdf_text(files)
    # One parse per OCR-bearing artifact: collect the recognized strings and
    # mask their fields in the same walk, then re-serialize.
    files = dict(files)
    strings: list[str] = []
    for name in _OCR_BEARING_ARTIFACTS:
        if name not in files:
            continue
        data = _load_json(files, name)
        strings.extend(_visit_ocr_fields(data, mask=True, mask_raster_digests=True))
        files[name] = _dump(data)
    fragments = sorted(
        {
            fragment
            for text in strings
            for fragment in (text, *text.splitlines())
            if len(fragment.strip()) >= MIN_SCRUBBABLE_FRAGMENT
        },
        key=len,
        reverse=True,
    )

    def scrub(name: str, text: str) -> str:
        for fragment in fragments:
            text = text.replace(fragment.strip(), "<OCR-TEXT>")
        # Stop at a quote so scrubbing inside a JSON string leaves valid JSON.
        text = re.sub(
            rf"{re.escape(OCR_PREFIX)}[^\n\"]*", f"{OCR_PREFIX} <OCR-TEXT>", text
        )
        text = OCR_CONFIDENCE_IN_READING_RE.sub(
            rf"\g<1>{MASKED_OCR_CONFIDENCE}\g<3>", text
        )
        text = SKIA_RE.sub("Skia/PDF <VERSION>", text)
        text = OCR_ENGINE_VERSION_RE.sub(r"\1 <VERSION>", text)
        return text

    result = {name: scrub(name, text) for name, text in files.items()}

    for name in ("bundle/manifest.json",):
        if name not in result:
            continue
        manifest = _load_json(result, name)
        pdf_record = manifest.get("pdf")
        if ocr_pdf_text and isinstance(pdf_record, dict):
            # The appendix is paginated by Chromium from recognized text, so its
            # length moves with both the browser build and the OCR reading. The
            # page-count invariant is asserted in code, on every profile.
            # Only keys the manifest already has: inventing one would make the
            # golden fail its own contract model rather than the comparison.
            for key in ("pages", "transcript_appendix_pages"):
                if key in pdf_record:
                    pdf_record[key] = MASKED_INT
        counts = manifest.get("content")
        if isinstance(counts, dict):
            for key in (
                "text_characters",
                "chunks",
                "ocr_words",
                "primary_ocr_text_characters",
                "alternate_ocr_pages",
            ):
                if key in counts:
                    counts[key] = MASKED_INT
            if "ocr_mean_confidence" in counts:
                counts["ocr_mean_confidence"] = MASKED_FLOAT
        result[name] = _dump(manifest)

    if "report.json" in result:
        report = _load_json(result, "report.json")
        counts = report.get("counts")
        if isinstance(counts, dict):
            for key in ("chunks", "text_characters", "ocr_words"):
                if counts.get(key) is not None:
                    counts[key] = MASKED_INT
        result["report.json"] = _dump(report)

    # Recomputed checksums cover files whose OCR text was just scrubbed.
    if "bundle/manifest.json" in result:
        manifest = _load_json(result, "bundle/manifest.json")
        files_record = manifest.get("files")

        def recompute(relative: str, previous: dict[str, Any]) -> dict[str, Any]:
            content = result.get(f"bundle/{relative}")
            if content is None or previous.get("bytes") == MASKED_INT:
                return previous
            data = content.encode("utf-8")
            return {"bytes": len(data), "sha256": _sha256(data)}

        if isinstance(files_record, dict):
            manifest["files"] = {
                relative: recompute(relative, record)
                for relative, record in files_record.items()
            }
        elif isinstance(files_record, list):
            manifest["files"] = [
                {"file": entry["file"], **recompute(entry["file"], entry)}
                for entry in files_record
            ]
        result["bundle/manifest.json"] = _dump(manifest)
    return result


# --------------------------------------------------------------------------- #
# Sentinels, environment, storage, comparison
# --------------------------------------------------------------------------- #


def chunk_digest_failures(run: RunArtifacts) -> list[str]:
    """Every chunk's `sha256` must be the digest of its own `text`.

    Asserted rather than recorded, and the distinction is the whole point. The
    digest is taken over the text *as written*, which for a `file://` capture
    contains the checkout's absolute path — so recording it pinned a value that
    is correct on the recording machine and wrong everywhere else, and CI
    failed on two chunks whose text was byte-identical (docs/09 P7-13).

    Masking the value alone would have exempted exactly the chunks that
    exposed the problem, which is how a genuinely wrong digest gets through
    later. Checking the property instead keeps every chunk covered: a hash that
    does not describe its own text fails here, on any machine, while the text
    itself stays byte-compared through the normalized snapshot.
    """
    chunks = run.bundle / "chunks.jsonl"
    if not chunks.is_file():
        return []
    problems = []
    for number, line in enumerate(chunks.read_text("utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"chunks.jsonl line {number} is not JSON: {exc}")
            continue
        recorded = (record.get("meta") or {}).get("sha256")
        if recorded is None:
            continue  # the protected-viewer chunk shape carries no digest
        expected = hashlib.sha256(
            str(record.get("text", "")).encode("utf-8")
        ).hexdigest()
        if recorded != expected:
            problems.append(
                f"chunk {record.get('id', number)}: sha256 {recorded} does not "
                f"match the digest of its own text ({expected})"
            )
    return problems


def asset_digest_failures(run: RunArtifacts) -> list[str]:
    """Every visual asset's `sha256` must be the digest of the raster it names.

    Asserted rather than compared, for the reason `chunk_digest_failures`
    exists (P7-13) and with a different environmental dependency behind it.
    The digest is taken over a PNG that Chromium rasterized, so it moves with
    the *font stack*: a macOS update on 2026-09-03 rewrote `Helvetica.ttc`,
    which is what `sans-serif` resolves to, and every corpus case whose raster
    contains rendered text changed digest while its OCR read the same string.
    Rasters with no text in them did not move at all (docs/09 P7-14).

    Recording that digest pinned a fact about the recording machine's fonts.
    The portable profile therefore masks it, and this assertion is what keeps
    the published value covered: a record whose digest does not describe its
    own file fails here, on every profile and every machine, which is the
    property a consumer verifying a bundle actually relies on.

    It reads exactly the artifacts the portable profile masks, from the same
    tuple. It used to carry its own list, which left out
    `legacy/content.json`, so that file's raster digest was masked and never
    checked: the same drift `_raster_records` exists to prevent, one level up
    (docs/09 P18-1). Every `file` is bundle-relative, the legacy one included:
    `legacy/` holds only the two JSON artifacts, and the rasters they name are
    the bundle's own.
    """
    problems: list[str] = []
    for artifact in _OCR_BEARING_ARTIFACTS:
        relative = artifact.removeprefix("bundle/")
        path = run.bundle / relative
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{relative} could not be read: {exc}")
            continue
        for label, record in _raster_records(data):
            named = record.get("file")
            recorded = record.get("sha256")
            if recorded is None:
                continue  # no digest published, so none to verify or to mask
            if not named:
                # A published digest with nothing to verify it against is not
                # a pass. It was skipped, and it is also exactly the value the
                # portable profile masks, so nothing looked at it at all.
                problems.append(
                    f"{relative} {label}: publishes sha256 {recorded} but names "
                    "no file it is the digest of"
                )
                continue
            asset = run.bundle / named
            if not asset.is_file():
                problems.append(
                    f"{relative} {label}: names {named}, which is not in the bundle"
                )
                continue
            expected = hashlib.sha256(asset.read_bytes()).hexdigest()
            if recorded != expected:
                problems.append(
                    f"{relative} {label}: sha256 {recorded} does not match the "
                    f"digest of {named} ({expected})"
                )
    return problems


def manifest_digest_failures(run: RunArtifacts) -> list[str]:
    """Every `files` record must describe the file it names, on disk.

    Asserted rather than compared, and the distinction is load-bearing here in
    a way that is easy to miss. `normalized_snapshot` deliberately *replaces*
    each record's `bytes` and `sha256` with values recomputed over the
    normalized text — it has to, because it masks OCR output and absolute
    paths, so a digest of the raw file could not match on a second machine.

    The consequence is that a capture emitting a *wrong* checksum passed both
    profiles: the harness threw the published value away before comparing, so
    the one thing a consumer uses these fields for — verifying a bundle it was
    handed — was the one property the corpus could not see (docs/09 P8-42).

    So the published values are checked here, against the files as written,
    before any normalization touches them. Same shape as
    `chunk_digest_failures`: normalize for comparison, assert the derivation.
    """
    manifest_path = run.bundle / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json could not be read: {exc}"]

    files = manifest.get("files")
    if isinstance(files, dict):
        records = [(name, record) for name, record in files.items()]
    elif isinstance(files, list):
        records = [(entry.get("file", ""), entry) for entry in files]
    else:
        return []

    problems = []
    for relative, record in records:
        if not isinstance(record, dict) or not relative:
            continue
        path = run.bundle / relative
        if not path.is_file():
            problems.append(f"manifest names {relative}, which was not written")
            continue
        data = path.read_bytes()
        if record.get("bytes") != len(data):
            problems.append(
                f"manifest says {relative} is {record.get('bytes')} bytes; "
                f"it is {len(data)}"
            )
        actual = hashlib.sha256(data).hexdigest()
        if record.get("sha256") != actual:
            problems.append(
                f"manifest's sha256 for {relative} does not describe the file "
                f"({record.get('sha256')} vs {actual})"
            )

    pdf_record = manifest.get("pdf")
    if isinstance(pdf_record, dict) and run.pdf.is_file():
        data = run.pdf.read_bytes()
        if pdf_record.get("bytes") not in (None, len(data)):
            problems.append(
                f"manifest says the PDF is {pdf_record.get('bytes')} bytes; "
                f"it is {len(data)}"
            )
        actual = hashlib.sha256(data).hexdigest()
        if pdf_record.get("sha256") not in (None, actual):
            problems.append("manifest's PDF sha256 does not describe the PDF")
    return problems


def chunk_pointer_failures(run: RunArtifacts) -> list[str]:
    """Every chunk's `doc_items` must resolve in the document it names.

    Asserted, not recorded, for the reason `chunk_digest_failures` is: a
    pointer recorded as a string is a string, and a golden that pins
    `#/videos/<id>/steps/1` cannot tell that nothing is there. It was not: the
    array in `steps.json` is at the root and is zero-based, and the file has no
    `videos` key at all — so `doc_items` addressed a real item on page chunks
    and nothing on video chunks, while both looked equally plausible in the
    recording (docs/09 P8-23).

    A pointer needs a document. Page chunks resolve in `content.json`; a video
    chunk names its own in `meta.video.steps_document`, and this resolves both
    against the bundle actually produced.
    """
    chunks = run.bundle / "chunks.jsonl"
    if not chunks.is_file():
        return []
    documents: dict[str, Any] = {}

    def load(relative: str) -> Any:
        if relative not in documents:
            path = run.bundle / relative
            documents[relative] = (
                json.loads(path.read_text("utf-8")) if path.is_file() else None
            )
        return documents[relative]

    problems = []
    for number, line in enumerate(chunks.read_text("utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        meta = record.get("meta") or {}
        kind = meta.get("kind")
        if kind == "video_step":
            relative = (meta.get("video") or {}).get("steps_document")
            if not relative:
                problems.append(
                    f"chunk {record.get('id', number)}: a video_step chunk names "
                    "no steps_document, so its doc_items point into nothing"
                )
                continue
        elif kind in {"text", "table", "figure"}:
            relative = "content.json"
        else:
            continue  # untranscribed_media stands in for absent content
        document = load(relative)
        if document is None:
            problems.append(
                f"chunk {record.get('id', number)}: {relative} does not exist"
            )
            continue
        for pointer in meta.get("doc_items", []):
            if _resolve_pointer(document, pointer) is _UNRESOLVED:
                problems.append(
                    f"chunk {record.get('id', number)}: doc_items {pointer!r} does "
                    f"not resolve in {relative}"
                )
    return problems


#: Distinct from `None`, which is a value a pointer may legitimately reach.
_UNRESOLVED = object()


def _resolve_pointer(document: Any, pointer: str) -> Any:
    """RFC 6901, enough of it: `#/a/0/b`, with `~1` and `~0` unescaped."""
    if not pointer.startswith("#/"):
        return _UNRESOLVED
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return _UNRESOLVED
            current = current[int(token)]
        elif isinstance(current, dict):
            if token not in current:
                return _UNRESOLVED
            current = current[token]
        else:
            return _UNRESOLVED
    return current


def structure_failures(case: GoldenCase, run: RunArtifacts) -> list[str]:
    """Check the PDF really is what the case says it is.

    Tagging and bookmarks are *requested* through `page.pdf(tagged=…,
    outline=…)`; docs/05 task 1.1 is about proving the request was honored, in
    both directions.  These are assertions rather than recorded values on
    purpose — a recorded fact can be re-recorded away, an assertion cannot.
    """
    if not run.pdf.exists():
        return []
    reader = PdfReader(str(run.pdf))
    # The product's own predicate, so the manifest's `tagged` claim and this
    # assertion can never mean two different things.
    tagged = is_tagged(run.pdf)
    problems = []
    if tagged != case.tagged:
        root = reader.root_object
        problems.append(
            f"PDF is {'tagged' if tagged else 'not tagged'} "
            f"(StructTreeRoot={'/StructTreeRoot' in root}, "
            f"MarkInfo={root.get('/MarkInfo')}), but the case expects "
            f"{'tagged' if case.tagged else 'untagged'}"
        )
    has_outline = bool(reader.outline)
    if has_outline != case.outline:
        problems.append(
            f"PDF {'has' if has_outline else 'has no'} bookmarks, but the case "
            f"expects {'bookmarks' if case.outline else 'none'}"
        )
    return problems


def sentinel_failures(case: GoldenCase, files: dict[str, str]) -> list[str]:
    """Check the strings a case says must be present, and those that must not.

    Presence was the original job: text that exists only inside a raster proves
    OCR ran.  It now also carries the render-versus-DOM relation — a sentinel
    inside a CSS-truncated region, named against both `bundle/content.md` and
    `pdf-text.txt`, fails exactly when the DOM keeps a paragraph the render
    drops.

    Absence is the inverse and cannot be expressed by the first loop: an
    element the capture hid must stay out of the render, and a recorded golden
    would not catch its return because the un-hidden text would simply be
    recorded as the expected output.
    """
    failures = []
    for sentinel in case.sentinels:
        for artifact in case.sentinel_artifacts:
            content = files.get(artifact)
            if content is None:
                failures.append(f"{artifact} missing, cannot look for {sentinel!r}")
            elif sentinel not in content:
                failures.append(f"{sentinel!r} not found in {artifact}")
    for sentinel in case.absent_sentinels:
        for artifact in case.absent_sentinel_artifacts:
            content = files.get(artifact)
            if content is None:
                failures.append(
                    f"{artifact} missing, cannot confirm {sentinel!r} is absent"
                )
            elif sentinel in content:
                failures.append(f"{sentinel!r} found in {artifact}, and must not be")
    return failures


def environment() -> dict[str, str]:
    from importlib.metadata import version

    tesseract = "absent"
    executable = shutil.which("tesseract")
    if executable:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False
        )
        tesseract = (
            completed.stdout.splitlines()[0].strip() if completed.stdout else "?"
        )
    return {
        "platform": platform.system(),
        "python": ".".join(platform.python_version_tuple()[:2]),
        "playwright": version("playwright"),
        "tesseract": tesseract,
        "fonts": font_fingerprint(),
    }


#: Where the system fonts a headless browser resolves `sans-serif` to live.
#: Only directories that exist are read, so this is one stat pass per platform.
#: Each is read recursively: Linux keeps its fonts in per-package
#: subdirectories (`/usr/share/fonts/truetype/dejavu/`), and so does macOS for
#: `Supplemental/`, which is why that one no longer needs an entry of its own.
FONT_DIRECTORIES = (
    "/System/Library/Fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
)

_FONT_SUFFIXES = (".ttf", ".ttc", ".otf")


def font_fingerprint() -> str:
    """A short digest of the installed system fonts, by name and size.

    The corpus rasterizes text through Chromium, so the *fonts* are part of
    the recording environment as surely as the browser is -- and they were the
    one part the fingerprint did not record. A macOS update on 2026-09-03
    rewrote `Helvetica.ttc`, and the next full run reported twelve cases
    differing under the heading "environment matches the recording", which was
    true of every dimension it knew about and false of the one that moved
    (docs/09 P7-14).

    Name and size rather than content: a digest of every font's bytes costs
    hundreds of megabytes of hashing per run to answer the same question. Size
    changes when a font is rebuilt, which is the event this exists to notice.
    Sorted, so the value does not depend on directory order.

    Recursive. `iterdir()` read only each directory's top level, and a Linux
    distribution puts nothing there: every font is a package's, in a
    subdirectory. The fingerprint was therefore `"none"` on every Linux
    machine whatever was installed, and a font update there reported
    "environment matches the recording" -- the P7-14 failure this function
    exists to prevent, on the platform it never covered (docs/09 P18-2).
    A font reached twice (nested roots, a symlink) is counted once, under its
    target's name, so which path the walk met first cannot change the value.
    """
    entries: list[str] = []
    seen: set[Path] = set()
    for directory in FONT_DIRECTORIES:
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not (path.is_file() and path.suffix.lower() in _FONT_SUFFIXES):
                continue
            try:
                resolved = path.resolve()
            except OSError:  # pragma: no cover - a link we may not follow
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                entries.append(f"{resolved.name}:{resolved.stat().st_size}")
            except OSError:  # pragma: no cover - a font we may not stat
                entries.append(f"{resolved.name}:?")
    if not entries:
        return "none"
    digest = hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()
    return f"{len(entries)} fonts, {digest[:12]}"


PROFILE_KEYS = ("platform", "playwright", "tesseract", "fonts")


def profile_for(recorded: dict[str, str], current: dict[str, str]) -> tuple[str, str]:
    """Pick `strict` or `portable`, with the reason to print alongside."""
    drift = [
        # A key the recording never wrote is drift, not a match. Treating
        # "unknown" as "same" is how a dimension that moves output stays
        # invisible for as long as nobody adds it to the recording.
        f"{key}: recorded {recorded.get(key, 'not recorded')!r}, "
        f"current {current.get(key)!r}"
        for key in PROFILE_KEYS
        if recorded.get(key) != current.get(key)
    ]
    if drift:
        return "portable", "; ".join(drift)
    return "strict", "environment matches the recording"


def golden_directory(case: GoldenCase) -> Path:
    return GOLDEN_ROOT / case.id


def write_snapshot(case: GoldenCase, files: dict[str, str]) -> None:
    directory = golden_directory(case)
    if directory.exists():
        shutil.rmtree(directory)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def read_snapshot_directory(directory: Path) -> dict[str, str]:
    """Load a recorded snapshot tree into the comparable files-dict form.

    The one definition of the on-disk snapshot convention (sorted rglob,
    files only, posix-relative keys, utf-8) — the golden checker and the
    parity harness's frozen baselines both read through here.
    """
    if not directory.exists():
        return {}
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def read_snapshot(case: GoldenCase) -> dict[str, str]:
    return read_snapshot_directory(golden_directory(case))


def compare(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Human-readable report of every difference, most structural first."""
    problems: list[str] = []
    missing = sorted(set(expected) - set(actual))
    added = sorted(set(actual) - set(expected))
    for name in missing:
        problems.append(f"MISSING artifact: {name}")
    for name in added:
        problems.append(f"UNEXPECTED artifact: {name}")
    for name in sorted(set(expected) & set(actual)):
        if expected[name] == actual[name]:
            continue
        diff = list(
            difflib.unified_diff(
                # `splitlines()` would also break at U+2028 and its kin and
                # drop them, so a change to one alone showed no line (P10-28).
                expected[name].split("\n"),
                actual[name].split("\n"),
                fromfile=f"golden/{name}",
                tofile=f"actual/{name}",
                lineterm="",
                n=1,
            )
        )
        shown = diff[:40]
        if len(diff) > len(shown):
            shown.append(f"... {len(diff) - len(shown)} more diff lines")
        problems.append(f"CHANGED {name}:\n" + "\n".join(shown))
    return problems


def selected_cases(ids: list[str] | None) -> list[GoldenCase]:
    if not ids:
        return list(CORPUS)
    unknown = [value for value in ids if value not in CASES_BY_ID]
    if unknown:
        raise SystemExit(f"Unknown case id(s): {', '.join(unknown)}")
    return [CASES_BY_ID[value] for value in ids]
