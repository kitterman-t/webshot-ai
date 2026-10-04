"""`--pdfa`, `--validate-pdf`, and the exit codes they answer with.

The veraPDF bridge is tested without veraPDF: what matters locally is that a
missing validator degrades to a warning and that a file which never claimed
PDF/A is not reported as failing a standard it did not invoke. The real
conformance verdict comes from `tools/verapdf/check.py`, which runs the whole
composition and validates the final file.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from webshot.cli import build_parser, options_from_args
from webshot.config import CaptureResult
from webshot.errors import UsageError
from webshot.report import build_qa_report, write_report
from webshot.validate.verapdf import declared_flavour, validate

ATTRIBUTE_XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta '
    b'xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" '
    b'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
    b'pdfaid:part="3" pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>'
)
ELEMENT_XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta '
    b'xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="">'
    b'<pdfaid:part xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">3</pdfaid:part>'
    b'<pdfaid:conformance xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">B'
    b"</pdfaid:conformance></rdf:Description></rdf:RDF></x:xmpmeta>"
)


def _pdf(path: Path, xmp: bytes | None = None) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if xmp is not None:
        stream = DecodedStreamObject()
        stream.set_data(xmp)
        stream[NameObject("/Type")] = NameObject("/Metadata")
        stream[NameObject("/Subtype")] = NameObject("/XML")
        writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize(
    ("xmp", "expected"),
    [(ATTRIBUTE_XMP, "3b"), (ELEMENT_XMP, "3b"), (None, None)],
    ids=["rdf-attribute", "rdf-element", "no-identification"],
)
def test_pdfa_identification_is_read_in_both_rdf_spellings(
    tmp_path: Path, xmp: bytes | None, expected: str | None
) -> None:
    """pikepdf writes either form depending on what else is in the packet.

    Reading only the attribute form made a conformant file look like a plain
    PDF, which is a false negative the gate would have reported as "nothing to
    validate".
    """
    assert declared_flavour(_pdf(tmp_path / "sample.pdf", xmp)) == expected


def test_a_file_that_never_claimed_pdfa_is_inconclusive_not_failing(
    tmp_path: Path,
) -> None:
    report = validate(_pdf(tmp_path / "plain.pdf"))
    assert report.compliant is None
    assert report.tool is None
    assert "no PDF/A conformance level" in report.detail


def test_pdfa_on_the_web_path_is_a_usage_error() -> None:
    """ADR-0010: the conversion strips Chromium's tags, so it is refused."""
    parser = build_parser()
    arguments = parser.parse_args(["tests/fixtures/sample_notes.txt", "--pdfa"])
    with pytest.raises(UsageError) as raised:
        options_from_args(arguments)
    assert raised.value.exit_code == 2
    assert "protected-viewer" in str(raised.value)


def test_pdfa_is_accepted_on_the_protected_path() -> None:
    parser = build_parser()
    options = options_from_args(
        parser.parse_args(
            ["https://example.invalid/doc", "--protected-viewer", "--pdfa"]
        )
    )
    assert options.pdfa is True
    assert options.validate_pdf is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [([], None), (["--validate-pdf"], "report"), (["--validate-pdf=strict"], "strict")],
    ids=["absent", "bare", "strict"],
)
def test_validate_pdf_levels(argv: list[str], expected: str | None) -> None:
    parser = build_parser()
    arguments = parser.parse_args(["tests/fixtures/sample_notes.txt", *argv])
    assert options_from_args(arguments).validate_pdf == expected


