"""WebShot — turn web pages and local documents into PDFs and AI-ready bundles.

The public Python API is deliberately small and is covered by the compatibility
policy in `docs/04-spec.md` §7: `convert_url_to_pdf()` and `main()` keep working
across v3.

Every name below is resolved on first access rather than imported here (PEP
562).  Importing *any* submodule runs this file, so the eager
`from .pipeline import convert_url_to_pdf` this module used to carry put the
whole capture stack — Playwright, Pillow, pypdf, lxml, pandas — behind
`import webshot.version`, and therefore behind `webshot --version`: 1467 of the
1505 modules that command loaded came through here, not through anything
`cli.py` asked for (docs/09 P10-6).  Deferring only `cli.py` would have changed
nothing at all.

What §7 promises is unchanged.  `from webshot import convert_url_to_pdf` already
went through an attribute lookup on this module; the lookup now imports the
submodule on the way past.  Only *when* the cost is paid has moved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import onnxruntime_telemetry
from .version import VERSION

if TYPE_CHECKING:  # pragma: no cover - resolved by `__getattr__` at runtime
    from .acquire.router import normalize_source, resolve_output, sanitize_filename
    from .cli import build_parser, main
    from .config import CaptureOptions, CaptureResult, validate_css_length
    from .errors import WebShotError
    from .pipeline import convert_url_to_pdf

# onnxruntime 1.29 is the first release to ship telemetry off Windows, and it
# starts the uploader when it is imported — which a document capture does,
# through markitdown's magika, and RapidOCR does again.  The uploader sends
# events and a persistent device id to Microsoft, which a capture tool must not
# do behind its user's back and a test must not do at all.  Its static
# destructor also races its own HTTP thread at interpreter exit: that race is
# the `recursive_mutex lock failed` abort, exit 134 after every output was
# already written (docs/09 P10-14).  Windows builds write ETW events instead,
# and take a different switch (P10-26).  Both have to be in place before
# onnxruntime can be imported, so they are set here — this file runs before any
# path into WebShot can import anything.  A caller who sets
# `ORT_DISABLE_TELEMETRY` explicitly is obeyed; `0` opts back into the
# telemetry on every platform, and into the abort with it.
onnxruntime_telemetry.switch_off()

__version__ = VERSION

__all__ = [
    "VERSION",
    "CaptureOptions",
    "CaptureResult",
    "WebShotError",
    "__version__",
    "build_parser",
    "convert_url_to_pdf",
    "main",
    "normalize_source",
    "resolve_output",
    "sanitize_filename",
    "validate_css_length",
]

#: Which submodule each exported name comes from.  A table rather than a chain
#: of `if name ==` branches so that a name in `__all__` with no source here is
#: a `KeyError` in one obvious place instead of a silent `AttributeError` that
#: reads exactly like a typo from the caller's side.
_EXPORTS: dict[str, str] = {
    "CaptureOptions": ".config",
    "CaptureResult": ".config",
    "WebShotError": ".errors",
    "build_parser": ".cli",
    "convert_url_to_pdf": ".pipeline",
    "main": ".cli",
    "normalize_source": ".acquire.router",
    "resolve_output": ".acquire.router",
    "sanitize_filename": ".acquire.router",
    "validate_css_length": ".config",
}


def __getattr__(name: str) -> Any:
    """Import the submodule that owns `name`, the first time `name` is read."""
    source = _EXPORTS.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(source, __name__), name)
    # Cache as a real global: every later read is a plain module attribute
    # lookup, so the deferral costs one dict miss per name per process and
    # nothing at all after that.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """`dir(webshot)` still lists the API, which `__getattr__` alone would not."""
    return sorted(__all__)
