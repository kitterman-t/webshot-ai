"""Local-file adapters: routing, guards, and what each format round-trips to.

Phase 3 moved the reading of every non-Markdown format to MarkItDown, so most
of what is worth pinning here is the seam: that WebShot still decides what it
accepts, that its guards run *before* any converter opens the file, and that
Markdown keeps resolving its own relative assets — the one behavior the swap
could have taken away silently.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from webshot.acquire.adapters import (
    MAX_ARCHIVE_MEMBERS,
    guard_archive,
    materialize_source,
)
from webshot.errors import UsageError, WebShotError
from webshot.netpolicy import file_url_path

XXE_DOCUMENT = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE records [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    "<records><record>&xxe;</record></records>\n"
)
ENTITY_BOMB = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE bomb [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>\n'
    "<bomb>&b;</bomb>\n"
)


def materialize_text(tmp_path: Path, suffix: str, content: str) -> tuple[str, str]:
    source = tmp_path / f"sample{suffix}"
    source.write_text(content, encoding="utf-8")
    return materialize_file(source)


def materialize_file(source: Path) -> tuple[str, str]:
    materialized = materialize_source(source.as_uri())
    try:
        # Not `removeprefix("file://")`: on Windows that leaves `/C:/...`
        # (docs/09 P10-24).
        html = file_url_path(materialized.browser_url).read_text(encoding="utf-8")
        return materialized.kind, html
    finally:
        materialized.cleanup()


# --------------------------------------------------------------------------- #
# Markdown: the one format that does NOT go through MarkItDown
# --------------------------------------------------------------------------- #


def test_markdown_becomes_semantic_html(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path,
        ".md",
        "# Report\n\n## Findings\n\n|Metric|Value|\n|---|---|\n|Revenue|42|",
    )
    assert kind == "markdown"
    assert "<h1" in rendered
    assert "<table>" in rendered
    assert "Revenue" in rendered


def test_markdown_is_sanitized(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path,
        ".md",
        "# Safe report\n\n<script>window.evil = true</script>\n\n"
        '<a href="javascript:steal()" onclick="steal()">click</a>\n',
    )
    assert kind == "markdown"
    assert "<script" not in rendered
    assert "window.evil" not in rendered
    assert "javascript:" not in rendered
    assert "onclick" not in rendered


def test_markdown_relative_links_and_images_resolve_against_the_source_directory(
    tmp_path: Path,
) -> None:
    """docs/05 task 3.1's named regression: this is why Markdown skips MarkItDown.

    A converted Markdown document is rendered from a temporary directory, so a
    relative `chart.png` would resolve to nothing.  What makes it resolve is
    the `<base href>` pointing at the *source* file's own directory, plus the
    references staying relative through sanitization.
    """
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (documents / "appendix.md").write_text("# Appendix", encoding="utf-8")
    source = documents / "report.md"
    source.write_text(
        "# Report\n\n![Chart](chart.png)\n\nSee the [appendix](appendix.md).\n"
        "Also [the site](https://example.com/docs).\n",
        encoding="utf-8",
    )

    kind, rendered = materialize_file(source)

    assert kind == "markdown"
    assert f'<base href="{documents.resolve().as_uri()}/">' in rendered
    assert 'src="chart.png"' in rendered
    assert 'href="appendix.md"' in rendered
    assert 'href="https://example.com/docs"' in rendered


def test_markdown_first_heading_becomes_the_document_title(tmp_path: Path) -> None:
    _, rendered = materialize_text(tmp_path, ".md", "# Quarterly review\n\nBody.")
    assert "<title>Quarterly review</title>" in rendered
    # ...and the wrapper does not add a second one on top of the page's own.
    assert rendered.count("<h1") == 1


# --------------------------------------------------------------------------- #
# The MarkItDown-routed formats
# --------------------------------------------------------------------------- #


def test_csv_becomes_a_table(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path, ".csv", "name,value\nalpha,10\nbeta,20\n"
    )
    assert kind == "csv"
    # `scope` is not something Markdown's table extension emits; WebShot puts
    # it back, because it is what makes the header cells headers to a screen
    # reader and to the PDF's structure tree.
    assert '<th scope="col">name</th>' in rendered
    assert "<td>alpha</td>" in rendered


def test_tsv_becomes_a_table_too(tmp_path: Path) -> None:
    """MarkItDown's CSV converter is comma-only; the bridge re-delimits."""
    kind, rendered = materialize_text(
        tmp_path, ".tsv", "name\tvalue\nalpha\t10\nbeta\t20\n"
    )
    assert kind == "tsv"
    assert '<th scope="col">name</th>' in rendered
    assert "<td>alpha</td>" in rendered
    assert "<td>10</td>" in rendered


