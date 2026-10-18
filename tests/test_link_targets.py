"""A link's target reaches content.md as the page writes it (docs/09 P92).

docling's HTML backend keeps a target that is not an absolute URL as a
`Path`, and docling-core writes `Path.as_posix()`. A path normalizes: `docs/`
lost its slash and `./notes.txt` its `./` on every platform, and on Windows
`//example.invalid/x`, a URL naming a host, was read as a network share and
written `//example.invalid/x/`. The Windows run found the last, in the
`bare_link_*` fixtures' captures; the rest are measured here on any platform.
"""

from __future__ import annotations

import copy
import json
import pickle
from pathlib import Path

import pytest

from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import ExtractionResult, extract

#: Targets a `Path` rewrote, with the platform it rewrote each on, and two it
#: never touched. A URL docling validates as one is held as one, and is here
#: to show it still is.
TARGETS = {
    "network-path": "//example.invalid/fenwold",  # Windows: `//…/fenwold/`
    "trailing-slash": "docs/",  # everywhere: `docs`
    "dot-segment": "./notes.txt",  # everywhere: `notes.txt`
    "doubled-slash": "guide//intro.html",  # everywhere: `guide/intro.html`
    "three-slashes": "///sorvane",  # POSIX: `/sorvane`
    "parent-folder": "../up/",  # everywhere: `../up`
    "plain": "chapter.xhtml",
    "query": "?glimt=1",
    "address": "https://example.invalid/marrowby/",
}


def _extracted(links: dict[str, str]) -> ExtractionResult:
    body = "".join(
        f'<p>before{n} <a href="{target}">{label}</a> after{n}</p>'
        for n, (label, target) in enumerate(links.items())
    )
    return extract(f"<html><body><h1>Links</h1>{body}</body></html>", [])


def _held(result: ExtractionResult) -> list[str]:
    """Each text's link target as content.json records it."""
    texts = json.loads(result.document_json)["texts"]
    return [text["hyperlink"] for text in texts if text.get("hyperlink")]


def test_each_target_is_written_as_the_page_writes_it() -> None:
    result = _extracted(TARGETS)
    lines = [line for line in result.markdown.split("\n") if line.startswith("before")]
    assert lines == [
        f"before{n} [{label}]({target}) after{n}"
        for n, (label, target) in enumerate(TARGETS.items())
    ]
    assert _held(result) == list(TARGETS.values())
    assert not any("P92" in warning for warning in result.warnings)


def test_two_links_a_path_would_make_one_stay_two() -> None:
    """`docs/` and `docs` are one path and two URLs, so the backend's merging
    of runs with one target must not make them one link."""
    result = extract(
        '<html><body><h1>L</h1><p><a href="docs/">first</a><a href="docs">'
        "second</a></p></body></html>",
        [],
    )
    assert "[first](docs/)" in result.markdown
    assert "[second](docs)" in result.markdown


def test_a_target_is_the_text_it_was_made_from_wherever_it_goes() -> None:
    target = docling_bridge._LinkTarget("//example.invalid/fenwold")
    for copied in (copy.copy(target), copy.deepcopy(target)):
        assert copied.as_posix() == str(copied) == "//example.invalid/fenwold"
    assert pickle.loads(pickle.dumps(target)) == target
    assert target != docling_bridge._LinkTarget("//example.invalid/fenwold/")
    assert isinstance(target, Path)


def test_the_backend_still_has_what_the_bridge_extends() -> None:
    from docling.backend import html_backend

    missing = [
        hook
        for hook in docling_bridge._LINK_HOOKS
        if not hasattr(html_backend.HTMLDocumentBackend, hook)
    ]
    assert not missing


def test_without_them_targets_are_docling_paths_and_the_record_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence must not read as success: with a method gone, as in a docling
    that renamed one, the capture completes on docling's paths and a warning
    says what that costs."""
    monkeypatch.setattr(
        docling_bridge,
        "_LINK_HOOKS",
        (*docling_bridge._LINK_HOOKS, "_a_method_docling_renamed"),
    )
    result = _extracted({"trailing-slash": "docs/"})
    assert "before0 [trailing-slash](docs) after0" in result.markdown.split("\n")
    assert any("docs/09 P92" in warning for warning in result.warnings)
