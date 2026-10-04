"""docs/04-spec.md §3: one exit code per failure, and v3 never exits 1.

Phase 5.1's audit is only real if every code in the table is exercised, so this
file covers all ten.  Seven of them — 0, 2, 3, 4, 6, 8, 9 — are reached by
running the actual command line against a fixture or a loopback server, which
is the only kind of proof that also covers the wiring between the pipeline, the
CLI and the QA report.  The two that a faithful trigger cannot reach without
either a real SharePoint tenant (5) or a deliberately corrupt PDF/A (7) are
fault-injected at the real function that classifies them.

The second half of the audit is the QA report: before Phase 5 a failing run
wrote no report, so `exit_code` was 0 in every report that existed.  Every case
below that passes `--report` asserts the file exists, carries the process's own
code, and says why.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import http.server
import json
import os
import signal
import ssl
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import webshot.ocr.tesseract as tesseract_module
from conftest import serve
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.errors import (
    EXIT_CODES,
    AuthenticationError,
    BundleBuildError,
    CaptureIntegrityError,
    EnvironmentFailure,
    NavigationError,
    OcrUnavailableError,
    PdfValidationError,
    UsageError,
    WebShotError,
    certificate_hint,
    classify,
)
from webshot.ocr.engine import resolve_engine
from webshot.outputlock import OutputLock, lock_path
from webshot.pipeline import _require_ocr_or_fail

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = str(REPO_ROOT / "tests" / "fixtures" / "sample_notes.txt")

#: Which class owns each documented code. Reading it the other way round is the
#: point: a code someone adds to the taxonomy without an error class to raise it
#: is a code nothing can ever return.
CODE_OWNERS: dict[int, type[WebShotError]] = {
    2: UsageError,
    3: NavigationError,
    4: AuthenticationError,
    5: CaptureIntegrityError,
    6: OcrUnavailableError,
    7: PdfValidationError,
    8: BundleBuildError,
    9: EnvironmentFailure,
}


# --------------------------------------------------------------------------- #
# The taxonomy itself
# --------------------------------------------------------------------------- #


def test_every_documented_code_has_a_class_that_returns_it() -> None:
    assert set(EXIT_CODES) == {0, *CODE_OWNERS}
    for code, error in CODE_OWNERS.items():
        assert error.exit_code == code, f"{error.__name__} does not return {code}"


def test_one_is_never_the_answer() -> None:
    """The migration note in spec §3: v2.2's blanket 1 is gone, deliberately."""
    assert 1 not in EXIT_CODES
    assert all(error.exit_code != 1 for error in CODE_OWNERS.values())
    assert WebShotError.exit_code != 1
    for exception in (
        RuntimeError("unexpected"),
        OSError("disk"),
        ValueError("nonsense"),
        KeyError("missing"),
    ):
        assert classify(exception) != 1


def test_no_module_raises_the_unclassified_base_class() -> None:
    """`WebShotError` is the base, not a choice — every raise names its kind.

    v2.2 exited 1 for everything and the migration ran a path at a time, so the
    bare base class is exactly what an unmigrated path looked like.  Scanning
    for it is what stops the next one from being written by habit.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(raised, ast.Name) and raised.id == "WebShotError":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "these raises use the unclassified base class instead of a spec §3 "
        f"code: {offenders}"
    )


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (UsageError("bad flag"), 2),
        (NavigationError("timed out"), 3),
        (EnvironmentFailure("no tesseract"), 9),
        (OSError("permission denied"), 9),
        (RuntimeError("something else"), 5),
        (ImportError("cannot import name 'x'"), 9),
        (ModuleNotFoundError("No module named 'playwright'"), 9),
    ],
)
def test_classify_maps_an_uncaught_exception_onto_the_taxonomy(
    exception: BaseException, expected: int
) -> None:
    assert classify(exception) == expected


def test_a_dependency_that_fails_at_first_use_is_an_environment_failure() -> None:
    """The exit code the lazy CLI made reachable (docs/09 P10-6).

    `cli.py` imports its heavy stages inside the functions that use them, so a
    dependency that is missing or unloadable now surfaces when the stage runs
    rather than when `webshot.cli` is imported.  That is the better moment —
    but it is only an improvement if the failure keeps a code from the §3
    taxonomy.  Before the deferral it was not a WebShot exit code at all: the
    ImportError escaped `main` as a traceback and Python's own 1, which §3
    forbids outright.

    `ModuleNotFoundError` is checked beside `ImportError` because it is the
    subclass a missing dependency actually raises, and a guard written for the
    parent that misses the child is the half-a-fix shape of P7-12.
    """
    assert classify(ImportError("libc++ not found")) == EnvironmentFailure.exit_code
    assert (
        classify(ModuleNotFoundError("No module named 'pypdf'"))
        == EnvironmentFailure.exit_code
    )
    # Not 1, which is the property the whole taxonomy exists to hold.
    assert classify(ImportError("anything")) != 1


def test_a_document_reader_that_cannot_load_is_an_environment_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deferred MarkItDown import keeps the code P10-6 gave every stage.

    `acquire/adapters.py` imports its MarkItDown bridge only when a local
    document is read through it (docs/09 P10-15), so a reader that is missing
    or unloadable now fails mid-capture instead of when `webshot.pipeline` is
    imported.  `classify` already answers an ImportError with 9; what this
    holds is that the error *reaches* it.  The pipeline's handler around
    `materialize_source` turns `ValueError` into 2 and `OSError` into 9, and a
    broadened clause there would re-label this one as the caller's mistake.

    Through `main`, for the reason
    `test_an_exception_nobody_foresaw_still_lands_in_the_taxonomy` gives:
    `classify` tested alone cannot see what never reaches it.
    """
    # The bridge may already be loaded in this process, and a loaded module is
    # never re-imported; removing it makes the deferred import execute for real
    # and meet the missing dependency, as it would on a broken install.
    monkeypatch.delitem(sys.modules, "webshot.acquire.markitdown_bridge", raising=False)
    monkeypatch.setitem(sys.modules, "markitdown", None)
    output = tmp_path / "unreadable.pdf"
    code, written = _run(tmp_path, FIXTURE, "--output", str(output), "--no-ocr")
    # The message first: it proves the run failed at the import under test, and
    # not somewhere else that happens to share the code.
    assert "markitdown" in str(written["error"]), written
    assert code == EnvironmentFailure.exit_code
    assert written["exit_code"] == code
    assert not output.exists()


