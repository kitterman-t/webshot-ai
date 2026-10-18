"""Tesseract's output is read as the UTF-8 it is, whatever the locale says.

Tesseract writes UTF-8. The bridge ran it with `text=True` and no encoding, so
`subprocess` decoded with the locale's encoding unless Python ran in UTF-8
mode. On Windows that is the ANSI code page, cp1252 on an English install: a
recognized "Café" became "CafÃ©" with no error, and "Í", whose UTF-8 holds a
byte cp1252 does not define, failed to decode (docs/09 P91).

Each test runs the bridge in a child interpreter with UTF-8 mode off and the
locale's encoding set to cp1252, which is what `subprocess` asks on a Windows
interpreter, against a stand-in Tesseract that prints fixed bytes. Where the
old decoding got those bytes wrong, the child also decodes them that way, as a
control: a child that no longer reproduces the old decoding has measured
nothing, and the test fails rather than passing on it. One variant runs with
the machine's own encoding and no override; it means something only on
Windows, so it runs only there.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"
CAPITAL_I_ACUTE = "\N{LATIN CAPITAL LETTER I WITH ACUTE}"
#: "Café" decodes under cp1252 without an error, to the wrong text. "Í" is
#: C3 8D in UTF-8, and cp1252 defines no character for 8D, so it raises.
CAFE = f"Caf{E_ACUTE}"
CAFE_I = f"Caf{E_ACUTE} {CAPITAL_I_ACUTE}"

#: What Tesseract 5.5.3 printed on macOS for a rendered line of Arial reading
#: "Café Índice": `tesseract <image> stdout -l eng --psm 6 tsv`. `eng` has no
#: "Í", so the second word is `indice`; "Café" is `Caf\xc3\xa9`, UTF-8.
RECORDED = Path(__file__).parent / "fixtures" / "tesseract" / "cafe-indice.tsv"

#: The columns Tesseract 5 prints at the head of its TSV.
TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)

STAND_IN = """\
import sys
from pathlib import Path

here = Path(__file__).parent
sys.stdout.buffer.write((here / "stdout.bin").read_bytes())
sys.stdout.buffer.flush()
sys.stderr.buffer.write((here / "stderr.bin").read_bytes())
sys.stderr.buffer.flush()
sys.exit({status})
"""

#: Runs in the child. `subprocess` decodes `text=True` output with
#: `locale.getencoding()` when UTF-8 mode is off, so replacing it is how this
#: interpreter decodes as a Windows one does. The report is JSON with ASCII
#: escapes, so the child's own stdout encoding cannot touch it.
CHILD = """\
import json, locale, subprocess, sys
from pathlib import Path

stand_in, forced, call = sys.argv[1:4]
if forced:
    locale.getencoding = lambda: forced

from webshot.ocr import tesseract

tesseract._TESSERACT = stand_in


def attempt(function):
    try:
        return {"value": function()}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


def page():
    result = tesseract.recognize_page(1, Path(stand_in), "eng", 6)
    return {"text": result.text, "tsv": result.tsv}


def asset():
    return tesseract._run_tesseract_sync(Path(stand_in), "eng", 6).text


def languages():
    return sorted(tesseract.installed_languages())


def doctor():
    from webshot.doctor import parse_tesseract_languages, tool_output

    return sorted(parse_tesseract_languages(tool_output(stand_in, "--list-langs")))


def old_decoding():
    # The bridge's call before P91: text=True and no encoding.
    completed = subprocess.run(
        [stand_in, "--list-langs"], capture_output=True, text=True, check=False
    )
    return {"stdout": completed.stdout, "stderr": completed.stderr}


