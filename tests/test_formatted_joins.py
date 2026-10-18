"""A word with a bold, code or linked part is one word where the page draws it as one (docs/09 P70-11).

P48 joins the text of an element docling's HTML backend formats or links to
the text it touches in the markup, as Chromium's selection text joins it.
The page does not always draw the two as one: a link with an icon before
its text, or in its padding before a comma, and inline code with a wide
padding are drawn apart from what they run into, and the PDF prints them
apart. P70 measures a span's edges on the live glyphs; the snapshot now
measures these elements' edges the same way, and records on each one whose
text the markup runs into its neighbour's, as `data-webshot-touches`, the
edges the page draws with no gap, empty where it draws neither. The bridge
joins at a named edge and nowhere else. An element without the attribute,
from a snapshot taken before this, joins by P48's rule, so old captures read
as they did.

`tests/fixtures/formatted_joins.html` uses made-up words, so each can be
searched for whole. The browser tests capture it end to end, where the
snapshot measures the edges; the rest call the bridge on markup, which
carries the attribute as the capture records it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.capture.snapshot import TOUCH_ATTRIBUTE
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import _JOINED_TAGS, extract

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "formatted_joins.html"

#: The words the page draws whole: bold, a link and a subscript touching
#: text, and code padded by 1 px, 0.0625 em (P70-6 counts 0.065 em of inline
#: code's padding as touching).
JOINED = (
    "Drevalish",  # bold, in the heading
    "selvoric",  # bold
    "quendral",  # strong
    "braskovan",  # a link whose text runs into a word
    "cor2vel",  # a subscript
    "telvo,",  # a link and its comma
    "vintoresk",  # code padded by 1 px
    "(mardel)",
)
#: Pairs the markup runs together and the page draws apart: code padded by
#: 0.4 em, a link with an icon in generated content before its text, a link
#: with an icon in its padding before a comma, a link holding an icon image,
#: and bold with margins whose own attribute claims both edges.
DRAWN_APART = (
    ("sko", "lim"),
    ("lim", "ber"),
    ("(", "parvesh"),
    ("parvesh", ")"),
    ("see", "Holdren"),
    ("Grevik", ","),
    ("hal", "Tamsel"),
    ("tor", "mel"),
    ("mel", "ask"),
)

#: What the capture writes for each formatted element the markup runs into
#: the text beside it, as `test_the_snapshot_records_each_formatted_edge`
#: reads it in `content.html`. The bridge tests below read the fixture with
#: these in place of its own markup.
_LINK = '<a href="#later" {} rel="noopener noreferrer">'
RECORDED = (
    ("Dre<b>val</b>ish", f'Dre<b {TOUCH_ATTRIBUTE}="before after">val</b>ish'),
    ("sel<b>vor</b>ic", f'sel<b {TOUCH_ATTRIBUTE}="before after">vor</b>ic'),
    (
        "<strong>quen</strong>dral",
        f'<strong {TOUCH_ATTRIBUTE}="after">quen</strong>dral',
    ),
    (
        '<a href="#later">brask</a>ovan',
        _LINK.format(f'{TOUCH_ATTRIBUTE}="after"') + "brask</a>ovan",
    ),
    ("cor<sub>2</sub>vel", f'cor<sub {TOUCH_ATTRIBUTE}="before after">2</sub>vel'),
    (
        '<a href="#later">telvo</a>,',
        _LINK.format(f'{TOUCH_ATTRIBUTE}="after"') + "telvo</a>,",
    ),
    (
        "vin<code>tor</code>esk",
        f'vin<code {TOUCH_ATTRIBUTE}="before after">tor</code>esk',
    ),
    (
        "(<code>mardel</code>)",
        f'(<code {TOUCH_ATTRIBUTE}="before after">mardel</code>)',
    ),
    ("sko<code>lim</code>ber", f'sko<code {TOUCH_ATTRIBUTE}="">lim</code>ber'),
    ("(<code>parvesh</code>)", f'(<code {TOUCH_ATTRIBUTE}="">parvesh</code>)'),
    (
        'see<a class="lead" href="#later">Holdren</a>',
        "see" + _LINK.format(f'{TOUCH_ATTRIBUTE}=""') + "Holdren</a>",
    ),
    (
        '<a class="trail" href="#later">Grevik</a>,',
        _LINK.format(f'{TOUCH_ATTRIBUTE}=""') + "Grevik</a>,",
    ),
    (
        f'tor<b class="claimed" {TOUCH_ATTRIBUTE}="before after">mel</b>ask',
        f'tor<b {TOUCH_ATTRIBUTE}="">mel</b>ask',
    ),
)

_TOUCHES_RE = re.compile(rf'\s{TOUCH_ATTRIBUTE}="[^"]*"')


def _source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _recorded(page: str) -> str:
    """`page` with each formatted element as the capture records it."""
    for written, recorded in RECORDED:
        assert page.count(written) == 1, written
        page = page.replace(written, recorded)
    return page


def _unmeasured(page: str) -> str:
    """`page` as a snapshot from before P70-11 carries it: no element names
    its edges, the page's own attribute removed as the snapshot removes it."""
    return _TOUCHES_RE.sub("", page)


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
    return {*tokens, *(token.rstrip(".") for token in tokens)}


