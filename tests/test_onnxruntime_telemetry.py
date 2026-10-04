"""onnxruntime's telemetry is off before onnxruntime can start it (docs/09 P10-14).

onnxruntime 1.29 starts Microsoft's 1DS uploader when it is imported, and the
uploader's static destructor races its own HTTP thread at interpreter exit:
the `recursive_mutex lock failed` abort, exit 134 from a capture that had
already written everything.  `ORT_DISABLE_TELEMETRY=1` keeps the uploader from
ever existing, but only when it is in the environment as onnxruntime
initializes.  So what is tested is an ordering — the variable is set before
the first import of onnxruntime, on each path WebShot reaches it by — and not
merely that the variable is set by the time someone looks.

Every probe runs in a fresh interpreter with the variable removed from its
environment.  This process has imported WebShot and so already carries it; a
child that inherited it would pass whether or not the package still set it.

The probe's import hook stops the child *before* onnxruntime's native module
loads when the variable is missing, so a regression fails here without
starting the uploader: a test must not send anything to Microsoft either.

Windows builds ignore the variable and write ETW events, which only
`disable_telemetry_events()` suppresses, once onnxruntime is imported (docs/09
P10-26).  The second half of this file holds that call to the same standard:
made by WebShot, after the import and before the first session is built, on
each path.  The hook is installed on Windows only; on the POSIX CI the probe
installs it by hand, which runs the same code against the real import chain.
"""

from __future__ import annotations

import importlib.util
import mmap
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

SWITCH = "ORT_DISABLE_TELEMETRY"

_PROBE = """
import importlib.abc, os, sys

class _Watch(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "onnxruntime":
            value = os.environ.get("ORT_DISABLE_TELEMETRY")
            print(f"ORT-IMPORT {value!r}", flush=True)
            if value != "1":
                os._exit(3)
        return None

sys.meta_path.insert(0, _Watch())
<<RUN>>
print("PROBE-DONE", flush=True)
"""

#: Each way WebShot loads onnxruntime, as the capture reaches it.  markitdown is
#: a base dependency and its file-type sniffer, magika, imports onnxruntime at
#: module scope — so every capture of any kind pays for it, which is why the
#: abort was never specific to OCR or to one page.  RapidOCR imports it only
#: when the recognizer is built.
PATHS = {
    "markitdown": "import webshot.acquire.markitdown_bridge",
    "rapidocr": (
        "from webshot.ocr.rapid import RapidOcrEngine\n"
        "print('UNAVAILABLE', RapidOcrEngine().unavailable_reason(), flush=True)"
    ),
}


def _child_env(**overrides: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != SWITCH}
    env.update(overrides)
    return env


def _run(code: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # `cwd` is a scratch directory: the checkout root carries the `webshot.py`
    # shim, and this measures the installed package (as test_footprint does).
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=cwd,
        env=env,
    )


