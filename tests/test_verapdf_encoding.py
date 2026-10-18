"""veraPDF's output is read as the UTF-8 it is, whatever the locale says.

veraPDF writes its report in UTF-8, and the report names the file it
validated. The gate ran it with `text=True` and no encoding, so `subprocess`
decoded with the locale's encoding unless Python ran in UTF-8 mode: on
Windows the ANSI code page, cp1252 on an English install. A report naming
"Café.pdf" was read as naming "CafÃ©.pdf" and published so, and one naming
"Índice.pdf", whose UTF-8 holds a byte cp1252 does not define, raised out of
`validate` (docs/09 P91-4). The Docker probe beside it decoded `docker info`
the same way, for nothing: only its exit status is read.

Each test runs the gate in a child interpreter with UTF-8 mode off and the
locale's encoding set to cp1252, which is what `subprocess` asks on a Windows
interpreter, against a stand-in veraPDF (or Docker) that prints fixed bytes,
found first on the child's PATH. Where the old decoding got those bytes
wrong, the child also decodes them that way, as a control: a child that no
longer reproduces the old decoding has measured nothing, and the test fails
rather than passing on it. One variant runs with the machine's own encoding
and no override; it means something only on Windows, so it runs only there.
tests/test_tesseract_encoding.py is the same harness for Tesseract (P91).
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

#: What veraPDF 1.30.0 printed on macOS for "Café Índice.pdf", a blank page
#: whose XMP claims PDF/A-3B: `verapdf --format json --flavour 3b <file>`. It
#: names the file by its absolute path; the directory is written here as
#: `/data/`, the one the `verapdf/cli` container is given, in place of the
#: recording machine's, and nothing else is changed. "é" is C3 A9 and "Í" is
#: C3 8D, and cp1252 defines no character at 8D.
RECORDED = Path(__file__).parent / "fixtures" / "verapdf" / "cafe-indice.json"
RECORDED_NAME = f"/data/Caf{E_ACUTE} {CAPITAL_I_ACUTE}ndice.pdf"
RULE_6_1_3 = (
    "6.1.3: The file trailer dictionary shall contain the ID keyword whose "
    "value shall be File Identifiers as defined in ISO 32000-1:2008, 14.4"
)

#: The XMP of a file that claims PDF/A-3B, so that `validate` runs veraPDF.
PDFA_XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta '
    b'xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" '
    b'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
    b'pdfaid:part="3" pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>'
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
#: interpreter decodes as a Windows one does. PATH holds only the stand-ins,
#: so the gate finds no other veraPDF and no Docker unless a test gives it
#: one. The report is JSON with ASCII escapes, so the child's own stdout
#: encoding cannot touch it.
CHILD = """\
import json, locale, os, subprocess, sys
from pathlib import Path

bin_dir, work, forced, call = sys.argv[1:5]
os.environ["PATH"] = bin_dir
if forced:
    locale.getencoding = lambda: forced

from webshot.validate import verapdf


def attempt(function):
    try:
        return {"value": function()}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


def validate():
    report_path = Path(work) / "out" / "verapdf.json"
    report = verapdf.validate(Path(work) / "capture.pdf", report_path=report_path)
    return {
        "tool": report.tool,
        "compliant": report.compliant,
        "passed_rules": report.passed_rules,
        "failed_rules": report.failed_rules,
        "failures": list(report.failures),
        "detail": report.detail,
        "report_file": report.report_file is not None,
        "written": report_path.read_text("utf-8") if report_path.exists() else None,
    }


def docker():
    return verapdf.docker_daemon_available()


def old_decoding():
    # The gate's call before P91-4: text=True and no encoding.
    stand_in = os.path.join(bin_dir, "docker" if call == "docker" else "verapdf")
    if sys.platform == "win32":
        stand_in += ".cmd"
    completed = subprocess.run([stand_in], capture_output=True, text=True, check=False)
    return {"stdout": completed.stdout, "stderr": completed.stderr}