def test_classify_reads_playwrights_own_failures() -> None:
    """The two shapes the browser reports, mapped where the meaning is known."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    assert classify(PlaywrightTimeoutError("Timeout 30000ms exceeded")) == 3
    assert classify(PlaywrightError("net::ERR_NAME_NOT_RESOLVED at http://x/")) == 3
    # A Playwright error that is not a network one is a capture that went wrong
    # rather than a page that was never reached.
    assert classify(PlaywrightError("Element is not attached to the DOM")) == 5


# --------------------------------------------------------------------------- #
# Codes reached by running the command line
# --------------------------------------------------------------------------- #


def _run(tmp_path: Path, *argv: str) -> tuple[int, dict[str, object]]:
    """Run the real CLI with `--report`, and read the report back.

    Every case in this file wants both halves — the process's code and the file
    it wrote — because the point of the audit is that the two agree. Returning
    the path instead would make every caller spell the same read.
    """
    destination = tmp_path / "report.json"
    code = main([*argv, "--report", str(destination)])
    assert destination.is_file(), "a run given --report must write it, pass or fail"
    return code, json.loads(destination.read_text(encoding="utf-8"))


@pytest.mark.browser
def test_zero_is_a_capture_that_happened(tmp_path: Path) -> None:
    code, written = _run(
        tmp_path, FIXTURE, "--output", str(tmp_path / "ok.pdf"), "--no-ocr"
    )
    assert code == 0
    assert written["exit_code"] == 0
    # The absence *is* the signal: a successful report carries no `error` key
    # at all, so `"error" in report` is the whole test a consumer needs.
    assert "error" not in written
    assert (tmp_path / "ok.pdf").is_file()


def test_two_is_an_impossible_request(tmp_path: Path) -> None:
    code, written = _run(
        tmp_path,
        FIXTURE,
        "--output",
        str(tmp_path / "x.pdf"),
        "--pdfa",
    )
    assert code == 2
    assert written["exit_code"] == 2
    assert "--pdfa" in str(written["error"])
    # The options never resolved, so the report says null rather than
    # inventing a plausible-looking set (docs/04-spec.md §2.3).
    assert written["options"] is None
    assert written["counts"] is None
    assert written["validation"] is None


def test_two_is_a_local_file_that_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mistyped `./typo.csv` used to be fetched as `https://./typo.csv`, exit 3.

    The whole command line, not only `normalize_source`: the code a script sees
    and the code the QA report records are the two that have to say 2.
    """
    monkeypatch.chdir(tmp_path)
    code, written = _run(tmp_path, "./typo.csv", "--output", str(tmp_path / "x.pdf"))
    assert code == 2
    assert written["exit_code"] == 2
    assert written["error"] == "No such file: ./typo.csv."


def test_two_covers_the_require_ocr_contradiction(tmp_path: Path) -> None:
    """spec §1.1: `--require-ocr` with `--no-ocr` is contradictory, not merely odd."""
    code, written = _run(
        tmp_path,
        FIXTURE,
        "--output",
        str(tmp_path / "x.pdf"),
        "--require-ocr",
        "--no-ocr",
    )
    assert code == 2
    assert "contradict" in str(written["error"])


