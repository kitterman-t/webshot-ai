"""A word with a part in a span is one word where the page draws it as one (docs/09 P70).

docling's HTML backend gives no item of its own to the text of a `<span>`,
`<mark>`, `<small>` or any other element of `INLINE_TAGS` it does not format
or link: it merges that text with the text beside it into one string, with a
space between, so `un<span>break</span>able` read "un break able" in
content.md, content.txt, chunks.jsonl and content.json while the page and the
PDF read "unbreakable". P48 joins an element the backend formats or links to
the text it touches. Touching in the markup is not enough for a span: a date
line's `<span>Article</span><span>•</span><span>12 Oct</span>` is drawn with
a gap at each edge by the spans' margins, and joined it read "Article•12
Oct". So the snapshot measures each edge where such an element touches the
text beside it and records, as `data-webshot-touches`, the ones the page
draws with no gap; the bridge joins there and nowhere else. A snapshot that
records none, as every one taken before P70 does, reads as docling spaces it.
Two readers beside the backend follow the joins: the chrome regions the
bridge files furniture by, and the snapshot, which dropped a space that ends
a line in a span of its own.

`tests/fixtures/span_joins.html` uses made-up words, so each can be searched
for whole. The browser tests capture it end to end, where the snapshot
measures the edges; the rest call the bridge on markup, which carries the
attribute as a snapshot would.
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
    MATH_ATTRIBUTE,
    MEASURED_INLINE_TAGS,
    TOUCH_ATTRIBUTE,
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
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "span_joins.html"

#: Each made-up word in the fixture with a part in an element docling does
#: not format, one or more for each such element of `INLINE_TAGS`.
JOINED = (
    "Vestrelian",  # span, in the title
    "kalindric",  # span
    "moraspent",  # mark
    "talwenish",  # small
    "bridoval",  # abbr
    "Plovenna",  # cite
    "framelt",  # q
    "dreskow",  # strike
    "gulvorine",  # font
    "hespindle",  # tt
    "Ormithe",  # label
    "zelquist",  # time
    "prenovish",  # data
    "yeldorn",  # dfn
    "maskelt",  # bdi
    "trevinow",  # bdo
    "boroth",  # big
    "kelmoravine",  # a span in a span
    "storundel",  # bold in a span
    "queldros",  # a span after bold
    "Linkerosh",  # a span in a link
    "bremdale",  # a span after a link
    "Paldrenica",  # a heading
    "grampelosh",  # a list item
    "stravine",
    "Morvelan",  # a term
    "termekish",  # its definition
    "Tabrelwick",  # a table's caption
    "Termash",  # a header cell
    "skovalder",  # a cell
    "Haldorvik",  # a term grouped in a <div> (P72)
    "brelmosk",  # its description
)
#: Text in a span touching punctuation, as the page reads it.
PUNCTUATED = ("Varlin,", "Tosmer;", "(prenik)", '"galdor"')
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
BROKEN = (("quor", "daz"), ("skel", "vona"))
#: Pairs a space keeps apart where it ends a line, in a span of its own.
HANGING = (("kelvar", "dostrel"), ("morvish", "plendor"))
#: Pairs only the page's CSS keeps apart: an inline-block, flex items, a block.
BOXED = (("zorbel", "quintar"), ("krev", "moltan"), ("Hessel", "Wiki"))
#: Pairs that touch in the markup, inline, and are drawn apart: a bullet
#: between margins, a separator in generated content, a line break in
#: generated content, padded labels, quotation marks, and a span with margins
#: whose own attribute names both edges as touching.
DRAWN_APART = (
    ("Ploskar", "\N{BULLET}"),
    ("\N{BULLET}", "Vendrel"),
    ("Halvenk", "Ostrim"),
    ("Quendril", "Moskav"),
    ("Tarvish", "Elgrove"),
    ("sel", "var"),
    ("var", "ont"),
    ("plin", "dor"),
    ("dor", "vesk"),
)

_TOUCHES_RE = re.compile(rf'\s{TOUCH_ATTRIBUTE}="[^"]*"')
_MEASURED_START_RE = re.compile(
    rf"<({'|'.join(sorted(MEASURED_INLINE_TAGS))})(?=[\s>])"
)
_TOUCHING = f'{TOUCH_ATTRIBUTE}="before after"'


def _snapshot(page: str, *, touching: bool) -> str:
    """`page` as a snapshot carries it: without the page's own touch
    attributes, which the snapshot removes, and with `touching` with both
    edges of every measured element named, as a snapshot of a page that draws
    each such part against the text beside it records them."""
    page = _TOUCHES_RE.sub("", page)
    if touching:
        page = _MEASURED_START_RE.sub(rf"<\1 {_TOUCHING}", page)
    return page


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


def _fixture_result(*, touching: bool = True) -> Any:
    return extract(
        _snapshot(FIXTURE.read_text(encoding="utf-8"), touching=touching), []
    )


def _chunks(result: Any) -> str:
    """chunks.jsonl's texts: each seed's under its heading path."""
    return "\n".join(
        record["text"] for record in build_chunk_records(result.chunk_seeds)
    )


# --------------------------------------------------------------------------- #
# The bridge, on markup
# --------------------------------------------------------------------------- #


def test_a_word_with_a_part_in_a_span_reads_as_one_word() -> None:
    """The fixture's words, with no browser, as a snapshot of a page drawing
    every part touching records them. The pairs the CSS draws apart are left
    out, since only a capture measures them."""
    result = _fixture_result()
    surfaces = {
        "content.txt": result.text,
        "content.md": _plain(result.markdown),
        "chunks.jsonl": _plain(_chunks(result)),
    }
    for name, surface in surfaces.items():
        words = _words(surface)
        for word in (*JOINED, *PUNCTUATED):
            assert word in words, (name, word)
        for left, right in (*SPACED, *BROKEN, *HANGING):
            assert f"{left}{right}" not in surface, (name, left, right)
    assert result.warnings == []


def test_a_snapshot_without_the_evidence_keeps_doclings_space() -> None:
    """A snapshot taken before P70 records no touching edge, and its spans
    read as docling spaced them: the same text, byte for byte, as master's
    bridge writes for this page (docs/09 P70-8). The parts of an element the backend
    formats or links still join, by P48's rule."""
    result = _fixture_result(touching=False)
    lines = result.text.splitlines()
    for written in (
        "Field notes on the Ves trel ian",
        "A ka lin dric spur, a mo rasp ent ridge and a tal wen ish slope.",
        "Parts in parts: kel mo ra vine, one sto rund el and one queldros word.",
        'Punctuated: the Varlin , the Tosmer ; then ( prenik ) and " galdor ".',
        "Lin ker osh and bremdale , both linked.",
        "Tab rel wick",
        "| sko val der | a test |",
    ):
        assert written in lines, written
    for word in JOINED:
        if word not in ("gulvorine", "boroth", "queldros", "bremdale"):
            assert word not in _words(result.text), word
    assert result.warnings == []


