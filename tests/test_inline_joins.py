"""A word with a formatted part is one word on every text surface (docs/09 P48).

docling's HTML backend makes an item of each run of bold, italic, inline code
or link text, and its serializers join items with a space, so
`un<b>break</b>able` read "un **break** able" in content.md and "un break
able" in content.txt and chunks.jsonl, while the page and the PDF read
"unbreakable". P14-56 carried where a sub/superscript touches its neighbour
through the backend as two marker characters; P48 does the same for every
element the backend formats or links, and leaves apart what the page keeps
apart: whitespace of any kind, a line break, and an element the page's CSS
lays out as a box of its own, which the snapshot records as
`data-webshot-display`. A capture also measures each edge where such an
element touches the text beside it, and joins only the ones the page draws
with no gap (docs/09 P70-11): the fixture's inline code is padded by 0.2 em,
which keeps it apart from the words it runs into in a capture, while the
markup alone, as a snapshot from before that change reads, joins it.

`tests/fixtures/inline_joins.html` uses made-up words, so each can be
searched for whole. The browser tests capture it end to end; the rest call
the bridge on markup directly.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from webshot.capture.snapshot import (
    DISPLAY_ATTRIBUTE,
    INLINE_TAGS,
    MEASURED_INLINE_TAGS,
)
from webshot.extract import docling_bridge
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import (
    _JOINED_TAGS,
    JOINED_AFTER,
    JOINED_BEFORE,
    extract,
    mark_inline_joins,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "inline_joins.html"

#: Each made-up word in the fixture with a formatted part inside it.
JOINED = (
    "Varnteshic",
    "velmorantic",
    "prequasted",
    "blornish",
    "cozibwort",
    "trevakul",
    "gorpendil",
    "flanoric",
    "torquellish",
    "brandovic",
    "fenniktal",
    "Fendralix",
    "grampelosh",
    "stravine",
    "skovalder",
)
#: Formatted text touching punctuation, as the page reads it.
PUNCTUATED = ("Plavik,", "Dromsen;", "(zubrel)", '"wenthar"')
#: Pairs the page keeps apart: a space, a no-break or thin space.
SPACED = (
    ("before", "spaced"),
    ("spaced", "after"),
    ("trel", "wisp"),
    ("wisp", "mor"),
    ("quil", "nesh"),
    ("nesh", "dor"),
)
#: Pairs a `<br>` keeps apart, with no whitespace beside it.
BROKEN = (("quor", "daz"), ("skel", "vona"), ("vrin", "tosk"))
#: Pairs only the page's CSS keeps apart: an inline-block, flex items, a block.
BOXED = (("zorbel", "quintar"), ("krev", "moltan"), ("Hessel", "Wiki"))
#: The words and punctuation the fixture's inline code runs into, which its
#: 0.2 em padding draws apart. Joined on the markup alone; apart in a capture,
#: which measures the gap (docs/09 P70-11).
PADDED = ("cozibwort", "grampelosh", "(zubrel)")
PADDED_APART = (
    ("co", "zib"),
    ("zib", "wort"),
    ("gram", "pel"),
    ("pel", "osh"),
    ("(", "zubrel"),
    ("zubrel", ")"),
)


def _page(body: str) -> str:
    return (
        '<html><head><meta charset="utf-8"></head><body><h1>Title</h1>'
        f"{body}</body></html>"
    )


def _line(body: str) -> str:
    """The plain text of `body`, which follows the title."""
    return extract(_page(body), []).text.split("\n\n", 1)[1].strip()


def _plain(markdown: str) -> str:
    """Markdown with its links, emphasis and code spans taken out."""
    return re.sub(r"[*`~]", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown))


def _words(text: str) -> set[str]:
    """Each whitespace-separated token, as written and without the sentence
    punctuation that ends it."""
    tokens = text.split()
    return {*tokens, *(token.rstrip(".,;:") for token in tokens)}


def _fixture_result() -> Any:
    return extract(FIXTURE.read_text(encoding="utf-8"), [])


def _chunks(result: Any) -> str:
    """chunks.jsonl's texts: each seed's under its heading path."""
    return "\n".join(
        record["text"] for record in build_chunk_records(result.chunk_seeds)
    )


# --------------------------------------------------------------------------- #
# The bridge, on markup
# --------------------------------------------------------------------------- #


def test_a_word_with_a_formatted_part_reads_as_one_word() -> None:
    """The fixture's words, with no browser: the parts the CSS lays out as
    boxes are left out, since only a capture records that."""
    result = _fixture_result()
    chunks = _chunks(result)
    surfaces = {
        "content.txt": result.text,
        "content.md": _plain(result.markdown),
        "chunks.jsonl": _plain(chunks),
    }
    for name, surface in surfaces.items():
        words = _words(surface)
        for word in (*JOINED, *PUNCTUATED):
            assert word in words, (name, word)
        for left, right in (*SPACED, *BROKEN):
            assert f"{left}{right}" not in surface, (name, left, right)
    assert result.warnings == []


def test_markdown_wraps_each_part_where_it_touches() -> None:
    result = _fixture_result()
    chunks = _chunks(result)
    for surface in (result.markdown, chunks):
        for written in (
            "A vel**moran**tic drift, a pre*quast*ed orbit and a *blor*nish tone.",
            "Call co`zib`wort, press tre`vak`ul, read gor`pen`dil and set fla*nor*ic.",
            'The [Plavik](#later), the [Dromsen](#later); then (`zubrel`) and "*wenthar*".',
            "One [**tor**](#later)quellish word, one **bran***dovic* word, and one "
            "~~fen~~niktal.",
            "trel **wisp** mor, quil *nesh* dor",
            "- gram`pel`osh and stra**vin**e",
            "| sko*val*der | a test |",
        ):
            assert written in surface, written
    # A flattened heading carries the join as well. It keeps no formatting:
    # since docling-slim 2.139 a heading flattened to one text keeps only a
    # formatting all of its runs share, where before the first run's bold
    # made the whole word bold (docs/09 P96-2).
    assert "## Fendralix" in result.markdown.splitlines()
    assert "Fendralix" in result.text.splitlines()


@pytest.mark.parametrize(
    "space",
    [chr(code) for code in range(0x110000) if chr(code).isspace()],
    ids=lambda space: f"U+{ord(space):04X}",
)
def test_whitespace_the_page_has_keeps_the_parts_apart(space: str) -> None:
    """Every character Python reads as whitespace, which is the set docling's
    `str.split()` collapses: at either edge of bold, italic or a link the
    output is docling's own, a space, as it was before P48."""
    body = (
        f"<p>alpha{space}<b>beta</b>{space}gamma and <i>delta</i>{space}epsilon "
        f"and zeta{space}<a href='#x'>eta</a></p>"
    )
    assert mark_inline_joins(_page(body)) == _page(body)
    assert _line(body) == "alpha beta gamma and delta epsilon and zeta eta"


