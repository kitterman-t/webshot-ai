"""Serve the journey stand-in, and say which hazard each of its behaviours carries.

No test may touch the live LMS (docs/06-quality-and-testing.md), and the
enumeration pass cannot be exercised against a static file: the thing under
test is a walk over an application whose content does not exist until it is
opened.  So the stand-in is a real single-page application on loopback.

It is built from the measurements in docs/13 P9 and P11, and every behaviour
below is present because a naive walker passes without it:

| Behaviour | Measured in | What it catches |
|---|---|---|
| A completion modal on every visit, intercepting clicks two ways | P11-1 | a walk that starts before clearing it — the click returns cleanly and nothing moves |
| A track view that is a shell when the click returns | P11-2 | a fixed wait, which records an empty track as a complete one |
| Chakra accordions with `aria-expanded`, `data-index`, `aria-controls` | P11-3 | a walker written against invented markup |
| `css-*` and `id` values that change on every render | P11-3 | keying on an Emotion hash or a React `useId` |
| Track rows carrying their title in `aria-label` | P11-3 | reading a track's name from its text |
| Panels that mount on first expand and are then retained | P9-4, P11-4 | a collapsed journey holding track names; content presence used as an expansion witness |
| `allowMultiple` — siblings stay open | P11-5 | nothing; it is why the count assertion does not fire spuriously |
| Row nodes reused across both views | P9-4 | a walker carrying a reference across a click |
| Five levels, journey → section → track → group → module | P9-1 | a four-level walk |
| Four anchors, all chrome | P9-4 | link-following |
| `document.title` keeping the last track's name | P9-4 | a walker keying titles on it |
| No `data-testid`, `aria-label` only on track rows | P9-6 | relying on hooks production does not have |

Three behaviours are opt-in by path segment and are **not** measured — they
model failures so the refusals have something real to refuse: `drift` advances
progress on a track open, `shift` drops a module once a track is fully open,
and `solo` collapses siblings (which P11-5 measured as *not* happening, and
which is kept only to show what the count assertion cannot see).

Two things are stand-ins for a mechanism rather than copies of one, and it is
worth being plain about which. Node reuse is *a* way to make a held reference
rebind silently; P9-4 recorded the effect on the live application, not the
implementation behind it. The unlabelled icons are the same shape of claim at a
different scale — this file carries a handful, production carries 250.
"""

from __future__ import annotations

import base64
import http.server
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "journey_app"


#: Two 1x1 PNGs with different pixels, so they hash differently. One is used by
#: several modules and one by exactly one — the corpus dedupe needs both cases,
#: and an "optimisation" that ate the single-module common case would otherwise
#: pass unnoticed.
_SHARED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_UNIQUE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def standin() -> type[http.server.BaseHTTPRequestHandler]:
    """A handler serving the journey application and its two images."""
    body = (FIXTURE / "index.html").read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # stdlib's spelling, not ours
            for suffix, payload in (
                ("shared.png", _SHARED_PNG),
                ("unique.png", _UNIQUE_PNG),
            ):
                if self.path.endswith(suffix):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            # Every other path answers the application, which is what a
            # single-page app behind one route does.
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Quiet: the harness records stderr and this is not output."""

    return Handler
