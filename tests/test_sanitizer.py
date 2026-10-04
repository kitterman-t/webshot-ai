"""Property tests for both nh3 sanitizers (spec §6.5; docs/05 tasks 2.1, 3.3).

WebShot sanitizes in two places, with two allowlists: the captured DOM
snapshot (`capture/snapshot.py`, which keeps form and media records) and
rendered local documents (`acquire/adapters.py`, which keeps Markdown's own
output).  The *properties* are the same for both, so the generators run
against both: whatever HTML goes in, no script element, no event-handler
attribute, and no URL outside the allowed schemes comes out — and sanitizing
twice changes nothing, so no second pass can ever "reveal" content the first
pass constructed.

Fixed adversarial cases pin the behaviors the property generators are least
likely to stumble into, per allowlist, since that is where the two differ.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from webshot.acquire.adapters import sanitize_markdown_html
from webshot.capture.snapshot import (
    PASSWORD_REDACTION,
    sanitize_body_html,
    standalone_document,
)

#: Every allowlist the package exposes.  A new sanitizer that is not in this
#: tuple is a sanitizer with no property coverage.
SANITIZERS = (sanitize_body_html, sanitize_markdown_html)

SCRIPT_RE = re.compile(r"<\s*script", re.IGNORECASE)
HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
BAD_SCHEME_RE = re.compile(
    r"""(?:href|src|action|poster)\s*=\s*["']?\s*(?:javascript|vbscript|file|ftp|blob):""",
    re.IGNORECASE,
)

_text = st.text(min_size=0, max_size=80)
_handler_names = st.from_regex(r"on[a-z]{1,12}", fullmatch=True)
_schemes = st.sampled_from(
    ["javascript", "vbscript", "file", "ftp", "blob", "data", "http", "https", "mailto"]
)
Sanitizer = Callable[[str], str]

_tags = st.sampled_from(
    ["p", "div", "a", "img", "video", "input", "table", "span", "script", "iframe"]
)


@st.composite
def hostile_html(draw: st.DrawFn) -> str:
    """A soup of markup with hostile bits woven through arbitrary text."""
    pieces = []
    for _ in range(draw(st.integers(0, 6))):
        tag = draw(_tags)
        handler = draw(_handler_names)
        scheme = draw(_schemes)
        payload = draw(_text)
        pieces.append(
            draw(
                st.sampled_from(
                    [
                        f'<{tag} {handler}="steal()">{payload}</{tag}>',
                        f'<a href="{scheme}:{payload}">{payload}</a>',
                        f'<img src="{scheme}:{payload}" alt="{payload}">',
                        f"<script>{payload}</script>",
                        f"<{tag}>{payload}</{tag}>",
                        payload,
                    ]
                )
            )
        )
    return "".join(pieces)


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@given(hostile_html())
@settings(max_examples=200, deadline=None)
def test_no_script_survives(sanitize: Sanitizer, html: str) -> None:
    assert not SCRIPT_RE.search(sanitize(html))


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@given(hostile_html())
@settings(max_examples=200, deadline=None)
def test_no_event_handler_survives(sanitize: Sanitizer, html: str) -> None:
    assert not HANDLER_RE.search(sanitize(html))


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@given(hostile_html())
@settings(max_examples=200, deadline=None)
def test_no_disallowed_url_scheme_survives(sanitize: Sanitizer, html: str) -> None:
    assert not BAD_SCHEME_RE.search(sanitize(html))


@pytest.mark.parametrize("sanitize", SANITIZERS, ids=lambda fn: fn.__name__)
@given(hostile_html())
@settings(max_examples=100, deadline=None)
def test_sanitization_is_idempotent(sanitize: Sanitizer, html: str) -> None:
    once = sanitize(html)
    assert sanitize(once) == once


# --------------------------------------------------------------------------- #
# Pinned adversarial cases — the snapshot allowlist
# --------------------------------------------------------------------------- #


def test_asset_references_and_markers_survive() -> None:
    cleaned = sanitize_body_html(
        '<img src="assets/asset-001.png" alt="chart" data-webshot-asset-id="asset-001">'
    )
    assert 'src="assets/asset-001.png"' in cleaned
    assert 'data-webshot-asset-id="asset-001"' in cleaned


def test_form_fields_survive_with_redacted_password() -> None:
    cleaned = sanitize_body_html(
        f'<form><input type="password" name="pw" value="{PASSWORD_REDACTION}">'
        '<input type="checkbox" name="opt" checked>'
        '<select name="tz"><option selected>UTC</option></select>'
        '<textarea name="note">hello</textarea></form>'
    )
    for fragment in (
        f'value="{PASSWORD_REDACTION}"',
        'type="checkbox"',
        "<select",
        "<textarea",
        "<option selected",
    ):
        assert fragment in cleaned