@pytest.mark.parametrize("tag", sorted(INLINE_TAGS - {"wbr"}))
def test_every_inline_element_joins_the_text_it_touches(tag: str) -> None:
    """Every element of `INLINE_TAGS` with an end tag: one the backend
    formats or links wherever it touches (P48), any other where the snapshot
    measured it touching (P70)."""
    touching = f" {_TOUCHING}" if tag in MEASURED_INLINE_TAGS else ""
    assert (
        _line(f"<p>x un<{tag}{touching}>break</{tag}>able y</p>") == "x unbreakable y"
    )


@pytest.mark.parametrize("tag", sorted(MEASURED_INLINE_TAGS))
@pytest.mark.parametrize(
    ("touches", "line"),
    [
        (None, "x un break able y"),
        ("", "x un break able y"),
        ("before", "x unbreak able y"),
        ("after", "x un breakable y"),
        ("after before", "x unbreakable y"),
        ("start end", "x un break able y"),
    ],
)
def test_a_measured_element_joins_only_at_an_edge_the_snapshot_named(
    tag: str, touches: str | None, line: str
) -> None:
    """No attribute, an empty one, or tokens the snapshot never writes join
    nothing; each edge named joins that edge."""
    attribute = "" if touches is None else f' {TOUCH_ATTRIBUTE}="{touches}"'
    assert _line(f"<p>x un<{tag}{attribute}>break</{tag}>able y</p>") == line


