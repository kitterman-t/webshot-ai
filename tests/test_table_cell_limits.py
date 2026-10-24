"""A page whose tables are past docling-core's cell limits ends the capture
with the bridge's error, naming the limit (docs/09 P95-2).

docling-core 2.103.1 bounds a table's grid at `max_table_cells` (1,000,000)
and a document's at `max_total_table_cells` (5,000,000), against tables whose
grid would not fit in memory. A table past the first failed inside the
bridge's parse, as it should; a page past the second was refused only when the
chunker's serializer validated the document, and that ended the capture in a
raw pydantic ValidationError. These tests lower the limits, so a small page
crosses them.
"""

from __future__ import annotations

import pytest

from webshot.errors import BundleBuildError
from webshot.extract import docling_bridge

ROW = "<tr>" + "<td>x</td>" * 3 + "</tr>"
#: A 3 x 3 table and a 2 x 3 one: 9 and 6 cells, 15 in all.
PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'></head><body><main>"
    f"<h1>Tables</h1><p>Before.</p><table>{ROW * 3}</table>"
    f"<p>After.</p><table>{ROW * 2}</table></main></body></html>"
)


def _limit(monkeypatch: pytest.MonkeyPatch, name: str, value: int) -> None:
    # The one place a test reaches past the bridge: the limits are
    # docling-core's own settings.
    from docling_core.utils.settings import settings

    monkeypatch.setattr(settings, name, value)


def test_a_table_past_the_cell_limit_fails_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit(monkeypatch, "max_table_cells", 8)
    with pytest.raises(BundleBuildError, match=r"3x3 cells exceeds the limit of 8"):
        docling_bridge.extract(PAGE, [])


def test_tables_past_the_total_limit_fail_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit(monkeypatch, "max_total_table_cells", 14)
    with pytest.raises(
        BundleBuildError,
        match=r"declare 15 cells in total, past docling-core's limit of 14",
    ):
        docling_bridge.extract(PAGE, [])


def test_tables_at_the_limits_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit(monkeypatch, "max_table_cells", 9)
    _limit(monkeypatch, "max_total_table_cells", 15)
    result = docling_bridge.extract(PAGE, [])
    assert len(result.tables) == 2
