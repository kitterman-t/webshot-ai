"""Headings the page showed that the PDF does not print, named (docs/09 P123-5).

A consulting firm's print stylesheet hid its article's `<h1>`: the PDF began
at the first section, with no title, in clean and faithful mode, and nothing
said so, because the bundle, taken under the same stylesheet, lacked it too.
A code host's README lost its last two sections at the page edge, headings
and all, while the bundle kept them. Now the page's title is read before the
capture isolates anything, the other headings before the print stylesheet
applies, and each is looked for in the PDF's text and the bundle's.

The tests hold the check from both sides: it names a title the print hid and
a heading the layout lost, and it says nothing of a title the caller's own
`--selector` leaves out, of page chrome clean mode removed, before the render
or with a fixed box after it, of a site's name left out of a region that
prints its own `<h1>`, or of a heading that is in the PDF in capitals, across
a line break, or across a page break.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webshot.capture.headings import (
    GAP_WORDS,
    Heading,
    MissingHeadings,
    ShownHeadings,
    missing_headings,
    missing_headings_warnings,
    with_unmarked,
    without_hidden,
)
from webshot.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "print_hidden_title_page.html"
TITLE = "New harbor dues for the winter season"

HIDDEN_TITLE = (
    f"The page's main heading, \"{TITLE}\", is not in the PDF's text, nor in the "
    "bundle's, though the page showed it on screen: its print stylesheet, or the "
    "print layout, hides it, so the PDF has no title. --media screen captures the "
    "page as the screen shows it."
)


def _isolated(title: str) -> str:
    return (
        f"The page's main heading, \"{title}\", is not in the PDF's text: it lies "
        "outside the region WebShot chose as the page's main content, so the PDF has "
        "no title. --selector with a region that holds it keeps it."
    )


def _warnings(tmp_path: Path, *args: str, fixture: Path = FIXTURE) -> list[str]:
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    assert (
        main([str(fixture), "-o", str(pdf), "--no-ocr", "--report", str(report), *args])
        == 0
    )
    return list(json.loads(report.read_text(encoding="utf-8"))["warnings"])


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_a_title_the_print_stylesheet_hides_is_named(tmp_path: Path, mode: str) -> None:
    assert _warnings(tmp_path, "--mode", mode) == [HIDDEN_TITLE]


@pytest.mark.browser
def test_printed_as_the_screen_shows_it_the_title_is_there(tmp_path: Path) -> None:
    assert _warnings(tmp_path, "--media", "screen") == []


@pytest.mark.browser
def test_without_a_bundle_the_title_is_still_looked_for(tmp_path: Path) -> None:
    warnings = _warnings(tmp_path, "--no-ai-bundle")
    assert (
        f"The page's main heading, \"{TITLE}\", is not in the PDF's text, though the "
        "page showed it on screen, so the PDF has no title."
    ) in warnings


@pytest.mark.browser
def test_a_title_clean_mode_hid_with_a_fixed_box_is_not_said_to_be_lost(
    tmp_path: Path,
) -> None:
    """A site's name in an `<h1>` inside a fixed masthead. The headings are
    read while the masthead shows; clean mode then hides it as page chrome and
    prints the page again (docs/09 P123-6), so the PDF lacks the name and the
    bundle has it. That is clean mode's doing, which its own warning names,
    not the printed layout's."""
    warnings = _warnings(
        tmp_path,
        "--mode",
        "clean",
        fixture=FIXTURES / "covering_masthead_title_page.html",
    )
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith(
        "Clean mode hid a fixed box that printed over the page's text, and printed "
        "the page again without it: div.masthead,"
    )


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_a_title_hidden_in_a_box_the_print_shows_despite_its_mark_is_named(
    tmp_path: Path, mode: str
) -> None:
    """Clean mode marks the panel as an overlay before the headings are read,
    so its title was not read; the page's print stylesheet shows the panel
    anyway, and the mark comes off (docs/09 P128), but hides the title. The
    PDF and the bundle are those of faithful mode, which names the title, and
    clean mode said nothing. The panel's other heading prints, and is not
    named."""
    title = "Welcome to the showcase"
    warnings = _warnings(
        tmp_path,
        "--mode",
        mode,
        fixture=FIXTURES / "marks" / "print_shows_panel_hides_title.html",
    )
    assert [w for w in warnings if "heading" in w] == [
        HIDDEN_TITLE.replace(TITLE, title)
    ], warnings
    shown_anyway = [w for w in warnings if "shows anyway" in w]
    assert len(shown_anyway) == (mode == "clean"), warnings


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_a_site_s_name_outside_the_region_is_not_named_when_the_region_has_a_title(
    tmp_path: Path, mode: str
) -> None:
    """The site's name in the header is the page's first `<h1>`; the article
    WebShot isolates prints an `<h1>` of its own, so the PDF has a title."""
    fixture = FIXTURES / "isolated_logo_title_page.html"
    assert _warnings(tmp_path, "--auto-selector", "--mode", mode, fixture=fixture) == []