def test_media_and_tracks_survive_but_file_urls_do_not() -> None:
    cleaned = sanitize_body_html(
        '<video src="file:///secret.mp4" width="320" height="180" preload="none">'
        '<track kind="captions" srclang="en" label="English" src="captions.vtt">'
        "</video>"
    )
    assert "<video" in cleaned and "<track" in cleaned
    assert 'kind="captions"' in cleaned and 'srclang="en"' in cleaned
    assert "file:" not in cleaned  # spec §6.5 scheme allowlist
    assert 'src="captions.vtt"' in cleaned  # relative refs are kept


def test_definition_lists_and_structure_survive() -> None:
    cleaned = sanitize_body_html(
        "<dl><dt>Term</dt><dd>Meaning</dd></dl>"
        '<table><tr><td colspan="2">cell</td></tr></table>'
        "<blockquote>quote</blockquote><pre>code</pre>"
    )
    for fragment in ("<dl>", "<dt>", "<dd>", 'colspan="2"', "<blockquote>", "<pre>"):
        assert fragment in cleaned


def test_svg_subtrees_are_removed_entirely_not_leaked_as_text() -> None:
    assert sanitize_body_html("<svg><text>leaky svg text</text></svg>").strip() == ""


def test_data_urls_are_allowed_for_images() -> None:
    cleaned = sanitize_body_html('<img src="data:image/png;base64,AAAA">')
    assert "data:image/png;base64,AAAA" in cleaned


def test_event_handlers_via_generic_prefix_do_not_sneak_through() -> None:
    cleaned = sanitize_body_html('<p data-webshot-onload="x" onclick="y()">t</p>')
    assert "onclick" not in cleaned
    # the data-webshot- prefix is ours; even odd names under it are inert data
    assert "steal" not in cleaned


def test_standalone_document_escapes_title() -> None:
    document = standalone_document('<script>"&', "en", "<p>x</p>")
    assert "<title>&lt;script&gt;&quot;&amp;</title>" in document
    assert '<html lang="en">' in document


# --------------------------------------------------------------------------- #
# Pinned adversarial cases — the local-document allowlist (docs/05 task 3.3)
# --------------------------------------------------------------------------- #


def test_markdown_structure_survives() -> None:
    cleaned = sanitize_markdown_html(
        "<h2>Findings</h2><p><strong>bold</strong> and <em>italic</em></p>"
        '<table><thead><tr><th scope="col">Metric</th></tr></thead>'
        "<tbody><tr><td>42</td></tr></tbody></table>"
        "<pre><code>fenced</code></pre><blockquote>quoted</blockquote>"
        "<dl><dt>Term</dt><dd>Meaning</dd></dl><hr>"
    )
    for fragment in (
        "<h2>",
        "<strong>",
        "<em>",
        '<th scope="col">',
        "<td>42</td>",
        "<pre>",
        "<blockquote>",
        "<dt>",
        "<hr>",
    ):
        assert fragment in cleaned


def test_relative_asset_references_survive_for_the_base_href_to_resolve() -> None:
    cleaned = sanitize_markdown_html(
        '<p><img src="chart.png" alt="Chart"> <a href="notes.md">notes</a></p>'
    )
    assert 'src="chart.png"' in cleaned
    assert 'href="notes.md"' in cleaned


def test_file_urls_no_longer_survive() -> None:
    """One scheme narrower than v2's bleach call, to match spec §6.5."""
    cleaned = sanitize_markdown_html('<a href="file:///etc/passwd">secrets</a>')
    assert "file:" not in cleaned
    assert "secrets" in cleaned  # the text is content; only the URL is refused


def test_embedding_tags_do_not_survive_a_local_document() -> None:
    cleaned = sanitize_markdown_html(
        '<iframe src="https://example.com"></iframe>'
        '<object data="x.swf"></object><embed src="x.swf">'
        '<form><input type="password" value="hunter2"></form>'
    )
    for fragment in ("<iframe", "<object", "<embed", "<form", "<input"):
        assert fragment not in cleaned


def test_style_and_script_contents_do_not_leak_as_text() -> None:
    cleaned = sanitize_markdown_html(
        "<script>window.evil = true</script><style>body{display:none}</style>"
    )
    assert "window.evil" not in cleaned
    assert "display:none" not in cleaned
