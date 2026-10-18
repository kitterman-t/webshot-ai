"""An HTML document reads the same whichever line endings it was saved with
(docs/09 P92).

A browser turns each `\\r\\n`, and each `\\r` alone, into `\\n` before it
parses a page. The bridge's reader, `html.parser`, kept every `\\r`, so the
same document saved on Windows gave other code than saved on macOS: a
`<pre>`'s blank first and last lines survived markdownify's trim, and a `\\r`
left before a `\\r\\n` was a line break in a list item's code and none at the
top level. An EPUB's chapters and a zip's HTML members are read this way, so a
book written on Windows reached the page with lines no other copy of it had.
The Windows run found it: every test that wrote its page with `write_text`
there saved it with `\\r\\n`.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from webshot.acquire.markitdown_bridge import to_markdown

DOCUMENT = """<html>
<head><title>Line endings</title></head>
<body>
<p>A paragraph
over two lines.</p>
<pre>

   lead
mid

</pre>
<ul>
<li>Item
<pre>
one

two
</pre>
</li>
<li>After</li>
</ul>
<blockquote>
<pre>
quoted
code
</pre>
</blockquote>
<dl>
<dt>Term</dt>
<dd><pre>
defined
code
</pre></dd>
</dl>
<p>Text with <code>inline
code</code> in it and a <a href="x" title="two
lines">link</a>.</p>
</body>
</html>
"""

ENDINGS = {"windows": "\r\n", "classic-mac": "\r"}


def _beside(tmp_path: Path, ending: str, name: str) -> Path:
    """`name` in a folder of its own for `ending`: a zip's name is on its page."""
    folder = tmp_path / ending.encode().hex()
    folder.mkdir(exist_ok=True)
    return folder / name


def _html(tmp_path: Path, ending: str) -> str:
    source = _beside(tmp_path, ending, "page.html")
    source.write_bytes(DOCUMENT.replace("\n", ending).encode())
    return to_markdown(source, suffix=".html").markdown


def _zipped(tmp_path: Path, ending: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pages/page.html", DOCUMENT.replace("\n", ending).encode())
    source = _beside(tmp_path, ending, "pages.zip")
    source.write_bytes(buffer.getvalue())
    return to_markdown(source, suffix=".zip").markdown


@pytest.mark.parametrize("ending", ENDINGS.values(), ids=ENDINGS.keys())
@pytest.mark.parametrize("read", [_html, _zipped], ids=["html", "zip-member"])
def test_a_document_reads_the_same_whatever_its_line_endings(
    tmp_path: Path, read: object, ending: str
) -> None:
    """Measured before, with `\\r\\n`: the top-level `<pre>` kept a blank line
    at each end that the `\\n` copy did not."""
    assert callable(read)
    unix = read(tmp_path, "\n")
    other = read(tmp_path, ending)
    assert "\r" not in other
    assert other == unix


def test_a_carriage_return_before_a_line_ending_is_a_line_of_its_own(
    tmp_path: Path,
) -> None:
    """`a\\rb\\r\\r\\nc` is `a`, `b`, a blank line and `c` to a browser, wherever
    the `<pre>` is. Before, the top level's code lost the blank line, and an
    item's and a quotation's kept it."""
    code = "a\rb\r\r\nc"
    source = tmp_path / "page.html"
    source.write_bytes(
        (
            f"<html><body><pre>{code}</pre><ul><li>I<pre>{code}</pre></li></ul>"
            f"<blockquote><pre>{code}</pre></blockquote></body></html>"
        ).encode()
    )
    markdown = to_markdown(source, suffix=".html").markdown
    assert "\r" not in markdown
    assert "```\na\nb\n\nc\n```" in markdown
    assert "        a\n        b\n\n        c" in markdown
    assert ">     a\n>     b\n>\n>     c" in markdown
