"""A converted document's pictures are its own bytes, or a line saying why not.

MarkItDown wrote a deck's picture as `![alt](Picture2.jpg)`, a name it made
up, and the page resolved that in the deck's folder: a failed request, or
whatever file of that name sat there.  A Word file's pictures were truncated to
`data:image/png;base64...`, and an EPUB's named paths inside the book, which
also resolved beside it.  Now each is a `data:` URL the page draws, a TIFF is
written again as PNG, and a picture nothing here draws (a metafile), one a deck
only links to, and one a book names but does not hold are each a line of text
and a warning (docs/09 P36).

The fixtures are `picture_*` in `tests/fixtures/office/`, written by
`tools/fixtures/office.py`.
"""

from __future__ import annotations

import base64
import html
import importlib.util
import io
import re
import shutil
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from webshot.acquire.adapters import materialize_source
from webshot.acquire.markitdown_bridge import (
    DRAWABLE_PICTURES,
    _drawable,
    _pictures_as_data,
)
from webshot.bundle.build import _recorded_source
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import PICTURE_ALTS, SENTINELS  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"


def _requires(module: str) -> None:
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is absent — install webshot[office] to run this")


def _page(path: Path) -> tuple[str, list[str]]:
    materialized = materialize_source(path.as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
        return page.split("<main>", 1)[1], list(materialized.warnings)
    finally:
        materialized.cleanup()


def _pictures(body: str) -> list[tuple[str, str]]:
    """(alt, media type) for each `<img>` the page draws."""
    found = []
    for tag in re.findall(r"<img\b[^>]*>", body):
        alt = html.unescape(re.search(r'alt="([^"]*)"', tag)[1])  # type: ignore[index]
        src = re.search(r'src="([^"]*)"', tag)[1]  # type: ignore[index]
        found.append((alt, src.split(";", 1)[0]))
    return found


def _png(rgb: tuple[int, int, int], kind: str) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (8, 6), rgb).save(output, kind)
    return output.getvalue()


def _data(media_type: str, data: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode()}"


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


def test_a_picture_the_browser_draws_is_kept_as_it_is() -> None:
    for media_type in sorted(DRAWABLE_PICTURES):
        src = _data(media_type, b"bytes")
        assert _drawable(src) == (src, "")


def test_a_tiff_is_written_again_as_png() -> None:
    drawn, reason = _drawable(_data("image/tiff", _png((20, 160, 60), "TIFF")))
    assert reason == ""
    assert drawn is not None and drawn.startswith("data:image/png;base64,")
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(drawn.split(",", 1)[1]))) as image:
        assert image.format == "PNG"
        assert image.getpixel((4, 3)) == (20, 160, 60)


@pytest.mark.parametrize(
    ("src", "reason"),
    [
        (
            "data:image/x-wmf;base64,AAAA",
            "a Windows Metafile (WMF), which a browser cannot draw",
        ),
        (
            "data:image/x-emf;base64,AAAA",
            "an Enhanced Metafile (EMF), which a browser cannot draw",
        ),
        (
            "data:image/vnd.ms-photo;base64,AAAA",
            "an HD Photo (JPEG XR), which a browser cannot draw",
        ),
        (
            "data:image/x-unknown;base64,AAAA",
            "image/x-unknown, which a browser cannot draw",
        ),
        ("data:image/tiff;base64,not base64!", "its data could not be decoded"),
        ("data:image/tiff;base64,AAAA", "a TIFF that Pillow could not read"),
    ],
)
def test_a_picture_nothing_here_draws_is_refused_with_its_reason(
    src: str, reason: str
) -> None:
    assert _drawable(src) == (None, reason)


def test_a_document_never_leaves_a_path_for_the_browser_to_resolve() -> None:
    """The page's base is the document's folder: a path loaded whatever file of
    that name sat beside it.  A remote picture is the document's own, and stays."""
    soup = BeautifulSoup(
        '<p><img src="figure.png" alt="Named"></p>'
        '<p><img src="https://example.invalid/remote.png" alt="Remote"></p>'
        '<p><img src="file:///etc/picture.png" alt="Local"></p>',
        "html.parser",
    )
    warnings = _pictures_as_data(soup, None, None)
    assert [image["src"] for image in soup.find_all("img")] == [
        "https://example.invalid/remote.png"
    ]
    assert warnings == [
        "a picture (“Named”) is not in the page: it names figure.png, a path "
        "the page cannot reach.",
        "a picture (“Local”) is not in the page: it names "
        "file:///etc/picture.png, a path the page cannot reach.",
    ]
    assert "[Picture: Named — not shown:" in soup.get_text()