def test_an_element_docling_formats_joins_without_the_evidence() -> None:
    """Without the attribute, the markup is the evidence for bold, a link and
    the rest of `_JOINED_TAGS`, as P48 made it, so a snapshot taken before
    the snapshot measured them reads as it did. One the snapshot measured
    joins only where it names an edge (docs/09 P70-11,
    `tests/test_formatted_joins.py`): an empty attribute is two edges drawn
    apart."""
    for tag in sorted(_JOINED_TAGS):
        for attribute, line in (
            ("", "x unbreakable y"),
            (f' {TOUCH_ATTRIBUTE}=""', "x un break able y"),
        ):
            body = f"<p>x un<{tag}{attribute}>break</{tag}>able y</p>"
            assert _line(body) == line, (tag, attribute)


def test_a_merged_string_is_joined_in_place() -> None:
    """The space docling put inside one string goes with the marker, wherever
    the backend made the string: a paragraph's runs, a heading, a list item, a
    term and its definition, a caption, a cell, a link's text."""
    result = _fixture_result()
    for surface in (result.markdown, _chunks(result)):
        for written in (
            "A kalindric spur, a moraspent ridge and a talwenish slope.",
            "Parts in parts: kelmoravine, one stor**un**del and one **quel**dros word.",
            'Punctuated: the Varlin, the Tosmer; then (prenik) and "galdor".',
            "[Linkerosh](#later) and [brem](#later)dale, both linked.",
            "- grampelosh and stravine",
            "| Termash | Use |",
            "| skovalder | a test |",
        ):
            assert written in surface, written
    for written in (
        "# Field notes on the Vestrelian",
        "## Paldrenica",
        "- **Morvelan**",
        "    - a termekish term",
        "Tabrelwick",
    ):
        assert written in result.markdown.splitlines(), written
    assert [["Termash", "Use"], ["skovalder", "a test"]] in result.tables


@pytest.mark.parametrize(
    ("body", "written"),
    [
        (
            "<dl><div><dt>Ves<span T>trel</span>ian</dt><dd>ka<span T>lin</span>dric</dd></div></dl>",
            "- **Vestrelian**\n    - kalindric",
        ),
        (
            "<dl><dt>Mor<span T>vel</span>an</dt><dd>ter<span T>mek</span>ish "
            "<dl><dt>in<span T>ner</span>most</dt><dd>sko<span T>val</span>der</dd></dl></dd></dl>",
            "- **Morvelan**\n    - termekish\n        - **innermost**\n            - skovalder",
        ),
        (
            "<dl><dt>Pal<span T>dren</span>ica<ul><li>gram<span T>pel</span>osh</li></ul></dt>"
            "<dd>stra<span T>vin</span>e</dd></dl>",
            "- **Paldrenica**\n    - grampelosh\n    - stravine",
        ),
        (
            "<ul><li>quel<span T>dro</span>sine<table><tr><td>Ter<span T>ma</span>sh</td></tr></table></li></ul>",
            "- queldrosine\n\n  | Termash |",
        ),
    ],
    ids=[
        "grouped terms (P72)",
        "a description holding a list (P73)",
        "a term holding a list (P60)",
        "an item with a table (P61)",
    ],
)
def test_the_description_and_item_readers_join_where_the_snapshot_measured(
    body: str, written: str
) -> None:
    """The backends that read a grouped description list, a description
    holding one, a term holding a list and an item beside its table make their
    items from the page the bridge marked, so a span the snapshot measured
    touching joins there too; with no evidence it keeps docling's space."""
    measured = extract(_page(body.replace("<span T>", f"<span {_TOUCHING}>")), [])
    assert written in measured.markdown
    assert measured.warnings == []
    unmeasured = extract(_page(body.replace("<span T>", "<span>")), []).markdown
    for word in re.findall(r"[A-Za-z]+", written):
        assert word not in unmeasured.split(), word


