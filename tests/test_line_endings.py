"""Bundle text is written with `\\n` line endings on every platform.

`Path.write_text` without `newline=` writes `os.linesep`, so a Windows capture
put `\\r\\n` in every text artifact: the `content.html` the weekly Windows run
embedded ended in `</html>\\r\\n` (docs/09 P10-24). The manifest's digests are
taken from the files as written, so they agreed with those bytes and nothing
noticed. The golden corpus cannot see this class at all: it reads bundle text
with universal newlines, and it is recorded on macOS (docs/09 P10-27).

Two tests, because neither covers what the other does. The first drives real
captures through a text layer that translates the way Windows's does, so a
writer that leaves translation on fails here on every platform rather than once
a week on one. It found the writer the second cannot see: OCRmyPDF's sidecar.
It reaches only the writers these captures run. The second reads every writer
in `src/webshot` and requires it to say which newline it writes. It reaches
writers no capture here runs, but only the calls it can recognize.

One file is exempt from the newline check: `source/`, the local input copied
byte for byte (docs/04 §2.2). Its newlines are the input's, and a Windows
checkout gives every fixture `\\r\\n`, so the first test compares that copy
with the input's bytes instead. A third gives it an input with `\\r\\n` on
every platform, so the exemption is exercised here and not only on Windows.
"""

from __future__ import annotations

import _pyio
import ast
import asyncio
import http.server
import io
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader
from tools.golden.guidde_standin import rewritten_payload, standin
from tools.golden.harness import FIXTURES, LMS_FIXTURE, assemble_viewer_fixture
from tools.golden.loopback import serve

from webshot.acquire.router import normalize_source
from webshot.config import CaptureOptions
from webshot.ocr.tesseract import tesseract_executable
from webshot.pipeline import convert_url_to_pdf
from webshot.report import write_report

SOURCE = Path(__file__).resolve().parents[1] / "src" / "webshot"