@pytest.mark.browser
def test_a_title_in_page_chrome_clean_mode_removes_is_not_named(tmp_path: Path) -> None:
    """The page's only `<h1>`, in its `body > header`: clean mode removes the
    header as page chrome whatever region is chosen, though the region chosen
    leaves it out too."""
    fixture = FIXTURES / "isolated_chrome_title_page.html"
    assert (
        _warnings(tmp_path, "--auto-selector", "--mode", "clean", fixture=fixture) == []
    )


@pytest.mark.browser
def test_a_title_only_the_region_webshot_chose_leaves_out_is_named(
    tmp_path: Path,
) -> None:
    """The same page in faithful mode, which removes no chrome: the choice of
    region alone leaves out the page's only `<h1>`."""
    fixture = FIXTURES / "isolated_chrome_title_page.html"
    assert _warnings(
        tmp_path, "--auto-selector", "--mode", "faithful", fixture=fixture
    ) == [_isolated("Tidewater Gazette")]


def _shown(*texts: str, title: str | None = None, fate: str = "shown") -> ShownHeadings:
    return ShownHeadings(
        headings=tuple(Heading(2, text) for text in texts),
        title=Heading(1, title) if title else None,
        title_fate=fate if title else None,
    )


def test_a_heading_in_capitals_or_across_a_line_is_printed() -> None:
    found = missing_headings(
        _shown("Further surveys and archives"), "FURTHER SURVEYS\nAND ARCHIVES", None
    )
    assert found == MissingHeadings()


def test_a_heading_split_by_a_page_break_is_printed() -> None:
    """The running header and footer fall between its words."""
    running = " ".join(["footer"] * (GAP_WORDS - 1))
    found = missing_headings(
        _shown("Further surveys"), f"Further {running} surveys", None
    )
    assert found == MissingHeadings()


def test_a_heading_the_layout_lost_is_one_the_bundle_has() -> None:
    found = missing_headings(
        _shown("How to read a sounding", "Further surveys and archives"),
        "How to read a sounding. A sounding is the depth.",
        "How to read a sounding\nFurther surveys and archives",
    )
    assert found.lost == (Heading(2, "Further surveys and archives"),)
    assert found.hidden == () and found.title is None


def test_a_title_outside_the_region_webshot_chose_is_named() -> None:
    found = missing_headings(
        _shown(title=TITLE, fate="isolated"), "Risk focus", "Risk focus"
    )
    assert missing_headings_warnings(found, media="print") == [_isolated(TITLE)]


def test_a_title_outside_the_region_is_not_named_when_the_region_prints_its_own() -> (
    None
):
    """A site's name in its header comes first; the article has an `<h1>`."""
    shown = ShownHeadings(
        headings=(Heading(1, "Winter dues"), Heading(2, "What changes")),
        title=Heading(1, "Port office"),
        title_fate="isolated",
    )
    text = "Winter dues. What changes"
    assert missing_headings(shown, text, text) == MissingHeadings()


def test_a_region_s_own_title_the_print_hid_leaves_the_pdf_without_one() -> None:
    shown = ShownHeadings(
        headings=(Heading(1, "Winter dues"), Heading(2, "What changes")),
        title=Heading(1, "Port office"),
        title_fate="isolated",
    )
    found = missing_headings(shown, "What changes", "What changes")
    assert found.title == Heading(1, "Port office")
    assert found.title_kind == "isolated"
    assert found.hidden == (Heading(1, "Winter dues"),)