class _Handler(http.server.BaseHTTPRequestHandler):
    """A page that never answers, and a page that demands a sign-in."""

    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            time.sleep(30)
            return
        self.send_response(401)
        self.send_header("Content-Type", "text/html")
        self.send_header("WWW-Authenticate", 'Basic realm="webshot-test"')
        self.end_headers()
        self.wfile.write(b"<html><body>Please sign in.</body></html>")

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def loopback() -> Iterator[str]:
    """A server on 127.0.0.1 — no test may touch the public internet (docs/06)."""
    with serve(_Handler) as origin:
        yield origin


@pytest.fixture
def no_browsers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment where Playwright cannot find a Chromium."""
    empty = tmp_path / "no-browsers"
    empty.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))


@pytest.fixture
def no_tesseract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with no Tesseract binary, which is what --require-ocr is for.

    An empty PATH rather than a patched predicate: the flag exists for a real
    machine that turns out not to have the binary, and that is what this is.
    The module-level cache is cleared because it survives between tests, and a
    previous test that *found* Tesseract would otherwise mask this one.
    """
    monkeypatch.setattr(tesseract_module, "_TESSERACT", None)
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries-here"))


@pytest.mark.browser
def test_three_is_a_page_that_never_arrived(tmp_path: Path, loopback: str) -> None:
    code, written = _run(
        tmp_path,
        f"{loopback}/slow",
        "--output",
        str(tmp_path / "slow.pdf"),
        "--timeout",
        "2",
        "--no-ocr",
    )
    assert code == 3
    assert written["exit_code"] == 3
    assert "did not load" in str(written["error"])
    # Everything a failure genuinely knows is published; everything it does not
    # is null rather than zero.
    assert written["options"] is not None
    assert written["pages"] is None
    assert written["title"] is None


@pytest.mark.parametrize(
    "detail",
    [
        "Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://intranet.example/",
        "Page.goto: net::ERR_CERT_DATE_INVALID at https://intranet.example/",
    ],
)
def test_a_refused_certificate_names_the_proxy_as_a_suspect(detail: str) -> None:
    """A user behind a TLS-inspecting proxy saw only the Chromium error code."""
    hint = certificate_hint(detail)
    assert hint.startswith("\n"), "the hint is its own line, after the call log"
    assert hint.count("\n") == 1
    assert "proxy" in hint and "NSS store" in hint and "curl" in hint


@pytest.mark.parametrize(
    "detail",
    [
        "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://intranet.example/",
        "Page.goto: net::ERR_CONNECTION_REFUSED at https://127.0.0.1:9/",
        "",
    ],
)
def test_other_network_failures_get_no_certificate_hint(detail: str) -> None:
    assert certificate_hint(detail) == ""


class _PlainPage(http.server.BaseHTTPRequestHandler):
    # The handshake runs on this thread's first read; a connection the browser
    # opened and abandoned must not hold it for the rest of the session.
    timeout = 10

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Behind a certificate.</body></html>")

    def log_message(self, *args: object) -> None:
        return


class _SelfSignedServer(http.server.ThreadingHTTPServer):
    """HTTPS on 127.0.0.1 with a certificate no browser trusts.

    The handshake waits for the handler thread, so a browser that hangs up on
    the certificate cannot block the accept loop, and `shutdown` cannot hang;
    `server_close` does not wait for handler threads for the same reason.
    """

    block_on_close = False

    def __init__(self, context: ssl.SSLContext) -> None:
        super().__init__(("127.0.0.1", 0), _PlainPage)
        self.socket = context.wrap_socket(
            self.socket, server_side=True, do_handshake_on_connect=False
        )

    def handle_error(self, request: object, client_address: object) -> None:
        # The browser refusing the certificate mid-handshake is the point of
        # the server, not a failure of it.
        return


@pytest.fixture
def self_signed_origin(tmp_path: Path) -> Iterator[str]:
    """`cryptography` rather than an `openssl` binary: it ships with every install.

    OCRmyPDF needs it through pdfminer.six, so the Windows and macOS runners
    have it too, where an `openssl` on PATH is not a given.
    """
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "loopback.crt", tmp_path / "loopback.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    httpd = _SelfSignedServer(context)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.browser
def test_three_with_a_certificate_hint_for_an_untrusted_certificate(
    tmp_path: Path, self_signed_origin: str
) -> None:
    """The real Chromium string, so the hint's trigger is the one users see."""
    code, written = _run(
        tmp_path,
        f"{self_signed_origin}/",
        "--output",
        str(tmp_path / "tls.pdf"),
        "--timeout",
        "20",
        "--no-ocr",
    )
    error = str(written["error"])
    assert code == 3, error
    assert written["exit_code"] == 3
    assert "net::ERR_CERT_" in error, error
    assert error.endswith(certificate_hint(error)), error
    assert certificate_hint(error), error