calls = {"page": page, "asset": asset, "languages": languages, "doctor": doctor}
print(
    json.dumps(
        {
            "encoding": locale.getencoding(),
            "utf8_mode": sys.flags.utf8_mode,
            "bridge": attempt(calls[call]),
            "control": attempt(old_decoding),
        }
    )
)
"""


def _stand_in(
    directory: Path, *, stdout: bytes, stderr: bytes = b"", status: int = 0
) -> Path:
    """A Tesseract that prints *stdout* and *stderr* and exits with *status*."""
    (directory / "stdout.bin").write_bytes(stdout)
    (directory / "stderr.bin").write_bytes(stderr)
    script = directory / "stand_in.py"
    script.write_text(STAND_IN.format(status=status), encoding="utf-8")
    if sys.platform == "win32":
        wrapper = directory / "tesseract.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        wrapper = directory / "tesseract"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return wrapper


def _run_child(
    directory: Path, stand_in: Path, call: str, forced: str | None = "cp1252"
) -> dict[str, Any]:
    # The child runs in the stand-in's directory: the repository root holds a
    # `webshot.py` that would otherwise be imported in place of the package.
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8=0",
            "-c",
            CHILD,
            str(stand_in),
            forced or "",
            call,
        ],
        capture_output=True,
        check=False,
        timeout=120,
        cwd=directory,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", "backslashreplace"
    )
    report: dict[str, Any] = json.loads(completed.stdout)
    assert report["utf8_mode"] == 0, report
    if forced:
        assert report["encoding"] == forced, report
    return report


def _lost_to_the_old_decoding(report: dict[str, Any], stream: str) -> bool:
    """Whether decoding *stream* the old way failed.

    Where `subprocess` decodes in the calling thread, that raised
    `UnicodeDecodeError`. On Windows it reads each pipe in a thread of its
    own, and a decode error there is printed and lost: the stream came back
    `None`, and the bridge's next call on it raised `AttributeError`, which
    no per-asset handler catches (docs/09 P91).
    """
    control = report["control"]
    if "error" in control:
        return bool(control["error"] == "UnicodeDecodeError")
    return control["value"][stream] is None


def _tsv(*words: str, newline: str = "\n") -> str:
    rows = [
        f"5\t1\t1\t1\t1\t{number}\t{40 * number}\t40\t120\t48\t96.5\t{word}\n"
        for number, word in enumerate(words, 1)
    ]
    return (TSV_HEADER + "".join(rows)).replace("\n", newline)


@pytest.mark.parametrize(
    "forced",
    (
        "cp1252",
        pytest.param(
            None,
            id="this-machine",
            marks=pytest.mark.skipif(
                sys.platform != "win32",
                reason="this machine's own encoding is cp1252 only on Windows",
            ),
        ),
    ),
)
@pytest.mark.parametrize("text", (CAFE, CAFE_I), ids=("cafe", "cafe-I"))
def test_recognized_text_is_read_as_utf8(
    tmp_path: Path, text: str, forced: str | None
) -> None:
    """The exact text Tesseract recognized, never its UTF-8 read as cp1252."""
    tsv = _tsv(*text.split(" "))
    stand_in = _stand_in(tmp_path, stdout=tsv.encode("utf-8"))
    report = _run_child(tmp_path, stand_in, "page", forced)
    if forced is None and report["encoding"].lower().replace("-", "") == "utf8":
        pytest.skip("this Windows uses UTF-8 as its ANSI code page")
    old = report["control"].get("value", {}).get("stdout")
    assert old != tsv, f"the child did not reproduce the old decoding: {report}"
    assert report["bridge"] == {"value": {"text": text, "tsv": tsv}}


def test_what_tesseract_printed_is_read_as_utf8(tmp_path: Path) -> None:
    """Real Tesseract output, replayed: the TSV it printed, word for word."""
    printed = RECORDED.read_bytes()
    stand_in = _stand_in(tmp_path, stdout=printed)
    report = _run_child(tmp_path, stand_in, "page")
    old = report["control"].get("value", {}).get("stdout")
    assert old is not None and f"Caf{E_ACUTE}" not in old, report
    assert report["bridge"] == {
        "value": {"text": f"Caf{E_ACUTE} indice", "tsv": printed.decode("utf-8")}
    }


def test_newlines_are_translated_as_before(tmp_path: Path) -> None:
    """The published TSV keeps the text `text=True` gave it: CRLF becomes LF.

    ASCII, so this holds before P91 as well as after it.
    """
    tsv = _tsv("Tesseract", newline="\r\n").encode("ascii")
    stand_in = _stand_in(tmp_path, stdout=tsv)
    report = _run_child(tmp_path, stand_in, "page")
    assert report["bridge"] == {
        "value": {"text": "Tesseract", "tsv": _tsv("Tesseract")}
    }


def test_bytes_that_are_not_utf8_are_an_error_not_a_replacement(
    tmp_path: Path,
) -> None:
    """A recognized word that is not UTF-8 fails that image; it is never
    rewritten into a character Tesseract did not print.

    On the web path that failure is the asset's `OcrRecognitionError`, which
    the visual stage records as one warning (docs/09 P90).
    """
    tsv = _tsv("caf").encode("utf-8").replace(b"caf\n", b"caf\xff\n")
    stand_in = _stand_in(tmp_path, stdout=tsv)
    report = _run_child(tmp_path, stand_in, "page")
    assert "value" in report["control"], report
    assert (
        "caf\N{LATIN SMALL LETTER Y WITH DIAERESIS}"
        in (report["control"]["value"]["stdout"])
    ), "cp1252 reads FF as a letter, which is why this needs saying"
    assert report["bridge"].get("error") == "UnicodeDecodeError", report

    asset = _run_child(tmp_path, stand_in, "asset")["bridge"]
    assert asset.get("error") == "OcrRecognitionError", asset
    assert asset["message"].startswith(
        "Tesseract's output could not be decoded: 'utf-8' codec can't decode "
        "byte 0xff in position "
    ), asset


def test_diagnostics_on_a_successful_run_cannot_fail_it(tmp_path: Path) -> None:
    """Tesseract's stderr is read only when it failed: a successful page is not
    lost to the bytes of a warning, in any encoding."""
    stand_in = _stand_in(
        tmp_path,
        stdout=_tsv(CAFE).encode("utf-8"),
        stderr=b"Warning: \xff\x8d\n",
    )
    report = _run_child(tmp_path, stand_in, "page")
    assert _lost_to_the_old_decoding(report, "stderr"), report
    assert report["bridge"] == {"value": {"text": CAFE, "tsv": _tsv(CAFE)}}


def test_a_failure_says_what_tesseract_said(tmp_path: Path) -> None:
    """A failed run reports Tesseract's own message: UTF-8 decoded, and a byte
    that is not UTF-8 shown as an escape rather than raising in its place."""
    message = f"cannot read {CAPITAL_I_ACUTE}ndice/Jos".encode() + b"\xe9.png\n"
    stand_in = _stand_in(tmp_path, stdout=b"", stderr=message, status=1)
    report = _run_child(tmp_path, stand_in, "page")
    assert report["bridge"] == {
        "error": "RuntimeError",
        "message": f"cannot read {CAPITAL_I_ACUTE}ndice/Jos\\xe9.png",
    }


@pytest.mark.parametrize(
    ("header_path", "old_decoding_fails"),
    (
        (f"C:\\{CAPITAL_I_ACUTE}ndice\\tessdata\\".encode(), True),
        (b"C:\\Jos\xe9\\tessdata\\", False),
    ),
    ids=("utf8-path", "ansi-path"),
)
@pytest.mark.parametrize("call", ("languages", "doctor"))
def test_the_language_list_is_read_whatever_its_header_holds(
    tmp_path: Path, header_path: bytes, old_decoding_fails: bool, call: str
) -> None:
    """`--list-langs` names its data directory before the codes. A byte there
    that is not UTF-8, or not cp1252, must not cost the language check, in
    the capture's preflight or in `webshot doctor`."""
    listing = b'List of available languages in "' + header_path + b'" (2):\neng\nspa\n'
    stand_in = _stand_in(tmp_path, stdout=listing)
    report = _run_child(tmp_path, stand_in, call)
    if old_decoding_fails:
        assert _lost_to_the_old_decoding(report, "stdout"), report
    assert report["bridge"] == {"value": ["eng", "spa"]}
