"""A loopback HTTP server, for tests and for the golden corpus alike.

No test and no recorded golden may touch the public internet
(docs/06-quality-and-testing.md), so both need a server on 127.0.0.1 — and the
lifecycle around one is the same ten lines whoever is standing it up.

It lives under `tools/` rather than in `tests/conftest.py` because the golden
harness needs it too, and a tools module reaching into the test tree to import
it needed a `sys.path` insert and a mypy suppression to work at all. The
dependency runs the other way now: `tests/conftest.py` re-exports this.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from contextlib import contextmanager


class LoopbackServer(http.server.ThreadingHTTPServer):
    """A threading server whose listen queue holds a browser's burst."""

    #: socketserver listens with a queue of 5, and Chromium opens six
    #: connections to one host at once. While the serve loop waits for CPU, as
    #: on a busy CI runner, Linux holds a connection that does not fit until the
    #: loop accepts it, but macOS resets it at once, so a request the page made
    #: failed on one platform only (docs/09 P20-9).
    request_queue_size = 64


@contextmanager
def serve(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[str]:
    """Run `handler` on 127.0.0.1 for the block, and yield its origin.

    Port 0 so concurrent test modules cannot collide, and the shutdown is the
    full three steps — `shutdown()` stops the serve loop, `server_close()`
    releases the socket, and the join proves the thread actually went away
    rather than leaking into the next test.
    """
    httpd = LoopbackServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
