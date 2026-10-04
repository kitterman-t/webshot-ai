"""A local stand-in for a Guidde share page, for the corpus and the tests.

No test and no recorded golden may touch live Guidde (docs/06): one that did
would be measuring a third party's uptime rather than WebShot's behaviour, and
a golden recorded against it could never be re-recorded reliably.

What this imitates is not Guidde's markup but its *shape* — a page whose body
is empty until a script fetches the playbook, and an endpoint that answers with
the recorded response. That shape is the whole reason the extractor intercepts
rather than requests, so it is what the extractor has to be driven against.

It lives beside the harness rather than in `tests/` because both need it: the
`video` corpus case stands one up to record, and `tests/test_video_capture.py`
stands one up to assert. One definition means a case and a test cannot end up
exercising two subtly different services.
"""

from __future__ import annotations

import http.server
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "guidde" / "quickguidde-sample-playbook.json"
)

#: A 2x2 PNG. `distinct_pixel` varies its pixel data per step so that no two
#: screenshots are byte-identical.
_PNG_PREFIX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000200000002080600000072b60d24"
)


def distinct_pixel(seed: int) -> bytes:
    """A valid 2x2 PNG whose bytes differ for every `seed`.

    One shared image would be indistinguishable from a bug. Every screenshot
    URL used to be rewritten to a single path, so the download cache collapsed
    all eleven steps onto one file — and a golden recorded that way cannot tell
    correct per-step mapping from an implementation that points every step at
    the first screenshot. Distinct bytes per step is what makes the mapping
    something the corpus actually pins (docs/09 P7-11).
    """
    import struct
    import zlib

    # One 2x2 RGBA frame, its first pixel carrying the seed.
    raw = b"".join(
        b"\x00" + bytes([seed % 251, (seed * 7) % 251, (seed * 13) % 251, 255] * 2)
        for _ in range(2)
    )
    data = zlib.compress(raw)
    chunks = b"".join(
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload))
        for name, payload in ((b"IDAT", data), (b"IEND", b""))
    )
    return _PNG_PREFIX + chunks


#: What the tests use where the content does not matter.
PIXEL = distinct_pixel(0)

_STORAGE_URL = re.compile(r"https://storage\.app\.guidde\.com/[^\"]+")


def rewritten_payload(origin: str) -> bytes:
    """The recorded response with its asset URLs aimed at the stand-in.

    The fixture on disk keeps the shape of a recorded response — every key,
    number and timing, and every URL's host and path layout — because the shape
    under test is Guidde's, and reshaping it would make everything pass against
    something Guidde never sent. Its prose, names and ids are synthetic and its
    download tokens are removed, so nothing in it is a third party's content or
    credential. Only the copy the stand-in serves is redirected, and **each
    distinct upstream URL keeps a distinct stand-in URL**, so the eleven steps
    stay eleven downloads rather than collapsing into one through the dedupe.
    """
    text = PLAYBOOK_FIXTURE.read_text(encoding="utf-8")
    seen: dict[str, str] = {}

    def redirect(match: re.Match[str]) -> str:
        upstream = match.group(0)
        if upstream not in seen:
            seen[upstream] = f"{origin}/asset/{len(seen):03d}.png"
        return seen[upstream]

    return _STORAGE_URL.sub(redirect, text).encode("utf-8")


def standin(holder: dict[str, bytes]) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler serving `holder["payload"]` as the hydration response.

    The payload arrives through a mutable holder because it has to name the
    origin serving it, and the origin is only known once the socket is bound.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/share/playbooks/"):
                identifier = self.path.rsplit("/", 1)[1]
                self._send(
                    (
                        "<!doctype html><html><head><title>Playbook</title></head>"
                        "<body><div id=root></div><script>"
                        f"fetch('/c/v1/quickguidde?id={identifier}&forEmbed=true')"
                        ".then(r => r.json()).then(d => {document"
                        ".getElementById('root').textContent = d.playbook.title;});"
                        "</script></body></html>"
                    ).encode(),
                    "text/html; charset=utf-8",
                )
            elif self.path.startswith("/c/v1/quickguidde"):
                self._send(holder["payload"], "application/json")
            elif self.path.startswith("/asset/"):
                # Distinct bytes per path, so a step pointed at the wrong
                # screenshot shows up as a different checksum rather than
                # passing unnoticed.
                stem = Path(self.path).stem
                seed = int(stem) if stem.isdigit() else 0
                self._send(distinct_pixel(seed), "image/png")
            else:
                self.send_error(404)

    return Handler
