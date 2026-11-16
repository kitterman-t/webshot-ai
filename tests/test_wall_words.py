"""A PDF that stops at a registration wall's words, named (docs/09 P124-2).

A business review's article sent its opening paragraphs and a wall, "Sign up
to keep reading … Become a free member … Already a subscriber? Sign in", in a
frame of its own. The bundle held the same teaser as the PDF, so the share of
the bundle's text the PDF holds was whole and P97's check said nothing, and
the capture exited 0 with no warning. What is left to see is the wall's own
words where the printed text stops.

tests/fixtures/registration_wall_teaser_page.html holds an article of that
shape: a masthead whose "Subscribe" and "Sign in" links are not a wall, four
paragraphs, the wall in the page's flow, then its topics, an author's note
and its tags. All of its text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webshot import pipeline
from webshot.cli import main
from webshot.render.pdfium_bridge import PdfInkError
from webshot.render.text_layer import WallWords, wall_words, wall_words_warning

FIXTURE = Path(__file__).parent / "fixtures" / "registration_wall_teaser_page.html"

#: About 400 letters of an article, as a printed page's text.
ARTICLE = (
    "The harbour office keeps its tide readings in a bound ledger, and has "
    "done so since before the lighthouse was built. Every reading is written "
    "in ink by whoever is on watch, with the time, the height of the water at "
    "the gauge and a word about the weather. Visitors often ask why the "
    "office does not let the radar gauge post its numbers to a screen.\n"
)

#: What follows an article: a little, and nothing a wall says.
TAIL = "Topics\nHarbours Records\nTags: Tides Ledgers\n"


def test_an_article_that_stops_at_the_wall_is_found() -> None:
    """The last of the wall's words, on the page they print on."""
    pages = [
        ARTICLE * 3,
        ARTICLE
        + "Sign up to keep reading\nBECOME A FREE MEMBER\n"
        + "Already a subscriber? Sign in\n"
        + TAIL,
    ]
    assert wall_words(pages) == WallWords(words="Already a subscriber? Sign in", page=2)


@pytest.mark.parametrize(
    "words",
    [
        "Sign up to keep reading",
        "Subscribe to continue reading",
        "Subscribe now to continue reading",
        "Log in or create a free account to continue reading",
        "Create a free account to keep reading",
        "To continue reading, please sign in",
        "To keep reading, subscribe",
        "Subscribe to read the full article",
        "Sign in to read the rest of this story",
        "Become a member to read",
        "This article is for subscribers",
        "This story is available to paid subscribers",
        "Already a member? Sign in",
        "Already a subscriber? Log in",
    ],
)
def test_what_a_wall_says_is_found(words: str) -> None:
    assert wall_words([ARTICLE, f"{words} only.\n{TAIL}"]) == WallWords(
        words=words, page=2
    )


def test_a_wall_s_words_across_a_line_break_are_found() -> None:
    """The PDF breaks a line where the page wrapped it."""
    found = wall_words([ARTICLE + "Sign up to keep\nreading\n" + TAIL])
    assert found == WallWords(words="Sign up to keep reading", page=1)


@pytest.mark.parametrize(
    "words",
    [
        # A newsletter's or a masthead's asks, and an article's own links.
        "Sign up for our newsletter",
        "Subscribe",
        "Sign in",
        "Continue reading",
        "Keep reading",
        "Read more",
        "Join the conversation. Keep reading below",
        "Subscribe to our podcast",
        "She signed up to keep reading groups going",
    ],
)
def test_what_every_page_says_is_not_a_wall(words: str) -> None:
    assert wall_words([ARTICLE, f"{words}\n{TAIL}"]) is None


def test_a_wall_s_words_at_the_start_of_the_text_are_not_where_it_stops() -> None:
    """A masthead that asks for a sign-up, and the whole article below it."""
    pages = ["Already a subscriber? Sign in\n" + ARTICLE * 2, ARTICLE * 2]
    assert wall_words(pages) is None


def test_the_words_must_have_less_text_after_them_than_before() -> None:
    """Measured on letters and digits, so layout counts for nothing."""
    before = "a" * 300
    assert wall_words([f"{before} Sign up to keep reading {'b' * 299}"]) is not None
    assert wall_words([f"{before} Sign up to keep reading {'b' * 300}"]) is None
    assert wall_words([f"{before} Sign up to keep reading {'-' * 900}"]) is not None


def test_no_text_has_no_wall() -> None:
    assert wall_words([]) is None
    assert wall_words(["", ""]) is None


