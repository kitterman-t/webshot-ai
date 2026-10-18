"""A link whose text is its target keeps its text on the page (docs/09 P75).

markdownify writes such a link as `<target>`, its `autolinks` shortcut, and
Python-Markdown links that form only for http, https and ftp.  Any other
target it read as an HTML tag: nh3 dropped `<report.pdf>` and the text with
it, kept `<em>` and made the rest of the paragraph italic, and dropped
everything after `<script>`.  The writer writes every link `[text](target)`,
which renders an http or https link the same page as before.

A link in `code`, `kbd` or `samp` is written as its text: markdownify's own
rule, which MarkItDown's `<a>` replaced and kept for `pre` alone, so a link in
code printed its Markdown on the page.

The `bare_link_*` fixtures are written by `tools/fixtures/office.py`; every
word in them is made up.  `tests/test_office_formats.py` captures them and
reads content.md and the PDF.
"""

from __future__ import annotations

import html
import importlib.util
import io
import re
import sys
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup
from markitdown import StreamInfo
from markitdown.converters._markdownify import _CustomMarkdownify

from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import (
    _keeping_edge_spaces,
    _ListSeams,
    _TextHtmlConverter,
    _TextMarkdownify,
)
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import BARE_LINK_TARGETS, bare_link_lines  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: The targets whose page did not change: the ones Python-Markdown autolinks.
AUTOLINKED = {"https"}
#: Spec §6.5: nh3 keeps no URL scheme but http(s), data and mailto, so this
#: link's text stays and its target goes, as for any `file:` link.
NO_HREF = {"file"}

#: http and https addresses that took the shortcut: a character `_as_text`
#: encodes in the text, or MarkItDown percent-encodes in the path, makes the
#: two differ, and the link was written `[text](target)` already.
ADDRESSES = [
    "https://example.invalid",
    "https://example.invalid/",
    "http://example.invalid/a/b.html",
    "https://example.invalid/a?x=1",
    "https://example.invalid/a%20b",
    "https://example.invalid/~u/x-y.z",
    "https://example.invalid:8080/p",
    "https://user@example.invalid/p",
    "https://example.invalid/a;b",
    "https://example.invalid/--x--",
    "https://example.invalid/caf%C3%A9",
]
#: Each block a link can stand in; `{a}` is the link, `{t}` its text.
BLOCKS = [
    "<p>alpha {a} omega</p>",
    '<p>alpha<a href="{t}"> {t} </a>omega</p>',
    "<h2>alpha {a} omega</h2>",
    "<ul><li>alpha {a} omega</li><li>two</li></ul>",
    "<ol><li><p>alpha {a}</p><ul><li>{a} omega</li></ul></li></ol>",
    "<table><tr><th>h</th></tr><tr><td>alpha {a} omega</td></tr></table>",
    "<blockquote><p>alpha {a} omega</p></blockquote>",
    "<p><strong>alpha {a} omega</strong></p>",
    "<dl><dt>alpha</dt><dd>{a} omega</dd></dl>",
]


class _Shortcut(_TextMarkdownify):
    """The bridge's writer as it was: MarkItDown's `<a>`, its edges kept
    (docs/09 P49), and markdownify's `autolinks` on, which wrote `<target>`."""

    convert_a = _keeping_edge_spaces(_CustomMarkdownify.convert_a)

    def __init__(self, *, list_seams: _ListSeams, **options: Any) -> None:
        _CustomMarkdownify.__init__(self, autolinks=True, **options)
        self._list_seams = list_seams


def _markdown(document: str, writer: type[_TextMarkdownify] = _TextMarkdownify) -> str:
    seams = _ListSeams()
    written: str = writer(list_seams=seams).convert_soup(
        BeautifulSoup(document, "html.parser")
    )
    return seams.resolve(written)


def _render(source: str) -> str:
    return sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )


def _page(document: str, writer: type[_TextMarkdownify] = _TextMarkdownify) -> str:
    """The page's HTML for `document`, through the bridge's HTML reader."""
    if writer is _TextMarkdownify:
        converted = _TextHtmlConverter().convert(
            io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
        )
        return _render(converted.markdown)
    return _render(_markdown(document, writer))