def _surfaces(markdown: str, text: str, chunks: str) -> dict[str, str]:
    return {
        "content.md": _plain(markdown),
        "content.txt": text,
        "chunks.jsonl": _plain(chunks),
    }


def _assert_joined_and_apart(surfaces: dict[str, str]) -> None:
    for name, surface in surfaces.items():
        words = _words(surface)
        for word in JOINED:
            assert word in words, (name, word)
        for left, right in DRAWN_APART:
            assert f"{left}{right}" not in surface, (name, left, right)
            assert re.search(rf"{re.escape(left)}\s+{re.escape(right)}", surface), (
                name,
                left,
                right,
            )


# --------------------------------------------------------------------------- #
# The bridge, on markup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tag", sorted(_JOINED_TAGS))
@pytest.mark.parametrize(
    ("touches", "line"),
    [
        (None, "x unbreakable y"),
        ("", "x un break able y"),
        ("before", "x unbreak able y"),
        ("after", "x un breakable y"),
        ("after before", "x unbreakable y"),
        ("start end", "x un break able y"),
    ],
)
def test_a_measured_formatted_element_joins_only_at_a_named_edge(
    tag: str, touches: str | None, line: str
) -> None:
    """With the attribute, an element the backend formats or links joins at
    the edges it names: none for an empty one or tokens the snapshot never
    writes. Without it, the markup is the evidence, as P48 made it."""
    attribute = "" if touches is None else f' {TOUCH_ATTRIBUTE}="{touches}"'
    assert _line(f"<p>x un<{tag}{attribute}>break</{tag}>able y</p>") == line


def test_each_part_joins_where_the_capture_measured_it_touching() -> None:
    """The fixture as the capture records it, with no browser: the parts drawn
    touching are one word on every text surface, the parts drawn apart keep
    docling's space, and the Markdown wraps each part where it touches."""
    result = extract(_recorded(_source()), [])
    chunks = "\n".join(
        record["text"] for record in build_chunk_records(result.chunk_seeds)
    )
    _assert_joined_and_apart(_surfaces(result.markdown, result.text, chunks))
    for written in (
        "Touching: a sel**vor**ic arc, a **quen**dral line, a [brask](#later)ovan "
        "word, the cor2vel mark and a [telvo](#later), with its comma.",
        "Tight code: call vin`tor`esk and (`mardel`).",
        "Padded code: call sko `lim` ber and ( `parvesh` ).",
        "Icons: see [Holdren](#later) once, then [Grevik](#later) , twice, and "
        "hal [Tamsel](#later) last.",
        "So tor **mel** ask claimed.",
    ):
        assert written in result.markdown.splitlines(), written
    assert result.warnings == []


def test_a_snapshot_without_the_evidence_joins_on_the_markup() -> None:
    """A snapshot taken before P70-11 names no formatted element's edges, so
    the markup is the evidence, as on master: every part it runs into the
    text beside it joins, drawn apart or not. The icon image still keeps
    its link's text off the word before, as any image does."""
    lines = extract(_unmeasured(_source()), []).text.splitlines()
    for written in (
        "Touching: a selvoric arc, a quendral line, a braskovan word, the cor2vel "
        "mark and a telvo, with its comma.",
        "Tight code: call vintoresk and (mardel).",
        "Padded code: call skolimber and (parvesh).",
        "Icons: seeHoldren once, then Grevik, twice, and hal Tamsel last.",
        "So tormelask claimed.",
    ):
        assert written in lines, written


# --------------------------------------------------------------------------- #
# The whole pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("formatted-joins")
    pdf = directory / "formatted_joins.pdf"
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
def test_the_snapshot_records_each_formatted_edge(captured: dict[str, str]) -> None:
    """Each formatted element the markup runs into its neighbour carries the
    edges the page draws touching, and an empty attribute where it draws
    neither: the page's own claim on the bold with margins is replaced. The
    link holding an image touches nothing, since the image ends the run."""
    html = captured["content.html"]
    for _, recorded in RECORDED:
        assert recorded in html, recorded
    assert html.count(TOUCH_ATTRIBUTE) == len(RECORDED)
    assert re.search(
        r'hal<a href="#later" rel="noopener noreferrer"><img [^>]*>Tamsel</a>', html
    )


@pytest.mark.browser
def test_every_text_surface_reads_the_page_as_drawn(captured: dict[str, str]) -> None:
    _assert_joined_and_apart(
        _surfaces(
            captured["content.md"], captured["content.txt"], captured["chunks.jsonl"]
        )
    )
    assert captured["warnings"] == "[]"


@pytest.mark.browser
def test_the_pdf_reads_the_same_words(captured: dict[str, str]) -> None:
    """The ground truth the bundle agrees with: each word whole where the page
    draws it whole, and each pair drawn apart printed with whitespace between.
    Read as substrings: pypdf breaks the line at the icon image."""
    pdf = captured["pdf"]
    for word in JOINED:
        assert word in pdf, word
    for left, right in DRAWN_APART:
        assert f"{left}{right}" not in pdf, (left, right)
        assert re.search(rf"{re.escape(left)}\s+{re.escape(right)}", pdf), (
            left,
            right,
        )
