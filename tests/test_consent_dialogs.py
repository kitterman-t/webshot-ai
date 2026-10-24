"""Both modes hide a consent manager's dialog, and say so (docs/09 P79).

A news article's Cookiebot dialog, fixed in a corner of the window, was printed
on five of its six pages over the text, and its words were read into
content.md between the article's ("…the security foundation that"marketing"
will makecookies it trustworthy…"), in faithful and clean mode alike. Clean
mode's overlay rule wants a box nearly half the window wide, and the dialog
was narrower. tests/fixtures/consent_dialog_page.html holds a dialog of that
shape, a closed OneTrust banner, a Usercentrics banner in a shadow root and an
Osano banner in its zero-height window (P97-4), beside article text that names
the vendors. All of its text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from webshot.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "consent_dialog_page.html"
DIALOGS = (
    "This website uses cookies",
    "Accept all cookies",
    "Usercentrics privacy settings banner",
    "utilizes technologies such as cookies",
)
ARTICLE = (
    "Cookiebot and Usercentrics among them",
    'its own box says "cookie consent"',
    "A ledger entry about cookies baked for the fair.",
    "The last paragraph says the security foundation is what makes the ledger "
    "trustworthy.",
)


def capture(tmp_path: Path, mode: str) -> tuple[str, list[str], dict[str, Any]]:
    """Each page's text layer, content.md, and the manifest."""
    pdf = tmp_path / f"{mode}.pdf"
    code = main([str(FIXTURE), "--output", str(pdf), "--mode", mode, "--no-ocr"])
    assert code == 0
    bundle = pdf.with_suffix(".ai")
    pages = [page.extract_text() or "" for page in PdfReader(str(pdf)).pages]
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    return (bundle / "content.md").read_text(encoding="utf-8"), pages, manifest


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["faithful", "clean"])
def test_a_consent_dialog_is_hidden_and_named(tmp_path: Path, mode: str) -> None:
    markdown, pages, manifest = capture(tmp_path, mode)
    for surface in (markdown, *pages):
        for phrase in DIALOGS:
            assert phrase not in surface, (phrase, surface[:400])
    flat = " ".join((markdown + "\n" + "\n".join(pages)).split())
    for phrase in ARTICLE:
        assert phrase in " ".join(markdown.split()), phrase
        assert phrase in flat, phrase
    consent = [w for w in manifest["warnings"] if "consent manager" in w]
    assert consent == [
        "Hid the consent manager dialogs on screen: Cookiebot's "
        "(#CybotCookiebotDialog), Usercentrics' (#usercentrics-root), Osano's "
        "(.osano-cm-window). A consent "
        "dialog is fixed to the window, so it would have printed over the page's "
        "text on every page, and its words would have been read into the bundle "
        "between the page's. It is the browser's session asking, not the page, so "
        "both modes hide it. Pass --exclude for any other box that covers the page."
    ]
    # The closed banner was hidden too, and is not named.
    assert "OneTrust" not in consent[0]
    assert "OneTrust banner text" not in markdown
