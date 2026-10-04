# 07 — Risk register

Scored L/M/H for likelihood × impact. Owner is the maintainer unless noted.

| # | Risk | L | I | Mitigation | Trigger/monitor |
|---|---|---|---|---|---|
| R1 | **Docling API churn** (fast-moving project; slim packaging is new) breaks the bridge | M | M | Single-file bridge seam; exact version pins; bridge contract tests run against pinned lib in CI; Dependabot PRs upgrade deliberately | bridge tests fail on upgrade PR |
| R2 | ~~HybridChunker offline risk~~ **RETIRED — resolved by spike (09 S4)**: HierarchicalChunker + chonkie adopted; HybridChunker optional | — | — | ADR-0008 | closed |
| R3 | ~~Web-path tag stripping~~ **RETIRED — confirmed by spike (09 S7)**: web-path `--pdfa` rejected; protected-path-only, PDF/A-3, final-composed veraPDF check | — | — | ADR-0010 | closed |
| R4 | **System-binary drift** (tesseract/gs missing, wrong versions) generates support burden | H | L | `webshot doctor`; actionable error taxonomy; docs matrix per OS | issue reports |
| R5 | **Bundle format break** disrupts existing consumers | M | M | `bundle_format: 3` self-description; `--legacy-bundle` for one release; migration guide; schemas published | parity harness |
| R6 | **SharePoint viewer DOM changes** break protected capture (upstream-hard problem; cf. Browsertrix #140) | H | H | **Mitigated in Phase 1:** the selectors and toolbar names are constants in `protected/viewer.py` (data, not literals buried in JavaScript), `tests/fixtures/viewer/dom/` reproduces the accessibility-tree contract, and `tests/test_viewer_dom.py` checks the fixtures against those same constants; capture is resumable so partial progress survives; `SUPPORTED_VIEWERS` records the builds covered. Honest monitor, restated: the fixtures were **authored from the contract, not exported from a tenant** (docs/09 P1-6), and frozen recordings cannot detect *live* DOM drift either way — they prevent regressions in WebShot, not drift in Microsoft. Detection stays user reports plus the supported-versions table | `tests/test_viewer_dom.py` fails |
| R7 | **License drift** in transitive deps | L | H | pip-licenses CI gate (deny GPL/AGPL/SSPL); components doc is the allowlist of record | CI |
| R8 | **Playwright/Chromium PDF regressions** (tagging/outline behavior changes) | M | M | Pin playwright; golden PDF assertions — outline titles and text layer are recorded, and **tagging is asserted against the produced file** on every profile, in both directions, so `--no-tagged-pdf` failing to disable tags fails too (Phase 1) | golden job |
| R9 | **OCR duplication cost** on protected path (WebShot's TSV pass + OCRmyPDF) unacceptable on large docs | M | L | Accepted for v3 and now real: both passes run on every protected capture. Roadmap: OCRmyPDF plugin hook to reuse its hOCR for TSV | perf smoke |
| R10 | **Solo-maintainer bus factor** | H | H | The plan itself: less bespoke code, ADRs, docs site, boring stdlib CLI, pinned reproducible env | — |
| R11 | **Name collision** (`webshot` R packages) on publication | H | L | Rename decision gate before PyPI (plan §Open questions) | Phase 4 |
| R12 | **MCP surface abused** (capture of internal URLs, path traversal on bundle reads) | M | H | Spec §6.8: allowlisted roots, no credential params, profile names not contents; tests for traversal | security tests |
| R13 | **Parity thresholds mask real regressions** (harness too lenient) | M | M | Thresholds in one visible TOML; any loosening requires PR justification; sentinel-string PDF assertions independent of parity | review checklist |
| R14 | **mkdocs-material maintenance mode** strands docs tooling | L | L | It's MIT and stable; revisit Zensical at Phase 4; docs are plain markdown either way | Phase 4 |

Retired-by-design risks: bespoke OCR-sandwich correctness, bespoke chunker
drift, bleach deprecation exposure, reportlab layout bugs — all removed with
the code that carried them. (OCR-sandwich and reportlab: removed in Phase 1;
the bespoke chunker and semantic extraction: replaced in Phase 2, with the v2
emitters quarantined in `extract/legacy.py` until `--legacy-bundle` dies at
v3.1; bleach and the bespoke format parsers: removed in Phase 3, which put
the adapters' allowlist on nh3 and their reading on MarkItDown.)

New in Phase 1: **R15 — PDF/A conformance regresses silently.** The composition
does four things after OCRmyPDF's conversion, any of which can invalidate it,
and nothing in the file complains. L: M · I: M. Mitigation: the veraPDF job runs
`tools/verapdf/check.py` against the final composed file on every pull request
and publishes its report; it goes blocking once the baseline has held (ADR-0010).
Monitor: that job.

Phase 2 status changes:

- **R1 (docling API churn): materialized immediately, mitigation held.** At
  adoption, docling-core 2.92's chunker package could not even be imported on
  a slim install (docs/09 P2-2), the backend dropped table captions, filed
  pre-heading content as furniture, and crashed doctags on its own JSON code
  label (P2-3). Every one was absorbed inside the single-file bridge and
  pinned by a bridge contract test — which is exactly the seam R1's
  mitigation bought. Likelihood stays M; the monitor (bridge tests on
  upgrade PRs) is now real code.
- **R5 (bundle format break): mitigations shipped.** `bundle_format: 3`
  self-description, `--legacy-bundle` (v3.0 only), frozen v2 schemas as
  `schemas/legacy-*.schema.json`, and `docs/migration-v2-to-v3.md`.
- **R13 (parity thresholds mask regressions): mitigation strengthened.** The
  harness now has to prove it can catch planted defects: a ten-mutation
  self-test runs in the ordinary suite, and an identity run must score 100%
  (`tests/test_parity_selftest.py`). The judge is calibrated, not trusted.

New in Phase 2: **R16 — the picture/asset mapping is positional.** docling's
HTML backend records no image URIs, so OCR annotations attach to pictures by
document order (docs/09 P2-4). If an upstream change makes docling skip or
reorder images, annotations would land on the wrong pictures. L: L · I: M.
Mitigation: the bridge counts snapshot images against produced pictures and
refuses to guess — on mismatch it skips the attachment and says so in a
manifest warning (never silent, never a failed capture; full OCR records stay
in assets.json); bridge contract tests pin both directions, including the
real mismatch shapes docling produces (img-in-pre, nested tables). Monitor:
bridge tests on Dependabot upgrade PRs.