def test_the_report_says_which_gates_ran_rather_than_omitting_them(
    tmp_path: Path,
) -> None:
    """QA report v2 states a gate's absence instead of implying it.

    v2.2 wrote no `validation` key at all when `--validate-pdf` had not run, so
    "was not checked" and "was checked and said nothing" looked identical.
    Schema v2 always carries the block and sets `pdfa` to null when the gate
    did not run (docs/04-spec.md §2.3).
    """
    result = CaptureResult(
        source="s",
        final_url="s",
        output="o.pdf",
        title="t",
        pages=1,
        bytes=1,
        http_status=None,
        content_type="",
        duration_seconds=0.0,
        captured_at="2026-01-01T00:00:00+00:00",
    )
    options = _options(tmp_path, None)
    destination = tmp_path / "report.json"
    write_report(result, options, destination)  # type: ignore[arg-type]
    written = json.loads(destination.read_text("utf-8"))
    assert written["schema_version"] == "2"
    assert written["validation"]["pdfa"] is None
    assert written["validation"]["pdf_readable"] is True
    assert written["exit_code"] == 0

    result.validation = {
        "tool": "veraPDF 1.30.0",
        "flavour": "PDF/A-3B",
        "compliant": True,
        "passed_rules": 146,
        "failed_rules": 0,
        "failed_checks": 0,
        "failures": [],
        "report_file": None,
        "detail": "conformant",
    }
    write_report(result, options, destination)  # type: ignore[arg-type]
    assert (
        json.loads(destination.read_text("utf-8"))["validation"]["pdfa"]["compliant"]
        is True
    )


def test_the_report_records_the_options_without_the_credential_paths(
    tmp_path: Path,
) -> None:
    """ "Options as resolved" is not "every option": §6.1's spirit, one artifact wider."""
    profile = tmp_path / "profile"
    parser = build_parser()
    arguments = parser.parse_args(
        ["https://example.com", "--auth-profile", str(profile), "--scale", "1.1"]
    )
    profile.mkdir()
    options = options_from_args(arguments)
    report = build_qa_report(
        CaptureResult(
            source="https://example.com",
            final_url="https://example.com",
            output="o.pdf",
            title="t",
            pages=1,
            bytes=1,
            http_status=200,
            content_type="text/html",
            duration_seconds=0.0,
            captured_at="2026-01-01T00:00:00+00:00",
        ),
        options,
    )
    payload = report.model_dump()
    assert payload["options"]["auth_mode"] == "auth-profile"
    assert payload["options"]["scale"] == 1.1
    assert str(profile) not in json.dumps(payload)


def _options(tmp_path: Path, level: str | None) -> object:
    parser = build_parser()
    arguments = parser.parse_args(
        [
            "tests/fixtures/sample_notes.txt",
            "--output",
            str(tmp_path / "out.pdf"),
            *([f"--validate-pdf={level}"] if level else []),
        ]
    )
    return options_from_args(arguments)


def test_the_gate_is_skipped_entirely_when_not_requested(tmp_path: Path) -> None:
    from webshot.pipeline import _conformance_check

    warnings: list[str] = []
    assert _conformance_check(_options(tmp_path, None), warnings) == (None, None)
    assert warnings == []


def test_a_file_with_no_pdfa_claim_warns_and_does_not_fail_strict(
    tmp_path: Path,
) -> None:
    """`strict` is about a file that was checked and failed, not one that wasn't."""
    from webshot.pipeline import _conformance_check

    options = _options(tmp_path, "strict")
    _pdf(Path(str(options.output)))  # type: ignore[attr-defined]
    warnings: list[str] = []
    record, strict_failure = _conformance_check(options, warnings)  # type: ignore[arg-type]
    assert record is not None
    assert strict_failure is None
    assert record["compliant"] is None
    assert warnings and "not established" in warnings[0]


def test_the_report_publishes_or_declares_every_capture_option() -> None:
    """A QA report that quietly drops the next option added is not evidence.

    `options` claims to be "what was asked for after every settings layer was
    reconciled". That claim has to be enforced, or the first person to add a
    flag will leave a hole nobody notices — so every `CaptureOptions` field is
    either in the published model or in the list of deliberate omissions.
    """
    from webshot.bundle.manifest import ResolvedOptions
    from webshot.config import CaptureOptions
    from webshot.report import UNPUBLISHED_OPTIONS

    fields = {field.name for field in dataclasses.fields(CaptureOptions)}
    published = {
        # The report renames a few for readability; the mapping is spelled here
        # rather than guessed, so a rename is visible as a change to this test.
        "wait_for_selector": "wait_for",
        "wait_after_load_s": "delay_seconds",
        "navigation_timeout_ms": "navigation_timeout_seconds",
        "scroll_delay_s": "scroll_delay_seconds",
        "exclude_selectors": "exclude",
        "extra_css": "css",
        "ocr_psm": "ocr_page_segmentation_mode",
    }
    reported = set(ResolvedOptions.model_fields)
    unaccounted = {
        name
        for name in fields
        if published.get(name, name) not in reported and name not in UNPUBLISHED_OPTIONS
    }
    assert not unaccounted, (
        "these capture options are neither published in the QA report nor listed "
        f"in report.UNPUBLISHED_OPTIONS: {sorted(unaccounted)}"
    )
    stale = set(UNPUBLISHED_OPTIONS) - fields
    assert not stale, f"UNPUBLISHED_OPTIONS names options that no longer exist: {stale}"


