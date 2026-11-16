"""A box clean mode marked that the page's print CSS shows is in the bundle (P128).

A blog theme drew its open post in a panel fixed over a grid of every other
post. Clean mode took the panel for an interstitial and marked it; the page's
print stylesheet makes the panel the page with an `!important` rule that
beats WebShot's hiding one. The PDF printed the post, and every text surface
of the bundle, built from a snapshot that drops what is marked, was empty,
with no warning. The same holds for a box any rule marked, and the warning
names the rule: a sibling outside `--selector` is not one clean mode took for
an overlay. The fixtures are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from webshot.capture.marks import shown_despite_marking_message
from webshot.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "marks"
FIXTURE = FIXTURES / "print_shows_panel.html"
POST = "The open post says the gallery opens on the first of the month."


def _capture(
    tmp_path: Path, *extra: str, page: Path = FIXTURE
) -> tuple[str, str, dict[str, Any]]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(page), "--output", str(pdf), "--no-ocr", *extra]) == 0
    printed = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    bundle = pdf.with_suffix(".ai")
    text = " ".join((bundle / "content.txt").read_text(encoding="utf-8").split())
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    return printed, text, manifest


@pytest.mark.browser
def test_a_marked_box_the_print_shows_is_in_the_bundle_and_named(
    tmp_path: Path,
) -> None:
    printed, text, manifest = _capture(tmp_path)
    assert POST in printed
    assert POST in text, text
    assert "Card one" not in printed and "Card one" not in text
    named = [w for w in manifest["warnings"] if "print stylesheet shows anyway" in w]
    assert len(named) == 1, manifest["warnings"]
    assert "div.panel (taken in clean mode for an overlay)" in named[0], named[0]


@pytest.mark.browser
def test_a_box_the_user_excluded_stays_out_of_the_bundle(tmp_path: Path) -> None:
    _, text, manifest = _capture(tmp_path, "--exclude", ".panel")
    assert POST not in text
    assert not [w for w in manifest["warnings"] if "shows anyway" in w]


@pytest.mark.browser
def test_a_sibling_outside_the_selector_is_named_for_the_selector(
    tmp_path: Path,
) -> None:
    """`isolate_content` marks in either mode, so the warning must not say
    clean mode took the box for anything."""
    related = "The related list says the ferry runs every half hour on weekends."
    printed, text, manifest = _capture(
        tmp_path,
        "--selector",
        "#post",
        "--mode",
        "faithful",
        page=FIXTURES / "print_shows_sibling.html",
    )
    assert related in printed
    assert related in text, text
    named = [w for w in manifest["warnings"] if "print stylesheet shows anyway" in w]
    assert len(named) == 1, manifest["warnings"]
    assert "aside.related (outside the content region #post)" in named[0], named[0]
    assert "clean mode" not in named[0]


def test_a_mark_with_no_recorded_reason_is_not_given_one() -> None:
    message = shown_despite_marking_message(
        [("div.a", "consent"), ("section.b", "")], None
    )
    assert "div.a (taken for a consent dialog)" in message
    assert "section.b (marked by a rule that recorded no reason)" in message
    assert message.startswith("WebShot marked 2 boxes")
