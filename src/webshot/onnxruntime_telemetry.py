"""onnxruntime's telemetry, switched off before it can report a capture.

WebShot never imports onnxruntime to do work.  markitdown's file-type sniffer
(magika) imports it at module scope, RapidOCR imports it when its recognizer is
built, and docling's model stages can import it too.  Each of them then builds
inference sessions, and onnxruntime reports every session it builds unless it
is told not to.  This module is the one place that tells it, and the only
WebShot module that touches onnxruntime at all.

The two platforms need different switches (upstream's `Privacy.md`):

- **Non-Windows** builds read `ORT_DISABLE_TELEMETRY` once, as onnxruntime
  initializes.  Set, it creates no uploader, no events and no device id
  (docs/09 P10-14).  So `switch_off` sets it before anything can import
  onnxruntime.
- **Windows** builds do not read it.  They write ETW TraceLogging events,
  which the operating system records when a trace session is collecting and
  may send to Microsoft under the user's diagnostic-data consent.  Those
  events are suppressed by calling `onnxruntime.disable_telemetry_events()`,
  which needs onnxruntime already imported, so the call is made the moment
  its import finishes and before the importer gets it back (docs/09 P10-26).

Neither is complete on Windows, and docs/04 §4.1 says so: the `ProcessInfo`
event is written while onnxruntime's native module initializes, before any
Python can run, and a trace session that enables the provider after the call
switches the events back on.
"""

from __future__ import annotations

import os
import sys
import warnings
from types import ModuleType
from typing import Any

SWITCH = "ORT_DISABLE_TELEMETRY"

#: The values onnxruntime reads as "off", after trimming ASCII whitespace and
#: lowering case (`IsTelemetryDisabledByEnvironment`, 1.30.0).  The Windows
#: call follows the same reading, so one variable means the same thing on
#: every platform, and a caller's `0` opts back in on all of them.
_OFF = frozenset({"1", "true", "yes", "on", "y"})
_ASCII_SPACE = " \t\n\v\f\r"


def switch_off() -> None:
    """Default the switch to off, and on Windows act on it at import.

    Runs from the package init, which every path into WebShot executes
    before it can import anything.  `setdefault`, so a caller who set the
    variable is obeyed.
    """
    os.environ.setdefault(SWITCH, "1")
    if sys.platform == "win32":
        watch()


def requested() -> bool:
    """Whether the switch says off, read as onnxruntime reads it."""
    value = os.environ.get(SWITCH, "").strip(_ASCII_SPACE).lower()
    return value in _OFF


def watch() -> None:
    """Quiet onnxruntime as its import finishes, whoever imports it.

    A post-import hook rather than a call beside each importer: magika,
    RapidOCR and docling can each import onnxruntime, and a guard covers only
    the paths that cross it.
    """
    module = sys.modules.get("onnxruntime")
    if module is not None:
        # Imported before WebShot was, by the caller's own code.  Sessions it
        # already built have been reported; the ones to come need not be.
        _quiet(module)
        return
    if not any(isinstance(finder, _AfterImport) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AfterImport())


def _quiet(module: ModuleType) -> None:
    # Read now, not when the hook was installed: onnxruntime reads its own
    # variable at initialization, and this keeps the two in step.
    if not requested():
        return
    disable = getattr(module, "disable_telemetry_events", None)
    if disable is None:
        # Failing the import would fail every document read on Windows over a
        # privacy setting; saying nothing would leave the events on in silence.
        version = getattr(module, "__version__", "?")
        warnings.warn(
            f"onnxruntime {version} has no disable_telemetry_events(); its "
            "Windows telemetry events stay on (docs/09 P10-26)",
            RuntimeWarning,
            stacklevel=2,
        )
        return
    disable()


class _AfterImport:
    """Meta-path finder: onnxruntime imports as it would have, then is quieted.

    It answers only for `onnxruntime`, and only by asking the finders behind
    it for the real spec and wrapping that spec's loader.  Nothing about the
    import changes except that one call after it.
    """

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "onnxruntime":
            return None
        # Only the finders behind this one: those in front have already
        # declined, and asking them again would repeat any side effect.
        behind = sys.meta_path[sys.meta_path.index(self) + 1 :]
        for finder in behind:
            find = getattr(finder, "find_spec", None)
            spec = find(fullname, path, target) if find is not None else None
            if spec is not None:
                break
        else:
            return None
        if spec.loader is not None:
            spec.loader = _QuietAfterExec(spec.loader)
        return spec


class _QuietAfterExec:
    """The real loader, with `_quiet` run after the module has executed.

    Attributes it does not define are the real loader's, so resource readers
    and `get_filename` keep working on the wrapped spec.
    """

    def __init__(self, loader: Any) -> None:
        self._loader = loader

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loader, name)

    def create_module(self, spec: Any) -> Any:
        return self._loader.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._loader.exec_module(module)
        _quiet(module)
