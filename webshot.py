"""Backward-compatible entry point for `python webshot.py SOURCE [options]`.

The package lives in `src/webshot/`, but a source checkout has this file on
`sys.path` under the same name, so a plain `import webshot` here would shadow
it. The shim loads the real package from `src/` and substitutes it for itself
in `sys.modules`, so `python webshot.py ...`, `import webshot` and
`import webshot.<module>` all reach the same code as the `webshot` command.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PACKAGE_DIR = Path(__file__).resolve().parent / "src" / "webshot"
_PACKAGE_INIT = _PACKAGE_DIR / "__init__.py"


def _load_package() -> ModuleType:
    """Import `src/webshot/` as the `webshot` package, replacing this shim."""
    if not _PACKAGE_INIT.is_file():
        raise ModuleNotFoundError(
            f"WebShot package not found at {_PACKAGE_DIR}. Run this shim from a "
            "source checkout, or use the installed `webshot` command."
        )
    spec = importlib.util.spec_from_file_location(
        "webshot",
        _PACKAGE_INIT,
        submodule_search_locations=[str(_PACKAGE_DIR)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise ImportError(f"Could not load the WebShot package from {_PACKAGE_INIT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["webshot"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("webshot", None)
        raise
    return module


_loaded = sys.modules.get("webshot")
if _loaded is None or getattr(_loaded, "__file__", None) != str(_PACKAGE_INIT):
    _loaded = _load_package()

main = _loaded.main


def __getattr__(name: str) -> object:
    """Forward every other public name to the package, on first access.

    On access rather than bound beside `main`: binding a name such as
    `convert_url_to_pdf` here imports `webshot.pipeline`, which made
    `python webshot.py --version` load the whole capture stack that the
    console script defers (docs/09 P10-6; `tests/test_footprint.py` holds it).

    Normally unreachable — `_load_package` substitutes the real package into
    `sys.modules["webshot"]`, so `import webshot` returns that and never this
    module. It exists for the case where this file *is* the module a caller
    holds, which is exactly what the two assignments above were for.
    """
    return getattr(_loaded, name)


if __name__ == "__main__":
    raise SystemExit(main())