@pytest.mark.parametrize(
    "space",
    [chr(code) for code in range(0x110000) if chr(code).isspace()],
    ids=lambda space: f"U+{ord(space):04X}",
)
def test_whitespace_the_page_has_keeps_the_parts_apart(space: str) -> None:
    """Every character Python reads as whitespace, which is the set docling's
    `str.split()` collapses: at either edge of a span, a mark or a small the
    output is docling's own, a space, even where the attribute names the
    edge, as it was before P70."""
    body = (
        f"<p>alpha{space}<span {_TOUCHING}>beta</span>{space}gamma and "
        f"<mark {_TOUCHING}>delta</mark>{space}epsilon and zeta{space}"
        f"<small {_TOUCHING}>eta</small></p>"
    )
    assert mark_inline_joins(_page(body)) == _page(body)
    assert _line(body) == "alpha beta gamma and delta epsilon and zeta eta"


def test_a_box_or_a_line_break_keeps_the_parts_apart() -> None:
    """The snapshot records `data-webshot-display` where the page's CSS made
    a span a box; its edges separate, as a block's do, while its own parts
    still join. A `<br>` separates as it did."""
    attribute = f'{DISPLAY_ATTRIBUTE}="block"'
    assert (
        _line(
            f"<p><span {attribute} {_TOUCHING}>krev</span><span {_TOUCHING}>moltan</span></p>"
        )
        == "krev moltan"
    )
    assert (
        _line(f"<p>a <span {attribute}>ka<span {_TOUCHING}>lin</span>dric</span>s</p>")
        == "a kalindric s"
    )
    assert _line(
        f"<p>quor<span {_TOUCHING}><br>daz</span> and skel<span {_TOUCHING}>vona<br></span>x</p>"
    ) == ("quor\ndaz and skelvona\nx")


def test_a_formula_the_snapshot_wrote_keeps_its_space() -> None:
    """The snapshot writes a `<math>` as a span holding the formula's text.
    Where the page draws the formula itself too, its drawing reaches
    content.html beside the holder: MathJax's 22/40 on a textbook page read
    `<span>2240<span data-webshot-math="text">2240</span></span>`, and joined
    the two read as one number, "22402240". A holder's edges separate."""
    holder = f'<span {MATH_ATTRIBUTE}="text">2240</span>'
    assert (
        _line(f"<p>is <span {_TOUCHING}>2240{holder}</span>.</p>") == "is 2240 2240 ."
    )
    assert _line(f"<p>so x{holder}y</p>") == "so x 2240 y"


def test_highlighted_code_keeps_its_tokens_together() -> None:
    """A highlighter writes each token of a line of code as a span. docling
    put a space between every two, so `def f(x):` read "def f ( x ):"."""
    body = f"<pre><span {_TOUCHING}>def</span> <span {_TOUCHING}>f</span>(<span {_TOUCHING}>x</span>):</pre>"
    assert "def f(x):" in extract(_page(body), []).text.splitlines()


def test_chrome_a_span_joins_stays_off_the_body() -> None:
    """The backend files everything before the first heading as furniture, and
    the bridge moves an item back to the body unless a chrome region holds its
    text. The nav's one item is "Gorvane and Module 3" where the snapshot
    measured its spans touching; its region, read with a space at every
    element's edge, said "Gor vane and Mod ule 3", so the nav reached
    content.md. Without the evidence item and region agree as before."""
    for touching, text in (
        (True, "Gorvane and Module 3"),
        (False, "Gor vane and Mod ule 3"),
    ):
        result = _fixture_result(touching=touching)
        document = json.loads(result.document_json)
        layers = {
            item["text"]: item["content_layer"]
            for item in document["texts"]
            if item["text"].startswith("Gor")
        }
        assert layers == {text: "furniture"}
        assert "Gor" not in result.markdown
        assert "Gor" not in result.text


