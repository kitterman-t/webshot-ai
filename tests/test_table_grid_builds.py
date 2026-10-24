"""A table's grid is built a fixed number of times, however long it is (docs/09 P41).

docling-core's `TableData.grid` builds the whole grid on every read, and its
DocTags writer read it once per cell, so a table of n cells built n² cells: a
1,000-row, 2-column CSV built four million, and a capture's time grew with the
square of its rows. The bridge handed that writer a grid built once until
docling-core 2.103.1 began reading it once itself (docling-core #899); the
bridge's copy went with the bump to 2.104.0 (docs/09 P95-3).

Wall time would make a flaky test of it, so this one counts grid builds:
`spanning_table.html` and the same table with its last body ten times as long
must build the same number, whichever side keeps the count fixed.
"""

from __future__ import annotations

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
