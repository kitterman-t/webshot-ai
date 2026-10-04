"""Shared test helpers.

Small on purpose: the only thing here is the loopback server, because
docs/06-quality-and-testing.md forbids a test touching the public internet and
three separate modules had therefore grown their own copy of the same
`ThreadingHTTPServer` lifecycle.  The *handlers* differ legitimately — a static
file server, a 401 wall, a recorder — and stay with the tests that need them;
the ten lines of bind/thread/shutdown around them did not need to be written
three times.

The implementation moved to `tools/golden/loopback.py` once the golden harness
needed it as well: a tools module importing from the test tree needed a
`sys.path` insert and a mypy suppression, and the dependency reads better the
other way round. This re-export keeps `from conftest import serve` working.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.golden.loopback import serve  # noqa: E402

__all__ = ["serve"]