def test_the_snapshot_facts_read_the_page_as_the_backend_did() -> None:
    """A caption and a chrome region join where the backend's items do."""
    page = _snapshot(FIXTURE.read_text(encoding="utf-8"), touching=True)
    facts = docling_bridge._SnapshotFacts(clean=lambda text: text)
    facts.feed(mark_inline_joins(page))
    assert facts.table_captions == ["Tabrelwick"]
    assert facts.chrome_regions == ["Gorvane and Module 3"]


def test_content_json_keeps_every_item_and_carries_no_marker(monkeypatch) -> None:
    """The DoclingDocument stays docling's: the same items with the same
    formatting and the page's own origin hash. The only change is a space
    docling put inside a string it merged or flattened, which the page never
    had, so the lossless record now holds the page's words."""
    result = _fixture_result()
    surfaces = [result.document_json, result.markdown, result.text, result.doctags]
    surfaces += [seed.text for seed in result.chunk_seeds]
    for surface in surfaces:
        assert JOINED_BEFORE not in surface
        assert JOINED_AFTER not in surface
        assert "\N{ZERO WIDTH NO-BREAK SPACE}" not in surface

    monkeypatch.setattr(docling_bridge, "mark_inline_joins", lambda html: html)
    unmarked = json.loads(_fixture_result().document_json)
    document = json.loads(result.document_json)
    assert document["origin"] == unmarked["origin"]
    assert len(document["texts"]) == len(unmarked["texts"])
    for item, plain in zip(document["texts"], unmarked["texts"], strict=True):
        assert item.get("formatting") == plain.get("formatting")
        assert item["text"].replace(" ", "") == plain["text"].replace(" ", "")
    texts = [item["text"] for item in document["texts"]]
    assert "A kalindric spur, a moraspent ridge and a talwenish slope." in texts


# --------------------------------------------------------------------------- #
# The whole pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("span-joins")
    pdf = directory / "span_joins.pdf"
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
    for name in ("content.md", "content.txt", "chunks.jsonl"):
        surface = _plain(captured[name])
        words = _words(surface)
        for word in (*JOINED, *PUNCTUATED, "yarbistle"):
            assert word in words, (name, word)
        for left, right in (*SPACED, *BROKEN, *HANGING, *BOXED, *DRAWN_APART):
            assert f"{left}{right}" not in surface, (name, left, right)
            assert re.search(rf"{left}\s+{right}", surface), (name, left, right)
    assert captured["warnings"] == "[]"


@pytest.mark.browser
def test_the_pdf_reads_the_same_words(captured: dict[str, str]) -> None:
    """The ground truth the bundle now agrees with: no space inside a made-up
    word, and whitespace wherever the page keeps two apart. Read as substrings,
    since pypdf runs a table's cells together ("skovaldera test"). A pair drawn
    apart prints with what the page draws between, a bullet, a separator,
    quotation marks or a line break, and never as one word."""
    pdf = captured["pdf"]
    for word in (*JOINED, "yarbistle"):
        assert word in pdf, word
    for left, right in (*SPACED, *BROKEN, *HANGING, *BOXED):
        assert re.search(rf"{left}\s+{right}", pdf), (left, right)
    for left, right in DRAWN_APART:
        assert f"{left}{right}" not in pdf, (left, right)


@pytest.mark.browser
def test_the_snapshot_keeps_a_space_that_ends_a_line(captured: dict[str, str]) -> None:
    """A space that ends a line hangs past its end, so a span holding only
    that space has no width, and the snapshot dropped it with its space:
    `kelvar<span> </span>dostrel` reached content.html as "kelvardostrel"."""
    html = captured["content.html"]
    for written in (
        "kelvar<span> </span>dostrel",
        "<span>morvish</span><span> </span><span>plendor</span>",
    ):
        assert written in html, written