def test_table_cells_survive_the_markdown_delimiter_and_line_breaks(
    tmp_path: Path,
) -> None:
    """docs/09 P3-1: a delimiter or a line break in a cell must not split its row."""
    kind, rendered = materialize_text(
        tmp_path,
        ".csv",
        'name,note\nalpha,"a | b"\nbeta,"line one\nline two"\n',
    )
    assert kind == "csv"
    assert "<td>a | b</td>" in rendered
    assert "<td>line one<br>line two</td>" in rendered
    # Two data rows, not four: neither cell split its row.
    assert rendered.count("<tr>") == 3


def test_a_pipe_is_escaped_once_whatever_stands_in_front_of_it(
    tmp_path: Path,
) -> None:
    """docs/09 P10-23: MarkItDown 0.1.8 escapes the cell delimiter itself.

    The bridge escaped it too, so `a | b` reached the page as `a \\| b`. A
    pipe that opens the cell, and one the data already puts a backslash in
    front of, are the two places a second escaper would still show through.
    """
    kind, rendered = materialize_text(
        tmp_path,
        ".csv",
        'name,note\nalpha,"| leading"\nbeta,"back\\| slash"\n',
    )
    assert kind == "csv"
    assert "<td>| leading</td>" in rendered
    assert "<td>back\\| slash</td>" in rendered
    assert rendered.count("<tr>") == 3


def test_a_cell_cannot_smuggle_active_html_into_the_rendered_table(
    tmp_path: Path,
) -> None:
    """A spreadsheet cell is data, and the renderer must keep it that way.

    Markdown passes raw HTML through, so `<img src=...>` in a cell became a
    real element and rendering the document *fetched that URL* — a tracking
    beacon, or an internal address, chosen by whoever wrote the file. The
    sanitizer does not stop it: `img[src]` is in `MARKDOWN_POLICY` because
    Markdown may legitimately emit an image (docs/09 P8-9).
    """
    kind, rendered = materialize_text(
        tmp_path,
        ".csv",
        'name,note\nrow,"<img src=""https://tracker.invalid/beacon"">"\n',
    )
    assert kind == "csv"
    assert "tracker.invalid" not in rendered or "&lt;img" in rendered
    assert "<img" not in rendered
    assert "&lt;img src=" in rendered


def test_a_cell_cannot_restructure_the_document_with_markdown(
    tmp_path: Path,
) -> None:
    """The other grammar a cell passes through on its way to the page."""
    kind, rendered = materialize_text(
        tmp_path,
        ".csv",
        'name,note\nrow,"# Not a heading"\nlink,"[text](https://evil.invalid)"\n',
    )
    assert kind == "csv"
    # The document's own `<h1>` is the filename; the cell must not add one.
    assert "Not a heading</h" not in rendered
    assert "# Not a heading" in rendered
    # The link text is published; the destination is not made clickable.
    assert 'href="https://evil.invalid"' not in rendered


def test_a_cell_past_the_csv_field_limit_fails_as_a_webshot_error(
    tmp_path: Path,
) -> None:
    """`csv.Error` is not a ValueError, so nothing upstream would catch it.

    A base64 blob or a long free-text column in one cell is ordinary data; it
    must end the run with a message and an exit code, not a traceback.
    """
    source = tmp_path / "wide.csv"
    source.write_text('a,b\n"' + "x" * 200_000 + '",2\n', encoding="utf-8")
    with pytest.raises(WebShotError, match=r"Could not read the rows of wide\.csv"):
        materialize_source(source.as_uri())


def test_json_is_preserved_verbatim_including_non_ascii(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path, ".json", '{"city": "Montréal", "value": 7}'
    )
    assert kind == "json"
    assert "Montréal" in rendered
    assert '"value": 7' in rendered
    # Verbatim means a code block, not reflowed prose.
    assert "<pre><code>" in rendered


