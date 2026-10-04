"""Calibrate the parity harness: prove it can fail before trusting it.

Every mutation in tools/parity/mutations.py plants one defect a real
extraction regression could cause — a dropped heading, a deleted table row, a
vanished redacted-password record, a stripped asset — into a copy of a frozen
v2 baseline, runs the mutated bundle through the candidate side, and checks
that the defect is reported under the metric that owns it while every other
metric keeps passing.  An identity run (baseline vs itself) proves the
opposite direction: no false alarms.

This needs no browser: the engine is format-agnostic and the baselines are
committed, so the calibration runs in the default test suite on every push —
the judge stays proven even as it evolves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.parity.check import BASELINE_ROOT, read_baseline  # noqa: E402
from tools.parity.engine import compare, load_thresholds  # noqa: E402
from tools.parity.mutations import MUTATIONS, V3_MUTATIONS, Mutation  # noqa: E402
from tools.parity.views import read_bundle  # noqa: E402

THRESHOLDS = load_thresholds()
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden"

#: Format-agnostic v2 mutations that must also be caught when planted into a
#: v3 candidate — they exercise the artifacts both formats share.
SHARED_MUTATION_NAMES = (
    "delete-table-row",
    "rewrite-link-url",
    "break-chunk-provenance",
)


def baseline_ids() -> list[str]:
    if not BASELINE_ROOT.is_dir():
        return []
    return sorted(
        path.name for path in BASELINE_ROOT.iterdir() if (path / "bundle").is_dir()
    )


def test_baselines_are_present() -> None:
    assert len(baseline_ids()) >= 17, (
        "the frozen v2 baselines are missing — the parity harness has no yardstick"
    )


@pytest.mark.parametrize("case_id", baseline_ids())
def test_identity_scores_perfect_parity(case_id: str) -> None:
    """A bundle compared against itself must pass every metric at 100%."""
    files = read_baseline(case_id)
    view = read_bundle(files)
    result = compare(case_id, view, read_bundle(files), THRESHOLDS)
    imperfect = [
        (metric.name, metric.score) for metric in result.metrics if metric.score < 1.0
    ]
    assert not imperfect, f"identity comparison is lossy: {imperfect}"


def _applicable(mutation: Mutation) -> list[str]:
    return [
        case_id
        for case_id in baseline_ids()
        if mutation.applies(read_baseline(case_id))
    ]


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_every_mutation_has_a_case_to_break(mutation: Mutation) -> None:
    """A planted defect nothing exercises would silently calibrate nothing."""
    assert _applicable(mutation), (
        f"no baseline case exercises {mutation.name!r} — the corpus audit missed "
        "a fidelity type"
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_planted_defect_is_caught_at_the_right_severity(mutation: Mutation) -> None:
    for case_id in _applicable(mutation):
        baseline_files = read_baseline(case_id)
        mutated = mutation.apply(dict(baseline_files))
        assert mutated != baseline_files, (
            f"{mutation.name} on {case_id} mutated nothing"
        )
        result = compare(
            case_id,
            read_bundle(baseline_files),
            read_bundle(mutated),
            THRESHOLDS,
        )
        by_name = {metric.name: metric for metric in result.metrics}
        undetected = [name for name in mutation.must_fail if by_name[name].passed]
        assert not undetected, (
            f"{mutation.name} on {case_id}: defect NOT reported by {undetected} "
            f"(scores: {[(m.name, round(m.score, 4)) for m in result.metrics]})"
        )
        wrongly_blamed = [
            metric.name
            for metric in result.metrics
            if not metric.passed
            and metric.name not in mutation.must_fail | mutation.may_fail
        ]
        assert not wrongly_blamed, (
            f"{mutation.name} on {case_id}: reported under the wrong metric(s) "
            f"{wrongly_blamed}"
        )


def test_redacted_password_mutation_targets_the_real_record() -> None:
    """The §6.2 trap, aimed at the harness itself: the form-fields baseline
    must contain the redacted record, and removing it must be what the
    form_fields metric catches — not a coincidence of other damage."""
    mutation = next(m for m in MUTATIONS if m.name == "remove-redacted-password-record")
    assert "form-fields" in _applicable(mutation)


# --------------------------------------------------------------------------- #
# v3-side calibration: the same defects planted into real v3 candidates
# (the recorded format-3 goldens), proving the v3 READER surfaces them —
# the v2 set above proves only the engine.
# --------------------------------------------------------------------------- #


def _golden_candidate(case_id: str) -> dict[str, str]:
    directory = GOLDEN_ROOT / case_id
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.relative_to(directory).parts[0] == "bundle"
    }


def _v3_calibration_pairs() -> list[tuple[str, Mutation]]:
    shared = [m for m in MUTATIONS if m.name in SHARED_MUTATION_NAMES]
    pairs = []
    for case_id in baseline_ids():
        candidate = _golden_candidate(case_id)
        if "bundle/assets.json" not in candidate:  # not a v3 golden yet
            continue
        for mutation in (*V3_MUTATIONS, *shared):
            if mutation.applies(candidate):
                pairs.append((case_id, mutation))
    return pairs


def test_v3_candidates_have_calibration_coverage() -> None:
    names = {mutation.name for _, mutation in _v3_calibration_pairs()}
    assert {m.name for m in V3_MUTATIONS} <= names, (
        f"v3 mutations with no case to break: "
        f"{sorted({m.name for m in V3_MUTATIONS} - names)}"
    )


@pytest.mark.parametrize(
    ("case_id", "mutation"),
    _v3_calibration_pairs(),
    ids=lambda value: value.name if isinstance(value, Mutation) else value,
)
def test_planted_v3_defect_is_caught_at_the_right_severity(
    case_id: str, mutation: Mutation
) -> None:
    baseline = read_bundle(read_baseline(case_id))
    candidate_files = _golden_candidate(case_id)
    mutated = mutation.apply(dict(candidate_files))
    assert mutated != candidate_files, f"{mutation.name} on {case_id} mutated nothing"
    result = compare(case_id, baseline, read_bundle(mutated), THRESHOLDS)
    by_name = {metric.name: metric for metric in result.metrics}
    undetected = [name for name in mutation.must_fail if by_name[name].passed]
    assert not undetected, (
        f"{mutation.name} on {case_id}: defect NOT reported by {undetected}"
    )
    wrongly_blamed = [
        metric.name
        for metric in result.metrics
        if not metric.passed
        and metric.name not in mutation.must_fail | mutation.may_fail
    ]
    assert not wrongly_blamed, (
        f"{mutation.name} on {case_id}: reported under the wrong metric(s) "
        f"{wrongly_blamed}"
    )


def test_a_rerendered_asset_is_parity_equal_but_a_lost_one_is_not() -> None:
    """The `assets` metric must survive a re-render and still catch a loss.

    A macOS font update rewrote every text-bearing raster in the corpus, and
    the metric — then keyed on the digest of those pixels — scored 0% against
    baselines frozen before it, on nine cases where nothing about the assets
    had changed (docs/09 P7-14). Keying on what the page authored instead has
    to hold both directions at once: a digest that moved is not a regression,
    and an asset that vanished still is.
    """
    import json

    baseline = read_baseline("svg-chart")
    content = json.loads(baseline["bundle/content.json"])
    assert content["visual_assets"], "fixture must carry an asset to re-render"

    rerendered = dict(baseline)
    redrawn = json.loads(baseline["bundle/content.json"])
    for asset in redrawn["visual_assets"]:
        asset["sha256"] = "f" * 64  # same asset, different fonts drew it
    rerendered["bundle/content.json"] = json.dumps(redrawn)

    metrics = {
        metric.name: metric
        for metric in compare(
            "svg-chart", read_bundle(baseline), read_bundle(rerendered), THRESHOLDS
        ).metrics
    }
    assert metrics["assets"].passed, (
        f"a re-rendered asset must not read as a parity regression: "
        f"{metrics['assets'].problems}"
    )

    stripped = dict(baseline)
    without = json.loads(baseline["bundle/content.json"])
    without["visual_assets"] = []
    stripped["bundle/content.json"] = json.dumps(without)
    metrics = {
        metric.name: metric
        for metric in compare(
            "svg-chart", read_bundle(baseline), read_bundle(stripped), THRESHOLDS
        ).metrics
    }
    assert not metrics["assets"].passed, "a lost asset must still fail the metric"