@pytest.mark.browser
def test_the_snapshot_records_where_css_made_a_box(captured: dict[str, str]) -> None:
    """The display the browser computed, on each span the page laid out as a
    box, and not on one the page itself marked but showed inline."""
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
    assert f'<span {TOUCH_ATTRIBUTE}="after">yar</span>' in html
    assert html.count(DISPLAY_ATTRIBUTE) == 5


@pytest.mark.browser
def test_the_snapshot_records_each_edge_drawn_touching(
    captured: dict[str, str],
) -> None:
    """The snapshot names an edge where the page draws the two glyphs on one
    line with no gap, and no other: not one drawn apart, not one beside
    whitespace, not a box's, and not the edges a page claimed for itself.
    An element docling formats or links is measured too (docs/09 P70-11):
    the bold in a span, the bold before one and the link before one."""
    html = captured["content.html"]
    for written in (
        f"ka<span {_TOUCHING}>lin</span>dric",
        f"kel<span {_TOUCHING}><span {_TOUCHING}>mo</span>ra</span>vine",
        f"sto<span {_TOUCHING}>r<b {_TOUCHING}>un</b>d</span>el",
        f'<b {TOUCH_ATTRIBUTE}="after">quel</b><span {TOUCH_ATTRIBUTE}="before">dros</span>',
        f'<a href="#later" {TOUCH_ATTRIBUTE}="after" rel="noopener noreferrer">brem</a>'
        f"<span {_TOUCHING}>dale</span>,",
        f'the <span {TOUCH_ATTRIBUTE}="after">Varlin</span>,',
        f"(<span {_TOUCHING}>prenik</span>)",
        f"<caption>Tab<span {_TOUCHING}>rel</span>wick</caption>",
        f"<dt>Hal<span {_TOUCHING}>dor</span>vik</dt>",
        "before <span>spaced</span> after",
        "<span>Ploskar</span><span>\N{BULLET}</span><span>Vendrel</span>",
        "<span>Halvenk</span><span>Ostrim</span>",
        "<span>Quendril</span><span>Moskav</span>",
        "<span>Tarvish</span><span>Elgrove</span>",
        "sel<q>var</q>ont",
        "So plin<span>dor</span>vesk",
    ):
        assert written in html, written
    assert html.count(TOUCH_ATTRIBUTE) == 42
    formatted = [
        match.group(1)
        for tag in _JOINED_TAGS
        for match in re.finditer(rf"<{tag}\s[^>]*{TOUCH_ATTRIBUTE}=\"([^\"]*)\"", html)
    ]
    assert sorted(formatted) == ["after", "after", "before after"]
    assert not re.search(rf"{DISPLAY_ATTRIBUTE}[^>]*{TOUCH_ATTRIBUTE}", html)
    assert not re.search(rf"{TOUCH_ATTRIBUTE}[^>]*{DISPLAY_ATTRIBUTE}", html)


@pytest.mark.browser
def test_a_line_that_wraps_at_the_edge_is_judged_by_the_boxes_there(
    captured: dict[str, str],
) -> None:
    """Two characters on two lines have no gap between them to measure. A
    range wrapped after its dash, with nothing drawn at the edge, is one range
    at any width; a margin at the edge keeps two parts apart, wrapped or not.
    The PDF shows that both wrap, so the case is the one it means to be."""
    dash = "\N{EN DASH}"
    html = captured["content.html"]
    assert f'<span {TOUCH_ATTRIBUTE}="after">gorm{dash}</span>tavel' in html
    assert f"<span>vorn{dash}</span><span>elsik</span>" in html
    assert re.search(rf"gorm{dash}\s*\n\s*tavel", captured["pdf"])
    assert re.search(rf"vorn{dash}\s*\n\s*elsik", captured["pdf"])
    for name in ("content.md", "content.txt", "chunks.jsonl"):
        surface = captured[name]
        assert "gorm-tavel" in _words(surface), name
        assert re.search(r"vorn-\s+elsik", surface), name
