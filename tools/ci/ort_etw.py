"""Count onnxruntime's ETW telemetry events on Windows, arm by arm (docs/09 P10-26).

Windows builds of onnxruntime ignore `ORT_DISABLE_TELEMETRY` and write
TraceLogging events to the `Microsoft.ML.ONNXRuntime` provider instead. WebShot
calls `disable_telemetry_events()` as onnxruntime finishes importing. The unit
tests prove the call is made, and in time. This proves what it does. For each
arm it starts an ETW session on the provider, runs a child interpreter,
stops the session, and counts the events in the trace by name.

- **default**: WebShot's default, a session built directly. The property:
  no session event reaches the trace. The three events onnxruntime writes
  while it is being imported still do, before the call can be made.
- **opted in**: the same child with `ORT_DISABLE_TELEMETRY=0`. The control:
  it must see the session events. Without it, an empty trace would pass
  the first arm while measuring nothing.
- **trace started late**: WebShot's default, with the trace started after
  the call and before the session. docs/04 §4.1 says this switches the
  events back on, because onnxruntime's ETW enable callback overwrites the
  flag the call cleared. It is asserted so that the claim fails here if it
  stops being true.
- **magika, opted in**: the MarkItDown route with WebShot's call opted out.
  magika makes the call itself before its own session. Reported, not
  asserted: WebShot does not rely on it.

The direct session stands for RapidOCR, which builds its sessions without
the call. It loads magika's model because that model is installed with every
WebShot.

Names are found in the raw trace. A TraceLogging event carries its metadata,
name included, NUL-terminated, in every record. So a count needs no decoder
that might not know the provider. `tracerpt` is run as well, and its total
for the provider is printed beside the sum, so an event with a name this file
does not list still shows up as a difference.

    python tools/ci/ort_etw.py --out out/etw --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree

#: `Microsoft.ML.ONNXRuntime`, from `core/platform/windows/telemetry.cc`.
PROVIDER = "{3a26b1ff-7484-7484-7484-15261f42614d}"

#: The provider's event names in onnxruntime 1.30.0's `telemetry.cc`.
#: Written while `import onnxruntime` loads the native module, before any
#: Python can run, so no switch WebShot can reach turns them off:
#: `Environment::Initialize` logs `ProcessInfo`, then registers the internal
#: execution providers, which logs each registration's start and end.
IMPORT_EVENTS = ("ProcessInfo", "RegisterEpLibraryStart", "RegisterEpLibraryEnd")
#: Everything else, which the call suppresses. A plugin library registered
#: later would log the last three here; WebShot registers none.
SESSION_EVENTS = (
    "SessionCreationStart",
    "SessionCreation",
    "SessionCreationEnd",
    "ModelLoadStart",
    "ModelLoadEnd",
    "EvaluationStart",
    "EvaluationStop",
    "RuntimePerf",
    "RuntimeError",
    "EpDeviceUsage",
    "AutoEpSelection",
    "ProviderOptions",
    "ExecutionProviderEvent",
    "DriverInfo",
    "CompileModelStart",
    "CompileModelComplete",
    "RegisterEpLibraryWithLibPath",
)

_DIRECT = """
import importlib.util, sys
from pathlib import Path
import webshot
import onnxruntime
root = Path(importlib.util.find_spec("magika").submodule_search_locations[0])
model = next(root.glob("models/*/model.onnx"))
print("CHILD-READY", flush=True)
if <<LATE>>:
    sys.stdin.readline()
onnxruntime.InferenceSession(str(model), providers=["CPUExecutionProvider"])
print("CHILD-DONE", flush=True)
"""

_MAGIKA = """
from pathlib import Path
from webshot.acquire.markitdown_bridge import to_markdown
print("CHILD-READY", flush=True)
to_markdown(Path(<<CSV>>), suffix=".csv")
print("CHILD-DONE", flush=True)
"""


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    switch: str | None
    code: str
    late: bool = False


@dataclass(slots=True)
class Seen:
    names: Counter[str] = field(default_factory=Counter)
    decoded: int | None = None
    child: str = ""

    @property
    def at_import(self) -> int:
        return sum(self.names[n] for n in IMPORT_EVENTS)

    @property
    def session(self) -> int:
        return sum(self.names[n] for n in SESSION_EVENTS)


def arms(csv: Path) -> list[Arm]:
    direct = _DIRECT.replace("<<LATE>>", "False")
    return [
        Arm("default", None, direct),
        Arm("opted in", "0", direct),
        Arm("trace started late", None, _DIRECT.replace("<<LATE>>", "True"), True),
        Arm("magika, opted in", "0", _MAGIKA.replace("<<CSV>>", repr(str(csv)))),
    ]


def count_names(trace: bytes) -> Counter[str]:
    """Each known event name, counted where a record's metadata carries it."""
    return Counter(
        {
            name: trace.count(name.encode() + b"\x00")
            for name in IMPORT_EVENTS + SESSION_EVENTS
        }
    )


