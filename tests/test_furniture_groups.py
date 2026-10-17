"""Groups before the first heading reach the body with their items (docs/09 P53).

The backend files everything before a page's first heading on the furniture
layer, which every text surface skips, and the bridge moved each text item
back to the body on its own. The groups holding them (a list, the list an item
holds, a paragraph's inline runs, a details section) stayed on furniture, so
the serializers met each item alone: content.md, content.txt and the chunks
wrote a nested list flat with every item a loose list of its own, and a
paragraph as one line per formatted run. A run as short as "Terms" read out of
a menu and left its sentence, and a menu item with a curly quote, which the
backend writes straight, read as content.

Content before the first heading must read as it does on a page with no
heading at all, where the backend files nothing as furniture; chrome must stay
furniture as a whole; and nothing after the first heading may change. No
browser: each case calls `extract`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from webshot.extract.docling_bridge import (
    _SnapshotFacts,
    extract,
    validate_document_json,
)

FIXTURE = Path(__file__).parent / "fixtures" / "furniture_groups.html"
HEADING = "Zenvar"


def _page(markup: str) -> str:
    return f'<html lang="en"><head></head><body>{markup}</body></html>'


def _nodes(document_json: str) -> list[dict[str, Any]]:
    document = json.loads(document_json)
    return [
        node
        for kind in ("groups", "texts", "tables", "pictures")
        for node in document[kind]
    ]


#: Content as it may open a page or a converted document, before any heading.
CONTENT = {
    "nested list": "<ul><li>Alpha<ul><li>Gorvane</li></ul></li><li>Istrel</li></ul>",
    "ordered list with a start": (
        '<ol start="3"><li>Third<ol><li>Inner</li><li>Second inner</li></ol></li>'
        "<li>Fourth</li></ol>"
    ),
    "three levels, mixed": (
        "<ul><li>One<ol><li>Two<ul><li>Three</li></ul></li></ol></li>"
        "<li>Back out</li></ul>"
    ),
    "paragraph with formatted runs": (
        "<p>Plain, <b>bold</b>, <em>slanted</em> and "
        '<a href="#here">linked</a> words.</p>'
    ),
    "item with formatted runs": (
        "<ul><li>Item <b>bold</b> end</li><li>Second <i>item</i></li></ul>"
    ),
    "description list": "<dl><dt>Quellan</dt><dd>A river crossing.</dd></dl>",
    "details": "<details><summary>Sorvik</summary><p>Hidden detail.</p></details>",
    "item holding code": (
        "<ul><li>Run this:<pre><code>pip install x\nwebshot run</code></pre></li>"
        "<li>Then this</li></ul>"
    ),
    "item holding a table": (
        "<ul><li>Holds a table<table><tr><td>Brask</td><td>Velm</td></tr>"
        "</table></li></ul>"
    ),
    "two lists and a paragraph": (
        "<ul><li>First</li><li>Second</li></ul><p>Between <b>them</b>.</p>"
        "<ol><li>Third</li></ol>"
    ),
}


@pytest.mark.parametrize("markup", CONTENT.values(), ids=CONTENT.keys())
def test_content_before_the_first_heading_reads_as_on_a_page_with_none(
    markup: str,
) -> None:
    before = extract(_page(f"{markup}<h1>{HEADING}</h1>"), [])
    alone = extract(_page(markup), [])

    assert before.markdown == f"{alone.markdown}\n# {HEADING}\n"
    assert before.text == f"{alone.text}\n{HEADING}\n"
    assert [seed.text for seed in before.chunk_seeds if not seed.headings] == [
        seed.text for seed in alone.chunk_seeds
    ]
    assert [seed.kind for seed in before.chunk_seeds if not seed.headings] == [
        seed.kind for seed in alone.chunk_seeds
    ]
    assert before.tables == alone.tables
    # Every node of it is on the body, as the backend puts it with no heading.
    assert {node["content_layer"] for node in _nodes(before.document_json)} == {"body"}
    validate_document_json(before.document_json)


def test_a_nested_list_before_the_first_heading_stays_nested() -> None:
    """The case as reported: Gorvane under Alpha, one tight list."""
    result = extract(
        _page(
            "<ul><li>Alpha<ul><li>Gorvane</li></ul></li><li>Istrel</li></ul>"
            "<h1>T</h1><p>After.</p>"
        ),
        [],
    )
    assert result.markdown == "- Alpha\n    - Gorvane\n- Istrel\n\n# T\n\nAfter.\n"
    assert result.chunk_seeds[0].text == "- Alpha\n    - Gorvane\n- Istrel"
    assert len(result.chunk_seeds[0].doc_items) == 3


#: Page chrome before the first heading, each region with something the old
#: item-by-item test let through.
CHROME = {
    "nav list with a curly quote, a dash and a nested list": (
        "<nav><ul><li>What\N{RIGHT SINGLE QUOTATION MARK}s new</li>"
        "<li>Guides<ul><li>Maps</li></ul></li>"
        "<li>Help \N{EM DASH} contact</li></ul></nav>"
    ),
    "header paragraph with formatted runs": (
        "<header><p>Welcome <b>back</b> to "
        "\N{LEFT DOUBLE QUOTATION MARK}Orlenvale\N{RIGHT DOUBLE QUOTATION MARK}"
        "\N{HORIZONTAL ELLIPSIS}</p></header>"
    ),
    "aside list": "<aside><ul><li>Related</li><li>Popular</li></ul></aside>",
    "nav list with a line break in an item": (
        "<nav><ul><li>Home</li><li>Line one<br>line two</li><li>Help</li></ul></nav>"
    ),
    "footer before the heading": (
        "<footer><p>Small print</p><ul><li>Legal</li><li>Privacy</li></ul></footer>"
    ),
}


@pytest.mark.parametrize("markup", CHROME.values(), ids=CHROME.keys())
def test_chrome_before_the_first_heading_stays_furniture_whole(markup: str) -> None:
    result = extract(_page(f"{markup}<h1>{HEADING}</h1><p>Body.</p>"), [])

    assert result.markdown == f"# {HEADING}\n\nBody.\n"
    assert result.text == f"{HEADING}\n\nBody.\n"
    assert [seed.text for seed in result.chunk_seeds] == ["Body."]
    layers = {
        node["self_ref"]: node["content_layer"] for node in _nodes(result.document_json)
    }
    document = json.loads(result.document_json)
    kept = {
        node["self_ref"]
        for node in document["texts"]
        if node["text"] in {HEADING, "Body."}
    }
    assert {ref: layer for ref, layer in layers.items() if ref not in kept} == {
        ref: "furniture" for ref in layers if ref not in kept
    }


def test_a_run_that_also_reads_in_a_menu_stays_in_its_sentence() -> None:
    """ "Terms" and "Maps" each read out of the menu on their own; the
    paragraph as a whole does not, so it is content, every run of it."""
    result = extract(
        _page(
            "<nav><ul><li>Terms of travel</li><li>Maps</li></ul></nav>"
            '<p>Please read the <b>Terms</b> before the <a href="#m">Maps</a> '
            "page.</p><h1>T</h1>"
        ),
        [],
    )
    assert result.markdown.startswith(
        "Please read the **Terms** before the [Maps](#m) page.\n\n# T\n"
    )
    assert result.chunk_seeds[0].text == (
        "Please read the **Terms** before the [Maps](#m) page."
    )


def test_an_item_that_also_reads_in_a_menu_stays_in_its_list() -> None:
    result = extract(
        _page(
            "<nav><ul><li>Home</li><li>About</li></ul></nav>"
            "<ul><li>Home</li><li>Garden</li></ul><h1>T</h1>"
        ),
        [],
    )
    assert result.markdown == "- Home\n- Garden\n\n# T\n"


def test_nothing_after_the_first_heading_changes() -> None:
    """The fixture after its heading reads the same with or without the
    content and chrome before it, on every surface and layer."""
    source = FIXTURE.read_text(encoding="utf-8")
    head, _, rest = source.partition("<body>")
    _, _, from_heading = rest.partition("<h1>")
    full = extract(source, [])
    bare = extract(f"{head}<body>\n<h1>{from_heading}", [])

    marker = "# Orlenvale\n"
    assert full.markdown[full.markdown.index(marker) :] == bare.markdown
    assert full.text[full.text.index("Orlenvale\n") :] == bare.text
    assert [s.text for s in full.chunk_seeds if s.headings] == [
        s.text for s in bare.chunk_seeds
    ]

    def after_heading(document_json: str) -> list[tuple[str, str, str]]:
        document = json.loads(document_json)
        texts = document["texts"]
        start = next(i for i, t in enumerate(texts) if t["label"] == "title")
        return [(t["label"], t["text"], t["content_layer"]) for t in texts[start:]]

    assert after_heading(full.document_json) == after_heading(bare.document_json)


def test_the_fixture_reads_as_its_page_does() -> None:
    result = extract(FIXTURE.read_text(encoding="utf-8"), [])
    assert result.markdown.startswith(
        "- Alpha\n    - Gorvane\n- Istrel\n\n"
        "3. Third step\n4. Fourth step\n\n"
        "Please read the **Terms** before the [Maps](#maps) page.\n\n"
    )
    for chrome in (
        "Welcome",
        "What's new",
        "What\N{RIGHT SINGLE QUOTATION MARK}s new",
        "Guides",
        "Help",
    ):
        assert chrome not in result.markdown
        assert chrome not in result.text
        assert all(chrome not in seed.text for seed in result.chunk_seeds)
    # The table a list item holds reaches the surfaces it was missing from.
    assert result.tables == [[["Brask", "Velm"]]]
    assert "| Brask | Velm |" in result.markdown
    assert [seed.kind for seed in result.chunk_seeds].count("table") == 1
    # The lists and the paragraph's runs are on the body in content.json.
    document = json.loads(result.document_json)
    by_ref = {node["self_ref"]: node for node in _nodes(result.document_json)}
    content_groups = [
        group
        for group in document["groups"]
        if any(
            by_ref[child["$ref"]].get("text") in {"Alpha", "Third step", "Terms"}
            for child in group["children"]
        )
    ]
    assert len(content_groups) == 3
    assert {group["content_layer"] for group in content_groups} == {"body"}
    assert result.warnings == []
    validate_document_json(result.document_json)


def test_the_doctags_do_not_depend_on_the_layer() -> None:
    """The doctags export holds both layers, so moving a group does not
    change it: the nested list was nested there before the fix too."""
    markup = CONTENT["nested list"]
    before = extract(_page(f"{markup}<h1>{HEADING}</h1>"), [])
    after = extract(_page(f"<h1>{HEADING}</h1>{markup}"), [])
    body = re.compile(r"<title>[^<]*</title>\n")
    assert body.sub("", before.doctags) == body.sub("", after.doctags)


def test_chrome_is_compared_as_the_backend_cleans_text() -> None:
    """Both sides go through the cleaner the facts are built with, which P58
    makes required, so every comparison is cleaned, the block test's (P53)
    and the item test's alike."""
    nav = (
        "<nav>What\N{RIGHT SINGLE QUOTATION MARK}s new \N{EM DASH} "
        "\N{LEFT DOUBLE QUOTATION MARK}Help\N{RIGHT DOUBLE QUOTATION MARK}"
        "\N{HORIZONTAL ELLIPSIS} and more</nav><p>Body</p>"
    )

    def clean(text: str) -> str:
        table = {
            "\N{RIGHT SINGLE QUOTATION MARK}": "'",
            "\N{EM DASH}": "-",
            "\N{LEFT DOUBLE QUOTATION MARK}": '"',
            "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
            "\N{HORIZONTAL ELLIPSIS}": "...",
        }
        return "".join(table.get(char, char) for char in text)

    cleaned = 'What\'s new - "Help"... and'
    as_given = _SnapshotFacts(clean=lambda text: text)
    as_given.feed(nav)
    assert not as_given.is_chrome_text(cleaned)
    facts = _SnapshotFacts(clean=clean)
    facts.feed(nav)
    assert facts.is_chrome_text(cleaned)
    # The page's own spelling is cleaned too, so it reads out of the region.
    assert facts.is_chrome_text("What\N{RIGHT SINGLE QUOTATION MARK}s new")
    assert not facts.is_chrome_text("Body")
    assert not facts.is_chrome_text("")
