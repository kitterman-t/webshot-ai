"""Flags a run cannot honour must be said, not dropped (docs/05 tasks 2.5, 3.5).

Three of these exist, and all three are usage mismatches rather than failures:
the capture is still exactly what the rest of the options asked for. What
makes them worth a test is the failure mode they replace — a flag that is
silently ignored looks like a flag that worked.

They live in one pure function so that the protected-viewer path, which needs
a live document viewer and cannot be driven from a test, is nonetheless
covered here for the decisions it makes about its own options.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webshot.config import CaptureOptions
from webshot.pipeline import ignored_option_warnings


def options(**overrides: object) -> CaptureOptions:
    return CaptureOptions(
        source="https://example.com", output=Path("out.pdf"), **overrides
    )


def test_an_ordinary_capture_has_nothing_to_report() -> None:
    assert ignored_option_warnings(options()) == []
    assert ignored_option_warnings(options(ocr_engine="rapid")) == []


def test_psm_is_reported_as_ignored_only_when_it_was_asked_for() -> None:
    asked = options(ocr_engine="rapid", ocr_psm=6, ocr_psm_explicit=True)
    assert any("--ocr-psm" in warning for warning in ignored_option_warnings(asked))
    defaulted = options(ocr_engine="rapid", ocr_psm=6)
    assert ignored_option_warnings(defaulted) == []


def test_psm_is_honoured_under_tesseract_so_nothing_is_reported() -> None:
    assert ignored_option_warnings(options(ocr_psm=6, ocr_psm_explicit=True)) == []


def _of(warnings: list[str], fragment: str) -> list[str]:
    return [warning for warning in warnings if fragment in warning]


def test_the_protected_path_says_it_ignored_a_second_engine() -> None:
    warnings = ignored_option_warnings(
        options(protected_viewer=True, ocr_engine="rapid")
    )
    engine = _of(warnings, "--ocr-engine rapid was ignored")
    assert len(engine) == 1
    assert "OCRmyPDF" in engine[0]


def test_the_protected_path_still_says_it_ignored_legacy_bundle() -> None:
    """Phase 2 behavior, moved rather than changed."""
    warnings = ignored_option_warnings(
        options(protected_viewer=True, legacy_bundle=True)
    )
    assert len(_of(warnings, "--legacy-bundle was ignored")) == 1


def test_a_run_that_skips_embedded_videos_says_so() -> None:
    """Not an ignored preference — content the deliverable does not contain.

    Enrichment writes into the AI bundle, so a run without one reads no
    walkthroughs and records no untranscribed media. Saying nothing would make
    that indistinguishable from a page that had no video at all
    (CONTRIBUTING rule 5).
    """
    for skipping in (options(ai_bundle=False), options(protected_viewer=True)):
        assert _of(ignored_option_warnings(skipping), "Embedded videos were not read")
    # A capture that does build one says nothing, because nothing was skipped.
    assert not _of(ignored_option_warnings(options()), "Embedded videos")
    # And a flag that was explicitly asked for is named in the same breath
    # rather than getting a second line of its own.
    asked = ignored_option_warnings(options(ai_bundle=False, video_assets=True))
    assert len(_of(asked, "Embedded videos were not read")) == 1
    assert _of(asked, "--video-assets had nothing to download")


def test_every_ignored_flag_is_reported_when_several_apply() -> None:
    warnings = ignored_option_warnings(
        options(
            protected_viewer=True,
            legacy_bundle=True,
            ocr_engine="rapid",
            ocr_psm=6,
            ocr_psm_explicit=True,
        )
    )
    assert len(warnings) == 3
    for fragment in (
        "--ocr-engine rapid was ignored",
        "--legacy-bundle was ignored",
        "Embedded videos were not read",
    ):
        assert len(_of(warnings, fragment)) == 1


def test_the_protected_path_does_not_claim_it_ignored_the_psm() -> None:
    """It did not: RapidOCR is what the protected path ignores, and the PSM
    goes straight to OCRmyPDF/Tesseract.

    Saying otherwise contradicted the setting that actually produced the text
    layer, in the same warnings list that correctly says the *engine* was
    ignored (docs/09 P8-31).
    """
    protected = ignored_option_warnings(
        options(
            protected_viewer=True,
            ocr_engine="rapid",
            ocr_psm=6,
            ocr_psm_explicit=True,
        )
    )
    assert not _of(protected, "--ocr-psm is a Tesseract")
    assert _of(protected, "--ocr-engine rapid was ignored")
    # The web path is unchanged: there, RapidOCR really does run and really
    # does have no page-segmentation model.
    web = ignored_option_warnings(
        options(ocr_engine="rapid", ocr_psm=6, ocr_psm_explicit=True)
    )
    assert _of(web, "--ocr-psm is a Tesseract")


def test_embed_assets_without_a_bundle_to_embed_is_reported() -> None:
    """The guard on `collect_payloads` made this succeed in silence.

    `--embed-assets --no-embed-bundle` published a PDF with no attachments and
    no warning, which reads exactly like one that embedded them
    (docs/09 P8-32).
    """
    assert _of(
        ignored_option_warnings(options(embed_assets=True, embed_bundle=False)),
        "--embed-assets was ignored",
    )
    assert _of(
        ignored_option_warnings(options(embed_assets=True, ai_bundle=False)),
        "--embed-assets was ignored",
    )
    # Both present: nothing was ignored.
    assert not _of(
        ignored_option_warnings(options(embed_assets=True)), "--embed-assets"
    )


def test_the_protected_path_says_it_cannot_honour_no_embed_bundle() -> None:
    """It embeds unconditionally, and the QA report said `embed_bundle: false`
    while the PDF carried the attachments (docs/09 P8-33)."""
    assert _of(
        ignored_option_warnings(options(protected_viewer=True, embed_bundle=False)),
        "--no-embed-bundle is not supported on the protected-viewer path",
    )


@pytest.mark.parametrize("engine", ("tesseract", "rapid"))
def test_the_web_path_never_reports_the_protected_only_rules(engine: str) -> None:
    warnings = ignored_option_warnings(options(ocr_engine=engine, legacy_bundle=True))
    assert not any("protected-viewer" in warning for warning in warnings)
