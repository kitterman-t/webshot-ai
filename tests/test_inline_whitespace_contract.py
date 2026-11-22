"""content.json reads as the page does to docling-core itself (docs/09 P131).

docling-core 2.106 made schema 1.11's inline whitespace contract: each run of
an inline group carries the whitespace the page has at its edges, and every
serializer joins the runs with nothing between. A document stamped 1.11.0
whose runs were stripped, as the backend wrote them before docling-slim
2.147, merged every word beside a link or emphasis into the next for any
reader that loads content.json and exports it with docling's defaults: "See
the[project documentation](…)for context." Since docling-slim 2.147 the
backend keeps that whitespace, and the bridge writes the space the page draws
where the markup has none (a link drawn apart by its icon, spans no snapshot
measured), so the runs say what the page says, the stamp is honest, and a
touching join such as "H<sub>2</sub>O" or "un<b>break</b>able" is one word
for every reader, not only in WebShot's own surfaces.

`tests/fixtures/inline_whitespace_contract.html` uses made-up words, so each
can be searched for whole.
"""

from __future__ import annotations

import json
import warnings
from itertools import pairwise
from pathlib import Path
from typing import Any

from webshot.extract.docling_bridge import ExtractionResult, extract

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "inline_whitespace_contract.html"
)

#: The lines the page's own whitespace, or the bridge, keeps apart where the
#: backend alone ran them together, each found on a captured page (P131-2):
#: a line ending between two elements, which the backend skips; a space only
#: an `aria-hidden` element holds, which it hides; one at the edge of each of
#: two runs it joins, which it doubles; and an empty block inside an inline
#: element, which ends no line to it. The last, a line ending either side of
#: an element the backend hides, is a breadcrumb's separator, which the fix's
#: review found reading as one word (P131-6).
CAPTURED = (
    "Harrowgate 555.010.4477",
    "1.2M 2w ago",
    "5 minute read 12 Oct 2026",
    "Tessaly Clips and 2 more",
    "Quenmoor Valdrith",
)
#: Each paragraph and item of the fixture as Markdown writes it, links and
#: emphasis mid-sentence, touching scripts and a formatted part of a word.
MARKDOWN = (
    "Read the [quarvel guide](https://example.com/quarvel) before the "
    "*stendrik* review, then **plonder** it.",
    "Water is H2O, the area is r2, and un**break**able stays one word.",
    "The vashtel dashboard [Open in a new tab](https://example.com/vashtel) "
    "is drawn apart from its link by an icon.",
    "Gorvane Module 3 are spans no snapshot measured.",
    "- See the [moltrin list](https://example.com/moltrin) and the `tavrel` flag.",
    # A line break in a list item is a space on its line (docs/09 P56).
    "- **Morvane** *quellish* ends the item.",
    *CAPTURED,
)
#: The same as plain text.
TEXT = (
    "Read the quarvel guide before the stendrik review, then plonder it.",
    "Water is H2O, the area is r2, and unbreakable stays one word.",
    "The vashtel dashboard Open in a new tab is drawn apart from its link by an icon.",
    "Gorvane Module 3 are spans no snapshot measured.",
    "- See the moltrin list and the tavrel flag.",
    "- Morvane quellish ends the item.",
    *CAPTURED,
)


def _result() -> ExtractionResult:
    return extract(FIXTURE.read_text(encoding="utf-8"), [])


def _loaded(document_json: str) -> tuple[Any, list[str]]:
    """content.json as docling-core loads it, and what it warned while it did."""
    from docling_core.types.doc import DoclingDocument

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        document = DoclingDocument.model_validate(json.loads(document_json))
    return document, [str(warning.message) for warning in caught]


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def test_content_json_is_stamped_with_the_schema_its_runs_follow() -> None:
    """Stamped 1.11.0, so docling-core loads it as it is: an older stamp is
    one it migrates, stripping no run, with a warning."""
    result = _result()
    document = json.loads(result.document_json)
    assert document["version"] == "1.11.0"
    _, warned = _loaded(result.document_json)
    assert warned == []
    runs = {
        item["self_ref"]: item["text"]
        for item in document["texts"]
        if item["parent"]["$ref"].startswith("#/groups/")
    }
    groups = [
        [runs[child["$ref"]] for child in group["children"] if child["$ref"] in runs]
        for group in document["groups"]
        if group["label"] == "inline"
    ]
    # The page's whitespace is in the runs, one side of each boundary, and
    # the touching joins have none.
    assert ["Read the ", "quarvel guide ", "before the "] == groups[0][:3]
    assert ["Water is H", "2", "O, the area is r", "2"] == groups[1][:4]
    assert [", and un", "break", "able stays one word."] == groups[1][4:]
    assert ["The vashtel dashboard ", "Open in a new tab "] == groups[2][:2]
    for group in groups:
        for left, right in pairwise(group):
            assert not (left[-1:].isspace() and right[:1].isspace()), (left, right)