def count_decoded(xml: bytes) -> int:
    """How many events `tracerpt` attributed to the provider."""
    root = ElementTree.fromstring(xml)
    total = 0
    for event in root.iter():
        if not event.tag.endswith("}Event") and event.tag != "Event":
            continue
        for node in event.iter():
            if node.tag.endswith("Provider") and (
                node.get("Guid", "").lower() == PROVIDER
            ):
                total += 1
                break
    return total


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(
            f"{command[0]} exited {done.returncode}: {' '.join(command)}\n"
            f"{done.stdout}\n{done.stderr}"
        )


def measure(arm: Arm, out: Path, index: int) -> Seen:
    session = f"webshot-ort-{os.getpid()}-{index}"
    etl = out / f"arm{index}.etl"
    start = ["logman", "start", session, "-p", PROVIDER, "0xffffffffffffffff"]
    start += ["0xff", "-o", str(etl), "-ets"]
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    if arm.switch is not None:
        env["ORT_DISABLE_TELEMETRY"] = arm.switch
    if not arm.late:
        _run(start)
    child = subprocess.Popen(
        [sys.executable, "-c", arm.code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=out,
    )
    assert child.stdin is not None and child.stdout is not None
    # Up to the child's "ready": for the late arm, WebShot's call has been
    # made by then, and the trace must start after it and before the session.
    lines: list[str] = []
    while not lines or lines[-1].strip() not in ("CHILD-READY", ""):
        lines.append(child.stdout.readline())
    if arm.late:
        _run(start)
        # The enable callback reaches the child on a thread of its own.
        time.sleep(2)
        child.stdin.write("go\n")
        child.stdin.flush()
    rest, _ = child.communicate(timeout=300)
    _run(["logman", "stop", session, "-ets"])
    seen = Seen(names=count_names(etl.read_bytes()), child="".join(lines) + rest)
    if child.returncode != 0 or "CHILD-DONE" not in seen.child:
        raise SystemExit(
            f"arm {arm.name!r}: the child exited {child.returncode}\n{seen.child}"
        )
    xml = out / f"arm{index}.xml"
    done = subprocess.run(
        ["tracerpt", str(etl), "-o", str(xml), "-of", "XML", "-y"],
        capture_output=True,
        text=True,
    )
    if done.returncode == 0 and xml.exists():
        seen.decoded = count_decoded(xml.read_bytes())
    return seen


def verdict(results: dict[str, Seen]) -> list[str]:
    """What is wrong with these counts; empty when every claim held."""
    problems = []
    control = results["opted in"]
    if control.names["ProcessInfo"] == 0 or control.names["SessionCreation"] == 0:
        problems.append(
            "the control arm saw no ProcessInfo or no SessionCreation, so this "
            "trace cannot see onnxruntime's events and the other arms measured "
            "nothing"
        )
    if results["default"].session:
        problems.append(
            "WebShot's default let session events through: "
            f"{_listing(results['default'].names, SESSION_EVENTS)}"
        )
    if results["trace started late"].names["SessionCreation"] == 0:
        problems.append(
            "a trace started after the call no longer turns the events back "
            "on. docs/04 §4.1 and docs/09 P10-26 say it does: update them"
        )
    return problems


def _listing(names: Counter[str], which: tuple[str, ...]) -> str:
    return ", ".join(f"{n} {names[n]}" for n in which if names[n]) or "none"


def render(results: dict[str, Seen], problems: list[str]) -> str:
    lines = [
        "### onnxruntime ETW events (docs/09 P10-26)",
        "",
        "| arm | at import | session events | session events by name | decoded total |",
        "|---|---:|---:|---|---:|",
    ]
    for name, seen in results.items():
        decoded = "n/a" if seen.decoded is None else str(seen.decoded)
        lines.append(
            f"| {name} | {seen.at_import} | {seen.session} | "
            f"{_listing(seen.names, SESSION_EVENTS)} | {decoded} |"
        )
    lines.append("")
    lines += [f"- **FAIL:** {p}" for p in problems] or ["Every claim held."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count onnxruntime's ETW telemetry events on Windows."
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, help="append the table here too")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("ETW exists only on Windows", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    csv = Path(__file__).resolve().parents[2] / "tests/fixtures/sample_data.csv"
    results = {arm.name: measure(arm, args.out, i) for i, arm in enumerate(arms(csv))}
    problems = verdict(results)
    table = render(results, problems)
    print(table)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(table)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