@pytest.mark.parametrize("path", sorted(PATHS))
def test_telemetry_is_off_before_onnxruntime_is_first_imported(
    path: str, tmp_path: Path
) -> None:
    if path == "rapidocr" and importlib.util.find_spec("rapidocr") is None:
        pytest.skip("install webshot[ocr-rapid] to run this")
    completed = _run(_PROBE.replace("<<RUN>>", PATHS[path]), tmp_path, _child_env())
    seen = [line for line in completed.stdout.splitlines() if "ORT-IMPORT" in line]
    assert seen, (
        f"onnxruntime was never imported through {path}, so this measured "
        "nothing. If that path no longer reaches onnxruntime, drop it from "
        f"PATHS and check docs/09 P10-14 still says how it is reached.\n"
        f"exit {completed.returncode}\nstdout:\n{completed.stdout[-2000:]}\n"
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    assert seen[0] == "ORT-IMPORT '1'", (
        f"onnxruntime was imported through {path} with {SWITCH}="
        f"{seen[0].removeprefix('ORT-IMPORT ')}: its telemetry uploader would "
        "have started, and with it the exit-time abort (docs/09 P10-14)."
    )
    assert completed.returncode == 0 and "PROBE-DONE" in completed.stdout, (
        f"the probe exited {completed.returncode} after the import.\n"
        f"stdout:\n{completed.stdout[-2000:]}\nstderr tail:\n{completed.stderr[-2000:]}"
    )
    if path == "rapidocr":
        assert "UNAVAILABLE None" in completed.stdout, completed.stdout[-2000:]


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "1"), ("0", "0"), ("true", "true")],
    ids=["unset", "opted-back-in", "caller-spelling"],
)
def test_a_callers_own_setting_is_obeyed(
    given: str | None, expected: str, tmp_path: Path
) -> None:
    """`setdefault`, not assignment: WebShot decides only when nobody else has."""
    env = _child_env() if given is None else _child_env(**{SWITCH: given})
    completed = _run(
        f"import os, webshot\nprint(repr(os.environ.get({SWITCH!r})))", tmp_path, env
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip() == repr(expected)


def test_the_installed_onnxruntime_still_reads_the_switch() -> None:
    """The variable's name is onnxruntime's, and an upgrade could retire it.

    If it did, setting it would do nothing, and every test above would still
    pass — they prove WebShot sets the variable in time, not that anything
    reads it.  This looks for the name where it is read: in the native module.
    It cannot prove the behaviour, which docs/09 P10-14 measured (importing
    onnxruntime adds two threads without the switch and none with it); it
    catches the rename, which is the change an unreviewed upgrade would bring.
    """
    spec = importlib.util.find_spec("onnxruntime")
    assert spec is not None and spec.origin, (
        "onnxruntime is not installed, but markitdown's magika depends on it; "
        "the environment is not the one this test was written for"
    )
    capi = Path(spec.origin).parent / "capi"
    binaries = sorted(capi.glob("onnxruntime_pybind11_state*"))
    assert binaries, (
        f"no native module in {capi}: {sorted(p.name for p in capi.iterdir())}"
    )
    for binary in binaries:
        with (
            binary.open("rb") as handle,
            mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as image,
        ):
            reads_switch = image.find(SWITCH.encode()) != -1
            uploader = image.find(b"PosixTelemetry") != -1
        if sys.platform == "win32":
            # Upstream's Privacy.md: the switch is for non-Windows builds, and
            # Windows emits ETW events instead, which WebShot suppresses with
            # the API (the tests below, docs/09 P10-26). The Windows module
            # carries neither the switch nor the uploader (1.29.0 and 1.30.0,
            # docs/09 P10-24), so the thing to catch is the in-process uploader
            # — and P10-14's abort with it — arriving on Windows too.
            assert not uploader, (
                f"{binary.name} now contains the PosixTelemetry uploader, which "
                f"{'reads' if reads_switch else 'does not read'} {SWITCH!r}: the "
                "exit-time abort of docs/09 P10-14 can reach Windows."
            )
            continue
        assert reads_switch, (
            f"{binary.name} does not contain {SWITCH!r}; it "
            f"{'does' if uploader else 'does not'} contain the PosixTelemetry "
            "uploader. If it does, the switch WebShot sets is no longer read "
            "and the uploader runs (docs/09 P10-14)."
        )


# --- Windows: the ETW events, switched off by the API (docs/09 P10-26) --------

_EVENTS_PROBE = """
import importlib.abc, os, sys

SPIED = (
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_inference_collection",
)

class _Spy(importlib.abc.MetaPathFinder):
    # Wraps two onnxruntime modules as they load and reports, in order, each
    # call to disable_telemetry_events() with the module that made it, and
    # each session built.  onnxruntime's own __init__ copies the function
    # from _pybind_state, so whoever calls it — WebShot, magika — calls this.
    def find_spec(self, name, path=None, target=None):
        if name == "onnxruntime" and os.environ.get("ORT_DISABLE_TELEMETRY") != "1":
            print("ORT-IMPORT without the POSIX switch", flush=True)
            os._exit(3)
        if name not in SPIED:
            return None
        for finder in sys.meta_path[sys.meta_path.index(self) + 1 :]:
            find = getattr(finder, "find_spec", None)
            spec = find(name, path, target) if find else None
            if spec is not None:
                break
        else:
            return None
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            if name == SPIED[0]:
                real = module.disable_telemetry_events

                def disable():
                    caller = sys._getframe(1).f_globals.get("__name__")
                    print(f"ORT-DISABLE {caller}", flush=True)
                    real()

                module.disable_telemetry_events = disable
            else:
                session = module.InferenceSession
                create = session._create_inference_session

                def spied(self, *args, **kwargs):
                    print("ORT-SESSION", flush=True)
                    return create(self, *args, **kwargs)

                session._create_inference_session = spied

        spec.loader.exec_module = exec_module
        return spec

import webshot
from webshot import onnxruntime_telemetry

if sys.platform != "win32":
    # What the package init does on Windows, done by hand to run it here.
    onnxruntime_telemetry.watch()
sys.meta_path.insert(0, _Spy())
<<RUN>>
print("PROBE-DONE", flush=True)
"""

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The same two routes as PATHS, taken far enough to build a session: the
#: order being tested is "switched off, then used", so each route has to
#: reach the use.  magika disables the events itself right before building
#: its session (0.6.2); RapidOCR does not (3.9.2).
EVENT_PATHS = {
    "markitdown": (
        "from pathlib import Path\n"
        "from webshot.acquire.markitdown_bridge import to_markdown\n"
        f"to_markdown(Path({str(_FIXTURES / 'sample_data.csv')!r}), suffix='.csv')"
    ),
    "rapidocr": PATHS["rapidocr"],
}

WEBSHOT_CALL = "ORT-DISABLE webshot.onnxruntime_telemetry"


@pytest.mark.parametrize("path", sorted(EVENT_PATHS))
def test_webshot_switches_the_events_off_before_any_session_is_built(
    path: str, tmp_path: Path
) -> None:
    if path == "rapidocr" and importlib.util.find_spec("rapidocr") is None:
        pytest.skip("install webshot[ocr-rapid] to run this")
    code = _EVENTS_PROBE.replace("<<RUN>>", EVENT_PATHS[path])
    completed = _run(code, tmp_path, _child_env())
    seen = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(("ORT-DISABLE", "ORT-SESSION"))
    ]
    report = (
        f"exit {completed.returncode}; in order: {seen}\n"
        f"stdout tail:\n{completed.stdout[-2000:]}\n"
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    assert completed.returncode == 0 and "PROBE-DONE" in completed.stdout, report
    assert "ORT-SESSION" in seen, (
        f"{path} built no onnxruntime session, so this measured nothing. If the "
        "route no longer reaches one, drop it here and from docs/09 P10-26.\n" + report
    )
    # WebShot's own call, not magika's: magika makes one right before its
    # session, which would satisfy "some call came first" without WebShot.
    assert WEBSHOT_CALL in seen, report
    assert seen.index(WEBSHOT_CALL) < seen.index("ORT-SESSION"), report
    assert seen.count(WEBSHOT_CALL) == 1, report


def test_the_import_hook_is_installed_on_windows_only(tmp_path: Path) -> None:
    """POSIX has its switch and must not pay for, or be changed by, this one."""
    completed = _run(
        "import sys, webshot\nprint(sorted(type(f).__name__ for f in sys.meta_path))",
        tmp_path,
        _child_env(),
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    installed = "'_AfterImport'" in completed.stdout
    assert installed == (sys.platform == "win32"), completed.stdout


@pytest.mark.parametrize(
    ("value", "off"),
    [
        (None, False),
        ("", False),
        ("1", True),
        ("true", True),
        (" TRUE\t", True),
        ("Yes", True),
        ("on", True),
        ("y", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("2", False),
        # onnxruntime trims ASCII whitespace only; str.strip() would take this.
        ("\xa01", False),
    ],
)
def test_the_switch_is_read_as_onnxruntime_reads_it(
    value: str | None, off: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One variable, one meaning: a value is "off" for the Windows call
    exactly when onnxruntime's own reading of it is (1.30.0)."""
    from webshot import onnxruntime_telemetry

    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value)
    assert onnxruntime_telemetry.requested() is off


def _without_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private `sys.meta_path`, minus the hook the package init installs on
    Windows, so each test starts from none and leaves the process as it was."""
    monkeypatch.setattr(
        sys,
        "meta_path",
        [f for f in sys.meta_path if type(f).__name__ != "_AfterImport"],
    )


class _FakeOnnxruntime(types.ModuleType):
    def __init__(self, *, has_api: bool = True) -> None:
        super().__init__("onnxruntime")
        self.__version__ = "0.0-test"
        self.calls = 0
        if has_api:
            self.disable_telemetry_events = self._disable

    def _disable(self) -> None:
        self.calls += 1


@pytest.mark.parametrize(("value", "calls"), [("1", 1), ("0", 0)])
def test_an_onnxruntime_imported_before_webshot_is_quieted_at_once(
    value: str, calls: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library caller may import onnxruntime first; the hook would never fire."""
    from webshot import onnxruntime_telemetry

    fake = _FakeOnnxruntime()
    monkeypatch.setenv(SWITCH, value)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    _without_the_hook(monkeypatch)
    onnxruntime_telemetry.watch()
    assert fake.calls == calls
    assert not any(type(f).__name__ == "_AfterImport" for f in sys.meta_path)


def test_the_hook_is_installed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from webshot import onnxruntime_telemetry

    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    _without_the_hook(monkeypatch)
    onnxruntime_telemetry.watch()
    onnxruntime_telemetry.watch()
    hooks = [f for f in sys.meta_path if type(f).__name__ == "_AfterImport"]
    assert len(hooks) == 1 and sys.meta_path[0] is hooks[0]


def test_an_onnxruntime_without_the_api_is_reported_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing the import would fail every document read on Windows; saying
    nothing would leave the events on with the docs claiming otherwise."""
    from webshot import onnxruntime_telemetry

    monkeypatch.setenv(SWITCH, "1")
    monkeypatch.setitem(sys.modules, "onnxruntime", _FakeOnnxruntime(has_api=False))
    _without_the_hook(monkeypatch)
    with pytest.warns(RuntimeWarning, match="0.0-test has no disable_telemetry_events"):
        onnxruntime_telemetry.watch()