def test_the_warning_names_the_words_and_the_page() -> None:
    message = wall_words_warning(WallWords(words="Sign up to keep reading", page=3))
    assert message == (
        'The PDF\'s text has "Sign up to keep reading" on page 3, the words a '
        "registration or subscription wall prints where it stops an article, "
        "with less of the page's text after them than before. The site may send "
        "the rest of the article only to readers who sign in or subscribe, so "
        "the PDF and the bundle may both hold only its opening. Open the page in "
        "your own browser, signed in if you have an account, and save it from "
        "there."
    )
    assert wall_words_warning(None) is None


def test_the_warning_names_a_wall_the_page_showed_and_no_bundle_it_lacks() -> None:
    message = wall_words_warning(
        WallWords(words="Already a member? Sign in", page=2), ["Piano"], bundle=False
    )
    assert message is not None
    assert message.startswith(
        "The page showed Piano's access wall, which asks the reader to sign in, "
        'register or subscribe. The PDF\'s text has "Already a member? Sign in" '
        "on page 2,"
    )
    assert "so the PDF may hold only its opening." in message
    assert "bundle" not in message


def _warnings(
    tmp_path: Path, *args: str, fixture: Path = FIXTURE
) -> tuple[int, list[str]]:
    pdf = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main(
        [str(fixture), "--output", str(pdf), "--no-ocr", "--report", str(report), *args]
    )
    return code, list(json.loads(report.read_text(encoding="utf-8"))["warnings"])


@pytest.mark.browser
@pytest.mark.parametrize("bundle", [True, False], ids=["bundle", "no-bundle"])
def test_a_teaser_that_stops_at_a_wall_is_named(tmp_path: Path, bundle: bool) -> None:
    """Exit 0, as P97's wall is: the capture is what the site sent."""
    code, warnings = _warnings(tmp_path, *(() if bundle else ("--no-ai-bundle",)))
    assert code == 0
    walled = [w for w in warnings if "the words a registration or subscription" in w]
    assert len(walled) == 1, warnings
    assert walled[0].startswith(
        'The PDF\'s text has "Already a subscriber? Sign in" on page 1, '
    ), walled[0]
    assert ("the PDF and the bundle may both hold" in walled[0]) is bundle
    # The bundle holds the teaser the PDF does, so the share said nothing.
    assert not [w for w in warnings if "text layer holds" in w], warnings


@pytest.mark.browser
@pytest.mark.parametrize("bundle", [True, False], ids=["bundle", "no-bundle"])
def test_a_pdf_whose_text_cannot_be_read_is_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundle: bool
) -> None:
    """One failure to read the PDF's text stops every check on it, the
    share's, the wall's and the headings' (docs/09 P123-5), and says so once,
    naming each check it stopped: the wall's check runs only when the share's
    warning did not."""

    def unreadable(path: Path) -> tuple[str, ...]:
        raise PdfInkError(f"PDFium could not open {path.name}: a test's failure")

    monkeypatch.setattr(pipeline, "page_texts", unreadable)
    code, warnings = _warnings(tmp_path, *(() if bundle else ("--no-ai-bundle",)))
    assert code == 0
    unread = [w for w in warnings if "could not read the PDF's text" in w]
    assert len(unread) == 1, warnings
    assert "a test's failure" in unread[0]
    assert "registration or subscription wall" in unread[0], unread[0]
    assert "lacks a heading the page showed" in unread[0], unread[0]
    assert ("bundle's" in unread[0]) is bundle, unread[0]


#: A wall that is a bar fixed to the window's bottom edge, printed over the
#: text at the foot of page one (docs/09 P123-6).
FIXED_BAR = Path(__file__).parent / "fixtures" / "fixed_wall_bar_page.html"


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_clean_mode_does_not_hide_a_fixed_bar_that_holds_the_wall_s_words(
    tmp_path: Path, mode: str
) -> None:
    """A print that stops at a wall's words showed a wall, though WebShot does
    not know its vendor: the box over the text may be the wall, and hiding it
    would take the words, and the warning naming them, out of the PDF while
    the site still keeps the article back."""
    code, warnings = _warnings(tmp_path, "--mode", mode, fixture=FIXED_BAR)
    assert code == 0
    walled = [w for w in warnings if "the words a registration or subscription" in w]
    assert len(walled) == 1, warnings
    assert walled[0].startswith(
        'The PDF\'s text has "Already a subscriber? Sign in" on page 2, '
    ), walled[0]
    assert not [w for w in warnings if w.startswith("Clean mode hid")], warnings
    covered = [w for w in warnings if w.startswith("A fixed box, div.member-bar,")]
    assert len(covered) == 1, warnings


@pytest.mark.browser
def test_without_the_wall_nothing_is_named(tmp_path: Path) -> None:
    """The check reads the printed text, so it names only words that print."""
    code, warnings = _warnings(tmp_path, "--exclude", ".offer")
    assert code == 0
    assert not [w for w in warnings if "registration or subscription" in w], warnings
