"""The loopback server answers a burst of connections its serve loop is late for.

Chromium opens six connections to one host at once, and socketserver listens
with a queue of five. When the serve loop is waiting for CPU, Linux holds a
connection that does not fit until the loop accepts it; macOS resets it at
once. A stalled image in `test_image_wait.py` then failed instead of waiting,
and the macOS job alone reported six images still loading where there were
seven (docs/09 P20-9). The golden corpus captures from the same server.

Linux holds the connections either way, so only a macOS run can fail this test.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

from tools.golden.loopback import LoopbackServer

#: One more than the connections Chromium opens to a host at once.
BURST = 7


class _NoContent(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_a_burst_waits_for_a_late_serve_loop() -> None:
    httpd = LoopbackServer(("127.0.0.1", 0), _NoContent)
    port = httpd.server_address[1]
    answers: list[str] = []

    def fetch() -> None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
                conn.sendall(b"GET / HTTP/1.0\r\n\r\n")
                answers.append(conn.makefile("rb").readline().decode().strip())
        except OSError as error:
            answers.append(repr(error))

    clients = [threading.Thread(target=fetch) for _ in range(BURST)]
    for client in clients:
        client.start()
    # Every connection is waiting in the queue before the loop accepts one.
    time.sleep(1)
    loop = threading.Thread(target=httpd.serve_forever, daemon=True)
    loop.start()
    try:
        for client in clients:
            client.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()
        loop.join(timeout=5)
    assert answers == ["HTTP/1.0 204 No Content"] * BURST, answers