def test_jsonl_keeps_one_record_per_line_under_its_v2_heading(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path, ".jsonl", '{"id": 1}\n{"id": 2}\n{"id": 3}\n'
    )
    assert kind == "jsonl"
    assert "<pre><code>" in rendered
    assert rendered.count('"id"') == 3
    # v2 published this heading, so the document's outline (and the PDF
    # bookmark built from it) does not change under the reader swap. It is
    # rendered from Markdown now, so it carries the anchor id the `toc`
    # extension gives every heading on these pages.
    assert '<h2 id="records">Records</h2>' in rendered


def test_no_other_verbatim_format_gains_a_heading(tmp_path: Path) -> None:
    for suffix in (".json", ".txt", ".xml", ".yaml"):
        _, rendered = materialize_text(tmp_path, suffix, "one\ntwo\n")
        assert "<h2>" not in rendered, suffix


def test_text_is_preserved_verbatim(tmp_path: Path) -> None:
    kind, rendered = materialize_text(
        tmp_path, ".txt", "Incident notes\n\n  indented line\n"
    )
    assert kind == "txt"
    assert "  indented line" in rendered


def test_verbatim_content_containing_a_code_fence_stays_inside_one(
    tmp_path: Path,
) -> None:
    _, rendered = materialize_text(
        tmp_path, ".txt", "before\n```\nnot a fence\n```\nafter\n"
    )
    assert "not a fence" in rendered
    assert rendered.count("<pre><code>") == 1


def test_yaml_and_log_and_xml_route_to_text(tmp_path: Path) -> None:
    for suffix, kind in ((".yaml", "yaml"), (".yml", "yml"), (".log", "log")):
        actual, rendered = materialize_text(tmp_path, suffix, "service: api\n")
        assert actual == kind
        assert "service: api" in rendered
    actual, rendered = materialize_text(tmp_path, ".xml", "<r><a>text</a></r>\n")
    assert actual == "xml"
    assert "&lt;a&gt;text&lt;/a&gt;" in rendered


def test_html_is_not_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "sample.html"
    source.write_text("<h1>Original</h1>", encoding="utf-8")
    materialized = materialize_source(source.as_uri())
    assert materialized.kind == "html"
    assert materialized.browser_url == source.as_uri()
    assert materialized.temporary_directory is None


def test_image_keeps_its_wrapper_page(tmp_path: Path) -> None:
    """docs/03 §9: MarkItDown would reduce an image to its EXIF fields."""
    source = tmp_path / "chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    kind, rendered = materialize_file(source)
    assert kind == "image"
    assert f'<img src="{source.resolve().as_uri()}"' in rendered
    assert "<figcaption>chart.png</figcaption>" in rendered


def test_unsupported_local_type_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "sample.bin"
    source.write_bytes(b"binary")
    with pytest.raises(ValueError, match="Unsupported local file type"):
        materialize_source(source.as_uri())


# --------------------------------------------------------------------------- #
# Guards (spec §6.6, §6.7)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "document"), (("xxe", XXE_DOCUMENT), ("entity-bomb", ENTITY_BOMB))
)
def test_hostile_xml_is_refused_before_any_converter_reads_it(
    tmp_path: Path, name: str, document: str
) -> None:
    source = tmp_path / f"{name}.xml"
    source.write_text(document, encoding="utf-8")
    with pytest.raises(ValueError, match="entities or external references"):
        materialize_source(source.as_uri())


def test_hostile_svg_is_refused_on_the_image_path_too(tmp_path: Path) -> None:
    source = tmp_path / "chart.svg"
    source.write_text(XXE_DOCUMENT, encoding="utf-8")
    with pytest.raises(ValueError, match="entities or external references"):
        materialize_source(source.as_uri())


def test_a_plain_doctype_is_not_hostile(tmp_path: Path) -> None:
    """SVG 1.1 files routinely carry one; refusing them would be wrong."""
    source = tmp_path / "chart.svg"
    source.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        '<svg xmlns="http://www.w3.org/2000/svg"><title>ok</title></svg>\n',
        encoding="utf-8",
    )
    kind, _ = materialize_file(source)
    assert kind == "image"


def test_malformed_xml_still_renders(tmp_path: Path) -> None:
    """The defusedxml pass is a security gate, not a validity gate."""
    kind, rendered = materialize_text(tmp_path, ".xml", "<a><b></a>")
    assert kind == "xml"
    assert "&lt;a&gt;&lt;b&gt;&lt;/a&gt;" in rendered


