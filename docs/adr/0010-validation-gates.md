# ADR-0010 — Validation gates: veraPDF, schemas, and the PDF/A tag question

**Status:** accepted; web-path PDF/A experiment COMPLETE — rejected; protected-path gate IMPLEMENTED and baselined in Phase 1 · **Date:** 2026-08-19

## Context
v2 asserts "tagged, accessible, structurally valid" but only checks signature/
pages via pypdf. veraPDF (the industry PDF/A / PDF/UA validator; Docker image;
CI-friendly exit codes; JSON reports) can substantiate or refute the claim.
Separately: Ghostscript-based PDF/A conversion is widely reported to drop
structure tags, which would trade accessibility for archival conformance on
the web path.

## Decision
1. Every artifact WebShot emits that has a schema (manifest, chunks, QA
   report) is validated against the published schema in CI.
2. veraPDF runs in CI on golden PDFs — non-blocking until the baseline is
   clean, then blocking. `--validate-pdf` exposes the same check to users.
3. `--pdfa` ships on the protected path (image-based, no tags to lose) in
   Phase 1. **Web-path experiment result (2026-08-19, docs/09 S7): running the
   tagged Chromium PDF through OCRmyPDF/Ghostscript PDF/A conversion removed
   StructTreeRoot and MarkInfo — accessibility tags do not survive. Web-path
   `--pdfa` is therefore rejected for v3**; the docs state the tradeoff
   (tagged PDF ≠ PDF/A; pick per use case, protected path offers PDF/A).
   OpenDataLoader's OSS auto-tagging remains the watched escape hatch.
4. Protected-path `--pdfa` uses `output_type='pdfa-3'` (A-3 permits the
   embedded associated files; the A-2b default forbids them), and the
   conformance gate runs against the **final composed file** (OCR → appendix
   merge → embeds), never the intermediate OCRmyPDF output — composition
   steps after conversion can invalidate conformance, so it must be proven,
   not assumed (red-team finding C2).

## Consequences
The accessibility claim becomes a tested property. CI gains a Docker
dependency for one non-blocking job (acceptable). The tradeoff between
PDF/A conformance and tagged accessibility is surfaced honestly instead of
silently choosing one.

## Phase 1 outcome (2026-08-19)

**The final composed file is conformant.** `tools/verapdf/check.py` assembles
the protected fixture with `--pdfa` and validates the deliverable *after* the
appendix merge, the bookmark and back-link rewrite, and five embedded
associated files: **PDF/A-3B, 146 rules passed, 0 failed** (veraPDF 1.30.0).
02's pre-authorized fallback — shipping the `--pdfa` variant without appendix
and embeds — was therefore not taken. It stays documented as the contingency.

Getting there required fixing two clause-6.8 defects in the embedded files
(docs/09 P1-3, ADR-0007), neither of which was visible without the validator.
That is the ADR's own thesis, paid off: an untested conformance claim was
wrong, in a way that reading the file back with our own library could not show.

**Where the gate has teeth.** `--validate-pdf` reports; `--validate-pdf=strict`
exits 7 on a non-conformant file. An *absent* validator is a warning at both
levels — a check that could not run has not found a problem — and a file that
declares no PDF/A level (every web capture, by this ADR's own decision) is
reported as having nothing to validate rather than as failing. The CI job is
therefore the real gate, and it is non-blocking only until the baseline has
held for a few releases.
