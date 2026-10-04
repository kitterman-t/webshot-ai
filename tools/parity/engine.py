"""Compare two bundle views and judge the result against the thresholds.

Every metric is a number in [0, 1] with a threshold read from `parity.toml`
(one file, so loosening a threshold is a visible diff — docs/06 §Parity).  A
metric never silently narrows what it measures: when a case has nothing to
measure for a metric (a page with no tables), the metric scores 1.0 and says
so, which keeps "vacuously true" visible in the report rather than implied.
"""

from __future__ import annotations

import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from tools.parity.views import BundleView, tokens

PARITY_TOML = Path(__file__).with_name("parity.toml")

Item = TypeVar("Item")


@dataclass(slots=True)
class Metric:
    name: str
    score: float
    threshold: float
    detail: str
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


@dataclass(slots=True)
class CaseResult:
    case_id: str
    metrics: list[Metric]

    @property
    def passed(self) -> bool:
        return all(metric.passed for metric in self.metrics)


def load_thresholds(path: Path = PARITY_TOML) -> dict[str, float]:
    with path.open("rb") as handle:
        return {
            name: float(value)
            for name, value in tomllib.load(handle)["thresholds"].items()
        }


def _describe(item: object) -> str:
    text = repr(item)
    return text if len(text) <= 160 else text[:157] + "..."


def _set_metric(
    name: str,
    baseline: Counter[Item],
    candidate: Counter[Item],
    threshold: float,
    empty_detail: str,
) -> Metric:
    """Exact-match score over multisets: 1.0 means the sets are identical."""
    if not baseline and not candidate:
        return Metric(name, 1.0, threshold, f"nothing to measure ({empty_detail})")
    matched = sum((baseline & candidate).values())
    denominator = max(sum(baseline.values()), sum(candidate.values()))
    problems = [
        f"missing from candidate: {_describe(item)} (x{count})"
        for item, count in sorted((baseline - candidate).items(), key=str)
    ] + [
        f"unexpected in candidate: {_describe(item)} (x{count})"
        for item, count in sorted((candidate - baseline).items(), key=str)
    ]
    return Metric(
        name,
        matched / denominator,
        threshold,
        f"{matched}/{denominator} matched",
        problems,
    )


def _coverage_metric(
    name: str,
    wanted: Counter[str],
    have: Counter[str],
    threshold: float,
    empty_detail: str,
) -> Metric:
    """Token-multiset coverage: how much of `wanted` appears in `have`."""
    total = sum(wanted.values())
    if not total:
        return Metric(name, 1.0, threshold, f"nothing to measure ({empty_detail})")
    covered = sum((wanted & have).values())
    missing = wanted - have
    worst = [
        f"missing token {token!r} (x{count})"
        for token, count in missing.most_common(15)
    ]
    if len(missing) > 15:
        worst.append(f"... and {len(missing) - 15} more distinct tokens")
    return Metric(
        name, covered / total, threshold, f"{covered}/{total} tokens covered", worst
    )


def _definitions_metric(
    baseline: BundleView, candidate: BundleView, threshold: float
) -> Metric:
    """A definition survives as a record, or as text docling modeled natively.

    docs/02's fidelity mapping allows either: "definition lists -> docling
    list/group items (else custom annotation)".  A pair counts as covered when
    an identical record exists, or when both its term and its definition appear
    in the candidate's document text.
    """
    total = sum(baseline.definitions.values())
    if not total:
        return Metric(
            "definitions", 1.0, threshold, "nothing to measure (no definition lists)"
        )
    covered = sum((baseline.definitions & candidate.definitions).values())
    problems = []
    leftover = baseline.definitions - candidate.definitions
    for term, definition in leftover.elements():
        if term in candidate.full_text and definition in candidate.full_text:
            covered += 1
        else:
            problems.append(
                f"definition lost: {_describe((term, definition))} — no record "
                "in assets.json and its text is not in the document"
            )
    return Metric(
        "definitions",
        covered / total,
        threshold,
        f"{covered}/{total} covered",
        problems,
    )