def test_the_protected_paths_page_count_invariant_is_reported(tmp_path: Path) -> None:
    """The one branch of the QA report that no golden case reaches.

    `tests/golden/protected-viewer/` records no `report.json` — the harness
    assembles that fixture directly instead of going through the CLI — so the
    `PageCountInvariant` branch had zero coverage of any kind, and renaming a
    manifest key it indexes would have kept the suite green while breaking a
    real `--protected-viewer --report` run (docs/09 P4-12).
    """
    result = CaptureResult(
        source="https://sharepoint.example/doc",
        final_url="https://sharepoint.example/doc",
        output="doc.pdf",
        title="Protected",
        pages=12,
        bytes=1,
        http_status=200,
        content_type="text/html",
        duration_seconds=1.0,
        captured_at="2026-01-01T00:00:00+00:00",
        source_kind="protected-viewer",
        ai_summary={"pages": 10, "chunks": 4, "text_characters": 900, "ocr_words": 300},
        page_count_invariant={"source_pages": 10, "appendix_pages": 2, "actual": 12},
    )
    report = build_qa_report(result, _options(tmp_path, None))  # type: ignore[arg-type]
    invariant = report.validation.page_count_invariant
    assert invariant is not None
    assert (invariant.expected, invariant.actual, invariant.passed) == (12, 12, True)
    # `counts.pages` is the document's page count, from the bundle; the composed
    # PDF's is published separately and includes the appendix.
    assert report.counts.pages == 10
    assert report.validation.pdf_pages == 12


def test_a_lost_page_makes_the_invariant_fail(tmp_path: Path) -> None:
    """The check has to be able to say no.

    `expected` comes from the manifest's source + appendix counts and `actual`
    from a fresh read of the published file. Deriving both from one record made
    them equal by construction, so the gate could never fire (docs/09 P4-12).
    """
    result = CaptureResult(
        source="https://sharepoint.example/doc",
        final_url="https://sharepoint.example/doc",
        output="doc.pdf",
        title="Protected",
        pages=11,  # the published file is one page short
        bytes=1,
        http_status=200,
        content_type="text/html",
        duration_seconds=1.0,
        captured_at="2026-01-01T00:00:00+00:00",
        source_kind="protected-viewer",
        ai_summary={"pages": 10, "chunks": 4, "text_characters": 900, "ocr_words": 300},
        page_count_invariant={"source_pages": 10, "appendix_pages": 2, "actual": 11},
    )
    invariant = build_qa_report(
        result, _options(tmp_path, None)
    ).validation.page_count_invariant  # type: ignore[arg-type]
    assert invariant is not None
    assert invariant.passed is False
    assert (invariant.expected, invariant.actual) == (12, 11)


def test_the_pipelines_summary_keys_are_the_ones_the_report_reads() -> None:
    """`ai_summary` is an untyped dict between the pipeline and the report.

    `counts()` reads it with `.get()`, which is right — a key a capture path
    does not produce must be absent, not an error — but it means a *renamed*
    key silently publishes `null` instead of a number. This pins the contract
    the two ends share (docs/09 P4-12).
    """
    import inspect

    from webshot import pipeline

    # The module rather than one function: the capture body has already been
    # split once (the output lock made `convert_url_to_pdf` a wrapper), and a
    # contract test that breaks on a refactor teaches people to weaken it.
    source = inspect.getsource(pipeline)
    for key in (
        "items",
        "tables",
        "links",
        "chunks",
        "visual_assets",
        "text_characters",
        "ocr_words",
        "pages",
    ):
        assert f'"{key}"' in source, (
            f"the pipeline no longer emits an ai_summary key named {key!r}; "
            "report.counts() reads it with .get() and would publish null"
        )