@pytest.fixture
def windows_text_layer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make pathlib's text writes translate `\\n` the way Windows's do.

    CPython's C text layer decides at build time whether to translate, so
    patching `os.linesep` alone changes nothing on macOS or Linux. The
    pure-Python implementation reads `os.linesep` whenever a file is opened,
    and pathlib opens through `io.open`, so routing `io.open` to it makes every
    `Path.write_text` and `Path.open` translate unless it passed `newline=`.
    Builtin `open` is not rerouted: nothing in src/webshot writes through it,
    and the static test below fails on one that does.
    """
    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(io, "open", _pyio.open)
    # A capture that writes no `\r\n` proves nothing unless an unpinned write
    # would have. This is the write every bundle file made before P10-27.
    probe = tmp_path / "probe.txt"
    probe.write_text("a\n", encoding="utf-8")
    assert probe.read_bytes() == b"a\r\n", (
        f"the emulated text layer wrote {probe.read_bytes()!r} for an unpinned "
        "'a\\n', so the test using it could not fail"
    )
    probe.unlink()


class _Fixtures(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def log_message(self, *args: Any) -> None:
        """Quiet."""


def _capture(workdir: Path, source: str, **options: Any) -> list[Path]:
    pdf, bundle = workdir / "capture.pdf", workdir / "capture.ai"
    capture = CaptureOptions(
        source=normalize_source(source),
        output=pdf,
        ai_bundle_directory=bundle,
        # No page here has text in a raster, and recognition must not make the
        # web cases depend on whether Tesseract is installed.
        ocr=False,
        **options,
    )
    result = asyncio.run(convert_url_to_pdf(capture))
    write_report(result, capture, workdir / "report.json")
    return [pdf, bundle, workdir / "report.json"]


def _walkthrough(workdir: Path) -> list[Path]:
    """A Guidde walkthrough: the video writers, and the rewrite of content.md."""
    holder = {"payload": b"{}"}
    with serve(standin(holder)) as origin:
        holder["payload"] = rewritten_payload(origin)
        return _capture(workdir, str(LMS_FIXTURE), guidde_origin=origin)


def _captions(workdir: Path) -> list[Path]:
    """Caption tracks, which load only over http: the transcript writers."""
    with serve(_Fixtures) as origin:
        return _capture(workdir, f"{origin}/captions_page.html")


def _tables(workdir: Path) -> list[Path]:
    """A page with a table: every web-path writer, and the one CSV."""
    return _capture(workdir, str(FIXTURES / "professional_page.html"))


def _protected(workdir: Path) -> list[Path]:
    """The protected-viewer bundle, whose OCR sidecar OCRmyPDF writes."""
    if tesseract_executable() is None:
        pytest.skip("tesseract is not installed; the protected path needs it")
    pdf, bundle = workdir / "capture.pdf", workdir / "capture.ai"
    assemble_viewer_fixture(workdir, pdf, bundle)
    return [pdf, bundle]


#: Each case, and files it must have written: a case that stopped producing
#: them would otherwise pass by checking less.
CASES: dict[str, tuple[Callable[[Path], list[Path]], tuple[str, ...]]] = {
    "walkthrough": (
        _walkthrough,
        (
            "capture.ai/content.md",
            "capture.ai/content.txt",
            "capture.ai/chunks.jsonl",
            "capture.ai/manifest.json",
            "capture.ai/source/lms_page.html",
            "capture.ai/videos/*/walkthrough.md",
            "capture.ai/videos/*/steps.json",
            "capture.ai/videos/*/transcript.txt",
            "capture.ai/videos/*/transcript.vtt",
            "report.json",
        ),
    ),
    "captions": (
        _captions,
        (
            "capture.ai/videos/media-01/transcript.txt",
            "capture.ai/videos/media-01/transcript.vtt",
            "capture.ai/videos/media-02/translation-de.vtt",
        ),
    ),
    "tables": (
        _tables,
        (
            "capture.ai/content.html",
            "capture.ai/content.json",
            "capture.ai/content.doctags",
            "capture.ai/accessibility.yaml",
            "capture.ai/assets.json",
            "capture.ai/links.json",
            "capture.ai/source/professional_page.html",
            "capture.ai/structured-data.json",
            "capture.ai/tables/table-001.csv",
        ),
    ),
    "protected": (
        _protected,
        (
            "capture.ai/capture.json",
            "capture.ai/content.txt",
            "capture.ai/chunks.jsonl",
            "capture.ai/ocr/page-001.tsv",
            "capture.ai/ocr/text-layer.txt",
        ),
    ),
}


def _text_files(workdir: Path, outputs: list[Path]) -> Iterator[tuple[str, bytes]]:
    """Every UTF-8 file the case wrote, and every UTF-8 attachment of its PDF."""
    for output in outputs:
        paths = sorted(output.rglob("*")) if output.is_dir() else [output]
        for path in paths:
            if not path.is_file():
                continue
            data = path.read_bytes()
            if path.suffix == ".pdf":
                for name, bodies in PdfReader(path).attachments.items():
                    for body in bodies:
                        if _is_text(body):
                            yield f"{path.name}#{name}", body
            elif _is_text(data):
                yield path.relative_to(workdir).as_posix(), data


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


#: Where a bundle keeps the local input, copied byte for byte (`bundle/build.py`).
#: A `\r\n` there is the input's own, not a writer's: on the first Windows run
#: that got this far (36463148043), git had checked every fixture out with
#: `\r\n`, and both `source/` copies were all this test found (docs/09 P10-27).
INPUT_COPY = "capture.ai/source/"


def _translated(name: str, data: bytes) -> bool:
    # RFC 4180 ends a CSV row with "\r\n", and the csv module writes that on
    # every platform through `newline=""`. A translating writer turns it into
    # "\r\r\n", so that is what a CSV is checked for.
    if name.endswith(".csv"):
        return b"\r\r\n" in data
    return b"\r\n" in data


def _translations(checked: dict[str, bytes]) -> dict[str, int]:
    """Each written file carrying a translated newline, and how many `\\r\\n`."""
    return {
        name: data.count(b"\r\n")
        for name, data in checked.items()
        if not name.startswith(INPUT_COPY) and _translated(name, data)
    }


@pytest.mark.browser
@pytest.mark.parametrize("case", sorted(CASES))
def test_no_text_artifact_carries_a_translated_newline(
    case: str, windows_text_layer: None, tmp_path: Path
) -> None:
    run, expected = CASES[case]
    workdir = tmp_path / case
    workdir.mkdir()
    checked = dict(_text_files(workdir, run(workdir)))

    missing = [
        pattern
        for pattern in expected
        if not any(Path(name).match(pattern) for name in checked)
    ]
    assert not missing, f"{case} wrote none of {missing}; checked {sorted(checked)}"
    translated = _translations(checked)
    assert not translated, (
        f"{case}: these files carry a translated newline (name: count of \\r\\n) "
        f"{translated}"
    )
    # What the newline check leaves out, checked for what it should be. Every
    # local input these cases capture is a fixture.
    altered = [
        name
        for name, data in checked.items()
        if name.startswith(INPUT_COPY)
        and data != (FIXTURES / name.removeprefix(INPUT_COPY)).read_bytes()
    ]
    assert not altered, f"{case}: not the input's bytes: {altered}"


#: A page whose own newlines are `\r\n`, as every fixture is in a Windows checkout.
CRLF_PAGE = (
    b"<!doctype html>\r\n"
    b'<html lang="en">\r\n'
    b"<head><title>CRLF input</title></head>\r\n"
    b"<body>\r\n"
    b"<h1>Line endings</h1>\r\n"
    b"<p>This page ends each of its lines with a carriage return.</p>\r\n"
    b"</body>\r\n"
    b"</html>\r\n"
)


@pytest.mark.browser
def test_the_input_copy_keeps_the_inputs_own_newlines(
    windows_text_layer: None, tmp_path: Path
) -> None:
    """`source/` is the input byte for byte, and the exemption is that wide.

    The input carries `\\r\\n` on every platform here, so the exemption above
    is exercised on each run rather than once a week on Windows: without it
    this fails everywhere, and so does a `source/` copy that rewrote the
    input's newlines, or a writer that let them into anything else.
    """
    page = tmp_path / "input" / "crlf_page.html"
    page.parent.mkdir()
    page.write_bytes(CRLF_PAGE)
    workdir = tmp_path / "capture"
    workdir.mkdir()
    checked = dict(_text_files(workdir, _capture(workdir, str(page))))

    copy = checked.get(f"{INPUT_COPY}crlf_page.html")
    assert copy == CRLF_PAGE, (
        f"the input copy is {copy!r}, not the input's bytes; wrote {sorted(checked)}"
    )
    assert "capture.ai/content.html" in checked, f"wrote {sorted(checked)}"
    translated = _translations(checked)
    assert not translated, (
        f"a \\r\\n input put a newline other than the copy's into {translated}"
    )


# --------------------------------------------------------------------------- #
# Every writer says which newline it writes
# --------------------------------------------------------------------------- #

_MODE_LETTERS = set("rwaxbt+")


def _mode(call: ast.Call) -> str:
    """The mode a call opens with, if it spells one out; "r" otherwise.

    `mode=`, or the first string argument made only of mode letters. That reads
    `path.open("w")`, `open(path, "a")` and `os.fdopen(fd, "w")` alike without
    knowing which one it is looking at, and a zip member's name or an image in
    memory is not taken for a mode. A mode computed at run time is not seen.
    """
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    for argument in call.args:
        if (
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and argument.value
            and set(argument.value) <= _MODE_LETTERS
        ):
            return argument.value
    return "r"


def text_writers(source: str, filename: str) -> list[tuple[str, bool]]:
    """Each call in `source` that writes text, and whether it pins its newline.

    Pinned means `newline="\\n"`, or `newline=""`, which writes what it is
    given: the CSV writer's own `\\r\\n` is the same on every platform.
    """
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else getattr(function, "id", None)
        )
        if name in {"open", "fdopen"}:
            mode = _mode(node)
            if "b" in mode or not set(mode) & set("wax+"):
                continue
        elif name != "write_text":
            continue
        newline = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "newline"),
            None,
        )
        pinned = isinstance(newline, ast.Constant) and newline.value in {"\n", ""}
        found.append((f"{filename}:{node.lineno}", pinned))
    return found


def test_the_writer_scan_finds_an_unpinned_write() -> None:
    snippet = "\n".join(
        [
            "from pathlib import Path",
            'Path("a").write_text("x", encoding="utf-8")',
            'Path("b").open("a", encoding="utf-8")',
            'open("c", "w", encoding="utf-8")',
            'Path("d").write_text("x", encoding="utf-8", newline=None)',
            'Path("e").write_text("x", encoding="utf-8", newline="\\n")',
            'Path("f").open("w", encoding="utf-8", newline="")',
            'Path("g").open("wb")',
            'Path("h").open(encoding="utf-8")',
            'os.open("i", os.O_CREAT | os.O_RDWR, 0o600)',
            'zipfile.ZipFile("j").open("word/document.xml")',
        ]
    )
    assert text_writers(snippet, "s.py") == [
        ("s.py:2", False),
        ("s.py:3", False),
        ("s.py:4", False),
        ("s.py:5", False),
        ("s.py:6", True),
        ("s.py:7", True),
    ]


def test_every_text_writer_says_which_newline_it_writes() -> None:
    writers = [
        writer
        for path in sorted(SOURCE.rglob("*.py"))
        for writer in text_writers(
            path.read_text("utf-8"), path.relative_to(SOURCE).as_posix()
        )
    ]
    # The scan found the web path's own writers, or it is not reading them.
    files = {location.rsplit(":", 1)[0] for location, _ in writers}
    assert {"extract/exports.py", "bundle/publish.py", "bundle/build.py"} <= files, (
        f"the scan found text writers only in {sorted(files)}"
    )
    unpinned = [location for location, pinned in writers if not pinned]
    assert not unpinned, (
        f'these write text without newline="\\n", so on Windows they write '
        f"\\r\\n (docs/09 P10-27): {unpinned}"
    )