# --------------------------------------------------------------------------- #
# Each format, from its fixture
# --------------------------------------------------------------------------- #


def test_a_decks_pictures_are_its_own(tmp_path: Path) -> None:
    """Even with a file of each name MarkItDown makes up sitting beside it."""
    _requires("pptx")
    deck = tmp_path / "picture_deck.pptx"
    shutil.copy(FIXTURE_ROOT / "picture_deck.pptx", deck)
    for number in range(1, 10):
        (tmp_path / f"Picture{number}.jpg").write_bytes(_png((220, 20, 20), "PNG"))
    body, warnings = _page(deck)
    assert _pictures(body) == [
        (PICTURE_ALTS["png"], "data:image/png"),
        (PICTURE_ALTS["tiff"], "data:image/png"),
    ]
    assert SENTINELS["picture_deck.pptx"] in body
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    assert (
        f"[Picture: {PICTURE_ALTS['metafile']} — not shown: a Windows Metafile "
        "(WMF), which a browser cannot draw]"
    ) in text
    assert (
        f"[Picture: {PICTURE_ALTS['linked']} — not shown: the deck links to it "
        "rather than holding it]"
    ) in text
    assert warnings == [
        f"Slide 2: a picture (“{PICTURE_ALTS['metafile']}”) is not in the "
        "page: a Windows Metafile (WMF), which a browser cannot draw.",
        f"Slide 2: a picture (“{PICTURE_ALTS['linked']}”) is not in the "
        "page: the deck links to it rather than holding it.",
    ]


def test_a_word_files_pictures_are_kept_whole() -> None:
    """MarkItDown truncated them to `data:image/png;base64...` unless asked."""
    _requires("mammoth")
    body, warnings = _page(FIXTURE_ROOT / "picture_report.docx")
    assert _pictures(body) == [
        (PICTURE_ALTS["png"], "data:image/png"),
        (PICTURE_ALTS["tiff"], "data:image/png"),
    ]
    assert "base64..." not in body
    assert warnings == [
        f"The document: a picture (“{PICTURE_ALTS['metafile']}”) is not in "
        "the page: an Enhanced Metafile (EMF), which a browser cannot draw.",
    ]


def test_a_books_pictures_are_read_from_the_book() -> None:
    body, warnings = _page(FIXTURE_ROOT / "picture_book.epub")
    assert _pictures(body) == [
        (PICTURE_ALTS["png"], "data:image/png"),
        (PICTURE_ALTS["tiff"], "data:image/png"),
    ]
    assert warnings == [
        f"OEBPS/text/chapter.xhtml: a picture (“{PICTURE_ALTS['missing']}”) "
        "is not in the page: it names ../images/missing.png, which the book does "
        "not hold.",
    ]


def test_alt_text_in_a_placeholder_is_still_text() -> None:
    """The placeholder quotes the alt text as page text, so the P28 encoding
    applies to it once, all the way to the page: no element, no reference
    written twice."""
    from webshot.acquire.adapters import _render_markdown_document
    from webshot.acquire.markitdown_bridge import _TextHtmlConverter

    converted = _TextHtmlConverter().convert_string(
        '<p><img src="data:image/x-wmf;base64,AAAA" '
        'alt="&lt;b&gt;bold&lt;/b&gt; *x* &amp;copy;"></p>'
    )
    page = _render_markdown_document(
        converted.markdown, fallback_title="t", label="l", base_href=""
    )
    body = page.split("<main>", 1)[1]
    assert "<b>" not in body and "<em>" not in body
    assert (
        "[Picture: &lt;b&gt;bold&lt;/b&gt; *x* &amp;copy; \u2014 not shown: a Windows "
        "Metafile (WMF), which a browser cannot draw]"
    ) in body


# --------------------------------------------------------------------------- #
# The bundle
# --------------------------------------------------------------------------- #


def test_assets_json_records_a_kept_files_picture_by_type_and_length() -> None:
    src = _data("image/png", b"x" * 300)
    payload = src.split(",", 1)[1]
    assert _recorded_source(src, kept=True) == (
        f"data:image/png;base64,… ({len(payload):,} characters; the picture "
        "is in source/)"
    )


def test_a_web_pages_data_url_and_every_other_url_are_recorded_as_they_are() -> None:
    src = _data("image/png", b"x" * 300)
    assert _recorded_source(src, kept=False) == src
    for url in ("https://example.invalid/a.png", "file:///tmp/a.png", ""):
        assert _recorded_source(url, kept=True) == url