def test_docling_core_reads_content_json_as_the_page_reads() -> None:
    """`export_to_markdown()` and `export_to_text()` with docling-core's own
    defaults: no word runs into the one beside a link or emphasis, no space
    doubles, and the touching joins stay one word."""
    document, _ = _loaded(_result().document_json)
    markdown = _lines(document.export_to_markdown())
    text = _lines(document.export_to_text())
    for line in MARKDOWN:
        assert line in markdown, (line, markdown)
    for line in TEXT:
        assert line in text, (line, text)
    words = {word for line in text for word in line.split()}
    for line in TEXT:
        # Each word of the page whole: none merged into its neighbour.
        assert set(line.split()) <= words, line
    for line in (*markdown, *text):
        assert "  " not in line, line


def test_webshot_writes_what_docling_core_reads() -> None:
    """content.md and content.txt hold the lines docling-core's own exports
    of content.json hold: the bridge's inline serializer differs from
    docling's only in wrapping a run of touching parts once."""
    result = _result()
    document, _ = _loaded(result.document_json)
    assert _lines(result.markdown) == _lines(document.export_to_markdown())
    assert _lines(result.text) == _lines(document.export_to_text())
    for line in MARKDOWN:
        assert line in _lines(result.markdown)
    for line in TEXT:
        assert line in _lines(result.text)
    assert result.warnings == []


def test_a_line_ending_between_two_elements_is_a_space_on_one_line_only() -> None:
    """The backend skips a text of only line endings between two elements,
    so the bridge writes one between two texts of a line as the space the
    page draws. One in a cell ends a paragraph of the cell to the backend,
    and one in a `<pre>` is a line of code: both are left as they are."""
    # Imported here, so the other tests run against a bridge without it.
    from webshot.extract.docling_bridge import space_inline_gaps

    line = "<p><span>gam</span>\n<b>ma</b></p>"
    cell = "<table><tr><td><span>alpha</span>\n<span>beta</span></td></tr></table>"
    code = "<pre><span>a</span>\n<span>b</span></pre>"
    assert space_inline_gaps(line) == "<p><span>gam</span> <b>ma</b></p>"
    # Line endings either side of an element the backend hides are one
    # blank, so one space: the first, which the second once dropped
    # (docs/09 P131-6). The backend skips the second.
    crumb = '<p><a>Home</a>\n<span aria-hidden="true">/</span>\n<a>Docs</a></p>'
    assert space_inline_gaps(crumb) == (
        '<p><a>Home</a> <span aria-hidden="true">/</span>\n<a>Docs</a></p>'
    )
    assert space_inline_gaps(cell) is cell
    assert space_inline_gaps(code) is code


def test_a_pictures_caption_is_on_its_pictures_layer() -> None:
    """docling-slim 2.149 hangs a picture's caption under its picture. A
    cover above the first heading, outside page chrome, goes back to the
    body by where it stands, with its alt-text caption, as before, while a
    nav's logo stays chrome and its caption is written nowhere. Decided
    with the list it stands in, the cover's caption stayed on the furniture
    layer and content.md lost it; read as the list's text, the logo's sent
    the nav to the body (docs/09 P131)."""
    page = (FIXTURE.parent / "picture_caption_layer.html").read_text(encoding="utf-8")
    result = extract(page, [])
    assert "The Vardle Almanac, front cover" in _lines(result.markdown)
    assert "The Vardle Almanac, front cover" in _lines(result.text)
    for surface in (result.markdown, result.text):
        assert "Quillmark logo" not in surface
        assert "Shelf" not in surface
    document = json.loads(result.document_json)
    (cover,) = [
        item
        for item in document["texts"]
        if item["text"] == "The Vardle Almanac, front cover"
    ]
    assert cover["content_layer"] == "body"
    assert cover["parent"]["$ref"].startswith("#/pictures/")