def _archive(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_zip_slip_member_is_refused(tmp_path: Path) -> None:
    source = _archive(tmp_path / "evil.zip", {"../../escaped.txt": b"payload"})
    with pytest.raises(ValueError, match="escapes the extraction root"):
        materialize_source(source.as_uri())


def test_absolute_member_path_is_refused(tmp_path: Path) -> None:
    source = _archive(tmp_path / "absolute.zip", {"/etc/passwd": b"payload"})
    with pytest.raises(ValueError, match="escapes the extraction root"):
        materialize_source(source.as_uri())


def test_symlink_member_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "link.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3  # unix
        info.external_attr = 0o120777 << 16  # S_IFLNK | 0777
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match="symbolic link"):
        materialize_source(source.as_uri())


def test_decompression_bomb_is_refused_without_being_decompressed(
    tmp_path: Path,
) -> None:
    """101 MB of zeros compresses to a few hundred KB; the guard reads sizes."""
    source = tmp_path / "bomb.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", b"\0" * (101 * 1024 * 1024))
    assert source.stat().st_size < 5 * 1024 * 1024
    with pytest.raises(ValueError, match="safety limit"):
        materialize_source(source.as_uri())


def test_a_bomb_hidden_one_archive_deep_is_refused(tmp_path: Path) -> None:
    """The limit is on what a capture expands, not on what one directory says.

    MarkItDown converts zip members recursively, so an outer archive whose
    central directory lists a small `nested.zip` passed a guard that summed the
    *compressed* inner file — and the converter then decompressed it anyway.
    A 688-byte input reached 200 MB through one level of indirection
    (docs/09 P8-16).
    """
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", b"\0" * (101 * 1024 * 1024))
    source = _archive(tmp_path / "outer.zip", {"nested.zip": inner.getvalue()})
    assert source.stat().st_size < 5 * 1024 * 1024
    with pytest.raises(ValueError, match="safety limit"):
        materialize_source(source.as_uri())


def test_members_are_counted_across_nesting_too(tmp_path: Path) -> None:
    """One budget, shared by every level: splitting the members over two
    archives must not buy twice the allowance."""
    half = MAX_ARCHIVE_MEMBERS // 2 + 1
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        for index in range(half):
            archive.writestr(f"inner-{index}.txt", b"x")
    members: dict[str, bytes] = {"nested.zip": inner.getvalue()}
    members.update({f"outer-{index}.txt": b"x" for index in range(half)})
    source = _archive(tmp_path / "split.zip", members)
    with pytest.raises(ValueError, match="more than the"):
        materialize_source(source.as_uri())


