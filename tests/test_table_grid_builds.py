"""A table's grid is built a fixed number of times, however long it is (docs/09 P41).

docling-core's `TableData.grid` builds the whole grid on every read, and its
DocTags writer read it once per cell, so a table of n cells built n² cells: a
1,000-row, 2-column CSV built four million, and a capture's time grew with the
square of its rows. The bridge now hands that writer a grid built once.

Wall time would make a flaky test of it, so this one counts grid builds:
`spanning_table.html` and the same table with its last body ten times as long
must build the same number. The fixture holds every shape a snapshot's table
can take, so the second test can hold the DocTags to docling-core's own bytes.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from webshot.extract import docling_bridge

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spanning_table.html"


def _lengthened(html: str, times: int) -> str:
    """`html` with the rows of its last `<tbody>` repeated `times` times."""
    head, tag, rest = html.rpartition("<tbody>")
    rows, end, tail = rest.partition("</tbody>")
    return head + tag + rows * times + end + tail


def _grid_builds(monkeypatch: pytest.MonkeyPatch, html: str) -> int:
    # The one place a test reaches past the bridge: the count is of
    # docling-core's own property, wherever in the pipeline it is read.
    from docling_core.types.doc.document import TableData

    build = TableData.grid.fget
    builds = 0

    def counted(self: TableData) -> object:
        nonlocal builds
        builds += 1
        return build(self)

    with monkeypatch.context() as patch:
        patch.setattr(TableData, "grid", property(counted))
        docling_bridge.extract(html, [])
    return builds


def test_a_longer_table_builds_its_grid_no_more_often(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = FIXTURE.read_text("utf-8")
    longer = _lengthened(html, 10)
    assert longer.count("<tr>") == html.count("<tr>") + 9 * 3

    builds = _grid_builds(monkeypatch, html)
    # Before the fix: 8 x 6 + 2 x 2 cells read through DocTags alone.
    assert 0 < builds < 8 * 6 + 2 * 2
    assert _grid_builds(monkeypatch, longer) == builds


def test_doctags_are_docling_cores_own_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    html = FIXTURE.read_text("utf-8")
    built_once = docling_bridge.extract(html, [])
    monkeypatch.setattr(
        docling_bridge, "_grids_built_once", lambda tables: contextlib.nullcontext()
    )
    upstream = docling_bridge.extract(html, [])

    assert built_once.doctags == upstream.doctags
    assert built_once.document_json == upstream.document_json
    assert built_once.markdown == upstream.markdown
    assert built_once.tables == upstream.tables
    # Every token a snapshot's table cells can be written as, so the
    # comparison covers each path through docling-core's OTSL writer but
    # `srow`, which needs a class attribute the sanitizer removes.
    for token in ("ched", "rhed", "fcel", "ecel", "lcel", "ucel", "xcel", "nl"):
        assert f"<{token}>" in built_once.doctags, token
    assert built_once.doctags.count("<otsl>") == 2  # the nested table too


def test_each_table_gets_its_own_data_back() -> None:
    from docling_core.types.doc.document import DoclingDocument, TableCell, TableData

    document = DoclingDocument(name="t")
    cell = TableCell(
        text="a",
        start_row_offset_idx=0,
        end_row_offset_idx=1,
        start_col_offset_idx=0,
        end_col_offset_idx=1,
    )
    table = document.add_table(
        data=TableData(num_rows=2, num_cols=1, table_cells=[cell])
    )
    original = table.data

    with pytest.raises(RuntimeError), docling_bridge._grids_built_once([table]):
        assert table.data is not original
        assert table.data.table_cells is original.table_cells
        assert table.data.grid is table.data.grid
        assert [[c.text for c in row] for row in table.data.grid] == [["a"], [""]]
        raise RuntimeError
    assert table.data is original
