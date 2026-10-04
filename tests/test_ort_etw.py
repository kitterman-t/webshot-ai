"""The Windows ETW measurement judges what it counted (docs/09 P10-26).

`tools/ci/ort_etw.py` runs only on Windows, weekly. What can be held here is
the part that decides: how a trace is counted, and which counts fail the run.
Above all, a trace that saw nothing must not pass. The control arm exists so
that "no session events under WebShot's default" cannot be read off a trace
session that was never able to see any.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci import ort_etw  # noqa: E402


def _seen(**names: int) -> ort_etw.Seen:
    return ort_etw.Seen(names=Counter(names))


def _as_measured() -> dict[str, ort_etw.Seen]:
    """The counts of the first Windows run (36451146827, onnxruntime 1.30.0).

    That run failed on its "default" arm, because this file then counted the
    two execution-provider registration events as session events. They are
    written while the native module loads, as `ProcessInfo` is, before the
    call can be made (docs/09 P10-26). These counts must pass.
    """
    at_import = {
        "ProcessInfo": 1,
        "RegisterEpLibraryStart": 1,
        "RegisterEpLibraryEnd": 1,
    }
    session = {
        "SessionCreationStart": 1,
        "SessionCreation": 1,
        "SessionCreationEnd": 1,
        "ModelLoadStart": 1,
        "ModelLoadEnd": 1,
        "EpDeviceUsage": 1,
        "ProviderOptions": 1,
    }
    return {
        "default": _seen(**at_import),
        "opted in": _seen(**at_import, **session),
        "trace started late": _seen(**session),
        "magika, opted in": _seen(**at_import),
    }


def test_the_predicted_counts_pass() -> None:
    assert ort_etw.verdict(_as_measured()) == []


def test_a_trace_that_saw_nothing_fails_as_measuring_nothing() -> None:
    results = {name: _seen() for name in _as_measured()}
    problems = ort_etw.verdict(results)
    assert any("measured nothing" in p for p in problems), problems


def test_a_session_event_under_the_default_fails() -> None:
    results = _as_measured()
    results["default"] = _seen(ProcessInfo=1, SessionCreation=1, RuntimePerf=2)
    problems = ort_etw.verdict(results)
    assert problems == [
        "WebShot's default let session events through: SessionCreation 1, RuntimePerf 2"
    ]


def test_the_documented_limitation_fails_when_it_stops_holding() -> None:
    results = _as_measured()
    results["trace started late"] = _seen()
    problems = ort_etw.verdict(results)
    assert len(problems) == 1 and "docs/04 §4.1" in problems[0], problems


def test_the_events_written_at_import_are_not_session_events() -> None:
    """They are written before Python runs; counting them failed a run."""
    assert not set(ort_etw.IMPORT_EVENTS) & set(ort_etw.SESSION_EVENTS)
    measured = _as_measured()["default"]
    assert measured.session == 0 and measured.at_import == 3


def test_names_are_counted_whole() -> None:
    """`SessionCreation` must not also count `SessionCreationStart`."""
    trace = (
        b"\x10\x00SessionCreationStart\x00\x01"
        b"\x10\x00SessionCreation\x00\x02"
        b"\x10\x00SessionCreation\x00\x03"
        b"ProcessInfo\x00"
    )
    names = ort_etw.count_names(trace)
    assert names["SessionCreationStart"] == 1
    assert names["SessionCreation"] == 2
    assert names["ProcessInfo"] == 1
    assert names["RuntimePerf"] == 0


def test_the_decoded_total_counts_only_this_provider() -> None:
    xml = b"""<?xml version="1.0"?>
<Events>
 <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Name="Microsoft.ML.ONNXRuntime"
   Guid="{3A26B1FF-7484-7484-7484-15261F42614D}"/></System>
 </Event>
 <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Guid="{68FDD900-4A3E-11D1-84F4-0000F80464E3}"/></System>
 </Event>
</Events>"""
    assert ort_etw.count_decoded(xml) == 1


def test_the_table_says_what_failed() -> None:
    results = _as_measured()
    table = ort_etw.render(results, ["something"])
    assert "| default | 3 | 0 | none |" in table
    assert "- **FAIL:** something" in table
    assert "Every claim held." in ort_etw.render(results, [])


@pytest.mark.skipif(sys.platform == "win32", reason="the refusal is off Windows")
def test_off_windows_it_refuses_rather_than_reporting_nothing(tmp_path: Path) -> None:
    assert ort_etw.main(["--out", str(tmp_path)]) == 2
