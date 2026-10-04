"""What reaches stderr: WebShot's own log, and only what a user can act on.

A new user saw pypdf's "Object count ... exceeds defined trailer size" and
docling's "Clashing hyperlinks" on ordinary captures, and under `--verbose`
hundreds of trafilatura and htmldate DEBUG lines around WebShot's own; a
protected-viewer capture adds fontTools' "head pruned" and its kin at INFO.
`setup_logging` now lowers only WebShot's loggers for `--verbose` and caps
those libraries' housekeeping at ERROR.

Each probe runs in a fresh interpreter, because `logging.basicConfig` does
nothing once the root logger has a handler, and pytest has already given it
one. Each one also triggers the real records (pypdf repairing a trailer,
docling merging a heading's two links, fontTools subsetting a fixture font,
trafilatura and htmldate reading a page), and a control run with the root
logger wide open proves they were emitted, so a quiet stderr cannot be the
quiet of a probe that never reached the libraries.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The smallest inputs that make each library speak. A heading holding two
#: links is what docling cannot represent and warns about; a PDF whose trailer
#: understates its object count is what pypdf repairs and warns about.
_PROBE = """
import io, logging, re, sys
mode = sys.argv[1]
stream = io.StringIO()
named = logging.Formatter("%(levelname)s [%(name)s]: %(message)s")
if mode == "control":
    logging.basicConfig(level=logging.DEBUG, stream=stream)
else:
    from webshot.cli import setup_logging
    setup_logging(mode == "verbose", stream=stream)
for handler in logging.getLogger().handlers:
    handler.setFormatter(named)

from pypdf import PdfWriter
writer = PdfWriter()
writer.add_blank_page(72, 72)
buffer = io.BytesIO()
writer.write(buffer)
PdfWriter(clone_from=io.BytesIO(re.sub(rb"/Size \\d+", b"/Size 1", buffer.getvalue())))

from webshot.extract.docling_bridge import extract
extract(
    "<html><body><h1>T</h1><h2><a href='https://a.example/'>one</a> "
    "<a href='https://b.example/'>two</a></h2></body></html>",
    [],
)

from pathlib import Path
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont
font = TTFont(Path("tests/fixtures/fonts/ligature-fixture.ttf"))
subsetter = Subsetter()
subsetter.populate(text="abc")
subsetter.subset(font)

import trafilatura
trafilatura.extract(
    "<html><head><title>T</title></head><body><article><h1>T</h1><p>"
    + "A sentence of ordinary article text. " * 40
    + "</p></article></body></html>",
    with_metadata=True,
)

webshot = logging.getLogger("webshot")
webshot.debug("WEBSHOT-DEBUG-MARKER")
webshot.info("WEBSHOT-INFO-MARKER")
logging.getLogger("webshot.journey").debug("WEBSHOT-CHILD-DEBUG-MARKER")
webshot.warning("WEBSHOT-WARNING-MARKER")
print("LOGGED:" + stream.getvalue())
"""

UPSTREAM = ("[pypdf", "[docling", "[fontTools", "[trafilatura", "[htmldate")


def _logged(mode: str) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, mode],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO_ROOT,
    )
    assert completed.returncode == 0, (
        f"the {mode} probe exited {completed.returncode}:\n{completed.stderr[-3000:]}"
    )
    _, found, logged = completed.stdout.partition("LOGGED:")
    assert found, f"the {mode} probe never reported; stdout was {completed.stdout!r}"
    return logged.splitlines()


@pytest.fixture(scope="module")
def control() -> list[str]:
    return _logged("control")


def test_the_probe_reaches_every_library_it_quiets(control: list[str]) -> None:
    """The guard for the guard: each record must exist before it can be hidden."""
    text = "\n".join(control)
    assert "Object count" in text and "exceeds defined trailer size" in text, text
    assert "Clashing hyperlinks" in text, text
    assert any(line.startswith("INFO [fontTools.subset]") for line in control), text
    assert any(line.startswith("DEBUG [trafilatura") for line in control), text
    assert any(line.startswith("DEBUG [htmldate") for line in control), text


def test_default_level_shows_webshot_and_no_upstream_housekeeping(
    control: list[str],
) -> None:
    del control  # ordering only: the control must have proven the inputs
    logged = _logged("default")
    upstream = [line for line in logged if any(name in line for name in UPSTREAM)]
    assert not upstream, "upstream records reached stderr:\n" + "\n".join(logged)
    assert "INFO [webshot]: WEBSHOT-INFO-MARKER" in logged, logged
    assert "WARNING [webshot]: WEBSHOT-WARNING-MARKER" in logged, logged
    assert not any("DEBUG-MARKER" in line for line in logged), logged


def test_verbose_shows_webshot_debug_and_no_one_elses(control: list[str]) -> None:
    del control
    logged = _logged("verbose")
    assert "DEBUG [webshot]: WEBSHOT-DEBUG-MARKER" in logged, logged
    assert "DEBUG [webshot.journey]: WEBSHOT-CHILD-DEBUG-MARKER" in logged, logged
    foreign_debug = [
        line
        for line in logged
        if line.startswith("DEBUG [") and not line.startswith("DEBUG [webshot")
    ]
    assert not foreign_debug, "other libraries' DEBUG under --verbose:\n" + "\n".join(
        foreign_debug
    )
    upstream = [line for line in logged if any(name in line for name in UPSTREAM)]
    assert not upstream, "upstream records reached stderr:\n" + "\n".join(logged)
