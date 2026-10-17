"""Chrome spelled with typographic punctuation stays chrome (docs/09 P58).

docling's HTML backend cleans every item's text as it builds the document: a
dash becomes "-", a curly quote a straight one, an ellipsis "...", and a soft
hyphen or a zero-width character goes. The bridge tested a furniture item
against the chrome regions as the page spells them, so a menu item whose
apostrophe the page curls was not found in its own `<nav>`, and the reflow
moved it to the body layer: into content.md, content.txt and every chunk,
while "Legal" beside it stayed out. The regions are now compared as the backend writes
text, and what the backend filed under a `<footer>` is not tested by its
text at all, being chrome by where it sits.

The page spellings below are written out here, not read from the backend's
cleaning table: the property is that what the page says stays out, so the
list of what it says cannot come from the component under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from webshot.capture.snapshot import standalone_document
from webshot.extract.docling_bridge import ExtractionResult, extract

FIXTURE = Path(__file__).parent / "fixtures" / "chrome_typography.html"

#: Every character the backend rewrites, by name. The two no-break spaces did
#: not leak, since splitting on whitespace already reads them as spaces; they
#: are here so a change that broke that would show.
SPELLINGS = {
    "no-break space": "\N{NO-BREAK SPACE}",
    "zero-width space": "\N{ZERO WIDTH SPACE}",
    "zero-width non-joiner": "\N{ZERO WIDTH NON-JOINER}",
    "zero-width joiner": "\N{ZERO WIDTH JOINER}",
    "hyphen": "\N{HYPHEN}",
    "non-breaking hyphen": "\N{NON-BREAKING HYPHEN}",
    "figure dash": "\N{FIGURE DASH}",
    "en dash": "\N{EN DASH}",
    "em dash": "\N{EM DASH}",
    "horizontal bar": "\N{HORIZONTAL BAR}",
    "left single quote": "\N{LEFT SINGLE QUOTATION MARK}",
    "right single quote": "\N{RIGHT SINGLE QUOTATION MARK}",
    "left double quote": "\N{LEFT DOUBLE QUOTATION MARK}",
    "right double quote": "\N{RIGHT DOUBLE QUOTATION MARK}",
    "ellipsis": "\N{HORIZONTAL ELLIPSIS}",
    "soft hyphen": "\N{SOFT HYPHEN}",
    "byte-order mark": "\N{ZERO WIDTH NO-BREAK SPACE}",
    "narrow no-break space": "\N{NARROW NO-BREAK SPACE}",
    "word joiner": "\N{WORD JOINER}",
}

#: Where chrome sits: before the first heading, where the backend files
#: everything as furniture and the bridge decides by text, and a footer after
#: it, which only the backend's <footer> rule puts there.
REGIONS = {
    "nav before the heading": "<nav>{chrome}</nav><h1>Trail guide</h1><p>Body.</p>",
    "header before the heading": (
        "<header>{chrome}</header><h1>Trail guide</h1><p>Body.</p>"
    ),
    "aside before the heading": (
        "<aside>{chrome}</aside><h1>Trail guide</h1><p>Body.</p>"
    ),
    "footer before the heading": (
        "<footer>{chrome}</footer><h1>Trail guide</h1><p>Body.</p>"
    ),
    "footer under the heading": (
        "<h1>Trail guide</h1><p>Body.</p><footer>{chrome}</footer>"
    ),
}


def _surfaces(result: ExtractionResult) -> dict[str, str]:
    return {
        "content.md": result.markdown,
        "content.txt": result.text,
        "chunks.jsonl": "\n".join(seed.text for seed in result.chunk_seeds),
    }


def _layers(result: ExtractionResult) -> dict[str, list[str]]:
    texts: list[dict[str, Any]] = json.loads(result.document_json)["texts"]
    layers: dict[str, list[str]] = {}
    for text in texts:
        layers.setdefault(text["content_layer"], []).append(text["text"])
    return layers


@pytest.fixture(scope="module")
def fixture_result() -> ExtractionResult:
    return extract(FIXTURE.read_text(encoding="utf-8"), [])


#: The fixture's chrome as content.json holds it, cleaned by the backend. On
#: master each of these reached every text surface; "Stores", "Shipping &
#: returns" and "Legal", spelled in ASCII, never did.
FIXTURE_CHROME = [
    "Quillfen Outfitters - since 1999",
    "What's new",
    '"Staff picks"',
    "Search...",
    "Giftcards",
    "Signin",
    "Stores",
    "Free shipping - today only",
    "Posted by Rowan - 3 comments",
    "Quillfen © 2026 - all rights reserved",
    "Shipping & returns",
    "Privacy... and cookies",
    "Legal",
    "Store's hours",
    "Member's badge",
]


@pytest.mark.parametrize("chrome", FIXTURE_CHROME)
def test_chrome_stays_out_of_every_text_surface(
    fixture_result: ExtractionResult, chrome: str
) -> None:
    for surface, text in _surfaces(fixture_result).items():
        assert chrome not in text, surface
    # ...and is kept, on its own layer, in the lossless document.
    assert chrome in _layers(fixture_result)["furniture"]


def test_an_item_holding_a_line_break_stays_chrome(
    fixture_result: ExtractionResult,
) -> None:
    """ASCII text the backend rewrites in another way: it writes a list
    item's `<br>` into the item's text as U+E000, so the text test could not
    find "Open daily" in the footer, nor "Late nights" in the menu. Under a
    footer the item is not tested by its text; before the first heading the
    character is read as the break it stands for."""
    for surface, text in _surfaces(fixture_result).items():
        for word in ("Open", "daily", "Late", "nights"):
            assert word not in text, (surface, word)


def test_content_keeps_its_place(fixture_result: ExtractionResult) -> None:
    """The fix narrows nothing that was content: a lede before the first
    heading, spelled with the same punctuation, still reaches every surface."""
    lede = "A lede before the heading - the page's own words."
    for surface, text in _surfaces(fixture_result).items():
        assert lede in text, surface
        assert 'The ridge path takes "about an hour" - longer in rain.' in text
        assert "A short post about boots." in text
    assert fixture_result.markdown.startswith(lede + "\n\n# Trail guide\n")
    assert fixture_result.warnings == []


@pytest.mark.parametrize("region", REGIONS)
def test_every_character_the_backend_rewrites_stays_chrome(region: str) -> None:
    """One chrome item per character, in a paragraph and in a list, in every
    place chrome sits. On master 17 of the 19 leaked in each place."""
    # Each item carries a token of its own, which no cleaning touches, so a
    # leak is found whatever the backend made of the character beside it.
    tokens = {name: f"Item{index:02d}" for index, name in enumerate(SPELLINGS)}
    words = [f"Menu{SPELLINGS[name]}{token}" for name, token in tokens.items()]
    chrome = "".join(f"<p>{word}</p>" for word in words)
    chrome += "<ul>" + "".join(f"<li>{word} link</li>" for word in words) + "</ul>"
    page = standalone_document("Chrome", "en", REGIONS[region].format(chrome=chrome))
    result = extract(page, [])
    for surface, text in _surfaces(result).items():
        leaked = [name for name, token in tokens.items() if token in text]
        assert leaked == [], surface
        assert "Body." in text, surface


@pytest.mark.parametrize("region", REGIONS)
def test_a_list_item_holding_a_line_break_stays_chrome(region: str) -> None:
    chrome = "<ul><li>Opening<br>Itembreak</li><li>Legal</li></ul>"
    page = standalone_document("Chrome", "en", REGIONS[region].format(chrome=chrome))
    result = extract(page, [])
    for surface, text in _surfaces(result).items():
        assert "Itembreak" not in text, surface
        assert "Body." in text, surface