def test_headings_a_box_hidden_since_held_are_left_out() -> None:
    """Clean mode hid a fixed box after the headings were read (docs/09
    P123-6): what it held, the title among them, is chrome it removed."""
    shown = ShownHeadings(
        headings=(Heading(1, "Port office"), Heading(2, "Risk focus")),
        title=Heading(1, "Port office"),
        title_fate="shown",
    )
    kept = without_hidden(shown, {"found": [True, False], "title": True})
    assert kept == ShownHeadings(
        headings=(Heading(2, "Risk focus"),),
        title=Heading(1, "Port office"),
        title_fate="removed",
    )
    assert missing_headings(kept, "Risk focus", "Port office Risk focus") == (
        MissingHeadings()
    )


def test_headings_a_box_shown_since_held_are_checked_and_then_matched() -> None:
    """A box marked before the read whose mark came off (docs/09 P128): its
    headings join the list in document order, its title is `shown`, and a
    box clean mode hides after the render is matched to the new list."""
    shown = ShownHeadings(
        headings=(Heading(2, "Risk focus"),),
        title=Heading(1, "Port office"),
        title_fate="removed",
    )
    answer = {
        "found": [
            {"level": 1, "text": "Port office"},
            {"level": 2, "text": "Risk focus"},
        ],
        "fate": "shown",
    }
    merged = with_unmarked(shown, answer)
    assert merged == ShownHeadings(
        headings=(Heading(1, "Port office"), Heading(2, "Risk focus")),
        title=Heading(1, "Port office"),
        title_fate="shown",
    )
    found = missing_headings(merged, "Risk focus", "Risk focus")
    assert found.title_kind == "hidden"
    assert without_hidden(merged, {"found": [False, True], "title": False}) == (
        ShownHeadings(
            headings=(Heading(1, "Port office"),),
            title=Heading(1, "Port office"),
            title_fate="shown",
        )
    )


def test_a_title_whose_fate_stands_keeps_it_and_a_short_answer_is_unchecked() -> None:
    shown = ShownHeadings(
        headings=(Heading(2, "Risk focus"),),
        title=Heading(1, "Port office"),
        title_fate="removed",
    )
    found = [{"level": 2, "text": "Risk focus"}]
    assert with_unmarked(shown, {"found": found, "fate": None}) == shown
    assert with_unmarked(shown, None).unread is not None
    assert with_unmarked(shown, {"found": [], "fate": None}).unread is not None
    assert with_unmarked(shown, {"found": ["Risk focus"], "fate": None}).unread
    assert with_unmarked(ShownHeadings(unread="Target closed"), None) == (
        ShownHeadings(unread="Target closed")
    )


def test_headings_that_cannot_be_matched_after_the_hiding_are_unchecked() -> None:
    shown = ShownHeadings(headings=(Heading(2, "Risk focus"),))
    assert without_hidden(shown, None).unread is not None
    assert without_hidden(shown, {"found": [], "title": False}).unread is not None
    assert without_hidden(ShownHeadings(unread="Target closed"), None) == (
        ShownHeadings(unread="Target closed")
    )


def test_a_title_outside_the_caller_s_own_selector_is_what_was_asked_for() -> None:
    found = missing_headings(
        _shown(title=TITLE, fate="isolated"), "Risk focus", "Risk focus"
    )
    assert missing_headings_warnings(found, media="print", explicit_selector=True) == []


def test_a_title_clean_mode_removed_with_the_page_chrome_is_not_named() -> None:
    """A site's logo set in an `<h1>` inside its header."""
    found = missing_headings(
        _shown(title="Port office", fate="removed"), "Risk focus", None
    )
    assert missing_headings_warnings(found, media="print") == []


def test_the_title_is_said_once_and_the_rest_together() -> None:
    found = missing_headings(
        _shown(TITLE, "Risk focus", "What changes", title=TITLE),
        "Nothing of the headings",
        "Risk focus What changes",
    )
    title, rest = missing_headings_warnings(found, media="screen")
    assert title.startswith(f'The page\'s main heading, "{TITLE}", is not in the PDF')
    assert title.endswith("so the PDF has no title.")  # no --media remedy on screen
    assert rest.startswith(
        "2 headings the page showed are in the bundle but not in the PDF's text: "
        '"Risk focus", "What changes".'
    )


def test_headings_that_could_not_be_read_are_said_to_be_unchecked() -> None:
    found = missing_headings(ShownHeadings(unread="Target closed"), "", None)
    assert missing_headings_warnings(found, media="print") == [
        "WebShot could not read the page's headings (Target closed), so whether "
        "the PDF lacks one the page showed was not checked."
    ]