def _chunk_metrics(candidate: BundleView, thresholds: dict[str, float]) -> list[Metric]:
    provenance_threshold = thresholds["chunk_doc_items"]
    coverage_threshold = thresholds["chunk_text_coverage"]
    if not candidate.chunks:
        empty = Metric(
            "chunk_doc_items",
            0.0,
            provenance_threshold,
            "no chunks at all",
            ["chunks.jsonl is empty"],
        )
        return [
            empty,
            Metric(
                "chunk_text_coverage",
                0.0,
                coverage_threshold,
                "no chunks at all",
                ["chunks.jsonl is empty"],
            ),
        ]
    resolvable = candidate.resolvable_refs
    bad = [
        f"chunk {index} ({_describe(chunk.text[:60])}): "
        + (
            "no doc_items"
            if not chunk.refs
            else f"unresolvable refs {sorted(set(chunk.refs) - resolvable)}"
        )
        for index, chunk in enumerate(candidate.chunks)
        if not chunk.refs or not set(chunk.refs) <= resolvable
    ]
    provenance = Metric(
        "chunk_doc_items",
        (len(candidate.chunks) - len(bad)) / len(candidate.chunks),
        provenance_threshold,
        f"{len(candidate.chunks) - len(bad)}/{len(candidate.chunks)} chunks traceable",
        bad,
    )
    chunk_tokens: Counter[str] = Counter()
    for chunk in candidate.chunks:
        chunk_tokens.update(tokens(chunk.text))
    coverage = _coverage_metric(
        "chunk_text_coverage",
        candidate.text_tokens,
        chunk_tokens,
        coverage_threshold,
        "document has no text",
    )
    return [provenance, coverage]


def compare(
    case_id: str,
    baseline: BundleView,
    candidate: BundleView,
    thresholds: dict[str, float],
) -> CaseResult:
    metrics = [
        _set_metric(
            "headings",
            baseline.headings,
            candidate.headings,
            thresholds["headings"],
            "no headings",
        ),
        _set_metric(
            "tables",
            baseline.tables,
            candidate.tables,
            thresholds["tables"],
            "no tables",
        ),
        _set_metric(
            "links", baseline.links, candidate.links, thresholds["links"], "no links"
        ),
        _set_metric(
            "assets",
            baseline.assets,
            candidate.assets,
            thresholds["assets"],
            "no visual assets",
        ),
        _coverage_metric(
            "text_coverage",
            baseline.text_tokens,
            candidate.text_tokens,
            thresholds["text_coverage"],
            "document has no text",
        ),
        _set_metric(
            "form_fields",
            baseline.form_fields,
            candidate.form_fields,
            thresholds["form_fields"],
            "no form fields",
        ),
        _definitions_metric(baseline, candidate, thresholds["definitions"]),
        _set_metric(
            "embedded_media",
            baseline.media,
            candidate.media,
            thresholds["embedded_media"],
            "no embedded media",
        ),
        _set_metric(
            "page_metadata",
            baseline.page_metadata,
            candidate.page_metadata,
            thresholds["page_metadata"],
            "no page metadata",
        ),
        *_chunk_metrics(candidate, thresholds),
    ]
    return CaseResult(case_id, metrics)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def render_report(
    results: list[CaseResult],
    skipped: dict[str, str],
    thresholds: dict[str, float],
    profile_note: str,
    failed_captures: list[str] | None = None,
) -> str:
    """The markdown report the CI job uploads and the PR body links."""
    failed_captures = failed_captures or []
    metric_names = [metric.name for metric in results[0].metrics] if results else []
    lines = ["# Parity report — v2 baseline vs v3 candidate", ""]
    verdict = (
        bool(results)
        and all(result.passed for result in results)
        and not failed_captures
    )
    lines.append(f"**Verdict: {'PASS' if verdict else 'FAIL'}** — {profile_note}")
    lines.append("")
    for case_id in failed_captures:
        lines.append(f"- **capture failed** `{case_id}` — no bundle to measure")
    if failed_captures:
        lines.append("")
    lines.append(
        "Thresholds ("
        + ", ".join(f"{name} ≥ {value:g}" for name, value in thresholds.items())
        + ") live in `tools/parity/parity.toml`; loosening one is a visible diff."
    )
    lines.append("")
    if results:
        lines.append("| case | " + " | ".join(metric_names) + " |")
        lines.append("|---|" + "---|" * len(metric_names))
        for result in results:
            cells = [
                f"{'✅' if metric.passed else '❌'} {metric.score * 100:.2f}%"
                for metric in result.metrics
            ]
            lines.append(f"| {result.case_id} | " + " | ".join(cells) + " |")
        lines.append("")
    for case_id, reason in sorted(skipped.items()):
        lines.append(f"- **skipped** `{case_id}`: {reason}")
    if skipped:
        lines.append("")
    for result in results:
        failing = [metric for metric in result.metrics if not metric.passed]
        interesting = failing or [
            metric for metric in result.metrics if metric.problems
        ]
        if not interesting:
            continue
        lines.append(f"## {result.case_id}")
        lines.append("")
        for metric in interesting:
            status = "FAIL" if not metric.passed else "pass (with notes)"
            lines.append(
                f"### {metric.name} — {status}: {metric.score * 100:.2f}% "
                f"(threshold {metric.threshold * 100:g}%) — {metric.detail}"
            )
            lines.extend(f"- {problem}" for problem in metric.problems[:40])
            if len(metric.problems) > 40:
                lines.append(f"- ... and {len(metric.problems) - 40} more")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
