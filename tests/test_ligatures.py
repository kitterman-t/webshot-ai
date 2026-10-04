"""Ligature glyphs stay out of the PDF's text layer (docs/09 P16-2, P16-5).

Chromium records a ligature glyph in the `ToUnicode` map as its presentation
form, so a PDF that looks right answers a search for "first" with nothing. The
render and the bundle disagree about the same words, and no visual check can
see it — only a text-layer assertion does.

Every browser case here draws its text in one committed font
(`fixtures/fonts/`), and the first case proves that font ligates when nothing
turns it off. Without that control the others could pass because the font
failed to load and the fallback happened not to ligate.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from webshot.cli import main
from webshot.ocr.tesseract import tesseract_executable
from webshot.render.ligatures import (
    LIGATURES_OFF,
    LIGATURES_OFF_DECLARATION,
    _forms_in,
    ligature_forms,
    ligature_warning,
    suppress_ligatures,
)
from webshot.render.pdf import _header_footer_templates, render_document_to_pdf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE = FIXTURES / "ligatures_page.html"
UNREACHABLE = FIXTURES / "ligatures_unreachable.html"
FONT = FIXTURES / "fonts" / "ligature-fixture.ttf"

PRESENTATION_FORMS = re.compile("[ﬀ-ﬆ]")

#: One word per mechanism the fixture uses to re-enable ligatures, so a
#: failure names the one that got through.
WORDS = {
    "title in the running header": "Office flow: first staff notice",
    "plain text": "the first office flow affects baffled staff",
    "font-feature-settings boilerplate": "the official figures",
    "font-feature-settings beside tnum": "fifty offers",
    "a more specific !important": "an afflicted shuffle",
    "a pseudo-element": "Generated: fluent coffee.",
    "an open shadow root": "a flawless affirmation",
    "a frame": "the difficult fjord offices",
}


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def capture(tmp_path: Path, source: Path) -> tuple[Path, dict[str, object]]:
    output = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main(
        [str(source), "--output", str(output), "--no-ocr", "--report", str(report)]
    )
    assert code == 0
    return output, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.browser
def test_the_fixture_font_ligates_when_nothing_turns_it_off(tmp_path: Path) -> None:
    destination = tmp_path / "control.pdf"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(PAGE.as_uri(), wait_until="load")
            page.pdf(path=str(destination))
        finally:
            browser.close()
    # Every form the font has, and the text a reader gets back for them.
    assert ligature_forms(destination) == ["ﬀ", "ﬁ", "ﬂ", "ﬃ", "ﬄ"]
    assert "ﬁrst oﬃce ﬂow" in pdf_text(destination)


@pytest.mark.browser
def test_a_capture_prints_every_word_without_ligatures(tmp_path: Path) -> None:
    output, report = capture(tmp_path, PAGE)
    text = pdf_text(output)
    assert not PRESENTATION_FORMS.findall(text), PRESENTATION_FORMS.findall(text)
    missing = {where: words for where, words in WORDS.items() if words not in text}
    assert not missing, f"not in the text layer: {missing}\n--- text layer ---\n{text}"
    assert ligature_forms(output) == []
    warnings = report["warnings"]
    assert isinstance(warnings, list)
    assert not [w for w in warnings if "ligature" in w], warnings


@pytest.mark.browser
def test_only_the_ligature_tags_of_an_override_are_rewritten() -> None:
    async def run() -> tuple[int, dict[str, list[str | None]]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(PAGE.as_uri(), wait_until="load")
                rewritten = await suppress_ligatures(page)
                styles: dict[str, list[str | None]] = await page.evaluate(
                    """() => Object.fromEntries(
                        ['plain', 'boilerplate', 'tabular', 'specific'].map(name => {
                            const element = document.querySelector('.' + name);
                            const style = getComputedStyle(element);
                            return [name, [style.fontVariantLigatures,
                                           style.fontFeatureSettings,
                                           element.getAttribute('style')]];
                        }))"""
                )
                return rewritten, styles
            finally:
                await browser.close()

    rewritten, styles = asyncio.run(run())
    # Boilerplate, tabular, specific, and the paragraph in the shadow root.
    assert rewritten == 4
    # The rule alone covers an element that overrides nothing: no inline style.
    assert styles["plain"] == [LIGATURES_OFF, "normal", None]
    # The page's other features survive; only the ligature tag is turned off.
    # A set, because Chromium serializes the computed value in its own order.
    features = {name: set(str(styles[name][1]).split(", ")) for name in styles}
    assert features["boilerplate"] == {'"kern"', '"liga" 0'}
    assert features["tabular"] == {'"tnum"', '"liga" 0'}
    assert styles["specific"][0] == LIGATURES_OFF


@pytest.mark.browser
def test_an_element_with_no_computed_style_is_not_an_override() -> None:
    """`<track>` has an empty computed style, which once read as "ligatures on".

    It counted two overrides on every page with a captioned video, and the
    embedded-media and captions-read goldens recorded the false log line.
    """

    async def run() -> int:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    '<video><track kind="captions" srclang="en"></video>'
                    '<audio><track kind="captions" srclang="en"></audio>'
                )
                return await suppress_ligatures(page)
            finally:
                await browser.close()

    assert asyncio.run(run()) == 0


@pytest.mark.browser
def test_ligatures_the_capture_cannot_reach_are_a_warning(tmp_path: Path) -> None:
    output, report = capture(tmp_path, UNREACHABLE)
    forms = ligature_forms(output)
    # The warning is true of the file: the forms it names are in the text.
    assert forms == ["ﬀ", "ﬃ"]
    assert "the oﬃcial oﬀer" in pdf_text(output)
    assert ligature_warning(forms) in report["warnings"]  # type: ignore[operator]
    manifest = json.loads(
        (tmp_path / "capture.ai" / "manifest.json").read_text("utf-8")
    )
    assert ligature_warning(forms) in manifest["warnings"]


@pytest.mark.browser
def test_an_appendix_prints_without_ligatures(tmp_path: Path) -> None:
    document = tmp_path / "appendix.html"
    document.write_text(
        "<!doctype html><meta charset=utf-8><style>"
        f'@font-face {{ font-family: F; src: url("{FONT.as_uri()}"); }}'
        "body { font-family: F; }</style><p>The first office flow affects baffled staff.</p>",
        encoding="utf-8",
    )
    destination = tmp_path / "appendix.pdf"
    render_document_to_pdf(document, destination)
    # The words the ligatures would have fused, not the whole sentence: "The"
    # has no ligature, and on Linux pypdf >= 6.16.2 reads it as "T he". The
    # gap it reads as a space is Chromium's whole-unit glyph placement, not
    # anything this test is about (docs/09 P10-20).
    assert "first office flow affects baffled staff." in pdf_text(destination)
    assert ligature_forms(destination) == []


def test_the_running_header_and_footer_turn_ligatures_off() -> None:
    # Asserted on the template, not the text layer: on macOS the header's font
    # did not ligate, so the capture test above cannot see this path, while a
    # Linux fallback font may. No page stylesheet reaches these documents.
    header, footer = _header_footer_templates("Office flow", "example.test")
    assert LIGATURES_OFF_DECLARATION in header
    assert LIGATURES_OFF_DECLARATION in footer


@pytest.mark.parametrize(
    ("cmap", "expected"),
    [
        # Chromium's own shape: a run of ligature glyphs onto U+FB00 upward.
        (b"1 beginbfrange\n<0060> <0063> <FB00>\nendbfrange", "ﬀﬁﬂﬃ"),
        (b"2 beginbfchar\n<0001> <0020>\n<00C0> <FB02>\nendbfchar", "ﬂ"),
        (b"1 beginbfrange\n<0010> <0011> [<0066> <FB04>]\nendbfrange", "ﬄ"),
        # A range that only reaches the block by incrementing.
        (b"1 beginbfrange\n<0001> <0003> <FAFF>\nendbfrange", "ﬀﬁ"),
        (b"1 beginbfrange\n<0001> <0003> <FAFD>\nendbfrange", ""),
        # Letters written out are the fix, not the finding.
        (b"1 beginbfchar\n<0005> <00660069>\nendbfchar", ""),
        # A multi-unit destination whose last unit is the form.
        (b"1 beginbfchar\n<0005> <0020FB01>\nendbfchar", "ﬁ"),
        # PDF allows white space inside a hex string.
        (b"1 beginbfchar\n<0005> <FB 06>\nendbfchar", "ﬆ"),
        # The source code is not the destination.
        (b"1 beginbfchar\n<FB01> <0041>\nendbfchar", ""),
    ],
)
def test_the_cmap_reader_finds_exactly_the_forms_a_reader_would_get(
    cmap: bytes, expected: str
) -> None:
    assert "".join(sorted(_forms_in(cmap))) == expected


VIEWER_PAGE = FIXTURES / "viewer" / "page-001.png"


def protected_capture(tmp_path: Path, title: str) -> tuple[list[str], str]:
    """One fixture page through the whole protected assembly, as published.

    Returns the published manifest's warnings and the PDF's text.
    """
    import shutil

    from webshot.protected.assemble import build_protected_viewer_artifacts

    pages = tmp_path / "pages"
    pages.mkdir(parents=True)
    shutil.copy2(VIEWER_PAGE, pages / "page-001.png")
    output = tmp_path / "capture.pdf"
    build_protected_viewer_artifacts(
        pages,
        output,
        source_url="https://fixture.invalid/protected/ligatures",
        title=title,
        expected_pages=1,
        ai_directory=tmp_path / "capture.ai",
    )
    # The published manifest, not the returned dict: the warning has to reach
    # the file a consumer holds.
    manifest = json.loads(
        (tmp_path / "capture.ai" / "manifest.json").read_text("utf-8")
    )
    return list(manifest["warnings"]), pdf_text(output)


@pytest.mark.browser
@pytest.mark.skipif(
    tesseract_executable() is None,
    reason="tesseract is not installed (the protected path builds its text layer with it)",
)
def test_the_protected_viewer_path_checks_its_text_layer_too(tmp_path: Path) -> None:
    """§5.11 says the text layer MUST be checked after printing. The protected
    path returned from the pipeline before the check, so a presentation form in
    its appendix -- here the document title, which is printed there and which
    turning ligatures off cannot reach -- went out with no warning (P18-3).

    The clean capture is the control: the OCR text layer's font maps only the
    glyphs it uses, so the check finds nothing on it rather than every form."""
    warnings, _ = protected_capture(tmp_path / "clean", "Quarterly finance review")
    assert not [w for w in warnings if "ligature characters" in w], warnings

    warnings, text = protected_capture(tmp_path / "found", "Quarterly ﬁnance review")
    assert "ﬁ" in text
    named = [w for w in warnings if "ligature characters" in w]
    assert named == [ligature_warning(["ﬁ"])], warnings