def _reads(page: str) -> str:
    """What a reader reads on `page`: its text, each run of whitespace one
    space.  A tag nh3 escaped would read as its characters, so it counts."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text).strip()


def _link(target: str) -> str:
    return f'<a href="{target}">{target}</a>'


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("label", list(BARE_LINK_TARGETS))
def test_a_link_whose_text_is_its_target_is_written_as_a_link(label: str) -> None:
    target = BARE_LINK_TARGETS[label]
    document = f"<p>alpha {_link(target)} omega</p>"
    assert _markdown(document).strip() == f"alpha [{target}]({target}) omega"
    page = _page(document)
    assert _reads(page) == f"alpha {target} omega"
    if label in NO_HREF:
        assert "<a " in page and "href" not in page, page
    else:
        assert f'href="{target}"' in page, page


@pytest.mark.parametrize("label", list(BARE_LINK_TARGETS))
def test_the_shortcut_lost_every_target_but_an_address(label: str) -> None:
    """What `<target>` did, so this file fails where the bug is back."""
    target = BARE_LINK_TARGETS[label]
    document = f"<p>alpha {_link(target)} omega</p>"
    assert _markdown(document, _Shortcut).strip() == f"alpha <{target}> omega"
    reads = _reads(_page(document, _Shortcut))
    if label in AUTOLINKED:
        assert reads == f"alpha {target} omega"
    else:
        assert reads != f"alpha {target} omega", reads


def test_a_tag_the_target_names_took_the_rest_of_the_document() -> None:
    later = "<p>later paragraph</p><h2>later heading</h2>"
    # nh3 escapes what follows `<title>` and `<textarea>` as their text.
    heading = '<h2 id="later-heading">later heading</h2>'
    for target, before in (
        ("script", "alpha"),
        ("style", "alpha"),
        ("title", f"alpha omega</p> <p>later paragraph</p> {heading}"),
        ("textarea", f"alpha omega</p> <p>later paragraph</p> {heading}"),
    ):
        document = f"<p>alpha {_link(target)} omega</p>{later}"
        assert _reads(_page(document, _Shortcut)) == before, target
        assert _reads(_page(document)) == (
            f"alpha {target} omega later paragraph later heading"
        ), target
    # `<em>` and `<strong>` were kept, and ran on to the end of the document.
    for target in ("em", "strong"):
        document = f"<p>alpha {_link(target)} omega</p>{later}"
        assert f"<{target}>\n<p>later paragraph" in _page(document, _Shortcut)
        assert f"<{target}>" not in _page(document)


def test_options_cannot_bring_the_shortcut_back() -> None:
    seams = _ListSeams()
    written = _TextMarkdownify(list_seams=seams, autolinks=True).convert_soup(
        BeautifulSoup(f"<p>{_link('em')}</p>", "html.parser")
    )
    assert written.strip() == "[em](em)"


@pytest.mark.parametrize("block", BLOCKS)
def test_an_address_renders_the_page_it_rendered(block: str) -> None:
    """For http and https the shortcut was right, and the page it made is the
    page `[address](address)` makes."""
    for address in ADDRESSES:
        document = block.format(a=_link(address), t=address)
        assert f"<{address}>" in _markdown(document, _Shortcut), document
        assert _page(document) == _page(document, _Shortcut), document


@pytest.mark.parametrize("block", BLOCKS)
def test_every_target_reads_in_every_block(block: str) -> None:
    """The page reads as the same block with each link's text in its place."""
    for target in BARE_LINK_TARGETS.values():
        document = block.format(a=_link(target), t=target)
        page = _page(document)
        unlinked = re.sub(r"<a [^>]*>(.*?)</a>", r"\1", document)
        assert "<a " not in unlinked, unlinked
        assert _reads(page) == _reads(_page(unlinked)), (document, page)


def test_a_link_in_code_is_its_text() -> None:
    for tag in ("code", "kbd", "samp"):
        for target, text in (
            ("quillmoss.pdf", "quillmoss.pdf"),
            ("https://example.invalid/x", "https://example.invalid/x"),
            ("chapter.xhtml", "see this"),
        ):
            document = f'<p>alpha <{tag}>beta <a href="{target}">{text}</a></{tag}></p>'
            assert _page(document) == f"<p>alpha <code>beta {text}</code></p>", tag
    # MarkItDown wrote the shortcut's tag, or the link's Markdown, as code.
    assert _page('<p><code><a href="em">em</a></code></p>', _Shortcut) == (
        "<p><code>&lt;em&gt;</code></p>"
    )
    assert _page('<p><code><a href="x.html">see</a></code></p>', _Shortcut) == (
        "<p><code>[see](x.html)</code></p>"
    )


def test_a_link_in_code_and_pre_keeps_its_edges() -> None:
    """docs/09 P49: the spaces at a link's edges keep its words apart in code,
    and in `pre` they are written as they stand."""
    assert _page('<p><code>alpha<a href="em"> beta\t</a>omega</code></p>') == (
        "<p><code>alpha beta omega</code></p>"
    )
    assert "alpha  beta\nomega" in _markdown(
        '<pre>alpha<a href="em">  beta\n</a>omega</pre>'
    )


def test_a_link_with_spaces_at_its_edges_keeps_them() -> None:
    """MarkItDown's `<target>` lost `chomp`'s spaces, and the target's text."""
    document = '<p>alpha<a href="quillmoss.pdf"> quillmoss.pdf </a>omega</p>'
    assert _markdown(document).strip() == "alpha [quillmoss.pdf](quillmoss.pdf) omega"
    assert _reads(_page(document)) == "alpha quillmoss.pdf omega"


def test_a_mailto_link_keeps_its_text_and_loses_its_target() -> None:
    """MarkItDown writes a link outside http, https and `file` as its text,
    before the shortcut is reached, so this change leaves it as it was."""
    for text in ("mailto:quill@example.invalid", "quill@example.invalid"):
        document = (
            f'<p>alpha <a href="mailto:quill@example.invalid">{text}</a> omega</p>'
        )
        assert (
            _page(document)
            == _page(document, _Shortcut)
            == (f"<p>alpha {text} omega</p>")
        )


# --------------------------------------------------------------------------- #
# The fixtures, as the page WebShot captures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["bare_link_report.docx", "bare_link_book.epub", "bare_link_archive.zip"],
)
def test_each_fixture_block_reads_on_the_page(name: str) -> None:
    """Through the whole of the adapter, MarkItDown's own clean-up included."""
    if name.endswith(".docx") and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    body = page.split("<main>", 1)[1]
    text = _reads(body)
    lines = bare_link_lines(name)
    assert len(lines) > 20, "the fixture lost its blocks"
    missing = [line.text for line in lines if line.text not in text]
    assert not missing, missing
    # nh3 marks every link it keeps `rel="noopener noreferrer"`.
    for line in lines:
        if (
            line.where in ("para", "heading", "item", "cell")
            and line.label not in NO_HREF
        ):
            link = (
                f'<a href="{line.target}" rel="noopener noreferrer">{line.target}</a>'
            )
            assert link in body, line.words