@pytest.mark.browser
def test_four_is_the_far_end_asking_who_you_are(tmp_path: Path, loopback: str) -> None:
    """401 and 403 are the one HTTP class that is about credentials, not routing."""
    code, written = _run(
        tmp_path,
        f"{loopback}/private",
        "--output",
        str(tmp_path / "private.pdf"),
        "--no-ocr",
    )
    assert code == 4
    assert written["exit_code"] == 4
    assert "--auth-profile" in str(written["error"])


@pytest.mark.browser
def test_six_is_require_ocr_with_nothing_to_recognize_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag spec §1.1 documented and Phase 5.1 implemented.

    PATH rather than a patched predicate: `--require-ocr` exists for a machine
    that turns out not to have Tesseract, and that is what an empty PATH is.
    """
    monkeypatch.setattr(tesseract_module, "_TESSERACT", None)
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries-here"))
    code, written = _run(
        tmp_path,
        FIXTURE,
        "--output",
        str(tmp_path / "ocr.pdf"),
        "--require-ocr",
    )
    assert code == 6
    assert written["exit_code"] == 6
    assert "--require-ocr" in str(written["error"])
    # It refuses before the browser opens: paying for a capture that cannot be
    # used is the thing the flag exists to avoid.
    assert not (tmp_path / "ocr.pdf").exists()


def test_without_require_ocr_a_missing_engine_stays_a_warning(
    tmp_path: Path, no_tesseract: None
) -> None:
    """spec §1.1: without the flag, degradation is a manifest warning.

    The gate is checked in the negative as well, because a guard that fires
    when it was not asked for would change the default behaviour of every
    capture on a machine without Tesseract.
    """
    options = CaptureOptions(source="x", output=tmp_path / "x.pdf")

    asyncio.run(_require_ocr_or_fail(options, resolve_engine("tesseract")))


def test_a_destination_that_cannot_be_published_to_is_refused_early(
    tmp_path: Path,
) -> None:
    """spec §3 code 2, and it is refused *before* the browser opens.

    This used to reach exit 8, because the bundle destination was only
    discovered to be impossible once the bundle was being written — after a
    full capture. §5.1's lock now touches every published path up front, so a
    destination whose parent is a regular file is a usage error the caller
    learns about immediately (docs/09 P5-10).
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("a regular file where a directory has to be", encoding="utf-8")
    code, written = _run(
        tmp_path,
        FIXTURE,
        "--output",
        str(tmp_path / "bundle.pdf"),
        "--no-ocr",
        "--ai-bundle-dir",
        str(blocker / "nested.ai"),
    )
    assert code == 2
    assert written["exit_code"] == 2
    assert "cannot be published to" in str(written["error"])
    assert not (tmp_path / "bundle.pdf").exists()


@pytest.mark.browser
def test_eight_is_a_bundle_that_could_not_be_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §3 code 8, on the real publication boundary with the fault injected.

    A faithful trigger is no longer reachable from the command line: every
    filesystem-shaped destination failure is now caught by the lock before the
    capture starts, which is a better outcome and leaves this code reachable
    only from a failure *during* publication — a disk that fills between the
    staging write and the swap. So the swap is what is injected; everything
    around it, including the rollback, is real.
    """
    from webshot import pipeline

    def full_disk(result: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pipeline, "publish_ai_bundle", full_disk)
    output = tmp_path / "unpublishable.pdf"
    code, written = _run(tmp_path, FIXTURE, "--output", str(output), "--no-ocr")
    assert code == 8
    assert written["exit_code"] == 8
    assert "rolled back" in str(written["error"])
    # Rolled back means rolled back: no half-published PDF left behind.
    assert not output.exists()


@pytest.mark.browser
def test_nine_is_a_missing_tool(tmp_path: Path, no_browsers: None) -> None:
    """`webshot doctor`'s job: an environment that cannot run a capture at all."""
    code, written = _run(
        tmp_path, FIXTURE, "--output", str(tmp_path / "no.pdf"), "--no-ocr"
    )
    assert code == 9
    assert written["exit_code"] == 9
    assert "playwright install" in str(written["error"])


# --------------------------------------------------------------------------- #
# Codes reached by injecting the fault their real trigger needs
# --------------------------------------------------------------------------- #


