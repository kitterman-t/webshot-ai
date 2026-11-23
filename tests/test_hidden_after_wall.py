"""A page that hides its article after its wall's marker says so (docs/09 P132-7).

An archived copy of a business magazine's article printed its opening, four
pages that ended at the site's footer, and exited 0 with no warning. The rest
of the article was in the document: the site's own stylesheet hid every
section after the one holding an empty `halfway-hardwall-insertion-point`.
WebShot does not show what the page hides; it names the marker and says the
capture holds only the opening. tests/fixtures/wall_marker_page.html is that
page, its text invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webshot.capture.landing import HiddenAfterWall, hidden_after_wall_message
from webshot.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "wall_marker_page.html"


def _warnings(tmp_path: Path, source: Path, *flags: str) -> tuple[list[str], str]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(source), "--output", str(pdf), "--no-ocr", *flags]) == 0
    bundle = pdf.with_suffix(".ai")
    warnings = json.loads((bundle / "manifest.json").read_text("utf-8"))["warnings"]
    return warnings, (bundle / "content.md").read_text("utf-8")


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_text_hidden_after_a_wall_marker_is_named(tmp_path: Path, mode: str) -> None:
    warnings, markdown = _warnings(tmp_path, FIXTURE, "--mode", mode)
    [walled] = [w for w in warnings if "names as a wall" in w]
    assert walled.startswith("The page hides ")
    assert "(div.halfway-hardwall-insertion-point), more than the " in walled
    assert "hold only what comes before the wall" in walled
    assert "web archive" not in walled
    # What the page hides stays hidden: the warning is the whole change.
    assert "WALLOPENINGSENTINEL" in markdown
    assert "WALLHIDDENSENTINEL" not in markdown


@pytest.mark.browser
def test_a_wall_marker_with_nothing_hidden_after_it_says_nothing(
    tmp_path: Path,
) -> None:
    """A reader with a membership is shown it all, and the marker stays."""
    shown = tmp_path / "shown.html"
    shown.write_text(
        FIXTURE.read_text("utf-8").replace(
            "section:has(.halfway-hardwall-insertion-point) ~ * { display: none; }",
            "",
        ),
        "utf-8",
    )
    warnings, markdown = _warnings(tmp_path, shown)
    assert not [w for w in warnings if "names as a wall" in w]
    assert "WALLHIDDENSENTINEL" in markdown


@pytest.mark.browser
def test_hidden_text_shorter_than_what_is_shown_is_not_named(tmp_path: Path) -> None:
    """A folded aside after a marker is not the rest of the article."""
    page = tmp_path / "short.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>A</title>'
        "</head><body><article><h1>The ferry</h1>"
        + "".join(
            f"<p>Shown paragraph {n} about the ferry and its fares.</p>"
            for n in range(20)
        )
        + '<div class="paywall-note"></div><div style="display:none"><p>'
        + "A folded note on the timetable, long enough to clear the floor. " * 5
        + "</p></div></article></body></html>",
        "utf-8",
    )
    warnings, _ = _warnings(tmp_path, page)
    assert not [w for w in warnings if "names as a wall" in w]


@pytest.mark.browser
def test_a_closed_dialog_after_a_marker_is_not_the_article(tmp_path: Path) -> None:
    """A sign-up dialog the page keeps closed at the end of its body holds
    more words than the short page above it, and is not what the wall hid."""
    page = tmp_path / "dialog.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>A</title>'
        "</head><body><h1>The ferry</h1><p>Shown text about the ferry.</p>"
        '<div class="paywall-slot"></div>'
        '<div role="dialog" style="display:none"><p>'
        + "Join the harbor letter for the timetable, the fares and the tides. " * 8
        + "</p></div></body></html>",
        "utf-8",
    )
    warnings, _ = _warnings(tmp_path, page)
    assert not [w for w in warnings if "names as a wall" in w]


def test_an_archived_copy_is_offered_an_earlier_snapshot() -> None:
    found = HiddenAfterWall("div.halfway-hardwall-insertion-point", 4517, 1644)
    message = hidden_after_wall_message(
        found, "https://web.archive.org/web/20260101000000/https://news.test/a"
    )
    assert message.startswith(
        "The page hides 4,517 letters and digits of text after an element it names "
        "as a wall (div.halfway-hardwall-insertion-point), more than the 1,644 it "
        "shows before it,"
    )
    assert message.endswith(
        "This is a web archive's copy: a snapshot saved before the site added its "
        "wall may hold the whole article."
    )
    assert "web archive" not in hidden_after_wall_message(found, "https://news.test/a")
