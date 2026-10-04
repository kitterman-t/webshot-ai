"""Install the built wheel into an empty venv and use it, as a stranger would.

Every other check in this repository runs against the source tree, where
`src/webshot/` is importable, `schemas/` is on disk beside it, and the console
script is an editable shim.  None of that is what a user gets.  This gate
therefore throws all of it away: `uv build`, a fresh virtual environment with
nothing else in it, `uv pip install ./dist/webshot-*.whl`, and then the three
things an installed WebShot has to be able to do —

1. capture a fixture into a valid PDF and a complete `.ai` bundle,
2. print a published JSON Schema (`webshot schema manifest`), and
3. report on its environment (`webshot doctor`),

— run from a working directory that is not the repository, so an import that
only resolved because of the checkout fails here instead of at a user.

docs/05 task 5.6 is a plan amendment: Phase 5 had a release workflow that built
artifacts and no step that used one.  The bugs this shape catches — a package
that forgets a subpackage, an entry point that points at a moved function, a
data file that only exists in the source tree — are invisible to every test
that imports from `src/` (docs/09 P5-6).

    python tools/release/wheel_check.py
    python tools/release/wheel_check.py --extras mcp,office,ocr-rapid
    python tools/release/wheel_check.py --dist dist   # reuse a built artifact
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "professional_page.html"
SCHEMA_ROOT = REPO_ROOT / "schemas"


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    allowed: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    """One command, echoed and checked.

    `allowed` exists so the one invocation that may legitimately fail —
    `webshot doctor`, which exits 9 on a runner without Ghostscript — goes
    through the same plumbing as the rest instead of a hand-rolled copy that
    had already drifted to a different timeout and no echo.

    `WEBSHOT_*` is stripped: a release gate must exercise the wheel's own
    defaults, not the maintainer's settings file (docs/09 P5-10).
    """
    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env={
            key: value
            for key, value in {**os.environ, **(env or {})}.items()
            if not key.startswith("WEBSHOT_")
        },
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if completed.returncode not in allowed:
        sys.stdout.write(completed.stdout[-4000:])
        sys.stderr.write(completed.stderr[-4000:])
        raise SystemExit(
            f"FAILED (exit {completed.returncode}): {' '.join(command[:3])}…"
        )
    return completed


def build_wheel(dist: Path, *, reuse: bool) -> Path:
    if not reuse:
        if dist.exists():
            shutil.rmtree(dist)
        run(["uv", "build", "--out-dir", str(dist)], cwd=REPO_ROOT)
    # Absolute, always: `check()` runs `uv pip install` from a temporary
    # working directory, so a relative wheel path would resolve against *that*
    # and not be found. It is the exact bug the release rehearsal caught before
    # a tag ever existed, which is what a rehearsal is for.
    dist = dist.resolve()
    wheels = sorted(dist.glob("webshot-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel in {dist}, found {wheels}")
    sdists = sorted(dist.glob("webshot-*.tar.gz"))
    if len(sdists) != 1:
        raise SystemExit(f"expected exactly one sdist in {dist}, found {sdists}")
    print(f"built {wheels[0].name} and {sdists[0].name}")
    return wheels[0]


def check(workdir: Path, wheel: Path, extras: str) -> None:
    """Install the wheel alone, then use it from outside the checkout."""
    venv = workdir / "venv"
    run(["uv", "venv", str(venv)], cwd=workdir)
    binaries = venv / ("Scripts" if os.name == "nt" else "bin")
    webshot = binaries / ("webshot.exe" if os.name == "nt" else "webshot")
    python = binaries / ("python.exe" if os.name == "nt" else "python")
    environment = {"VIRTUAL_ENV": str(venv), "UV_PROJECT_ENVIRONMENT": str(venv)}

    if not wheel.is_absolute():  # pragma: no cover - build_wheel resolves it
        raise SystemExit(f"the wheel path must be absolute, got {wheel}")
    requirement = f"{wheel}[{extras}]" if extras else str(wheel)
    run(["uv", "pip", "install", "--python", str(python), requirement], cwd=workdir)

    # The entry point exists and is the console script, not a shim on sys.path.
    if not webshot.is_file():
        raise SystemExit(f"the wheel installed no `webshot` entry point in {binaries}")
    version = run([str(webshot), "--version"], cwd=workdir).stdout.strip()
    print(f"  entry point: {version}")

    # Playwright's browsers live outside the venv, so a fresh environment still
    # has to be told to fetch them — which is exactly what the README says.
    run([str(python), "-m", "playwright", "install", "chromium"], cwd=workdir)

    # A real capture, of a fixture copied out of the repository so that nothing
    # in this run resolves a path relative to the checkout.
    fixture = workdir / "article.html"
    shutil.copy(FIXTURE, fixture)
    output = workdir / "capture" / "article.pdf"
    run(
        [str(webshot), str(fixture), "--output", str(output), "--no-ocr"],
        cwd=workdir,
        env=environment,
    )
    if not output.is_file() or output.stat().st_size < 1_000:
        raise SystemExit("the installed wheel produced no usable PDF")
    bundle = output.with_suffix(".ai")
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("the installed wheel produced no bundle manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("bundle_format") != 3:
        raise SystemExit(f"unexpected bundle_format: {manifest.get('bundle_format')}")
    for required in ("content.json", "content.md", "chunks.jsonl"):
        if not (bundle / required).is_file():
            raise SystemExit(f"the published bundle is missing {required}")
    print(f"  capture: {output.stat().st_size} byte PDF, bundle_format 3")

    # `webshot schema manifest` — the contract an installed consumer reads.
    # WebShot generates it from the pydantic models rather than shipping a data
    # file, so the thing to prove is that what an installed wheel prints is
    # byte-identical to what `schemas/` has committed (docs/09 P5-6).
    printed = run([str(webshot), "schema", "manifest"], cwd=workdir).stdout
    committed = (SCHEMA_ROOT / "manifest.schema.json").read_text(encoding="utf-8")
    if printed != committed:
        raise SystemExit(
            "`webshot schema manifest` from the installed wheel does not match "
            "schemas/manifest.schema.json"
        )
    listed = run([str(webshot), "schema"], cwd=workdir).stdout.split()
    missing = [
        name for name in listed if not (SCHEMA_ROOT / f"{name}.schema.json").is_file()
    ]
    if missing:
        raise SystemExit(f"the wheel publishes schemas that schemas/ lacks: {missing}")
    print(f"  schema: {len(listed)} contracts, manifest matches the committed file")

    # `webshot doctor` — allowed to fail (a runner may lack Ghostscript or
    # veraPDF, which is exit 9), but it must run and reach a verdict rather
    # than crash.
    doctor = run([str(webshot), "doctor"], cwd=workdir, env=environment, allowed=(0, 9))
    print(f"  doctor: exit {doctor.returncode}")
    print(doctor.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--extras",
        default="mcp,office",
        help="extras to install alongside the wheel; empty string for none",
    )
    parser.add_argument(
        "--dist", type=Path, default=REPO_ROOT / "dist", help="where to build into"
    )
    parser.add_argument(
        "--reuse-dist",
        action="store_true",
        help="use the artifacts already in --dist instead of rebuilding",
    )
    arguments = parser.parse_args(argv)

    wheel = build_wheel(arguments.dist, reuse=arguments.reuse_dist)
    with tempfile.TemporaryDirectory(prefix="webshot-wheel-check-") as workdir:
        check(Path(workdir), wheel, arguments.extras)
    print("\nthe built wheel installs clean and works from a fresh environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