calls = {"validate": validate, "docker": docker}
print(
    json.dumps(
        {
            "encoding": locale.getencoding(),
            "utf8_mode": sys.flags.utf8_mode,
            "gate": attempt(calls[call]),
            "control": attempt(old_decoding),
        }
    )
)
"""


def _stand_in(
    bin_dir: Path, name: str, *, stdout: bytes, stderr: bytes = b"", status: int = 0
) -> Path:
    """A `name` on PATH that prints *stdout* and *stderr* and exits with *status*."""
    home = bin_dir.parent / f"{name}-stand-in"
    home.mkdir(parents=True, exist_ok=True)
    (home / "stdout.bin").write_bytes(stdout)
    (home / "stderr.bin").write_bytes(stderr)
    script = home / "stand_in.py"
    script.write_text(STAND_IN.format(status=status), encoding="utf-8")
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        wrapper = bin_dir / f"{name}.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        wrapper = bin_dir / name
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return wrapper


def _capture_claiming_pdfa(work: Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(PDFA_XMP)
    stream[NameObject("/Type")] = NameObject("/Metadata")
    stream[NameObject("/Subtype")] = NameObject("/XML")
    writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    with (work / "capture.pdf").open("wb") as handle:
        writer.write(handle)


def _run_child(
    work: Path, call: str = "validate", forced: str | None = "cp1252"
) -> dict[str, Any]:
    # The child runs in the work directory: the repository root holds a
    # `webshot.py` that would otherwise be imported in place of the package.
    if call == "validate":
        _capture_claiming_pdfa(work)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8=0",
            "-c",
            CHILD,
            str(work / "bin"),
            str(work),
            forced or "",
            call,
        ],
        capture_output=True,
        check=False,
        timeout=120,
        cwd=work,
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
    `None` (docs/09 P91).
    """
    control = report["control"]
    if "error" in control:
        return bool(control["error"] == "UnicodeDecodeError")
    return control["value"][stream] is None


def _gate(report: dict[str, Any]) -> dict[str, Any]:
    """What `validate` returned, failing with what it raised instead."""
    assert "value" in report["gate"], report["gate"]
    value: dict[str, Any] = report["gate"]["value"]
    return value


def _report_naming(name: str) -> bytes:
    """The recorded report, naming *name* instead of the file it named."""
    recorded = RECORDED.read_bytes()
    return recorded.replace(RECORDED_NAME.encode("utf-8"), name.encode("utf-8"))


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
@pytest.mark.parametrize(
    "name",
    (f"/data/Caf{E_ACUTE}.pdf", RECORDED_NAME),
    ids=("cafe", "cafe-I"),
)
def test_the_report_is_read_as_utf8(
    tmp_path: Path, name: str, forced: str | None
) -> None:
    """The verdict, and the published report naming the file as veraPDF did,
    never its UTF-8 read as cp1252. "Café" decodes under cp1252 to the wrong
    text with no error; "Í" does not decode."""
    printed = _report_naming(name)
    _stand_in(tmp_path / "bin", "verapdf", stdout=printed, status=1)
    report = _run_child(tmp_path, forced=forced)
    if forced is None and report["encoding"].lower().replace("-", "") == "utf8":
        pytest.skip("this Windows uses UTF-8 as its ANSI code page")
    old = report["control"].get("value", {}).get("stdout")
    assert old != printed.decode("utf-8"), (
        f"the child did not reproduce the old decoding: {report}"
    )
    gate = _gate(report)
    assert gate["written"] == printed.decode("utf-8")
    assert (
        json.loads(gate["written"])["report"]["jobs"][0]["itemDetails"]["name"] == name
    )
    assert (gate["tool"], gate["compliant"]) == ("verapdf", False)
    assert (gate["passed_rules"], gate["failed_rules"]) == (145, 1)
    assert gate["failures"] == [RULE_6_1_3]
    assert gate["detail"] == "PDF/A-3b validation profile"


def test_what_verapdf_printed_is_read_as_utf8(tmp_path: Path) -> None:
    """Real veraPDF output, replayed: the report it printed, byte for byte."""
    printed = RECORDED.read_bytes()
    _stand_in(tmp_path / "bin", "verapdf", stdout=printed, status=1)
    report = _run_child(tmp_path)
    assert _lost_to_the_old_decoding(report, "stdout"), report
    gate = _gate(report)
    assert gate["written"] == printed.decode("utf-8")
    assert gate["failures"] == [RULE_6_1_3]
    assert gate["report_file"] is True


