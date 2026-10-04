"""Spec §4 footprint: the base install MUST NOT pull torch or any GPU stack.

Two layers of proof.  The import check catches the environment the tests are
actually running in (which is what CI installs).  The lockfile check catches a
resolution that WOULD pull a forbidden package on some platform this runner is
not — `uv.lock` records every wheel for every platform, so a torch that only
arrives on Linux still fails the test on macOS.

docling-slim earns its name here: the whole point of adopting it over the
docling metapackage was keeping the default install model-free (docs/09 S3,
ADR-0004).  transformers is included in the deny list because S4 demoted
HybridChunker to an extra precisely to keep it out.

The gate has exactly one exception — the CPU `onnxruntime` that markitdown's
magika drags in — and it is not an unchecked one: the accelerated ONNX builds
are denied by name, and `test_the_onnx_runtime_exception_is_still_needed`
fails the day the exception stops being necessary (docs/09 P3-2, P5-4).

The second half of the file measures a different footprint with the same
method: not what is *installed*, but what is *loaded* to run a command that
needs none of it.  `webshot --version` imported 1823 modules and nine
native-extension roots before docs/09 P10-6; it imports 158 and none now.  The
same method holds the capture path to the same standard: every capture imports
`webshot.pipeline`, and since P10-15 that no longer loads the ONNX runtime the
first half permits to be installed.  Both halves deny by name, for the same
reason — a name has to be argued with.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FORBIDDEN_MODULES = ("torch", "torchvision", "transformers", "tensorflow")
#: Replaced, not merely unused. bleach is deprecated by its maintainers, and
#: Phase 3 moved the last allowlist (the local-document one) onto nh3 — so an
#: environment that can still import it is an environment where the old code
#: path could come back (docs/05 task 3.3).
RETIRED_MODULES = ("bleach", "reportlab")
FORBIDDEN_PACKAGES = (
    "torch",
    "torchvision",
    "transformers",
    "tensorflow",
    "nvidia-cublas-cu12",  # the usual GPU-stack tell
    # The accelerated ONNX builds. The CPU `onnxruntime` is a documented,
    # tested exception (see below); these are not — each one is a GPU or
    # vendor-accelerator stack under a name close enough to the exception to
    # be waved through by someone skimming (docs/09 P5-4).
    "onnxruntime-gpu",
    "onnxruntime-openvino",
    "onnxruntime-directml",
    "onnxruntime-training",
)

#: The one weight in the default install that WebShot never asked for, kept
#: because removing it costs more than it saves — and held to that bargain by
#: `test_the_onnx_runtime_exception_is_still_needed` below.
#: `onnxruntime` is deliberately not in it — the test has already failed if
#: that is missing, so listing it here would be an element nothing can report.
ONNX_EXCEPTION_CHAIN = ("markitdown", "magika")

#: Distributions that may only arrive through an extra. Phase 3 added two
#: extras that carry real weight, and "behind an extra" is a claim the README
#: and docs/03 make on their behalf — so something has to check it.
EXTRA_ONLY_PACKAGES = {
    "office": ("mammoth", "python-pptx", "openpyxl", "xlsxwriter"),
    "ocr-rapid": ("rapidocr", "opencv-python", "pyclipper", "shapely"),
    "chunk-hybrid": ("transformers",),
}

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_retired_dependency_is_gone_from_the_environment(module: str) -> None:
    assert importlib.util.find_spec(module) is None, (
        f"{module} is installed — it was replaced, and the replacement is only "
        "real once the original cannot be reached"
    )


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_retired_dependency_is_gone_from_the_lock(module: str) -> None:
    """Every extra, not just the default set: it must be gone everywhere."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; the import check above still holds the line")
    completed = subprocess.run(
        [uv, "export", "--frozen", "--all-extras", "--no-emit-project", "--no-hashes"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert not re.search(rf"(?mi)^{module}[=<>~\[ ]", completed.stdout), (
        f"{module} is still resolvable from uv.lock"
    )


@pytest.mark.parametrize("module", FORBIDDEN_MODULES)
def test_forbidden_module_is_not_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None, (
        f"{module} is installed — the default environment must stay model-free "
        "(spec §4; docs/09 S3/S4)"
    )


def _default_resolution() -> set[str]:
    """The DEFAULT dependency set from the lock, across every platform.

    The whole lockfile is deliberately not the subject: the `chunk-hybrid`
    extra legitimately references docling's tokenizer stack, and extras exist
    precisely so that weight is opt-in. What must stay clean is what
    `pip install webshot` gives everyone — spec §4.
    """
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; the import checks above still hold the line")
    completed = subprocess.run(
        [
            uv,
            "export",
            "--frozen",
            "--no-emit-project",
            "--no-hashes",
            "--no-annotate",
            "--format",
            "requirements-txt",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return {
        re.split(r"[=<>!~\[;]", line.strip(), maxsplit=1)[0].lower()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.startswith(("#", "-"))
    }


def test_default_resolution_contains_no_forbidden_package() -> None:
    forbidden = _default_resolution() & set(FORBIDDEN_PACKAGES)
    assert not forbidden, (
        f"the default install would pull {sorted(forbidden)} (spec §4 footprint)"
    )


def test_the_onnx_runtime_exception_is_still_needed() -> None:
    """spec §4's footprint has exactly one exception, and it expires by itself.

    `markitdown` requires `magika`, Google's learned file-type detector, which
    requires `onnxruntime` — 75 MB of inference runtime that WebShot never
    invokes, because the bridge tells MarkItDown what each file is rather than
    letting it guess (docs/09 P3-2). Phase 5.5 re-examined whether it could be
    trimmed and it cannot: `magika~=0.6.1` is an unconditional requirement of
    every markitdown 0.1.x, magika declares `onnxruntime` unconditionally with
    no extra to opt out of, and moving markitdown behind an extra would take
    CSV, TSV, EPUB and ZIP — formats the README and spec §1.1 promise in the
    base install — out of `pip install webshot` (docs/09 P5-4).

    What the gate holds is the line that matters: no torch, no GPU stack, and
    no *second* ML runtime. And this test is written to fail on the good news
    too — the day markitdown drops magika, or magika makes the runtime
    optional, it says so, and `onnxruntime` moves into FORBIDDEN_PACKAGES.
    """
    resolution = _default_resolution()
    if "onnxruntime" not in resolution:
        pytest.fail(
            "onnxruntime is no longer in the default install — the documented "
            "exception has expired. Add 'onnxruntime' to FORBIDDEN_PACKAGES, "
            "delete this test, and record it in docs/09."
        )
    missing = [name for name in ONNX_EXCEPTION_CHAIN if name not in resolution]
    assert not missing, (
        f"onnxruntime is in the default install but {missing} is not, so it is "
        "no longer arriving through markitdown's magika. Something else now "
        "wants an inference runtime — find out what before accepting it."
    )


def test_extras_do_not_leak_into_the_default_install() -> None:
    """`pip install webshot` must not carry what an extra is there to gate."""
    resolution = _default_resolution()
    for extra, packages in EXTRA_ONLY_PACKAGES.items():
        leaked = resolution & set(packages)
        assert not leaked, (
            f"the {extra} extra leaked {sorted(leaked)} into the default install"
        )


# ---------------------------------------------------------------------------
# Startup footprint: what the fast paths are allowed to load.
# ---------------------------------------------------------------------------

#: Third-party roots that must not be loaded to print a version string.  Named
#: rather than counted, deliberately.  The same commit measures 1505 modules on
#: a `--extra dev` install and 1823 with `ocr-rapid` and `office` added, so a
#: ceiling would be a fact about the machine that recorded it rather than about
#: this code — the shape docs/09 P7-13 warns about.  A number also invites the
#: one-line fix of raising it; a name has to be argued with.
#:
#: Every root here was on the `--version` path before docs/09 P10-6 and is not
#: on it now.  The nine carrying native extensions are marked, because they are
#: the interpreter-teardown surface P10-4 is measured against.
VERSION_PATH_FORBIDDEN_ROOTS = (
    "playwright",  # native
    "pydantic_core",  # native
    "pypdf",  # native
    "PIL",  # native
    "lxml",  # native
    "numpy",  # native
    "nh3",  # native
    "regex",  # native
    "charset_normalizer",  # native
    "pandas",
    "pydantic",
    "ocrmypdf",
    "pikepdf",
    "img2pdf",
    "markitdown",
    "trafilatura",
    "docling",
    "docling_core",
    "chonkie",
    "magika",
    "pdfminer",
    "fontTools",
    "bs4",
    "requests",
    "urllib3",
)

#: WebShot's own modules that must stay off the fast path, which is the guard
#: that actually holds: each is a doorway to the roots above, and
#: `webshot.pipeline` alone accounted for 1456 of the 1467 modules
#: `webshot --version` used to load.  The lesson of P10-6 is that they arrived
#: through `webshot/__init__.py`, not through anything `cli.py` asked for — so
#: an eager re-export there fails this test too, which is the case that would
#: otherwise look like an innocent convenience.
VERSION_PATH_FORBIDDEN_WEBSHOT = (
    "webshot.pipeline",
    "webshot.report",
    "webshot.settings",
    "webshot.doctor",
    "webshot.bundle.manifest",
    "webshot.capture.session",
    "webshot.render.pdf",
    "webshot.extract.docling_bridge",
    "webshot.acquire.adapters",
)

#: Proof the measurement ran at all.  Without it an empty module set — a
#: subprocess that died before importing anything, a dump that never reached
#: stderr — satisfies every "not in" assertion below and reports success for
#: work it never did (CLAUDE.md: absence must not read as success).
VERSION_PATH_REQUIRED = ("webshot", "webshot.cli", "webshot.version", "argparse")

FAST_PATHS = ("--version", "--help")

#: Dump `sys.modules` rather than parse `-X importtime`.  importtime reports
#: only what the `import` *statement* loaded: a module reached through
#: `importlib.import_module` is absent from its output, which is exactly how
#: `webshot/__init__.py` resolves its deferred re-exports.  Measuring that way
#: would have left the one construction this change introduced invisible to the
#: test guarding it.  `sys.modules` records what was loaded, not how.
_DUMP_MODULES = """
import json, sys
{run}
sys.stderr.write("MODULES:" + json.dumps(sorted(sys.modules)))
"""


def _modules_after(
    run: str, cwd: Path, env: dict[str, str] | None = None
) -> tuple[set[str], str]:
    """Every module left in `sys.modules` after `run`, and the run's stdout."""
    completed = subprocess.run(
        [sys.executable, "-c", _DUMP_MODULES.format(run=run)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
        env=env,
    )
    assert completed.returncode == 0, (
        f"the probe exited {completed.returncode}; there is nothing to "
        f"measure.\nstderr tail:\n{completed.stderr[-2000:]}"
    )
    marker, found, payload = completed.stderr.partition("MODULES:")
    assert found, (
        "the probe never reached its `sys.modules` dump, so this measured "
        f"nothing. stderr was:\n{completed.stderr[-2000:]}"
    )
    del marker
    return set(json.loads(payload)), completed.stdout


def _cli_probe(flag: str) -> str:
    return (
        "from webshot.cli import main\n"
        "try:\n"
        f"    main([{flag!r}])\n"
        "except SystemExit:\n"
        "    pass\n"
    )


@pytest.mark.parametrize("flag", FAST_PATHS)
def test_fast_path_measurement_actually_ran(flag: str, tmp_path: Path) -> None:
    """The guard for the guard: prove the probe observed a real run.

    Separate from the deny lists below so a broken measurement fails as a
    broken measurement rather than as a clean footprint.  `tmp_path` is the
    working directory on purpose — the checkout root carries a `webshot.py`
    shim that shadows `sys.path[0]`, which is fine for running WebShot and
    wrong for measuring the installed package (its own test is below).
    """
    modules, stdout = _modules_after(_cli_probe(flag), tmp_path)
    assert "WebShot" in stdout, (
        f"`webshot {flag}` printed no banner; stdout was {stdout!r}"
    )
    missing = [name for name in VERSION_PATH_REQUIRED if name not in modules]
    assert not missing, (
        f"{missing} absent from {len(modules)} loaded modules, so the CLI did "
        "not run and the deny lists below would pass vacuously. Loaded roots: "
        f"{sorted({m.split('.')[0] for m in modules})}"
    )


@pytest.mark.parametrize("flag", FAST_PATHS)
def test_fast_path_loads_no_heavy_third_party(flag: str, tmp_path: Path) -> None:
    """`webshot --version` must not load the capture stack to print a string.

    Two reasons, and the second is the one that is easy to lose.  Startup cost
    is paid by every invocation, including the scripted `--version` a caller
    uses to check whether WebShot is installed at all.  And the nine
    native-extension roots that were on this path are the process-teardown
    surface docs/09 P10-4's unexplained `recursive_mutex` abort was first seen
    on — at v3.0.0, on `webshot --version` itself.  Removing them does not fix
    P10-4 and is not claimed to; it removes nine variables from anyone
    measuring it (P10-6).
    """
    modules, _ = _modules_after(_cli_probe(flag), tmp_path)
    roots = {name.split(".")[0] for name in modules}
    loaded = sorted(roots.intersection(VERSION_PATH_FORBIDDEN_ROOTS))
    assert not loaded, (
        f"`webshot {flag}` loaded {loaded}. Something on the fast path imports "
        "the capture stack again — look for a new module-scope import in "
        "webshot/__init__.py, cli.py, config.py or errors.py and move it into "
        f"the function that needs it. {len(modules)} modules loaded in total."
    )


@pytest.mark.parametrize("flag", FAST_PATHS)
def test_fast_path_loads_no_heavy_webshot_module(flag: str, tmp_path: Path) -> None:
    """The first-party half, which is where the regression will come from."""
    modules, _ = _modules_after(_cli_probe(flag), tmp_path)
    loaded = sorted(modules.intersection(VERSION_PATH_FORBIDDEN_WEBSHOT))
    assert not loaded, (
        f"`webshot {flag}` loaded {loaded}. These pull the third-party stack "
        "behind them; import them inside the function that uses them, the way "
        f"`schema_main` and `doctor_main` do. {len(modules)} modules loaded."
    )


def test_the_source_checkout_shim_is_not_the_expensive_way_in() -> None:
    """`python webshot.py --version` must be as cheap as the console script.

    Its own test because it is a separate load path, and one that stayed
    expensive after the package itself was made lazy: the shim bound
    `convert_url_to_pdf = _loaded.convert_url_to_pdf` at module scope, and that
    single line re-imported `webshot.pipeline` and everything under it — 1809
    modules against the console script's 158 (docs/09 P10-6).  A deferral that
    covers one entry point and not the other is the shape of P7-6: a guard
    covers what it covers, so check the path rather than the intention.
    """
    modules, stdout = _modules_after(
        "import runpy, sys\n"
        "sys.argv = ['webshot.py', '--version']\n"
        "try:\n"
        "    runpy.run_path('webshot.py', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n",
        REPO_ROOT,
    )
    assert "WebShot" in stdout, (
        f"the shim printed no version banner; stdout was {stdout!r}"
    )
    assert "webshot.cli" in modules, (
        f"the shim loaded {len(modules)} modules and none of them was "
        "webshot.cli, so it never reached the CLI and the assertion below "
        "would pass vacuously"
    )
    roots = {name.split(".")[0] for name in modules}
    loaded = sorted(roots.intersection(VERSION_PATH_FORBIDDEN_ROOTS))
    assert not loaded, (
        f"`python webshot.py --version` loaded {loaded}. The shim re-exports "
        "eagerly again; forward the name through its `__getattr__` instead. "
        f"{len(modules)} modules loaded in total."
    )


def test_every_public_name_survives_the_lazy_package_init() -> None:
    """`webshot/__init__.py` defers its re-exports; spec §7 still promises them.

    The deferral is a `__getattr__` over a table, so a name added to `__all__`
    and not to that table raises `AttributeError` at the caller — which reads
    exactly like the caller's own typo. Resolving every name the package
    advertises is the only way that stays honest.
    """
    import webshot

    # Which `webshot` this is, before trusting what it says. A source checkout
    # has `webshot.py` on `sys.path` under the same name as the package, and
    # under pytest that shim is what `import webshot` finds first — so this is
    # the difference between testing the package's `__getattr__` and testing
    # the shim's (docs/09 P10-6).
    assert Path(webshot.__file__ or "").name == "__init__.py", (
        f"imported the shim, not the package: webshot.__file__ is {webshot.__file__!r}"
    )

    unresolved = []
    for name in webshot.__all__:
        try:
            getattr(webshot, name)
        except AttributeError as exc:  # pragma: no cover - the failure it guards
            unresolved.append(f"{name}: {exc}")
    assert not unresolved, (
        f"names in webshot.__all__ that do not resolve: {unresolved}. Add them "
        "to `_EXPORTS` in webshot/__init__.py."
    )
    assert sorted(dir(webshot)) == sorted(webshot.__all__), (
        "dir(webshot) and webshot.__all__ disagree, so tab-completion and the "
        "documented API have drifted apart"
    )


def test_deferring_an_import_does_not_add_a_line_to_stderr() -> None:
    """Loading a stage must log nothing, or the deferral moves the goldens.

    This is the cost the deferral nearly shipped with (docs/09 P10-6).  pikepdf
    announces its C++ logging bridge at INFO the moment it is imported.  While
    the capture stack was imported at `webshot.cli` module scope that record
    met no handler and the last-resort one dropped it; importing the stage on
    first use moved it to *after* `basicConfig`, and 21 of 23 golden cases
    gained `INFO: pikepdf C++ to Python logger bridge initialized` — a line
    about a library starting up, in the transcript of a page capture.

    The assertion is deliberately over the whole deferred stack rather than
    over pikepdf: the defect is not that one library is chatty, it is that
    *any* import-time record now reaches the user's stderr.  A dependency that
    starts logging on import fails here, at the moment it is added, instead of
    as an unexplained golden diff months later.

    `htmldate` also logs at import, at DEBUG.  `--verbose` used to show it,
    with the rest of that library's DEBUG records; it now lowers only
    WebShot's own loggers, so no level shows it (`tests/test_logging.py`).
    This test pins the default level, which is the level the corpus records.
    """
    probe = (
        "import io, logging\n"
        "from webshot.cli import setup_logging\n"
        "stream = io.StringIO()\n"
        "setup_logging(stream=stream)\n"
        "import webshot.pipeline\n"
        "import webshot.report\n"
        "import webshot.settings\n"
        "import webshot.bundle.manifest\n"
        "import webshot.doctor\n"
        "print('LOGGED:' + stream.getvalue())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=REPO_ROOT.parent,
    )
    assert completed.returncode == 0, (
        f"the probe exited {completed.returncode}:\n{completed.stderr[-2000:]}"
    )
    _, found, logged = completed.stdout.partition("LOGGED:")
    assert found, (
        "the probe never reached its report, so nothing was measured. stdout "
        f"was {completed.stdout!r}"
    )
    assert logged.strip() == "", (
        "importing the capture stack emitted these records, which every "
        "capture's stderr would now carry:\n"
        f"{logged}"
        "Quiet the library's logger in `setup_logging`, the way pikepdf's is."
    )


# ---------------------------------------------------------------------------
# The capture path: what every capture pays for, whether it needs it or not.
# ---------------------------------------------------------------------------

#: Loaded by `import webshot.pipeline` until docs/09 P10-15, and needed only to
#: read a local document through MarkItDown.  markitdown imports magika at
#: module scope and magika imports onnxruntime, so the pipeline's one
#: module-scope import of the bridge put a native inference runtime into every
#: capture — a URL, an HTML file, a Markdown file, an image.  Installed is the
#: exception `test_the_onnx_runtime_exception_is_still_needed` argues for;
#: loaded where nothing uses it is not part of that bargain.
PIPELINE_FORBIDDEN_ROOTS = ("onnxruntime", "magika", "markitdown")

#: Proof the pipeline probe measured the module it is about.  `webshot.acquire.
#: adapters` is the one that carried the import: were it missing, the roots
#: above would be absent because the carrier was never loaded, and the deny
#: test would pass without having looked.
PIPELINE_REQUIRED = ("webshot", "webshot.pipeline", "webshot.acquire.adapters")


def test_pipeline_measurement_actually_ran_and_can_see_what_it_denies(
    tmp_path: Path,
) -> None:
    """Both halves of "the probe observed a real run", for the deny test below.

    The first half is `test_fast_path_measurement_actually_ran`'s: the modules
    the probe exists to measure are present, so an empty set cannot read as a
    clean footprint.  The second is a positive control.  A deny list is only as
    good as its spelling, and a name nothing ever loads — a typo, or a
    dependency that renames its package — is denied forever at no cost.  So
    the same probe, pointed at the bridge that *does* need them, must see every
    denied root.  The day markitdown stops importing magika, or magika stops
    importing onnxruntime, this fails with the good news, and the name comes
    off the list rather than staying on it as a check of nothing.

    The control loads onnxruntime, so its telemetry is switched off for it
    explicitly: the uploader sends from every process that loads the runtime
    and can abort that process's exit (docs/09 P10-14).
    """
    modules, _ = _modules_after("import webshot.pipeline\n", tmp_path)
    missing = [name for name in PIPELINE_REQUIRED if name not in modules]
    assert not missing, (
        f"{missing} absent from {len(modules)} loaded modules, so the probe did "
        "not measure the pipeline's import and the deny test below would pass "
        "vacuously. If `webshot.pipeline` stopped importing the adapters on "
        "purpose, probe `webshot.acquire.adapters` directly instead. Loaded "
        f"roots: {sorted({m.split('.')[0] for m in modules})}"
    )

    control, _ = _modules_after(
        "import webshot.acquire.markitdown_bridge\n",
        tmp_path,
        env={**os.environ, "ORT_DISABLE_TELEMETRY": "1"},
    )
    roots = {name.split(".")[0] for name in control}
    unseen = [name for name in PIPELINE_FORBIDDEN_ROOTS if name not in roots]
    assert not unseen, (
        f"importing the MarkItDown bridge no longer loads {unseen}, so denying "
        "them on the pipeline path checks nothing. Remove them from "
        "PIPELINE_FORBIDDEN_ROOTS and record why in docs/09. The bridge loaded "
        f"these non-stdlib roots: {sorted(roots - set(sys.stdlib_module_names))}"
    )


def test_pipeline_import_loads_no_ml_runtime(tmp_path: Path) -> None:
    """Every capture imports `webshot.pipeline`; it must not load onnxruntime.

    Before docs/09 P10-15 it did, through `acquire/adapters.py`'s module-scope
    import of the MarkItDown bridge: 1861 modules and 0.75 s to import the
    pipeline, and 959 and 0.46 s after, on the machine that measured it.  The
    numbers are there for scale and deliberately not asserted, for the reason
    `VERSION_PATH_FORBIDDEN_ROOTS` gives.

    `tmp_path` is the working directory for the reason
    `test_fast_path_measurement_actually_ran` gives: the checkout root's
    `webshot.py` shim would otherwise be what `import webshot` finds.
    """
    modules, _ = _modules_after("import webshot.pipeline\n", tmp_path)
    roots = {name.split(".")[0] for name in modules}
    loaded = sorted(roots.intersection(PIPELINE_FORBIDDEN_ROOTS))
    assert not loaded, (
        f"`import webshot.pipeline` loaded {loaded}, so every capture loads an "
        "ML runtime again. Look for a module-scope import of "
        "`webshot.acquire.markitdown_bridge` (or of markitdown itself) on the "
        "pipeline's import graph, and move it into the function that reads the "
        f"local document. {len(modules)} modules loaded in total."
    )