def test_an_ordinary_nested_archive_is_still_accepted(tmp_path: Path) -> None:
    """The guard must not refuse the shape it was widened to inspect."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("notes.txt", "hello from the inner archive")
    source = _archive(
        tmp_path / "ok.zip",
        {"nested.zip": inner.getvalue(), "readme.txt": b"top level"},
    )
    guard_archive(source)


def test_a_member_that_only_looks_like_an_archive_is_ordinary_data(
    tmp_path: Path,
) -> None:
    """A `.zip` that is not one is data, and was already charged as its bytes."""
    source = _archive(tmp_path / "mislabelled.zip", {"nested.zip": b"not a zip"})
    guard_archive(source)


def test_too_many_members_is_refused(tmp_path: Path) -> None:
    source = _archive(
        tmp_path / "many.zip",
        {f"file-{index}.txt": b"x" for index in range(MAX_ARCHIVE_MEMBERS + 1)},
    )
    with pytest.raises(ValueError, match="more than the"):
        materialize_source(source.as_uri())


def test_a_corrupt_archive_is_refused_clearly(tmp_path: Path) -> None:
    source = tmp_path / "broken.zip"
    source.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(ValueError, match="not a readable archive"):
        materialize_source(source.as_uri())


OFFICE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "office"

#: Each office fixture, and where MarkItDown 0.1.x's converter for it records
#: that its package failed to import. Setting that is how the test makes the
#: real converter fail the way it does on an install without the office extra.
OFFICE_DEPENDENCY_MARKERS = {
    "sample_report.docx": (
        "markitdown.converters._docx_converter",
        "_dependency_exc_info",
    ),
    "sample_deck.pptx": (
        "markitdown.converters._pptx_converter",
        "_dependency_exc_info",
    ),
    "sample_workbook.xlsx": (
        "markitdown.converters._xlsx_converter",
        "_xlsx_dependency_exc_info",
    ),
}


@pytest.mark.parametrize("fixture", sorted(OFFICE_DEPENDENCY_MARKERS))
def test_office_formats_without_the_extra_name_webshots_own_extra(
    fixture: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advice a user gets must be installable, so it cannot be passed through.

    MarkItDown's own exception recommends `pip install 'markitdown[docx]'`;
    WebShot ships those dependencies inside `webshot[office]`. The converter is
    MarkItDown's real one, told its package is missing: 0.1.x does not let the
    `MissingDependencyException` out of `convert_stream` but wraps it in a
    `FileConversionException`, and a stub that raised the inner exception
    directly is how this test passed while users got the pip advice.
    """
    import importlib
    import sys

    from markitdown import FileConversionException

    module_name, marker = OFFICE_DEPENDENCY_MARKERS[fixture]
    module = importlib.import_module(module_name)
    assert hasattr(module, marker), (
        f"{module_name} has no {marker}; its attributes are "
        f"{sorted(name for name in vars(module) if 'dependency' in name)}"
    )
    try:
        raise ImportError("the office extra is not installed")
    except ImportError:
        monkeypatch.setattr(module, marker, sys.exc_info())

    with pytest.raises(UsageError, match=r"webshot\[office\]") as raised:
        materialize_source((OFFICE_FIXTURES / fixture).as_uri())
    # The shape the fix exists for, so a MarkItDown that stops wrapping is
    # noticed here rather than passing for the wrong reason.
    assert isinstance(raised.value.__cause__, FileConversionException)
    # From the checkout: `pip install webshot[...]` would fetch an unrelated
    # project, because WebShot is not on PyPI and that name there is not ours.
    assert "uv sync --extra office" in str(raised.value)
    assert "pip install" not in str(raised.value)


def test_an_unwrapped_missing_dependency_gets_the_same_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A MarkItDown that lets the inner exception escape is answered the same."""
    from markitdown import MissingDependencyException

    from webshot.acquire import markitdown_bridge

    def refuse(*args: object, **kwargs: object) -> object:
        raise MissingDependencyException("DocxConverter ... markitdown[docx]")

    monkeypatch.setattr(
        markitdown_bridge.MarkItDown, "convert_stream", refuse, raising=True
    )
    source = _archive(tmp_path / "report.docx", {"[Content_Types].xml": b"<a/>"})
    with pytest.raises(UsageError, match=r"webshot\[office\]") as raised:
        materialize_source(source.as_uri())
    assert "uv sync --extra office" in str(raised.value)
    assert "pip install" not in str(raised.value)


def test_a_conversion_failure_that_is_not_a_missing_package_is_reported_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a missing package earns install advice; anything else says what broke."""
    from markitdown import FileConversionException

    from webshot.acquire import markitdown_bridge

    def fail(*args: object, **kwargs: object) -> object:
        raise FileConversionException("the document is damaged")

    monkeypatch.setattr(
        markitdown_bridge.MarkItDown, "convert_stream", fail, raising=True
    )
    source = _archive(tmp_path / "report.docx", {"[Content_Types].xml": b"<a/>"})
    with pytest.raises(UsageError) as raised:
        materialize_source(source.as_uri())
    assert str(raised.value) == (
        "Could not read report.docx as .docx: the document is damaged"
    )


def test_a_base_format_missing_a_dependency_names_no_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A format the base install reads has no extra to install.

    The advice used to fall back to `webshot[all]`, an extra that does not
    exist, so a user who followed it got an error from the installer instead.
    """
    from markitdown import MissingDependencyException

    from webshot.acquire import markitdown_bridge

    def refuse(*args: object, **kwargs: object) -> object:
        raise MissingDependencyException("CsvConverter ... markitdown[all]")

    monkeypatch.setattr(
        markitdown_bridge.MarkItDown, "convert_stream", refuse, raising=True
    )
    source = tmp_path / "table.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(UsageError, match="base install") as raised:
        materialize_source(source.as_uri())
    message = str(raised.value)
    assert "uv sync" in message
    assert "webshot[" not in message
    assert "pip install" not in message