def test_a_line_break_before_an_elements_text_keeps_it_off_the_word_before() -> None:
    """A `<br>` between an element's start and its text separates that text
    from the word before. P14-56's parser kept the character from before the
    element regardless, and read `foo<sub><br>0</sub>` as "foo0"."""
    assert _line("<p>foo<sub><br>0</sub> baz</p>") == "foo 0 baz"
    assert _line("<p>foo<b><br>bar</b> baz</p>") == "foo bar baz"
    assert _line("<p>foo<b>bar<br></b>baz</p>") == "foobar baz"


def test_an_element_laid_out_as_a_box_keeps_doclings_spacing() -> None:
    """The snapshot records `data-webshot-display` where the page's CSS made
    an inline element a box; its edges separate, as a block's do."""
    attribute = f'{DISPLAY_ATTRIBUTE}="inline-block"'
    assert (
        _line(f'<p><a href="/a">Home</a><a href="/b" {attribute}>About</a></p>')
        == "Home About"
    )
    assert _line(f"<p><span {attribute}>krev</span><b>moltan</b></p>") == "krev moltan"
    # Inside the box, its own parts still join.
    assert (
        _line(f"<p>a <span {attribute}>vel<b>moran</b>tic</span>s</p>")
        == "a velmorantic s"
    )