def test_five_is_a_composition_that_does_not_add_up(tmp_path: Path) -> None:
    """spec §2.2's page-count invariant, exercised on the real checker.

    A faithful trigger needs a SharePoint viewer that lies about its page
    count.  What can be reproduced exactly is the check itself, which is the
    part that has to classify: a page directory that does not hold the pages
    the viewer promised.
    """
    from webshot.protected.assemble import _verified_pages

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(CaptureIntegrityError) as caught:
        _verified_pages(pages, 3)
    assert caught.value.exit_code == 5
    assert classify(caught.value) == 5

    # And the other half of the same invariant: a sequence with a hole in it.
    (pages / "page-003.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(CaptureIntegrityError):
        _verified_pages(pages, 2)


def test_seven_is_a_pdf_that_cannot_be_published(tmp_path: Path) -> None:
    """The render-side half of code 7, on the real pre-publication gate."""
    from webshot.render.pdf import validate_pdf

    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-1.7\n" + b"0" * 64)
    with pytest.raises(PdfValidationError) as caught:
        validate_pdf(truncated)
    assert caught.value.exit_code == 7


@pytest.mark.browser
def test_seven_is_also_a_strict_conformance_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--validate-pdf=strict`, and the report a post-publication failure earns.

    veraPDF's verdict is injected rather than provoked — producing a genuinely
    non-conformant PDF/A on demand means corrupting one, and the thing under
    test is what WebShot does with the answer.  What is *not* injected is the
    rest: a real capture is published, and the report is the real one.
    """
    import webshot.pipeline as pipeline
    from webshot.validate.verapdf import ValidationReport

    non_conformant = ValidationReport(
        tool="veraPDF (injected verdict)",
        flavour="PDF/A-3B",
        compliant=False,
        passed_rules=145,
        failed_rules=1,
        failed_checks=1,
        failures=("6.1.2: the document catalog has no valid metadata",),
        report_file=str(tmp_path / "strict.verapdf.json"),
    )
    monkeypatch.setattr(
        pipeline, "verapdf_validate", lambda *args, **kwargs: non_conformant
    )
    code, written = _run(
        tmp_path,
        FIXTURE,
        "--output",
        str(tmp_path / "strict.pdf"),
        "--no-ocr",
        "--validate-pdf=strict",
    )
    assert code == 7
    assert written["exit_code"] == 7
    assert "not PDF/A-3B conformant" in str(written["error"])
    # The file *was* published and the gate *did* run, so unlike every other
    # failure this report is a complete record of a capture plus its verdict.
    assert (tmp_path / "strict.pdf").is_file()
    assert written["pages"] == 1
    validation = written["validation"]
    assert isinstance(validation, dict)
    assert validation["pdfa"] is not None


def test_the_taxonomy_and_the_spec_table_agree() -> None:
    """The table in docs/04-spec.md §3 is normative, so it is checked.

    A code that the specification lists and the code does not return — or the
    reverse — is the drift this whole audit exists to remove.
    """
    spec = (REPO_ROOT / "docs" / "04-spec.md").read_text(encoding="utf-8")
    section = spec.split("## 3. Exit codes")[1].split("## 4.")[0]
    documented = {
        int(line.split("|")[1].strip())
        for line in section.splitlines()
        if line.startswith("|") and line.split("|")[1].strip().isdigit()
    }
    assert documented == set(EXIT_CODES)


def test_a_report_destination_that_cannot_be_written_does_not_change_the_code(
    tmp_path: Path, no_browsers: None
) -> None:
    """Reporting a failure must never replace it with a different one."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert (
        main(
            [
                FIXTURE,
                "--output",
                str(tmp_path / "x.pdf"),
                "--no-ocr",
                "--report",
                str(blocker / "nested" / "report.json"),
            ]
        )
        == 9
    )


def test_an_exception_nobody_foresaw_still_lands_in_the_taxonomy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of `classify`, exercised through `main` rather than alone.

    Catching a hand-written tuple of exception types made "v3 never exits 1"
    true only for the types someone remembered: a `ValueError` out of a bridge,
    a pypdf parser type, a `subprocess.SubprocessError` from Tesseract — each
    escaped as a traceback, and a traceback is Python's own exit 1 (docs/09
    P5-9). Testing `classify` in isolation could not see that, because its only
    caller never reached it.
    """
    from webshot import pipeline

    async def unforeseen(*args: object, **kwargs: object) -> None:
        raise ValueError("a bridge raised something nobody catches")

    monkeypatch.setattr(pipeline, "_capture", unforeseen)
    code, written = _run(
        tmp_path, FIXTURE, "--output", str(tmp_path / "boom.pdf"), "--no-ocr"
    )
    assert code == 5
    assert written["exit_code"] == 5
    assert "nobody catches" in str(written["error"])


# --------------------------------------------------------------------------- #
# One capture at a time per output path (docs/04-spec.md §5.1)
# --------------------------------------------------------------------------- #


def test_a_second_run_against_the_same_output_is_refused(tmp_path: Path) -> None:
    """spec §5.1's per-output lockfile, and it is exit 2 like the spec says."""
    output = tmp_path / "contested.pdf"
    with OutputLock(output, output.with_suffix(".ai")):
        code, written = _run(tmp_path, FIXTURE, "--output", str(output), "--no-ocr")
        assert code == 2
        assert written["exit_code"] == 2
        assert "already writing" in str(written["error"])
        # The refusal has to be actionable, and actionable for *both* callers:
        # an MCP client can neither choose an output path nor delete a file, so
        # the message leads with "try again" and still names the lock for a
        # person who knows no run is in progress.
        assert "Try again" in str(written["error"])
        # Whichever of the two published paths it contended for first — they
        # are taken in sorted order, so the bundle is named here — the message
        # points at a real lock file a person can delete.
        assert any(
            str(lock_path(target)) in str(written["error"])
            for target in (output, output.with_suffix(".ai"))
        )


def test_the_lock_covers_a_shared_bundle_directory(tmp_path: Path) -> None:
    """`--ai-bundle-dir` is a second published path, so it is a second lock.

    Two runs writing *different* PDFs into one bundle directory take different
    PDF locks and would both proceed, then race on the bundle — the interleaved
    publication §5.1 exists to prevent, reached without any race in the lock
    itself (docs/09 P5-10).
    """
    shared = tmp_path / "shared.ai"
    with OutputLock(tmp_path / "a.pdf", shared):
        code, written = _run(
            tmp_path,
            FIXTURE,
            "--output",
            str(tmp_path / "b.pdf"),
            "--ai-bundle-dir",
            str(shared),
            "--no-ocr",
        )
    assert code == 2
    assert "shared.ai" in str(written["error"])


def test_the_lock_excludes_a_real_second_process(tmp_path: Path) -> None:
    """The property the whole module exists for, proven across processes.

    In-process tests cannot see the failure the first design had: its exclusion
    came from a pid written into the file a moment *after* the file appeared, so
    two genuine processes could both get in. The lock is `flock` now — held on
    the open file description and released by the kernel however the process
    dies — and this is what checks it.
    """
    import subprocess
    import sys
    import textwrap

    output = tmp_path / "raced.pdf"
    program = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(REPO_ROOT / "src")!r})
        from webshot.outputlock import OutputLock
        from pathlib import Path
        lock = OutputLock(Path({str(output)!r}))
        lock.acquire()
        print("held", os.getpid(), flush=True)
        time.sleep(30)
    """)
    holder = subprocess.Popen(
        [sys.executable, "-c", program], stdout=subprocess.PIPE, text=True
    )
    pid = ""
    try:
        assert holder.stdout is not None
        said, _, pid = holder.stdout.readline().strip().partition(" ")
        assert said == "held"
        with pytest.raises(UsageError) as caught:
            OutputLock(output).acquire()
        # The holder's own pid, not `holder.pid`: on Windows a venv's
        # python.exe is a launcher that runs the interpreter as its child, so
        # the process that holds the lock is not the one Popen started
        # (docs/09 P10-24).
        assert f"pid {pid}" in str(caught.value)
    finally:
        # The holder itself, for the same reason: killing only the launcher
        # left its child holding the lock on Windows (docs/09 P10-24).
        if pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        holder.kill()
        holder.wait(timeout=10)
    # The kernel released it when that process died — no stale-lock reasoning,
    # no pid probing, and nothing left for a person to clean up. At once, on
    # POSIX. Windows releases a dead process's byte locks on its own schedule,
    # so there it gets a bounded wait rather than no check.
    deadline = time.monotonic() + (10 if sys.platform == "win32" else 0)
    while True:
        try:
            OutputLock(output).acquire()
            break
        except UsageError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def test_a_lock_is_released_even_when_the_capture_fails(
    tmp_path: Path, no_browsers: None
) -> None:
    """Otherwise the first failure poisons the output path for every run after.

    The dotfiles stay behind, deliberately (the `outputlock` docstring says
    why), and what they hold is the failed run's pid, not a lock.
    """
    output = tmp_path / "failed.pdf"
    assert main([FIXTURE, "--output", str(output), "--no-ocr"]) == 9
    for target in (output, output.with_suffix(".ai")):
        assert lock_path(target).read_text(encoding="utf-8") == f"{os.getpid()}\n"
    OutputLock(output, output.with_suffix(".ai")).acquire()


def test_the_lock_does_not_outlive_a_successful_capture(
    tmp_path: Path, no_tesseract: None
) -> None:
    """The file stays, deliberately; what must not survive is the *lock*.

    Deleting it on release would reintroduce the race the module avoids — a
    second process can already hold a descriptor to that inode — so the check
    is that the path is lockable again, not that the dotfile is gone.
    """
    output = tmp_path / "clean.pdf"
    assert main([FIXTURE, "--output", str(output), "--no-ocr"]) == 0
    OutputLock(output, output.with_suffix(".ai")).acquire()


def test_a_long_output_name_still_gets_a_lock(tmp_path: Path) -> None:
    """The lock decorates the name it guards, so a long one must not break it.

    A 250-byte output name plus `.` and `.lock` exceeds what most filesystems
    accept, which made a capture that used to work fail before it started
    (docs/09 P5-10).
    """
    output = tmp_path / (("x" * 250) + ".pdf")
    path = lock_path(output)
    assert len(path.name.encode("utf-8")) <= 255
    lock = OutputLock(output)
    lock.acquire()
    try:
        with pytest.raises(UsageError):
            OutputLock(output).acquire()
    finally:
        lock.release()


def test_a_symlinked_lock_path_cannot_be_truncated(tmp_path: Path) -> None:
    """The lock path is predictable, and shared output directories exist.

    Another local user who can write the output directory could pre-create the
    lock path as a symlink to any file the WebShot user can write; the open
    followed it and the pid write truncated the target. `O_NOFOLLOW` refuses
    the symlink outright (docs/09 P8-12).
    """
    from webshot.outputlock import OutputLock, lock_path

    victim = tmp_path / "important.txt"
    victim.write_text("do not destroy me", encoding="utf-8")
    destination = tmp_path / "out" / "capture.pdf"
    destination.parent.mkdir()
    lock_path(destination).symlink_to(victim)

    with pytest.raises(WebShotError) as caught:
        OutputLock(destination).acquire()
    assert caught.value.exit_code == 2
    assert victim.read_text(encoding="utf-8") == "do not destroy me"


@pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason="POSIX-only: Windows has no FIFO a directory entry can name",
)
def test_a_lock_path_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    """Checked on the descriptor, so nothing can be swapped in underneath."""
    from webshot.outputlock import OutputLock, lock_path

    destination = tmp_path / "capture.pdf"
    os.mkfifo(lock_path(destination))
    with pytest.raises(WebShotError) as caught:
        OutputLock(destination).acquire()
    assert caught.value.exit_code == 2


@pytest.fixture
def no_o_nofollow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A platform without `os.O_NOFOLLOW` — Windows, reproduced here.

    The attribute is removed as well as the module's copy of it, so a
    regression that names `os.O_NOFOLLOW` again fails the way Windows did:
    every capture exited 5 with `AttributeError` before it began, and the
    weekly Windows run reported green (docs/09 P10-24).
    """
    import webshot.outputlock as outputlock

    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(outputlock, "_NOFOLLOW", 0)


def test_a_capture_runs_where_o_nofollow_does_not_exist(
    tmp_path: Path, no_o_nofollow: None, no_browsers: None
) -> None:
    """Past the lock to the browser launch: 9 for no Chromium, not 5."""
    output = tmp_path / "capture.pdf"
    assert main([FIXTURE, "--output", str(output), "--no-ocr"]) == 9
    # And the lock it took and gave back is takeable again, twice over: the
    # second time through the path that opens an existing lock file.
    for _ in range(2):
        with OutputLock(output, output.with_suffix(".ai")):
            pass


def test_without_o_nofollow_a_lock_still_excludes(
    tmp_path: Path, no_o_nofollow: None
) -> None:
    destination = tmp_path / "capture.pdf"
    held = OutputLock(destination)
    held.acquire()
    try:
        with pytest.raises(UsageError, match="already writing"):
            OutputLock(destination).acquire()
    finally:
        held.release()


def test_without_o_nofollow_a_symlinked_lock_path_cannot_be_truncated(
    tmp_path: Path, no_o_nofollow: None
) -> None:
    """The P8-12 guarantee, kept without the flag that gave it (docs/09 P10-24)."""
    victim = tmp_path / "important.txt"
    victim.write_bytes(b"do not destroy me")
    destination = tmp_path / "out" / "capture.pdf"
    destination.parent.mkdir()
    lock_path(destination).symlink_to(victim)

    with pytest.raises(UsageError, match="symbolic link") as caught:
        OutputLock(destination).acquire()
    assert caught.value.exit_code == 2
    assert victim.read_bytes() == b"do not destroy me"


def test_without_o_nofollow_a_dangling_symlink_creates_nothing(
    tmp_path: Path, no_o_nofollow: None
) -> None:
    """`O_CREAT` through a planted link would create its target: never used on one."""
    target = tmp_path / "planted.txt"
    destination = tmp_path / "out" / "capture.pdf"
    destination.parent.mkdir()
    lock_path(destination).symlink_to(target)

    with pytest.raises(UsageError) as caught:
        OutputLock(destination).acquire()
    assert caught.value.exit_code == 2
    assert not target.exists()


def test_the_contention_message_does_not_advise_deleting_a_live_lock(
    tmp_path: Path,
) -> None:
    """This message is only reached once the kernel confirmed a live holder.

    Unlinking a held lock lets the next run create and lock a different inode
    while the first keeps publishing — the interleaving the lock exists to
    prevent (docs/09 P8-13). The path is still named; the instruction is not.
    """
    from webshot.outputlock import OutputLock, lock_path

    destination = tmp_path / "capture.pdf"
    held = OutputLock(destination)
    held.acquire()
    try:
        with pytest.raises(WebShotError) as caught:
            OutputLock(destination).acquire()
        message = str(caught.value)
        assert str(lock_path(destination)) in message
        assert "delete" not in message.lower()
        assert "remove" not in message.lower()
    finally:
        held.release()


def test_a_failed_metadata_write_still_releases_the_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long-lived MCP server would otherwise hold the lock until it restarts.

    The descriptor was appended to the instance's list *after* the pid write,
    so a write failure left it open and locked with nothing tracking it — and
    every later capture of that destination was refused (docs/09 P8-14).
    """
    from webshot.outputlock import OutputLock

    destination = tmp_path / "capture.pdf"

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "ftruncate", explode)
    lock = OutputLock(destination)
    with pytest.raises(OSError):
        lock.acquire()

    # `acquire` cleaned up, so the destination is claimable again.
    monkeypatch.undo()
    second = OutputLock(destination)
    second.acquire()
    second.release()


def test_a_report_naming_the_output_cannot_destroy_it(tmp_path: Path) -> None:
    """The QA report is written after the capture is published, on purpose.

    So `--report` pointing at the output replaced the finished PDF with JSON —
    and the command exited 0, reporting success for a run whose only
    deliverable it had just overwritten (docs/09 P8-53).
    """
    output = tmp_path / "capture.pdf"
    assert main(["tests/fixtures/sample_notes.txt", "-o", str(output)]) == 0
    original = output.read_bytes()
    assert original.startswith(b"%PDF")

    code = main(
        [
            "tests/fixtures/sample_notes.txt",
            "-o",
            str(output),
            "--report",
            str(output),
        ]
    )
    assert code == 2
    # And the refusal did not then write its own failure report over the file
    # it was protecting.
    assert output.read_bytes() == original


def test_a_report_naming_the_bundle_directory_is_refused_too(tmp_path: Path) -> None:
    output = tmp_path / "capture.pdf"
    code = main(
        [
            "tests/fixtures/sample_notes.txt",
            "-o",
            str(output),
            "--report",
            str(output.with_suffix(".ai")),
        ]
    )
    assert code == 2


def test_an_ordinary_report_path_is_unaffected(tmp_path: Path) -> None:
    """The refusal must not narrow the common case."""
    output = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "tests/fixtures/sample_notes.txt",
                "-o",
                str(output),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert output.read_bytes().startswith(b"%PDF")
    assert json.loads(report.read_text("utf-8"))["exit_code"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--scale", "nope"],
        ["--mode", "not-a-mode"],
        ["--no-such-option"],
    ],
    ids=["bad-typed-value", "invalid-choice", "unknown-option"],
)
def test_argparse_rejections_still_write_the_report(
    extra: list[str], tmp_path: Path
) -> None:
    """docs/04-spec.md §2.3: a run given `--report` records its exit code and
    its error, succeed or fail.

    `parse_args` raises `SystemExit(2)` from inside itself, before the parsed
    namespace exists and therefore before the failure-report boundary — so the
    one shape of failure an automated caller is most likely to hit was the one
    that wrote nothing (docs/09 P8-73).
    """
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit) as caught:
        main(["tests/fixtures/sample_notes.txt", "--report", str(report), *extra])
    assert caught.value.code == 2
    assert report.is_file(), "argparse rejected the run and wrote no report"
    written = json.loads(report.read_text("utf-8"))
    assert written["exit_code"] == 2
    assert written["error"]


def test_a_missing_source_writes_a_report_too(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        main(["--report", str(report)])
    assert report.is_file()
    assert json.loads(report.read_text("utf-8"))["exit_code"] == 2


def test_a_successful_parse_writes_no_extra_report(tmp_path: Path) -> None:
    """The reporting parser must not fire on a command line that is fine."""
    output = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "tests/fixtures/sample_notes.txt",
                "-o",
                str(output),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert json.loads(report.read_text("utf-8"))["exit_code"] == 0