def test_newlines_are_translated_as_before(tmp_path: Path) -> None:
    """The published report keeps the text `text=True` gave it: CRLF becomes LF.

    ASCII, so this holds before P91-4 as well as after it.
    """
    printed = _report_naming("/data/capture.pdf")
    _stand_in(
        tmp_path / "bin",
        "verapdf",
        stdout=printed.replace(b"\n", b"\r\n"),
        status=1,
    )
    report = _run_child(tmp_path)
    assert _gate(report)["written"] == printed.decode("ascii")


def test_bytes_that_are_not_utf8_are_an_attempt_that_failed(
    tmp_path: Path,
) -> None:
    """A report that is not UTF-8 is not veraPDF's report: no verdict is read
    from it, nothing is published, and the detail says why. It is never
    rewritten into a character veraPDF did not print."""
    printed = RECORDED.read_bytes().replace(
        RECORDED_NAME.encode("utf-8"), b"/data/caf\xff.pdf"
    )
    assert b"/data/caf\xff.pdf" in printed
    _stand_in(tmp_path / "bin", "verapdf", stdout=printed, status=1)
    report = _run_child(tmp_path)
    assert "value" in report["control"], report
    assert (
        "caf\N{LATIN SMALL LETTER Y WITH DIAERESIS}.pdf"
        in report["control"]["value"]["stdout"]
    ), "cp1252 reads FF as a letter, which is why this needs saying"
    gate = _gate(report)
    assert (gate["tool"], gate["compliant"], gate["written"]) == (None, None, None)
    assert gate["detail"].startswith(
        "verapdf exited 1 with a report that is not UTF-8: 'utf-8' codec can't "
        "decode byte 0xff in position "
    ), gate


def test_diagnostics_on_a_successful_run_cannot_fail_it(tmp_path: Path) -> None:
    """veraPDF's stderr is read only when it printed no report: a verdict is
    not lost to the bytes of a warning, in any encoding. The report is ASCII,
    so only stderr is at stake."""
    printed = _report_naming("/data/capture.pdf")
    _stand_in(
        tmp_path / "bin",
        "verapdf",
        stdout=printed,
        stderr=b"WARNING: \xff\x8d\n",
        status=1,
    )
    report = _run_child(tmp_path)
    assert _lost_to_the_old_decoding(report, "stderr"), report
    assert _gate(report)["failures"] == [RULE_6_1_3]


def test_a_failure_says_what_verapdf_said(tmp_path: Path) -> None:
    """A run with no report gives veraPDF's own last line: UTF-8 decoded, and
    a byte that is not UTF-8 shown as an escape rather than raising in its
    place."""
    message = (
        b"SEVERE: /data/Jos\xe9/"
        + f"Caf{E_ACUTE} {CAPITAL_I_ACUTE}ndice.pdf".encode()
        + b" doesn't appear to be a valid PDF.\n"
    )
    _stand_in(tmp_path / "bin", "verapdf", stdout=b"", stderr=message, status=7)
    report = _run_child(tmp_path)
    assert _lost_to_the_old_decoding(report, "stderr"), report
    gate = _gate(report)
    assert (gate["tool"], gate["compliant"]) == (None, None)
    assert gate["detail"] == (
        "verapdf exited 7 without a JSON report: SEVERE: /data/Jos\\xe9/"
        f"Caf{E_ACUTE} {CAPITAL_I_ACUTE}ndice.pdf doesn't appear to be a valid PDF."
    )


@pytest.mark.parametrize(("status", "available"), ((0, True), (1, False)))
def test_the_docker_probe_reads_only_the_exit_status(
    tmp_path: Path, status: int, available: bool
) -> None:
    """`docker info` names the host and the context; a byte there that the
    locale cannot decode must not cost the gate its container backend, in
    `validate` or in `webshot doctor`."""
    printed = f"Name: {CAPITAL_I_ACUTE}ndice\n".encode()
    _stand_in(tmp_path / "bin", "docker", stdout=printed, status=status)
    report = _run_child(tmp_path, "docker")
    assert _lost_to_the_old_decoding(report, "stdout"), report
    assert report["gate"] == {"value": available}