def test_the_joined_elements_are_those_docling_formats_or_links() -> None:
    """The set follows the backend: an upgrade that formats another element
    fails here until the bridge marks it by P48's rule. Every other element
    of `INLINE_TAGS` but `wbr` is one the snapshot measures (docs/09 P70), so
    one that the backend stops formatting fails here too."""
    from docling.backend.html_backend import _FORMAT_TAG_MAP

    assert set(_FORMAT_TAG_MAP) | {"a"} == _JOINED_TAGS
    assert _JOINED_TAGS <= INLINE_TAGS
    assert MEASURED_INLINE_TAGS == INLINE_TAGS - _JOINED_TAGS - {"wbr"}


def _marked_by_slicing(html: str, marks: set[tuple[int, str]]) -> str:
    """P14-56's insertion, one slice of the whole page per marker."""
    marked = html
    for offset, mark in sorted(marks, reverse=True):
        marked = marked[:offset] + mark + marked[offset:]
    return "﻿" + marked


def test_markers_are_placed_as_before_in_one_pass() -> None:
    """The page is assembled once now, with the same markers in the same
    places: slicing per marker was quadratic in a page with many links."""
    pages = [
        FIXTURE.read_text(encoding="utf-8"),
        _page(
            "".join(
                f"<p>w{index}<a href='#{index}'>x</a>, H<sub>{index}</sub>O</p>"
                for index in range(300)
            )
        ),
    ]
    for page in pages:
        parser = docling_bridge._InlineJoins(page)
        parser.feed(page)
        parser.close()
        assert parser.marks
        assert mark_inline_joins(page) == _marked_by_slicing(page, parser.marks)


def test_content_json_keeps_every_item_and_carries_no_marker(monkeypatch) -> None:
    """The DoclingDocument stays docling's: the same items with the same
    formatting and the page's own origin hash. The only change is a space
    docling put inside a string it flattened, a heading or a table cell."""
    result = _fixture_result()
    surfaces = [result.document_json, result.markdown, result.text, result.doctags]
    surfaces += [seed.text for seed in result.chunk_seeds]
    for surface in surfaces:
        assert JOINED_BEFORE not in surface
        assert JOINED_AFTER not in surface
        assert "﻿" not in surface

    monkeypatch.setattr(docling_bridge, "mark_inline_joins", lambda html: html)
    unmarked = json.loads(_fixture_result().document_json)
    document = json.loads(result.document_json)
    assert document["origin"] == unmarked["origin"]
    assert len(document["texts"]) == len(unmarked["texts"])
    for item, plain in zip(document["texts"], unmarked["texts"], strict=True):
        assert item.get("formatting") == plain.get("formatting")
        assert item["text"].replace(" ", "") == plain["text"].replace(" ", "")


# --------------------------------------------------------------------------- #
# The whole pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("inline-joins")
    pdf = directory / "inline_joins.pdf"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            str(FIXTURE),
            "--output",
            str(pdf),
            "--no-ocr",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    bundle = pdf.with_suffix(".ai")
    chunks = [
        json.loads(line)
        for line in (bundle / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return {
        "content.md": (bundle / "content.md").read_text(encoding="utf-8"),
        "content.txt": (bundle / "content.txt").read_text(encoding="utf-8"),
        "chunks.jsonl": "\n".join(chunk["text"] for chunk in chunks),
        "content.html": (bundle / "content.html").read_text(encoding="utf-8"),
        "pdf": "\n".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ),
        "warnings": json.dumps(
            json.loads((bundle / "manifest.json").read_text(encoding="utf-8")).get(
                "warnings"
            )
        ),
    }


@pytest.mark.browser
def test_every_text_surface_reads_each_word_whole(captured: dict[str, str]) -> None:
    """Each word the page draws whole, and each pair it draws apart: the
    padded code too, which the markup alone joins."""
    for name in ("content.md", "content.txt", "chunks.jsonl"):
        surface = _plain(captured[name])
        words = _words(surface)
        for word in (*JOINED, *PUNCTUATED, "yarbistle"):
            if word in PADDED:
                continue
            assert word in words, (name, word)
        for left, right in (*SPACED, *BROKEN, *BOXED, *PADDED_APART):
            assert f"{left}{right}" not in surface, (name, left, right)
            assert re.search(rf"{re.escape(left)}\s+{re.escape(right)}", surface), (
                name,
                left,
                right,
            )
    assert "data-webshot-display" not in captured["warnings"]


@pytest.mark.browser
def test_the_pdf_reads_the_same_words(captured: dict[str, str]) -> None:
    """The ground truth the bundle now agrees with. pypdf reads a gap at an
    inline code's padding as a space, so only the emphasized words are read
    off the PDF; a box or a space the page has is whitespace there too."""
    pdf = captured["pdf"]
    for word in (
        "Varnteshic",
        "velmorantic",
        "prequasted",
        "blornish",
        "torquellish",
        "brandovic",
        "fenniktal",
        "Fendralix",
        "stravine",
        "yarbistle",
    ):
        assert word in _words(pdf), word
    for left, right in (*SPACED, *BROKEN, *BOXED):
        assert re.search(rf"{left}\s+{right}", pdf), (left, right)


@pytest.mark.browser
def test_the_snapshot_records_where_css_made_a_box(captured: dict[str, str]) -> None:
    """The display the browser computed, on each inline element the page laid
    out as a box, and not on one the page itself marked but showed inline."""
    html = captured["content.html"]
    for display, text in (
        ("inline-block", "zorbel"),
        ("inline-block", "quintar"),
        ("block", "krev"),
        ("block", "moltan"),
        ("block", "Wiki page"),
    ):
        assert re.search(rf'{DISPLAY_ATTRIBUTE}="{display}"[^>]*>{text}<', html), (
            display,
            text,
        )
    # The span touches the bold "bistle" and is drawn against it, which the
    # snapshot records on both (docs/09 P70, P70-11).
    assert (
        '<span data-webshot-touches="after">yar</span>'
        '<b data-webshot-touches="before">bistle</b>'
    ) in html
    assert html.count(DISPLAY_ATTRIBUTE) == 5


@pytest.mark.browser
def test_the_snapshot_records_each_formatted_edge(captured: dict[str, str]) -> None:
    """Each formatted element whose text the markup runs into its neighbour's
    carries the edges the page draws touching, and the padded code an empty
    attribute: both its edges are drawn apart (docs/09 P70-11)."""
    html = captured["content.html"]
    for written in (
        'vel<b data-webshot-touches="before after">moran</b>tic',
        'co<code data-webshot-touches="">zib</code>wort',
        '(<code data-webshot-touches="">zubrel</code>)',
        'gram<code data-webshot-touches="">pel</code>osh',
        'tre<kbd data-webshot-touches="before after">vak</kbd>ul',
        '<b data-webshot-touches="after">bran</b><i data-webshot-touches="before">dovic</i>',
        "before <b>spaced</b> after",
    ):
        assert written in html, written


@pytest.mark.browser
def test_the_snapshot_keeps_each_line_break(captured: dict[str, str]) -> None:
    """A `<br>` has no width, so the visibility filter dropped every one, and
    `skel<br>vona` reached content.html as "skelvona" before any join."""
    html = captured["content.html"]
    for written in (
        "quor<b><br>daz</b>",
        "skel<br>vona",
        "<code>vrin</code><br><code>tosk</code>",
    ):
        assert written in html, written
